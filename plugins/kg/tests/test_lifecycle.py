"""Lifecycle contracts and opt-in transactions against an isolated Neo4j fixture."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import unittest
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugins"))
from kg.config import KGConfig
from kg.lifecycle import MemoryService, VersionConflict, evidence_for, payload_evidence
from kg.schema import normalize_properties
from kg.store import KGStore, _safe_temporals


class LifecycleContractTests(unittest.TestCase):
    def test_driver_boundary_preserves_zoneinfo_instant_and_dst_fold(self):
        for original in (datetime(2026, 10, 23, 9, 30, tzinfo=ZoneInfo('Asia/Shanghai')),
                         datetime(2024, 11, 3, 1, 30, tzinfo=ZoneInfo('America/New_York'), fold=1)):
            with self.subTest(original=original.isoformat()):
                converted = _safe_temporals({'event': [original]})['event'][0]
                self.assertIsInstance(converted.tzinfo, timezone)
                self.assertEqual(converted.timestamp(), original.timestamp())
                self.assertEqual(converted.replace(tzinfo=None), original.replace(tzinfo=None))
                self.assertEqual(converted.utcoffset(), original.utcoffset())

    def test_driver_datetime_nanoseconds_and_duration_survive_safe_boundary(self):
        try:
            from neo4j.time import DateTime, Duration
            from neo4j._codec.hydration.v2.temporal import dehydrate_datetime
        except ImportError:
            self.skipTest('Neo4j driver not installed')
        original = DateTime(2026, 10, 23, 9, 30, 0, 123456789, tzinfo=ZoneInfo('Asia/Shanghai'))
        duration = Duration(months=1, nanoseconds=789)
        values = _safe_temporals({'time': original, 'duration': duration})
        self.assertEqual(values['time'].nanosecond, 123456789)
        self.assertIsInstance(values['time'].tzinfo, timezone)
        self.assertIs(values['duration'], duration)
        # Exercise the actual failing driver serializer repeatedly, with fixed
        # offsets enforced by the same helper used for every graph transaction.
        for _ in range(1000):
            dehydrate_datetime(values['time'])

    def test_callers_cannot_forge_lifecycle_metadata(self):
        for field in ("hermes_revision", "hermes_origin", "hermes_userConfirmed",
                      "hermes_evidenceJson", "hermes_archivedByWorkbench"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                normalize_properties({field: "forged"})

    def test_evidence_is_preserved_without_inventing_old_sources(self):
        self.assertEqual(evidence_for(None, "urn:fixture"), [])
        original = {"entities": {"urn:fixture": [{"message_id": 7, "quote": "原始用户陈述"}]}}
        actual = evidence_for(original, "urn:fixture")
        actual[0]["quote"] = "changed"
        self.assertEqual(original["entities"]["urn:fixture"][0]["quote"], "原始用户陈述")
        payload = {"session_id": "fixture", "snapshot_id": "frozen", "entities": [
            {"type": "concept", "id": "x", "name": "X", "evidence": {"message_id": 7, "quote": "支持原文"}}
        ], "relations": []}
        result = payload_evidence(payload, KGConfig())
        citation = result["entities"]["urn:hermes:kg:concept-x"][0]
        self.assertEqual(citation["snapshot_id"], "frozen")
        self.assertEqual(citation["quote"], "支持原文")


@unittest.skipUnless(os.environ.get("KG_TEST_BOLT_URL"), "Set KG_TEST_BOLT_URL to an isolated test database")
class LifecycleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.prefix = "urn:kgtest_" + uuid.uuid4().hex + ":kg:"
        self.config = KGConfig(neo4j_url=os.environ["KG_TEST_BOLT_URL"],
            user=os.environ.get("KG_TEST_USER", "neo4j"), password=os.environ.get("KG_TEST_PASSWORD", ""),
            database=os.environ.get("KG_TEST_DATABASE", "neo4j"), urn_prefix=self.prefix,
            user_person_urn=self.prefix + "person-user")
        self.store = KGStore(self.config)
        self.store.ensure_schema()
        self.service = MemoryService(self.config, self.store)
        self.addCleanup(self.store.close)
        self.addCleanup(self.cleanup_graph)

    def cleanup_graph(self):
        # Never touch another profile, even on an assertion failure.
        with self.store.driver.session(database=self.config.database) as session:
            session.run("MATCH (n) WHERE n.namespace=$prefix OR n.uri STARTS WITH $prefix DETACH DELETE n", {"prefix": self.prefix}).consume()

    def entity(self, id="x", name="Original", **props):
        return {"type": "concept", "id": id, "name": name, "props": props}

    def create(self, id="x", name="Original", **props):
        self.store.write_batch([self.entity(id, name, **props)], [], origin="auto")
        return self.prefix + "concept-" + id

    def op(self):
        return uuid.uuid4().hex

    def test_correction_is_versioned_protected_and_idempotent_after_later_changes(self):
        uri = self.create(description="Old")
        operation = self.op()
        receipt = self.service.correct(uri, {"props": {"description": "User corrected"}},
            expected_version=1, operation_id=operation, reason="Explicit correction")
        card = self.service.detail(uri)
        self.assertEqual(card["version"], 2)
        self.assertTrue(card["user_confirmed"])
        self.assertEqual(card["origin"], "user_correction")
        self.assertEqual(len(self.service.revisions(uri)), 2)
        self.service.correct(uri, {"name": "Newer name"}, expected_version=2, operation_id=self.op())
        replay = self.service.correct(uri, {"props": {"description": "User corrected"}},
            expected_version=1, operation_id=operation, reason="Explicit correction")
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["receipt_id"], receipt["receipt_id"])
        with self.assertRaises(ValueError):
            self.service.correct(uri, {"name": "Different intent"}, expected_version=1, operation_id=operation)
        with self.assertRaises(VersionConflict):
            self.service.correct(uri, {"name": "Stale"}, expected_version=1, operation_id=self.op())

    def test_entire_anomalous_batch_is_deferred_with_immutable_payload_and_receipt(self):
        uri = self.create()
        self.service.correct(uri, {"name": "Reviewed"}, expected_version=1, operation_id=self.op())
        entities = [self.entity(name="Conflicting automatic name"), self.entity("safe", "Safe companion")]
        payload = {"entities": entities, "relations": []}
        snapshot = "snapshot-" + self.op()
        checkpoint = {"snapshot_id": snapshot, "extraction_hash": "source-hash"}
        receipt = self.store.write_batch(entities, [], "synthetic-session", checkpoint,
            origin="auto", review_payload=payload | {"snapshot_id": snapshot})
        self.assertEqual(receipt["disposition"], "deferred")
        self.assertEqual(receipt["entities_written"], 0)
        self.assertEqual(self.service.detail(uri)["name"], "Reviewed")
        self.assertEqual(len(self.service.list_memories()), 1)
        review = self.service.review(receipt["review_batch_id"])
        original = copy.deepcopy(review["payload"])
        self.assertEqual(review["status"], "pending")
        self.service.resolve_review(review["id"], "reject", operation_id=self.op())
        self.assertEqual(self.service.review(review["id"])["payload"], original)
        repeated = self.store.write_batch(entities, [], "synthetic-session", checkpoint,
            origin="auto", review_payload=payload | {"snapshot_id": snapshot})
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(repeated["disposition"], "deferred")
        self.assertEqual(len(self.service.reviews(status="all")), 1)

    def make_review(self):
        # These transaction fixtures omit Hermes transcripts; provenance is tested
        # by test_extraction_grounding and the production default fails closed.
        self.service.review_validator = lambda payload: None
        uri = self.create()
        self.service.correct(uri, {"name": "Reviewed"}, expected_version=1, operation_id=self.op())
        entity = self.entity(name="Automatic candidate")
        receipt = self.store.write_batch([entity], [], origin="auto", review_payload={"entities": [entity], "relations": []})
        return uri, self.service.review(receipt["review_batch_id"])

    def test_review_approval_is_a_new_protected_operation_and_can_include_user_edits(self):
        uri, review = self.make_review()
        operation = self.op()
        result = self.service.resolve_review(review["id"], "approve", operation_id=operation,
            changes={uri: {"name": "Human edited candidate"}})
        self.assertEqual(result["disposition"], "applied")
        card = self.service.detail(uri)
        self.assertEqual(card["name"], "Human edited candidate")
        self.assertEqual(card["version"], 3)
        self.assertTrue(card["user_confirmed"])
        self.assertEqual(card["evidence"][-1]["kind"], "user_edit")
        self.assertEqual(self.service.review(review["id"])["payload"], review["payload"])
        self.assertTrue(self.service.resolve_review(review["id"], "approve", operation_id=operation,
            changes={uri: {"name": "Human edited candidate"}})["idempotent"])

    def test_review_stale_versions_or_failed_source_validation_never_apply(self):
        uri, review = self.make_review()
        self.service.correct(uri, {"name": "New fact"}, expected_version=2, operation_id=self.op())
        with self.assertRaises(VersionConflict):
            self.service.resolve_review(review["id"], "approve", operation_id=self.op())
        self.assertEqual(self.service.detail(uri)["name"], "New fact")
        self.assertEqual(self.service.review(review["id"])["status"], "pending")
        self.service.review_validator = lambda payload: (_ for _ in ()).throw(ValueError("withdrawn source"))
        with self.assertRaises(ValueError):
            self.service.resolve_review(review["id"], "approve", operation_id=self.op(),
                expected_versions={uri: 3})

    def test_archive_restore_cannot_restore_legacy_quarantine_or_be_undone_by_sync(self):
        uri = self.create()
        self.service.archive(uri, expected_version=1, operation_id=self.op())
        self.assertTrue(self.service.detail(uri)["archived"])
        self.assertEqual(self.service.list_memories(), [])
        receipt = self.store.write_batch([self.entity(name="Automatic resurrection")], [], origin="auto")
        self.assertEqual(receipt["disposition"], "deferred")
        self.assertTrue(self.service.detail(uri)["archived"])
        self.service.restore(uri, expected_version=2, operation_id=self.op())
        self.assertFalse(self.service.detail(uri)["archived"])
        self.assertEqual(self.service.detail(uri)["name"], "Original")
        with self.store.driver.session(database=self.config.database) as session:
            session.run("MATCH (n {uri:$uri}) SET n:KGQuarantined REMOVE n.hermes_archivedByWorkbench", {"uri": uri}).consume()
        with self.assertRaisesRegex(ValueError, "Only memories archived"):
            self.service.restore(uri, expected_version=3, operation_id=self.op())

    def test_relationship_only_write_cannot_bypass_protection(self):
        uri = self.create()
        other = self.create("other")
        self.service.correct(uri, {"name": "Reviewed"}, expected_version=1, operation_id=self.op())
        receipt = self.store.write_batch([], [{"from": uri, "to": other, "type": "RELATED_TO"}], origin="auto")
        self.assertEqual(receipt["disposition"], "deferred")
        self.assertEqual(self.service.detail(uri)["relations"], [])

    def test_legacy_baseline_is_preserved_without_fabricated_evidence(self):
        uri = self.prefix + "concept-old"
        with self.store.driver.session(database=self.config.database) as session:
            session.run("CREATE (:Concept {uri:$uri,hermes_name:'Old card',shared:false})", {"uri": uri}).consume()
        self.assertEqual(self.service.detail(uri)["version"], 0)
        self.assertEqual(self.service.list_memories()[0]["uri"], uri)
        self.service.correct(uri, {"name": "Corrected old card"}, expected_version=0, operation_id=self.op())
        revisions = self.service.revisions(uri)
        self.assertEqual([row["version"] for row in revisions], [1, 0])
        self.assertEqual(revisions[-1]["action"], "legacy_baseline")
        self.assertEqual(revisions[-1]["evidence"], [])

    def test_two_concurrent_corrections_have_exactly_one_winner(self):
        uri = self.create()
        def change(name):
            try:
                return self.service.correct(uri, {"name": name}, expected_version=1, operation_id=self.op())
            except VersionConflict:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(change, ["First correction", "Second correction"]))
        self.assertEqual(sum(isinstance(result, dict) and result["ok"] for result in results), 1)
        self.assertEqual(results.count("conflict"), 1)
        self.assertEqual(self.service.detail(uri)["version"], 2)
        self.assertEqual(len(self.service.revisions(uri)), 2)

    def test_event_correction_preserves_native_database_timestamps(self):
        uri = self.prefix + "event-meeting"
        self.store.write_batch([{"type": "event", "uri": uri, "name": "Meeting",
            "props": {"event_time": "2026-10-23T09:00:00+08:00",
                      "end_time": "2026-10-23T10:00:00+08:00"}}], [])
        old_time = self.service.detail(uri)["props"]["hermes_eventTime"]
        self.service.correct(uri, {"props": {"description": "Corrected description"}},
            expected_version=1, operation_id=self.op())
        current = self.service.detail(uri)
        self.assertEqual(current["props"]["hermes_eventTime"], old_time)
        self.assertEqual(current["version"], 2)

    def test_event_replacement_clears_obsolete_inferred_year_and_recomputes_user_precision(self):
        uri = self.prefix + 'event-inferred'
        self.store.write_batch([{'type': 'event', 'uri': uri, 'name': 'Event', 'props': {
            'event_time': '2026-10-23', 'time_precision': 'day', 'hermes_timeYearInferred': True}}], [])
        self.store.write_batch([{'type': 'event', 'uri': uri, 'name': 'Event', 'props': {
            'event_time': '2027-10-23', 'time_precision': 'day'}}], [], origin='auto')
        self.assertNotIn('hermes_timeYearInferred', self.service.detail(uri)['props'])
        self.service.correct(uri, {'props': {'event_time': '2028-10-23T14:00:00+08:00'}},
            expected_version=2, operation_id=self.op())
        props = self.service.detail(uri)['props']
        self.assertEqual(props['hermes_timePrecision'], 'datetime')
        self.assertFalse(props['hermes_timeYearInferred'])

    def test_read_parameters_and_loaded_named_datetime_use_fixed_offsets(self):
        moment = datetime(2026, 10, 23, 12, 30, tzinfo=ZoneInfo('Asia/Shanghai'))
        returned = self.store.run_read('RETURN $moment AS moment', {'moment': moment})[0]['moment']
        self.assertIsInstance(returned.tzinfo, timezone)
        self.assertEqual(returned.to_native().timestamp(), moment.timestamp())
        # Server-created old named-zone values must also become safe before
        # they are JSON-encoded or written again by a correction/restore.
        loaded = self.store.run_read("RETURN datetime('2026-10-23T12:30:00.123456789[Asia/Shanghai]') AS moment")[0]['moment']
        self.assertIsInstance(loaded.tzinfo, timezone)
        self.assertEqual(loaded.nanosecond, 123456789)

    def test_foreign_private_endpoints_and_confirmed_aliases_remain_unwritable(self):
        uri = self.create()
        foreign = "urn:foreign_test_" + uuid.uuid4().hex + ":kg:concept-private"
        with self.store.driver.session(database=self.config.database) as session:
            session.run("CREATE (:KGEntity:Concept {uri:$uri,hermes_name:'External',shared:false})", {"uri": foreign}).consume()
        try:
            with self.assertRaises(ValueError):
                self.store.write_batch([], [{"from": uri, "to": foreign, "type": "RELATED_TO"}], origin="auto")
            with self.assertRaises(ValueError):
                self.service.detail(foreign)
            with self.store.driver.session(database=self.config.database) as session:
                session.run("MATCH (n {uri:$uri}) SET n.hermes_aliasConfirmed=true,n:KGQuarantined", {"uri": uri}).consume()
            with self.assertRaises(ValueError):
                self.store.write_batch([self.entity(name="Alias overwrite")], [], origin="auto")
            with self.assertRaises(ValueError):
                self.service.restore(uri, expected_version=1, operation_id=self.op())
        finally:
            with self.store.driver.session(database=self.config.database) as session:
                session.run("MATCH (n {uri:$uri}) DETACH DELETE n", {"uri": foreign}).consume()

    def test_relationship_change_versions_source_card_and_cannot_skip_endpoint_versions(self):
        uri, other = self.create(), self.create("other")
        relation = {"from": uri, "to": other, "type": "RELATED_TO"}
        with self.assertRaisesRegex(ValueError, "every entity and local endpoint"):
            self.store.write_batch([], [relation], origin="review_approved", expected_versions={uri: 1})
        self.store.write_batch([], [relation], origin="review_approved",
            expected_versions={uri: 1, other: 1}, operation_id=self.op())
        self.assertEqual(self.service.detail(uri)["version"], 2)
        revision = self.service.revisions(uri)[0]
        self.assertEqual(revision["action"], "relationship_write")
        self.assertEqual(revision["state"]["relations"][0]["to_uri"], other)
        with self.assertRaises(VersionConflict):
            self.store.write_batch([], [relation], origin="review_approved",
                expected_versions={uri: 1, other: 1}, operation_id=self.op())

    def test_person_name_alias_and_same_batch_collisions_are_deferred_without_merging(self):
        first = {'type': 'person', 'id': 'alice', 'name': 'Alice', 'props': {'alias_names': ['A. Example']}}
        self.store.write_batch([first], [])
        for name in ('ＡＬＩＣＥ', ' a.   example '):
            with self.subTest(name=name):
                candidate = {'type': 'person', 'id': 'other-' + self.op(), 'name': name}
                result = self.store.write_batch([candidate], [], origin='auto')
                self.assertEqual(result['disposition'], 'deferred')
                review = self.service.review(result['review_batch_id'])
                self.assertIn('possible_duplicate_person', review['reason_codes'])
                self.assertIn(self.prefix + 'person-alice', review['possible_duplicate_uris'])
        result = self.store.write_batch([{'type': 'person', 'id': 'bob-a', 'name': 'Bob'},
                                        {'type': 'person', 'id': 'bob-b', 'name': 'BOB'}], [], origin='auto')
        self.assertEqual(result['disposition'], 'deferred')
        self.assertEqual(len(self.service.list_memories(entity_type='person')), 1)

    def test_related_card_context_has_direction_and_hides_only_archived_other_endpoints(self):
        person = self.prefix + 'person-context'
        event = self.prefix + 'event-context'
        self.store.write_batch([{'type': 'person', 'uri': person, 'name': 'Context Person'},
            {'type': 'event', 'uri': event, 'name': 'Context Meeting',
             'props': {'event_time': '2026-10-23T11:00:00+08:00'}}],
            [{'from': person, 'to': event, 'type': 'PARTICIPATED_IN'}])
        relation = self.service.detail(person)['relations'][0]
        self.assertEqual(relation['related_uri'], event)
        self.assertEqual(relation['related_name'], 'Context Meeting')
        self.assertEqual(relation['related_type'], 'event')
        self.assertEqual(relation['related_time'].hour, 11)
        self.assertEqual(relation['relation_label'], '参与的事件')
        self.assertEqual(relation['from_name'], 'Context Person')
        self.assertEqual(self.service.detail(event)['relations'][0]['relation_label'], '参与者')
        self.service.archive(event, expected_version=1, operation_id=self.op())
        self.assertEqual(self.service.detail(person)['relations'], [])
        archived = self.service.detail(event)
        self.assertTrue(archived['archived'])
        self.assertEqual(archived['relations'][0]['related_uri'], person)


if __name__ == "__main__":
    unittest.main()
