"""Bootstrap orchestration only: fake subprocesses, no sync/model downloads."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[3] / 'scripts/run_retrieval_research.py'
SPEC = importlib.util.spec_from_file_location('retrieval_research_bootstrap', SCRIPT)
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


class FakeProcesses:
    """Imitates the two existing CLI contracts; never starts an interpreter."""
    def __init__(self, root):
        self.root, self.calls, self.prepared = root, [], {}
        self.version, self.user_site = [3, 11], False
        self.model_salt = ''
        self.fail_stage = None
        self.fail_eval_after = None
        self.evaluations = 0
        self.needs_download = False
        self.base_prefix = None
        self.invalid_model = False

    @staticmethod
    def option(command, name):
        return command[command.index(name) + 1]

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), deepcopy(kwargs)))
        if kwargs.get('shell') is not False or not isinstance(command, list):
            raise AssertionError('Subprocess must use a list and shell=False')
        if any(not isinstance(argument, str) for argument in command):
            raise AssertionError('Arguments must be strings')
        if 'venv' in command:
            venv = self.root / '.cache/retrieval-research/venv'
            python = venv / ('Scripts/python.exe' if bootstrap.os.name == 'nt' else 'bin/python')
            python.parent.mkdir(parents=True)
            python.write_text('fake interpreter', encoding='utf-8')
            (venv / 'pyvenv.cfg').write_text('include-system-site-packages = false\n', encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, '', '')
        if 'sync' in command:
            return subprocess.CompletedProcess(command, 1 if self.fail_stage == 'sync' else 0,
                                               'PRIVATE-SYNC-OUTPUT', 'PRIVATE-SYNC-DETAIL')
        if '-c' in command and bootstrap._PYTHON_PROBE in command:
            return subprocess.CompletedProcess(command, 0, json.dumps({
                'version': self.version, 'prefix': str(self.root / '.cache/retrieval-research/venv'),
                'base_prefix': self.base_prefix or str(self.root / '.cache/retrieval-research/python/3.11'),
                'user_site': self.user_site}), '')
        if 'prepare-fixture' in command:
            folder = Path(self.option(command, '--output'))
            dataset = {'namespace': 'urn:bootstrap-fixture:kg:', 'entities': [], 'relations': []}
            source = {'authorized': False}
            if '--real-snapshot' in command:
                snapshot = bootstrap._read(Path(self.option(command, '--real-snapshot')))
                source = {'authorized': True, 'snapshot_sha256': bootstrap._digest(snapshot)}
            fixture = {'dataset_sha256': bootstrap._digest(dataset), 'real_source_manifest': source,
                       'queries': [{'id': 'dev-a', 'track': 'facts', 'split': 'dev'},
                                   {'id': 'blind-a', 'track': 'facts', 'split': 'blind'}]}
            write(folder / 'dataset.json', dataset)
            write(folder / 'fixture.json', fixture)
            write(folder / 'labels-template.json', {'entries': []})
            write(folder / 'protocol-template.json', {'human': 'not frozen'})
            (folder / 'human-review.html').write_text('HUMAN REVIEW MUST SURVIVE', encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, '{"ok":true}', '')
        if 'prepare' in command:
            model = self.option(command, '--model')
            if self.fail_stage == 'prepare' or self.needs_download and '--allow-download' not in command:
                return subprocess.CompletedProcess(command, 2, '{"error":"model_not_cached"}', 'PRIVATE-MODEL-DETAIL')
            manifest = {'key': model, 'model': 'synthetic-test-only/' + model,
                        'model_revision': bootstrap._digest(model)[:40],
                        'device': 'cpu', 'precision': 'float32', 'threads': int(self.option(command, '--threads')),
                        'batch_size': 8, 'max_tokens': 512, 'ready': True,
                        'fingerprint_requires_prepare': True,
                        'files_sha256': {'weights': bootstrap._digest([model, self.model_salt])}}
            if self.invalid_model:
                manifest['ready'] = False
            unsigned = {key: value for key, value in manifest.items()
                        if key not in ('ready', 'fingerprint_requires_prepare')}
            self.prepared[model] = {'manifest': manifest, 'fingerprint': bootstrap._digest(unsigned)}
            return subprocess.CompletedProcess(command, 0,
                json.dumps({'status': {'ready': True}, **self.prepared[model]}), '')
        if 'evaluate' in command:
            self.evaluations += 1
            if self.fail_eval_after is not None and self.evaluations > self.fail_eval_after:
                return subprocess.CompletedProcess(command, 1, 'PRIVATE-QUERY-TEXT', 'PRIVATE-EVALUATION-DETAIL')
            model, representation = self.option(command, '--model'), self.option(command, '--representation')
            folder = Path(self.option(command, '--fixture-dir'))
            config = {**bootstrap._evaluation_defaults(self.root, self.option(command, '--scope')), 'representation': representation,
                      'mode': self.option(command, '--mode'), 'model_fingerprint': self.prepared[model]['fingerprint']}
            manifest = {'dataset_sha256': bootstrap._digest(bootstrap._read(folder / 'dataset.json')),
                        'fixture_sha256': bootstrap._digest(bootstrap._read(folder / 'fixture.json')),
                        'tool_code_sha256': bootstrap._runner_tool_hash(self.root),
                        'config': config, 'config_sha256': bootstrap._digest(config),
                        'model': self.prepared[model]['manifest'],
                        'labels_validation': {'labels_sha256': bootstrap._digest(bootstrap._read(
                            Path(self.option(command, '--labels'))))} if '--labels' in command else {},
                        'protocol_sha256': bootstrap._digest(bootstrap._read(
                            Path(self.option(command, '--protocol')))) if '--protocol' in command else None}
            manifest['run_id'] = bootstrap._digest(manifest)
            rows = [{'id': 'dev-a', 'track': 'facts', 'metrics': {'synthetic_test_only': 1}}]
            value = {'manifest': manifest, 'rows': rows, 'results_sha256': bootstrap._digest(rows),
                     'promotion': {'status': 'blocked', 'reasons': ['not_blind_test', '100_real_human_gold_not_locked']}}
            write(Path(self.option(command, '--output')), value)
            return subprocess.CompletedProcess(command, 0, '{"ok":true}', '')
        raise AssertionError('Unexpected fake subprocess')


class RetrievalResearchBootstrapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        for relative in ('requirements-research.lock', 'scripts/research_retrieval.py',
                         'scripts/run_retrieval_research.py', 'plugins/kg/research/__init__.py',
                         'plugins/kg/__init__.py',
                         'plugins/kg/research/runner.py', 'plugins/kg/research/protocol-v1.json',
                         'plugins/kg/schema.py', 'plugins/kg/embedding.py', 'plugins/kg/embedding_profiles.py'):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# synthetic bootstrap test source\n', encoding='utf-8')
        (self.root / 'plugins/kg/retrieval_documents.py').write_text('DOCUMENT_REVISION = "test-v1"\n', encoding='utf-8')
        self.uv = self.root / 'uv-test.exe'
        self.uv.write_text('fake executable, never launched', encoding='utf-8')
        self.processes = FakeProcesses(self.root)
        self.environ = {'PATH': '', 'HERMES_HOME': 'PRIVATE-PROFILE', 'KG_PASSWORD': 'PRIVATE-PASSWORD',
                        'OPENAI_API_KEY': 'PRIVATE-TOKEN', 'HF_TOKEN': 'PRIVATE-HF-TOKEN',
                        'VIRTUAL_ENV': 'HOST-VENV', 'PYTHONPATH': 'HOST-PATH'}

    def args(self, *extra):
        return bootstrap.parser().parse_args(['--output', 'reports/dev', '--uv', str(self.uv), *extra])

    def run_bootstrap(self, *extra, **kwargs):
        return bootstrap.run_bootstrap(self.args(*extra), root=self.root, run=self.processes,
                                       environ=self.environ, platform_name=kwargs.get('platform_name', 'Windows'))

    def result_paths(self):
        return sorted((self.root / 'reports/dev/runs').glob('*.json'))

    def test_dry_run_is_read_only_and_describes_six_development_groups(self):
        result = self.run_bootstrap('--dry-run')
        self.assertTrue(result['dry_run'])
        self.assertFalse(result['execution_success'])
        self.assertFalse(result['research_ready'])
        commands = [step['command'] for step in result['steps']]
        evaluations = [command for command in commands if 'evaluate' in command]
        self.assertEqual(len(evaluations), 6)
        self.assertTrue(all(command[command.index('--split') + 1] == 'dev' for command in evaluations))
        self.assertTrue(all(command[command.index('--track') + 1] == 'facts' for command in evaluations))
        self.assertTrue(all(command[command.index('--scope') + 1] == 'profile' for command in evaluations))
        self.assertFalse((self.root / 'reports').exists())
        self.assertFalse((self.root / '.cache').exists())
        self.assertEqual(self.processes.calls, [])

    def test_runner_tool_hash_includes_embedding_implementation_and_profiles(self):
        before = bootstrap._runner_tool_hash(self.root)
        profile = self.root / 'plugins/kg/embedding_profiles.py'
        original = profile.read_bytes()
        profile.write_text('# updated pinned profile\n', encoding='utf-8')
        self.assertNotEqual(bootstrap._runner_tool_hash(self.root), before)
        profile.write_bytes(original)
        (self.root / 'plugins/kg/embedding.py').write_text('# updated embedding renderer\n', encoding='utf-8')
        self.assertNotEqual(bootstrap._runner_tool_hash(self.root), before)

    def test_one_command_uses_isolated_locked_cpu_runtime_and_never_claims_promotion(self):
        result = self.run_bootstrap()
        self.assertTrue(result['execution_success'])
        self.assertTrue(result['research_ready'])
        self.assertFalse(result['formal_evidence_complete'])
        self.assertEqual(result['blind_queries_executed'], 0)
        self.assertEqual(result['promotion']['status'], 'blocked')
        self.assertEqual(len(result['runs']), 6)
        self.assertEqual(len(self.result_paths()), 6)
        self.assertEqual(result['scope'], 'profile')
        create = next(command for command, _ in self.processes.calls if 'venv' in command)
        self.assertIn('--managed-python', create)
        self.assertEqual(create[create.index('--python') + 1], '3.11')
        sync = next(command for command, _ in self.processes.calls if 'sync' in command)
        self.assertIn('--require-hashes', sync)
        self.assertIn('--strict', sync)
        self.assertEqual(sync[sync.index('--torch-backend') + 1], 'cpu')
        for command, options in self.processes.calls:
            self.assertFalse(options['shell'])
            env = options['env']
            self.assertEqual(env['UV_LINK_MODE'], 'hardlink')
            self.assertEqual(Path(env['UV_CACHE_DIR']).drive, (self.root / '.cache/retrieval-research/venv').drive)
            self.assertNotIn('HERMES_HOME', env)
            self.assertNotIn('KG_PASSWORD', env)
            self.assertNotIn('OPENAI_API_KEY', env)
            self.assertNotIn('HF_TOKEN', env)
            self.assertNotIn('VIRTUAL_ENV', env)
            self.assertNotIn('PYTHONPATH', env)
            for key in ('TEMP', 'TMP', 'HOME', 'USERPROFILE', 'HF_HOME', 'TORCH_HOME', 'UV_CACHE_DIR', 'UV_PYTHON_INSTALL_DIR'):
                self.assertTrue(Path(env[key]).is_relative_to(self.root))
            if 'evaluate' in command:
                self.assertEqual(env['HF_HUB_OFFLINE'], '1')
                self.assertEqual(env['TRANSFORMERS_OFFLINE'], '1')
                self.assertNotIn('--allow-download', command)

    def test_explicit_shared_scope_is_forwarded_and_cannot_resume_as_profile(self):
        result = self.run_bootstrap('--scope', 'shared')
        self.assertEqual(result['scope'], 'shared')
        evaluations = [command for command, _ in self.processes.calls if 'evaluate' in command]
        self.assertTrue(all(command[command.index('--scope') + 1] == 'shared' for command in evaluations))
        self.assertTrue(all(bootstrap._read(path)['manifest']['config']['scope'] == 'shared'
                            for path in self.result_paths()))
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'hashes differ'):
            self.run_bootstrap('--resume')

    def test_model_download_is_explicit_and_evaluation_stays_offline(self):
        self.processes.needs_download = True
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'Model preparation'):
            self.run_bootstrap()
        self.assertEqual(self.result_paths(), [])
        result = self.run_bootstrap('--allow-download')
        self.assertTrue(result['execution_success'])
        preparations = [(command, options) for command, options in self.processes.calls if 'prepare' in command]
        permitted = [(command, options) for command, options in preparations if '--allow-download' in command]
        self.assertEqual(len(permitted), 2)
        self.assertTrue(all(options['env']['HF_HUB_OFFLINE'] == '0' for _, options in permitted))
        self.assertTrue(all(options['env']['HF_HUB_OFFLINE'] == '1'
                            for command, options in self.processes.calls if 'evaluate' in command))

    def test_offline_sync_and_prepare_fail_honestly_without_smoke_or_private_output(self):
        self.processes.fail_stage = 'sync'
        with self.assertRaises(bootstrap.BootstrapError) as caught:
            self.run_bootstrap('--offline')
        self.assertNotIn('PRIVATE', str(caught.exception))
        self.assertIn('No smoke fallback', str(caught.exception))
        self.assertFalse((self.root / 'reports/dev/bootstrap-summary.json').exists())
        sync, options = next((command, options) for command, options in self.processes.calls if 'sync' in command)
        self.assertIn('--offline', sync)
        self.assertEqual(options['env']['UV_PYTHON_DOWNLOADS'], 'never')
        self.assertEqual(options['env']['HF_HUB_OFFLINE'], '1')

    def test_resume_reuses_only_verified_runs_and_preserves_review_files(self):
        self.run_bootstrap()
        fixture = self.root / 'reports/dev/fixture'
        human = fixture / 'human-labels.json'
        human.write_text('DO NOT OVERWRITE MANUAL REVIEW', encoding='utf-8')
        before = {path: path.read_bytes() for path in fixture.iterdir()}
        evaluation_count = self.processes.evaluations
        resumed = self.run_bootstrap('--resume')
        self.assertEqual(resumed['resumed_runs'], 6)
        self.assertEqual(self.processes.evaluations, evaluation_count)
        self.assertEqual(before, {path: path.read_bytes() for path in fixture.iterdir()})
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'strict --resume'):
            self.run_bootstrap()

    def test_failed_partial_matrix_can_resume_without_overwriting_completed_result(self):
        self.processes.fail_eval_after = 1
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'Development fact evaluation'):
            self.run_bootstrap()
        existing = self.result_paths()
        self.assertEqual(len(existing), 1)
        preserved = existing[0].read_bytes()
        self.processes.fail_eval_after = None
        result = self.run_bootstrap('--resume')
        self.assertEqual(result['resumed_runs'], 1)
        self.assertEqual(len(self.result_paths()), 6)
        self.assertEqual(existing[0].read_bytes(), preserved)

    def test_changed_dataset_tool_lock_model_or_config_cannot_resume(self):
        self.run_bootstrap()
        manifest_path = self.root / 'reports/dev/bootstrap-manifest.json'
        original_manifest = manifest_path.read_bytes()
        original_runs = {path: path.read_bytes() for path in self.result_paths()}
        runner = self.root / 'plugins/kg/research/runner.py'
        source = runner.read_bytes()
        runner.write_text('# changed evaluation tool\n', encoding='utf-8')
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'hashes differ'):
            self.run_bootstrap('--resume')
        runner.write_bytes(source)
        self.processes.model_salt = 'different-weights'
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'hashes differ'):
            self.run_bootstrap('--resume')
        self.processes.model_salt = ''
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'hashes differ'):
            self.run_bootstrap('--resume', '--mode', 'hybrid')
        lock = self.root / 'requirements-research.lock'
        original_lock = lock.read_bytes()
        lock.write_text('# changed complete lock\n', encoding='utf-8')
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'hashes differ'):
            self.run_bootstrap('--resume')
        lock.write_bytes(original_lock)
        folder = self.root / 'reports/dev/fixture'
        dataset, fixture = bootstrap._read(folder / 'dataset.json'), bootstrap._read(folder / 'fixture.json')
        dataset['changed'] = True
        fixture['dataset_sha256'] = bootstrap._digest(dataset)
        write(folder / 'dataset.json', dataset)
        write(folder / 'fixture.json', fixture)
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'hashes differ'):
            self.run_bootstrap('--resume')
        self.assertEqual(manifest_path.read_bytes(), original_manifest)
        self.assertEqual(original_runs, {path: path.read_bytes() for path in self.result_paths()})

    def test_self_consistent_wrong_config_or_blind_rows_are_not_accepted_for_resume(self):
        self.run_bootstrap()
        path = self.result_paths()[0]
        original = bootstrap._read(path)
        changed = deepcopy(original)
        changed['manifest']['config']['candidate_budget'] = 500
        changed['manifest']['config_sha256'] = bootstrap._digest(changed['manifest']['config'])
        changed['manifest']['run_id'] = bootstrap._digest({key: value for key, value in changed['manifest'].items() if key != 'run_id'})
        write(path, changed)
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'config hashes'):
            self.run_bootstrap('--resume')
        changed = deepcopy(original)
        changed['rows'][0]['id'] = 'blind-a'
        changed['results_sha256'] = bootstrap._digest(changed['rows'])
        write(path, changed)
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'config hashes'):
            self.run_bootstrap('--resume')

    def test_incomplete_fixture_is_preserved(self):
        folder = self.root / 'reports/dev/fixture'
        folder.mkdir(parents=True)
        review = folder / 'human-review.html'
        review.write_text('HUMAN INPUT', encoding='utf-8')
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'incomplete'):
            self.run_bootstrap()
        self.assertEqual(review.read_text(encoding='utf-8'), 'HUMAN INPUT')
        self.assertFalse(any('prepare-fixture' in command for command, _ in self.processes.calls))

    def test_results_without_bootstrap_manifest_are_never_overwritten(self):
        result = self.root / 'reports/dev/runs/existing-human-result.json'
        write(result, {'human': 'preserve existing result'})
        original = result.read_bytes()
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'no matching bootstrap manifest'):
            self.run_bootstrap('--resume')
        self.assertEqual(result.read_bytes(), original)
        self.assertEqual(self.processes.evaluations, 0)

    def test_explicit_private_snapshot_is_required_for_its_fixture_reuse(self):
        snapshot = self.root / 'private/snapshot.json'
        write(snapshot, {'authorization': 'local_authorized_snapshot', 'dataset': {'entities': ['PRIVATE-SOURCE-TEXT']}})
        result = self.run_bootstrap('--real-snapshot', str(snapshot))
        self.assertNotIn('PRIVATE-SOURCE-TEXT', json.dumps(result))
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'same explicit --real-snapshot'):
            self.run_bootstrap('--resume')
        result = self.run_bootstrap('--resume', '--real-snapshot', str(snapshot))
        self.assertEqual(result['resumed_runs'], 6)

    def test_human_input_hash_changes_block_resume_and_mid_run_completion(self):
        labels = self.root / 'private/labels.json'
        write(labels, {'human': 'first reviewed submission'})
        self.run_bootstrap('--labels', str(labels))
        write(labels, {'human': 'changed reviewed submission'})
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'hashes differ'):
            self.run_bootstrap('--resume', '--labels', str(labels))
        def change_after_evaluate(command, **options):
            result = self.processes(command, **options)
            if 'evaluate' in command:
                write(labels, {'human': 'concurrently edited submission'})
            return result
        args = self.args('--output', 'reports/changed-during-run', '--labels', str(labels))
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'changed during execution'):
            bootstrap.run_bootstrap(args, root=self.root, run=change_after_evaluate,
                                     environ=self.environ, platform_name='Windows')
        self.assertFalse((self.root / 'reports/changed-during-run/bootstrap-summary.json').exists())

    def test_wrong_python_or_user_site_is_rejected_before_dependency_sync(self):
        self.processes.version = [3, 14]
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'Python 3.11'):
            self.run_bootstrap()
        self.assertFalse(any('sync' in command for command, _ in self.processes.calls))
        self.processes.version, self.processes.user_site = [3, 11], True
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'system/user site'):
            self.run_bootstrap()
        self.processes.user_site, self.processes.base_prefix = False, 'C:/some-host-Hermes-runtime'
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'workspace-managed base'):
            self.run_bootstrap()

    def test_zero_exit_unready_model_is_not_a_successful_research_run(self):
        self.processes.invalid_model = True
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'ready pinned CPU profile'):
            self.run_bootstrap()
        self.assertEqual(self.result_paths(), [])
        self.assertFalse((self.root / 'reports/dev/bootstrap-summary.json').exists())

    def test_workspace_boundaries_vendor_and_incompatible_flags_fail_before_subprocess(self):
        for extra in (('--output', '../outside'), ('--output', 'reports'),
                      ('--model-cache', '../outside-cache'), ('--offline', '--allow-download'),
                      ('--threads', '0'), ('--labels-lock', 'missing.json')):
            with self.subTest(extra=extra), self.assertRaises(bootstrap.BootstrapError):
                self.run_bootstrap(*extra)
        (self.root / 'plugins/kg/_vendor').mkdir()
        with self.assertRaisesRegex(bootstrap.BootstrapError, '_vendor'):
            self.run_bootstrap()
        self.assertEqual(self.processes.calls, [])

    def test_existing_runtime_lock_prevents_sync_and_other_platforms_are_explicitly_untested(self):
        lock = self.root / '.cache/retrieval-research/bootstrap.lock'
        lock.parent.mkdir(parents=True)
        lock.write_text('active bootstrap', encoding='utf-8')
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'bootstrap is active'):
            self.run_bootstrap()
        self.assertEqual(self.processes.calls, [])
        lock.unlink()
        result = self.run_bootstrap('--dry-run', platform_name='Linux')
        self.assertEqual(result['platform']['current'], 'Linux')
        self.assertEqual(result['platform']['other_platforms'], 'untested')


if __name__ == '__main__':
    unittest.main()
