"""HTTP forwarding and incompatible-query boundaries for explicit graph plans."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kg.config import KGConfig

try:
    from fastapi.testclient import TestClient
    from kg.console_web import create_app
    from test_console_web import FakeMemory, FakeSearch
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, "Console extras unavailable")
class StructuredPlanHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        app = create_app(KGConfig(home=Path(self.temp.name)), token="structured-fixture-token",
                         memory=FakeMemory(), search=FakeSearch(), background=False)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.auth = {"Authorization": "Bearer structured-fixture-token"}

    def test_explicit_plan_uses_structured_executor_without_lexical_or_llm(self):
        expected = {"mode": "structured", "status": "found", "kg_results": [],
                    "evidence": [{"kind": "count", "is_rule": False, "value": 231}],
                    "boundaries": {"complete": True}}
        plan = {"op": "count", "target": "entities"}
        with patch("kg.retrieval_plan.StructuredGraphSearch") as executor, \
             patch.object(FakeSearch, "search", side_effect=AssertionError("No lexical fallback")), \
             patch("kg.query_planner.interpret_query", side_effect=AssertionError("No LLM")):
            executor.return_value.execute.return_value = expected
            response = self.client.post("/api/v1/search", headers=self.auth,
                                        json={"question": "有多少条记录？", "query_plan": plan})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), expected)
            executor.return_value.execute.assert_called_once_with(plan)

    def test_plan_cannot_silently_ignore_independent_filters_or_interpretation(self):
        for extra in ({"filters": {"type": "Person"}}, {"interpret": True}):
            with self.subTest(extra=extra), patch("kg.retrieval_plan.StructuredGraphSearch") as executor:
                response = self.client.post("/api/v1/search", headers=self.auth, json={
                    "question": "有多少人？", "query_plan": {"op": "count", "target": "entities"}, **extra})
                self.assertEqual(response.status_code, 400)
                executor.assert_not_called()

    def test_validation_errors_do_not_echo_untrusted_plan_text(self):
        with patch("kg.retrieval_plan.StructuredGraphSearch") as executor:
            executor.return_value.execute.side_effect = ValueError("PRIVATE-INPUT invalid operation")
            response = self.client.post("/api/v1/search", headers=self.auth, json={
                "question": "查询", "query_plan": {"op": "cypher", "query": "PRIVATE-INPUT"}})
            self.assertEqual(response.status_code, 400)
            self.assertNotIn("PRIVATE-INPUT", response.text)

    def test_explicit_plan_requires_authentication(self):
        with patch("kg.retrieval_plan.StructuredGraphSearch") as executor:
            response = self.client.post("/api/v1/search", json={"question": "数量", "query_plan": {"op": "count", "target": "entities"}})
            self.assertEqual(response.status_code, 401)
            executor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
