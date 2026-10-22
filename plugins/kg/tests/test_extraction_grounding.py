"""Independent provenance regression tests; all source data is synthetic.

These assert exact provenance and date/identity boundaries. They do not claim
that quote matching proves arbitrary semantic entailment, negation, or ownership.
"""
import copy
from contextlib import closing
import json
from datetime import datetime
from pathlib import Path
import sys
import sqlite3
import tempfile
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins"))
sys.path.insert(0, str(ROOT / "scripts"))
from kg.config import KGConfig
import kg_sync_runner as runner


class GroundingTests(unittest.TestCase):
    def setUp(self):
        self.config = KGConfig()
        self.timestamp = datetime(2026, 9, 13, 0, 30, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        self.snapshot = {"session_id": "grounding-fixture-session", "snapshot_id": "grounding-fixture-snapshot",
                         "after_message_id": 0, "last_message_id": 3,
                         "messages": [{"id": 1, "role": "user", "content": "I met Ada at the library.", "timestamp": self.timestamp},
                                      {"id": 2, "role": "assistant", "content": "I suggest using Markdown for all notes.", "timestamp": self.timestamp},
                                      {"id": 3, "role": "user", "content": "I prefer concise meeting notes.", "timestamp": self.timestamp}]}

    def person(self):
        return {"type": "Person", "urn": "urn:hermes:kg:person-ada", "name": "Ada", "props": {},
                "evidence": {"message_id": 1, "quote": "I met Ada at the library."}}

    def preference(self):
        return {"type": "Preference", "urn": "urn:hermes:kg:preference-concise-notes", "name": "concise meeting notes",
                "props": {"pref_key": "meeting notes", "pref_value": "concise"},
                "evidence": {"message_id": 3, "quote": "I prefer concise meeting notes."}}

    def event(self, quote, value):
        self.snapshot["messages"][0]["content"] = quote
        return {"type": "Event", "urn": "urn:hermes:kg:event-fixture", "name": "Meeting",
                "props": {"event_time": value}, "evidence": {"message_id": 1, "quote": quote}}

    def payload(self, entities=None, relations=None):
        return {"session_id": self.snapshot["session_id"], "snapshot_id": self.snapshot["snapshot_id"],
                "entities": entities or [], "relations": relations or []}

    def validate(self, payload):
        return runner._validate_extraction(json.dumps(payload, ensure_ascii=False), self.snapshot, self.config)

    def test_explicit_user_person_and_preference_are_accepted(self):
        payload = self.payload([self.person(), self.preference()])
        result = self.validate(payload)
        self.assertEqual(len(result["entities"]), 2)
        self.assertEqual(result["entities"][0]["evidence"]["message_id"], 1)

    def test_missing_malformed_or_nonverbatim_evidence_is_rejected(self):
        invalid = [None, {}, [], {"message_id": 1}, {"message_id": True, "quote": "I met Ada at the library."},
                   {"message_id": "1", "quote": "I met Ada at the library."},
                   {"message_id": 999, "quote": "I met Ada at the library."},
                   {"message_id": 1, "quote": ""}, {"message_id": 1, "quote": "   "},
                   {"message_id": 1, "quote": "I met Grace at the library."},
                   {"message_id": 1, "quote": "I met Ada at the library.", "inferred": True}]
        for evidence in invalid:
            with self.subTest(evidence=evidence):
                entity = self.person()
                if evidence is None:
                    entity.pop("evidence")
                else:
                    entity["evidence"] = evidence
                with self.assertRaises(ValueError):
                    self.validate(self.payload([entity]))

    def test_valid_quote_from_another_message_is_not_accepted(self):
        entity = self.person()
        entity["evidence"]["message_id"] = 3
        with self.assertRaises(ValueError):
            self.validate(self.payload([entity]))

    def test_assistant_tool_or_system_text_cannot_supply_user_evidence(self):
        for role in ("assistant", "tool", "system"):
            with self.subTest(role=role):
                self.snapshot["messages"][0]["role"] = role
                with self.assertRaises(ValueError):
                    self.validate(self.payload([self.person()]))

    def test_unicode_quote_requires_actual_source_characters(self):
        # Precomposed e-acute is not an exact quote of decomposed source text.
        self.snapshot["messages"][0]["content"] = "I met Ade\u0301 at the library."
        entity = self.person()
        entity["name"] = "Adé"
        entity["evidence"]["quote"] = "I met Adé at the library."
        with self.assertRaises(ValueError):
            self.validate(self.payload([entity]))

    def test_person_name_cannot_be_inferred_from_identifier(self):
        entity = self.person()
        entity["name"] = "Grace"
        with self.assertRaises(ValueError):
            self.validate(self.payload([entity]))

    def test_current_user_cannot_be_recreated_even_with_a_name_quote(self):
        self.snapshot["messages"][0]["content"] = "Please call me Bill in all replies."
        for urn in (self.config.user_person_urn, "urn:hermes:kg:person:bill"):
            with self.subTest(urn=urn):
                entity = {"type": "Person", "urn": urn, "name": "Bill", "props": {},
                          "evidence": {"message_id": 1, "quote": "Please call me Bill in all replies."}}
                with self.assertRaises(ValueError):
                    self.validate(self.payload([entity]))

    def test_current_user_remains_a_valid_relation_anchor(self):
        preference = self.preference()
        relation = {"from_urn": self.config.user_person_urn, "to_urn": preference["urn"],
                    "relation_type": "EXPRESSED_PREFERENCE", "props": {},
                    "evidence": copy.deepcopy(preference["evidence"])}
        result = self.validate(self.payload([preference], [relation]))
        self.assertEqual(result["relations"][0]["from_urn"], self.config.user_person_urn)

    def test_explicit_relationship_requires_its_own_evidence(self):
        person = self.person()
        relation = {"from_urn": self.config.user_person_urn, "to_urn": person["urn"], "relation_type": "KNOWS", "props": {}}
        with self.assertRaises(ValueError):
            self.validate(self.payload([person], [relation]))
        relation["evidence"] = {"message_id": 2, "quote": self.snapshot["messages"][1]["content"]}
        with self.assertRaises(ValueError):
            self.validate(self.payload([person], [relation]))

    def test_message_timestamp_alone_is_not_event_evidence(self):
        entity = self.event("I decided to use Markdown for Project Violet notes.", "2026-09-13T00:30:00+08:00")
        with self.assertRaises(ValueError):
            self.validate(self.payload([entity]))

    def test_recorded_at_metadata_cannot_be_quoted_as_body(self):
        entity = self.event("I decided to use Markdown for Project Violet notes.", "2026-09-13T00:30:00+08:00")
        self.snapshot["messages"][0]["recorded_at"] = "2026-09-13T00:30:00+08:00"
        entity["evidence"]["quote"] = "2026-09-13T00:30:00+08:00"
        with self.assertRaises(ValueError):
            self.validate(self.payload([entity]))

    def test_absolute_date_accepts_day_precision_but_rejects_invented_clock(self):
        for quote in ("I met Ada on 2026-09-12.", "我在2026年9月12日见了Ada。"):
            with self.subTest(quote=quote):
                entity = self.event(quote, "2026-09-12T00:00:00+08:00")
                output = self.validate(self.payload([entity]))
                self.assertEqual(output["entities"][0]["props"]["hermes_timePrecision"], "day")
                entity["props"]["event_time"] = "2026-09-12T08:30:00+08:00"
                with self.assertRaises(ValueError):
                    self.validate(self.payload([entity]))
                entity["props"]["event_time"] = "2026-09-13T00:00:00+08:00"
                with self.assertRaises(ValueError):
                    self.validate(self.payload([entity]))

    def test_explicit_iso_timestamp_accepts_same_instant_only(self):
        entity = self.event("I met Ada at 2026-09-12T15:30:00+08:00.", "2026-09-12T07:30:00Z")
        output = self.validate(self.payload([entity]))
        self.assertEqual(output["entities"][0]["props"]["hermes_timePrecision"], "datetime")
        entity["props"]["event_time"] = "2026-09-12T07:31:00Z"
        with self.assertRaises(ValueError):
            self.validate(self.payload([entity]))

    def test_clock_time_needs_matching_quoted_precision(self):
        entity = self.event("I met Ada on 2026-09-12 at 15:30.", "2026-09-12T15:30:00+08:00")
        self.validate(self.payload([entity]))
        for value in ("2026-09-12T15:31:00+08:00", "2026-09-12T15:30:01+08:00", "2026-09-12T15:30:00.001+08:00"):
            with self.subTest(value=value):
                entity["props"]["event_time"] = value
                with self.assertRaises(ValueError):
                    self.validate(self.payload([entity]))

    def test_relative_dates_use_original_timestamp_in_configured_timezone(self):
        # Source is Sep 13 in Shanghai and still Sep 12 in UTC.
        for phrase, expected in (("yesterday", "2026-09-12"), ("today", "2026-09-13"), ("tomorrow", "2026-09-14")):
            with self.subTest(phrase=phrase):
                entity = self.event(f"I met Ada {phrase}.", expected + "T00:00:00+08:00")
                self.validate(self.payload([entity]))
                entity["props"]["event_time"] = "2027-01-01T00:00:00+08:00"
                with self.assertRaises(ValueError):
                    self.validate(self.payload([entity]))

    def test_relative_date_without_valid_source_timestamp_is_rejected(self):
        entity = self.event("I met Ada yesterday.", "2026-09-12T00:00:00+08:00")
        for timestamp in (None, True, "2026-09-13T00:30:00+08:00"):
            with self.subTest(timestamp=timestamp):
                self.snapshot["messages"][0]["timestamp"] = timestamp
                with self.assertRaises(ValueError):
                    self.validate(self.payload([entity]))

    def test_unknown_relative_dates_do_not_fall_back_to_run_time(self):
        entity = self.event("I met Ada last month.", "2026-08-13T00:00:00+08:00")
        with self.assertRaises(ValueError):
            self.validate(self.payload([entity]))

    def test_one_invalid_fact_rejects_the_entire_extraction(self):
        valid, invalid = self.person(), self.preference()
        invalid.pop("evidence")
        payload = self.payload([valid, invalid])
        with self.assertRaises(ValueError):
            self.validate(payload)
        # The caller receives no partially valid return payload to stage/commit.

    def test_empty_extraction_remains_valid(self):
        self.assertEqual(self.validate(self.payload())["entities"], [])


class GroundingReplayTests(unittest.TestCase):
    def test_staged_payload_from_old_extractor_cannot_bypass_grounding(self):
        from kg.sync_state import SyncState
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = KGConfig(home=home)
            with closing(sqlite3.connect(home / "state.db")) as connection:
                connection.executescript("CREATE TABLE sessions(id TEXT PRIMARY KEY,source TEXT,started_at REAL);"
                                        "CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,timestamp REAL);")
                connection.execute("INSERT INTO sessions VALUES('replay-fixture','desktop',1000)")
                connection.executemany("INSERT INTO messages VALUES(?,?,?,?,?)", [
                    (index, "replay-fixture", "user" if index % 2 else "assistant", "I met Ada at the library.", 1000 + index)
                    for index in range(1, 7)
                ])
                connection.commit()
            state = SyncState(home)
            snapshot = state.discover()[0]
            legacy_payload = {"session_id": snapshot["session_id"], "snapshot_id": snapshot["snapshot_id"],
                              "entities": [{"type": "Person", "urn": "urn:hermes:kg:person-ada", "name": "Ada", "props": {}}], "relations": []}
            state.stage_extraction(snapshot["snapshot_id"], legacy_payload)
            def forbidden_model(**kwargs):
                self.fail("Durable extraction must not be silently recomputed")
            def forbidden_writer(*args, **kwargs):
                self.fail("Ungrounded legacy extraction reached the graph")
            code, report = runner.run(home=home, state=state, config=config, llm=forbidden_model, writer=forbidden_writer)
            self.assertEqual((code, report["phase"], report["committed"]), (1, "validation", 0))
            self.assertEqual(state.status()["committed_sessions"], 0)
            self.assertEqual(state.get_extraction(snapshot["snapshot_id"]), legacy_payload)

    def test_staged_date_evidence_without_derived_precision_requires_review(self):
        from kg.sync_state import SyncState
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = KGConfig(home=home)
            quote = "I met Ada on 2026-09-12."
            with closing(sqlite3.connect(home / "state.db")) as connection:
                connection.executescript("CREATE TABLE sessions(id TEXT PRIMARY KEY,source TEXT,started_at REAL);"
                                        "CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,timestamp REAL);")
                connection.execute("INSERT INTO sessions VALUES('precision-fixture','desktop',1000)")
                connection.executemany("INSERT INTO messages VALUES(?,?,?,?,?)", [
                    (index, "precision-fixture", "user" if index % 2 else "assistant", quote, 1000 + index)
                    for index in range(1, 7)
                ])
                connection.commit()
            state = SyncState(home)
            snapshot = state.discover()[0]
            staged = {"session_id": snapshot["session_id"], "snapshot_id": snapshot["snapshot_id"],
                      "entities": [{"type": "Event", "urn": "urn:hermes:kg:event-precision-fixture", "name": "Meeting Ada",
                                    "props": {"event_time": "2026-09-12"}, "evidence": {"message_id": 1, "quote": quote}}],
                      "relations": []}
            state.stage_extraction(snapshot["snapshot_id"], staged)
            with closing(sqlite3.connect(state.path)) as connection:
                original = connection.execute("SELECT payload_json,extraction_hash FROM snapshots WHERE snapshot_id=?", (snapshot["snapshot_id"],)).fetchone()
            # The source is adequate; the only incompatibility is a newer
            # normalization step adding precision to an immutable older payload.
            checked = runner._validate_extraction(json.dumps(staged), snapshot, config)
            self.assertEqual(checked["entities"][0]["props"]["hermes_timePrecision"], "day")
            self.assertNotEqual(checked, staged)
            def forbidden_model(**kwargs):
                self.fail("Staged payload must not be silently re-extracted")
            def forbidden_writer(*args, **kwargs):
                self.fail("Old normalized payload reached the graph")
            code, report = runner.run(home=home, state=state, config=config, llm=forbidden_model, writer=forbidden_writer)
            self.assertEqual((code, report["phase"], report["committed"]), (1, "validation", 0))
            self.assertEqual(state.status()["committed_sessions"], 0)
            with closing(sqlite3.connect(state.path)) as connection:
                after = connection.execute("SELECT payload_json,extraction_hash FROM snapshots WHERE snapshot_id=?", (snapshot["snapshot_id"],)).fetchone()
            self.assertEqual(after, original)
            self.assertEqual(state.get_extraction(snapshot["snapshot_id"]), staged)


class ExtractionInputBoundaryTests(unittest.TestCase):
    """The extractor must exclude non-user source text before calling a model."""

    def setUp(self):
        self.config = KGConfig()
        self.snapshot = {"session_id": "input-boundary-fixture-session", "snapshot_id": "input-boundary-fixture-snapshot",
                         "after_message_id": 0, "last_message_id": 6, "messages": []}

    @staticmethod
    def record(message_id, role, content):
        return {"id": message_id, "role": role, "content": content, "timestamp": 1789259400 + message_id}

    def empty_payload(self):
        return {"session_id": self.snapshot["session_id"], "snapshot_id": self.snapshot["snapshot_id"], "entities": [], "relations": []}

    def test_model_sees_only_user_records_and_no_assistant_tool_or_system_response(self):
        first = self.record(1, "user", "请记住，我更喜欢简短的会议纪要。")
        second = self.record(5, "user", "I decided to use Markdown for Project Violet notes.")
        self.snapshot["messages"] = [
            first,
            self.record(2, "assistant", "ASSISTANT_PRIVATE_RESPONSE_SENTINEL: fictional access_token=do-not-send-this-response"),
            self.record(3, "tool", "TOOL_PRIVATE_RESPONSE_SENTINEL: fictional credential=do-not-send-this-tool-output"),
            self.record(4, "system", "SYSTEM_PRIVATE_RESPONSE_SENTINEL: do-not-send-this-source-system-message"),
            second,
            self.record(6, "assistant", "ASSISTANT_COMMAND_RESPONSE_SENTINEL: shell command and extracted fact suggestion"),
        ]
        original = copy.deepcopy(self.snapshot)
        calls = []
        def fake_llm(**kwargs):
            calls.append(kwargs)
            serialized = json.dumps(kwargs["messages"], ensure_ascii=False)
            for sentinel in ("ASSISTANT_PRIVATE_RESPONSE_SENTINEL", "TOOL_PRIVATE_RESPONSE_SENTINEL", "SYSTEM_PRIVATE_RESPONSE_SENTINEL", "ASSISTANT_COMMAND_RESPONSE_SENTINEL"):
                self.assertNotIn(sentinel, serialized)
            request = json.loads(kwargs["messages"][1]["content"].partition("\n")[2])
            self.assertEqual(request["session_id"], self.snapshot["session_id"])
            self.assertEqual(request["snapshot_id"], self.snapshot["snapshot_id"])
            self.assertEqual([message["id"] for message in request["messages"]], [1, 5])
            self.assertTrue(all(message["role"] == "user" for message in request["messages"]))
            self.assertEqual([message["content"] for message in request["messages"]], [first["content"], second["content"]])
            return json.dumps(self.empty_payload())
        output = runner._extract(self.snapshot, self.config, llm=fake_llm)
        self.assertEqual(json.loads(output), self.empty_payload())
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.snapshot, original, "Filtering must not alter the complete evidence/checkpoint snapshot")

    def test_user_text_is_preserved_verbatim_without_length_truncation(self):
        content = "BEGIN_USER_SOURCE\n" + ('中文原文 with "quotes", backslash \\ and a newline\n' * 1000) + "END_USER_SOURCE_保留末尾"
        self.snapshot["messages"] = [self.record(1, "user", content)]
        def fake_llm(**kwargs):
            request = json.loads(kwargs["messages"][1]["content"].partition("\n")[2])
            self.assertEqual(len(request["messages"]), 1)
            actual = request["messages"][0]["content"]
            self.assertEqual(actual, content)
            self.assertEqual(len(actual), len(content))
            self.assertTrue(actual.endswith("END_USER_SOURCE_保留末尾"))
            return json.dumps(self.empty_payload())
        self.assertEqual(json.loads(runner._extract(self.snapshot, self.config, llm=fake_llm)), self.empty_payload())

    def test_no_user_records_returns_empty_extraction_without_model_call(self):
        non_user_cases = [[], [self.record(1, "assistant", "An assistant-generated memory suggestion")],
                          [self.record(1, "tool", "A tool response")], [self.record(1, "system", "A source system message")],
                          [self.record(1, "assistant", "Suggestion"), self.record(2, "tool", "Tool output")]]
        def forbidden_llm(**kwargs):
            self.fail("A snapshot without user source text must not call the model")
        for messages in non_user_cases:
            with self.subTest(roles=[message["role"] for message in messages]):
                self.snapshot["messages"] = messages
                original = copy.deepcopy(self.snapshot)
                output = runner._extract(self.snapshot, self.config, llm=forbidden_llm)
                self.assertEqual(json.loads(output), self.empty_payload())
                self.assertEqual(self.snapshot, original)


class ExtractionCorrectionTests(unittest.TestCase):
    """Correction is bounded and must never create a path around validation."""

    def setUp(self):
        self.config = KGConfig()
        self.quote = "I met Ada at the library."
        self.snapshot = {"session_id": "correction-fixture-session", "snapshot_id": "correction-fixture-snapshot",
                         "has_extraction": False,
                         "messages": [{"id": 1, "role": "user", "content": self.quote, "timestamp": 1789259400}]}
        self.valid = {"session_id": self.snapshot["session_id"], "snapshot_id": self.snapshot["snapshot_id"],
                      "entities": [{"type": "Person", "urn": "urn:hermes:kg:person-ada", "name": "Ada", "props": {},
                                    "evidence": {"message_id": 1, "quote": self.quote}}], "relations": []}
        self.invalid = copy.deepcopy(self.valid)
        self.invalid["entities"][0].pop("evidence")
        self.invalid["entities"][0]["urn"] = "urn:hermes:kg:person-rejected-draft"

    def test_first_invalid_then_valid_returns_only_validated_replacement(self):
        requests, responses, call_callbacks = [], [], []
        drafts = [json.dumps(self.invalid), json.dumps(self.valid)]
        def fake_llm(**kwargs):
            requests.append(kwargs)
            return drafts[len(requests) - 1]
        result = runner._extract_validated(
            self.snapshot, self.config, llm=fake_llm,
            on_call=lambda: call_callbacks.append(True),
            on_response=lambda text, attempt: responses.append((text, attempt)),
        )
        self.assertEqual(result, self.valid)
        self.assertNotIn("person-rejected-draft", json.dumps(result))
        self.assertEqual(len(requests), 2)
        self.assertEqual(len(call_callbacks), 2)
        self.assertEqual(responses, [(drafts[0], 0), (drafts[1], 1)])
        correction_messages = requests[1]["messages"]
        self.assertEqual(correction_messages[2], {"role": "assistant", "content": drafts[0]})
        self.assertIn("REJECTED", correction_messages[3]["content"])
        self.assertIn("evidence", correction_messages[3]["content"])
        self.assertEqual(requests[0]["messages"][1], correction_messages[1], "Correction must preserve the original user source")

    def test_two_invalid_drafts_raise_after_two_calls_and_never_stage(self):
        requests = []
        def fake_llm(**kwargs):
            requests.append(kwargs)
            if len(requests) > 2:
                self.fail("Validation correction exceeded its two-call bound")
            return json.dumps(self.invalid)
        with self.assertRaises(runner.ExtractionValidationError):
            runner._extract_validated(self.snapshot, self.config, llm=fake_llm)
        self.assertEqual(len(requests), 2)

        requests.clear()
        snapshot = copy.deepcopy(self.snapshot)
        test_case = self
        class NoStageState:
            def discover(self, **kwargs):
                return [snapshot]
            def stage_extraction(self, *args, **kwargs):
                test_case.fail("Twice-rejected extraction must not be staged")
            def commit(self, *args, **kwargs):
                test_case.fail("Twice-rejected extraction must not reach graph commit")
        with tempfile.TemporaryDirectory() as directory:
            code, report = runner.run(home=directory, state=NoStageState(), config=KGConfig(home=Path(directory)), llm=fake_llm)
        self.assertEqual((code, report["phase"], report["committed"], report["llm_calls"]), (1, "validation", 0, 2))
        self.assertEqual(len(requests), 2)

    def test_api_connection_failure_is_not_retried_as_schema_correction(self):
        requests, calls, responses = [], [], []
        def disconnected(**kwargs):
            requests.append(kwargs)
            raise ConnectionError("Synthetic upstream TLS connection failure")
        with self.assertRaises(ConnectionError):
            runner._extract_validated(
                self.snapshot, self.config, llm=disconnected,
                on_call=lambda: calls.append(True),
                on_response=lambda *args: responses.append(args),
            )
        self.assertEqual(len(requests), 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(responses, [])

    def test_no_user_source_skips_model_and_all_callbacks(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["messages"] = [{"id": 1, "role": "assistant", "content": "An unsupported suggestion", "timestamp": 1789259400}]
        def forbidden(*args, **kwargs):
            self.fail("No-user extraction must not call the model or response/call callbacks")
        result = runner._extract_validated(snapshot, self.config, llm=forbidden, on_call=forbidden, on_response=forbidden)
        self.assertEqual(result, {"session_id": snapshot["session_id"], "snapshot_id": snapshot["snapshot_id"], "entities": [], "relations": []})


class SupportingEventEvidenceTests(unittest.TestCase):
    """Combine only verified citations, with per-citation source-year inference."""

    def setUp(self):
        self.config = KGConfig()
        self.date_quote = "我们9月17号见Ada。"
        self.clock_quote = "时间改到18:30。"
        timestamp = datetime(2026, 9, 13, 12, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        self.snapshot = {"session_id": "supporting-fixture-session", "snapshot_id": "supporting-fixture-snapshot",
                         "messages": [{"id": 1, "role": "user", "content": self.date_quote, "timestamp": timestamp},
                                      {"id": 2, "role": "assistant", "content": "建议你在9月17号19:00见Ada。", "timestamp": timestamp},
                                      {"id": 3, "role": "user", "content": self.clock_quote, "timestamp": timestamp}]}

    def event(self, value="2026-09-17T18:30:00+08:00"):
        return {"type": "Event", "urn": "urn:hermes:kg:event-supporting-fixture", "name": "与Ada见面",
                "props": {"event_time": value}, "evidence": {"message_id": 3, "quote": self.clock_quote},
                "supporting_evidence": [{"message_id": 1, "quote": self.date_quote}]}

    def payload(self, entity):
        return {"session_id": self.snapshot["session_id"], "snapshot_id": self.snapshot["snapshot_id"], "entities": [entity], "relations": []}

    def validate(self, entity):
        return runner._validate_extraction(json.dumps(self.payload(entity), ensure_ascii=False), self.snapshot, self.config)

    def test_split_date_and_later_revised_time_use_verified_user_quotes(self):
        entity = self.event()
        self.snapshot["messages"][0]["content"] = "我们9月17号18:00见Ada。"
        entity["supporting_evidence"][0]["quote"] = self.snapshot["messages"][0]["content"]
        result = self.validate(entity)
        props = result["entities"][0]["props"]
        self.assertEqual(props["hermes_timePrecision"], "datetime")
        self.assertIs(props["hermes_timeYearInferred"], True)
        self.assertEqual(props["event_time"], "2026-09-17T18:30:00+08:00")
        entity["props"]["event_time"] = "2026-09-17T19:00:00+08:00"
        with self.assertRaises(ValueError):
            self.validate(entity)

    def test_primary_date_can_use_supporting_clock(self):
        entity = self.event()
        entity["evidence"], entity["supporting_evidence"] = entity["supporting_evidence"][0], [entity["evidence"]]
        self.assertIs(self.validate(entity)["entities"][0]["props"]["hermes_timeYearInferred"], True)

    def test_year_comes_from_date_citation_not_primary_message_or_run_year(self):
        # UTC is still 2023, but the date citation was sent on Jan 1, 2024 locally.
        self.snapshot["messages"][0]["timestamp"] = datetime.fromisoformat("2023-12-31T17:00:00+00:00").timestamp()
        entity = self.event("2024-09-17T18:30:00+08:00")
        self.assertIs(self.validate(entity)["entities"][0]["props"]["hermes_timeYearInferred"], True)
        for wrong_year in (2023, 2025, 2026):
            with self.subTest(wrong_year=wrong_year):
                entity["props"]["event_time"] = f"{wrong_year}-09-17T18:30:00+08:00"
                with self.assertRaises(ValueError):
                    self.validate(entity)

    def test_yearless_date_does_not_choose_nearest_future_year(self):
        self.snapshot["messages"][0]["timestamp"] = datetime(2026, 12, 31, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        self.snapshot["messages"][0]["content"] = "我们1月2日见Ada。"
        entity = self.event("2026-01-02T18:30:00+08:00")
        entity["supporting_evidence"][0]["quote"] = self.snapshot["messages"][0]["content"]
        self.validate(entity)
        entity["props"]["event_time"] = "2027-01-02T18:30:00+08:00"
        with self.assertRaises(ValueError):
            self.validate(entity)

    def test_explicit_unsupported_year_qualifier_is_not_yearless(self):
        for quote in ("我们明年9月17日见Ada。", "我们去年 9月17号见Ada。", "我们26年 9月17号见Ada。"):
            with self.subTest(quote=quote):
                self.snapshot["messages"][0]["content"] = quote
                entity = self.event()
                entity["supporting_evidence"][0]["quote"] = quote
                with self.assertRaises(ValueError):
                    self.validate(entity)

    def test_yearless_date_requires_its_own_valid_timestamp(self):
        for timestamp in (None, True, "2026-09-13", float("nan"), float("inf")):
            with self.subTest(timestamp=timestamp):
                self.snapshot["messages"][0]["timestamp"] = timestamp
                with self.assertRaises(ValueError):
                    self.validate(self.event())
        # Clock-only evidence does not supply or need the inferred year.
        self.snapshot["messages"][0]["timestamp"] = datetime(2026, 9, 13, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        self.snapshot["messages"][2]["timestamp"] = None
        self.validate(self.event())

    def test_explicit_chinese_year_is_not_reinterpreted_as_yearless(self):
        self.snapshot["messages"][0]["content"] = "我们2024 年 9月17号见Ada。"
        self.snapshot["messages"][0]["timestamp"] = None
        entity = self.event("2024-09-17T18:30:00+08:00")
        entity["supporting_evidence"][0]["quote"] = self.snapshot["messages"][0]["content"]
        result = self.validate(entity)
        self.assertNotIn("hermes_timeYearInferred", result["entities"][0]["props"])

    def test_supporting_quotes_must_all_be_valid_and_are_bounded(self):
        valid = {"message_id": 1, "quote": self.date_quote}
        invalid = [None, {}, "not-an-array", [valid] * 5,
                   [{"message_id": 99, "quote": self.date_quote}],
                   [{"message_id": True, "quote": self.date_quote}],
                   [{"message_id": 1, "quote": "我们9月18号见Ada。"}],
                   [{"message_id": 1, "quote": self.date_quote, "extra": True}],
                   [valid, {"message_id": 1, "quote": "not in this source message"}]]
        for supporting in invalid:
            with self.subTest(supporting=supporting):
                entity = self.event()
                entity["supporting_evidence"] = supporting
                with self.assertRaises(ValueError):
                    self.validate(entity)
        entity = self.event()
        entity["supporting_evidence"] = [valid] * 4
        self.validate(entity)

    def test_nonuser_supporting_text_never_supplies_date_or_time(self):
        entity = self.event("2026-09-17T19:00:00+08:00")
        entity["supporting_evidence"] = [{"message_id": 2, "quote": self.snapshot["messages"][1]["content"]}]
        for role in ("assistant", "tool", "system"):
            with self.subTest(role=role):
                self.snapshot["messages"][1]["role"] = role
                with self.assertRaises(ValueError):
                    self.validate(entity)

    def test_synthetic_user_handoff_is_not_supporting_evidence(self):
        from kg.provenance import COMPACTION_PREFIX
        self.snapshot["messages"][0]["content"] = COMPACTION_PREFIX + "\n" + self.date_quote
        entity = self.event()
        with self.assertRaises(ValueError):
            self.validate(entity)

    def test_supporting_quotes_cannot_replace_missing_primary_evidence(self):
        entity = self.event()
        entity.pop("evidence")
        with self.assertRaises(ValueError):
            self.validate(entity)

    def test_yearless_day_precision_payload_is_stable_on_revalidation(self):
        entity = self.event("2026-09-17")
        entity["evidence"] = entity["supporting_evidence"][0]
        entity["supporting_evidence"] = []
        first = self.validate(entity)
        props = first["entities"][0]["props"]
        self.assertEqual(props["hermes_timePrecision"], "day")
        self.assertIs(props["hermes_timeYearInferred"], True)
        second = runner._validate_extraction(json.dumps(first, ensure_ascii=False), self.snapshot, self.config)
        self.assertEqual(second, first)

    def test_existing_absolute_day_precision_payload_keeps_replay_shape(self):
        self.snapshot["messages"][0]["content"] = "我们2026年9月17日见Ada。"
        entity = self.event("2026-09-17")
        entity["evidence"] = {"message_id": 1, "quote": self.snapshot["messages"][0]["content"]}
        entity.pop("supporting_evidence")
        entity["props"]["hermes_timePrecision"] = "day"
        before = self.payload(entity)
        result = runner._validate_extraction(json.dumps(before, ensure_ascii=False), self.snapshot, self.config)
        self.assertEqual(result, before)
        self.assertNotIn("hermes_timeYearInferred", result["entities"][0]["props"])


if __name__ == "__main__":
    unittest.main()
