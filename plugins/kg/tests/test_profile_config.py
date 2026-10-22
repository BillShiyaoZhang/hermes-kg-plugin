"""Profile defaults work for both copied plugins and standalone wheel installs."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from kg.config import resolve_home


class ProfileConfigTests(unittest.TestCase):
    def test_copied_plugin_defaults_to_its_own_profile(self):
        with patch.dict(os.environ,{'HERMES_HOME':''}),patch('kg.config.__file__','/tmp/profile/plugins/kg/config.py'):
            self.assertEqual(resolve_home(),Path('/tmp/profile').resolve())

    def test_wheel_defaults_to_user_profile_instead_of_python_library_directory(self):
        with patch.dict(os.environ,{'HERMES_HOME':''}),patch('kg.config.__file__','/tmp/venv/lib/python3.11/site-packages/kg/config.py'):
            self.assertEqual(resolve_home(),(Path.home()/'.hermes').resolve())

    def test_explicit_profile_wins_over_inherited_home(self):
        with patch.dict(os.environ,{'HERMES_HOME':'/tmp/inherited'}):
            self.assertEqual(resolve_home('/tmp/selected'),Path('/tmp/selected').resolve())
