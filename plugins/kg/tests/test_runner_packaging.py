"""Small compatibility contracts for the packaged and old cron entry points."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins"))
from kg import runner
from kg.hermes_runtime import discover_core
from kg.config import KGConfig


class RunnerPackagingTests(unittest.TestCase):
    def test_console_doctor_does_not_prepare_agent_model_runtime(self):
        from kg.console_cli import main
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ,{},clear=False), \
             patch('kg.console_cli._core',return_value='/installed-core'), \
             patch('kg.console_cli.doctor',return_value={'ok':True}), \
             patch('kg.hermes_runtime.prepare_runtime') as prepare:
            self.assertEqual(main(['--home',directory,'doctor']),0)
            prepare.assert_not_called()

    def load_wrapper(self, path=None):
        spec = importlib.util.spec_from_file_location("legacy_runner_contract", path or ROOT / "scripts/kg_sync_runner.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_legacy_private_helpers_delegate_to_canonical_module(self):
        legacy = self.load_wrapper()
        for name in ("_extract", "_extract_validated", "_validate_extraction", "_RunLock", "ExtractionValidationError"):
            self.assertIs(getattr(legacy, name), getattr(runner, name))

    def test_legacy_default_home_does_not_override_explicit_environment_or_config(self):
        legacy = self.load_wrapper()
        with patch.dict(os.environ, {"HERMES_HOME": "", "HERMES_AGENT_HOME": ""}), patch.object(runner, "run", return_value=(0, {})) as run:
            legacy.run(verify_only=True)
            self.assertEqual(run.call_args.kwargs["home"], ROOT)
            legacy.run(home=Path("/explicit"), verify_only=True)
            self.assertEqual(run.call_args.kwargs["home"], Path("/explicit"))
            config = object()
            legacy.run(config=config, verify_only=True)
            self.assertIsNone(run.call_args.kwargs["home"])
        with patch.dict(os.environ, {"HERMES_HOME": "/environment"}), patch.object(runner, "main", return_value=0) as main:
            legacy.main(["--verify-only"])
            self.assertIsNone(main.call_args.kwargs["default_home"])
        with patch.dict(os.environ, {"HERMES_HOME": "", "KG_HOME": "/product-profile"}), \
             patch.object(runner, "run", return_value=(0, {})) as run, \
             patch.object(runner, "main", return_value=0) as main:
            legacy.run(verify_only=True)
            self.assertIsNone(run.call_args.kwargs["home"])
            legacy.main(["--verify-only"])
            self.assertIsNone(main.call_args.kwargs["default_home"])

    def test_bootstrap_uses_explicit_profile_core_without_requiring_checkout_scripts(self):
        original = list(sys.path)
        try:
            with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"HERMES_HOME": "", "HERMES_AGENT_HOME": ""}):
                profile = Path(directory)
                core = profile / "hermes-agent"
                (core / "agent").mkdir(parents=True)
                (core / "agent/memory_provider.py").write_text("")
                self.assertEqual(runner._bootstrap(profile), profile)
                self.assertEqual(sys.path[0], str(core))
        finally:
            sys.path[:] = original

    def test_core_discovery_finds_running_interpreter_install_without_profile_coupling(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            core = base / "application" / "hermes-agent"
            (core / "agent").mkdir(parents=True)
            (core / "agent" / "memory_provider.py").write_text("")
            executable = core / "venv" / "Scripts" / "python.exe"
            with patch.dict(os.environ, {"HERMES_AGENT_HOME": "", "LOCALAPPDATA": ""}), \
                 patch.object(sys, "path", []), patch.object(sys, "executable", str(executable)), \
                 patch("kg.hermes_runtime.Path.home", return_value=base / "user"):
                self.assertEqual(discover_core(base / "separate-profile"), core.resolve())

    def test_core_discovery_accepts_localappdata_install(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            core = base / "local" / "hermes" / "hermes-agent"
            (core / "agent").mkdir(parents=True)
            (core / "agent" / "memory_provider.py").write_text("")
            with patch.dict(os.environ, {"HERMES_AGENT_HOME": "", "LOCALAPPDATA": str(base / "local")}), \
                 patch.object(sys, "path", []), patch.object(sys, "executable", str(base / "python.exe")), \
                 patch("kg.hermes_runtime.Path.home", return_value=base / "user"):
                self.assertEqual(discover_core(base / "separate-profile"), core.resolve())

    def test_copied_profile_cron_runs_from_unrelated_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            profile = base / "profile"
            (profile / "scripts").mkdir(parents=True)
            shutil.copy2(ROOT / "scripts/kg_sync_runner.py", profile / "scripts/kg_sync_runner.py")
            shutil.copytree(ROOT / "plugins/kg", profile / "plugins/kg", ignore=shutil.ignore_patterns("__pycache__", "_vendor", "tests"))
            env = {key: value for key, value in os.environ.items() if key not in ("HERMES_HOME", "HERMES_AGENT_HOME", "PYTHONPATH")}
            completed = subprocess.run([sys.executable, str(profile / "scripts/kg_sync_runner.py"), "--verify-only"], cwd=base, env=env, text=True, capture_output=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["status"], "verification")
            self.assertFalse((profile / "kg-sync").exists(), "Verification must remain read-only")

    def test_mock_extractor_does_not_load_profile_credentials(self):
        snapshot = {"session_id": "session", "snapshot_id": "snapshot", "messages": [{"id": 1, "role": "user", "content": "I prefer jasmine tea."}]}
        response = {"choices": [{"message": {"content": "{}"}}]}
        with patch("kg.hermes_runtime.prepare_runtime", side_effect=AssertionError("Mock LLM must not load credentials")):
            self.assertEqual(runner._extract(snapshot, KGConfig(), llm=lambda **_: response), "{}")

    def test_real_extractor_prepares_selected_profile_before_model_call(self):
        snapshot = {"session_id": "session", "snapshot_id": "snapshot", "messages": [{"id": 1, "role": "user", "content": "I prefer jasmine tea."}]}
        config = KGConfig(home=Path(tempfile.gettempdir()) / "kg-selected-profile")
        calls = []
        auxiliary = types.ModuleType("agent.auxiliary_client")
        def call_llm(**_):
            calls.append("llm")
            return {"choices": [{"message": {"content": "{}"}}]}
        auxiliary.call_llm = call_llm
        with patch.dict(sys.modules, {"agent.auxiliary_client": auxiliary}), patch.object(runner, "_bootstrap") as bootstrap, patch("kg.hermes_runtime.prepare_runtime", side_effect=lambda home: calls.append(home)):
            self.assertEqual(runner._extract(snapshot, config), "{}")
        bootstrap.assert_called_once_with(config.home)
        self.assertEqual(calls, [config.home, "llm"])


if __name__ == "__main__":
    unittest.main()
