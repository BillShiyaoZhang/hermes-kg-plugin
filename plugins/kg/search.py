"""Revision-aware hybrid search over rebuildable KGSearchChunk projections.

No LLM calls. Search never writes, downloads, or creates indexes. Setup and the
console's background index worker call ensure_indexes/index_pending explicitly.
All candidates are re-hydrated from current authoritative KGEntity nodes; chunk
text is never returned to a caller. Old/archived records cannot survive via ANN.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta

from .embedding import DIMENSIONS, EMBEDDING_REVISION, MODEL_REVISION, EmbeddingUnavailable, normalized_vector
from .schema import (ENTITY_LABELS, CUSTOM_TYPE, entity_type, parse_time, relationship_types,
                     runtime_entity_labels, runtime_kind, schema_for_store)

FULLTEXT_INDEX = "kg_search_fulltext_v1"
VECTOR_INDEX = "kg_search_vector_v1"
SOURCE_INDEX = "kg_search_source_v1"
RRF_K = 60
ANN_OVERSAMPLE = 10
ANN_MAX_CANDIDATES = 10000
_EXCLUDED = {"KGQuarantined", "KGLegacyAlias", "KGLegacyUnresolved"}
_TEXT_KEYS = ("foaf_name", "hermes_name", "hermes_aliasNames", "hermes_description",
              "hermes_note", "hermes_preferenceKey", "hermes_preferenceValue",
              "hermes_decisionText", "hermes_decisionContext", "hermes_semanticScope",
              "hermes_eventTime", "hermes_eventEndTime")


def _visible(alias="n"):
    return (f"(({alias}.uri STARTS WITH $prefix OR {alias}.shared=true) "
            f"AND NOT {alias}:KGQuarantined AND NOT {alias}:KGLegacyAlias "
            f"AND NOT {alias}:KGLegacyUnresolved AND coalesce({alias}.hermes_enabled,true) "
            f"AND coalesce({alias}.hermes_status,'') <> 'disabled' "
            f"AND any(label IN labels({alias}) WHERE label IN $entity_labels))")


def _current_chunk():
    return ("c.namespace=$prefix AND c.source_revision=coalesce(n.hermes_revision,0) "
            "AND c.source_updated_at=coalesce(toString(n.hermes_updatedAt),'') "
            "AND c.model_revision=$model_revision AND c.embedding_revision=$embedding_revision")


def _requested_scope(alias="n"):
    # Apply the caller's namespace/type constraints before every channel's
    # candidate budget. Shared data must not crowd out the selected profile.
    return (f"($filter_namespace IS NULL OR {alias}.uri STARTS WITH $filter_namespace) "
            f"AND (size($filter_types)=0 OR any(label IN labels({alias}) WHERE label IN $filter_types))")


def _lookup_uri(expression, outer, *, allow_missing=False):
    """Seek canonical entities by their unique index, with a gated legacy path.

    An unlabeled ``MATCH (n {uri:...})`` cannot use the KGEntity URI constraint.
    The empty UNWIND gates the scan behind an actual zero-row dependency;
    a WHERE-only null guard can be planned after AllNodesScan. This preserves
    old shared external entities without scanning for every canonical result.
    Only library-owned expressions/variable names are passed here.
    """
    return f"""OPTIONAL MATCH (kg_primary:KGEntity {{uri:{expression}}})
        CALL {{
            WITH kg_primary,{outer}
            WITH kg_primary WHERE kg_primary IS NOT NULL
            RETURN kg_primary AS n
            UNION
            WITH kg_primary,{outer}
            UNWIND CASE WHEN kg_primary IS NULL THEN [{expression}] ELSE [] END AS legacy_uri
            {'OPTIONAL MATCH' if allow_missing else 'MATCH'} (n {{uri:legacy_uri}}) WHERE NOT n:KGEntity
            RETURN n
        }}"""


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def entity_document(properties):
    """A deterministic projection of user content, excluding model/admin metadata."""
    pieces = []
    for key in (*_TEXT_KEYS, *sorted(key for key in properties if CUSTOM_TYPE.fullmatch(key))):
        value = properties.get(key)
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value)
        value = str(value).strip()
        if value:
            pieces.append(value)
    return "\n".join(pieces)


def entity_fingerprint(properties):
    return hashlib.sha256(entity_document(properties).encode("utf-8")).hexdigest()


def reciprocal_rank_fusion(rankings, *, k=RRF_K):
    """Merge channel rank, never incomparable Lucene/cosine raw scores."""
    fused = {}
    for channel, candidates in rankings.items():
        seen = set()
        for rank, row in enumerate(candidates, 1):
            uri = row.get("uri")
            if not uri or uri in seen:
                continue
            seen.add(uri)
            item = fused.setdefault(uri, {**row, "rrf_score": 0.0, "channels": []})
            item["rrf_score"] += 1.0 / (k + rank)
            item["channels"].append(channel)
            # Exact identity has a hard priority over relevance scores.
            if channel == "exact":
                item["exact_rank"] = rank
    return sorted(fused.values(), key=lambda r: (r.get("exact_rank", float("inf")),
                                                 -r["rrf_score"], r["uri"]))


def _name_query(question):
    value = re.sub(r"^(?:请问|请介绍一下|你认识|你知道|who\s+is\s+|who's\s+|谁是)", "", question.strip(), flags=re.I)
    value = re.sub(r"(?:是谁|是哪位|是什么人|是做什么的)[？?。.!！吗]*$", "", value)
    return value.strip(" ?？。.!！\"“”").casefold()


def _terms(question):
    words = re.findall(r"[\w\u4e00-\u9fff]+", question.casefold(), re.UNICODE)
    stop = {"a", "an", "the", "is", "are", "what", "who", "where", "when", "did", "do", "i", "my", "me", "about", "does"}
    return list(dict.fromkeys([_name_query(question)] + [w for w in words if w not in stop]))[:24]


class HybridSearch:
    def __init__(self, store, embedder=None):
        self.store, self.embedder = store, embedder
        self.config = store.config
        self._runtime_labels = dict(ENTITY_LABELS)
        self._ontology = None

    @property
    def model_revision(self):
        return getattr(self.embedder, "model_revision", MODEL_REVISION)

    @property
    def embedding_revision(self):
        return getattr(self.embedder, "embedding_revision", EMBEDDING_REVISION)

    def _params(self):
        self._ontology = schema_for_store(self.store)
        self._runtime_labels = runtime_entity_labels(self._ontology)
        return {"prefix": self.config.urn_prefix, "entity_labels": list(self._runtime_labels.values()) + ["foaf:Person"],
                "model_revision": self.model_revision, "embedding_revision": self.embedding_revision}

    def _run(self, query, params=None, *, write=False):
        """Fixed library-owned templates, including read-only CALL procedures.

        KGStore.run_read deliberately rejects all CALLs; do not broaden it just
        to admit vector/fulltext procedures.
        """
        values = {**self._params(), **(params or {})}
        with self.store.driver.session(database=self.config.database,
                                       default_access_mode="WRITE" if write else "READ") as session:
            return self.store._execute(session, "write" if write else "read",
                                       lambda tx: [dict(row) for row in tx.run(query, values)])

    def ensure_indexes(self):
        """Explicit setup, compatible with Neo4j 5.26; no APOC/GDS required."""
        statements = (
            "CREATE CONSTRAINT kg_search_chunk_id IF NOT EXISTS FOR (c:KGSearchChunk) REQUIRE c.id IS UNIQUE",
            f"CREATE INDEX {SOURCE_INDEX} IF NOT EXISTS FOR (c:KGSearchChunk) ON (c.namespace,c.source_uri)",
            f"CREATE FULLTEXT INDEX {FULLTEXT_INDEX} IF NOT EXISTS FOR (c:KGSearchChunk) ON EACH [c.text] OPTIONS {{indexConfig: {{`fulltext.analyzer`: 'standard-no-stop-words', `fulltext.eventually_consistent`: false}}}}",
            f"CREATE VECTOR INDEX {VECTOR_INDEX} IF NOT EXISTS FOR (c:KGSearchChunk) ON c.embedding OPTIONS {{indexConfig: {{`vector.dimensions`: 384, `vector.similarity_function`: 'cosine'}}}}",
        )
        # KGStore._execute(write) acquires the workspace's data lock. Neo4j
        # forbids mixing that data write with schema DDL, so setup uses separate
        # auto-commit schema transactions just like KGStore.ensure_schema().
        with self.store.driver.session(database=self.config.database) as session:
            for query in statements:
                session.run(query).consume()
        return self.status()

    def status(self):
        try:
            indexes = self._run("SHOW INDEXES YIELD name,state,populationPercent,type WHERE name IN $names RETURN name,state,populationPercent,type",
                                {"names": [FULLTEXT_INDEX, VECTOR_INDEX, SOURCE_INDEX]})
            states = {row["name"]: {"state": row["state"], "population_percent": row.get("populationPercent", 0)} for row in indexes}
            for name in (FULLTEXT_INDEX, VECTOR_INDEX, SOURCE_INDEX):
                states.setdefault(name, {"state": "MISSING", "population_percent": 0})
            pending = self._run(f"""MATCH (n) WHERE {_visible()}
                RETURN count(n) AS total,
                    sum(CASE WHEN EXISTS {{ MATCH (c:KGSearchChunk {{source_uri:n.uri}})
                        WHERE {_current_chunk()} AND c.embedding_state='ready' }}
                        THEN 0 ELSE 1 END) AS pending""")
            pending_count = pending[0]["pending"] if pending else 0
            model = self.embedder.status() if self.embedder is not None else {"ready": False, "error": "embedder_not_configured"}
            return {"state": "ready" if all(v["state"] == "ONLINE" for v in states.values()) and not pending_count else "pending",
                    "indexes": states, "pending_count": pending_count,
                    "indexed_count": (pending[0].get("total", pending_count) - pending_count) if pending else 0,
                    "model_ready": model.get("ready", False),
                    "mode": "hybrid" if model.get("ready") and states[VECTOR_INDEX]["state"] == "ONLINE" else "keyword",
                    "model": model}
        except Exception:
            return {"state": "unavailable", "indexes": {}, "pending_count": None,
                    "model": {"ready": False, "error": "search_status_unavailable"}}

    def _sources(self):
        return self._run(f"""MATCH (n) WHERE {_visible()}
            OPTIONAL MATCH (c:KGSearchChunk {{source_uri:n.uri, namespace:$prefix}})
            WHERE {_current_chunk()}
            RETURN properties(n) AS properties, labels(n) AS labels,
                coalesce(toString(n.hermes_updatedAt),'') AS updated,
                collect(DISTINCT {{fingerprint:c.source_fingerprint, state:c.embedding_state}}) AS indexed
            ORDER BY CASE WHEN properties.uri STARTS WITH $prefix THEN 0 ELSE 1 END,properties.uri""")

    def index_pending(self, limit=100, *, force=False):
        """Index a bounded batch; an unavailable model still builds lexical chunks.

        A later call retries pending vector rows. Content writes retain their own
        atomic receipt contract; search projections can always be rebuilt.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ValueError("Index limit must be from 1 to 10000")
        return self._index_sources(self._sources(), limit=limit, force=force)

    def index_uris(self, uris, *, limit=100):
        """Refresh committed owned facts before acknowledging a Web mutation.

        The initialized model may encode immediately; a cold model is never
        loaded or downloaded here. Oversized sets are reported as degraded,
        rather than silently claiming every changed entity was refreshed.
        """
        if not isinstance(uris, (list, tuple)) or any(not isinstance(uri, str) for uri in uris):
            raise ValueError("Index URIs must be a list of strings")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("Targeted index limit must be from 1 to 1000")
        owned = list(dict.fromkeys(uri for uri in uris if uri.startswith(self.config.urn_prefix)))
        selected = owned[:limit]
        sources = self._run(f"""UNWIND $uris AS source_uri
            {_lookup_uri('source_uri', 'source_uri')}
            WITH n WHERE {_visible()} AND n.uri STARTS WITH $prefix
            OPTIONAL MATCH (c:KGSearchChunk {{source_uri:n.uri,namespace:$prefix}})
            WHERE {_current_chunk()}
            RETURN properties(n) AS properties,labels(n) AS labels,
                coalesce(toString(n.hermes_updatedAt),'') AS updated,
                collect(DISTINCT {{fingerprint:c.source_fingerprint,state:c.embedding_state}}) AS indexed
            ORDER BY properties.uri""", {"uris": selected}) if selected else []
        report = self._index_sources(sources, limit=limit, force=False, ready_only=True,
                                     cleanup_uris=selected)
        omitted = len(owned) - len(selected)
        if omitted:
            report['errors'].append('targeted_index_limit')
        report.update({'requested': len(owned), 'omitted': omitted,
                       'state': 'degraded' if report['errors'] or report['pending'] or report['stale_skipped'] else 'current',
                       'lexical': 'current' if not omitted and not report['stale_skipped'] else 'degraded'})
        # Absence of embeddings is a semantic limitation even when no active
        # source remains (e.g. an archive), while visibility remains immediate.
        ready = self.embedder is not None and self.embedder.status().get('ready')
        report['semantic'] = 'current' if ready and report['state'] == 'current' else 'degraded'
        if report['semantic'] != 'current':
            report['state'] = 'degraded'
            if not ready and 'embedding_unavailable' not in report['errors']:
                report['errors'].append('vector_pending')
        return report

    def _index_sources(self, sources, *, limit, force=False, ready_only=False, cleanup_uris=None):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ValueError("Index limit must be from 1 to 10000")
        report = {"indexed": 0, "vector_indexed": 0, "pending": 0, "stale_skipped": 0, "errors": []}
        model_status = self.embedder.status() if self.embedder is not None else {}
        can_vectorize = self.embedder is not None and model_status.get("cached") is not False and not model_status.get("error")
        if ready_only:
            can_vectorize = bool(self.embedder is not None and model_status.get('ready'))
        for source in sources:
            props = source["properties"]
            fingerprint = entity_fingerprint(props)
            indexed = source.get("indexed", [])
            if not force and any(i.get("fingerprint") == fingerprint and i.get("state") == "ready" for i in indexed):
                continue
            if not force and not can_vectorize and any(i.get("fingerprint") == fingerprint for i in indexed):
                # Do not repeatedly rewrite the same first batch while offline;
                # later sources still need their lexical projection built.
                continue
            if report["indexed"] + report["stale_skipped"] >= limit:
                break
            text = entity_document(props) or props["uri"]
            vectors, chunks, embedding_state = [], [(text, None, 0)], "pending"
            if self.embedder is not None and (not ready_only or can_vectorize):
                try:
                    token_chunks = self.embedder.chunk(text)
                    chunks = [(c.text, c.token_count, c.index) for c in token_chunks]
                    vectors = self.embedder.embed_passages([c[0] for c in chunks])
                    if len(vectors) != len(chunks):
                        raise ValueError("Embedding count mismatch")
                    vectors = [normalized_vector(v) for v in vectors]
                    embedding_state = "ready"
                except Exception:
                    # No truncation masquerading as a complete vector index.
                    # The raw lexical-only chunk is replaced by token chunks once
                    # the explicit model preparation succeeds.
                    vectors, chunks = [], [(text, None, 0)]
                    if "embedding_unavailable" not in report["errors"]:
                        report["errors"].append("embedding_unavailable")
            revision = props.get("hermes_revision", 0)
            updated = source.get("updated", str(props.get("hermes_updatedAt") or ""))
            rows = []
            for index, (value, tokens, ordinal) in enumerate(chunks):
                identifier = hashlib.sha256(_json([self.config.urn_prefix, props["uri"], revision, updated,
                                                    fingerprint, self.model_revision, self.embedding_revision, ordinal]).encode()).hexdigest()
                row = {"id": identifier, "source_uri": props["uri"], "source_revision": revision,
                       "source_updated_at": updated, "source_fingerprint": fingerprint,
                       "namespace": self.config.urn_prefix, "text": value, "chunk_index": ordinal,
                       "token_count": tokens, "model_revision": self.model_revision,
                       "embedding_revision": self.embedding_revision, "embedding_state": embedding_state}
                if vectors:
                    row["embedding"] = vectors[index]
                rows.append(row)
            committed = self._run(f"""MATCH (n {{uri:$uri}}) WHERE {_visible()}
                AND coalesce(n.hermes_revision,0)=$revision
                AND coalesce(toString(n.hermes_updatedAt),'')=$updated
                OPTIONAL MATCH (old:KGSearchChunk {{source_uri:n.uri,namespace:$prefix}})
                WITH n,collect(old) AS obsolete
                FOREACH (old IN obsolete | DELETE old)
                WITH n UNWIND $chunks AS chunk
                MERGE (c:KGSearchChunk {{id:chunk.id}}) SET c=chunk
                RETURN count(c) AS written""",
                {"uri": props["uri"], "revision": revision, "updated": updated, "chunks": rows}, write=True)
            if committed and committed[0].get("written"):
                report["indexed"] += 1
                report["vector_indexed" if vectors else "pending"] += 1
            else:
                report["stale_skipped"] += 1
        # Remove derived content for deleted/archived sources without touching facts.
        self._run(f"""MATCH (c:KGSearchChunk) WHERE c.namespace=$prefix
            AND ($cleanup_uris IS NULL OR c.source_uri IN $cleanup_uris)
            {_lookup_uri('c.source_uri', 'c', allow_missing=True)}
            WITH c,n WHERE n IS NULL OR NOT {_visible()}
            DELETE c""", {'cleanup_uris': cleanup_uris}, write=True)
        return report

    def reindex(self, limit=10000):
        return self.index_pending(limit=limit, force=True)

    def _filters(self, filters):
        if filters is not None and not isinstance(filters, dict):
            raise ValueError("Search filters must be an object")
        value = dict(filters or {})
        allowed = {"type", "entity_type", "types", "namespace", "from", "to", "from_date", "to_date", "date_from", "date_to", "end_exclusive"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError("Unsupported search filters: " + ", ".join(sorted(unknown)))
        kinds = value.get("types", value.get("entity_type", value.get("type")))
        ontology = schema_for_store(self.store) if self.config.enabled else None
        self._runtime_labels = runtime_entity_labels(ontology)
        if isinstance(kinds, str):
            kinds = [kinds]
        if kinds is not None and (not isinstance(kinds, (list, tuple)) or len(kinds) > len(self._runtime_labels)):
            raise ValueError("Search types must be an array of supported entity types")
        value["types"] = [self._runtime_labels[entity_type(k, ontology)] for k in kinds] if kinds else []
        if "end_exclusive" in value and not isinstance(value["end_exclusive"], bool):
            raise ValueError("end_exclusive must be boolean")
        start = value.get("from", value.get("from_date", value.get("date_from")))
        end = value.get("to", value.get("to_date", value.get("date_to")))
        value["start"] = parse_time(start, self.config.timezone) if start else None
        value["end"] = parse_time(end, self.config.timezone) if end else None
        if value["start"] and value["end"] and value["start"] > value["end"]:
            raise ValueError("Search date range starts after its end")
        namespace = value.get("namespace")
        if namespace is not None and (not isinstance(namespace, str) or not namespace.startswith("urn:") or not namespace.endswith(":")):
            raise ValueError("Namespace must be a URN prefix")
        return value

    def _accept(self, row, filters):
        props, labels = row["properties"], set(row["labels"])
        uri = props.get("uri", "")
        if labels & _EXCLUDED or not labels.intersection(set(self._runtime_labels.values()) | {"foaf:Person"}):
            return False
        if not (uri.startswith(self.config.urn_prefix) or props.get("shared") is True):
            return False
        if props.get("hermes_enabled") is False or props.get("hermes_status") == "disabled":
            return False
        if filters["types"] and not labels.intersection(filters["types"]):
            return False
        if filters.get("namespace") and not uri.startswith(filters["namespace"]):
            return False
        candidate = row.get("candidate", {})
        if candidate.get("revision") is not None and candidate["revision"] != props.get("hermes_revision", 0):
            return False
        if candidate.get("updated") is not None and candidate["updated"] != row.get("updated", str(props.get("hermes_updatedAt") or "")):
            return False
        if filters["start"] or filters["end"]:
            if props.get("hermes_timePrecision") == "unknown":
                return False
            try:
                raw = props.get("hermes_eventTime", props.get("event:time"))
                start = parse_time(raw.to_native() if hasattr(raw, "to_native") else raw, self.config.timezone)
                raw_end = props.get("hermes_eventEndTime")
                end = parse_time(raw_end.to_native() if hasattr(raw_end, "to_native") else raw_end, self.config.timezone) if raw_end else None
                if end is None and props.get("hermes_timePrecision") == "day":
                    end = start + timedelta(days=1)
            except (TypeError, ValueError, OverflowError):
                return False
            if filters["start"] and ((end <= filters["start"]) if end else start < filters["start"]):
                return False
            if filters["end"] and (start >= filters["end"] if filters.get("end_exclusive", True) else start > filters["end"]):
                return False
        return True

    @staticmethod
    def _result(row):
        p, candidate = row["properties"], row["candidate"]
        canonical_kind = runtime_kind(row['labels'])
        kind = ENTITY_LABELS.get(canonical_kind, canonical_kind) or 'Person'
        try:
            evidence = json.loads(p.get("hermes_evidenceJson") or "[]")
        except (ValueError, TypeError):
            evidence = []
        if not isinstance(evidence, list):
            evidence = []
        evidence = [entry for entry in evidence if isinstance(entry, dict)]
        business_keys = set(_TEXT_KEYS) | {"uri", "hermes_eventType", "hermes_timePrecision",
            "hermes_eventStatus", "hermes_timeYearInferred", "hermes_status", "hermes_statusNote",
            "geo_lat", "geo_long", "hermes_revision"}
        result = {"uri": p["uri"], "type": kind, "name": p.get("foaf_name", p.get("hermes_name", p.get("foaf:name"))),
                  "revision": p.get("hermes_revision", 0), "score": candidate.get("rrf_score", 0),
                  "version": p.get("hermes_revision", 0), "origin": p.get("hermes_origin"),
                  "user_confirmed": p.get("hermes_userConfirmed") is True, "evidence": evidence,
                  "properties": {key: value for key, value in p.items() if key in business_keys or CUSTOM_TYPE.fullmatch(key)},
                  "channels": candidate.get("channels", []), "matched_alias_uri": candidate.get("matched_alias_uri")}
        mapping = {"description": "hermes_description", "note": "hermes_note", "time": "hermes_eventTime",
                   "end_time": "hermes_eventEndTime", "time_precision": "hermes_timePrecision",
                   "event_status": "hermes_eventStatus", "alias_names": "hermes_aliasNames",
                   "preference_key": "hermes_preferenceKey", "preference_value": "hermes_preferenceValue",
                   "decision_text": "hermes_decisionText", "decision_context": "hermes_decisionContext",
                   "semantic_scope": "hermes_semanticScope", "status": "hermes_status", "status_note": "hermes_statusNote"}
        result.update({key: p[prop] for key, prop in mapping.items() if prop in p})
        if result.get("time_precision") == "unknown":
            result["time"] = result["end_time"] = None
        return result

    def search(self, question, limit=10, filters=None):
        if not isinstance(question, str) or not question.strip():
            raise ValueError("A nonempty search question is required")
        if len(question) > 20000:
            raise ValueError("Search question exceeds 20000 characters")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("Search limit must be from 1 to 100")
        filters = self._filters(filters)
        if not self.config.enabled:
            return {"kg_results": [], "mode": "disabled", "index": {"state": "disabled"}, "warnings": []}
        question = question.strip()
        candidate_limit = min(1000, max(50, limit * 10))
        scope = {"filter_namespace": filters.get("namespace"), "filter_types": filters["types"]}
        warnings, rankings = [], {}
        index = self.status()
        exact_query = f"""MATCH (n) WHERE {_visible()} AND {_requested_scope()} AND (
            n.uri=$question OR toLower(coalesce(n.foaf_name,n.hermes_name,n['foaf:name'],''))=$name
            OR any(alias IN coalesce(n.hermes_aliasNames,[]) WHERE toLower(alias)=$name)
            OR EXISTS {{ MATCH (alias:KGQuarantined {{uri:$question}})
                WHERE alias.hermes_aliasConfirmed=true AND alias.hermes_canonicalUri=n.uri
                AND (alias.uri STARTS WITH $prefix OR alias.shared=true) }})
            RETURN n.uri AS uri,coalesce(n.hermes_revision,0) AS revision,
                coalesce(toString(n.hermes_updatedAt),'') AS updated,
                CASE WHEN n.uri<>$question AND $question STARTS WITH 'urn:' THEN $question ELSE null END AS matched_alias_uri
            ORDER BY CASE WHEN n.uri=$question THEN 0 ELSE 1 END,n.uri LIMIT $limit"""
        exact_params = {**scope, "question": question, "name": _name_query(question), "limit": candidate_limit}
        exact = self._run(exact_query, exact_params)
        rankings["exact"] = exact
        # A type filter controls returned cards, but an exact named person can
        # still supply context for their events/preferences. Keep this separate
        # from result candidates so unrelated types cannot fill their budget.
        context = self._run(exact_query, {**exact_params, 'filter_types': [], 'limit': 3}) if filters['types'] else []
        lexical = []
        if index.get("indexes", {}).get(FULLTEXT_INDEX, {}).get("state") == "ONLINE":
            # Quote terms and escape Lucene metacharacters: a question is content,
            # never an untrusted Lucene program (wildcards/regex/boolean clauses).
            terms = [t for t in _terms(question) if t]
            lucene = " OR ".join('"' + re.sub(r'([+\-!(){}\[\]^"~*?:\\/])', r'\\\1', t) + '"' for t in terms)
            if lucene:
                try:
                    lexical = self._run(f"""CALL db.index.fulltext.queryNodes($index,$query)
                        YIELD node AS c,score
                        WITH c,score WHERE c.namespace=$prefix AND c.model_revision=$model_revision
                            AND c.embedding_revision=$embedding_revision
                        {_lookup_uri('c.source_uri', 'c')}
                        WITH c,score,n
                        WHERE {_visible()} AND {_current_chunk()} AND {_requested_scope()}
                        RETURN n.uri AS uri,coalesce(n.hermes_revision,0) AS revision,
                            coalesce(toString(n.hermes_updatedAt),'') AS updated,max(score) AS score
                        ORDER BY score DESC,uri LIMIT $limit""",
                        {**scope, "index": FULLTEXT_INDEX, "query": lucene, "limit": candidate_limit})
                except Exception:
                    warnings.append("fulltext_unavailable")
        # Current authoritative text remains searchable while its new revision
        # awaits projection, even if some OTHER indexed fact already matched.
        fresh = self._run(f"""MATCH (n) WHERE {_visible()} AND {_requested_scope()}
                AND (NOT $fresh_only OR NOT EXISTS {{ MATCH (c:KGSearchChunk {{source_uri:n.uri}})
                    WHERE {_current_chunk()} }})
                AND any(term IN $terms WHERE term<>'' AND any(value IN
                    [n.uri,n.foaf_name,n.hermes_name,n.hermes_description,n.hermes_note,
                     n.hermes_preferenceKey,n.hermes_preferenceValue,n.hermes_decisionText,n.hermes_decisionContext]
                     + coalesce(n.hermes_aliasNames,[])
                     + [key IN keys(n) WHERE key STARTS WITH 'x_' | n[key]]
                     WHERE toLower(CASE WHEN valueType(value) STARTS WITH 'LIST'
                         THEN reduce(text='',part IN value | text+' '+coalesce(toStringOrNull(part),''))
                         ELSE toStringOrNull(value) END) CONTAINS term))
                RETURN n.uri AS uri,coalesce(n.hermes_revision,0) AS revision,
                    coalesce(toString(n.hermes_updatedAt),'') AS updated ORDER BY updated DESC,uri LIMIT $limit""",
                {**scope, "terms": _terms(question), "limit": candidate_limit, "fresh_only": bool(lexical)})
        fresh_uris = {row['uri'] for row in fresh}
        lexical = fresh + [row for row in lexical if row['uri'] not in fresh_uris]
        rankings["keyword"] = lexical
        # The worker may be downloading/loading under the embedder's lock.
        # Readiness is a nonblocking snapshot; a search must never enter model
        # initialization (or wait for it) merely because Neo4j's ANN index exists.
        if (self.embedder is not None and index.get("model_ready")
                and index.get("indexes", {}).get(VECTOR_INDEX, {}).get("state") == "ONLINE"):
            try:
                vector = normalized_vector(self.embedder.embed_query(question))
                # Neo4j 5 ANN indexes cannot prefilter by namespace/model. A
                # bounded oversample keeps other profiles from consuming the
                # ordinary result budget, without an unbounded vector scan.
                rankings["vector"] = self._run(f"""CALL db.index.vector.queryNodes($index,$ann_limit,$vector)
                    YIELD node AS c,score
                    WITH c,score WHERE c.namespace=$prefix AND c.model_revision=$model_revision
                        AND c.embedding_revision=$embedding_revision
                    {_lookup_uri('c.source_uri', 'c')}
                    WITH c,score,n
                    WHERE {_visible()} AND {_current_chunk()} AND {_requested_scope()} AND c.embedding_state='ready'
                    RETURN n.uri AS uri,coalesce(n.hermes_revision,0) AS revision,
                        coalesce(toString(n.hermes_updatedAt),'') AS updated,max(score) AS score
                    ORDER BY score DESC,uri LIMIT $limit""",
                    {**scope, "index": VECTOR_INDEX, "limit": candidate_limit,
                     "ann_limit": min(ANN_MAX_CANDIDATES, candidate_limit * ANN_OVERSAMPLE), "vector": vector})
            except Exception:
                warnings.append("embedding_unavailable")
        else:
            warnings.append("vector_pending")
        fused = reciprocal_rank_fusion(rankings)
        seed_by_uri = {row['uri']: row for row in fused[:3]}
        for rank, row in enumerate(context, 1):
            seed_by_uri.setdefault(row['uri'], {**row, 'rrf_score': 1.0 / (RRF_K + rank)})
        graph_seeds = sorted(seed_by_uri.values(), key=lambda row: (-row['rrf_score'], row['uri']))[:3]
        if graph_seeds:
            expanded = self._run(f"""UNWIND $seeds AS seed MATCH (source {{uri:seed.uri}})-[r]-(n)
                WHERE {_visible('source')} AND {_visible()}
                    AND {_requested_scope()}
                    AND type(r) IN $relations AND coalesce(source.hermes_revision,0)=seed.revision
                    AND coalesce(toString(source.hermes_updatedAt),'')=seed.updated
                RETURN DISTINCT n.uri AS uri,coalesce(n.hermes_revision,0) AS revision,
                    coalesce(toString(n.hermes_updatedAt),'') AS updated,seed.rrf_score AS source_score
                ORDER BY source_score DESC,uri LIMIT $limit""",
                {**scope, "seeds": graph_seeds, "relations": relationship_types(ontology=self._ontology), "limit": candidate_limit})
            # A graph neighbor is contextual support for a strong direct hit,
            # not an independent relevance vote. Adding all neighbors as an RRF
            # channel unfairly promotes highly connected hubs over precise,
            # isolated preferences and decisions.
            by_candidate = {row["uri"]: row for row in fused}
            for row in expanded:
                score = row.get("source_score", graph_seeds[0]["rrf_score"]) * 0.5
                if row["uri"] in by_candidate:
                    item = by_candidate[row["uri"]]
                    item["rrf_score"] = max(item["rrf_score"], score)
                    if "graph" not in item["channels"]:
                        item["channels"].append("graph")
                else:
                    by_candidate[row["uri"]] = {**row, "rrf_score": score, "channels": ["graph"]}
            fused = sorted(by_candidate.values(), key=lambda r: (r.get("exact_rank", float("inf")),
                                                                 -r["rrf_score"], r["uri"]))
            hydrated = self._run(f"""UNWIND $candidates AS candidate {_lookup_uri('candidate.uri', 'candidate')}
                WITH candidate,n
                WHERE {_visible()} AND coalesce(n.hermes_revision,0)=candidate.revision
                    AND coalesce(toString(n.hermes_updatedAt),'')=candidate.updated
                RETURN properties(n) AS properties,labels(n) AS labels,
                    coalesce(toString(n.hermes_updatedAt),'') AS updated,candidate""", {"candidates": fused})
            by_uri = {r["properties"]["uri"]: r for r in hydrated if self._accept(r, filters)}
            results = [self._result(by_uri[c["uri"]]) for c in fused if c["uri"] in by_uri][:limit]
        else:
            results = []
        semantic_current = index.get('pending_count') == 0 and 'vector' in rankings
        if not semantic_current:
            warnings.append('semantic_index_pending')
        return {"kg_results": results, "mode": "hybrid" if "vector" in rankings else "keyword",
                "index": index, "warnings": list(dict.fromkeys(warnings)),
                "freshness": {'state': 'current' if semantic_current else 'degraded',
                              'lexical': 'current', 'semantic': 'current' if semantic_current else 'degraded',
                              'pending_count': index.get('pending_count')}}
