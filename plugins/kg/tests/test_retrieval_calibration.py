"""Calibration tool unit tests only: tiny synthetic data, no quality evaluation."""
from contextlib import redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from kg.research import FORMAT
from kg.research.common import digest, write_json
from kg.research.metrics import aggregate, score_query
from kg.research.runner import SmokeEmbedder, _groups, evaluate, validate_run


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('retrieval_calibration_tool', ROOT / 'scripts/calibrate_retrieval.py')
calibration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(calibration)


def reseal(run):
    """Make controlled synthetic variants satisfy the real integrity checker."""
    run['results_sha256'] = digest(run['rows'])
    run['summary'] = aggregate(run['rows'])
    run['groups'] = _groups(run['rows'])
    manifest = run['manifest']
    manifest['config_sha256'] = digest(manifest['config'])
    manifest['run_id'] = digest({key: value for key, value in manifest.items() if key != 'run_id'})
    assert validate_run(run)
    return run


class RetrievalCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        namespace = 'urn:calibration-unit:kg:'
        uri = namespace + 'concept-canary'
        dataset = {'namespace': namespace, 'entities': [
            {'uri': uri, 'type': 'concept', 'name': 'Fabricated archive plan',
             'props': {'description': 'The fictional team owns the fabricated archive plan.'}}
        ], 'relations': []}
        common = {'source_family': 'synthetic-unit-only', 'split': 'dev', 'track': 'facts',
                  'provenance': 'authored_synthetic', 'task_type': 'semantic'}
        positive = {**common, 'id': 'unit-positive', 'question': 'Who owns this archive plan?',
                    'gold': {'answerable': True, 'relevance': {uri: 2}, 'sufficient_sets': [[uri]], 'answer_terms': []}}
        negative = {**common, 'id': 'unit-negative', 'question': 'Which price was agreed for the archive plan?',
                    'gold': {'answerable': False, 'relevance': {}, 'answer_terms': []}}
        fixture = {'format': FORMAT, 'dataset_sha256': digest(dataset), 'queries': [positive, negative]}
        cls.template = evaluate(dataset, fixture, embedder=SmokeEmbedder(), mode='vector', track='facts')
        cls.queries = {query['id']: query for query in fixture['queries']}

    def controlled(self, positive=.9, negative=.2, *, empty=(), sufficient=True):
        run = deepcopy(self.template)
        for row in run['rows']:
            row['scores'] = [] if row['id'] in empty else [positive if row['id'] == 'unit-positive' else negative]
            if row['id'] in empty:
                row['ranked'] = []
            row['accepted'] = bool(row['scores'])
            query = deepcopy(self.queries[row['id']])
            if not sufficient and row['id'] == 'unit-positive':
                query['gold']['sufficient_sets'] = [['urn:calibration-unit:kg:missing']]
            row['metrics'] = score_query(query, row['ranked'], accepted=row['accepted'])
        return reseal(run)

    def test_real_smoke_run_validates_and_never_promotes(self):
        self.assertTrue(validate_run(self.template))
        report = calibration.calibrate(self.template)
        self.assertEqual(report['positive_questions'], 1)
        self.assertEqual(report['noanswer_questions'], 1)
        self.assertEqual(report['promotion'], 'blocked')
        self.assertIn('Scores are not probabilities', report['interpretation'])
        json.dumps(report, allow_nan=False)

    def test_corrupt_results_manifest_and_derived_groups_are_rejected(self):
        for mutation, message in [
            (lambda run: run['rows'][0]['scores'].__setitem__(0, .123), 'results integrity'),
            (lambda run: run['manifest']['config'].__setitem__('candidate_budget', 999), 'manifest integrity'),
            (lambda run: run['summary'].__setitem__('safety_violations', 10), 'summary'),
            (lambda run: run['groups']['facts'].__setitem__('queries', 999), 'groups'),
        ]:
            with self.subTest(message=message):
                run = deepcopy(self.template)
                mutation(run)
                with self.assertRaisesRegex(ValueError, message):
                    calibration.calibrate(run)

    def test_blind_and_regression_are_rejected_even_with_valid_hashes(self):
        for split in ('blind', 'regression'):
            with self.subTest(split=split):
                run = deepcopy(self.template)
                run['manifest']['config']['split'] = split
                reseal(run)
                with self.assertRaisesRegex(ValueError, 'development data, never blind'):
                    calibration.calibrate(run)

    def test_one_threshold_cannot_pass_inseparable_scores(self):
        report = calibration.calibrate(self.controlled(positive=.2, negative=.9))
        self.assertFalse(report['feasible'])
        self.assertEqual(report['selected']['noanswer_fpr'], 0)
        self.assertEqual(report['selected']['sufficient_coverage'], 0)
        self.assertEqual(report['promotion'], 'blocked')
        # A truly feasible synthetic curve remains exploratory, never promoted.
        separable = calibration.calibrate(self.controlled())
        self.assertTrue(separable['feasible'])
        self.assertEqual(separable['selected']['noanswer_fpr'], 0)
        self.assertEqual(separable['selected']['sufficient_coverage'], 1)
        self.assertEqual(separable['promotion'], 'blocked')

    def test_accepting_without_complete_evidence_does_not_count_as_coverage(self):
        report = calibration.calibrate(self.controlled(sufficient=False))
        self.assertFalse(report['feasible'])
        self.assertTrue(all(point['sufficient_coverage'] == 0 for point in report['curve']))

    def test_empty_results_abstain_and_all_empty_is_not_calibratable(self):
        empty_positive = calibration.calibrate(self.controlled(empty=('unit-positive',)))
        self.assertFalse(empty_positive['feasible'])
        self.assertTrue(all(point['sufficient_coverage'] == 0 for point in empty_positive['curve']))
        empty_negative = calibration.calibrate(self.controlled(empty=('unit-negative',)))
        self.assertTrue(empty_negative['feasible'])
        self.assertTrue(all(point['noanswer_fpr'] == 0 for point in empty_negative['curve']))
        with self.assertRaisesRegex(ValueError, 'finite retrieval scores'):
            calibration.calibrate(self.controlled(empty=('unit-positive', 'unit-negative')))

    def test_missing_positive_or_negative_labels_cannot_calibrate(self):
        for query_id in ('unit-positive', 'unit-negative'):
            run = deepcopy(self.template)
            run['rows'] = [row for row in run['rows'] if row['id'] == query_id]
            reseal(run)
            with self.subTest(query_id=query_id), self.assertRaisesRegex(ValueError, 'answerable and genuine no-answer'):
                calibration.calibrate(run)

    def test_nonfinite_out_of_range_and_nonnumeric_gate_parameters_are_rejected(self):
        for name in ('max_fpr', 'minimum_coverage'):
            for value in (math.nan, math.inf, -math.inf, -.1, 1.1, 10 ** 1000, True, None, '.9'):
                with self.subTest(name=name, value=str(value)), self.assertRaisesRegex(ValueError, 'finite number'):
                    calibration.calibrate(self.template, **{name: value})
        report = calibration.calibrate(self.controlled(), max_fpr=0, minimum_coverage=1)
        self.assertTrue(report['feasible'])

    def test_malformed_scores_and_nonrepresentable_reject_all_are_rejected(self):
        for value in (['.9'], [True], [10 ** 1000], [.9, .8]):
            run = deepcopy(self.template)
            run['rows'][0]['scores'] = value
            reseal(run)
            with self.subTest(value=str(value)), self.assertRaisesRegex(ValueError, 'finite, descending scores'):
                calibration.calibrate(run)
        with self.assertRaisesRegex(ValueError, 'finite reject-all'):
            calibration.calibrate(self.controlled(positive=sys.float_info.max))
        json.dumps(calibration.calibrate(self.controlled(positive=-sys.float_info.max, negative=-sys.float_info.max)), allow_nan=False)

    def test_cli_does_not_overwrite_or_publish_invalid_output(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path, output = Path(directory) / 'run.json', Path(directory) / 'calibration.json'
            write_json(run_path, self.controlled())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(calibration.main(['--run', str(run_path), '--output', str(output)]), 0)
            self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['promotion'], 'blocked')
            old_bytes = output.read_bytes()
            with redirect_stdout(io.StringIO()):
                self.assertEqual(calibration.main(['--run', str(run_path), '--output', str(output)]), 1)
            self.assertEqual(output.read_bytes(), old_bytes)
            output.unlink()
            damaged = deepcopy(self.template)
            damaged['rows'][0]['scores'][0] = .123
            write_json(run_path, damaged)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(calibration.main(['--run', str(run_path), '--output', str(output)]), 1)
            self.assertFalse(output.exists())


class ResearchWheelResourceTests(unittest.TestCase):
    def test_real_build_configuration_ships_protocol_and_readme(self):
        # Build a minimal isolated wheel using the actual setup/config/resources.
        # No model, reports, user state, network, or business database is involved.
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            for relative in ('setup.py', 'pyproject.toml', 'plugins/kg/__init__.py',
                             'plugins/kg/research/__init__.py', 'plugins/kg/research/protocol-v1.json',
                             'plugins/kg/research/README.md'):
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, target)
            for relative in ('console/dist/index.html', 'skills/kg-memory/SKILL.md', 'skills/kg-memory-sync/SKILL.md'):
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('synthetic packaging fixture only', encoding='utf-8')
            built = subprocess.run([sys.executable, 'setup.py', 'bdist_wheel'], cwd=stage,
                                   capture_output=True, text=True, timeout=60, shell=False)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            wheels = list((stage / 'dist').glob('*.whl'))
            self.assertEqual(len(wheels), 1)
            with zipfile.ZipFile(wheels[0]) as wheel:
                for name in ('protocol-v1.json', 'README.md'):
                    self.assertEqual(wheel.read('kg/research/' + name), (ROOT / 'plugins/kg/research' / name).read_bytes())


if __name__ == '__main__':
    unittest.main()
