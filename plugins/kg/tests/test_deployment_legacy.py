"""Legacy migration ownership/idle/rollback tests use synthetic files and OS mocks."""
from copy import deepcopy
from contextlib import closing
import ctypes
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from kg import deployment as d
from kg import deployment_legacy as legacy


class LegacyDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / '合成 legacy profile'
        self.home.mkdir()
        self.base = self.home / 'base/python.exe'
        self.python = self.home / 'venv/Scripts/python.exe'
        self.launcher = self.home / 'scripts/kg_console_local.py'
        for path in (self.base, self.python, self.launcher):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic fixture', encoding='utf-8')
        (self.home / 'venv/pyvenv.cfg').write_text('home = ' + str(self.base.parent), encoding='utf-8')
        self.metadata = {'pid': 818181, 'kind': 'kg-console', 'home': str(self.home)}
        self.runtime = {'python': str(self.python), 'hermes_python': str(self.base), 'port': 8765,
                        'model_cache': str(self.home / 'models'), 'scheduler_owner': 'hermes-cron', 'sync_scheduler_enabled': False}
        d.atomic_json(self.home / 'kg-console.pid', self.metadata)
        d.atomic_json(self.home / 'kg-console-runtime.json', self.runtime)
        d.atomic_json(self.home / 'kg.json', {'password': 'synthetic-only', 'urn_prefix': 'urn:legacy:kg:', 'user_person_urn': 'urn:legacy:kg:person-owner'})
        (self.home / 'kg-console.token').write_text('synthetic-private-console-token')
        ledger = self.home / 'kg-sync/console.db'
        ledger.parent.mkdir()
        with closing(sqlite3.connect(ledger)) as db, db:
            db.execute('CREATE TABLE jobs(status TEXT)')
        self.snapshot = {'pid': 818181, 'exe': str(self.base), 'argv': [str(self.base), '-B', '-u', str(self.launcher)],
                         'created': '123456789000', 'listeners': [818181]}
        self.options = d.DeployOptions(self.home, self.home.parent / 'source', core=self.home / 'core', hermes_python=self.base, port=8765)
        self.status = {'connected': True, 'sync': {'available': True, 'queued_count': 0, 'pending_count': 0, 'jobs': []}}
        self.namespace = 'urn:legacy:kg:'
        self.jobs = []
        self.events = []
        outer = self
        class Process:
            def __init__(self, snapshot):
                outer.events.append(('process_handle', snapshot['pid']))
            def __enter__(self):
                return self
            def __exit__(self, *args):
                outer.events.append('close_handle')
            def terminate(self):
                outer.events.append('terminate_verified_handle')
        self.process_factory = Process
        self.envpatch = patch.dict(os.environ, {}, clear=True)
        self.envpatch.start()
        self.addCleanup(self.envpatch.stop)

    def inspect(self, pid, port, run):
        self.events.append(('inspect', pid, port))
        return deepcopy(self.snapshot)

    def request(self, url, *, headers=None):
        self.assertEqual(headers, {'Authorization': 'Bearer synthetic-private-console-token'})
        self.assertNotIn('token=', url)
        if url.endswith('/status'):
            return deepcopy(self.status)
        if url.endswith('/ontology'):
            return {'namespace': self.namespace}
        if url.endswith('/jobs'):
            return {'items': deepcopy(self.jobs)}
        self.fail('unexpected endpoint')

    def migrate(self, **kwargs):
        with patch.object(d, 'require_free_port'):
            return legacy.migrate_legacy(self.options, {'SYNTHETIC_ENV': 'preserve'}, lambda *args, **kw: None,
                                         inspect=kwargs.pop('inspect', self.inspect), process_factory=kwargs.pop('process_factory', self.process_factory),
                                         request=self.request, **kwargs)

    def assert_not_terminated(self):
        self.assertNotIn('terminate_verified_handle', self.events)
        self.assertTrue((self.home / 'kg-console.pid').exists())

    def test_exact_known_legacy_process_migrates_without_printing_identity_or_secrets(self):
        stopped = []
        record = self.migrate(on_stopped=stopped.append)
        self.assertTrue(record['legacy'])
        self.assertEqual(record['argv'], self.snapshot['argv'])
        self.assertEqual(record['restart_env']['KG_SYNC_SCHEDULER_ENABLED'], 'false')
        self.assertEqual(record['restart_env']['KG_MODEL_CACHE'], self.runtime['model_cache'])
        self.assertEqual(record['restart_env']['SYNTHETIC_ENV'], 'preserve')
        self.assertEqual(stopped, [record])
        self.assertFalse((self.home / 'kg-console.pid').exists())
        self.assertEqual(self.events.count('terminate_verified_handle'), 1)
        self.assertEqual(len([event for event in self.events if isinstance(event, tuple) and event[0] == 'inspect']), 2)

    def test_venv_redirector_base_image_is_allowed_from_pyvenv_cfg(self):
        candidates = legacy.python_images(self.python)
        self.assertIn(str(self.base), candidates)
        self.assertIn(str(self.python), candidates)
        legacy.validate_snapshot(self.snapshot, self.home, self.python, 8765)

    def test_metadata_home_kind_pid_and_port_must_match(self):
        for field, value in (('kind', 'unknown-service'), ('home', str(self.home.parent / 'other')), ('pid', True)):
            with self.subTest(field=field):
                changed = {**self.metadata, field: value}
                d.atomic_json(self.home / 'kg-console.pid', changed)
                with self.assertRaises(d.DeploymentError):
                    self.migrate()
                self.assert_not_terminated()
        d.atomic_json(self.home / 'kg-console.pid', self.metadata)
        d.atomic_json(self.home / 'kg-console-runtime.json', {**self.runtime, 'port': 8877})
        with self.assertRaises(d.DeploymentError):
            self.migrate()
        self.assert_not_terminated()

    def test_unknown_launcher_image_arguments_or_listener_is_never_stopped(self):
        original = deepcopy(self.snapshot)
        changes = [ {'exe': str(self.home / 'unrelated-python.exe')}, {'argv': [str(self.base), '-c', 'private-other-command']},
                    {'argv': [str(self.base), '-B', '-u', str(self.home / 'other.py')]}, {'listeners': [999999]}, {'created': ''}]
        for change in changes:
            with self.subTest(change=change):
                self.snapshot = {**original, **change}
                with self.assertRaises(d.DeploymentError):
                    self.migrate()
                self.assert_not_terminated()

    def test_running_pending_or_unknown_idle_state_blocks_migration(self):
        for change in ({'queued_count': None}, {'pending_count': None}, {'available': False}, {'jobs': [{'status': 'running'}]}):
            with self.subTest(change=change):
                self.status['sync'] = {'available': True, 'queued_count': 0, 'pending_count': 0, 'jobs': [], **change}
                with self.assertRaises(d.DeploymentError):
                    self.migrate()
                self.assert_not_terminated()

    def test_durable_queued_sessions_and_pending_snapshots_are_preserved(self):
        self.status['sync'].update(queued_count=18, pending_count=2)
        durable = self.home / 'kg-sync/sync-state-v2.db'
        durable.write_bytes(b'synthetic durable sessions and snapshots; do not extract or clear')
        before = durable.read_bytes()
        record = self.migrate()
        self.assertTrue(record['legacy'])
        self.assertEqual(durable.read_bytes(), before)

    def test_namespace_mismatch_auth_failure_or_api_jobs_block_migration(self):
        self.namespace = 'urn:other:kg:'
        with self.assertRaisesRegex(d.DeploymentError, 'namespace'):
            self.migrate()
        self.assert_not_terminated()
        self.namespace = 'urn:legacy:kg:'
        self.jobs = [{'status': 'queued'}]
        with self.assertRaisesRegex(d.DeploymentError, 'jobs'):
            self.migrate()
        self.assert_not_terminated()

    def test_running_job_hidden_outside_bounded_api_list_is_detected(self):
        with closing(sqlite3.connect(self.home / 'kg-sync/console.db')) as db, db:
            db.execute("INSERT INTO jobs VALUES('running')")
            db.executemany("INSERT INTO jobs VALUES('succeeded')", [()] * 40)
        with self.assertRaisesRegex(d.DeploymentError, 'durable'):
            self.migrate()
        self.assert_not_terminated()

    def test_missing_job_ledger_is_unknown_and_blocks_migration(self):
        (self.home / 'kg-sync/console.db').unlink()
        with self.assertRaisesRegex(d.DeploymentError, 'ledger'):
            self.migrate()
        self.assert_not_terminated()

    def test_pid_creation_generation_change_after_handle_is_detected(self):
        calls = []
        def inspect(pid, port, run):
            calls.append(1)
            return {**deepcopy(self.snapshot), 'created': str(123456789000 + len(calls))}
        with self.assertRaisesRegex(d.DeploymentError, 'changed'):
            self.migrate(inspect=inspect)
        self.assert_not_terminated()

    def test_second_idle_check_prevents_stopping_newly_started_job(self):
        original = self.request
        calls = []
        def request(url, **kwargs):
            if url.endswith('/status'):
                calls.append(1)
                if len(calls) == 2:
                    self.status['sync']['jobs'] = [{'status': 'running'}]
            return original(url, **kwargs)
        with patch.object(self, 'request', side_effect=request), self.assertRaises(d.DeploymentError):
            self.migrate()
        self.assert_not_terminated()

    def test_metadata_change_between_os_reads_is_not_adopted(self):
        original = self.inspect
        calls = []
        def inspect(pid, port, run):
            calls.append(1)
            result = original(pid, port, run)
            if len(calls) == 2:
                d.atomic_json(self.home / 'kg-console.pid', {**self.metadata, 'pid': 999999})
            return result
        with self.assertRaisesRegex(d.DeploymentError, 'changed'):
            self.migrate(inspect=inspect)
        self.assert_not_terminated()

    def test_rollback_restarts_exact_old_argv_environment_and_records_new_pid(self):
        record = self.migrate()
        spawned = []
        class Child:
            pid = 919191
            def poll(self):
                return None
        def popen(argv, **kwargs):
            spawned.append((argv, kwargs))
            return Child()
        def inspect(pid, port, run):
            return {**deepcopy(self.snapshot), 'pid': pid, 'listeners': [pid]}
        (self.home / 'kg-deploy').mkdir(exist_ok=True)
        with patch.object(d, 'require_free_port'):
            result = legacy.restart_legacy(self.options, record, popen=popen, request=self.request, inspect=inspect, run=lambda *args, **kwargs: None)
        self.assertTrue(result['restored'])
        self.assertEqual(spawned[0][0], self.snapshot['argv'])
        self.assertEqual(spawned[0][1]['env']['KG_SYNC_SCHEDULER_ENABLED'], 'false')
        self.assertEqual(json.loads((self.home / 'kg-console.pid').read_text(encoding='utf-8'))['pid'], 919191)

    def test_service_controller_dispatches_busy_unknown_port_only_to_verified_adoption(self):
        controller = d.ServiceController(self.options, self.python, {}, lambda *args, **kwargs: None)
        with patch.object(d, 'require_free_port', side_effect=d.DeploymentError('occupied')), \
             patch.object(legacy, 'migrate_legacy', return_value={'legacy': True}) as adopt:
            self.assertEqual(controller.stop(), {'legacy': True})
            self.assertEqual(adopt.call_args.args[0], self.options)

    def test_service_controller_rollback_dispatches_legacy_restore(self):
        controller = d.ServiceController(self.options, self.python, {}, lambda *args, **kwargs: None)
        with patch.object(legacy, 'restart_legacy', return_value={'restored': True}) as restore:
            self.assertEqual(controller.restore({'legacy': True}), {'restored': True})
            restore.assert_called_once()

    def windows_api(self, created):
        calls = []
        class API:
            pass
        api = API()
        api.OpenProcess = lambda *args: 777
        def times(handle, creation, exit_time, kernel, user):
            creation._obj.dwLowDateTime = created & 0xffffffff
            creation._obj.dwHighDateTime = created >> 32
            return 1
        api.GetProcessTimes = times
        api.TerminateProcess = lambda handle, code: calls.append(('terminate', handle)) or 1
        api.WaitForSingleObject = lambda handle, timeout: calls.append(('wait', handle)) or 0
        api.CloseHandle = lambda handle: calls.append(('close', handle)) or 1
        return api, calls

    def test_windows_reused_pid_creation_time_never_reaches_terminate(self):
        api, calls = self.windows_api(123456789999)
        with patch.object(legacy.os, 'name', 'nt'), patch.object(ctypes, 'WinDLL', return_value=api, create=True), \
             self.assertRaisesRegex(d.DeploymentError, 'reused'):
            with legacy.VerifiedProcess(self.snapshot):
                self.fail('must not adopt reused PID')
        self.assertEqual(calls, [('close', 777)])

    def test_windows_idle_stop_terminates_and_waits_same_verified_handle(self):
        api, calls = self.windows_api(int(self.snapshot['created']))
        with patch.object(legacy.os, 'name', 'nt'), patch.object(ctypes, 'WinDLL', return_value=api, create=True):
            with legacy.VerifiedProcess(self.snapshot) as process:
                process.terminate()
        self.assertEqual(calls, [('terminate', 777), ('wait', 777), ('close', 777)])


if __name__ == '__main__':
    unittest.main()
