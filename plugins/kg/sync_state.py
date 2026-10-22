"""Transactional extraction queue, immutable snapshots and durable write receipts.

The v2 state is SQLite, independent of untrustworthy legacy processedAt JSON.
Only a successful graph receipt can advance a transcript message checkpoint.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

from .session_source import SessionSource, SourceChangedError, resolve_home


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class SyncBusyError(RuntimeError):
    pass


class SyncState:
    def __init__(self, home=None):
        self.home = resolve_home(home)
        self.path = self.home / "kg-sync" / "sync-state-v2.db"
        self.source = SessionSource(self.home)

    @contextmanager
    def transaction(self, readonly=False):
        if readonly:
            conn = sqlite3.connect(self.path.as_uri()+"?mode=ro",uri=True,timeout=30)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        if readonly:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            try:
                yield conn
            finally:
                conn.close()
            return
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=FULL")
        # SQLite owns interprocess locking and atomic commit. No stale lock file
        # deletion or partially replaced JSON can lose another runner's work.
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS queue(session_id TEXT PRIMARY KEY,requested_at REAL NOT NULL,reason TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS checkpoints(session_id TEXT NOT NULL,source_version TEXT NOT NULL,last_message_id INTEGER NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(session_id,source_version));
            CREATE TABLE IF NOT EXISTS snapshots(snapshot_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,source_version TEXT NOT NULL,after_message_id INTEGER NOT NULL,last_message_id INTEGER NOT NULL,snapshot_json TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',payload_json TEXT,extraction_hash TEXT,receipt_json TEXT,error TEXT,attempts INTEGER NOT NULL DEFAULT 0,lease_token TEXT,lease_until REAL,created_at REAL NOT NULL,updated_at REAL NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_snapshot ON snapshots(session_id,source_version) WHERE status!='committed';
            CREATE TABLE IF NOT EXISTS legacy_sources(session_id TEXT PRIMARY KEY,source_version TEXT NOT NULL,metadata_json TEXT NOT NULL,registered_at REAL NOT NULL);
        """)
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def enqueue(self, session_id: str, reason="conversation_turn") -> bool:
        # Resolve and reject automation at the authoritative source, not from
        # caller claims. No graph write or LLM call happens here.
        try:
            self.source.metadata(session_id)
        except ValueError:
            return False
        with self.transaction() as conn:
            conn.execute("INSERT INTO queue VALUES(?,?,?) ON CONFLICT(session_id) DO UPDATE SET requested_at=excluded.requested_at,reason=excluded.reason", (session_id,time.time(),reason))
        return True

    @staticmethod
    def _legacy_candidates(conn,exclude_ids=()):
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='legacy_sources'").fetchone():
            return []
        excluded=set(exclude_ids)
        records=[json.loads(row[0]) for row in conn.execute("SELECT metadata_json FROM legacy_sources ORDER BY registered_at")]
        return [record for record in records if record["id"] not in excluded]

    def _legacy_reader(self,metadata):
        from .legacy_source import LegacyJSONLSource
        return LegacyJSONLSource(self.home,metadata["source_path"],metadata["file_sha256"])

    def _freeze(self,conn,snapshot):
        now=time.time()
        conn.execute("INSERT INTO snapshots(snapshot_id,session_id,source_version,after_message_id,last_message_id,snapshot_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(snapshot["snapshot_id"],snapshot["session_id"],snapshot["source_version"],snapshot["after_message_id"],snapshot["last_message_id"],_json(snapshot),now,now))
        return {**snapshot,"status":"pending","has_extraction":False,"attempts":0}

    def import_legacy(self,path,expected_sha256=None,min_messages=5) -> dict:
        """Explicitly register one reviewed historical file; never write state.db."""
        from .legacy_source import LegacyJSONLSource
        source=LegacyJSONLSource(self.home,path,expected_sha256)
        meta=source.metadata()
        if meta["message_count"]<min_messages:
            raise ValueError("Legacy transcript has fewer eligible text messages than the requested threshold")
        if self.source.path.is_file():
            with self.source.connect() as conn:
                if conn.execute("SELECT 1 FROM sessions WHERE id=?",(meta["id"],)).fetchone():
                    raise ValueError("This session already exists in authoritative SQLite; legacy import would duplicate a source")
        # Subsequent read must use the exact bytes inspected above.
        source.expected_sha256=meta["file_sha256"]
        with self.transaction() as conn:
            existing=conn.execute("SELECT source_version FROM legacy_sources WHERE session_id=?",(meta["id"],)).fetchone()
            if existing and existing[0]!=meta["source_version"]:
                raise SourceChangedError("A different legacy transcript version is already registered; do not reset its cursor")
            conn.execute("INSERT OR IGNORE INTO legacy_sources VALUES(?,?,?,?)",(meta["id"],meta["source_version"],_json(meta),time.time()))
            active=conn.execute("SELECT * FROM snapshots WHERE session_id=? AND source_version=? AND status!='committed'",(meta["id"],meta["source_version"])).fetchone()
            if active:
                return self._view(active,json.loads(active["snapshot_json"]))
            cursor=conn.execute("SELECT last_message_id FROM checkpoints WHERE session_id=? AND source_version=?",(meta["id"],meta["source_version"])).fetchone()
            snapshot=source.read_snapshot(cursor[0] if cursor else 0)
            if snapshot is None:
                return {"session_id":meta["id"],"source":"legacy-jsonl","source_platform":meta["source_platform"],"file_sha256":meta["file_sha256"],"status":"already_committed"}
            return self._freeze(conn,snapshot)

    def discover(self, limit=3, min_messages=5, exclude_ids=()) -> list[dict]:
        if limit < 1:
            raise ValueError("limit must be positive")
        candidates = self.source.list_sessions(min_messages, exclude_ids)
        result = []
        with self.transaction() as conn:
            candidates.extend(self._legacy_candidates(conn,exclude_ids))
            queued = {r["session_id"]: r["requested_at"] for r in conn.execute("SELECT * FROM queue")}
            # Existing extraction/failed write always survives a later scan.
            candidates.sort(key=lambda r:(0 if r["id"] in queued else 1, queued.get(r["id"],r["started_at"]), r["id"]))
            for item in candidates:
                existing = conn.execute("SELECT * FROM snapshots WHERE session_id=? AND source_version=? AND status!='committed'", (item["id"],item["source_version"])).fetchone()
                if existing:
                    snapshot = json.loads(existing["snapshot_json"])
                    result.append(self._view(existing, snapshot))
                else:
                    row = conn.execute("SELECT last_message_id FROM checkpoints WHERE session_id=? AND source_version=?", (item["id"],item["source_version"])).fetchone()
                    after = row[0] if row else 0
                    if item["last_message_id"] <= after:
                        conn.execute("DELETE FROM queue WHERE session_id=?",(item["id"],))
                        continue
                    snapshot = self._legacy_reader(item).read_snapshot(after) if item["source"]=="legacy-jsonl" else self.source.read_snapshot(item["id"],after)
                    if snapshot is None:
                        continue
                    if snapshot["source_version"] != item["source_version"]:
                        # Source changed between metadata discovery and reading;
                        # do not apply a cursor belonging to a different history.
                        continue
                    result.append(self._freeze(conn,snapshot))
                if len(result) >= limit:
                    break
        return result

    @staticmethod
    def _view(row,snapshot):
        return {**snapshot,"status":row["status"],"has_extraction":row["payload_json"] is not None,"attempts":row["attempts"],"error":row["error"]}

    def get_snapshot(self, snapshot_id=None, session_id=None) -> dict:
        if not snapshot_id and not session_id:
            raise ValueError("A snapshot_id or session_id is required")
        if not self.path.is_file():
            raise ValueError("No durable snapshots exist; run --list-updated first")
        with self.transaction(readonly=True) as conn:
            if snapshot_id:
                row=conn.execute("SELECT * FROM snapshots WHERE snapshot_id=?",(snapshot_id,)).fetchone()
            else:
                rows=conn.execute("SELECT * FROM snapshots WHERE session_id=? AND status!='committed' ORDER BY created_at DESC",(session_id,)).fetchall()
                if len(rows)>1:
                    raise ValueError("Multiple source histories are pending; specify the exact snapshot_id")
                row=rows[0] if rows else None
            if row is None:
                raise ValueError("No matching durable snapshot; run --list-updated first")
            if session_id and row["session_id"] != session_id:
                raise ValueError("Snapshot does not belong to the requested session")
            return self._view(row,json.loads(row["snapshot_json"]))

    def get_extraction(self,snapshot_id: str) -> dict:
        """Return the exact durable payload for read-only validation/review."""
        if not self.path.is_file():
            raise ValueError("No durable extraction exists for this snapshot")
        with self.transaction(readonly=True) as conn:
            row=conn.execute("SELECT payload_json FROM snapshots WHERE snapshot_id=?",(snapshot_id,)).fetchone()
            if row is None or row[0] is None:
                raise ValueError("No durable extraction exists for this snapshot")
            return json.loads(row[0])

    def stage_extraction(self, snapshot_id: str, payload: dict) -> dict:
        snapshot=self.get_snapshot(snapshot_id=snapshot_id)
        if payload.get("session_id") != snapshot["session_id"]:
            raise ValueError("Extraction session_id must match its durable snapshot")
        if payload.get("snapshot_id") != snapshot_id:
            raise ValueError("Extraction snapshot_id must match the snapshot the LLM read")
        if not isinstance(payload.get("entities"),list) or not isinstance(payload.get("relations"),list):
            raise ValueError("Extraction must include entities and relations arrays")
        if not all(isinstance(e,dict) for e in payload["entities"] + payload["relations"]):
            raise ValueError("Entities and relations must be JSON objects")
        # Reject malformed extraction before making it immutable. Store performs
        # the same validation again and verifies endpoints transactionally.
        from .config import load_config
        from .schema import prepare_entity, prepare_relation
        config=load_config(home=self.home)
        prepared=[prepare_entity(entity,config) for entity in payload["entities"]]
        if len({entity[0]["uri"] for entity in prepared})!=len(prepared):
            raise ValueError("Extraction repeats a canonical entity URI")
        for relation in payload["relations"] + [rel for _,relations in prepared for rel in relations]:
            prepare_relation(relation,config)
        encoded=_json(payload)
        digest=hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self.transaction() as conn:
            row=conn.execute("SELECT * FROM snapshots WHERE snapshot_id=?",(snapshot_id,)).fetchone()
            if row["extraction_hash"] and row["extraction_hash"] != digest:
                raise ValueError("Extraction is immutable once staged; replay the original payload to recover an uncertain write")
            updated=conn.execute("UPDATE snapshots SET payload_json=?,extraction_hash=?,status=CASE WHEN status='committed' THEN status ELSE 'extracted' END,updated_at=? WHERE snapshot_id=? AND (lease_until IS NULL OR lease_until<?)",(encoded,digest,time.time(),snapshot_id,time.time()))
            if updated.rowcount!=1:
                raise SyncBusyError("Another process holds the snapshot write lease; extraction was not changed")
        return {"snapshot_id":snapshot_id,"extraction_hash":digest}

    def commit(self, snapshot_id: str, writer=None) -> dict:
        token=uuid.uuid4().hex
        with self.transaction() as conn:
            row=conn.execute("SELECT * FROM snapshots WHERE snapshot_id=?",(snapshot_id,)).fetchone()
            if row is None or not row["payload_json"]:
                raise ValueError("No durable extraction exists for this snapshot")
            if row["status"]=='committed':
                return json.loads(row["receipt_json"])
            if row["lease_until"] and row["lease_until"]>time.time():
                raise SyncBusyError("Another process is writing this snapshot; retry after its lease expires")
            conn.execute("UPDATE snapshots SET status='writing',lease_token=?,lease_until=?,attempts=attempts+1,updated_at=? WHERE snapshot_id=?",(token,time.time()+300,time.time(),snapshot_id))
            payload=json.loads(row["payload_json"])
            checkpoint={key:row[key] for key in ("snapshot_id","source_version","after_message_id","last_message_id","extraction_hash")}
            frozen=json.loads(row["snapshot_json"])
            checkpoint["source_storage"]="legacy-jsonl" if frozen.get("source")=="legacy-jsonl" else "sqlite"
            checkpoint["source_platform"]=frozen.get("source_platform",frozen.get("source") or "unknown")
            if frozen.get("source")=="legacy-jsonl":
                checkpoint["file_sha256"]=frozen["file_sha256"]
                checkpoint["source_path"]=frozen["source_path"]
        try:
            if frozen.get("source")=="legacy-jsonl":
                self._legacy_reader(frozen).validate_snapshot(frozen)
            else:
                self.source.validate_snapshot(frozen)
            if writer is None:
                from .store import write_batch
                from .config import load_config
                writer=lambda entities,relations,**kwargs:write_batch(entities,relations,config=load_config(home=self.home),**kwargs)
            receipt=writer(payload["entities"],payload["relations"],source_session=row["session_id"],checkpoint=checkpoint)
            if not isinstance(receipt,dict) or receipt.get("ok") is not True or receipt.get("receipt_id")!=snapshot_id or receipt.get("extraction_hash")!=row["extraction_hash"]:
                raise ValueError("Graph write returned no matching durable receipt; checkpoint remains pending")
            with self.transaction() as conn:
                live=conn.execute("SELECT lease_token FROM snapshots WHERE snapshot_id=?",(snapshot_id,)).fetchone()
                if live[0]!=token:
                    raise SyncBusyError("Write lease was superseded; replay the original extraction to recover its graph receipt")
                cursor=conn.execute("SELECT last_message_id FROM checkpoints WHERE session_id=? AND source_version=?",(row["session_id"],row["source_version"])).fetchone()
                if (cursor[0] if cursor else 0)!=row["after_message_id"]:
                    raise ValueError("Checkpoint boundary changed; refusing to skip or roll back messages")
                now=time.time()
                conn.execute("INSERT INTO checkpoints VALUES(?,?,?,?) ON CONFLICT(session_id,source_version) DO UPDATE SET last_message_id=excluded.last_message_id,updated_at=excluded.updated_at",(row["session_id"],row["source_version"],row["last_message_id"],now))
                conn.execute("UPDATE snapshots SET status='committed',receipt_json=?,error=NULL,lease_token=NULL,lease_until=NULL,updated_at=? WHERE snapshot_id=?",(_json(receipt),now,snapshot_id))
                conn.execute("DELETE FROM queue WHERE session_id=?",(row["session_id"],))
            return receipt
        except BaseException as exc:
            with self.transaction() as conn:
                conn.execute("UPDATE snapshots SET status=?,error=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE snapshot_id=? AND lease_token=?",("source_changed" if isinstance(exc,SourceChangedError) else "failed",f"{type(exc).__name__}: {exc}"[:1000],time.time(),snapshot_id,token))
            raise

    def preview(self, limit=3, min_messages=5, exclude_ids=()) -> list[dict]:
        """Read-only pending metadata; does not freeze messages or create state."""
        candidates=self.source.list_sessions(min_messages,exclude_ids)
        checkpoints,active,queued={},{},{}
        if self.path.is_file():
            with self.transaction(readonly=True) as conn:
                candidates.extend(self._legacy_candidates(conn,exclude_ids))
                queued={r["session_id"]:r["requested_at"] for r in conn.execute("SELECT * FROM queue")}
                checkpoints={(r["session_id"],r["source_version"]):r["last_message_id"] for r in conn.execute("SELECT * FROM checkpoints")}
                active={(r["session_id"],r["source_version"]):dict(r) for r in conn.execute("SELECT snapshot_id,session_id,source_version,status,payload_json,attempts FROM snapshots WHERE status!='committed'")}
        candidates.sort(key=lambda r:(0 if r["id"] in queued else 1,queued.get(r["id"],r["started_at"]),r["id"]))
        result=[]
        for item in candidates:
            key=(item["id"],item["source_version"])
            after=checkpoints.get(key,0)
            if key in active or item["last_message_id"]>after:
                row=active.get(key,{})
                result.append({"session_id":item["id"],"source":item["source"],"snapshot_id":row.get("snapshot_id"),"after_message_id":after,"last_message_id":item["last_message_id"],"status":row.get("status","not_snapshotted"),"has_extraction":bool(row.get("payload_json")),"attempts":row.get("attempts",0)})
                if len(result)>=limit:
                    break
        return result

    def status(self, readonly=True) -> dict:
        if not self.path.is_file():
            return {"format":2,"source":"state.db","snapshot_counts":{},"committed_sessions":0,"queued_sessions":0,"pending":[]}
        with self.transaction(readonly=readonly) as conn:
            counts=dict(conn.execute("SELECT status,count(*) FROM snapshots GROUP BY status"))
            pending=[dict(r) for r in conn.execute("SELECT snapshot_id,session_id,status,attempts,error FROM snapshots WHERE status!='committed' ORDER BY created_at")]
            checkpoints=conn.execute("SELECT count(*) FROM checkpoints").fetchone()[0]
            queued=conn.execute("SELECT count(*) FROM queue").fetchone()[0]
        return {"format":2,"source":"state.db","snapshot_counts":counts,"committed_sessions":checkpoints,"queued_sessions":queued,"pending":pending}


def enqueue_session(session_id: str, hermes_home=None, reason="conversation_turn") -> bool:
    return SyncState(hermes_home).enqueue(session_id, reason)
