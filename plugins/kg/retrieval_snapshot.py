"""Explicit, read-only exports for private retrieval research.

Nothing is exported on import. The caller selects a profile and output file;
credentials, transcripts, candidates and other namespaces are never exported.
Two identical reads are required so an experiment does not silently mix writes.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .schema import runtime_entity_labels, schema_for_store


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def snapshot_fingerprint(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _active(alias):
    return (f"{alias}.uri STARTS WITH $prefix "
            f"AND any(label IN labels({alias}) WHERE label IN $labels) "
            f"AND NOT {alias}:KGQuarantined AND NOT {alias}:KGLegacyAlias "
            f"AND NOT {alias}:KGLegacyUnresolved "
            f"AND coalesce({alias}.hermes_enabled,true) "
            f"AND coalesce({alias}.hermes_status,'') <> 'disabled'")


def _read_once(store, max_entities, max_relations):
    schema = schema_for_store(store)
    params = {"prefix": store.config.urn_prefix,
              "labels": list(runtime_entity_labels(schema).values()),
              "node_limit": max_entities + 1, "edge_limit": max_relations + 1}
    entities = store.run_read(f"""/* retrieval_research_snapshot_nodes */
        MATCH (n) WHERE {_active('n')}
        RETURN properties(n) AS properties,labels(n) AS labels
        ORDER BY n.uri LIMIT $node_limit""", params)
    if len(entities) > max_entities:
        raise ValueError("Snapshot entity budget exceeded; no truncated export was produced")
    relations = store.run_read(f"""/* retrieval_research_snapshot_relations */
        MATCH (a)-[r]->(b) WHERE {_active('a')} AND {_active('b')}
        RETURN a.uri AS source_uri,b.uri AS target_uri,type(r) AS type,properties(r) AS properties
        ORDER BY a.uri,type(r),b.uri LIMIT $edge_limit""", params)
    if len(relations) > max_relations:
        raise ValueError("Snapshot relation budget exceeded; no truncated export was produced")
    # Sort physical parallel relationships by their full content too. elementId
    # must not become a persistent research identity across backup/restore.
    relations = [{"from": row["source_uri"], "to": row["target_uri"],
                  "type": row["type"], "properties": row["properties"]} for row in relations]
    relations.sort(key=canonical_json)
    return {"namespace": store.config.urn_prefix, "schema": schema,
            "entities": entities, "relations": relations}


def export_snapshot(store, *, max_entities=100000, max_relations=500000):
    for name, value in (("max_entities", max_entities), ("max_relations", max_relations)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    previous = _read_once(store, max_entities, max_relations)
    for _ in range(2):
        current = _read_once(store, max_entities, max_relations)
        fingerprint = snapshot_fingerprint(current)
        if fingerprint == snapshot_fingerprint(previous):
            return {"format": "kg-retrieval-snapshot-v1", **current,
                    "snapshot_fingerprint": fingerprint,
                    "provenance": {"source": "explicit_local_profile", "synthetic": False,
                                   "scope": "owned_active", "complete": True,
                                   "stable_repeated_read": True, "human_reviewed": False}}
        previous = current
    raise RuntimeError("Facts changed during export; retry when the profile is quiet")


def write_private_json(path, value):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(canonical_json(value))
            stream.write("\n")
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return path
