"""Search safety and ranking tests; no model downloads or database required."""
import hashlib
import json
import math
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kg.config import KGConfig
from kg.embedding import LocalEmbedder, TextChunk, EmbeddingUnavailable, normalized_vector
from kg.search import (HybridSearch, FULLTEXT_INDEX, VECTOR_INDEX, SOURCE_INDEX,
                       entity_document, entity_fingerprint, reciprocal_rank_fusion)

PREFIX = "urn:hermes:kg:"


def node(slug, kind="Concept", **props):
    return {"properties": {"uri": PREFIX + kind.lower() + "-" + slug,
                           "hermes_name": slug, "hermes_revision": 1,
                           "hermes_updatedAt": "2026-10-22T00:00:00Z", **props},
            "labels": ["KGEntity", kind]}


class SearchHarness(HybridSearch):
    """An intentionally untrusted backend, to exercise the final safety boundary."""
    def __init__(self, nodes, *, embedder=None):
        super().__init__(SimpleNamespace(config=KGConfig()), embedder)
        self.nodes = {n["properties"]["uri"]: n for n in nodes}
        self.channels = {"exact": [], "keyword": [], "vector": [], "graph": [], "fresh": []}
        self.calls = []
        self.index_online = True
        self.before_hydrate = None
        self.indexed = []

    def candidates(self, channel, *nodes):
        self.channels[channel] = [{"uri": n["properties"]["uri"],
                                   "revision": n["properties"].get("hermes_revision", 0),
                                   "updated": str(n["properties"].get("hermes_updatedAt") or "")}
                                  for n in nodes]

    def _run(self, query, params=None, *, write=False):
        params = params or {}
        self.calls.append((query, params, write))
        if query.startswith("SHOW INDEXES"):
            return [{"name": name, "state": "ONLINE", "populationPercent": 100}
                    for name in (FULLTEXT_INDEX, VECTOR_INDEX, SOURCE_INDEX)] if self.index_online else []
        if "RETURN count(n) AS total" in query:
            return [{"pending": 0, "total": len(self.nodes)}]
        if "collect(DISTINCT {fingerprint" in query:
            return [{**n, "indexed": []} for n in self.nodes.values()
                    if 'uris' not in params or n['properties']['uri'] in params['uris']]
        if "MERGE (c:KGSearchChunk" in query:
            self.indexed.extend(params["chunks"])
            return [{"written": len(params["chunks"])}]
        if "UNWIND $candidates AS candidate" in query:
            if self.before_hydrate:
                self.before_hydrate()
            return [{**self.nodes[c["uri"]], "candidate": c}
                    for c in params["candidates"] if c["uri"] in self.nodes]
        if "UNWIND $seeds" in query:
            return self.channels["graph"]
        if "CALL db.index.vector" in query:
            return self.channels["vector"]
        if "any(term IN $terms" in query:
            return self.channels['fresh'] if params.get('fresh_only') else self.channels['keyword']
        if "CALL db.index.fulltext" in query:
            return self.channels["keyword"]
        if "n.uri=$question" in query:
            return self.channels["exact"]
        return []


class TinyTokenizer:
    """Model-like offsets with punctuation/special tokens and uneven token sizes."""
    def encode(self, text, add_special_tokens=True):
        # Every non-space Unicode character is three model tokens. Character
        # counting would therefore badly exceed the model budget in this fixture.
        offsets = [(i, i + 1) for i, c in enumerate(text) if not c.isspace() for _ in range(3)]
        return SimpleNamespace(offsets=offsets, ids=list(range(len(offsets) + (2 if add_special_tokens else 0))))


class FakeEmbedder:
    model_revision = "fixture-model"
    embedding_revision = "fixture-mean-l2"
    def chunk(self, text):
        return [TextChunk(text, 50, 0)]
    def embed_query(self, text):
        return [1.0] + [0.0] * 383
    def embed_passages(self, texts):
        return [self.embed_query(t) for t in texts]
    def status(self):
        return {"ready": True}


class EmbeddingTests(unittest.TestCase):
    def test_module_and_constructor_do_not_import_heavyweight_packages(self):
        with TemporaryDirectory() as root:
            embedder = LocalEmbedder(root)
            self.assertFalse(embedder.status()["ready"])
            self.assertFalse(embedder.status()["cached"])
            with self.assertRaisesRegex(EmbeddingUnavailable, "model_not_cached"):
                embedder.prepare()

    def test_mutable_model_revision_is_rejected(self):
        with self.assertRaises(ValueError):
            LocalEmbedder("/tmp", model_revision="main")

    def test_chunking_counts_real_prefixed_tokens_and_covers_tail(self):
        with TemporaryDirectory() as root:
            model = LocalEmbedder(root)
            model._tokenizer = TinyTokenizer()
            text = "中英memory" * 150
            chunks = model.chunk(text)
            self.assertGreater(len(chunks), 3)
            self.assertTrue(text.endswith(chunks[-1].text))
            self.assertTrue(all(c.token_count <= 512 for c in chunks))
            self.assertTrue(all(len(model._tokenizer.encode("passage: " + c.text).ids) <= 512 for c in chunks))
            self.assertEqual([c.index for c in chunks], list(range(len(chunks))))

    def test_query_and_passage_prefix_are_added_once(self):
        model = LocalEmbedder("/tmp")
        model._tokenizer = TinyTokenizer()
        model._model = Mock()
        model._model.embed.side_effect = lambda texts, **kw: [[2] + [0] * 383 for _ in texts]
        self.assertEqual(model.embed_query("你好")[0], 1.0)
        self.assertEqual(model._model.embed.call_args.args[0], ["query: 你好"])
        model.embed_passages(["Hello"])
        self.assertEqual(model._model.embed.call_args.args[0], ["passage: Hello"])

    def test_vector_validation(self):
        for vector in ([0] * 384, [1] * 383, [float("nan")] * 384, [float("inf")] * 384):
            with self.assertRaises(ValueError):
                normalized_vector(vector)
        result = normalized_vector([2] * 384)
        self.assertAlmostEqual(sum(v * v for v in result), 1.0)

    def test_long_passage_rejected_instead_of_silent_truncation(self):
        model = LocalEmbedder("/tmp")
        model._tokenizer, model._model = TinyTokenizer(), Mock()
        with self.assertRaisesRegex(ValueError, "512 tokens"):
            model.embed_passages(["长" * 1000])


class HybridSearchTests(unittest.TestCase):
    def test_unindexed_current_fact_is_recalled_even_with_an_existing_fulltext_hit(self):
        old, fresh = node('already-indexed'), node('new-revision', hermes_revision=2)
        harness = SearchHarness([old, fresh])
        harness.candidates('keyword', old)
        harness.candidates('fresh', fresh)
        result = harness.search('new evidence', limit=1)
        self.assertEqual(result['kg_results'][0]['uri'], fresh['properties']['uri'])
        fallback = next((q, p) for q, p, _ in harness.calls if 'any(term IN $terms' in q)
        self.assertTrue(fallback[1]['fresh_only'])
        self.assertIn('NOT EXISTS { MATCH (c:KGSearchChunk', fallback[0])
        self.assertIn('c.source_revision=coalesce(n.hermes_revision,0)', fallback[0])
        self.assertIn("key STARTS WITH 'x_'", fallback[0])
        self.assertEqual(result['freshness']['semantic'], 'degraded')
        self.assertFalse(any(write for _, _, write in harness.calls))

    def test_fresh_fallback_remains_subject_to_revision_and_visibility_hydration(self):
        current, old, archived, foreign = (node('current'), node('stale'), node('archived'), node('foreign'))
        archived['labels'].append('KGQuarantined')
        foreign['properties']['uri'] = 'urn:elsewhere:kg:concept-foreign'
        harness = SearchHarness([current, old, archived, foreign])
        harness.candidates('keyword', current)
        harness.candidates('fresh', old, archived, foreign)
        harness.before_hydrate = lambda: old['properties'].update(hermes_revision=2)
        self.assertEqual([r['uri'] for r in harness.search('evidence')['kg_results']], [current['properties']['uri']])

    def test_targeted_refresh_is_bounded_scoped_and_current_revision_guarded(self):
        first, second = node('first', hermes_revision=9), node('second')
        model = FakeEmbedder()
        harness = SearchHarness([first, second], embedder=model)
        report = harness.index_uris([first['properties']['uri'], first['properties']['uri'],
                                     'urn:other:kg:person-secret', second['properties']['uri']], limit=1)
        self.assertEqual((report['requested'], report['omitted'], report['vector_indexed']), (2, 1, 1))
        self.assertEqual(report['state'], 'degraded')
        self.assertIn('targeted_index_limit', report['errors'])
        self.assertEqual([c['source_uri'] for c in harness.indexed], [first['properties']['uri']])
        write = next(q for q, _, _ in harness.calls if 'MERGE (c:KGSearchChunk' in q)
        self.assertIn('coalesce(n.hermes_revision,0)=$revision', write)
        self.assertIn("coalesce(toString(n.hermes_updatedAt),'')=$updated", write)
        cleanup = next((q, p) for q, p, _ in harness.calls if 'DELETE c' in q and 'MERGE' not in q)
        self.assertEqual(cleanup[1]['cleanup_uris'], [first['properties']['uri']])
        self.assertIn('NOT ((n.uri STARTS WITH $prefix', cleanup[0])

    def test_targeted_refresh_ready_model_is_current_but_cold_never_loads(self):
        item = node('committed')
        ready = SearchHarness([item], embedder=FakeEmbedder())
        self.assertEqual(ready.index_uris([item['properties']['uri']])['semantic'], 'current')
        cold = FakeEmbedder()
        cold.status = Mock(return_value={'ready': False, 'cached': True})
        cold.chunk = Mock(side_effect=AssertionError('Must not enter cold embedding runtime'))
        cold.embed_passages = Mock(side_effect=AssertionError('Must not load from a write'))
        offline = SearchHarness([item], embedder=cold)
        report = offline.index_uris([item['properties']['uri']])
        self.assertEqual((report['lexical'], report['semantic']), ('current', 'degraded'))
        self.assertEqual(report['pending'], 1)
        self.assertNotIn('embedding', offline.indexed[0])
        cold.chunk.assert_not_called()
        cold.embed_passages.assert_not_called()

    def test_authored_fixture_has_50_valid_questions_and_expected_entities(self):
        data = json.loads((Path(__file__).parent / "fixtures" / "search_50.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["queries"]), 50)
        self.assertEqual(len({q["id"] for q in data["queries"]}), 50)
        ids = {e["id"] for e in data["entities"]}
        for query in data["queries"]:
            self.assertTrue(set(query["expected"]).issubset(ids))
            self.assertTrue(set(query.get("forbidden", [])).issubset(ids))

    def test_exact_identity_dominates_fused_relevance(self):
        exact, other = node("exact", "Person"), node("other")
        harness = SearchHarness([exact, other], embedder=FakeEmbedder())
        harness.candidates("exact", exact)
        harness.candidates("keyword", other)
        harness.candidates("vector", other)
        self.assertEqual(harness.search("exact")["kg_results"][0]["uri"], exact["properties"]["uri"])

    def test_results_preserve_citations_and_card_version_without_admin_payload(self):
        evidence = [{"message_id": 42, "quote": "I prefer oat milk", "session_id": "s1"}]
        item = node("coffee", "Preference", hermes_evidenceJson=json.dumps(evidence),
                    hermes_userConfirmed=True, hermes_origin="user_edit", hermes_revision=7,
                    hermes_preferenceValue="oat milk", embedding=[99] * 384,
                    hermes_internalAdminJson='{"private_admin_payload":true}')
        harness = SearchHarness([item])
        harness.candidates("exact", item)
        result = harness.search("coffee")["kg_results"][0]
        self.assertEqual(result["evidence"], evidence)
        self.assertEqual(result["version"], result["revision"])
        self.assertEqual(result["version"], 7)
        self.assertEqual(result["origin"], "user_edit")
        self.assertTrue(result["user_confirmed"])
        self.assertEqual(result["properties"]["hermes_preferenceValue"], "oat milk")
        self.assertNotIn("embedding", result["properties"])
        self.assertNotIn("hermes_internalAdminJson", result["properties"])
        self.assertNotIn("hermes_evidenceJson", result["properties"])

    def test_malformed_legacy_evidence_does_not_break_search(self):
        for value in ("not-json", '{"quote":"wrong shape"}', None):
            item = node("legacy", hermes_evidenceJson=value)
            harness = SearchHarness([item])
            harness.candidates("exact", item)
            self.assertEqual(harness.search("legacy")["kg_results"][0]["evidence"], [])

    def test_rrf_deduplicates_chunks_per_channel(self):
        merged = reciprocal_rank_fusion({"keyword": [{"uri": "a"}, {"uri": "a"}], "vector": [{"uri": "b"}, {"uri": "a"}]})
        self.assertEqual(merged[0]["uri"], "a")
        self.assertAlmostEqual(merged[0]["rrf_score"], 1 / 61 + 1 / 62)

    def test_graph_hubs_do_not_outvote_precise_isolated_semantic_hit(self):
        preference, hub = node("coffee", "Preference"), node("hub", "Person")
        harness = SearchHarness([preference, hub], embedder=FakeEmbedder())
        harness.candidates("vector", preference, hub)
        harness.candidates("graph", hub)
        result = harness.search("how should I order my drink?")
        self.assertEqual(result["kg_results"][0]["uri"], preference["properties"]["uri"])

    def test_archived_disabled_foreign_and_legacy_never_return(self):
        nodes = [node("good"), node("archived"), node("disabled", hermes_enabled=False),
                 node("legacy"), node("foreign")]
        nodes[1]["labels"].append("KGQuarantined")
        nodes[3]["labels"].append("KGLegacyUnresolved")
        nodes[4]["properties"]["uri"] = "urn:someone:kg:concept-foreign"
        harness = SearchHarness(nodes, embedder=FakeEmbedder())
        for channel in harness.channels:
            harness.candidates(channel, *nodes)
        result = harness.search("anything")
        self.assertEqual([r["uri"] for r in result["kg_results"]], [nodes[0]["properties"]["uri"]])

    def test_revision_change_between_candidate_and_hydration_is_hidden(self):
        old = node("changed", hermes_description="obsolete claim")
        harness = SearchHarness([old], embedder=FakeEmbedder())
        harness.candidates("vector", old)
        harness.before_hydrate = lambda: old["properties"].update(hermes_revision=2, hermes_description="new claim")
        self.assertEqual(harness.search("old claim")["kg_results"], [])

    def test_updated_timestamp_also_rejects_legacy_revision_zero_writes(self):
        old = node("legacy-update", hermes_revision=0)
        harness = SearchHarness([old])
        harness.candidates("keyword", old)
        harness.before_hydrate = lambda: old["properties"].update(hermes_updatedAt="later")
        self.assertEqual(harness.search("legacy")["kg_results"], [])

    def test_date_type_namespace_filters_apply_to_all_channels(self):
        event = node("meeting", "Event", hermes_eventTime="2026-10-20T12:00:00+08:00")
        outside = node("older", "Event", hermes_eventTime="2026-10-01T12:00:00+08:00")
        unknown = node("unknown", "Event", hermes_timePrecision="unknown")
        person = node("alice", "Person")
        harness = SearchHarness([event, outside, unknown, person], embedder=FakeEmbedder())
        harness.candidates("vector", event, outside, unknown, person)
        harness.candidates("graph", person, outside)
        results = harness.search("meeting", filters={"type": "Event", "from": "2026-10-20", "to": "2026-10-21", "namespace": PREFIX})
        self.assertEqual([r["uri"] for r in results["kg_results"]], [event["properties"]["uri"]])

    def test_day_event_overlaps_interval_and_exclusive_end_is_respected(self):
        event = node("day", "Event", hermes_eventTime="2026-10-20", hermes_timePrecision="day")
        harness = SearchHarness([event])
        harness.candidates("keyword", event)
        self.assertEqual(len(harness.search("day", filters={"from": "2026-10-20T12:00:00+08:00"})["kg_results"]), 1)
        self.assertFalse(harness.search("day", filters={"from": "2026-10-21"})["kg_results"])

    def test_model_failure_degrades_to_keyword_without_writes(self):
        item = node("local")
        model = FakeEmbedder()
        model.embed_query = Mock(side_effect=EmbeddingUnavailable("model_not_cached"))
        harness = SearchHarness([item], embedder=model)
        harness.candidates("keyword", item)
        result = harness.search("local")
        self.assertEqual(result["mode"], "keyword")
        self.assertIn("embedding_unavailable", result["warnings"])
        self.assertEqual(len(result["kg_results"]), 1)
        self.assertFalse(any(write for _, _, write in harness.calls))

    def test_cold_model_never_initializes_from_search(self):
        item = node("local")
        model = FakeEmbedder()
        model.status = Mock(return_value={"ready": False, "cached": True})
        model.embed_query = Mock(side_effect=AssertionError("Search must not initialize a model"))
        harness = SearchHarness([item], embedder=model)
        harness.candidates("keyword", item)
        result = harness.search("local")
        self.assertEqual(result["mode"], "keyword")
        self.assertIn("vector_pending", result["warnings"])
        self.assertFalse(result["index"]["model_ready"])
        self.assertEqual(len(result["kg_results"]), 1)
        model.embed_query.assert_not_called()

    def test_model_download_lock_does_not_block_search_or_status(self):
        # Exercise the real prepare() lock with a paused download, without
        # importing inference packages or accessing the network.
        with TemporaryDirectory() as root:
            model = LocalEmbedder(root)
            item = node("local")
            harness = SearchHarness([item], embedder=model)
            harness.candidates("keyword", item)
            downloading, release, completed = threading.Event(), threading.Event(), threading.Event()
            results, failures = {}, []

            def paused_download(**kwargs):
                downloading.set()
                if not release.wait(5):
                    raise AssertionError("Test did not release the download")
                raise OSError("Simulated offline download")

            def prepare():
                try:
                    model.prepare(allow_download=True)
                except EmbeddingUnavailable:
                    pass
                except BaseException as exc:
                    failures.append(exc)

            def read():
                try:
                    results["status"] = harness.status()
                    results["search"] = harness.search("local")
                except BaseException as exc:
                    failures.append(exc)
                finally:
                    completed.set()

            with patch.dict(sys.modules, {"huggingface_hub": SimpleNamespace(snapshot_download=paused_download)}):
                worker = threading.Thread(target=prepare, daemon=True)
                reader = threading.Thread(target=read, daemon=True)
                worker.start()
                try:
                    self.assertTrue(downloading.wait(2), "prepare did not reach its download")
                    reader.start()
                    self.assertTrue(completed.wait(1), "Search/status waited for model preparation")
                    self.assertFalse(results["status"]["model_ready"])
                    self.assertEqual(results["search"]["mode"], "keyword")
                    self.assertIn("vector_pending", results["search"]["warnings"])
                    self.assertEqual(len(results["search"]["kg_results"]), 1)
                    self.assertFalse(any("CALL db.index.vector" in q for q, _, _ in harness.calls))
                finally:
                    release.set()
                    worker.join(2)
                    if reader.ident is not None:
                        reader.join(2)
                self.assertFalse(worker.is_alive())
                self.assertFalse(reader.is_alive())
                self.assertEqual(failures, [])

    def test_missing_indexes_still_searches_current_source(self):
        item = node("local")
        harness = SearchHarness([item])
        harness.index_online = False
        harness.candidates("keyword", item)
        result = harness.search("local")
        self.assertEqual(result["index"]["state"], "pending")
        self.assertEqual(len(result["kg_results"]), 1)

    def test_index_projection_contains_revision_model_and_source_guards(self):
        item = node("one", hermes_description="a durable fact")
        harness = SearchHarness([item], embedder=FakeEmbedder())
        report = harness.index_pending()
        self.assertEqual(report["vector_indexed"], 1)
        self.assertEqual(harness.indexed[0]["source_revision"], 1)
        self.assertEqual(harness.indexed[0]["model_revision"], "fixture-model")
        write = next(q for q, _, _ in harness.calls if "MERGE (c:KGSearchChunk" in q)
        self.assertIn("coalesce(n.hermes_revision,0)=$revision", write)
        self.assertNotIn("SET n.", write)

    def test_model_unavailable_builds_only_lexical_pending_chunk(self):
        item = node("one")
        harness = SearchHarness([item])
        self.assertEqual(harness.index_pending()["pending"], 1)
        self.assertEqual(harness.indexed[0]["embedding_state"], "pending")
        self.assertNotIn("embedding", harness.indexed[0])

    def test_offline_index_worker_skips_completed_lexical_projection(self):
        item = node("one")
        harness = SearchHarness([item])
        harness._sources = lambda: [{**item, "indexed": [{"fingerprint": entity_fingerprint(item["properties"]), "state": "pending"}]}]
        self.assertEqual(harness.index_pending()["indexed"], 0)
        self.assertFalse(harness.indexed)

    def test_fingerprint_ignores_governance_but_tracks_content(self):
        item = node("one")["properties"]
        fingerprint = entity_fingerprint(item)
        self.assertEqual(entity_fingerprint({**item, "hermes_revision": 5}), fingerprint)
        self.assertNotEqual(entity_fingerprint({**item, "hermes_note": "new fact"}), fingerprint)

    def test_parameterized_queries_and_visibility_guards(self):
        item = node("one")
        harness = SearchHarness([item], embedder=FakeEmbedder())
        harness.candidates("keyword", item)
        harness.search('one" OR *:* // DELETE n')
        for query, params, _ in harness.calls:
            self.assertNotIn('one" OR *:* // DELETE n', query)
            if "MATCH (n" in query:
                self.assertIn("KGQuarantined", query)
        fulltext_params = next(p for q, p, _ in harness.calls if "CALL db.index.fulltext" in q)
        self.assertNotIn(' OR *:*', fulltext_params["query"])

    def test_invalid_filters_and_limits_fail_explicitly(self):
        harness = SearchHarness([])
        for kwargs in ({"limit": True}, {"limit": 101}, {"filters": {"admin": True}},
                       {"filters": []}, {"filters": {"types": 1}},
                       {"filters": {"type": "Invented"}}, {"filters": {"from": "2026-11-01", "to": "2026-01-01"}}):
            with self.assertRaises(ValueError):
                harness.search("hello", **kwargs)

    def test_disabled_profile_has_no_database_or_embedding_calls(self):
        harness = SearchHarness([], embedder=FakeEmbedder())
        harness.config = KGConfig(enabled=False)
        self.assertEqual(harness.search("hello")["mode"], "disabled")
        self.assertFalse(harness.calls)


if __name__ == "__main__":
    unittest.main()
