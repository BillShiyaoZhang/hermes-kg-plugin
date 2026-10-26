"""Readiness is a read-only audit, not fabricated human or online evidence."""
from contextlib import redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kg.research.common import digest
from kg.research.fixtures import prepare_fixture
from kg.research.protocol import protocol_template
from kg.research.runner import evaluate


SPEC = importlib.util.spec_from_file_location('research_readiness', Path(__file__).resolve().parents[3] / 'scripts/research_readiness.py')
readiness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(readiness)


class ResearchReadinessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.folder = self.root / 'reports/private/sealed'
        self.dataset, self.fixture, self.labels = prepare_fixture()
        self.write('dataset.json', self.dataset)
        self.write('fixture.json', self.fixture)
        self.write('labels-template.json', self.labels)
        self.write('protocol-template.json', protocol_template())
        self.spec = {'format': readiness.FORMAT, 'dataset': self.ref('dataset.json'),
                     'fixture': self.ref('fixture.json'), 'labels': self.ref('labels-template.json'),
                     'protocol': self.ref('protocol-template.json'), 'targets': []}

    def write(self, name, value):
        path = self.folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return path

    def ref(self, name):
        return readiness.reference(self.folder / name, self.root)

    def checks(self, report, name):
        return [row for row in report['checks'] if row['id'] == name]

    def run_target(self, **patches):
        value = evaluate(self.dataset, self.fixture, mode='lexical', track='facts', limit=2)
        value['manifest'].update(patches)
        value['manifest']['run_id'] = digest({k: v for k, v in value['manifest'].items() if k != 'run_id'})
        self.write('run.json', value)
        self.spec['targets'] = [{'id': 'baseline', 'run': self.ref('run.json')},
                                {'id': 'candidate', 'run': self.ref('run.json')}]
        return value

    def test_zero_reviewed_is_blocked_and_never_changes_any_input(self):
        before = {path: path.read_bytes() for path in self.folder.iterdir()}
        report = readiness.audit(self.spec, root=self.root)
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(report['human_reviewed'], 0)
        self.assertEqual(report['human_required'], 100)
        self.assertEqual(report['promotion'], 'blocked')
        self.assertFalse(report['blind_executed_by_audit'])
        self.assertEqual(report['external_model_calls'], 0)
        self.assertEqual(before, {path: path.read_bytes() for path in self.folder.iterdir()})

    def test_tampered_reference_and_outside_paths_are_blocked_before_read(self):
        self.write('dataset.json', {**self.dataset, 'changed': True})
        report = readiness.audit(self.spec, root=self.root)
        self.assertEqual(self.checks(report, 'dataset_reference')[0]['status'], 'blocked')
        outside = self.root / 'not-reports.json'
        outside.write_text('{"private":"not to be read"}', encoding='utf-8')
        ref = {'path': str(outside), 'sha256': '0' * 64}
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('must not open outside file')):
            with self.assertRaisesRegex(ValueError, 'outside'):
                readiness.Reader(self.root).read(ref)

    def test_source_v2_is_compatible_but_changed_real_rows_are_not(self):
        source_fixture = deepcopy(self.fixture)
        # Only ontology allocation differs; the whole real review is stable.
        source_fixture['queries'] = [q for q in source_fixture['queries'] if q['id'] != 'ont-08-4']
        extra = deepcopy(next(q for q in source_fixture['queries'] if q['id'] == 'ont-07-2'))
        extra.update(id='ont-07-3', question='Another authored definition lookup')
        source_fixture['queries'].append(extra)
        self.write('old-dataset.json', self.dataset)
        self.write('old-fixture.json', source_fixture)
        self.spec['review_source'] = {'dataset': self.ref('old-dataset.json'), 'fixture': self.ref('old-fixture.json')}
        report = readiness.audit(self.spec, root=self.root)
        self.assertEqual(self.checks(report, 'review_source_import_compatibility')[0]['status'], 'passed')
        next(q for q in source_fixture['queries'] if q.get('provenance') == 'real_pending_human')['source_fingerprint'] = 'changed'
        self.write('old-fixture.json', source_fixture)
        self.spec['review_source']['fixture'] = self.ref('old-fixture.json')
        report = readiness.audit(self.spec, root=self.root)
        self.assertEqual(self.checks(report, 'review_source_import_compatibility')[0]['status'], 'blocked')

    def test_dev_run_integrity_is_not_blind_or_locked_human_completion(self):
        self.run_target()
        report = readiness.audit(self.spec, root=self.root)
        self.assertTrue(all(row['status'] == 'passed' for row in self.checks(report, 'run_integrity_and_target_binding')))
        self.assertTrue(all(row['status'] == 'not_run' for row in self.checks(report, 'blind_execution')))
        self.assertTrue(all(row['status'] == 'blocked' for row in self.checks(report, 'run_human_binding')))
        self.assertEqual(report['status'], 'blocked')

    def test_wrong_tool_or_dataset_run_cannot_count_as_current_target(self):
        self.run_target(tool_code_sha256='0' * 64)
        report = readiness.audit(self.spec, root=self.root)
        self.assertTrue(all(row['status'] == 'blocked' for row in self.checks(report, 'run_integrity_and_target_binding')))

    def test_corrupt_derived_run_summary_is_rejected(self):
        value = self.run_target()
        value['summary']['queries'] = 9999
        self.write('run.json', value)
        for target in self.spec['targets']:
            target['run'] = self.ref('run.json')
        report = readiness.audit(self.spec, root=self.root)
        self.assertTrue(all(row['status'] == 'blocked' for row in self.checks(report, 'run_integrity_and_target_binding')))

    def test_safety_http_binding_needs_all_candidate_dimensions(self):
        manifest = {'tool_code_sha256': 'a' * 64, 'dataset_sha256': 'b' * 64,
                    'config': {'representation': 'entity_relations', 'scope': 'profile', 'model_fingerprint': 'c' * 64}}
        report = {'ok': True, 'tool_code_sha256': 'a' * 64, 'dataset_sha256': 'b' * 64,
                  'representation': 'entity_relations', 'scope': 'profile', 'model_fingerprint': 'c' * 64}
        self.assertTrue(readiness._binding(report, manifest))
        for key in report:
            if key == 'ok':
                continue
            changed = {k: v for k, v in report.items() if k != key}
            self.assertFalse(readiness._binding(changed, manifest), key)
        self.assertFalse(readiness._binding({**report, 'representation': 'values'}, manifest))

    def test_ann_v1_uses_retrieval_scope_not_diagnostic_scope_label(self):
        value = self.run_target()
        manifest, config = value['manifest'], value['manifest']['config']
        scope = manifest['card_scopes'][0]
        ann = {'format': 'kg-retrieval-ann-v1', 'ok': True, 'real_vectors': True,
               'backend': 'neo4j_ann', 'scope': 'synthetic_development_queries', 'retrieval_scope': 'profile',
               'profile_fingerprint': config['model_fingerprint'], 'dataset_sha256': manifest['dataset_sha256'],
               'representation': config['representation'], 'tool_code_sha256': manifest['tool_code_sha256'],
               'namespace': scope['namespace'], 'actualcards_digest': scope['actualcards_digest'],
               'top_k': 50, 'query_count': 2, 'ann_recall_at_50': 1,
               'cleanup': {'complete': True, 'remaining': 0}, 'source': 'isolated_snapshot'}
        self.write('ann.json', ann)
        for target in self.spec['targets']:
            target['evidence'] = {'ann_evidence': self.ref('ann.json')}
        report = readiness.audit(self.spec, root=self.root)
        self.assertTrue(all(row['status'] == 'passed' for row in self.checks(report, 'ann_evidence_scope_binding')))
        del ann['retrieval_scope']
        ann['scope'] = 'profile'
        self.write('ann.json', ann)
        for target in self.spec['targets']:
            target['evidence']['ann_evidence'] = self.ref('ann.json')
        report = readiness.audit(self.spec, root=self.root)
        self.assertTrue(all(row['status'] == 'blocked' for row in self.checks(report, 'ann_evidence_scope_binding')))

    def test_static_fingerprints_and_e2e_templates_are_not_online_or_human_proof(self):
        value = self.run_target()
        manifest = value['manifest']
        authority = {'format': 'kg-retrieval-authority-v1', 'ok': True, 'synthetic_only': True,
                     'tool_code_sha256': manifest['tool_code_sha256'], 'dataset_sha256': manifest['dataset_sha256'],
                     **{key: manifest['config'][key] for key in ('representation', 'scope', 'model_fingerprint')},
                     'run_id': 'a static ANN run is not live authority'}
        self.write('authority.json', authority)
        for target in self.spec['targets']:
            target['evidence'] = {'authority_evidence': self.ref('authority.json')}
        self.write('e2e-template.json', {'entries': [], 'status': 'pending'})
        self.spec['e2e'] = {'human_scores': self.ref('e2e-template.json')}
        report = readiness.audit(self.spec, root=self.root)
        self.assertTrue(all(row['status'] == 'blocked' for row in self.checks(report, 'online_authority_and_generation')))
        self.assertEqual(self.checks(report, 'e2e_human_scores_validated')[0]['status'], 'not_run')

    def test_protocol_external_nested_reference_is_never_opened(self):
        value = self.run_target()
        protocol = protocol_template()
        protocol['evidence']['ann_evidence'] = {'path': str(self.root / 'outside.json'), 'sha256': '0' * 64}
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('outside must not be opened')):
            with self.assertRaisesRegex(ValueError, 'outside'):
                readiness._core_blockers(protocol, value, readiness.Reader(self.root))

    def test_output_never_contains_source_text_questions_or_reviewer_identity(self):
        self.labels['entries'][0].update(question='PRIVATE-QUESTION-SENTINEL', reviewer='PRIVATE-REVIEWER-SENTINEL')
        self.write('labels-template.json', self.labels)
        self.spec['labels'] = self.ref('labels-template.json')
        report = json.dumps(readiness.audit(self.spec, root=self.root))
        self.assertNotIn('PRIVATE-QUESTION-SENTINEL', report)
        self.assertNotIn('PRIVATE-REVIEWER-SENTINEL', report)

    def test_cli_is_blocked_exit_2_and_refuses_overwriting_reports(self):
        output = self.root / 'reports/audit.json'
        manifest = self.write('readiness-manifest.json', self.spec)
        with patch.object(readiness, 'ROOT', self.root), redirect_stdout(io.StringIO()) as stdout:
            code = readiness.main(['--manifest', str(manifest), '--output', str(output)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'blocked')
        before = output.read_bytes()
        with patch.object(readiness, 'ROOT', self.root), redirect_stdout(io.StringIO()):
            self.assertEqual(readiness.main(['--manifest', str(manifest), '--output', str(output)]), 1)
        self.assertEqual(output.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
