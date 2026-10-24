"""A syncable runtime lock must include platform-conditional dependencies too."""
import importlib.metadata
from pathlib import Path
import unittest

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


class ConsoleLockTests(unittest.TestCase):
    def test_installed_console_dependency_metadata_is_closed_on_this_platform(self):
        lock = Path(__file__).resolve().parents[3] / 'requirements-console.lock'
        requirements = [Requirement(line) for line in lock.read_text(encoding='utf-8').splitlines()
                        if line.strip() and not line.startswith('#')]
        active = {canonicalize_name(req.name): req for req in requirements
                  if req.marker is None or req.marker.evaluate({'extra': ''})}
        try:
            importlib.metadata.distribution('fastembed')
        except importlib.metadata.PackageNotFoundError:
            self.skipTest('Full console dependencies are required to validate the runtime lock')
        for name, pin in active.items():
            with self.subTest(package=name):
                installed = importlib.metadata.distribution(name)
                self.assertIn(installed.version, pin.specifier)
                for item in installed.requires or []:
                    dependency = Requirement(item)
                    if dependency.marker is not None and not dependency.marker.evaluate({'extra': ''}):
                        continue
                    key = canonicalize_name(dependency.name)
                    self.assertIn(key, active, f'{name} runtime dependency is absent from the lock: {dependency}')
                    self.assertIn(importlib.metadata.version(key), dependency.specifier)
