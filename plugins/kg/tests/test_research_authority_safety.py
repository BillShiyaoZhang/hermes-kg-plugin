"""Harness isolation guards; these tests never connect or prepare a model."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[3] / 'scripts/research_authority_safety.py'
SPEC = importlib.util.spec_from_file_location('research_authority_safety', PATH)
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


class AuthoritySafetyHarnessTests(unittest.TestCase):
    def test_explicit_isolated_loopback_target(self):
        with patch.dict(os.environ, {'KG_RESEARCH_BOLT_URL': 'bolt://127.0.0.1:28687',
                                     'KG_RESEARCH_PASSWORD': 'synthetic-password'}, clear=True):
            self.assertEqual(harness.target(), ('bolt://127.0.0.1:28687', 'synthetic-password'))

    def test_missing_target_does_not_fall_back_to_profile(self):
        for environment in ({}, {'KG_RESEARCH_BOLT_URL':'bolt://127.0.0.1:28687'},
                            {'KG_RESEARCH_PASSWORD':'synthetic-password'}):
            with self.subTest(keys=list(environment)), patch.dict(os.environ, environment, clear=True):
                with self.assertRaises(ValueError):
                    harness.target()

    def test_other_port_remote_or_url_credentials_are_rejected(self):
        for url in ('bolt://127.0.0.1:7687', 'bolt://127.0.0.1:17687',
                    'bolt://remote.invalid:28687', 'https://127.0.0.1:28687',
                    'bolt://user:password@127.0.0.1:28687',
                    'bolt://127.0.0.1:28687/?x=1', 'bolt://127.0.0.1:28687/other'):
            with self.subTest(url=url), patch.dict(os.environ, {'KG_RESEARCH_BOLT_URL':url,
                         'KG_RESEARCH_PASSWORD':'synthetic-password'}, clear=True):
                with self.assertRaises(ValueError):
                    harness.target()

    def test_cleanup_scope_requires_matching_random_marker_and_both_namespaces(self):
        marker = 'a' * 32
        namespace, foreign = 'urn:researchshadow:'+marker+':kg:', 'urn:researchshadow:'+marker+':foreign:'
        harness.validate_cleanup_scope(marker, namespace, foreign)
        for values in (('a', namespace, foreign), (marker, 'urn:hermes:kg:', foreign),
                       (marker, namespace, 'urn:foreign:'), ('g'*32, namespace, foreign)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                harness.validate_cleanup_scope(*values)


if __name__ == '__main__':
    unittest.main()
