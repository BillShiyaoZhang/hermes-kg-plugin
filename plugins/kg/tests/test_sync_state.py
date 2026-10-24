"""Behavior tests using isolated SQLite fixtures; no production KG or LLM calls."""
from __future__ import annotations
import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

INSTALL=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(INSTALL/"plugins"))
from kg.session_source import SessionSource,SourceChangedError
from kg.sync_state import SyncState,SyncBusyError
from kg.legacy_source import LegacyJSONLSource


def load_cli(name,filename):
    spec=importlib.util.spec_from_file_location(name,INSTALL/"skills/kg-memory-sync/scripts"/filename)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SyncBehaviorTests(unittest.TestCase):
    def setUp(self):
        from kg.ontology_editing import effective_schema
        ontology = patch('kg.extraction.read_current_ontology', side_effect=lambda config: effective_schema(config, {'version': 0, 'patch': {}}))
        ontology.start()
        self.addCleanup(ontology.stop)
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)
        self.db=sqlite3.connect(self.home/"state.db")
        self.addCleanup(self.db.close)
        self.db.executescript("""
            CREATE TABLE sessions(id TEXT PRIMARY KEY,source TEXT,started_at REAL);
            CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT,role TEXT,content TEXT,timestamp REAL,active INTEGER DEFAULT 1,_compressed_summary INTEGER DEFAULT 0,compacted INTEGER DEFAULT 0);
        """)
        self.state=SyncState(self.home)
        self.graph_receipts={}
        self.graph_writes=0

    def session(self,sid="human",source="desktop",count=5):
        self.db.execute("INSERT INTO sessions VALUES(?,?,?)",(sid,source,100.0))
        for index in range(count):
            self.add(sid,"synthetic message "+str(index))
        self.db.commit()

    def add(self,sid,content,role="user",active=1,summary=0,compacted=0):
        self.db.execute("INSERT INTO messages(session_id,role,content,timestamp,active,_compressed_summary,compacted) VALUES(?,?,?,?,?,?,?)",(sid,role,content,100.0,active,summary,compacted))
        self.db.commit()

    def payload(self,snapshot):
        return {"snapshot_id":snapshot["snapshot_id"],"session_id":snapshot["session_id"],"entities":[{"type":"Concept","id":"fixture-fact","name":"Synthetic Fact","props":{"description":"Synthetic test data"},"evidence":{"message_id":snapshot["messages"][0]["id"],"quote":snapshot["messages"][0]["content"]}}],"relations":[]}

    def writer(self,entities,relations,source_session=None,checkpoint=None):
        key=checkpoint["snapshot_id"]
        if key not in self.graph_receipts:
            self.graph_writes+=1
            self.graph_receipts[key]={"ok":True,"receipt_id":key,"extraction_hash":checkpoint["extraction_hash"],"entities_written":len(entities),"relations_written":len(relations)}
        return self.graph_receipts[key]

    def test_preview_status_are_read_only_and_ignore_legacy_processed(self):
        self.session()
        (self.home/"kg-sync").mkdir()
        legacy=self.home/"kg-sync/processed-sessions.json"
        content='{"sessions":{"human":{"status":"analyzed","processedAt":"2099-01-01T00:00:00Z"}}}'
        legacy.write_text(content)
        self.assertEqual(self.state.status()["committed_sessions"],0)
        self.assertEqual(self.state.preview()[0]["session_id"],"human")
        self.assertFalse(self.state.path.exists())
        self.assertEqual(self.state.discover()[0]["after_message_id"],0)
        self.assertEqual(legacy.read_text(),content)

    def test_cron_queue_and_compacted_filter(self):
        self.session("cron_x","cron")
        self.session("human","feishu",0)
        for _ in range(5):
            self.add("human","original retained",compacted=1)
        self.add("human","rewound should be absent",active=0)
        self.add("human","compression summary should be absent",summary=1)
        self.assertFalse(self.state.enqueue("cron_x"))
        self.assertTrue(self.state.enqueue("human"))
        snapshots=self.state.discover()
        self.assertEqual([s["session_id"] for s in snapshots],["human"])
        self.assertEqual(len(snapshots[0]["messages"]),5)

    def test_frozen_snapshot_and_late_append(self):
        self.session()
        before=self.state.discover()[0]
        self.add("human","late fact")
        self.assertEqual(self.state.get_snapshot(session_id="human")["messages"],before["messages"])
        self.state.stage_extraction(before["snapshot_id"],self.payload(before))
        self.state.commit(before["snapshot_id"],self.writer)
        after=self.state.discover()[0]
        self.assertEqual(len(after["messages"]),1)
        self.assertEqual(after["after_message_id"],before["last_message_id"])
        self.assertEqual(after["messages"][0]["content"],"late fact")

    def test_failed_writer_has_no_checkpoint_and_replays(self):
        self.session()
        snapshot=self.state.discover()[0]
        self.state.stage_extraction(snapshot["snapshot_id"],self.payload(snapshot))
        with self.assertRaisesRegex(RuntimeError,"offline"):
            self.state.commit(snapshot["snapshot_id"],lambda *a,**kw:(_ for _ in ()).throw(RuntimeError("offline")))
        self.assertEqual(self.state.status()["committed_sessions"],0)
        pending=self.state.discover()[0]
        self.assertEqual(pending["snapshot_id"],snapshot["snapshot_id"])
        self.assertTrue(pending["has_extraction"])
        self.state.commit(snapshot["snapshot_id"],self.writer)
        self.assertEqual(self.state.discover(),[])

    def test_no_receipt_cannot_complete(self):
        self.session()
        snapshot=self.state.discover()[0]
        self.state.stage_extraction(snapshot["snapshot_id"],self.payload(snapshot))
        with self.assertRaisesRegex(ValueError,"receipt"):
            self.state.commit(snapshot["snapshot_id"],lambda *a,**kw:{"ok":True})
        self.assertEqual(self.state.status()["committed_sessions"],0)

    def test_no_extraction_cannot_complete(self):
        self.session()
        snapshot=self.state.discover()[0]
        with self.assertRaisesRegex(ValueError,"durable extraction"):
            self.state.commit(snapshot["snapshot_id"],self.writer)
        self.assertEqual(self.graph_writes,0)

    def test_replay_does_not_call_writer_after_local_commit(self):
        self.session()
        snapshot=self.state.discover()[0]
        self.state.stage_extraction(snapshot["snapshot_id"],self.payload(snapshot))
        receipt=self.state.commit(snapshot["snapshot_id"],self.writer)
        second=self.state.commit(snapshot["snapshot_id"],lambda *a,**kw:self.fail("Writer called after receipt committed"))
        self.assertEqual(receipt,second)
        self.assertEqual(self.graph_writes,1)

    def test_graph_commit_then_process_failure_replays_same_receipt(self):
        self.session()
        snapshot=self.state.discover()[0]
        self.state.stage_extraction(snapshot["snapshot_id"],self.payload(snapshot))
        def uncertain(*args,**kwargs):
            self.writer(*args,**kwargs)
            raise RuntimeError("response lost after graph commit")
        with self.assertRaises(RuntimeError):
            self.state.commit(snapshot["snapshot_id"],uncertain)
        self.state.commit(snapshot["snapshot_id"],self.writer)
        self.assertEqual(self.graph_writes,1)
        self.assertEqual(self.state.status()["committed_sessions"],1)

    def test_extraction_snapshot_binding_and_immutability(self):
        self.session()
        snapshot=self.state.discover()[0]
        payload=self.payload(snapshot)
        bad={**payload,"session_id":"someone-else"}
        with self.assertRaisesRegex(ValueError,"session_id"):
            self.state.stage_extraction(snapshot["snapshot_id"],bad)
        self.state.stage_extraction(snapshot["snapshot_id"],payload)
        with self.assertRaisesRegex(ValueError,"immutable"):
            self.state.stage_extraction(snapshot["snapshot_id"],{**payload,"entities":[{**payload["entities"][0],"name":"Changed fact"}]})

    def test_bad_schema_does_not_poison_staged_payload(self):
        self.session()
        snapshot=self.state.discover()[0]
        payload=self.payload(snapshot)
        payload["entities"][0]["type"]="garbage"
        with self.assertRaises(ValueError):
            self.state.stage_extraction(snapshot["snapshot_id"],payload)
        self.assertFalse(self.state.get_snapshot(snapshot_id=snapshot["snapshot_id"])["has_extraction"])
        self.state.stage_extraction(snapshot["snapshot_id"],self.payload(snapshot))

    def test_get_extraction_is_read_only_and_returns_exact_payload(self):
        with self.assertRaises(ValueError):
            self.state.get_extraction("missing")
        self.assertFalse(self.state.path.exists())
        self.session()
        snapshot=self.state.discover()[0]
        with self.assertRaises(ValueError):
            self.state.get_extraction(snapshot["snapshot_id"])
        payload=self.payload(snapshot)
        self.state.stage_extraction(snapshot["snapshot_id"],payload)
        self.assertEqual(self.state.get_extraction(snapshot["snapshot_id"]),payload)
        returned=self.state.get_extraction(snapshot["snapshot_id"])
        returned["summary"]="local caller mutation"
        self.assertEqual(self.state.get_extraction(snapshot["snapshot_id"]),payload)

    def test_concurrent_discovery_preserves_one_snapshot(self):
        self.session()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:SyncState(self.home).discover(),range(8)))
        self.assertEqual(len({r[0]["snapshot_id"] for r in results}),1)
        self.assertEqual(self.state.status()["snapshot_counts"],{"pending":1})

    def test_live_lease_blocks_second_writer(self):
        self.session()
        snapshot=self.state.discover()[0]
        self.state.stage_extraction(snapshot["snapshot_id"],self.payload(snapshot))
        def first(*args,**kwargs):
            with self.assertRaises(SyncBusyError):
                self.state.stage_extraction(snapshot["snapshot_id"],self.payload(snapshot))
            with self.assertRaises(SyncBusyError):
                SyncState(self.home).commit(snapshot["snapshot_id"],self.writer)
            return self.writer(*args,**kwargs)
        self.state.commit(snapshot["snapshot_id"],first)

    def test_rewind_or_edit_is_detected_before_graph_write(self):
        self.session()
        snapshot=self.state.discover()[0]
        self.state.stage_extraction(snapshot["snapshot_id"],self.payload(snapshot))
        self.db.execute("UPDATE messages SET active=0 WHERE id=?",(snapshot["messages"][0]["id"],))
        self.db.commit()
        with self.assertRaises(SourceChangedError):
            self.state.commit(snapshot["snapshot_id"],self.writer)
        self.assertEqual(self.graph_writes,0)
        self.assertEqual(self.state.status()["committed_sessions"],0)
        self.assertEqual(self.state.status()["pending"][0]["status"],"source_changed")
        self.db.execute("UPDATE messages SET active=1,content='edited fact' WHERE id=?",(snapshot["messages"][0]["id"],))
        self.db.commit()
        with self.assertRaises(SourceChangedError):
            self.state.commit(snapshot["snapshot_id"],self.writer)

    def test_bounded_snapshots_do_not_truncate_messages(self):
        self.session(count=0)
        text="x"*150000
        self.add("human",text)
        self.add("human","next message")
        first=SessionSource(self.home).read_snapshot("human",max_chars=1000)
        self.assertEqual(first["messages"][0]["content"],text)
        second=SessionSource(self.home).read_snapshot("human",first["last_message_id"])
        self.assertEqual(second["messages"][0]["content"],"next message")

    def test_json_user_facts_are_preserved_and_multimodal_tools_excluded(self):
        self.session(count=0)
        structured='{"project":"synthetic","preference":"quiet"}'
        self.add("human",structured)
        self.add("human",json.dumps([{"type":"text","text":"visible fact"},{"type":"tool_use","name":"irrelevant","input":{"secret":"not in extracted text"}}]))
        snapshot=SessionSource(self.home).read_snapshot("human")
        self.assertEqual(snapshot["messages"][0]["content"],structured)
        self.assertEqual(snapshot["messages"][1]["content"],"visible fact")

    def test_cli_no_entities_requires_snapshot_and_save_is_not_ack(self):
        self.session()
        snapshot=self.state.discover()[0]
        cli=load_cli("kg_sync_cli_test","sync.py")
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["--home",str(self.home),"--save-sync"]),1)
            self.assertEqual(cli.main(["--home",str(self.home),"--write-llm-output","NO_ENTITIES","--stage-only"]),1)
            self.assertEqual(cli.main(["--home",str(self.home),"--write-llm-output","NO_ENTITIES","--snapshot",snapshot["snapshot_id"],"--stage-only"]),0)
        receipt=self.state.commit(snapshot["snapshot_id"],self.writer)
        self.assertEqual(receipt["entities_written"],0)
        self.assertEqual(set(self.state.get_extraction(snapshot["snapshot_id"])),{"session_id","snapshot_id","entities","relations"})

    def test_analyzer_uses_frozen_snapshot_and_configured_owner(self):
        self.session()
        snapshot=self.state.discover()[0]
        analyzer=load_cli("kg_analyzer_test","analyze_session.py")
        stream=io.StringIO()
        with contextlib.redirect_stdout(stream):
            rc=analyzer.main(["human","--home",str(self.home)])
        self.assertEqual(rc,0)
        self.assertIn(snapshot["snapshot_id"],stream.getvalue())
        self.assertIn("urn:hermes:kg:person-self",stream.getvalue())

    def test_analyzer_default_projects_human_evidence_but_json_stays_raw(self):
        from kg.provenance import COMPACTION_PREFIX
        self.session(count=0)
        self.add("human",COMPACTION_PREFIX+" synthetic summary content")
        self.add("human","actual user preference")
        self.add("human","assistant suggestion must not be user evidence",role="assistant")
        self.add("human","[IMPORTANT: Background process fixture finished] synthetic tool report")
        self.add("human","another actual user statement")
        snapshot=self.state.discover()[0]
        analyzer=load_cli("kg_analyzer_projection_test","analyze_session.py")
        normal=io.StringIO()
        with contextlib.redirect_stdout(normal):
            self.assertEqual(analyzer.main(["human","--home",str(self.home)]),0)
        self.assertIn("actual user preference",normal.getvalue())
        self.assertNotIn("synthetic summary content",normal.getvalue())
        self.assertNotIn("assistant suggestion must not",normal.getvalue())
        self.assertNotIn("synthetic tool report",normal.getvalue())
        self.assertIn("16",normal.getvalue())
        self.assertIn("32",normal.getvalue())
        self.assertIn("evidence",normal.getvalue())
        raw=io.StringIO()
        with contextlib.redirect_stdout(raw):
            self.assertEqual(analyzer.main(["human","--home",str(self.home),"--json"]),0)
        self.assertEqual(json.loads(raw.getvalue())["messages"],snapshot["messages"])
        self.assertEqual(self.state.get_snapshot(snapshot_id=snapshot["snapshot_id"])["messages"],snapshot["messages"])

    def legacy_file(self,sid="legacy",count=5,metadata=None):
        (self.home/"sessions").mkdir(exist_ok=True)
        path=self.home/"sessions"/(sid+".jsonl")
        objects=([metadata] if metadata else [])+[{"role":"user","content":"historical synthetic "+str(i),"timestamp":"2026-05-18T12:00:00+08:00"} for i in range(count)]
        path.write_text("\n".join(json.dumps(obj) for obj in objects)+"\n",encoding="utf-8")
        return path

    def test_explicit_legacy_import_retains_unknown_source_and_receipts(self):
        path=self.legacy_file()
        self.assertEqual(self.state.preview(limit=100),[])
        self.assertFalse(self.state.path.exists())
        inspected=LegacyJSONLSource(self.home,path).metadata()
        snapshot=self.state.import_legacy(path,inspected["file_sha256"])
        self.assertEqual(snapshot["source"],"legacy-jsonl")
        self.assertEqual(snapshot["source_platform"],"unknown")
        self.assertEqual(self.db.execute("SELECT count(*) FROM sessions").fetchone()[0],0)
        self.assertEqual(self.state.discover()[0]["snapshot_id"],snapshot["snapshot_id"])
        self.assertEqual(self.state.import_legacy(path)["snapshot_id"],snapshot["snapshot_id"])
        self.state.stage_extraction(snapshot["snapshot_id"],self.payload(snapshot))
        with self.assertRaises(RuntimeError):
            self.state.commit(snapshot["snapshot_id"],lambda *a,**kw:(_ for _ in ()).throw(RuntimeError("offline")))
        self.assertEqual(self.state.status()["committed_sessions"],0)
        self.state.commit(snapshot["snapshot_id"],self.writer)
        self.assertEqual(self.state.import_legacy(path)["status"],"already_committed")
        self.assertEqual(self.state.discover(),[])

    def test_legacy_scope_cron_and_sqlite_duplicates_rejected(self):
        outside=self.home/"outside.jsonl"
        outside.write_text('{}')
        with self.assertRaisesRegex(ValueError,"sessions directory"):
            self.state.import_legacy(outside)
        automated=self.legacy_file("automated",metadata={"role":"session_meta","platform":"cron"})
        with self.assertRaisesRegex(ValueError,"automated"):
            self.state.import_legacy(automated)
        wrong_id=self.legacy_file("mismatched",metadata={"role":"session_meta","session_id":"other-session"})
        with self.assertRaisesRegex(ValueError,"identity"):
            self.state.import_legacy(wrong_id)
        existing=self.legacy_file("human")
        self.session("human")
        with self.assertRaisesRegex(ValueError,"already exists"):
            self.state.import_legacy(existing)
        self.assertFalse(self.state.path.exists())

    def test_legacy_platform_metadata_and_hash_are_validated(self):
        path=self.legacy_file(metadata={"role":"session_meta","platform":"feishu"})
        with self.assertRaises(SourceChangedError):
            self.state.import_legacy(path,"incorrect-hash")
        snapshot=self.state.import_legacy(path)
        self.assertEqual(snapshot["source_platform"],"feishu")
        self.state.stage_extraction(snapshot["snapshot_id"],self.payload(snapshot))
        path.write_text(path.read_text()+json.dumps({"role":"user","content":"a changed historical file"})+'\n')
        with self.assertRaises(SourceChangedError):
            self.state.commit(snapshot["snapshot_id"],self.writer)
        self.assertEqual(self.graph_writes,0)
        self.assertEqual(self.state.status()["pending"][0]["status"],"source_changed")

    def test_legacy_more_than_one_batch_advances_line_cursor(self):
        path=self.legacy_file(count=85,metadata={"role":"session_meta","platform":"feishu"})
        first=self.state.import_legacy(path)
        self.assertEqual(len(first["messages"]),80)
        self.state.stage_extraction(first["snapshot_id"],self.payload(first))
        self.state.commit(first["snapshot_id"],self.writer)
        second=self.state.discover()[0]
        self.assertEqual(second["after_message_id"],first["last_message_id"])
        self.assertEqual(len(second["messages"]),5)
        self.assertTrue(all(msg["id"]>first["last_message_id"] for msg in second["messages"]))

    def test_invalid_legacy_record_is_not_partially_imported(self):
        path=self.legacy_file()
        path.write_text(path.read_text()+'not-json\n')
        with self.assertRaisesRegex(ValueError,"Malformed legacy"):
            self.state.import_legacy(path)
        self.assertFalse(self.state.path.exists())


if __name__=="__main__":
    unittest.main()
