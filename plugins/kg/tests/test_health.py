"""Health status distinguishes read success, empty data and historical writes."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT/'plugins'), str(ROOT/'hermes-agent')]
from kg.config import KGConfig
from kg.provider import KGMemoryProvider


class ReadOnlyStore:
    def __init__(self, config):
        self.config = config
        self.calls = []
        self.fail = False
        self.counts = {"namespace_nodes": 4, "searchable_entities": 2,
                       "quarantined_nodes": 2, "configured_user_matches": 1,
                       "legacy_alias_nodes": 1, "legacy_unresolved_nodes": 1,
                       "content_quarantined_nodes": 0, "confirmed_alias_nodes": 0,
                       "ordinary_content_nodes": 0}
        self.receipts = {"completed_nonempty_batches": 1, "last_committed_at": None}

    def run_read(self, query, params=None):
        self.calls.append((query, params))
        if self.fail:
            raise ConnectionError("fixture unreachable")
        if "RETURN 1" in query:
            return [{"ready": 1}]
        return [self.receipts if "KGSyncReceipt" in query else self.counts]

    def close(self):
        pass

    def write_batch(self, *args, **kwargs):
        raise AssertionError("A health check must never write")


class HealthTests(unittest.TestCase):
    def provider(self, **kwargs):
        p = KGMemoryProvider(KGConfig(), store_factory=ReadOnlyStore)
        p.initialize("fixture-health", **kwargs)
        self.addCleanup(p.shutdown)
        return p

    def test_status_uses_read_only_store_and_exposes_configured_identity(self):
        p = self.provider()
        result = json.loads(p.handle_tool_call("kg_status", {}))
        self.assertTrue(result["ok"] and result["connected"])
        self.assertEqual(result["check"], {"mode":"read_only_status_probe", "writes_performed":0})
        self.assertNotIn("read_only", result)
        self.assertEqual(result["write_capability"], {
            "database_write_permissions":"not_tested_by_this_check",
            "kg_write_allowed_in_current_context":True})
        self.assertEqual(result["legacy_exclusions"]["alias_nodes"], 1)
        self.assertEqual(result["legacy_exclusions"]["unresolved_nodes"], 1)
        self.assertEqual(result["configured_user"], {"uri": p._config.user_person_urn, "present": True})
        self.assertEqual(result["searchable_entities"], 2)
        self.assertEqual(result["past_commits"]["completed_nonempty_batches"], 1)
        self.assertFalse(result["write_probe_performed"])
        for query, params in p._store.calls[1:]:
            self.assertEqual(params["prefix"], p._config.urn_prefix)
            self.assertNotIn(p._config.urn_prefix, query)

    def test_empty_graph_is_connected_without_claiming_past_commits(self):
        p = self.provider()
        p._store.counts = dict.fromkeys(p._store.counts, 0)
        p._store.receipts["completed_nonempty_batches"] = 0
        result = json.loads(p.handle_tool_call("kg_status", {}))
        self.assertTrue(result["connected"])
        self.assertEqual(result["searchable_entities"], 0)
        self.assertFalse(result["configured_user"]["present"])
        self.assertEqual(result["past_commits"]["completed_nonempty_batches"], 0)
        self.assertEqual(result["legacy_exclusions"]["alias_nodes"], 0)
        self.assertEqual(result["legacy_exclusions"]["unresolved_nodes"], 0)

    def test_failed_read_never_reports_connected(self):
        p = self.provider()
        p._store.fail = True
        result = json.loads(p.handle_tool_call("kg_status", {}))
        self.assertFalse(result["ok"])
        self.assertNotIn("connected", result)

    def test_closed_provider_and_invalid_args_do_not_read(self):
        p = self.provider()
        store = p._store
        self.assertFalse(json.loads(p.handle_tool_call("kg_status", {"write": True}))["ok"])
        p.shutdown()
        self.assertFalse(json.loads(p.handle_tool_call("kg_status", {}))["ok"])
        self.assertEqual(store.calls, [])

    def test_secondary_context_can_check_status_without_fact_write_access(self):
        p = self.provider(platform="cron")
        status = json.loads(p.handle_tool_call("kg_status", {}))
        self.assertEqual(status["check"]["writes_performed"], 0)
        self.assertFalse(status["write_capability"]["kg_write_allowed_in_current_context"])
        self.assertEqual(status["write_capability"]["database_write_permissions"], "not_tested_by_this_check")
        self.assertFalse(json.loads(p.handle_tool_call("kg_write", {"entities": []}))["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
