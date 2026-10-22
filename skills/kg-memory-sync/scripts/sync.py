#!/usr/bin/env python3
"""KG extraction CLI: SQLite snapshots, durable payloads and verified receipts.

--list-updated freezes a transcript snapshot. --write-llm-output saves the
extraction before writing. --replay retries the identical saved extraction.
No command can mark a session complete without a matching graph receipt.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

INSTALL_HOME = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(INSTALL_HOME / "plugins"))
from kg.sync_state import SyncState
from kg.config import resolve_home


def read_payload(value: str | None, input_file: str | None = None):
    if input_file:
        raw = Path(input_file).read_text(encoding="utf-8-sig")
    elif value and value.startswith("@"):
        raw = Path(value[1:]).read_text(encoding="utf-8-sig")
    elif value:
        raw = value
    else:
        raise ValueError("Pass JSON, @file, or --input-file")
    raw = raw.strip()
    if raw.startswith("```"):
        lines=raw.splitlines()
        if len(lines)>=3 and lines[-1].strip()=="```":
            raw="\n".join(lines[1:-1]).strip()
    if raw=="NO_ENTITIES":
        return None
    payload=json.loads(raw)
    if not isinstance(payload,dict):
        raise ValueError("Extraction must be a JSON object")
    return payload


def prepare_payload(state, payload, snapshot_id=None):
    sid=snapshot_id or (payload or {}).get("snapshot_id")
    if not sid:
        raise ValueError("Exact snapshot_id is required (JSON field or --snapshot), including for NO_ENTITIES")
    snapshot=state.get_snapshot(snapshot_id=sid)
    if payload is None:
        payload={"session_id":snapshot["session_id"],"snapshot_id":sid,"entities":[],"relations":[]}
    elif snapshot_id:
        if payload.get("snapshot_id") not in (None,snapshot_id):
            raise ValueError("JSON and --snapshot refer to different snapshots")
        payload={**payload,"snapshot_id":snapshot_id}
    return sid,payload


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list-updated",action="store_true",help="Freeze up to --limit SQLite sessions")
    group.add_argument("--preview",action="store_true",help="Read pending metadata without creating snapshots or state")
    group.add_argument("--inspect-legacy",metavar="JSONL_FILE",help="Inspect one historical transcript without registering it")
    group.add_argument("--import-legacy",metavar="JSONL_FILE",help="Explicitly register one reviewed historical transcript")
    group.add_argument("--get-messages",metavar="SESSION_ID",help="Return an existing immutable snapshot as JSON")
    group.add_argument("--write-llm-output",nargs="?",const="",metavar="JSON_OR_@FILE",help="Stage extraction, write transaction, commit receipt")
    group.add_argument("--replay",metavar="SNAPSHOT_ID",help="Retry the durable payload without another LLM call")
    group.add_argument("--save-sync",action="store_true",help="Verify status; cannot blindly complete a batch")
    group.add_argument("--status",action="store_true")
    group.add_argument("--gc",action="store_true",help="Disabled; reviewed migrations own maintenance")
    group.add_argument("--write-event",metavar="JSON",help="Retired; use kg-memory/manage_entity.py")
    group.add_argument("--write-entity",metavar="JSON",help="Retired; use kg-memory/manage_entity.py")
    parser.add_argument("--home",help="Explicit HERMES_HOME")
    parser.add_argument("--snapshot",help="Exact snapshot ID to read or bind JSON/NO_ENTITIES")
    parser.add_argument("--input-file",help="UTF-8 extraction JSON or NO_ENTITIES file")
    parser.add_argument("--limit",type=int,default=3)
    parser.add_argument("--min-messages",type=int,default=5)
    parser.add_argument("--exclude-session",action="append",default=[])
    parser.add_argument("--details",action="store_true",help="Discovery includes snapshot metadata")
    parser.add_argument("--stage-only",action="store_true",help="Save extraction for --replay without writing KG")
    parser.add_argument("--expected-sha256",help="Require this exact historical transcript file hash")
    args=parser.parse_args(argv)
    try:
        if args.write_event is not None or args.write_entity is not None:
            raise ValueError("Direct sync writes are retired. Use kg-memory/scripts/manage_entity.py; session extraction requires --write-llm-output and snapshot_id")
        if args.gc:
            raise ValueError("Automatic GC is disabled. Use a reviewed migration with backup and explicit URI mapping")
        state=SyncState(resolve_home(args.home))
        if args.inspect_legacy:
            from kg.legacy_source import LegacyJSONLSource
            result=LegacyJSONLSource(state.home,args.inspect_legacy,args.expected_sha256).metadata()
        elif args.import_legacy:
            imported=state.import_legacy(args.import_legacy,args.expected_sha256,args.min_messages)
            result={key:value for key,value in imported.items() if key!="messages"}
        elif args.preview:
            result=state.preview(args.limit,args.min_messages,args.exclude_session)
        elif args.list_updated:
            snapshots=state.discover(args.limit,args.min_messages,args.exclude_session)
            result=[{key:s[key] for key in ("session_id","snapshot_id","after_message_id","last_message_id","status","has_extraction")} for s in snapshots] if args.details else [s["session_id"] for s in snapshots]
        elif args.get_messages:
            result=state.get_snapshot(snapshot_id=args.snapshot,session_id=args.get_messages)
        elif args.write_llm_output is not None:
            payload=read_payload(args.write_llm_output,args.input_file)
            snapshot_id,payload=prepare_payload(state,payload,args.snapshot)
            result=state.stage_extraction(snapshot_id,payload)
            if not args.stage_only:
                result=state.commit(snapshot_id)
        elif args.replay:
            result=state.commit(args.replay)
        else:
            result=state.status()
            if args.save_sync and result["pending"]:
                print(json.dumps(result,ensure_ascii=False,indent=2))
                raise ValueError("Pending snapshots require extraction and graph receipts; --save-sync cannot mark them complete")
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (ValueError,OSError,RuntimeError,ImportError) as exc:
        print(f"KG sync failed: {type(exc).__name__}: {exc}",file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
