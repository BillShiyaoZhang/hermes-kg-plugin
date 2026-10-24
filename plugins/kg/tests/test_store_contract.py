"""Behavior checks for shared storage/query/schema contracts; no live DB needed."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins"))
sys.path.insert(0, str(ROOT / "skills" / "kg-memory" / "scripts"))
from kg.config import KGConfig, load_config
from kg.schema import canonical_urn, normalize_rel, normalize_properties, prepare_entity
from kg.store import KGStore
from kg.queries import parse_natural_query, query_entities, query_events, query_natural


class Result(list):
    def single(self):
        return self[0] if self else None

    def consume(self):
        return None


class FakeTransaction:
    def __init__(self, state):
        self.state = state

    def run(self, query, params=None):
        assert isinstance(query, str), "Transaction.run must receive strings"
        params = params or {}
        if 'MATCH (s:KGOntologySchema' in query:
            return Result()
        if "MERGE (w:KGWorkspace" in query:
            return Result()
        if "AS candidate_uri" in query:
            return Result([{"candidate_uri": uri, "candidate_name": node["properties"].get("foaf_name"),
                "candidate_aliases": node["properties"].get("hermes_aliasNames", [])}
                for uri, node in self.state["nodes"].items() if "Person" in node["labels"]
                and not set(node["labels"]) & {"KGQuarantined", "KGLegacyAlias", "KGLegacyUnresolved"}])
        if "MATCH (a {uri:$uri})-[r]->(b)" in query:
            return Result()
        if "RETURN properties(r) AS properties" in query:
            return Result()
        if "SET n.hermes_revision=coalesce(n.hermes_revision,0)+1" in query:
            node = self.state["nodes"][params["uri"]]
            node["properties"]["hermes_revision"] = node["properties"].get("hermes_revision", 0) + 1
            return Result()
        if "MERGE (r:KGSyncReceipt" in query:
            receipt = self.state["receipts"].setdefault(params["snapshot_id"], {**params, "complete": False})
            return Result([{"receipt": copy.deepcopy(receipt)}])
        if query.lstrip().startswith("MATCH (n {uri:$uri})") and "RETURN labels(n) AS labels" in query:
            node = self.state["nodes"].get(params["uri"])
            return Result([{"labels": node["labels"], "properties": copy.deepcopy(node["properties"]), "alias_confirmed": node["properties"].get("hermes_aliasConfirmed"),
                            **{key: node["properties"].get(prop) for key, prop in {
                                "event_time": "hermes_eventTime", "event_end_time": "hermes_eventEndTime",
                                "time_precision": "hermes_timePrecision", "event_status": "hermes_eventStatus"}.items()}}]) if node else Result()
        if "MERGE (n {uri:$uri})" in query:
            label = query.split("SET n:KGEntity:")[1].split(",")[0]
            node = self.state["nodes"].setdefault(params["uri"], {"labels": [], "properties": {}})
            node["labels"] = list(set(node["labels"]) | {"KGEntity", label})
            node["properties"].update(params["props"])
            node["properties"].update({"uri": params["uri"], "hermes_revision": params["revision"],
                "hermes_origin": params["origin"], "hermes_userConfirmed": params["user_confirmed"],
                "hermes_evidenceJson": params["evidence_json"]})
            if "REMOVE n:KGLegacyUnresolved" in query:
                node["labels"].remove("KGLegacyUnresolved")
                node["properties"].pop("hermes_repairReason", None)
            if "REMOVE n.hermes_eventTime, n.hermes_eventEndTime" in query:
                node["properties"].pop("hermes_eventTime", None)
                node["properties"].pop("hermes_eventEndTime", None)
            if "SET n.hermes_timePrecision=CASE" in query and node["properties"].get("hermes_timePrecision") == "unknown":
                node["properties"]["hermes_timePrecision"] = "datetime"
            return Result()
        if "MERGE (v:KGMemoryRevision" in query:
            self.state.setdefault("revisions", {}).setdefault(params["revision_id"], copy.deepcopy(params))
            return Result()
        if "RETURN labels(a) AS source_labels" in query:
            a, b = self.state["nodes"].get(params["from"]), self.state["nodes"].get(params["to"])
            return Result([{"source_labels": a["labels"], "target_labels": b["labels"],
                            "source_alias_confirmed": a["properties"].get("hermes_aliasConfirmed"),
                            "target_alias_confirmed": b["properties"].get("hermes_aliasConfirmed")}]) if a and b else Result()
        if "MERGE (a)-[r:" in query:
            self.state["edges"].append((params["from"], params["to"]))
            return Result()
        if "r.complete=true" in query:
            self.state["receipts"][params["snapshot_id"]].update({**params, "complete": True})
            return Result()
        raise AssertionError("Unexpected test query")


class FakeDriver:
    def __init__(self):
        self.state = {"nodes": {}, "edges": [], "receipts": {}}
        self.closed = False

    def session(self, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def execute_write(self, callback, *args):
        pending = copy.deepcopy(self.state)
        result = callback(FakeTransaction(pending), *args)
        self.state = pending
        return result

    def execute_read(self, callback, *args):
        return callback(FakeTransaction(copy.deepcopy(self.state)), *args)

    def close(self):
        self.closed = True


class CaptureStore:
    config = KGConfig()

    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []

    def run_read(self, query, params=None):
        self.calls.append((query, params))
        return self.rows


class SchemaTests(unittest.TestCase):
    def test_urn_and_relationship_aliases(self):
        self.assertEqual(canonical_urn("urn:hermes:kg:person:alice"), "urn:hermes:kg:person-alice")
        self.assertEqual(normalize_rel(":PARTICIPATED_IN"), "HERMES_participant")
        self.assertEqual(normalize_rel("HERMES_PARTICIPANT"), "HERMES_participant")
        self.assertEqual(normalize_rel("AT_LOCATION"), "HERMES_locatedAt")
        with self.assertRaises(ValueError):
            canonical_urn("urn:hermes:kg:person-alice", "event")
        with self.assertRaises(ValueError):
            normalize_rel("R] SET n.secret=1")

    def test_typed_properties_and_unsafe_keys(self):
        props = normalize_properties({"shared": "false", "latitude": "12.3", "time": "2026-09-13T12:00:00+08:00"})
        self.assertIs(props["shared"], False)
        self.assertEqual(props["geo_lat"], 12.3)
        self.assertIsInstance(props["hermes_eventTime"], datetime)
        for invalid in ({"uri": "overwrite"}, {"bad key": "x"}, {"shared": 1}, {"time": "2026-02-30"}, {"latitude": "NaN"}):
            with self.assertRaises(ValueError):
                normalize_properties(invalid)

    def test_concept_and_required_fields(self):
        entity, _ = prepare_entity({"type": "concept", "id": "fixture", "name": "Fixture", "props": {"description": "Useful"}}, KGConfig())
        self.assertEqual(entity["label"], "Concept")
        with self.assertRaises(ValueError):
            prepare_entity({"type": "event", "id": "fixture", "name": "Missing date"}, KGConfig())

    def test_profiles_and_strict_booleans(self):
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            Path(one, "kg.json").write_text(json.dumps({"urn_prefix": "urn:one:kg:", "enabled": False}), encoding="utf-8")
            Path(two, "kg.json").write_text(json.dumps({"urn_prefix": "urn:two:kg:"}), encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(load_config(home=one).user_person_urn, "urn:one:kg:person-self")
                self.assertIs(load_config(home=one).enabled, False)
                self.assertEqual(load_config(home=two).urn_prefix, "urn:two:kg:")
        for invalid in (1, "yes", "0"):
            with self.assertRaises(ValueError):
                KGConfig(enabled=invalid)


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.driver = FakeDriver()
        self.store = KGStore(KGConfig(), self.driver)
        self.entities = [{"type": "person", "id": "fixture-person", "name": "O'Brien"}, {"type": "event", "id": "fixture-event", "name": "Meeting", "time": "2026-09-13T12:00:00+08:00"}]
        self.relations = [{"from": "urn:hermes:kg:person:fixture-person", "to": "urn:hermes:kg:event:fixture-event", "type": "PARTICIPATED_IN"}]
        self.checkpoint = {"snapshot_id": "snapshot-fixture", "extraction_hash": "fixture-hash", "last_message_id": 3}

    def test_batch_receipt_and_idempotent_replay(self):
        receipt = self.store.write_batch(self.entities, self.relations, "session-fixture", self.checkpoint)
        state = copy.deepcopy(self.driver.state)
        repeated = self.store.write_batch(self.entities, self.relations, "session-fixture", self.checkpoint)
        self.assertEqual(receipt["entities_written"], 2)
        self.assertEqual(receipt["relations_written"], 1)
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(self.driver.state, state)

    def test_missing_endpoint_rolls_back_entities_and_receipt(self):
        wrong = [{**self.relations[0], "to": "urn:hermes:kg:event-missing"}]
        with self.assertRaises(ValueError):
            self.store.write_batch(self.entities, wrong, "session-fixture", self.checkpoint)
        self.assertEqual(self.driver.state, {"nodes": {}, "edges": [], "receipts": {}})

    def test_snapshot_payload_conflict_is_rejected(self):
        self.store.write_batch(self.entities, self.relations, "session-fixture", self.checkpoint)
        changed = copy.deepcopy(self.entities)
        changed[0]["name"] = "Another name"
        with self.assertRaises(ValueError):
            self.store.write_batch(changed, self.relations, "session-fixture", self.checkpoint)

    def test_private_shared_type_remains_boolean(self):
        receipt = self.store.write_batch([{**self.entities[0], "shared": False}], [])
        node = self.driver.state["nodes"][receipt["entity_uris"][0]]
        self.assertIs(node["properties"]["shared"], False)

    def test_owned_driver_is_closed(self):
        store = KGStore()
        store._driver = self.driver
        store.close()
        self.assertTrue(self.driver.closed)

    def test_valid_fact_recovers_unresolved_shell_without_losing_repair_audit(self):
        uri = "urn:hermes:kg:person-fixture-person"
        self.driver.state["nodes"][uri] = {
            "labels": ["KGLegacyUnresolved"],
            "properties": {"uri": uri, "hermes_repairReason": "missing content", "hermes_repairId": "repair-fixture"},
        }
        self.store.write_batch([self.entities[0]], [])
        recovered = self.driver.state["nodes"][uri]
        self.assertIn("Person", recovered["labels"])
        self.assertIn("KGEntity", recovered["labels"])
        self.assertNotIn("KGLegacyUnresolved", recovered["labels"])
        self.assertNotIn("hermes_repairReason", recovered["properties"])
        self.assertEqual(recovered["properties"]["hermes_repairId"], "repair-fixture")
        self.assertEqual(recovered["properties"]["foaf_name"], "O'Brien")

    def test_alias_is_not_silently_reclassified(self):
        uri = "urn:hermes:kg:person-fixture-person"
        self.driver.state["nodes"][uri] = {"labels": ["KGLegacyAlias"], "properties": {"hermes_canonicalUri": "urn:hermes:kg:person-other"}}
        before = copy.deepcopy(self.driver.state)
        with self.assertRaises(ValueError):
            self.store.write_batch([self.entities[0]], [])
        self.assertEqual(self.driver.state, before)


class QueryTests(unittest.TestCase):
    def test_full_uris_bypass_natural_intent_and_time_keywords(self):
        for kind in ("person", "event", "preference", "decision", "location", "concept"):
            uri = f"urn:hermes:kg:{kind}-today-memory-travel-Morgan"
            with self.subTest(kind=kind):
                store = CaptureStore()
                result = query_natural(uri, store=store)
                self.assertEqual(result["parsed_params"]["keywords"], [uri])
                self.assertIsNone(result["parsed_params"]["time_range"]["from"])
                self.assertIsNone(result["parsed_params"]["person_urn"])
                self.assertEqual(len(store.calls), 1)
                query, params = store.calls[0]
                self.assertIn("n.uri=$uri", query)
                self.assertNotIn("CONTAINS $keyword", query)
                self.assertNotIn(uri, query)
                self.assertEqual(params["uri"], uri)

    def test_exact_uri_lookup_preserves_identity_and_visibility(self):
        for uri in ("urn:hermes:kg:person:legacy-owner", "urn:external:kg:person-Morgan"):
            with self.subTest(uri=uri):
                store = CaptureStore()
                query_entities(uri, store=store)
                query, params = store.calls[0]
                self.assertEqual(params["uri"], uri)
                self.assertIn("NOT n:KGLegacyAlias", query)
                self.assertIn("NOT n:KGLegacyUnresolved", query)
                self.assertIn("n.uri STARTS WITH $prefix OR n.shared=true", query)
                self.assertIn("label IN $entity_labels", query)
                self.assertIn("alias.hermes_aliasConfirmed=true", query)
                self.assertIn("MATCH (alias:KGQuarantined {uri:$uri})", query)
                self.assertIn("alias.hermes_canonicalUri=n.uri", query)

    def test_unstructured_search_phrases_remain_literal_keywords(self):
        for phrase in ("Hermes knowledge graph connection test", "owner URN person"):
            with self.subTest(phrase=phrase):
                store = CaptureStore()
                result = query_natural(phrase, store=store)
                self.assertEqual(result["parsed_params"]["keywords"], [phrase])
                query, params = store.calls[0]
                self.assertEqual(params["keyword"], phrase.lower())
                self.assertIn("CONTAINS $keyword", query)
                self.assertNotIn("uri", params)

    def test_name_and_month_boundaries(self):
        parsed = parse_natural_query("秦一丹是谁", config=KGConfig())
        self.assertEqual(parsed["keywords"], ["秦一丹"])
        parsed = parse_natural_query("上个月我做了什么", config=KGConfig(), now=datetime(2026, 9, 13))
        self.assertTrue(parsed["time_range"]["end_exclusive"])
        self.assertTrue(parsed["time_range"]["from"].startswith("2026-08-01"))
        self.assertTrue(parsed["time_range"]["to"].startswith("2026-09-01"))

    def test_keyword_parameterization_and_owner_visibility(self):
        store = CaptureStore()
        query_entities("O'Brien", "person", "urn:hermes:kg:person-legacy-owner", store=store)
        query, params = store.calls[0]
        self.assertNotIn("O'Brien", query)
        self.assertEqual(params["keyword"], "o'brien")
        self.assertIn("$owner", query)
        self.assertIn("foaf_name", query)
        self.assertIn("KGLegacyUnresolved", query)

    def test_invalid_date_rows_do_not_abort_and_subseconds_are_included(self):
        store = CaptureStore([
            {"uri": "urn:hermes:kg:event-invalid", "time": "16:00-18:00"},
            {"uri": "urn:hermes:kg:event-day", "time": "2026-08-31T23:59:59.999+08:00"},
            {"uri": "urn:hermes:kg:event-next", "time": "2026-09-01T00:00:00+08:00"},
        ])
        rows = query_events("2026-08-01", "2026-08-31", "urn:hermes:kg:person-legacy-owner", store=store)
        self.assertEqual([row["uri"] for row in rows], ["urn:hermes:kg:event-day"])
        self.assertNotIn("datetime(", store.calls[0][0])
        self.assertEqual(store.calls[0][1]["person"], "urn:hermes:kg:person-legacy-owner")

    def test_memory_query_does_not_use_cross_scope_union(self):
        store = CaptureStore()
        output = query_natural("你记得我吗", store=store)
        self.assertEqual(output["kg_results"], [])
        self.assertNotIn("UNION", store.calls[0][0])
        self.assertIn("$owner", store.calls[0][0])

    def test_event_year_inference_metadata_reaches_natural_query_callers(self):
        store = CaptureStore([
            {"uri": "urn:hermes:kg:event-inferred", "time": "2026-09-17T00:00:00+08:00",
             "time_precision": "day", "time_year_inferred": True},
            {"uri": "urn:hermes:kg:event-unannotated", "time": "2026-09-16T18:30:00+08:00",
             "time_precision": None, "time_year_inferred": None},
        ])
        rows = query_natural("events", store=store)["kg_results"]
        self.assertIs(rows[0]["time_year_inferred"], True)
        self.assertEqual(rows[0]["time_precision"], "day")
        self.assertEqual(rows[0]["time"].hour, 0)
        self.assertIsNone(rows[1]["time_year_inferred"])
        self.assertIn("e.hermes_timeYearInferred AS time_year_inferred", store.calls[0][0])

    def test_memory_entity_results_keep_event_inference_annotation(self):
        store = CaptureStore([{"type": "Event", "time_precision": "day", "time_year_inferred": True}])
        result = query_natural("remember me", store=store)["kg_results"]
        self.assertIs(result[0]["time_year_inferred"], True)
        self.assertEqual(result[0]["time_precision"], "day")
        self.assertIn("n.hermes_timeYearInferred AS time_year_inferred", store.calls[0][0])
        self.assertIn("n.hermes_timePrecision AS time_precision", store.calls[0][0])


class OntologyTests(unittest.TestCase):
    def test_inferred_year_has_boolean_event_property_mapping(self):
        from add_concept import ONTOLOGY_PATH
        from rdflib import Graph, Literal, Namespace
        from rdflib.namespace import OWL, RDF, RDFS, XSD
        ontology = Namespace("urn:hermes:ontology:")
        graph = Graph().parse(str(ONTOLOGY_PATH), format="turtle")
        subject = ontology.timeYearInferred
        self.assertIn((subject, RDF.type, OWL.DatatypeProperty), graph)
        self.assertIn((subject, RDFS.domain, ontology.Event), graph)
        self.assertIn((subject, RDFS.range, XSD.boolean), graph)
        self.assertIn((subject, ontology.neo4jProperty, Literal("hermes_timeYearInferred")), graph)

    def test_real_rdf_parser_and_schema_context(self):
        from validate import validate
        from query_natural import load_schema_from_ontology
        self.assertEqual(validate(), [])
        schema = load_schema_from_ontology()
        self.assertEqual(len(schema["classes"]), 6)
        self.assertTrue(all(value["comment"] for value in schema["classes"].values()))
        self.assertTrue(all(value["domain"] and value["range"] for value in schema["properties"].values()))

    def test_semver_qnames_and_atomic_extension_on_temporary_copy(self):
        from add_concept import ONTOLOGY_PATH, _draft, append_draft, bump_version
        from rdflib import Graph, URIRef
        from rdflib.namespace import OWL, RDF, RDFS
        content = bump_version(ONTOLOGY_PATH.read_text(encoding="utf-8"))
        graph = Graph().parse(data=content, format="turtle")
        ontology = next(graph.subjects(RDF.type, OWL.Ontology))
        self.assertEqual(str(graph.value(ontology, OWL.versionInfo)), "1.1.0")
        draft = _draft("fixtureRelation", 'Quoted "description"', "ObjectProperty", "Person", "hermesont:Person")
        parsed = Graph().parse(data=draft, format="turtle")
        self.assertEqual(str(parsed.value(URIRef("urn:hermes:ontology:fixtureRelation"), RDFS.range)), "urn:hermes:ontology:Person")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "core.ttl")
            path.write_text(ONTOLOGY_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            append_draft(draft, path)
            self.assertIn((URIRef("urn:hermes:ontology:fixtureRelation"), RDF.type, OWL.ObjectProperty), Graph().parse(str(path), format="turtle"))
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                append_draft(draft, path)
            self.assertEqual(path.read_bytes(), before)

    def test_relation_delete_routes_to_deletion(self):
        import manage_entity
        class Store:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def delete_relation(self, *args): return 1
            def write_batch(self, *_): raise AssertionError("delete must not call write_batch")
        with patch.object(manage_entity, "KGStore", return_value=Store()):
            with contextlib.redirect_stdout(io.StringIO()):
                manage_entity.main(["--type", "relation", "--from", "urn:hermes:kg:person-fixture", "--to", "urn:hermes:kg:event-fixture", "--rel-type", "PARTICIPATED_IN", "--delete"])


if __name__ == "__main__":
    unittest.main()
