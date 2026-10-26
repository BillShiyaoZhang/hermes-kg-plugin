"""Fabricated E2E tool contracts; zero external LLM, profile or database calls."""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kg import research_e2e as e2e
from kg.research import FORMAT
from kg.research.common import digest, write_json
from kg.research.fixtures import ATTESTATION, apply_labels, validate_labels
from kg.research.metrics import aggregate, score_query
from kg.research.runner import _groups, tool_fingerprint, validate_run


def reseal(run):
    run['results_sha256'] = digest(run['rows'])
    run['summary'], run['groups'] = aggregate(run['rows']), _groups(run['rows'])
    manifest = run['manifest']
    manifest['config_sha256'] = digest(manifest['config'])
    manifest['run_id'] = digest({key: value for key, value in manifest.items() if key != 'run_id'})
    assert validate_run(run)


class ReplayFixture:
    """Minimal fabricated 100-human-label shape, never actual reviewed gold."""
    def __init__(self, root):
        self.root = Path(root)
        folder = self.root / 'reports/private/inputs'
        folder.mkdir(parents=True)
        ns = 'urn:e2e-unit:kg:'
        dataset = {'namespace': ns, 'entities': [], 'relations': []}
        queries, entries = [], []
        for n in range(115):
            uri = ns + 'concept-' + str(n)
            entity = {'uri': uri, 'type': 'concept', 'name': 'UNIT ONLY ' + str(n),
                      'source_family': 'family-' + str(n), 'props': {'description': 'Fabricated record ' + str(n)}}
            dataset['entities'].append(entity)
            real = n < 100
            q = {'id': ('real-' if real else 'synthetic-') + str(n), 'namespace': ns,
                 'split': 'dev' if n < 40 else 'blind', 'track': 'facts', 'task_type': 'semantic',
                 'provenance': 'real_pending_human' if real else 'authored_synthetic',
                 'question': '' if real else 'What is stated for this fabricated record?',
                 'source_family': ('real:' if real else '') + entity['source_family'],
                 'source_refs': [uri], 'source_fingerprint': digest(entity),
                 'gold': None if real else {'answerable': True, 'relevance': {uri: 2}, 'sufficient_sets': [[uri]], 'answer_terms': []}}
            queries.append(q)
            if real:
                entries.append({'id': q['id'], 'question': 'What fact does this fabricated record state?',
                    'source_refs': [uri], 'source_family': q['source_family'], 'source_fingerprint': digest(entity),
                    'answerable': True, 'relevance': {uri: 2}, 'sufficient_sets': [[uri]], 'answer_terms': [],
                    'status': 'reviewed', 'review_origin': 'human', 'reviewer': 'UNIT SYNTHETIC LABELS, NOT REAL HUMAN GOLD',
                    'reviewed_at': '2026-10-24T00:00:00Z', 'attestation': ATTESTATION})
        fixture = {'format': FORMAT, 'dataset_sha256': digest(dataset), 'queries': queries}
        labels = {'format': FORMAT, 'dataset_sha256': digest(dataset), 'entries': entries}
        validation = validate_labels(fixture, dataset, labels)
        assert validation['ok']
        lock = {'format': FORMAT, 'validation': validation}
        effective, human = apply_labels(fixture, dataset, labels, lock)
        self.plan = e2e.plan_template()
        self.plan.update(frozen=True, frozen_at='2026-10-24T00:00:00Z', reviewer='SYNTHETIC UNIT PLAN',
                         blind_unseen_until_freeze=True, external_call_authorized=True, tool_code_sha256=tool_fingerprint())
        for name, value in [('dataset', dataset), ('fixture', fixture), ('labels', labels), ('labels_lock', lock)]:
            path = folder / (name + '.json')
            write_json(path, value)
            self.plan['inputs'][name] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        self.plan['task_ids'] = [q['id'] for q in effective['queries'] if q['split'] == 'blind' and q['provenance'] == 'real_human_locked'][:15]
        self.plan['task_ids'] += [q['id'] for q in effective['queries'] if q['provenance'] == 'authored_synthetic']
        self.plan['route'] = {'provider': 'custom', 'model': 'MiniMax-UNIT-NEVER-CALLED', 'endpoint_sha256': 'a' * 64}
        self.runs = {}
        for arm, representation in [('baseline', 'values'), ('candidate', 'entity')]:
            config = {'mode': 'lexical', 'representation': representation, 'scope': 'profile', 'split': 'blind',
                      'track': 'facts', 'smoke_limit': None, 'model_fingerprint': 'lexical-only'}
            rows = []
            for q in effective['queries']:
                if q['split'] != 'blind':
                    continue
                ranked = list(q['source_refs'])
                rows.append({'id': q['id'], 'source_family': q['source_family'], 'track': 'facts',
                    'provenance': q['provenance'], 'task_type': 'semantic', 'language': 'en', 'latency_ms': 0,
                    'ranked': ranked, 'scores': [1] * len(ranked), 'accepted': True, 'metrics': score_query(q, ranked)})
            run = {'format': FORMAT, 'started_at': '2026-10-24T00:01:00Z', 'manifest': {
                'config': config, 'tool_code_sha256': tool_fingerprint(), 'dataset_sha256': digest(dataset),
                'fixture_sha256': digest(fixture), 'effective_fixture_sha256': digest(effective),
                'labels_validation': human, 'model': {}}, 'rows': rows}
            reseal(run)
            self.runs[arm] = run
            self.plan['arms'][arm] = {'config': config, 'config_sha256': digest(config)}
        self.prepared = e2e.prepare_replay(self.plan, self.runs, root=self.root)

    def output(self, name='execution'):
        return self.root / 'reports/private' / name

    def probe(self, *args, **kwargs):
        return {'route': self.plan['route'], 'model_calls': 0}

    def fake_call(self, *args, **kwargs):
        return {'route': self.plan['route'], 'response': {'choices': [{'message': {'content': json.dumps({
            'answer': 'Fabricated replay answer', 'abstain': False, 'citations': []})}}]}}

    def revise_synthetic_gold(self, change):
        values = {name: e2e.read(self.plan['inputs'][name]['path']) for name in self.plan['inputs']}
        change(values['fixture'])
        validation = validate_labels(values['fixture'], values['dataset'], values['labels'])
        values['labels_lock'] = {'format': FORMAT, 'validation': validation}
        effective, human = apply_labels(values['fixture'], values['dataset'], values['labels'], values['labels_lock'])
        by_id = {q['id']: q for q in effective['queries']}
        for name, value in values.items():
            path = Path(self.plan['inputs'][name]['path']); write_json(path, value)
            self.plan['inputs'][name]['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        for run in self.runs.values():
            run['manifest'].update(fixture_sha256=digest(values['fixture']), effective_fixture_sha256=digest(effective), labels_validation=human)
            for row in run['rows']:
                row['task_type'] = by_id[row['id']]['task_type']
                row['metrics'] = score_query(by_id[row['id']], row['ranked'], accepted=row['accepted'])
            reseal(run)


class ResearchE2eTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = ReplayFixture(self.temp.name)

    def test_preflight_uses_real_integrity_and_context_checks_without_any_profile(self):
        f = self.fixture
        with patch.object(e2e, 'profile_request', side_effect=AssertionError('No profile in preflight')):
            prepared = e2e.prepare_replay(f.plan, f.runs, root=f.root)
        self.assertEqual(prepared['planned_calls'], 120)
        self.assertEqual(len(prepared['tasks']), 30)
        self.assertTrue(all(v['context_sufficient'] for v in prepared['contexts']['candidate'].values()))
        self.assertIsNone(prepared['answer_correctness'])
        broken = deepcopy(f.runs)
        broken['baseline']['rows'][0]['ranked'] = []
        with self.assertRaisesRegex(ValueError, 'integrity'):
            e2e.prepare_replay(f.plan, broken, root=f.root)

    def test_unlocked_labels_unfrozen_plan_wrong_task_mix_and_late_freeze_block(self):
        f = self.fixture
        for key, value, message in [('frozen', False, 'frozen'), ('blind_unseen_until_freeze', False, 'before results'),
                                    ('repetitions', 3, '120'), ('max_calls', 121, '120')]:
            plan = deepcopy(f.plan); plan[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, message):
                e2e.prepare_replay(plan, f.runs, root=f.root)
        plan = deepcopy(f.plan); plan['frozen_at'] = '2026-10-24T00:02:00Z'
        with self.assertRaisesRegex(ValueError, 'after the freeze'):
            e2e.prepare_replay(plan, f.runs, root=f.root)
        plan = deepcopy(f.plan); plan['task_ids'][0] = 'real-0'
        with self.assertRaisesRegex(ValueError, 'blind fact'):
            e2e.prepare_replay(plan, f.runs, root=f.root)
        plan = deepcopy(f.plan); plan['inputs']['labels_lock'] = plan['inputs']['labels']
        with self.assertRaisesRegex(ValueError, 'locked'):
            e2e.prepare_replay(plan, f.runs, root=f.root)

    def test_threshold_config_and_artifact_tampering_fail_closed(self):
        f = self.fixture
        plan = deepcopy(f.plan); plan['arms']['baseline']['config_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'predeclared'):
            e2e.prepare_replay(plan, f.runs, root=f.root)
        plan = deepcopy(f.plan); plan['inputs']['dataset']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'digest'):
            e2e.prepare_replay(plan, f.runs, root=f.root)

    def test_context_sufficiency_requires_the_complete_gold_set(self):
        f = self.fixture
        key = f.plan['task_ids'][15]
        other = 'urn:e2e-unit:kg:concept-101'
        def add_required(fixture):
            query = next(q for q in fixture['queries'] if q['id'] == key)
            own = query['source_refs'][0]
            query['gold'].update(relevance={own: 2, other: 2}, sufficient_sets=[[own, other]])
        f.revise_synthetic_gold(add_required)
        row = next(row for row in f.runs['candidate']['rows'] if row['id'] == key)
        row['ranked'].append(other); row['scores'].append(.5); reseal(f.runs['candidate'])
        prepared = e2e.prepare_replay(f.plan, f.runs, root=f.root)
        self.assertFalse(prepared['contexts']['baseline'][key]['context_sufficient'])
        self.assertTrue(prepared['contexts']['candidate'][key]['context_sufficient'])

    def test_structured_evidence_is_not_scored_as_answer_correctness(self):
        f = self.fixture
        key = f.plan['task_ids'][15]
        def structural(fixture):
            next(q for q in fixture['queries'] if q['id'] == key)['task_type'] = 'aggregate'
        f.revise_synthetic_gold(structural)
        prepared = e2e.prepare_replay(f.plan, f.runs, root=f.root)
        self.assertIsNone(prepared['contexts']['candidate'][key]['context_sufficient'])
        self.assertIsNone(prepared['contexts']['candidate'][key]['answer_correctness'])

    def test_execution_code_change_blocks_prepare_and_execute(self):
        f = self.fixture
        with patch.object(e2e, 'execution_code_sha256', return_value='changed'):
            with self.assertRaisesRegex(ValueError, 'implementation changed'):
                e2e.prepare_replay(f.plan, f.runs, root=f.root)
            with self.assertRaisesRegex(ValueError, 'implementation changed'):
                e2e.execute_replay(f.plan, f.prepared, home=f.root, output=f.output(), root=f.root)

    def test_ledger_reservations_are_durable_capped_and_bound_to_one_plan(self):
        path = self.fixture.output('ledger.sqlite3'); path.parent.mkdir(exist_ok=True)
        ledger = e2e.BudgetLedger(path, 'unit-plan')
        self.assertTrue(ledger.reserve('slot', 'request'))
        ledger.close()
        ledger = e2e.BudgetLedger(path, 'unit-plan')
        self.assertFalse(ledger.reserve('slot', 'request'))
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            ledger.reserve('slot', 'changed')
        for i in range(119):
            self.assertTrue(ledger.reserve(str(i), 'request'))
        with self.assertRaisesRegex(ValueError, '120-call'):
            ledger.reserve('over-budget', 'request')
        ledger.close()
        with self.assertRaisesRegex(ValueError, 'another frozen plan'):
            e2e.BudgetLedger(path, 'other-plan')

    def test_unknown_dispatch_is_charged_and_never_retried_on_resume(self):
        f = self.fixture
        calls = []
        def fail(*args, **kwargs):
            calls.append('unknown')
            raise TimeoutError('Synthetic unit timeout; no actual model request')
        first = e2e.execute_replay(f.plan, f.prepared, home=f.root, output=f.output(), root=f.root, probe=f.probe, call=fail)
        self.assertEqual((first['reserved'], first['complete'], first['unknown_or_reserved']), (1, 0, 1))
        def complete(*args, **kwargs):
            calls.append('complete')
            return f.fake_call(*args, **kwargs)
        second = e2e.execute_replay(f.plan, f.prepared, home=f.root, output=f.output(), root=f.root, probe=f.probe, call=complete)
        self.assertEqual((second['reserved'], second['complete'], second['unknown_or_reserved']), (120, 119, 1))
        third = e2e.execute_replay(f.plan, f.prepared, home=f.root, output=f.output(), root=f.root, probe=f.probe, call=complete)
        self.assertEqual(third['reserved_this_attempt'], 0)
        self.assertEqual(len(calls), 120)
        template = e2e.read(f.output() / 'human-scores-template.json')
        self.assertEqual(len(template['entries']), 120)
        self.assertTrue(all(entry['answer_correct'] is None for entry in template['entries']))
        audited = e2e.audit_human_scores(f.output(), template, root=f.root)
        self.assertFalse(audited['ok'])
        self.assertIsNone(audited['answer_accuracy'])

    def test_success_resume_is_idempotent_and_keeps_human_draft(self):
        f = self.fixture
        with patch.object(e2e, 'profile_request', side_effect=AssertionError('Real route must not run')):
            first = e2e.execute_replay(f.plan, f.prepared, home=f.root, output=f.output(), root=f.root, probe=f.probe, call=f.fake_call)
            self.assertEqual((first['reserved'], first['complete']), (120, 120))
            score_path = f.output() / 'human-scores-template.json'
            scores = e2e.read(score_path); scores['entries'][0]['notes'] = 'A human draft must survive resume'
            write_json(score_path, scores)
            second = e2e.execute_replay(f.plan, f.prepared, home=f.root, output=f.output(), root=f.root, probe=f.probe,
                                        call=lambda *a, **k: self.fail('Completed slots must not be resent'))
        self.assertEqual(second['reserved_this_attempt'], 0)
        self.assertEqual(e2e.read(score_path)['entries'][0]['notes'], 'A human draft must survive resume')
        self.assertIsNone(e2e.audit_human_scores(f.output(), scores, root=f.root)['answer_accuracy'])
        # Fabricated unit scoring tests the assessor only, never actual answers.
        scores.update(status='reviewed', attestation='I manually judged generated answers and their supporting evidence.',
                      reviewed_at='2026-10-24T00:00:00Z')
        for entry in scores['entries']:
            raw = e2e.read(entry['output_path'])
            entry.update(status='reviewed', reviewer='UNIT ONLY, NOT HUMAN GOLD',
                         answer_correct=raw['arm'] == 'candidate', evidence_supported=True)
        audited = e2e.audit_human_scores(f.output(), scores, root=f.root)
        self.assertTrue(audited['ok'])
        self.assertEqual(audited['groups']['baseline']['answer_accuracy'], 0)
        self.assertEqual(audited['groups']['candidate']['answer_accuracy'], 1)
        self.assertEqual(audited['paired_tasks'], 30)
        self.assertEqual(audited['candidate_minus_baseline_task_mean'], 1)
        self.assertEqual(audited['promotion'], 'blocked')

    def test_no_authorization_no_home_or_route_drift_prevents_dispatch(self):
        f = self.fixture
        for home, authorized in ((None, True), (f.root, False)):
            plan = deepcopy(f.plan); plan['external_call_authorized'] = authorized
            with self.subTest(home=home), self.assertRaisesRegex(ValueError, 'Explicit --home'):
                e2e.execute_replay(plan, f.prepared, home=home, output=f.output(), root=f.root)
        with self.assertRaisesRegex(ValueError, 'exact frozen'):
            e2e.execute_replay(f.plan, f.prepared, home=f.root, output=f.output(), root=f.root,
                              probe=lambda *a, **k: {'route': {}, 'model_calls': 0}, call=f.fake_call)

    def test_changing_output_or_plan_cannot_reset_canonical_budget(self):
        f = self.fixture
        def fail(*args, **kwargs):
            raise TimeoutError('Unit-only uncertain outcome')
        first = e2e.execute_replay(f.plan, f.prepared, home=f.root, output=f.output(), root=f.root,
                                   probe=f.probe, call=fail)
        self.assertEqual(first['reserved'], 1)
        with self.assertRaisesRegex(ValueError, 'canonical budget ledger'):
            e2e.execute_replay(f.plan, f.prepared, home=f.root, output=f.output('different'), root=f.root,
                               probe=lambda *a, **k: self.fail('Cannot probe another execution'), call=f.fake_call)
        plan, prepared = deepcopy(f.plan), deepcopy(f.prepared)
        plan['reviewer'] = 'Another fabricated plan'; prepared['plan_sha256'] = digest(plan)
        with self.assertRaisesRegex(ValueError, 'canonical budget ledger'):
            e2e.execute_replay(plan, prepared, home=f.root, output=f.output('different-plan'), root=f.root,
                               probe=lambda *a, **k: self.fail('Cannot probe another plan'), call=f.fake_call)

    def test_answer_format_is_not_answer_correctness_and_unknown_citations_fail(self):
        raw = {'choices': [{'message': {'content': '{"answer":"x","abstain":false,"citations":["a"]}'}}]}
        self.assertTrue(e2e.parse_answer(raw, ['a'])['valid'])
        self.assertIsNone(e2e.parse_answer(raw, ['a'])['answer_correctness'])
        self.assertFalse(e2e.parse_answer(raw, ['b'])['valid'])

    def test_output_must_be_inside_private_reports(self):
        with self.assertRaisesRegex(ValueError, 'private'):
            e2e.private_path(self.fixture.root / 'reports/public.json', self.fixture.root)
        with self.assertRaisesRegex(ValueError, 'private'):
            e2e.private_path(self.fixture.root / 'outside.json', self.fixture.root)


class SingleDispatchTests(unittest.TestCase):
    def test_actual_sdk_has_one_mocked_http_attempt_even_on_server_error(self):
        import httpx
        from openai import OpenAI, InternalServerError
        attempts = []
        def transport(request):
            attempts.append(request)
            return httpx.Response(500, json={'error': {'message': 'Synthetic unit server error'}})
        client = OpenAI(api_key='UNIT-NOT-A-REAL-KEY', base_url='https://unit.invalid/v1',
                        http_client=httpx.Client(transport=httpx.MockTransport(transport)), max_retries=5)
        with self.assertRaises(InternalServerError):
            e2e.one_shot_completion(client, model='MiniMax-UNIT', messages=[{'role': 'user', 'content': 'fake'}],
                generation={'temperature': 0, 'max_tokens': 10, 'timeout': 1})
        self.assertEqual(len(attempts), 1)
        client.close()

    def test_unknown_client_adapter_is_rejected_without_any_call(self):
        with self.assertRaisesRegex(RuntimeError, 'Cannot prove one-shot'):
            e2e.one_shot_completion(object(), model='MiniMax-UNIT', messages=[],
                                    generation={'temperature': 0, 'max_tokens': 10, 'timeout': 1})

    def test_default_cli_is_offline_preflight_without_loading_profile(self):
        path = Path(__file__).resolve().parents[3] / 'scripts/research_end_to_end.py'
        spec = importlib.util.spec_from_file_location('e2e_cli_unit', path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        stream = io.StringIO()
        with patch.object(e2e, 'profile_request', side_effect=AssertionError('Profile must not load')), redirect_stdout(stream):
            self.assertEqual(module.main([]), 2)
        value = json.loads(stream.getvalue())
        self.assertFalse(value['ready'])
        self.assertEqual(value['external_calls'], 0)


if __name__ == '__main__':
    unittest.main()
