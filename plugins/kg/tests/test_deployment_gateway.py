"""A successful CLI exit and a retained PID do not prove gateway readiness."""
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from kg.deployment import DeploymentError
from kg.deployment_gateway import current_host, restart_gateway, verified


def status(**changes):
    value = {'ok': True, 'running': True, 'provider_configured': True,
             'provider_registered': True, 'database_connected': True,
             'gateway_state': 'running', 'updated_at': datetime.fromtimestamp(100, timezone.utc).isoformat(),
             'pid': 55, 'provider_sha256': 'synthetic-code-hash'}
    return {**value, **changes}


class GatewayDeploymentTests(unittest.TestCase):
    def test_readiness_requires_os_liveness_current_state_and_real_provider(self):
        self.assertTrue(verified(status(), 100))
        for change in ({'running': False}, {'provider_configured': False}, {'provider_registered': False},
                       {'database_connected': False}, {'gateway_state': 'starting'}, {'updated_at': 'invalid'},
                       {'updated_at': datetime.fromtimestamp(10, timezone.utc).isoformat()}):
            with self.subTest(change=change):
                self.assertFalse(verified(status(**change), 100))

    def test_restart_targets_only_explicit_home_and_waits_for_fresh_state(self):
        clock = [100]
        calls = []
        replies = [status(running=False), status()]
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            return json.dumps(replies.pop(0)) if '-c' in argv else ''
        with patch('kg.deployment_gateway.launcher', return_value=['official-hermes']):
            result = restart_gateway(Path('/synthetic/profile'), Path('/synthetic/core'), 'host-python',
                {'HERMES_HOME': '/unrelated', 'HERMES_PROFILE': 'unrelated'}, run,
                now=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
        self.assertTrue(result['running'])
        self.assertEqual(calls[1][0], ['official-hermes', 'gateway', 'restart'])
        self.assertEqual(calls[1][1]['env']['HERMES_HOME'], str(Path('/synthetic/profile').resolve()))
        self.assertNotIn('HERMES_PROFILE', calls[1][1]['env'])
        self.assertEqual(len(calls), 3)

    def test_new_heartbeat_from_old_process_does_not_prove_restart(self):
        clock = [100]
        def run(argv, **kwargs):
            return json.dumps(status()) if '-c' in argv else ''
        with patch('kg.deployment_gateway.launcher', return_value=['official-hermes']), self.assertRaisesRegex(DeploymentError, 'live serving gateway'):
            restart_gateway('/synthetic/profile', '/synthetic/core', 'host-python', {}, run, timeout=2,
                now=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    def test_new_process_identity_confirms_completed_restart(self):
        replies = [status(pid=55), status(pid=56)]
        def run(argv, **kwargs):
            return json.dumps(replies.pop(0)) if '-c' in argv else ''
        with patch('kg.deployment_gateway.launcher', return_value=['official-hermes']):
            result = restart_gateway('/synthetic/profile', '/synthetic/core', 'host-python', {}, run, now=lambda: 100)
        self.assertEqual(result['pid'], 56)

    def test_cli_exit_zero_with_only_stale_state_cannot_report_success(self):
        clock = [100]
        def run(argv, **kwargs):
            return json.dumps(status(running=False)) if '-c' in argv else ''
        with patch('kg.deployment_gateway.launcher', return_value=['official-hermes']), self.assertRaisesRegex(DeploymentError, 'live serving gateway'):
            restart_gateway('/synthetic/profile', '/synthetic/core', 'host-python', {}, run, timeout=2,
                now=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    def test_host_diagnostics_do_not_expose_upstream_error_contents(self):
        for response in ('private-token-and-transcript', json.dumps({'ok': False, 'error': 'private-token'})):
            with self.subTest(response=response), self.assertRaises(DeploymentError) as failure:
                current_host('/synthetic/profile', '/synthetic/core', 'host-python', {}, lambda *a, **k: response)
            self.assertNotIn('private-token', str(failure.exception))
