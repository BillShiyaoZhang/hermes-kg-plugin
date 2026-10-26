import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kg.embedding import normalized_vector
from kg.retrieval_snapshot import export_snapshot, write_private_json


class SnapshotStore:
    config = SimpleNamespace(urn_prefix="urn:test:kg:")

    def __init__(self, versions=(1, 1)):
        self.calls = []
        self.versions = iter(versions)

    def run_read(self, query, params):
        self.calls.append((query, params))
        if "snapshot_nodes" in query:
            return [{"properties": {"uri": "urn:test:kg:concept-one", "hermes_revision": next(self.versions)},
                     "labels": ["KGEntity", "Concept"]}]
        return []


class RetrievalSnapshotTests(unittest.TestCase):
    def test_explicit_export_requires_stable_reads_and_never_writes(self):
        store = SnapshotStore((1, 2, 2))
        snapshot = export_snapshot(store)
        self.assertEqual(snapshot["entities"][0]["properties"]["hermes_revision"], 2)
        self.assertFalse(snapshot["provenance"]["human_reviewed"])
        self.assertTrue(snapshot["provenance"]["complete"])
        self.assertEqual(len(store.calls), 6)
        for query, params in store.calls:
            self.assertNotIn("shared=true", query)
            self.assertIn("STARTS WITH $prefix", query)
            self.assertEqual(params["prefix"], "urn:test:kg:")

    def test_continuously_changing_export_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "changed"):
            export_snapshot(SnapshotStore((1, 2, 3)))

    def test_over_budget_export_is_not_silently_truncated(self):
        store = SnapshotStore()
        original = store.run_read

        def many(query, params):
            rows = original(query, params)
            return rows * 2 if "snapshot_nodes" in query else rows

        store.run_read = many
        with self.assertRaisesRegex(ValueError, "budget exceeded"):
            export_snapshot(store, max_entities=1)

    def test_private_json_written_atomically_without_changing_content(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "nested" / "snapshot.json"
            write_private_json(target, {"text": "中文", "count": 3})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"text": "中文", "count": 3})
            self.assertFalse(target.with_name(target.name + ".tmp").exists())

    def test_vectors_require_the_selected_profiles_dimensions(self):
        self.assertEqual(normalized_vector([0, 2], dimensions=2), [0, 1])
        with self.assertRaises(ValueError):
            normalized_vector([1, 0], dimensions=384)
        with self.assertRaises(ValueError):
            normalized_vector([1], dimensions=True)
        with self.assertRaises(ValueError):
            normalized_vector([1e308] * 4, dimensions=4)
        self.assertEqual(normalized_vector([1e-308], dimensions=1), [1])


if __name__ == "__main__":
    unittest.main()
