"""Offline research contracts: fabricated vectors/gold are explicitly test-only."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from kg.research.common import digest, read_json, relation_key, write_json
from kg.research.fixtures import ATTESTATION, apply_labels, prepare_fixture, validate_fixture, validate_labels, write_prepared
from kg.research.metrics import aggregate, holm, paired_clustered_bootstrap, score_query
from kg.research.protocol import RULES, evidence_blockers, protocol_template
from kg.research.review import review_html
from kg.research.runner import SmokeEmbedder, VectorCache, evaluate, normalize, report_runs


class ResearchFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.dataset, cls.fixture, cls.labels = prepare_fixture()

    def test_counts_splits_and_source_families_are_fixed(self):
        queries = self.fixture['queries']
        self.assertEqual(len(queries), 480)
        self.assertEqual(sum(q['track'] == 'ontology' for q in queries), 60)
        self.assertEqual(sum(q['track'] == 'safety' for q in queries), 120)
        for provenance, dev, blind in [('authored_synthetic', 60, 140), ('real_pending_human', 40, 60)]:
            rows = [q for q in queries if q['track'] == 'facts' and q['provenance'] == provenance]
            self.assertEqual(sum(q['split'] == 'dev' for q in rows), dev)
            self.assertEqual(sum(q['split'] == 'blind' for q in rows), blind)
        self.assertTrue(validate_fixture(self.fixture, self.dataset))
        facts = [q for q in queries if q['track'] == 'facts' and q.get('gold')]
        self.assertEqual(sum(q['gold']['answerable'] is False for q in facts), 40)
        self.assertEqual(sum(q['task_type'] == 'aggregate' for q in facts), 20)

    def test_pending_real_slots_cannot_be_counted_as_gold(self):
        report = validate_labels(self.fixture, self.dataset, self.labels)
        self.assertFalse(report['ok'])
        self.assertEqual(report['reviewed'], 0)
        _, validation = apply_labels(self.fixture, self.dataset)
        self.assertFalse(validation['ok'])

    def test_ontology_allocation_is_exactly_20_dev_40_blind_without_family_leakage(self):
        queries = [query for query in self.fixture['queries'] if query['track'] == 'ontology']
        self.assertEqual(sum(query['split'] == 'dev' for query in queries), 20)
        self.assertEqual(sum(query['split'] == 'blind' for query in queries), 40)
        families = {}
        for query in queries:
            families.setdefault(query['source_family'], []).append(query)
        self.assertEqual(len(families), 20)
        self.assertTrue(all(len({query['split'] for query in family}) == 1 for family in families.values()))
        self.assertEqual(len(families['ontology:x_research_6']), 2)
        self.assertEqual(len(families['ontology:x_research_7']), 4)
        extra = next(query for query in queries if query['id'] == 'ont-08-4')
        self.assertNotIn(extra['question'], [query['question'] for query in queries if query['id'] != extra['id']])
        self.assertTrue(all(term not in extra['question'] for term in extra['gold']['answer_terms']))

    def test_source_change_family_leak_and_answer_copy_are_rejected(self):
        fixture = deepcopy(self.fixture)
        fixture['queries'][1]['split'] = 'blind'
        with self.assertRaisesRegex(ValueError, 'leaks'): validate_fixture(fixture, self.dataset)
        fixture = deepcopy(self.fixture)
        fixture['queries'][0]['question'] += fixture['queries'][0]['gold']['answer_terms'][0]
        with self.assertRaisesRegex(ValueError, 'copied'): validate_fixture(fixture, self.dataset)
        changed = deepcopy(self.dataset)
        changed['entities'][0]['name'] = 'Changed'
        with self.assertRaisesRegex(ValueError, 'another snapshot'): validate_fixture(self.fixture, changed)

    def test_real_import_is_explicit_and_excludes_test_placeholders(self):
        snapshot = {'dataset': {'namespace': 'urn:private:kg:', 'entities': []}}
        with self.assertRaisesRegex(ValueError, 'authorization'): prepare_fixture(real_snapshot=snapshot)
        snapshot['authorization'] = 'local_authorized_snapshot'
        snapshot['dataset']['entities'] = [{'uri': 'urn:private:kg:concept-a', 'type': 'concept', 'name': 'synthetic fixture', 'props': {}, 'source_family': 'one'}]
        _, fixture, _ = prepare_fixture(real_snapshot=snapshot)
        self.assertEqual(fixture['real_source_manifest']['excluded_test_or_placeholder_entities'], 1)
        self.assertFalse(any(q.get('source_refs') for q in fixture['queries']))

    def test_prepare_never_overwrites_review_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            write_prepared(directory)
            with self.assertRaisesRegex(ValueError, 'empty'): write_prepared(directory)

    def test_human_review_page_escapes_script_payload_and_has_no_network(self):
        dataset, fixture, labels = prepare_fixture()
        dataset['entities'][0]['name'] = '</script><img src=x onerror=alert(1)>'
        fixture['queries'].append({'provenance': 'real_pending_human', 'id': 'attack', 'split': 'dev',
                                   'source_family': 'one', 'source_refs': [dataset['entities'][0]['uri']]})
        page = review_html(dataset, fixture, labels)
        self.assertNotIn('</script><img', page)
        self.assertIn('\\u003c/script', page)
        self.assertIn("connect-src 'none'", page)
        self.assertNotIn('innerHTML', page)

    def test_full_human_attestation_is_bound_to_source_and_lock(self):
        # A fabricated unit fixture is not imported into actual experiment data.
        snapshot = {'authorization': 'local_authorized_snapshot', 'dataset': {'namespace': 'urn:private:kg:', 'relations': [],
                    'entities': [{'uri': f'urn:private:kg:concept-{n}', 'type': 'concept', 'name': f'Item {n}',
                                  'props': {'description': f'Value {n}'}, 'source_family': f'family-{n}'} for n in range(100)]}}
        dataset, fixture, labels = prepare_fixture(real_snapshot=snapshot)
        real = {q['id']: q for q in fixture['queries'] if q['provenance'] == 'real_pending_human'}
        for row in labels['entries']:
            uri = real[row['id']]['source_refs'][0]
            row.update(question='Which fact is stated in this source?', relevance={uri: 2}, sufficient_sets=[[uri]], answerable=True,
                       status='reviewed', review_origin='human', reviewer='UNIT FIXTURE NOT A REAL REVIEW',
                       reviewed_at='2026-10-24T00:00:00+00:00', attestation=ATTESTATION)
        validation = validate_labels(fixture, dataset, labels)
        self.assertTrue(validation['ok'])
        _, no_lock = apply_labels(fixture, dataset, labels)
        self.assertFalse(no_lock['ok'])
        effective, locked = apply_labels(fixture, dataset, labels, {'format': fixture['format'], 'validation': validation})
        self.assertTrue(locked['ok'])
        self.assertEqual(sum(q['provenance'] == 'real_human_locked' for q in effective['queries']), 100)
        # An initially unassigned slot can be completed by a human using an
        # existing same-split family and an independently authored question.
        first = real[labels['entries'][0]['id']]
        first.update(source_refs=[], source_fingerprint=None, source_family='real:unassigned-unit')
        labels['entries'][0]['source_fingerprint'] = digest(dataset)
        self.assertTrue(validate_labels(fixture, dataset, labels)['ok'])
        # Positive evidence crossing the split is rejected even if the caller
        # keeps a plausible family name on its row.
        other = next(q for q in real.values() if q['split'] == 'blind')
        saved = deepcopy(labels['entries'][0])
        labels['entries'][0].update(relevance={other['source_refs'][0]: 2}, sufficient_sets=[other['source_refs'][:1]])
        self.assertFalse(validate_labels(fixture, dataset, labels)['ok'])
        labels['entries'][0] = saved
        labels['entries'][0]['question'] = 'Changed after lock'
        _, invalid = apply_labels(fixture, dataset, labels, {'format': fixture['format'], 'validation': validation})
        self.assertFalse(invalid['ok'])


class ResearchMetricsTests(unittest.TestCase):
    def test_sufficiency_requires_a_complete_alternative_set(self):
        query = {'gold': {'answerable': True, 'relevance': {'a': 2, 'b': 2, 'c': 2}, 'sufficient_sets': [['a', 'b'], ['c']]}}
        short = score_query(query, ['a'])
        self.assertEqual(short['sufficiency@5'], 0)
        self.assertEqual(short['answerable_coverage'], 0)
        self.assertEqual(short['acceptance_rate'], 1)
        self.assertEqual(score_query(query, ['a', 'b'])['sufficiency@5'], 1)
        self.assertEqual(score_query(query, ['c'])['sufficiency@5'], 1)
        self.assertEqual(score_query(query, ['a', 'b'], accepted=False)['answerable_coverage'], 0)

    def test_k_metrics_are_explicit_and_chunks_cannot_vote_twice(self):
        query = {'gold': {'answerable': True, 'relevance': {'a': 2, 'b': 1}}}
        metrics = score_query(query, ['a', 'a', 'z', 'y', 'x', 'w', 'b'])
        self.assertEqual(metrics['recall@5'], .5)
        self.assertEqual(metrics['recall@20'], 1)
        self.assertEqual(metrics['precision@5'], .2)
        self.assertEqual(metrics['mrr@10'], 1)
        self.assertGreater(metrics['ndcg@10'], 0)

    def test_noanswer_fpr_and_coverage_have_different_denominators(self):
        result = score_query({'gold': {'answerable': False, 'relevance': {}}}, ['irrelevant'])
        self.assertEqual(result['noanswer_false_positive'], 1)
        self.assertIsNone(result['answerable_coverage'])
        self.assertEqual(score_query({'gold': {'answerable': False, 'relevance': {}}}, [], accepted=False)['noanswer_false_positive'], 0)

    def test_aggregate_is_not_treated_as_five_document_answer_coverage(self):
        query = {'task_type': 'aggregate', 'gold': {'answerable': True, 'relevance': {'a': 2}, 'sufficient_sets': [['a']]}}
        result = score_query(query, ['a'])
        self.assertIsNone(result['sufficiency@5'])
        self.assertIsNone(result['answerable_coverage'])
        self.assertIsNone(result['structured_task_success'])
        self.assertEqual(result['recall@5'], 1)

    def test_family_macro_is_not_query_weighted_mean(self):
        rows = [{'id': str(n), 'source_family': 'a' if n < 9 else 'b', 'latency_ms': n,
                 'metrics': {'ndcg@10': 1 if n < 9 else 0}} for n in range(10)]
        result = aggregate(rows)
        self.assertEqual(result['metrics']['ndcg@10'], .9)
        self.assertEqual(result['family_macro_metrics']['ndcg@10'], .5)

    def test_bootstrap_is_paired_clustered_and_seeded(self):
        before = [{'id': str(n), 'source_family': str(n // 2), 'metrics': {'ndcg@10': 0}} for n in range(20)]
        after = deepcopy(before)
        for row in after: row['metrics']['ndcg@10'] = 1
        a = paired_clustered_bootstrap(before, after, iterations=300)
        self.assertEqual(a, paired_clustered_bootstrap(before, after, iterations=300))
        self.assertEqual(a['clusters'], 10)
        self.assertEqual(a['ci95'], [1, 1])
        self.assertEqual(a['difference'], 1)
        with self.assertRaisesRegex(ValueError, 'identical'): paired_clustered_bootstrap(before, after[:-1], iterations=300)

    def test_holm_adjusts_the_whole_comparison_family(self):
        result = holm([{'p': .01}, {'p': .04}, {'p': .2}])
        self.assertEqual([round(r['p_holm'], 2) for r in result], [.03, .08, .2])
        self.assertEqual([r['reject_holm'] for r in result], [True, False, False])


class ResearchRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.dataset, cls.fixture, _ = prepare_fixture()

    def test_smoke_cache_reuse_and_model_namespace_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = VectorCache(Path(directory) / 'vectors.db')
            cache.put('a', 'passage', 'same text', [1, 0])
            self.assertEqual(cache.get('a', 'passage', 'same text'), [1, 0])
            self.assertIsNone(cache.get('b', 'passage', 'same text'))
            self.assertIsNone(cache.get('a', 'query', 'same text'))
            cache.close()

    def test_smoke_run_is_blocked_and_ontology_is_separate(self):
        run = evaluate(self.dataset, self.fixture, embedder=SmokeEmbedder(), track='ontology', limit=2)
        self.assertEqual(len(run['rows']), 2)
        self.assertEqual(run['groups']['facts']['queries'], 0)
        self.assertTrue(all(row['track'] == 'ontology' for row in run['rows']))
        self.assertEqual(run['promotion']['status'], 'blocked')
        self.assertIn('smoke_only', run['promotion']['reasons'])
        self.assertIsNone(run['rows'][0]['structured_answer_correct'])

    def test_static_snapshot_safety_does_not_return_blocked_sources(self):
        run = evaluate(self.dataset, self.fixture, mode='lexical', track='safety')
        self.assertEqual(run['summary']['safety_violations'], 0)
        self.assertEqual(run['groups']['safety']['queries'], 36)
        self.assertIn('not permission-change', run['interpretation'])

    def test_explicit_scope_filters_actual_evidence_uris_and_both_relation_endpoints(self):
        dataset, fixture = deepcopy(self.dataset), deepcopy(self.fixture)
        own = dataset['entities'][0]['uri']
        shared, private = 'urn:foreign-scope:kg:concept-shared', 'urn:foreign-scope:kg:concept-private'
        for uri, visible in ((shared, True), (private, False)):
            dataset['entities'].append({'uri': uri, 'type': 'concept', 'name': 'scopeprobeunittest',
                                        'props': {'shared': visible}, 'source_family': 'scope-unit'})
            dataset['relations'].append({'from': own, 'to': uri, 'type': 'RELATED_TO', 'props': {}})
        fixture['dataset_sha256'] = digest(dataset)
        fixture['queries'][0]['question'] = 'scopeprobeunittest'
        profile = evaluate(dataset, fixture, representation='entity_relations', mode='lexical', track='facts', limit=1)
        shared_run = evaluate(dataset, fixture, representation='entity_relations', mode='lexical', track='facts', limit=1, scope='shared')
        self.assertEqual(profile['manifest']['config']['scope'], 'profile')
        self.assertEqual(profile['rows'][0]['ranked'], [])
        self.assertEqual(set(shared_run['rows'][0]['ranked']), {shared, relation_key(own, 'RELATED_TO', shared)})
        self.assertEqual(shared_run['manifest']['config']['scope'], 'shared')
        self.assertEqual(shared_run['manifest']['card_scopes'][0]['documents'],
                         profile['manifest']['card_scopes'][0]['documents'] + 2)
        # Every projection's doc.namespace is the requesting namespace, so the
        # foreign endpoint above can only be excluded by checking source URIs.
        with self.assertRaisesRegex(ValueError, 'scopes differ'):
            report_runs(profile, [shared_run], iterations=20)
        with self.assertRaisesRegex(ValueError, 'scope'):
            evaluate(dataset, fixture, mode='lexical', scope='arbitrary')

    def test_cli_defaults_to_profile_and_records_explicit_shared_option(self):
        from kg.research.cli import parser
        args = ['evaluate', '--fixture-dir', 'fixture', '--output', 'run.json']
        self.assertEqual(parser().parse_args(args).scope, 'profile')
        self.assertEqual(parser().parse_args(args + ['--scope', 'shared']).scope, 'shared')

    def test_empty_corpus_and_invalid_dimensions_fail(self):
        with self.assertRaisesRegex(ValueError, 'Zero'): normalize([0, 0])
        with self.assertRaisesRegex(ValueError, 'Invalid'): normalize([float('nan')])
        with self.assertRaisesRegex(ValueError, 'required'): evaluate(self.dataset, self.fixture)

    def test_report_cannot_promote_without_locked_gold_and_fixed_evidence(self):
        base = evaluate(self.dataset, self.fixture, mode='lexical', limit=2)
        other = evaluate(self.dataset, self.fixture, mode='lexical', limit=2)
        result = report_runs(base, [other], iterations=200)
        self.assertEqual(result['promotion'][0]['status'], 'blocked')
        self.assertIn('100_real_human_gold_not_locked', result['promotion'][0]['blocked_reasons'])
        self.assertEqual(result['primary_estimand'].split(';')[0], 'source-family macro mean')

    def test_both_run_arms_and_derived_summaries_are_strictly_verified(self):
        base = evaluate(self.dataset, self.fixture, mode='lexical', limit=2)
        other = deepcopy(base)
        for arm in ('baseline', 'candidate'):
            broken = deepcopy(base)
            broken['rows'][0]['metrics']['ndcg@10'] = .123
            with self.subTest(arm=arm), self.assertRaisesRegex(ValueError, 'results integrity'):
                report_runs(broken if arm == 'baseline' else base, [broken if arm == 'candidate' else other], iterations=200)
        broken = deepcopy(base)
        broken['summary']['safety_violations'] = 10
        with self.assertRaisesRegex(ValueError, 'summary'): report_runs(broken, [other], iterations=200)
        broken = deepcopy(base)
        broken['groups']['selection_facts']['metrics']['ndcg@10'] = 1
        with self.assertRaisesRegex(ValueError, 'groups'): report_runs(base, [broken], iterations=200)
        broken = deepcopy(base)
        broken['manifest']['config']['candidate_budget'] = 999
        with self.assertRaisesRegex(ValueError, 'manifest'): report_runs(broken, [other], iterations=200)

    def test_rule_template_cannot_relax_frozen_gates(self):
        rules = protocol_template()
        rules['rules']['http_p95_ms_maximum'] = 100000
        self.assertEqual(RULES['http_p95_ms_maximum'], 3000)
        self.assertEqual(evidence_blockers(rules, {}), ['frozen_protocol_rules_missing_or_changed'])

    def test_real_ann_evidence_needs_content_hash_and_exact_model_dataset(self):
        base = evaluate(self.dataset, self.fixture, mode='lexical', limit=1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ann.json'
            evidence = {'format': 'kg-retrieval-ann-v1', 'ok': True, 'real_vectors': True, 'backend': 'neo4j_ann',
                        'profile_fingerprint': base['manifest']['config']['model_fingerprint'],
                        'dataset_sha256': base['manifest']['dataset_sha256'], 'representation': 'entity', 'top_k': 50,
                        'query_count': 10, 'ann_recall_at_50': .99, 'cleanup': {'complete': True, 'remaining': 0}, 'source': 'isolated_snapshot',
                        'tool_code_sha256': base['manifest']['tool_code_sha256'],
                        'namespace': base['manifest']['card_scopes'][0]['namespace'],
                        'actualcards_digest': base['manifest']['card_scopes'][0]['actualcards_digest']}
            write_json(path, evidence)
            protocol = protocol_template()
            protocol['evidence']['ann_evidence'] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            reasons = evidence_blockers(protocol, base)
            self.assertNotIn('real_ann_evidence_missing_or_below_gate', reasons)
            evidence['profile_fingerprint'] = 'another-model'
            write_json(path, evidence)
            self.assertIn('real_ann_evidence_missing_or_below_gate', evidence_blockers(protocol, base))

    def test_corrupt_cache_and_nonfinite_artifacts_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache.db'
            cache = VectorCache(path)
            cache.put('m', 'passage', 'body', [1, 0])
            cache.db.execute('UPDATE vectors SET dimensions=3')
            cache.db.commit()
            with self.assertRaisesRegex(ValueError, 'Corrupt'): cache.get('m', 'passage', 'body')
            cache.close()
            bad = Path(directory) / 'bad.json'
            bad.write_text('{"p":NaN}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Non-finite'): read_json(bad)
            bad.write_text('{"p":1,"p":2}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Duplicate'): read_json(bad)

    def test_cli_prepare_validate_evaluate_and_report_closed_loop(self):
        from kg.research.cli import main
        from contextlib import redirect_stdout
        import io
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            root = Path(directory)
            self.assertEqual(main(['prepare-fixture', '--output', str(root)]), 0)
            self.assertTrue((root / 'human-review.html').is_file())
            self.assertEqual(main(['validate-labels', '--fixture-dir', str(root), '--labels', str(root / 'labels-template.json')]), 2)
            for name in ('a', 'b'):
                self.assertEqual(main(['evaluate', '--fixture-dir', str(root), '--model', 'smoke', '--limit', '2',
                                       '--output', str(root / (name + '.json')), '--vector-cache', str(root / 'cache.db')]), 0)
            self.assertGreater(read_json(root / 'b.json')['index']['cache_hits'], 0)
            self.assertEqual(main(['report', '--baseline', str(root / 'a.json'), '--candidate', str(root / 'b.json'),
                                   '--resamples', '200', '--output', str(root / 'comparison.json')]), 0)
            self.assertEqual(read_json(root / 'comparison.json')['promotion'][0]['status'], 'blocked')


if __name__ == '__main__': unittest.main()
