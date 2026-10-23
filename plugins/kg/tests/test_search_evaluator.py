"""Evaluation fixtures cannot overwrite a production namespace."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from evaluate_search import load_fixture, seed_fixture


class EvaluatorSafetyTests(unittest.TestCase):
    def test_random_namespace_rewrites_exact_identifier_queries(self):
        namespace = 'urn:kg-eval-' + 'a' * 32 + ':kg:'
        fixture = load_fixture(namespace)
        self.assertEqual(fixture['namespace'], namespace)
        exact = next(query for query in fixture['queries'] if query['id'] == 'q06')
        self.assertEqual(exact['question'], namespace + 'person-alice')
        self.assertEqual(len(fixture['queries']), 50)

    def test_production_and_mismatched_namespaces_are_rejected_before_graph_access(self):
        for namespace in ('urn:customer:kg:', 'urn:kg-eval-live:kg:'):
            with self.subTest(namespace=namespace), self.assertRaises(ValueError):
                load_fixture(namespace)
        store = SimpleNamespace(config=SimpleNamespace(urn_prefix='urn:customer:kg:'))
        with self.assertRaisesRegex(ValueError, 'synthetic evaluation namespace'):
            seed_fixture(store, load_fixture())

    def test_fixture_entities_are_private_even_if_the_backend_default_is_shared(self):
        fixture = load_fixture('urn:kg-eval-' + 'b' * 32 + ':kg:')
        store = MagicMock()
        store.config.urn_prefix = fixture['namespace']
        seed_fixture(store, fixture)
        entities = store.write_batch.call_args.args[0]
        self.assertTrue(entities)
        self.assertTrue(all(entity['shared'] is False for entity in entities))


if __name__ == '__main__':
    unittest.main()
