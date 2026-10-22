"""First-install privacy boundaries survive setup failures and late transcripts."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kg.sync_state import SyncState
from kg.console_cli import doctor, setup


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)
        with sqlite3.connect(self.home/'state.db') as db:
            db.executescript("CREATE TABLE sessions(id TEXT PRIMARY KEY,source TEXT,started_at REAL); CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,timestamp REAL);")
            db.execute("INSERT INTO sessions VALUES('fixture','desktop',100)")
            db.executemany("INSERT INTO messages VALUES(?,'fixture','user','Existing historical text',100)",[(i,) for i in range(1,7)])

    def test_doctor_reports_successful_health_probe(self):
        with patch('kg.store.KGStore'), patch('kg.health.check_health',return_value={'connected':True}), patch('kg.console_cli._core',return_value='/core'):
            result=doctor(self.home)
        self.assertTrue(result['database']['connected'])
        self.assertTrue(result['ok'])

    def test_failed_setup_keeps_history_boundary_and_does_not_activate(self):
        with patch('kg.store.KGStore',side_effect=ConnectionError('fixture offline')):
            with self.assertRaises(ConnectionError): setup(self.home)
        marker=json.loads((self.home/'kg-console.json').read_text())
        self.assertFalse(marker['ready'])
        self.assertTrue(marker['history_seeded'])
        self.assertFalse(json.loads((self.home/'kg.json').read_text())['enabled'])
        self.assertEqual(SyncState(self.home).discover(),[])
        self.assertEqual(SyncState(self.home).status()['committed_sessions'],0)
        with patch('kg.store.KGStore',side_effect=ConnectionError('fixture offline')):
            with self.assertRaises(ConnectionError): setup(self.home)
        self.assertEqual(json.loads((self.home/'kg-console.json').read_text())['capture_started_at'],marker['capture_started_at'])

    def test_selected_seeded_history_can_backfill_without_resetting_real_receipts(self):
        state=SyncState(self.home)
        self.assertEqual(state.seed_existing_history(),1)
        self.assertEqual(state.discover(),[])
        state.request_backfill(['fixture'])
        snapshot=state.discover()[0]
        self.assertEqual(snapshot['after_message_id'],0)
        with self.assertRaisesRegex(ValueError,'extraction history'):
            state.request_backfill(['fixture'])

    def test_capture_cutoff_keeps_messages_written_after_activation(self):
        with sqlite3.connect(self.home/'state.db') as db:
            db.execute("INSERT INTO messages VALUES(7,'fixture','user','New intended memory',300)")
        state=SyncState(self.home)
        state.seed_existing_history(cutoff=200)
        self.assertEqual(state.discover()[0]['messages'][0]['content'],'New intended memory')

    def test_host_provenance_metadata_is_preserved_and_changes_invalidate_snapshot(self):
        with sqlite3.connect(self.home/'state.db') as db:
            db.execute('ALTER TABLE messages ADD COLUMN display_kind TEXT')
            db.execute("UPDATE messages SET display_kind='hidden' WHERE id=1")
        state=SyncState(self.home)
        snapshot=state.discover()[0]
        from kg.provenance import groundable_user_messages
        self.assertEqual(len(groundable_user_messages(snapshot)),5)
        with sqlite3.connect(self.home/'state.db') as db:
            db.execute("UPDATE messages SET display_kind='hidden' WHERE id=2")
        from kg.session_source import SourceChangedError
        with self.assertRaises(SourceChangedError): state.source.validate_snapshot(snapshot)
