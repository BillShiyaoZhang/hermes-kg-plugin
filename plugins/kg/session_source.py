"""Read stable, bounded snapshots from Hermes' authoritative SQLite transcript.

This module never opens SessionDB's writable/repairing API and never reads a
request dump, cron transcript, or legacy JSONL implicitly.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class SourceChangedError(RuntimeError):
    """An immutable extraction input was rewound, deleted, or edited."""


def resolve_home(home=None) -> Path:
    # Shared configuration module owns all platform and environment decisions.
    from .config import resolve_home as shared_resolve_home
    return shared_resolve_home(home)


def _version(session_id: str, started_at) -> str:
    return hashlib.sha256(json.dumps([session_id, started_at], separators=(",", ":")).encode()).hexdigest()


def _text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        # SQLite content may hold multimodal JSON rather than a plain string.
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            return content
        block_types={"text","input_text","output_text","image_url","input_image","image","tool_use","tool_result","toolCall"}
        is_block=isinstance(parsed,dict) and parsed.get("type") in block_types
        is_blocks=isinstance(parsed,list) and any(isinstance(block,dict) and block.get("type") in block_types for block in parsed)
        if not is_block and not is_blocks:
            # A user can communicate facts as ordinary JSON. Only decode a
            # recognized multimodal block envelope, never discard a JSON form.
            return content
        content = parsed
    if isinstance(content, dict):
        return str(content.get("text", "")) if content.get("type") in ("text", "input_text", "output_text") else ""
    if isinstance(content, list):
        return "\n".join(filter(None, (_text(block) for block in content)))
    return ""


class SessionSource:
    def __init__(self, home=None):
        self.home = resolve_home(home)
        self.path = self.home / "state.db"

    @contextmanager
    def connect(self):
        if not self.path.is_file():
            raise FileNotFoundError(f"Hermes session database not found: {self.path}")
        conn = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")  # All reads in one consistent source snapshot.
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _filter(conn) -> str:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
        terms = ["role IN ('user','assistant')", "content IS NOT NULL", "content != ''"]
        if "active" in cols:
            terms.append("COALESCE(active,1)=1")
        if "_compressed_summary" in cols:
            terms.append("COALESCE(_compressed_summary,0)=0")
        return " AND ".join(terms)

    @staticmethod
    def _message_columns(conn):
        available={r[1] for r in conn.execute('PRAGMA table_info(messages)')}
        optional=[name for name in ('active','_compressed_summary','_micro_compact_marker','display_kind') if name in available]
        return ','.join(['id','role','content','timestamp',*optional])

    @staticmethod
    def allowed(session: dict, exclude_ids=()) -> bool:
        sid = session["id"]
        source = (session.get("source") or "").lower()
        # Never ingest the memory extractor, even if its source metadata is lost.
        return source not in {"cron", "kg", "kg-sync", "memory-sync", "background_review"} and not sid.startswith(("cron_", "kg-sync-")) and sid not in set(exclude_ids)

    def list_sessions(self, min_messages=5, exclude_ids=()) -> list[dict]:
        exclude = set(exclude_ids)
        for key in ("HERMES_SESSION_ID", "HERMES_CURRENT_SESSION_ID"):
            if os.environ.get(key):
                exclude.add(os.environ[key])
        with self.connect() as conn:
            query = f"""SELECT s.id,s.source,s.started_at,count(m.id) AS message_count,
                       max(m.id) AS last_message_id
                FROM sessions s JOIN messages m ON m.session_id=s.id
                WHERE {self._filter(conn)}
                GROUP BY s.id HAVING count(m.id)>=?
                ORDER BY s.started_at,s.id"""
            rows = [dict(r) for r in conn.execute(query, (min_messages,))]
        return [{**r, "source_version": _version(r["id"], r["started_at"])} for r in rows if self.allowed(r, exclude)]

    def metadata(self, session_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT id,source,started_at FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown session: {session_id}")
        result = dict(row)
        if not self.allowed(result):
            raise ValueError("Automated memory/cron sessions are excluded from KG extraction")
        result["source_version"] = _version(result["id"], result["started_at"])
        return result

    def read_snapshot(self, session_id: str, after_message_id=0, max_messages=80, max_chars=100000) -> dict | None:
        if max_messages < 1 or max_chars < 1:
            raise ValueError("Snapshot limits must be positive")
        with self.connect() as conn:
            row = conn.execute("SELECT id,source,started_at FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise ValueError(f"Unknown session: {session_id}")
            meta = dict(row)
            if not self.allowed(meta):
                raise ValueError("Automated memory/cron sessions are excluded from KG extraction")
            rows = conn.execute(f"SELECT {self._message_columns(conn)} FROM messages WHERE session_id=? AND id>? AND {self._filter(conn)} ORDER BY id", (session_id, after_message_id))
            messages, chars, last_id = [], 0, after_message_id
            for row in rows:
                content = _text(row["content"])
                # Only textual conversation is input. Tool payloads and binaries
                # cannot become memory merely through stringification.
                if not content:
                    last_id = row["id"]
                    continue
                if messages and (len(messages) >= max_messages or chars + len(content) > max_chars):
                    break
                messages.append({**dict(row),"content":content})
                last_id = row["id"]
                chars += len(content)
            if last_id == after_message_id:
                return None
        data = {"session_id": session_id, "source": meta["source"], "source_version": _version(session_id, meta["started_at"]), "after_message_id": after_message_id, "last_message_id": last_id, "messages": messages}
        data["snapshot_id"] = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        return data

    def validate_snapshot(self, snapshot: dict) -> None:
        """Fail before graph writes if input changed; later appended IDs are fine."""
        with self.connect() as conn:
            meta=conn.execute("SELECT id,source,started_at FROM sessions WHERE id=?",(snapshot["session_id"],)).fetchone()
            if meta is None or not self.allowed(dict(meta)) or _version(meta["id"],meta["started_at"])!=snapshot["source_version"]:
                raise SourceChangedError("Source session was removed, replaced, or became an excluded automated session")
            rows=conn.execute(f"SELECT {self._message_columns(conn)} FROM messages WHERE session_id=? AND id>? AND id<=? AND {self._filter(conn)} ORDER BY id",(snapshot["session_id"],snapshot["after_message_id"],snapshot["last_message_id"]))
            actual=[]
            for row in rows:
                content=_text(row["content"])
                if content:
                    current={**dict(row),'content':content}
                    frozen=next((m for m in snapshot['messages'] if m['id']==row['id']),{})
                    from .provenance import user_message_view
                    now_view=user_message_view(current)
                    old_view=user_message_view(frozen)
                    if (now_view is None)!=(old_view is None):
                        raise SourceChangedError('Source provenance metadata changed')
                    # Older snapshots did not include optional host metadata.
                    # Compare their original shape after checking the boundary.
                    actual.append({key:current.get(key) for key in frozen})
        if actual!=snapshot["messages"]:
            raise SourceChangedError("Frozen messages were edited, rewound, or removed; the extraction needs review and must not advance its checkpoint")
