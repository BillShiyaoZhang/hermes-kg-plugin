"""Install only distributable resources and report dependency failures accurately."""
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('kg_profile_installer',ROOT/'scripts/install.py')
installer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def test_nested_cache_vendor_and_local_state_are_never_copied(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source';target=Path(directory)/'target'
            for name in ('provider.py','static/assets/workbench.js','requirements.lock',
                         '__pycache__/nested/hidden.py','_vendor/copied_secret.py',
                         'tests/nested/test_fixture.py','.env','.env.local','state.db',
                         'state.db-wal','nested/.env/token.txt','nested/cache/private.txt'):
                path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture')
            installer._copy_tree(source,target,False)
            actual={str(path.relative_to(target)).replace('\\','/') for path in target.rglob('*') if path.is_file()}
            self.assertEqual(actual,{'provider.py','static/assets/workbench.js','requirements.lock'})

    def test_install_uses_only_shipped_sync_helpers_and_preserves_profile_config(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source';home=Path(directory)/'profile'
            home.mkdir();(home/'config.yaml').write_text('memory: {}')
            (home/'kg.json').write_text('{"password":"profile-secret"}')
            for relative,_ in installer.TREES:
                path=source/relative/'fixture.txt';path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture')
            for relative,_ in installer.FILES:
                path=source/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture')
            # This untracked runtime file is intentionally present in the source profile layout.
            (source/'kg-sync/private-memory.json').write_text('private fixture')
            (source/'kg-sync/sync-state-v2.db').write_text('private fixture')
            with patch.object(installer,'REPO_ROOT',source),patch('sys.argv',['install.py','--home',str(home),'--skip-deps']),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(installer.main(),0)
            self.assertEqual((home/'kg.json').read_text(),'{"password":"profile-secret"}')
            self.assertTrue((home/'kg-sync/check_kg.py').exists())
            self.assertFalse((home/'kg-sync/private-memory.json').exists())
            self.assertFalse((home/'kg-sync/sync-state-v2.db').exists())

    def test_dependency_failure_has_nonzero_exit_status(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);(home/'config.yaml').write_text('memory: {}')
            with patch('sys.argv',['install.py','--home',str(home)]), \
                 patch.object(installer,'_copy_tree',return_value=[]), \
                 patch.object(installer,'_copy_file',return_value=[]), \
                 patch.object(installer,'install_dependencies',return_value=7), \
                 contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(installer.main(),7)


if __name__=='__main__': unittest.main()
