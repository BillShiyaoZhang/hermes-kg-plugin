"""Public distributions must reject local state and historical private reports."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from setuptools import Distribution


ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('kg_release_packaging',ROOT/'setup.py')
packaging=importlib.util.module_from_spec(spec)
with patch('setuptools.setup'):
    spec.loader.exec_module(packaging)


class ReleaseResourceTests(unittest.TestCase):
    def test_current_agent_skills_are_public_resources(self):
        for name in ('kg-memory','kg-memory-sync'):
            packaging.validate_distributable_tree(ROOT/'skills'/name)

    def test_state_credentials_and_private_exports_are_rejected(self):
        for relative in ('kg.json','kg-console-runtime.json','.env','sync-state-v2.db',
                         'private/graph-export.json','reports/migration.md',
                         'cache/model.onnx','profile-backup.zip'):
            with self.subTest(relative=relative),tempfile.TemporaryDirectory() as directory:
                source=Path(directory);path=source/relative
                path.parent.mkdir(parents=True,exist_ok=True);path.write_text('synthetic private fixture',encoding='utf-8')
                with self.assertRaisesRegex(RuntimeError,'Refusing to package'):
                    packaging.validate_distributable_tree(source)

    def test_dated_audit_stops_wheel_resource_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);assets=root/'console/dist';assets.mkdir(parents=True)
            (assets/'index.html').write_text('<html>fixture</html>',encoding='utf-8')
            for name in ('kg-memory','kg-memory-sync'):
                skill=root/'skills'/name;skill.mkdir(parents=True)
                (skill/'SKILL.md').write_text('# Public fixture skill',encoding='utf-8')
            report=root/'skills/kg-memory-sync/references/kg-audit-2026-01-01.md'
            report.parent.mkdir();report.write_text('synthetic private audit',encoding='utf-8')
            command=packaging.BuildPy(Distribution());command.build_lib=str(root/'build')
            with patch.object(packaging,'ROOT',root),patch('setuptools.command.build_py.build_py.run') as build:
                with self.assertRaisesRegex(RuntimeError,'kg-audit-2026-01-01'):
                    command.run()
                build.assert_not_called()
            self.assertFalse((root/'build').exists(),'A rejected report must never reach staged wheel contents')


if __name__=='__main__': unittest.main()
