"""Only synthetic Startup/profile directories and injected process controllers."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from kg import deployment_startup as startup
from kg.deployment import DeploymentError, atomic_json


class DeploymentStartupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='kg-startup-test-')
        self.addCleanup(self.directory.cleanup)
        # macOS tempfile paths can use /var while production entry points
        # canonicalize to /private/var. Keep every fixture identity canonical.
        self.root = Path(self.directory.name).resolve()
        self.home = self.root / '合成 profile'
        self.home.mkdir()
        self.folder = self.root / 'synthetic Startup'
        self.folder.mkdir()
        self.entry = self.folder / startup.ENTRY_NAME
        self.backup = self.home / 'kg-deploy/backups/fixture/startup'
        self.python = self.home / 'kg-runtime' / ('a' * 20) / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        self.python.parent.mkdir(parents=True)
        self.python.write_bytes(b'synthetic managed interpreter')
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def legacy(self, *, home=None, python=None):
        old_python = python or self.root / 'source checkout/.venv-console/Scripts/python.exe'
        home = home or self.home
        command = f'"{old_python}" -B -u "{home / "scripts/kg_console_local.py"}"'
        content = ('Set shell = CreateObject("WScript.Shell")\r\nshell.Run "' + command.replace('"', '""') + '", 0, False\r\n').encode('utf-8')
        self.entry.write_bytes(content)
        atomic_json(self.home / 'kg-console-runtime.json', {'python': str(old_python)})
        return content

    def plan(self, autostart=False):
        return startup.plan_startup(self.home, self.python, autostart, startup_dir=self.folder, platform='win32')

    def manifest(self):
        files = {}
        for relative in ('plugins/kg/deployment.py', 'plugins/kg/deployment_startup.py', 'scripts/kg_service.py', 'plugins/kg/static/index.html'):
            path = self.home / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic deployed ' + relative, encoding='utf-8')
            files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        value = {'format': 1, 'runtime_python': str(self.python), 'lock_sha256': 'a' * 64,
                 'source_sha256': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
                 'files': files, 'model_required': True, 'hermes_core': str(self.home / 'hermes-agent'),
                 'hermes_python': str(self.home / 'hermes-agent/installed-python.exe')}
        atomic_json(self.python.parent.parent / 'kg-runtime.json', {'lock_sha256': value['lock_sha256']})
        atomic_json(self.home / 'kg-deploy/manifest.json', value)
        (self.home / 'kg.json').write_text(json.dumps({'urn_prefix': 'urn:startup-test:kg:', 'console_url': 'http://127.0.0.1:18888'}))
        return value

    def controller(self, *, identity=True, probe=True):
        outer = self
        class Controller:
            def __init__(self, options, python, env):
                self.options, self.python, self.env = options, python, env
                self.calls = []
                outer.service = self
            def identity(self, record):
                self.calls.append('identity')
                if not identity:
                    raise DeploymentError('synthetic stale identity')
                return record
            def start(self, release):
                self.calls.append(('start', release))
                return {'instance': 'synthetic-created-instance'}
            def stop(self):
                self.calls.append('stop')
            def probe(self, manifest, model=True):
                self.calls.append(('probe', model))
                if not probe:
                    raise DeploymentError('synthetic unhealthy service')
                return {'authenticated_api': True, 'database': True, 'semantic_model': model}
        return Controller

    def record(self, manifest, **changes):
        value = {'format': 1, 'profile': str(self.home), 'release': manifest['source_sha256'],
                 'port': 18888, 'python': str(self.python), 'pid': 987, 'control_port': 18889,
                 'nonce': 'synthetic-private-nonce', 'instance': 'synthetic-instance'}
        atomic_json(self.home / 'kg-deploy/console.json', {**value, **changes})

    def test_exact_old_launcher_migrates_without_new_opt_in_and_rolls_back_byte_exact(self):
        before = self.legacy()
        plan = self.plan()
        self.assertEqual(plan['kind'], 'migrate')
        result = startup.apply_startup(plan, self.backup)
        self.assertEqual(result, {'kind': 'migrate', 'enabled': True, 'migrated': True, 'installed': False})
        self.assertEqual((self.backup / startup.ENTRY_NAME).read_bytes(), before)
        command = startup._command(self.entry.read_bytes())
        self.assertIn(str(self.python), command)
        self.assertIn(str(self.home / 'scripts/kg_service.py'), command)
        self.assertNotIn('source checkout', command)
        self.assertNotIn('kg_console_local.py', command)
        startup.rollback_startup(plan)
        self.assertEqual(self.entry.read_bytes(), before)

    def test_fresh_profile_requires_opt_in_and_rollback_removes_only_created_entry(self):
        other = self.folder / 'Hermes_Gateway_other.vbs'
        other.write_bytes(b'other user service')
        self.assertEqual(self.plan()['kind'], 'none')
        plan = self.plan(True)
        self.assertEqual(plan['kind'], 'install')
        startup.apply_startup(plan, self.backup)
        startup.rollback_startup(plan)
        self.assertFalse(self.entry.exists())
        self.assertEqual(other.read_bytes(), b'other user service')

    def test_other_profile_or_unrecognized_script_is_never_overwritten(self):
        self.legacy(home=self.root / 'another profile')
        before = self.entry.read_bytes()
        self.assertEqual(self.plan()['kind'], 'none')
        with self.assertRaisesRegex(DeploymentError, 'unrecognized'):
            self.plan(True)
        self.assertEqual(self.entry.read_bytes(), before)
        self.entry.write_bytes(before + b'\nMsgBox "extra code"')
        self.assertEqual(self.plan()['kind'], 'none')

    def test_expected_profile_with_unverified_legacy_interpreter_fails_closed(self):
        self.legacy()
        atomic_json(self.home / 'kg-console-runtime.json', {'python': '/unrelated/python'})
        with self.assertRaisesRegex(DeploymentError, 'interpreter'):
            self.plan()

    def test_managed_entry_same_runtime_is_idempotent_and_new_runtime_replaces_it(self):
        value = self.manifest()
        self.entry.write_bytes(startup._launcher(self.home, self.python))
        plan = self.plan()
        self.assertEqual(plan['kind'], 'unchanged')
        startup.apply_startup(plan, self.backup)
        new_python = self.home / 'kg-runtime/new-lock/Scripts/python.exe'
        upgraded = startup.plan_startup(self.home, new_python, startup_dir=self.folder, platform='win32')
        self.assertEqual(upgraded['kind'], 'migrate')
        self.assertEqual(json.loads((self.home / 'kg-deploy/manifest.json').read_text(encoding='utf-8'))['source_sha256'], value['source_sha256'])

    def test_concurrent_startup_edit_is_preserved_during_apply_and_rollback(self):
        self.legacy()
        plan = self.plan()
        self.entry.write_bytes(b'concurrent replacement')
        with self.assertRaisesRegex(DeploymentError, 'changed'):
            startup.apply_startup(plan, self.backup)
        self.assertEqual(self.entry.read_bytes(), b'concurrent replacement')
        self.entry.write_bytes(plan['before'])
        startup.apply_startup(plan, self.backup)
        self.entry.write_bytes(b'concurrent edit after install')
        with self.assertRaisesRegex(DeploymentError, 'manual recovery'):
            startup.rollback_startup(plan)
        self.assertEqual(self.entry.read_bytes(), b'concurrent edit after install')

    def test_non_windows_does_not_claim_or_install_login_startup(self):
        self.assertEqual(startup.plan_startup(self.home, self.python, platform='darwin')['kind'], 'none')
        with self.assertRaisesRegex(DeploymentError, 'only on Windows'):
            startup.plan_startup(self.home, self.python, True, platform='linux')

    def test_start_uses_manifest_runtime_profile_environment_and_file_hashes(self):
        value = self.manifest()
        with patch.object(startup, 'require_free_port') as free:
            result = startup.start(self.home, controller_factory=self.controller())
        self.assertTrue(result['started'])
        self.assertEqual(self.service.python, self.python)
        self.assertEqual(self.service.env['KG_STATIC_DIR'], str(self.home / 'plugins/kg/static'))
        self.assertEqual(self.service.env['KG_HOME'], str(self.home))
        self.assertEqual(self.service.env['KG_HERMES_PYTHON'], value['hermes_python'])
        self.assertEqual(self.service.calls, [('start', value['source_sha256']), ('probe', True)])
        free.assert_called_once_with(18888)

    def test_manifest_accepts_both_locked_runtime_leaf_layouts_on_any_host(self):
        for relative in ('Scripts/python.exe', 'bin/python'):
            with self.subTest(relative=relative):
                self.python = self.home / 'kg-runtime' / ('a' * 20) / relative
                self.python.parent.mkdir(parents=True, exist_ok=True)
                self.python.write_bytes(b'synthetic managed interpreter')
                value = self.manifest()
                actual, python = startup._installed_manifest(self.home)
                self.assertEqual(actual, value)
                self.assertEqual(python, self.python)

    def test_same_release_running_only_probes_without_restarting(self):
        value = self.manifest()
        self.record(value)
        with patch.object(startup, 'require_free_port') as free:
            result = startup.start(self.home, controller_factory=self.controller())
        self.assertTrue(result['already_running'])
        self.assertFalse(result['started'])
        self.assertEqual(self.service.calls, ['identity', ('probe', True)])
        free.assert_not_called()

    def test_stale_record_is_removed_only_if_both_ports_are_free_without_pid_signals(self):
        value = self.manifest()
        self.record(value, port=18890)
        with patch.object(startup, 'require_free_port') as free:
            self.assertTrue(startup.start(self.home, controller_factory=self.controller(identity=False))['started'])
        self.assertEqual([call.args[0] for call in free.call_args_list], [18888, 18890, 18888])
        self.assertFalse((self.home / 'kg-deploy/console.json').exists())
        self.assertNotIn('stop', self.service.calls)

    def test_unknown_listener_does_not_remove_record_or_stop_any_process(self):
        value = self.manifest()
        self.record(value)
        original = (self.home / 'kg-deploy/console.json').read_bytes()
        with patch.object(startup, 'require_free_port', side_effect=DeploymentError('unknown listener')), self.assertRaisesRegex(DeploymentError, 'unknown listener'):
            startup.start(self.home, controller_factory=self.controller(identity=False))
        self.assertEqual((self.home / 'kg-deploy/console.json').read_bytes(), original)
        self.assertEqual(self.service.calls, ['identity'])

    def test_live_different_release_is_not_restarted(self):
        value = self.manifest()
        self.record(value, release='b' * 64)
        with self.assertRaisesRegex(DeploymentError, 'another release'):
            startup.start(self.home, controller_factory=self.controller())
        self.assertEqual(self.service.calls, ['identity'])

    def test_modified_code_runtime_or_interrupted_deployment_refuses_launch(self):
        self.manifest()
        (self.home / 'plugins/kg/deployment_startup.py').write_text('modified code')
        with self.assertRaisesRegex(DeploymentError, 'could not be verified'):
            startup.start(self.home, controller_factory=self.controller())
        self.manifest()
        atomic_json(self.python.parent.parent / 'kg-runtime.json', {'lock_sha256': 'wrong lock'})
        with self.assertRaisesRegex(DeploymentError, 'could not be verified'):
            startup.start(self.home, controller_factory=self.controller())
        self.manifest()
        atomic_json(self.home / 'kg-deploy/transaction.json', {'state': 'interrupted'})
        with self.assertRaisesRegex(DeploymentError, 'recovery'):
            startup.start(self.home, controller_factory=self.controller())

    def test_uv_runtime_leaf_symlink_may_only_target_profile_managed_base(self):
        self.manifest()
        base = self.home / 'kg-runtime/python/managed-3.11/python'
        base.parent.mkdir(parents=True)
        base.write_bytes(b'synthetic managed base')
        self.python.unlink()
        try:
            self.python.symlink_to(base)
        except OSError:
            self.skipTest('This platform does not permit test symlinks')
        manifest, python = startup._installed_manifest(self.home)
        self.assertEqual(python, self.python)
        self.assertEqual(manifest['runtime_python'], str(self.python))
        self.python.unlink()
        outside = self.root / 'unmanaged-python'
        outside.write_bytes(b'unmanaged executable')
        self.python.symlink_to(outside)
        with self.assertRaises(DeploymentError):
            startup._installed_manifest(self.home)

    def test_unhealthy_new_process_is_stopped_but_same_release_is_not_restarted(self):
        value = self.manifest()
        with patch.object(startup, 'require_free_port'), self.assertRaisesRegex(DeploymentError, 'unhealthy'):
            startup.start(self.home, controller_factory=self.controller(probe=False))
        self.assertEqual(self.service.calls[-1], 'stop')
        self.record(value)
        with self.assertRaisesRegex(DeploymentError, 'unhealthy'):
            startup.start(self.home, controller_factory=self.controller(probe=False))
        self.assertEqual(self.service.calls, ['identity', ('probe', True)])

    def test_wrapper_help_bootstraps_with_stdlib_only_and_no_source_checkout_path(self):
        wrapper = Path(__file__).resolve().parents[3] / 'scripts/kg_service.py'
        done = subprocess.run([sys.executable, '-S', str(wrapper), '--help'], capture_output=True, text=True, timeout=10)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('start', done.stdout)

