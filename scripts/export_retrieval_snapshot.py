#!/usr/bin/env python3
"""Export an explicitly selected profile locally for human-reviewed experiments."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if not output.is_relative_to(root / "reports"):
        parser.error("Private snapshots must be written beneath the ignored reports/ directory")
    from kg.config import load_config
    from kg.store import KGStore
    from kg.retrieval_snapshot import export_snapshot, write_private_json
    logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
    try:
        with KGStore(load_config(home=args.home)) as store:
            snapshot = export_snapshot(store)
        write_private_json(output, snapshot)
        print(json.dumps({"ok": True, "entities": len(snapshot["entities"]),
                          "relations": len(snapshot["relations"]),
                          "snapshot_fingerprint": snapshot["snapshot_fingerprint"],
                          "human_reviewed": False, "production_writes": 0}))
        return 0
    except Exception as exc:
        # Never dump connection strings, URIs, credentials or fact content.
        print(json.dumps({"ok": False, "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
