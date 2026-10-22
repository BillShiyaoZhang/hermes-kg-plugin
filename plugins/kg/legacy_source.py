"""Explicit, immutable import source for reviewed historical JSONL transcripts.

No directory scanning and no SQLite transcript insertion. A caller must register
each existing file under this profile's sessions directory. File bytes are hashed
and remain immutable for the duration of import; line numbers are its cursor.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .session_source import SourceChangedError,_text,resolve_home


class LegacyJSONLSource:
    def __init__(self, home, path, expected_sha256=None):
        self.home=resolve_home(home)
        self.root=(self.home/"sessions").resolve()
        candidate=Path(path)
        if not candidate.is_absolute():
            candidate=self.home/candidate
        self.path=candidate.resolve()
        if self.path.parent!=self.root or self.path.suffix.lower()!=".jsonl" or not self.path.is_file():
            raise ValueError("Legacy import requires an existing .jsonl file directly inside this profile's sessions directory")
        if any(marker in self.path.name for marker in (".reset.",".deleted.",".checkpoint.")):
            raise ValueError("Reset/deleted/checkpoint copies are not eligible for legacy import")
        if self.path.stem.startswith(("cron_","kg-sync-")):
            raise ValueError("Automated memory/cron transcripts cannot be imported")
        self.expected_sha256=expected_sha256

    def _read(self):
        raw=self.path.read_bytes()
        digest=hashlib.sha256(raw).hexdigest()
        if self.expected_sha256 and digest!=self.expected_sha256:
            raise SourceChangedError("Registered legacy transcript bytes changed; review the original import before continuing")
        text=raw.decode("utf-8-sig")
        messages=[]
        source_labels=set()
        total_lines=0
        for number,line in enumerate(text.splitlines(),1):
            total_lines=number
            if not line.strip():
                continue
            try:
                obj=json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Malformed legacy JSONL at line {number}; no partial import was registered") from exc
            if not isinstance(obj,dict):
                raise ValueError(f"Legacy line {number} is not a JSON object")
            role=obj.get("role")
            record_type=obj.get("type")
            # Metadata appeared in both role=session_meta and type=session_meta
            # in earlier Hermes releases. Source fields are trusted only here,
            # never interpreted from a user's content.
            metadata=role in {"session_meta","metadata","session"} or record_type in {"session_meta","metadata","session"} or ("platform" in obj and role not in {"user","assistant","tool","system"})
            if metadata:
                for key in ("id","session_id"):
                    if obj.get(key) is not None and obj[key]!=self.path.stem:
                        raise ValueError("Legacy metadata session identity disagrees with its filename")
                for key in ("source","platform"):
                    if obj.get(key) is not None:
                        if not isinstance(obj[key],str):
                            raise ValueError("Legacy source metadata must be a string")
                        source_labels.add(obj[key].strip().lower())
                continue
            if role not in {"user","assistant","tool","system"}:
                raise ValueError(f"Unsupported legacy record role at line {number}")
            if role not in {"user","assistant"} or obj.get("active",1) in (False,0) or obj.get("_compressed_summary",False):
                continue
            content=_text(obj.get("content"))
            if content:
                timestamp=obj.get("timestamp")
                if timestamp is not None and not isinstance(timestamp,(str,int,float)):
                    raise ValueError("Legacy message timestamp must be a scalar")
                messages.append({"id":number,"role":role,"content":content,"timestamp":timestamp})
        blocked={"cron","kg","kg-sync","memory-sync","background_review"}
        if any(source in blocked or source.startswith(("cron:","cron_","kg-sync")) for source in source_labels):
            raise ValueError("Source metadata identifies an automated memory/cron transcript")
        if len(source_labels)>1:
            raise ValueError("Conflicting legacy source metadata requires review")
        platform=next(iter(source_labels),"unknown") or "unknown"
        metadata={"id":self.path.stem,"source":"legacy-jsonl","source_platform":platform,"source_path":self.path.relative_to(self.home).as_posix(),"file_sha256":digest,"message_count":len(messages),"last_message_id":total_lines,"started_at":0}
        metadata["source_version"]=hashlib.sha256(("legacy-jsonl\0"+metadata["id"]+"\0"+digest).encode()).hexdigest()
        return metadata,messages

    def metadata(self):
        return self._read()[0]

    def read_snapshot(self, after_message_id=0, max_messages=80, max_chars=100000):
        if max_messages<1 or max_chars<1:
            raise ValueError("Snapshot limits must be positive")
        meta,messages=self._read()
        if meta["last_message_id"]<=after_message_id:
            return None
        selected=[]
        chars=0
        last=after_message_id
        for msg in messages:
            if msg["id"]<=after_message_id:
                continue
            if selected and (len(selected)>=max_messages or chars+len(msg["content"])>max_chars):
                break
            selected.append(msg)
            chars+=len(msg["content"])
            last=msg["id"]
        else:
            last=meta["last_message_id"]  # Validated ignored metadata/tool tail.
        snapshot={"session_id":meta["id"],"source":"legacy-jsonl","source_platform":meta["source_platform"],"source_path":meta["source_path"],"file_sha256":meta["file_sha256"],"source_version":meta["source_version"],"after_message_id":after_message_id,"last_message_id":last,"messages":selected}
        snapshot["snapshot_id"]=hashlib.sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()
        return snapshot

    def validate_snapshot(self,snapshot):
        meta,messages=self._read()
        if meta["source_version"]!=snapshot["source_version"]:
            raise SourceChangedError("Legacy source identity changed")
        selected=[m for m in messages if snapshot["after_message_id"]<m["id"]<=snapshot["last_message_id"]]
        if selected!=snapshot["messages"]:
            raise SourceChangedError("Legacy snapshot messages changed")
