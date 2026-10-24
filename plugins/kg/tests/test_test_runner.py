"""Source-only Hermes checkouts use the explicitly configured test interpreter."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from kg.hermes_runtime import discover_python

ROOT = Path(__file__).resolve().parents[3]


class TestRunnerEnvironmentTests(unittest.TestCase):
    def runner(self):
        spec = importlib.util.spec_from_file_location('test_runner_entrypoint', ROOT / 'scripts/run_tests.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def execute(self, core, environment):
        runner = self.runner()
        with patch.dict(os.environ, environment), patch.object(runner, 'find_core', return_value=core), \
             patch.object(sys, 'argv', ['run_tests.py', 'test_hermes_route']), \
             patch.object(runner.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(runner.main(), 0)
            self.assertEqual(os.environ.get('KG_HERMES_PYTHON'), environment.get('KG_HERMES_PYTHON'))
        return run.call_args.kwargs['env']

    def test_bare_checkout_gets_suite_interpreter_without_production_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            core = Path(directory) / 'core'
            (core / 'agent').mkdir(parents=True)
            (core / 'agent/memory_provider.py').write_text('', encoding='utf-8')
            environment = {'KG_HERMES_PYTHON': '', 'HERMES_PYTHON': ''}
            with patch.dict(os.environ, environment), self.assertRaisesRegex(RuntimeError, 'Hermes Python was not found'):
                discover_python(core)
            child = self.execute(core, environment)
            self.assertEqual(child['KG_HERMES_PYTHON'], sys.executable)
            with patch.dict(os.environ, child):
                self.assertEqual(discover_python(core), Path(sys.executable).resolve())

    def test_explicit_current_and_legacy_interpreters_are_preserved(self):
        for environment, expected in (({'KG_HERMES_PYTHON': '/explicit/current/python', 'HERMES_PYTHON': '/legacy/python'}, '/explicit/current/python'),
                                      ({'KG_HERMES_PYTHON': '', 'HERMES_PYTHON': '/explicit/legacy/python'}, '/explicit/legacy/python')):
            with self.subTest(environment=environment):
                child = self.execute(ROOT / 'synthetic-core', environment)
                self.assertEqual(child['KG_HERMES_PYTHON'], expected)
