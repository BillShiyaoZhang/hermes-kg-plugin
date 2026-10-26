"""Exact-vector and controlled offline ablations; no production DB access."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import math
import hashlib
from pathlib import Path
import re
import sqlite3
import time

from . import FORMAT
from .common import digest, encoded, relation_key
from .fixtures import apply_labels, validate_fixture
from .metrics import aggregate, holm, paired_clustered_bootstrap, score_query


def normalize(vector):
    vector = [float(x) for x in vector]
    if not vector or not all(math.isfinite(x) for x in vector): raise ValueError('Invalid embedding components')
    norm = math.hypot(*vector)
    if not norm: raise ValueError('Zero embedding')
    return [x / norm for x in vector]


class VectorCache:
    """Reusable real vectors; keys isolate query/passage/model/text revisions."""
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS vectors(cache_key TEXT PRIMARY KEY, model TEXT NOT NULL, purpose TEXT NOT NULL, text_sha TEXT NOT NULL, dimensions INTEGER NOT NULL, vector_json TEXT NOT NULL)')
        self.hits = self.misses = 0

    def get(self, fingerprint, purpose, text):
        import json
        key = digest([fingerprint, purpose, digest(text)])
        row = self.db.execute('SELECT model,purpose,text_sha,dimensions,vector_json FROM vectors WHERE cache_key=?', (key,)).fetchone()
        if row is None:
            self.misses += 1
            return None
        vector = normalize(json.loads(row[4]))
        if row[:3] != (fingerprint, purpose, digest(text)) or len(vector) != row[3]: raise ValueError('Corrupt vector cache entry')
        self.hits += 1
        return vector

    def put(self, fingerprint, purpose, text, vector):
        vector = normalize(vector)
        self.db.execute('INSERT OR REPLACE INTO vectors VALUES(?,?,?,?,?,?)',
                        (digest([fingerprint, purpose, digest(text)]), fingerprint, purpose, digest(text), len(vector), encoded(vector)))
        self.db.commit()
        return vector

    def close(self): self.db.close()


class SmokeEmbedder:
    """Deterministic token hash vectors for tool smoke tests, NOT a model."""
    fingerprint = 'SMOKE-NOT-SEMANTIC-token-hash-v1'
    model_manifest = {'model': 'smoke-token-hash', 'ready': True, 'dimensions': 64, 'real_vectors': False}
    def prepare(self, **kwargs): return self.model_manifest
    def chunk(self, text): return [{'text': text, 'token_count': len(tokens(text)), 'index': 0}]
    def embed_query(self, text): return self.embed_passages([text])[0]
    def embed_passages(self, texts):
        result = []
        for text in texts:
            vector = [0.0] * 64
            for token in tokens(text) or ['empty']:
                vector[int(digest(token)[:8], 16) % 64] += 1
            result.append(normalize(vector))
        return result


def tokens(text):
    words = re.findall(r'[a-z0-9_]+|[\u3400-\u9fff]+', text.casefold())
    return [piece for word in words for piece in ([word] if word.isascii() or len(word) < 2 else [word[i:i + 2] for i in range(len(word) - 1)])]


def logical_key(doc):
    m = doc.metadata
    if doc.kind == 'entity': return m.get('uri', doc.source_refs[0])
    if doc.kind == 'relation': return relation_key(m['from_uri'], m['relation_type'], m['to_uri'])
    collection = m.get('collection', m.get('kind', m.get('definition_kind', 'property')))
    kind = {'properties': 'property', 'relations': 'relation', 'entity_types': 'entity_type'}.get(collection, collection)
    return 'ontology:' + str(kind) + ':' + str(m.get('definition_key', m.get('key', doc.source_refs[0].split(':')[-1])))


def _source_state(dataset):
    result = {}
    for entity in dataset.get('entities', []):
        p = entity.get('properties', entity.get('props', {}))
        uri = entity.get('uri', p.get('uri'))
        result[uri] = (entity, p)
    return result


def _allowed(doc, sources, *, namespace, scope):
    # Projection namespaces identify the requesting scope, including when a
    # shared external fact is projected. Check actual evidence URIs instead.
    if scope == 'profile' and any(not ref.startswith(namespace) for ref in doc.source_refs):
        return False
    for ref in doc.source_refs if doc.kind != 'ontology' else ():
        entity, p = sources.get(ref, ({}, {}))
        if not entity or entity.get('research_source_missing') or entity.get('research_visible') is False: return False
        if entity.get('research_expected_revision', p.get('hermes_revision', 0)) != p.get('hermes_revision', 0): return False
    return True


def _chunk_text(chunk):
    return chunk['text'] if isinstance(chunk, dict) else chunk.text


def _rrf(channels, k=60):
    scores = defaultdict(float)
    for ranking in channels:
        for rank, key in enumerate(dict.fromkeys(ranking), 1): scores[key] += 1 / (k + rank)
    return sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))


def tool_fingerprint():
    package = Path(__file__).parent
    paths = [*package.glob('*.py'), package / 'protocol-v1.json', package.parent / 'retrieval_documents.py', package.parent / 'schema.py',
             package.parent / 'embedding.py', package.parent / 'embedding_profiles.py']
    return digest({path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)})


def documents_fingerprint(documents):
    return digest([doc.to_dict() for doc in sorted(documents, key=lambda doc: doc.id)])


def _groups(rows):
    result = {name: aggregate([r for r in rows if r['track'] == name]) for name in ('facts', 'ontology', 'safety')}
    result.update({name: aggregate([r for r in rows if r['provenance'] == name]) for name in ('authored_synthetic', 'real_human_locked')})
    result.update({'task:' + name: aggregate([r for r in rows if r['task_type'] == name and r['track'] == 'facts'])
                   for name in sorted({r['task_type'] for r in rows if r['track'] == 'facts'})})
    result.update({'language:' + name: aggregate([r for r in rows if r['language'] == name and r['track'] == 'facts']) for name in ('zh', 'en')})
    result['selection_facts'] = aggregate([r for r in rows if r['track'] == 'facts' and r['task_type'] not in {'aggregate', 'multihop', 'path', 'count'}])
    result['structured_tasks'] = aggregate([r for r in rows if r['track'] == 'facts' and r['task_type'] in {'aggregate', 'multihop', 'path', 'count'}])
    return result


def validate_run(run):
    """Reject damage to either arm before computing any improvement statistic."""
    if run.get('format') != FORMAT or not isinstance(run.get('rows'), list): raise ValueError('Invalid research run format')
    manifest, rows = run.get('manifest', {}), run['rows']
    if manifest.get('run_id') != digest({key: value for key, value in manifest.items() if key != 'run_id'}):
        raise ValueError('Run manifest integrity failed')
    if manifest.get('config_sha256') != digest(manifest.get('config')): raise ValueError('Run config integrity failed')
    if run.get('results_sha256') != digest(rows): raise ValueError('Run results integrity failed')
    if len({row['id'] for row in rows}) != len(rows): raise ValueError('Duplicate run query IDs')
    if run.get('summary') != aggregate(rows): raise ValueError('Run summary does not match rows')
    if run.get('groups') != _groups(rows): raise ValueError('Run groups do not match rows')
    return True


def evaluate(dataset, fixture, *, embedder=None, representation='entity', mode='vector', split='dev',
             track='all', cache_path=None, labels=None, lock=None, protocol=None, limit=None,
             candidate_budget=50, graph_direction='out', graph_seeds=3, graph_neighbors=8,
             acceptance_threshold=None, seed=8128, scope='profile'):
    from kg.retrieval_documents import DOCUMENT_REVISION, dataset_to_documents
    started_at = datetime.now(timezone.utc).isoformat()
    validate_fixture(fixture, dataset)
    if mode not in {'vector', 'lexical', 'hybrid', 'hybrid_graph'}: raise ValueError('Unknown ablation mode')
    if split not in {'dev', 'blind', 'regression'}: raise ValueError('Unknown split')
    if scope not in {'profile', 'shared'}: raise ValueError('Unknown retrieval scope')
    if not 50 <= candidate_budget <= 10000: raise ValueError('Candidate budget must be 50..10000')
    if graph_direction not in {'out', 'in', 'both'}: raise ValueError('Invalid graph direction')
    if not 1 <= graph_seeds <= 3 or not 1 <= graph_neighbors <= 32: raise ValueError('Graph expansion must stay bounded')
    if mode != 'lexical' and embedder is None: raise ValueError('A real embedder or explicitly marked smoke adapter is required')
    if acceptance_threshold is not None and not math.isfinite(acceptance_threshold): raise ValueError('Acceptance threshold must be finite')
    effective, human = apply_labels(fixture, dataset, labels, lock)
    queries = [q for q in effective['queries'] if q['split'] == split and (track == 'all' or q['track'] == track) and q.get('gold') is not None]
    if limit is not None:
        if limit < 1: raise ValueError('Smoke limit must be positive')
        queries = queries[:limit]
    if not queries: raise ValueError('No labeled queries in requested split/track')
    if embedder is not None:
        embedder.prepare(allow_download=False)
    model = dict(getattr(embedder, 'model_manifest', {}) or {})
    fingerprint = getattr(embedder, 'fingerprint', 'lexical-only')
    if callable(fingerprint): fingerprint = fingerprint()
    if embedder is not None and (not model.get('ready', True) or not fingerprint): raise ValueError('Embedding manifest is not ready')
    config = {'representation': representation, 'mode': mode, 'split': split, 'track': track, 'scope': scope,
              'candidate_budget': candidate_budget, 'graph_direction': graph_direction, 'graph_seeds': graph_seeds,
              'graph_neighbors': graph_neighbors, 'acceptance_threshold': acceptance_threshold,
              'projection_revision': DOCUMENT_REVISION, 'model_fingerprint': fingerprint, 'seed': seed,
              'lexical_algorithm': 'offline-BM25-unicode-word-CJK-bigram-v1', 'rrf_k': 60,
              'graph_score_multiplier': .5, 'ranking_backend': 'exact_cosine', 'smoke_limit': limit}
    manifest = {'format': FORMAT, 'tool_code_sha256': tool_fingerprint(), 'dataset_sha256': digest(dataset), 'fixture_sha256': digest(fixture),
                'effective_fixture_sha256': digest(effective), 'labels_validation': human, 'config': config,
                'config_sha256': digest(config), 'model': model, 'protocol_sha256': digest(protocol) if protocol else None}
    cache = VectorCache(cache_path) if cache_path else None
    sources, aliases = _source_state(dataset), fixture.get('gold_aliases', {})
    documents_by_scope, vectors, chunk_counts = {}, {}, {}
    preparation_started = time.perf_counter()
    try:
        for query in queries:
            namespace = query.get('namespace') or dataset['namespace']
            card_scope = (namespace, query['track'] == 'ontology')
            if card_scope in documents_by_scope: continue
            schema = dataset.get('real_schema') if namespace != dataset['namespace'] else dataset.get('schema')
            docs = dataset_to_documents(dataset, representation, schema=schema, namespace=namespace, include_ontology=card_scope[1])
            docs = [d for d in docs if _allowed(d, sources, namespace=namespace, scope=scope)
                    and ((d.kind == 'ontology') == card_scope[1])]
            documents_by_scope[card_scope] = docs
            if mode != 'lexical':
                for doc in docs:
                    if doc.id in vectors: continue
                    pieces = [_chunk_text(c) for c in embedder.chunk(doc.text)]
                    if not pieces: raise ValueError('Nonempty retrieval card produced no chunks')
                    encoded_vectors = []
                    missing = []
                    for index, piece in enumerate(pieces):
                        value = cache.get(fingerprint, 'passage', piece) if cache else None
                        encoded_vectors.append(value)
                        if value is None: missing.append((index, piece))
                    if missing:
                        batch = list(embedder.embed_passages([piece for _, piece in missing]))
                        if len(batch) != len(missing): raise ValueError('Passage embedding count mismatch')
                        for (index, piece), vector in zip(missing, batch):
                            encoded_vectors[index] = cache.put(fingerprint, 'passage', piece, vector) if cache else normalize(vector)
                    vectors[doc.id] = encoded_vectors
                    chunk_counts[doc.id] = len(pieces)
        prep_ms = (time.perf_counter() - preparation_started) * 1000
        manifest['card_scopes'] = [{'namespace': namespace, 'track': 'ontology' if ontology else 'facts',
                                   'actualcards_digest': documents_fingerprint(docs), 'documents': len(docs)}
                                  for (namespace, ontology), docs in sorted(documents_by_scope.items())]
        manifest['run_id'] = digest(manifest)
        rows = []
        for query in queries:
            docs = documents_by_scope[(query.get('namespace') or dataset['namespace'], query['track'] == 'ontology')]
            by_key = defaultdict(list)
            for doc in docs: by_key[aliases.get(logical_key(doc), logical_key(doc))].append(doc)
            tick = time.perf_counter()
            channels, similarities = [], {}
            if mode != 'lexical':
                # Query encoding is intentionally measured on every query. A
                # reusable passage cache must not fake warm query latency.
                qv = normalize(embedder.embed_query(query['question']))
                for key, cards in by_key.items():
                    all_vectors = [v for card in cards for v in vectors[card.id]]
                    if any(len(v) != len(qv) for v in all_vectors): raise ValueError('Mixed embedding dimensions')
                    similarities[key] = max(sum(a * b for a, b in zip(qv, v)) for v in all_vectors)
                channels.append([key for key, _ in sorted(similarities.items(), key=lambda p: (-p[1], p[0]))[:candidate_budget]])
            if mode in {'lexical', 'hybrid', 'hybrid_graph'}:
                counts = {key: Counter(tokens('\n'.join(card.text for card in cards))) for key, cards in by_key.items()}
                avg_len = sum(sum(c.values()) for c in counts.values()) / max(1, len(counts))
                scores = {}
                for key, count in counts.items():
                    total = 0
                    for token in set(tokens(query['question'])):
                        df = sum(token in other for other in counts.values())
                        tf = count[token]
                        idf = math.log(1 + (len(counts) - df + .5) / (df + .5))
                        total += idf * tf * 2.2 / (tf + 1.2 * (.25 + .75 * sum(count.values()) / max(1, avg_len))) if tf else 0
                    if total: scores[key] = total
                lexical = [key for key, _ in sorted(scores.items(), key=lambda p: (-p[1], p[0]))[:candidate_budget]]
                channels.append(lexical)
                if mode == 'lexical': similarities = scores
            if mode in {'hybrid', 'hybrid_graph'}:
                fused = _rrf(channels)
            else:
                fused = [(key, similarities[key]) for key in channels[0]]
            if mode == 'hybrid_graph' and query['track'] != 'ontology':
                scores = dict(fused)
                for source, score in fused[:graph_seeds]:
                    neighbors = []
                    for rel in dataset.get('relations', []):
                        if rel.get('is_rule'): continue
                        if graph_direction in {'out', 'both'} and rel['from'] == source: neighbors.append(rel['to'])
                        if graph_direction in {'in', 'both'} and rel['to'] == source: neighbors.append(rel['from'])
                    for target in sorted(set(neighbors))[:graph_neighbors]:
                        if target in by_key: scores[target] = max(scores.get(target, 0), score * .5)
                fused = sorted(scores.items(), key=lambda p: (-p[1], p[0]))
            ranking = [key for key, _ in fused][:50]
            accepted = bool(fused) and (acceptance_threshold is None or fused[0][1] >= acceptance_threshold)
            rows.append({'id': query['id'], 'source_family': query['source_family'], 'track': query['track'],
                         'provenance': query['provenance'], 'scenario': query.get('scenario'),
                         'task_type': query.get('task_type', 'semantic'),
                         'language': 'zh' if re.search(r'[\u3400-\u9fff]', query['question']) else 'en',
                         'structured_answer_correct': None,
                         'ranked': ranking, 'scores': [v for _, v in fused[:50]], 'accepted': accepted,
                         'latency_ms': (time.perf_counter() - tick) * 1000,
                         'metrics': score_query(query, ranking, accepted=accepted)})
        groups = _groups(rows)
        reasons = []
        if not human.get('ok'): reasons.append('100_real_human_gold_not_locked')
        if limit is not None or (embedder is not None and model.get('real_vectors') is False): reasons.append('smoke_only')
        if split != 'blind': reasons.append('not_blind_test')
        if protocol is None: reasons.append('protocol_not_frozen')
        if mode != 'lexical' and (model.get('real_vectors') is False or not model.get('files_sha256')): reasons.append('real_model_integrity_not_attested')
        return {'format': FORMAT, 'manifest': manifest, 'started_at': started_at, 'results_sha256': digest(rows),
                'rows': rows, 'groups': groups, 'summary': aggregate(rows),
                'index': {'documents': len(vectors) if vectors else sum(map(len, documents_by_scope.values())),
                          'chunks': sum(chunk_counts.values()), 'prepare_ms': prep_ms,
                          'cache_hits': cache.hits if cache else 0, 'cache_misses': cache.misses if cache else 0},
                'promotion': {'status': 'blocked' if reasons else 'requires_paired_report', 'reasons': reasons},
                'interpretation': 'Offline immutable-snapshot experiment. Safety checks are static snapshot assertions, not permission-change or update-race tests. Structured answer correctness is unexecuted; aggregate/path sufficiency and coverage are N/A, and are excluded from selection gates. A count requiring ten sources is not judged by five retrieved objects. Latency includes exact ranking, not HTTP or model cold start.'}
    finally:
        if cache: cache.close()


def report_runs(baseline, candidates, *, protocol=None, iterations=2000):
    from .protocol import RULES, evidence_blockers
    comparisons, records = [], []
    primary = RULES['primary_metric']
    validate_run(baseline)
    for candidate in candidates: validate_run(candidate)
    for candidate in candidates:
        a, b = baseline['manifest'], candidate['manifest']
        for key in ('dataset_sha256', 'fixture_sha256', 'effective_fixture_sha256'):
            if a[key] != b[key]: raise ValueError('Comparison dataset/labels mismatch')
        if a.get('tool_code_sha256') != b.get('tool_code_sha256'): raise ValueError('Comparison tool versions differ; rerun under the same frozen tool code')
        if a['config'].get('scope', 'shared') != b['config'].get('scope', 'shared'):
            raise ValueError('Comparison retrieval scopes differ')
        for track in ('facts', 'ontology'):
            result = paired_clustered_bootstrap([r for r in baseline['rows'] if r['track'] == track and r.get('task_type') not in {'aggregate', 'multihop', 'path', 'count'}],
                                                [r for r in candidate['rows'] if r['track'] == track and r.get('task_type') not in {'aggregate', 'multihop', 'path', 'count'}],
                                                metric=primary, iterations=iterations)
            comparisons.append({**result, 'candidate': b['run_id'], 'track': track})
        reasons = list(candidate['promotion']['reasons'])
        if not b.get('labels_validation', {}).get('ok') or b['labels_validation'].get('reviewed') != RULES['human_real_gold_required']:
            reasons.append('100_real_human_gold_not_locked')
        if b['config'].get('split') != 'blind': reasons.append('not_blind_test')
        if b['config'].get('smoke_limit') is not None: reasons.append('smoke_only')
        if not b.get('tool_code_sha256'): reasons.append('tool_version_not_frozen')
        reasons.extend(evidence_blockers(protocol, candidate))
        if candidate['summary']['safety_violations']: reasons.append('safety_violation')
        facts = candidate['groups']['selection_facts']
        fpr = facts['family_macro_metrics'].get('noanswer_false_positive')
        if fpr is None or fpr > RULES['noanswer_false_positive_maximum']: reasons.append('noanswer_gate')
        coverage = facts['family_macro_metrics'].get('answerable_coverage')
        if coverage is None or coverage < RULES['answerable_coverage_minimum']: reasons.append('coverage_gate')
        for name, old in baseline['groups'].items():
            if not (name.startswith('task:') or name.startswith('language:')) or name in {'task:aggregate', 'task:multihop', 'task:path', 'task:count'} or not old['queries']: continue
            new = candidate['groups'].get(name)
            x = old['family_macro_metrics'].get('recall@5')
            y = new['family_macro_metrics'].get('recall@5') if new else None
            if x is not None and (y is None or x - y > RULES['critical_group_recall5_maximum_decline'] + 1e-12): reasons.append('critical_group_regression:' + name)
        real_rows = [row for row in candidate['rows'] if row['provenance'] == 'real_human_locked']
        real_families = len({row['source_family'] for row in real_rows})
        records.append({'run_id': b['run_id'], 'blocked_reasons': sorted(set(reasons)),
                        'real_queries': len(real_rows), 'real_source_families': real_families,
                        'warnings': ['Few real source families: repeated questions are not independent evidence.'] if real_families < 10 else []})
    comparisons = holm(comparisons)
    for record in records:
        evidence = next(c for c in comparisons if c['candidate'] == record['run_id'] and c['track'] == 'facts')
        if not evidence['ci95'] or evidence['ci95'][0] <= 0 or evidence['difference'] < RULES['observed_improvement_minimum'] or not evidence['reject_holm']:
            record['blocked_reasons'].append('improvement_not_demonstrated')
        record['status'] = 'blocked' if record['blocked_reasons'] else 'eligible_for_manual_promotion'
    return {'format': FORMAT, 'baseline': baseline['manifest']['run_id'], 'comparisons': comparisons,
            'promotion': records, 'protocol_rules': RULES,
            'primary_estimand': 'source-family macro mean; query means are secondary descriptive statistics',
            'note': 'Local attestations and hashes establish reproducibility, not an authenticated human identity or production approval.'}
