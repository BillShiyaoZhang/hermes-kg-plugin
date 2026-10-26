import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kg.retrieval_ann import ResearchVectorIndex, exact_card_ranking, agreement_at_k


def rows():
    return [{"id": "chunk-1", "card_id": "one", "namespace": "owned", "kind": "entity", "embedding": [1, 0]},
            {"id": "chunk-2", "card_id": "two", "namespace": "owned", "kind": "relation", "embedding": [0, 1]},
            {"id": "chunk-3", "card_id": "one", "namespace": "owned", "kind": "entity", "embedding": [0.8, 0.2]},
            {"id": "chunk-4", "card_id": "foreign", "namespace": "other", "kind": "entity", "embedding": [1, 0]}]


class RecordingIndex(ResearchVectorIndex):
    """Record fixed DB commands only; exact candidate budgets need no model."""
    def __init__(self, **kwargs):
        super().__init__(None, dimensions=2, profile_fingerprint='mechanics-only-2d', **kwargs)
        self.calls = []

    def _run(self, query, parameters=None, *, write=False):
        self.calls.append((query, parameters or {}, write))
        return []


class ExactRetrievalTests(unittest.TestCase):
    def test_ground_truth_prefilters_scope_and_aggregates_chunks(self):
        ranking = exact_card_ranking(rows(), [1, 0], namespace="owned")
        self.assertEqual([row["card_id"] for row in ranking], ["one", "two"])
        filtered = exact_card_ranking(rows(), [1, 0], namespace="owned", kind="relation")
        self.assertEqual([row["card_id"] for row in filtered], ["two"])
        self.assertEqual(agreement_at_k(ranking, filtered), 0.5)

    def test_physical_index_isolation_even_for_identical_profiles(self):
        first = ResearchVectorIndex(None, dimensions=2, profile_fingerprint="same")
        second = ResearchVectorIndex(None, dimensions=2, profile_fingerprint="same")
        self.assertNotEqual(first.index_name, second.index_name)
        self.assertRegex(first.label, r"^KGResearchVector_[0-9a-f]{24}$")

    def test_validation_happens_before_database_mutation(self):
        index = ResearchVectorIndex(None, dimensions=2, profile_fingerprint="fixture")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            index.build([rows()[0], rows()[0]])
        self.assertFalse(index._created)
        with self.assertRaises(ValueError):
            index.build([{**rows()[0], "embedding": [1, 0, 0]}])
        self.assertFalse(index._created)

    def test_partition_constructor_rejects_invalid_namespace_and_generates_safe_label(self):
        for value in ('', '  ', 0, False, [], {}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'indexed_namespace'):
                ResearchVectorIndex(None, dimensions=2, profile_fingerprint='fixture', indexed_namespace=value)
        namespace = 'owned`) MATCH (n) DETACH DELETE n //'
        index = RecordingIndex(indexed_namespace=namespace)
        index.build([{**rows()[0], 'namespace': namespace}])
        self.assertRegex(index.partition_label, r'^KGResearchPartition_[0-9a-f]{24}$')
        self.assertNotEqual(index.partition_label, index.label)
        self.assertTrue(all(namespace not in query for query, _, _ in index.calls))
        self.assertEqual(index.profile_fingerprint, 'mechanics-only-2d')

    def test_partition_candidate_budget_uses_indexed_rows_and_keeps_all_base_rows(self):
        index = RecordingIndex(indexed_namespace='owned')
        report = index.build(rows())
        self.assertEqual((index.count, index.indexed_count), (4, 3))
        self.assertEqual((report['chunks'], report['indexed_count'], report['indexed_namespace']), (4, 3, 'owned'))
        self.assertTrue(report['partition_by_namespace'])
        self.assertIn('FOR (c:' + index.partition_label + ')', index.calls[0][0])
        creation = next(call for call in index.calls if call[0].startswith('UNWIND'))
        self.assertIn('CREATE (c:' + index.label + ')', creation[0])
        self.assertEqual(len(creation[1]['rows']), 4)
        self.assertEqual(creation[1]['indexed_namespace'], 'owned')
        index.query([1, 0], namespace='owned', limit=50, oversample=10)
        self.assertEqual(index.calls[-1][1]['candidates'], 3)
        calls = len(index.calls)
        with self.assertRaisesRegex(ValueError, 'must match indexed_namespace'):
            index.query([1, 0], namespace='other')
        self.assertEqual(len(index.calls), calls)

    def test_global_default_keeps_full_budget_and_allows_each_namespace(self):
        index = RecordingIndex()
        report = index.build(rows())
        self.assertIsNone(index.partition_label)
        self.assertEqual(index.index_label, index.label)
        self.assertEqual(index.indexed_count, 4)
        self.assertFalse(report['partition_by_namespace'])
        self.assertIsNone(report['indexed_namespace'])
        self.assertIn('FOR (c:' + index.label + ')', index.calls[0][0])
        self.assertFalse(any('FOREACH' in query for query, _, _ in index.calls))
        for namespace in ('owned', 'other'):
            index.query([1, 0], namespace=namespace)
            self.assertEqual(index.calls[-1][1]['candidates'], 4)

    def test_empty_partition_does_not_query_ann_and_still_checks_namespace(self):
        index = RecordingIndex(indexed_namespace='absent')
        report = index.build(rows())
        self.assertEqual((report['chunks'], report['indexed_count']), (4, 0))
        calls = len(index.calls)
        self.assertEqual(index.query([1, 0], namespace='absent'), [])
        self.assertEqual(len(index.calls), calls)
        with self.assertRaisesRegex(ValueError, 'must match indexed_namespace'):
            index.query([1, 0], namespace='owned')


@unittest.skipUnless(os.environ.get("KG_RESEARCH_TEST_URL"), "Disposable research Neo4j not configured")
class RealResearchAnnTests(unittest.TestCase):
    def test_partition_prevents_foreign_nearest_neighbors_from_consuming_owned_budget(self):
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(os.environ['KG_RESEARCH_TEST_URL'],
                                      auth=('neo4j', os.environ['KG_RESEARCH_TEST_PASSWORD']))
        global_index = ResearchVectorIndex(driver, dimensions=2, profile_fingerprint='mechanics-only-2d')
        partitioned = ResearchVectorIndex(driver, dimensions=2, profile_fingerprint='mechanics-only-2d',
                                         indexed_namespace='owned')
        fixture = [
            {'id': 'own-a', 'card_id': 'own-a', 'namespace': 'owned', 'kind': 'entity', 'embedding': [.4, .9]},
            {'id': 'own-b', 'card_id': 'own-b', 'namespace': 'owned', 'kind': 'entity', 'embedding': [.1, 1]},
            *[{'id': 'foreign-' + str(n), 'card_id': 'foreign-' + str(n), 'namespace': 'other',
               'kind': 'entity', 'embedding': [1, n / 1000]} for n in range(16)],
        ]
        try:
            global_report = global_index.build(fixture)
            partition_report = partitioned.build(fixture)
            self.assertEqual((partition_report['chunks'], partition_report['indexed_count']), (18, 2))
            self.assertTrue(partition_report['partition_by_namespace'])
            self.assertEqual(global_report['profile_fingerprint'], partition_report['profile_fingerprint'])
            self.assertEqual(global_index.query([1, 0], namespace='owned', limit=1, oversample=1), [])
            ranked = partitioned.query([1, 0], namespace='owned', limit=1, oversample=1)
            self.assertEqual([row['card_id'] for row in ranked], ['own-a'])
            for rescore in (False, True):
                ranked = partitioned.query([1, 0], namespace='owned', limit=2, oversample=1, exact_rescore=rescore)
                self.assertEqual([row['card_id'] for row in ranked], ['own-a', 'own-b'])
            with self.assertRaisesRegex(ValueError, 'must match indexed_namespace'):
                partitioned.query([1, 0], namespace='other')
            partitions = partitioned._run(f'MATCH(c:{partitioned.partition_label}) RETURN c.namespace AS namespace')
            self.assertEqual([row['namespace'] for row in partitions], ['owned', 'owned'])
        finally:
            global_cleanup = global_index.cleanup()
            partition_cleanup = partitioned.cleanup()
            driver.close()
        self.assertEqual(global_cleanup, {'complete': True, 'remaining': 0})
        self.assertEqual(partition_cleanup, {'complete': True, 'remaining': 0})

    def test_actual_ann_generation_isolation_filtering_and_cleanup(self):
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(os.environ["KG_RESEARCH_TEST_URL"],
                                      auth=("neo4j", os.environ["KG_RESEARCH_TEST_PASSWORD"]))
        first = ResearchVectorIndex(driver, dimensions=2, profile_fingerprint="mechanics-only-2d")
        second = ResearchVectorIndex(driver, dimensions=3, profile_fingerprint="mechanics-only-3d")
        try:
            first.build(rows())
            second.build([{**rows()[0], "embedding": [1, 0, 0]}])
            ranking = first.query([1, 0], namespace="owned")
            self.assertEqual([row["card_id"] for row in ranking], ["one", "two"])
            self.assertEqual(first.query([1, 0], namespace="owned", kind="relation")[0]["card_id"], "two")
            self.assertEqual(first.query([1, 0], namespace="owned", exact_rescore=True)[0]["card_id"], "one")
            self.assertEqual(second.query([1, 0, 0], namespace="owned")[0]["card_id"], "one")
        finally:
            first_cleanup = first.cleanup()
            second_cleanup = second.cleanup()
            driver.close()
        self.assertTrue(first_cleanup["complete"])
        self.assertTrue(second_cleanup["complete"])


if __name__ == "__main__":
    unittest.main()
