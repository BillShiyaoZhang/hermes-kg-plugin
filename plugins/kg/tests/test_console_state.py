"""Restored console state is usable before any background writes run."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from kg.console_state import ConsoleState
from kg.maintenance import _validated_sqlite_files


class RestoredConsoleStateTests(unittest.TestCase):
    def test_profile_without_console_backup_gets_complete_paused_state(self):
        files = _validated_sqlite_files({'graph.json': b'{"nodes":[],"relations":[]}'})
        with tempfile.TemporaryDirectory() as directory:
            state = ConsoleState(Path(directory))
            state.path.parent.mkdir()
            state.path.write_bytes(files['console.db'])
            self.assertTrue(state.get('paused'))
            self.assertEqual(state.jobs(), [])
            job = state.create_job('index')
            self.assertEqual(state.jobs()[0]['id'], job)
            self.assertTrue(state.get('paused'))

    def test_old_settings_only_restore_supports_read_only_status(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ConsoleState(Path(directory))
            state.path.parent.mkdir()
            with closing(sqlite3.connect(state.path)) as conn:
                conn.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                conn.execute("INSERT INTO settings VALUES('paused','true')")
                conn.commit()
            original = state.path.read_bytes()
            self.assertTrue(state.get('paused'))
            self.assertEqual(state.jobs(), [])
            self.assertEqual(state.path.read_bytes(), original)

    def test_existing_job_ledger_upgrades_on_write_and_records_running_phase(self):
        with tempfile.TemporaryDirectory() as directory:
            state=ConsoleState(Path(directory))
            state.path.parent.mkdir()
            with closing(sqlite3.connect(state.path)) as conn:
                conn.executescript('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);'
                                  'CREATE TABLE jobs(id TEXT PRIMARY KEY,kind TEXT NOT NULL,status TEXT NOT NULL,'
                                  'created_at REAL NOT NULL,finished_at REAL,result_json TEXT);'
                                  "INSERT INTO jobs VALUES('old','index','queued',1,NULL,NULL);")
                conn.commit()
            original=state.path.read_bytes()
            self.assertEqual(state.jobs()[0]['status'],'queued')
            self.assertEqual(state.path.read_bytes(),original)
            state.start('old')
            self.assertEqual(state.jobs()[0]['status'],'running')
            self.assertIsNotNone(state.jobs()[0]['started_at'])
            state.recover()
            self.assertEqual(state.jobs()[0]['status'],'interrupted')


if __name__ == '__main__':
    unittest.main()
