"""Small durable console settings/job ledger; never a second memory store."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,kind TEXT NOT NULL,status TEXT NOT NULL,
    created_at REAL NOT NULL,started_at REAL,finished_at REAL,result_json TEXT);
"""


class ConsoleState:
    def __init__(self, home):
        self.path = Path(home) / "kg-sync" / "console.db"

    @contextmanager
    def connect(self,readonly=False):
        if not readonly:
            from .maintenance import assert_not_restoring
            assert_not_restoring(self.path.parent.parent)
            self.path.parent.mkdir(parents=True, exist_ok=True)
        conn=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=15) if readonly else sqlite3.connect(self.path,timeout=15)
        try:
            conn.row_factory = sqlite3.Row
            if readonly:
                conn.execute('PRAGMA query_only=ON')
            else:
                conn.executescript(SCHEMA_SQL)
                # Preserve existing/restored ledgers while adding real running
                # timestamps. Read-only status never performs a migration.
                if 'started_at' not in {row[1] for row in conn.execute('PRAGMA table_info(jobs)')}:
                    conn.execute('ALTER TABLE jobs ADD COLUMN started_at REAL')
            yield conn
            if not readonly: conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get(self, key, default=None):
        if not self.path.exists():
            return default
        with self.connect(readonly=True) as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.connect() as conn:
            conn.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key,json.dumps(value)))

    def create_job(self, kind):
        job_id = uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute("INSERT INTO jobs(id,kind,status,created_at) VALUES(?,?,?,?)", (job_id,kind,"queued",time.time()))
        return job_id

    def finish(self, job_id, status, result=None):
        with self.connect() as conn:
            conn.execute("UPDATE jobs SET status=?,finished_at=?,result_json=? WHERE id=?", (status,time.time(),json.dumps(result or {},ensure_ascii=False,default=str),job_id))

    def start(self, job_id):
        with self.connect() as conn:
            conn.execute("UPDATE jobs SET status='running',started_at=? WHERE id=? AND status='queued'", (time.time(),job_id))

    def recover(self):
        with self.connect() as conn:
            conn.execute("UPDATE jobs SET status='interrupted',finished_at=? WHERE status IN ('running','queued')", (time.time(),))

    def jobs(self, limit=30):
        if not self.path.exists():
            return []
        with self.connect(readonly=True) as conn:
            # Early restores created a paused settings-only database. Reading
            # status must work before the first writable job/schema upgrade.
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone():
                return []
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row),"result":json.loads(row["result_json"] or "{}")} for row in rows]
