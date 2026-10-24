"""Deployment uses synthetic profiles and injected processes, never a host service."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from kg import deployment as d


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / '合成 profile'
        self.source = self.root / 'source'
        for name in ('requirements-console.lock', 'console/package-lock.json', 'plugins/kg/requirements.lock'):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture==1\n', encoding='utf-8')
        self.options = d.DeployOptions(self.home, self.source, skip_services=False, non_interactive=True)
        self.calls = []
        self.failure = None
        self.login_plan = None
        outer = self
        class Services:
            def __init__(self):
                self.old = None
                self.events = []
            def stop(self):
                self.events.append('stop')
                return self.old
            def start(self, release):
                self.events.append('start')
                if outer.failure == 'start':
                    raise d.DeploymentError('fixture startup failure')
                return {}
            def probe(self, manifest, model=True):
                self.events.append('probe')
                if outer.failure == 'probe':
                    raise d.DeploymentError('fixture health failure')
                return {'database': True, 'static_assets': True}
            def gateway(self):
                self.events.append('gateway')
                if outer.failure == 'gateway':
                    raise d.DeploymentError('fixture gateway failure')
        self.services = Services()
        self.environment = patch.dict(os.environ, {'KG_PASSWORD': 'synthetic-password'}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def build(self, source, run):
        self.calls.append('build')
        if self.failure == 'build':
            raise d.DeploymentError('fixture build failure')

    def runtime(self, home, lock, uv, run):
        self.calls.append('runtime')
        path = d.python_in(home / 'kg-runtime/synthetic-lock')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('synthetic executable')
        return path

    def stage(self, source, target, python, uv, run):
        self.calls.append('stage')
        for relative in d.PAYLOAD:
            path = target / relative
            if relative in d.PAYLOAD[:3]:
                path.mkdir(parents=True)
                path /= 'current.py'
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('new release', encoding='utf-8')
        static = target / 'plugins/kg/static'
        (static / 'assets').mkdir(parents=True)
        (static / 'index.html').write_text('<html><script src="/assets/current.js"></script></html>')
        (static / 'assets/current.js').write_text('fixture application')
        if self.failure == 'stage':
            raise d.DeploymentError('fixture staging failure')

    def fake_run(self, argv, **kwargs):
        self.calls.append([str(value) for value in argv])
        if str(argv[0]) == 'git':
            return 'a' * 40
        if '-m' in argv and argv[argv.index('-m') + 1] == 'kg.console_cli':
            action = argv[argv.index('--home') + 2]
            if action == self.failure:
                raise d.DeploymentError('fixture command failure')
            if action == 'setup':
                path = self.home / 'kg.json'
                if not path.exists():
                    path.write_text(json.dumps({'enabled': True, 'urn_prefix': 'urn:synthetic:kg:', 'password': 'not-set-up-by-cli'}))
                (self.home / 'kg-console.json').write_text('{"ready":true,"capture_started_at":123}')
                (self.home / 'kg-console.token').write_text('synthetic-private-console-token')
            return '{"ok":true}'
        return ''

    def deploy(self):
        with patch.object(d, 'find_uv', return_value='synthetic-uv'), patch.object(d, 'startup_plan', return_value=self.login_plan), contextlib.redirect_stdout(io.StringIO()):
            return d.deploy(self.options, run=self.fake_run, services=self.services, build=self.build, runtime=self.runtime, stage=self.stage)

    def existing(self):
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / 'kg.json').write_bytes(b'{"password":"existing-synthetic-password", "urn_prefix":"urn:fixture:kg:"}\n')
        (self.home / 'config.yaml').write_bytes(b'# unchanged comment\nmemory:\n  provider: original # provider comment\n  other: preserve\nmodel: fixture\n')
        (self.home / 'kg-console.token').write_text('existing-synthetic-token')
        os.environ['KG_PASSWORD'] = 'existing-synthetic-password'
        for path in ('plugins/kg/obsolete.py', 'plugins/kg/static/assets/obsolete.js', 'skills/kg-memory/obsolete.md'):
            target = self.home / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('old release')

    def test_full_new_install_prepares_model_and_unified_profile_payload(self):
        result = self.deploy()
        self.assertTrue(result['complete'])
        self.assertEqual(self.services.events, ['stop', 'start', 'probe', 'gateway'])
        self.assertTrue((self.home / 'plugins/kg/current.py').is_file())
        self.assertEqual(json.loads((self.home / 'kg.json').read_text(encoding='utf-8'))['password'], 'synthetic-password')
        self.assertIn('prepare-model', [str(arg) for call in self.calls if isinstance(call, list) for arg in call])
        self.assertEqual((self.home / 'config.yaml').read_text(encoding='utf-8'), 'memory:\n  provider: kg\n')
        manifest = json.loads((self.home / 'kg-deploy/manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['release_sha'], 'a' * 40)
        self.assertEqual(len(manifest['source_sha256']), 64)
        self.assertNotIn('password', json.dumps(result))

    def test_upgrade_removes_stale_code_and_assets_preserves_profile_state(self):
        self.existing()
        original = (self.home / 'kg.json').read_bytes()
        state = self.home / 'kg-sync/state.db'
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_bytes(b'opaque synthetic state')
        result = self.deploy()
        self.assertEqual((self.home / 'kg.json').read_bytes(), original)
        self.assertEqual(state.read_bytes(), b'opaque synthetic state')
        self.assertFalse((self.home / 'plugins/kg/obsolete.py').exists())
        self.assertFalse((self.home / 'plugins/kg/static/assets/obsolete.js').exists())
        self.assertFalse((self.home / 'skills/kg-memory/obsolete.md').exists())
        self.assertIn('provider: kg # provider comment', (self.home / 'config.yaml').read_text(encoding='utf-8'))
        backup = self.home / 'kg-deploy/backups' / result['backup_id']
        self.assertEqual((backup / 'plugins/kg/obsolete.py').read_text(encoding='utf-8'), 'old release')
        self.assertEqual((backup / 'profile/kg.json').read_bytes(), original)

    def test_repeated_deployment_same_content_has_same_release_hash(self):
        first = self.deploy()
        second = self.deploy()
        self.assertEqual(first['source_sha256'], second['source_sha256'])
        self.assertEqual(first['bundle_sha256'], second['bundle_sha256'])

    def test_dry_run_does_not_create_home_or_launch_build_tools(self):
        self.options.dry_run = True
        with patch.object(d, 'find_uv', side_effect=AssertionError('must not run')):
            result = d.deploy(self.options)
        self.assertTrue(result['dry_run'])
        self.assertFalse(self.home.exists())

    def test_upgrade_failure_rolls_payload_config_and_manifest_back(self):
        for action in ('setup', 'doctor', 'prepare-model', 'probe', 'gateway'):
            with self.subTest(action=action):
                self.existing()
                original = (self.home / 'kg.json').read_bytes()
                config = (self.home / 'config.yaml').read_bytes()
                self.failure = action
                with self.assertRaises(d.DeploymentError):
                    self.deploy()
                self.assertEqual((self.home / 'plugins/kg/obsolete.py').read_text(encoding='utf-8'), 'old release')
                self.assertEqual((self.home / 'kg.json').read_bytes(), original)
                self.assertEqual((self.home / 'config.yaml').read_bytes(), config)
                self.assertFalse((self.home / 'kg-deploy/manifest.json').exists())

    def test_first_model_failure_keeps_disabled_identity_and_capture_boundary(self):
        self.failure = 'prepare-model'
        with self.assertRaises(d.DeploymentError):
            self.deploy()
        state = json.loads((self.home / 'kg.json').read_text(encoding='utf-8'))
        self.assertEqual(state['urn_prefix'], 'urn:synthetic:kg:')
        self.assertFalse(state['enabled'])
        self.assertEqual(json.loads((self.home / 'kg-console.json').read_text(encoding='utf-8'))['capture_started_at'], 123)
        self.assertFalse((self.home / 'config.yaml').exists())

    def test_first_failure_retry_enables_only_our_pending_identity(self):
        self.failure = 'prepare-model'
        with self.assertRaises(d.DeploymentError):
            self.deploy()
        first = json.loads((self.home / 'kg.json').read_text(encoding='utf-8'))
        boundary = json.loads((self.home / 'kg-console.json').read_text(encoding='utf-8'))['capture_started_at']
        self.failure = None
        result = self.deploy()
        current = json.loads((self.home / 'kg.json').read_text(encoding='utf-8'))
        self.assertTrue(result['complete'])
        self.assertTrue(current['enabled'])
        self.assertEqual(current['urn_prefix'], first['urn_prefix'])
        self.assertEqual(json.loads((self.home / 'kg-console.json').read_text(encoding='utf-8'))['capture_started_at'], boundary)
        self.assertFalse((self.home / 'kg-deploy/pending-install.json').exists())

    def test_existing_connection_namespace_environment_conflict_fails_before_build(self):
        self.existing()
        with patch.dict(os.environ, {'KG_URN_PREFIX': 'urn:different:kg:'}), self.assertRaisesRegex(d.DeploymentError, 'urn_prefix'):
            self.deploy()
        self.assertEqual(self.calls, [])

    def test_existing_console_port_is_default_and_conflict_fails(self):
        self.existing()
        value = json.loads((self.home / 'kg.json').read_text(encoding='utf-8'))
        value['console_url'] = 'http://localhost:8877'
        (self.home / 'kg.json').write_text(json.dumps(value))
        self.assertEqual(d.deployment_port(self.home), 8877)
        with self.assertRaisesRegex(d.DeploymentError, 'conflicts'):
            d.deployment_port(self.home, 8765)
        with patch.dict(os.environ, {'KG_CONSOLE_URL': 'https://external.invalid:8877'}), self.assertRaisesRegex(d.DeploymentError, 'loopback'):
            d.deployment_port(self.home)

    def test_new_install_custom_port_is_persisted_for_host_provider(self):
        self.options.port = 8877
        self.deploy()
        self.assertEqual(json.loads((self.home / 'kg.json').read_text(encoding='utf-8'))['console_url'], 'http://127.0.0.1:8877')

    def test_official_host_python_wins_over_legacy_venv(self):
        core = self.root / 'core'
        (core / 'hermes_cli').mkdir(parents=True)
        (core / 'hermes_cli/_launchers.py').write_text('synthetic source')
        managed = self.root / 'managed-python'
        managed.write_text('synthetic interpreter')
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            return json.dumps({'python': str(managed)})
        with patch('kg.hermes_runtime.discover_python', side_effect=AssertionError('must not select stale venv')):
            self.assertEqual(d.discover_host_python(self.home, core, run=run), managed)
        self.assertIn('-S', calls[0])

    def test_staging_failure_cleans_work_before_touching_existing_payload(self):
        self.existing()
        self.failure = 'stage'
        with self.assertRaises(d.DeploymentError):
            self.deploy()
        self.assertTrue((self.home / 'plugins/kg/obsolete.py').exists())
        self.assertEqual(list((self.home / 'kg-deploy/staging').iterdir()), [])
        self.assertEqual(self.services.events, [])

    def test_interrupted_transaction_is_not_overwritten_by_retry(self):
        self.existing()
        journal = self.home / 'kg-deploy/transaction.json'
        journal.parent.mkdir()
        journal.write_text('{"backup_id":"synthetic-interrupted"}')
        with self.assertRaisesRegex(d.DeploymentError, 'interrupted'):
            self.deploy()
        self.assertEqual(json.loads(journal.read_text(encoding='utf-8'))['backup_id'], 'synthetic-interrupted')

    def test_stdlib_only_bootstrap_dry_run_needs_no_site_packages(self):
        root = Path(__file__).resolve().parents[3]
        env = dict(os.environ, PYTHONPATH='')
        bootstrap = 'import sys; sys.path.insert(0,sys.argv.pop(1)); from kg.deployment import main; raise SystemExit(main(source=sys.argv.pop(1)))'
        done = subprocess.run([sys.executable, '-S', '-c', bootstrap, str(root / 'plugins'), str(self.source), '--home', str(self.home), '--core', str(self.source), '--dry-run'],
                              env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(json.loads(done.stdout)['dry_run'])
        self.assertFalse(self.home.exists())

    def test_restore_marker_blocks_before_build_or_runtime(self):
        marker = self.home / 'kg-sync/restore-in-progress.json'
        marker.parent.mkdir(parents=True)
        marker.write_text('{}')
        with self.assertRaisesRegex(d.DeploymentError, 'restore'):
            self.deploy()
        self.assertEqual(self.calls, [])

    def test_skip_flags_are_explicit_partial_and_do_not_touch_services(self):
        self.options.skip_services = True
        self.options.skip_model = True
        result = self.deploy()
        self.assertFalse(result['complete'])
        self.assertTrue(result['services_skipped'])
        self.assertTrue(result['model_skipped'])
        self.assertEqual(self.services.events, [])

    def test_skip_restart_still_checks_running_console(self):
        self.options.skip_restart = True
        result = self.deploy()
        self.assertFalse(result['complete'])
        self.assertEqual(self.services.events, ['stop', 'start', 'probe'])

    def test_connection_password_not_in_subprocess_argv_or_result(self):
        result = self.deploy()
        self.assertNotIn('synthetic-password', json.dumps(self.calls))
        self.assertNotIn('synthetic-password', json.dumps(result))

    def test_existing_connection_does_not_prompt_or_override(self):
        self.existing()
        with patch.object(d.getpass, 'getpass', side_effect=AssertionError('must not prompt')):
            self.assertEqual(d.initial_connection(self.home, non_interactive=True), {})

    def test_missing_new_connection_noninteractive_is_explicit_failure(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(d.DeploymentError, 'credentials'):
            d.initial_connection(self.home, non_interactive=True)

    def test_runtime_settings_preserve_external_scheduler_and_model_cache(self):
        self.home.mkdir()
        value = {'model_cache': 'synthetic-model-cache', 'scheduler_owner': 'hermes-cron', 'sync_scheduler_enabled': False, 'private_extra': 'never-read-by-env'}
        (self.home / 'kg-console-runtime.json').write_text(json.dumps(value))
        settings = d.runtime_settings(self.home)
        env = d.profile_environment(self.home, settings=settings)
        self.assertEqual(env['KG_MODEL_CACHE'], 'synthetic-model-cache')
        self.assertEqual(env['KG_SYNC_SCHEDULER_ENABLED'], 'false')
        self.assertNotIn('private_extra', settings)

    def test_environment_imports_single_profile_copy_and_removes_ambient_path(self):
        with patch.dict(os.environ, {'PYTHONPATH': 'wrong-copy', 'PYTHONHOME': 'wrong-runtime', 'VIRTUAL_ENV': 'wrong-venv'}):
            env = d.profile_environment(self.home, self.root / 'core', self.root / 'host-python')
        self.assertEqual(env['PYTHONPATH'].split(os.pathsep), [str(self.home / 'plugins'), str(self.root / 'core')])
        self.assertNotIn('PYTHONHOME', env)
        self.assertNotIn('VIRTUAL_ENV', env)

    def test_lock_refuses_concurrent_deployment(self):
        with d.DeploymentLock(self.home), self.assertRaisesRegex(d.DeploymentError, 'Another'):
            with d.DeploymentLock(self.home):
                self.fail('must not acquire')

    def test_active_real_runner_lock_blocks_before_console_stop_or_payload_swap(self):
        from kg.runner import _RunLock
        self.existing()
        state = self.home / 'kg-sync/sync-state-v2.db'
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_bytes(b'queued synthetic sessions remain untouched')
        with _RunLock(self.home), self.assertRaisesRegex(d.DeploymentError, 'active KG synchronization'):
            self.deploy()
        self.assertEqual(self.services.events, [])
        self.assertTrue((self.home / 'plugins/kg/obsolete.py').exists())
        self.assertEqual(state.read_bytes(), b'queued synthetic sessions remain untouched')

    def test_runner_lock_stays_held_through_service_probe_and_is_released_after(self):
        from kg.runner import _RunLock, RunnerBusyError
        original = self.services.probe
        def probe(*args, **kwargs):
            with self.assertRaises(RunnerBusyError):
                with _RunLock(self.home):
                    self.fail('cron cannot acquire lock during deployment acceptance')
            return original(*args, **kwargs)
        self.services.probe = probe
        self.deploy()
        with _RunLock(self.home):
            pass

    def test_existing_startup_is_migrated_in_transaction_and_public_result_is_small(self):
        self.existing()
        entry = self.home / 'synthetic-startup.vbs'
        entry.write_bytes(b'SYNTHETIC_PRIVATE_LAUNCHER')
        self.login_plan = {'kind': 'migrate', 'entry': entry, 'before': entry.read_bytes(), 'after': b'new profile service launcher'}
        def apply(plan, backup):
            self.assertTrue((self.home / 'kg-deploy/manifest.json').exists())
            self.assertIn('startup', str(backup))
            entry.write_bytes(plan['after'])
            return {'kind': 'migrate', 'enabled': True, 'migrated': True, 'installed': False, 'private_before': plan['before']}
        with patch.object(d, 'startup_apply', side_effect=apply), patch.object(d, 'startup_rollback') as rollback:
            result = self.deploy()
        rollback.assert_not_called()
        self.assertEqual(entry.read_bytes(), b'new profile service launcher')
        self.assertTrue(result['startup']['migrated'])
        self.assertNotIn('SYNTHETIC_PRIVATE_LAUNCHER', json.dumps(result))

    def test_partial_startup_write_failure_restores_original_startup_and_payload(self):
        self.existing()
        entry = self.home / 'synthetic-startup.vbs'
        entry.write_bytes(b'old verified startup')
        self.login_plan = {'kind': 'migrate', 'entry': entry, 'before': entry.read_bytes(), 'after': b'new startup'}
        def apply(plan, backup):
            entry.write_bytes(plan['after'])
            raise d.DeploymentError('synthetic partial startup write failure')
        def rollback(plan):
            entry.write_bytes(plan['before'])
        with patch.object(d, 'startup_apply', side_effect=apply), patch.object(d, 'startup_rollback', side_effect=rollback) as restore:
            with self.assertRaises(d.DeploymentError):
                self.deploy()
        restore.assert_called_once()
        self.assertEqual(entry.read_bytes(), b'old verified startup')
        self.assertTrue((self.home / 'plugins/kg/obsolete.py').exists())

    def test_service_health_failure_rolls_back_startup_after_successful_hook_write(self):
        self.existing()
        entry = self.home / 'synthetic-startup.vbs'
        entry.write_bytes(b'old verified startup')
        self.login_plan = {'kind': 'migrate', 'entry': entry, 'before': entry.read_bytes(), 'after': b'new startup'}
        self.failure = 'probe'
        def apply(plan, backup):
            entry.write_bytes(plan['after'])
            return {'kind': 'migrate', 'enabled': True, 'migrated': True}
        with patch.object(d, 'startup_apply', side_effect=apply), patch.object(d, 'startup_rollback', side_effect=lambda plan: entry.write_bytes(plan['before'])):
            with self.assertRaises(d.DeploymentError):
                self.deploy()
        self.assertEqual(entry.read_bytes(), b'old verified startup')

    def test_autostart_requires_services_and_new_wrapper_is_owned_payload(self):
        self.options.skip_services = True
        self.options.autostart = True
        with self.assertRaisesRegex(d.DeploymentError, 'service verification'):
            d.startup_plan(self.options, Path('synthetic-runtime'))
        self.assertIn('scripts/kg_service.py', d.PAYLOAD)

    def test_payload_swap_failure_can_restore_previous_payload(self):
        self.existing()
        stage = self.root / 'stage'
        stage.mkdir()
        self.stage(self.source, stage, None, None, None)
        transaction = d.PayloadTransaction(self.home, stage, self.home / 'kg-deploy/backups/synthetic')
        original = os.replace
        def replace(a, b):
            if str(a).endswith('skills/kg-memory') or str(a).endswith('skills\\kg-memory'):
                if Path(a).is_relative_to(stage):
                    raise OSError('synthetic interrupted swap')
            return original(a, b)
        with patch.object(d.os, 'replace', side_effect=replace):
            with self.assertRaises(OSError):
                transaction.install()
        transaction.rollback()
        self.assertTrue((self.home / 'plugins/kg/obsolete.py').exists())

    def test_source_and_profile_must_be_separate(self):
        self.options.home = self.source / 'profile'
        with self.assertRaisesRegex(d.DeploymentError, 'separate'):
            self.deploy()

    def test_release_resource_rejects_state_secrets_and_historical_reports(self):
        for name in ('kg.json', 'kg-console-runtime.json', 'private/facts.json', 'reports/diagnostic.md', 'backup.zip', '.env', 'auth.token', 'report-2026-01-01.md'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('synthetic private fixture')
                with self.assertRaises(d.DeploymentError):
                    d.validate_release_tree(Path(directory))

    def test_asset_validation_requires_actual_js_and_existing_references(self):
        folder = self.root / 'dist'
        folder.mkdir()
        for html in ('<html/>', '<script src="/assets/missing.js"></script>', '<script src="https://external.invalid/app.js"></script>', '<script src="/../outside.js"></script>'):
            with self.subTest(html=html):
                (folder / 'index.html').write_text(html)
                with self.assertRaises(d.DeploymentError):
                    d.asset_files(folder)

    def test_runtime_is_locked_python311_and_no_kg_package_is_installed(self):
        base = self.home / 'kg-runtime/python/base-python'
        base.parent.mkdir(parents=True, exist_ok=True)
        base.write_text('synthetic interpreter')
        commands = []
        def run(argv, **kwargs):
            commands.append([str(value) for value in argv])
            if 'find' in argv:
                return str(base)
            if 'venv' in argv:
                target = d.python_in(Path(argv[-1]))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('synthetic runtime')
            return ''
        runtime = d.prepare_runtime(self.home, self.source / 'requirements-console.lock', 'synthetic-uv', run)
        self.assertTrue(runtime.is_relative_to(self.home / 'kg-runtime'))
        self.assertIn(['synthetic-uv', 'python', 'find', '--no-project', '--managed-python', '3.11'], commands)
        self.assertTrue(any(call[1:3] == ['pip', 'sync'] for call in commands))
        self.assertFalse(any('hermes-memory-console' in arg for call in commands for arg in call))
        count = len(commands)
        d.prepare_runtime(self.home, self.source / 'requirements-console.lock', 'synthetic-uv', run)
        self.assertEqual(len(commands), count + 2, 'already locked runtime verifies imports and dependency closure')
        self.assertTrue(any(call[1:3] == ['pip', 'check'] for call in commands))

    def test_runtime_failed_dependency_install_has_no_ready_marker(self):
        base = self.home / 'kg-runtime/python/base-python'
        base.parent.mkdir(parents=True, exist_ok=True)
        base.write_text('synthetic interpreter')
        def run(argv, **kwargs):
            if 'find' in argv:
                return str(base)
            if 'venv' in argv:
                Path(argv[-1]).mkdir(parents=True)
            if 'sync' in argv:
                raise d.DeploymentError('synthetic download failure')
            return ''
        with self.assertRaises(d.DeploymentError):
            d.prepare_runtime(self.home, self.source / 'requirements-console.lock', 'synthetic-uv', run)
        self.assertFalse(list((self.home / 'kg-runtime').glob('*/kg-runtime.json')))

    def test_process_identity_requires_profile_nonce_pid_release(self):
        controller = d.ServiceController(self.options)
        record = {'profile': str(self.home), 'control_port': 43210, 'nonce': 'synthetic-control', 'instance': 'i', 'pid': 42, 'release': 'r'}
        with patch.object(d, 'read_json_response', return_value={'profile': str(self.home), 'instance': 'i', 'pid': 999, 'release': 'r'}):
            with self.assertRaisesRegex(d.DeploymentError, 'ownership'):
                controller.identity(record)
        with patch.object(d, 'read_json_response', return_value={key: record[key] for key in ('profile', 'instance', 'pid', 'release')}) as request:
            controller.identity(record)
            self.assertEqual(request.call_args.kwargs['headers']['X-KG-Control'], 'synthetic-control')

    def test_unknown_port_process_is_never_stopped(self):
        controller = d.ServiceController(self.options)
        with patch.object(d, 'require_free_port', side_effect=d.DeploymentError('occupied')), patch.object(d, 'read_json_response') as request:
            with self.assertRaises(d.DeploymentError):
                controller.stop()
            request.assert_not_called()

    def test_stop_keeps_record_until_authenticated_process_finishes_work(self):
        self.options.port = 8765
        controller = d.ServiceController(self.options)
        record = {'profile': str(self.home), 'port': 8765, 'control_port': 44321, 'pid': 12345,
                  'nonce': 'synthetic', 'instance': 'synthetic', 'release': 'synthetic'}
        d.atomic_json(controller.record_path, record)
        events = []
        class BusyProcess:
            def __init__(self, pid):
                events.append(('handle', pid))
            def __enter__(self):
                return self
            def __exit__(self, *args):
                events.append('close')
            def wait(self):
                events.append('wait')
                raise d.DeploymentError('still running work')
        with patch.object(controller, 'identity', return_value=record), patch.object(d, 'ProcessExit', BusyProcess), \
             patch.object(d, 'read_json_response', side_effect=lambda *args, **kwargs: events.append('stop_request')), \
             patch.object(d, 'require_free_port') as port:
            with self.assertRaisesRegex(d.DeploymentError, 'running work'):
                controller.stop()
            port.assert_not_called()
        self.assertTrue(controller.record_path.exists())
        self.assertEqual(events, [('handle', 12345), 'stop_request', 'wait', 'close'])

    def test_probe_waits_for_model_warmup_then_verifies_auth_and_exact_assets(self):
        self.options.port = 8765
        self.stage(self.source, self.home, None, None, None)
        (self.home / 'kg-console.token').write_text('synthetic-private-console-token')
        controller = d.ServiceController(self.options)
        calls = []
        statuses = iter([{'connected': True, 'search': {'model_ready': False}}, {'connected': True, 'search': {'model_ready': True}}])
        def response(url, *, headers=None, **kwargs):
            if not headers:
                raise urllib.error.HTTPError(url, 401, 'fixture unauthorized', {}, None)
            calls.append(url)
            return next(statuses)
        static = self.home / 'plugins/kg/static'
        class Opener:
            def open(self, url, **kwargs):
                return io.BytesIO((static / url.split(':8765/', 1)[1]).read_bytes())
        with patch.object(d, 'read_json_response', side_effect=response), \
             patch.object(d.urllib.request, 'build_opener', return_value=Opener()), patch.object(d.time, 'sleep'):
            result = controller.probe({}, model=True)
        self.assertTrue(result['semantic_model'])
        self.assertEqual(len(calls), 2)

    def test_windows_start_uses_base_shim_instead_of_redirector_pid(self):
        self.options.port = 8765
        base = self.home / 'kg-runtime/python/synthetic-base/python.exe'
        python = self.home / 'kg-runtime/synthetic-lock/Scripts/python.exe'
        for path in (base, python):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic image')
        (python.parent.parent / 'pyvenv.cfg').write_text('home = ' + str(base.parent), encoding='utf-8')
        controller = d.ServiceController(self.options, python, {})
        calls = []
        class Child:
            pid = 112233
            def poll(self):
                return None
        def popen(argv, **kwargs):
            calls.append(argv)
            record = json.loads(controller.record_path.read_text(encoding='utf-8'))
            # A redirector would produce a different server PID. The base shim
            # must be used, retaining the caller's strong exact-PID check.
            record.update(pid=Child.pid if argv[0] == str(base) else Child.pid + 1, control_port=43210)
            d.atomic_json(controller.record_path, record)
            return Child()
        with patch.object(d.os, 'name', 'nt'), patch.object(d, 'require_free_port'), \
             patch.object(d.subprocess, 'Popen', side_effect=popen), patch.object(controller, 'identity', return_value={}):
            running = controller.start('synthetic-release')
        self.assertEqual(running['pid'], Child.pid)
        self.assertEqual(calls[0][0], str(base))
        self.assertIn('-I', calls[0])
        self.assertIn('-S', calls[0])

    def test_unexpected_process_pid_is_rejected_and_owned_child_handle_cleaned(self):
        self.options.port = 8765
        controller = d.ServiceController(self.options, Path('synthetic-runtime'), {})
        class Child:
            pid = 112233
            terminated = False
            waited = False
            def poll(self):
                return None
            def terminate(self):
                self.terminated = True
            def wait(self, **kwargs):
                self.waited = True
        child = Child()
        def popen(argv, **kwargs):
            record = json.loads(controller.record_path.read_text(encoding='utf-8'))
            record.update(pid=child.pid + 1, control_port=43210)
            d.atomic_json(controller.record_path, record)
            return child
        with patch.object(d, 'require_free_port'), patch.object(d, 'console_command', return_value=['synthetic-base']), \
             patch.object(d.subprocess, 'Popen', side_effect=popen), patch.object(d.time, 'sleep'), \
             self.assertRaisesRegex(d.DeploymentError, 'timed out'):
            controller.start('synthetic-release')
        self.assertTrue(child.terminated)
        self.assertTrue(child.waited)
        self.assertFalse(controller.record_path.exists())

    def test_real_stdlib_base_shim_has_same_pid_and_target_runtime_paths(self):
        runtime = self.home / 'probe-runtime'
        site = runtime / ('Lib/site-packages' if os.name == 'nt' else 'lib/python3.11/site-packages')
        site.mkdir(parents=True)
        (self.home / 'plugins').mkdir(exist_ok=True)
        base = Path(getattr(sys, '_base_executable', sys.executable)).resolve()
        program = 'import json,os,sys; print(json.dumps({"pid":os.getpid(),"prefix":sys.prefix,"paths":sys.path}))'
        argv = d.runtime_bootstrap_command(base, runtime, self.home, program)
        child = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        output, errors = child.communicate(timeout=20)
        self.assertEqual(child.returncode, 0, errors)
        report = json.loads(output)
        self.assertEqual(report['pid'], child.pid)
        self.assertEqual(report['prefix'], str(runtime.resolve()))
        self.assertIn(str(site), report['paths'])
        self.assertIn(str(self.home / 'plugins'), report['paths'])


class ProviderYamlTests(unittest.TestCase):
    def test_preserves_other_keys_comments_and_crlf(self):
        before = '# header\r\nmemory:\r\n  provider: local # keep\r\n  query_limit: 8\r\nmodel: fixture\r\n'
        self.assertEqual(d.patch_provider(before), before.replace('provider: local', 'provider: kg'))

    def test_empty_mapping_and_missing_provider(self):
        self.assertEqual(d.patch_provider('memory: {} # keep\nmodel: fixture\n'), 'memory: # keep\n  provider: kg\nmodel: fixture\n')
        self.assertEqual(d.patch_provider('memory:\n  other: keep\n'), 'memory:\n  provider: kg\n  other: keep\n')

    def test_duplicate_flow_anchor_multidoc_fail_without_rewrite(self):
        for value in ('memory: {provider: local}\n', 'memory: &anchor\n', 'memory: {}\nmemory: {}\n', 'memory:\n  provider: a\n  provider: b\n', '---\nmemory: {}\n'):
            with self.subTest(value=value), self.assertRaises(d.DeploymentError):
                d.patch_provider(value)

    def test_idempotent_provider_patch(self):
        value = '# synthetic\nmemory:\n  provider: kg\n'
        self.assertEqual(d.patch_provider(d.patch_provider(value)), value)


if __name__ == '__main__':
    unittest.main()
