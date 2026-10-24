"""Read-only KG diagnostics: connectivity, stored data and past commit evidence."""
from __future__ import annotations

from .schema import runtime_entity_labels, schema_for_store

STATUS_SCHEMA = {
    "name": "kg_status",
    "description": (
        "Check whether KG is connected and contains data, using read-only database queries. "
        "Returns this profile's entity counts, configured user URI and past nonempty commit evidence. "
        "Use for connection/health questions instead of a made-up search phrase or writing test facts. "
        "Past receipts prove previous commits. check.mode describes ONLY this status probe; "
        "never report it as the database's write mode. Current database write permissions are not tested. "
        "Legacy exclusion counts distinguish retained aliases from unresolved old records. "
        "Content quarantine is counted separately and retains original memories for review."
    ),
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}


def check_health(config, store):
    ready = store.run_read("RETURN 1 AS ready")
    if not ready or ready[0].get("ready") != 1:
        raise RuntimeError("KG read probe did not succeed")
    counts = store.run_read("""
        MATCH (n) WHERE n.uri STARTS WITH $prefix
        RETURN count(n) AS namespace_nodes,
            sum(CASE WHEN any(label IN labels(n) WHERE label IN $entity_labels)
                AND NOT n:KGLegacyAlias AND NOT n:KGLegacyUnresolved AND NOT n:KGQuarantined THEN 1 ELSE 0 END) AS searchable_entities,
            sum(CASE WHEN n:KGLegacyAlias OR n:KGLegacyUnresolved OR n:KGQuarantined THEN 1 ELSE 0 END) AS quarantined_nodes,
            sum(CASE WHEN n:KGLegacyAlias THEN 1 ELSE 0 END) AS legacy_alias_nodes,
            sum(CASE WHEN n:KGLegacyUnresolved THEN 1 ELSE 0 END) AS legacy_unresolved_nodes,
            sum(CASE WHEN n:KGQuarantined THEN 1 ELSE 0 END) AS content_quarantined_nodes,
            sum(CASE WHEN n:KGQuarantined AND n.hermes_aliasConfirmed=true THEN 1 ELSE 0 END) AS confirmed_alias_nodes,
            sum(CASE WHEN n:KGQuarantined THEN CASE WHEN n.hermes_aliasConfirmed=true THEN 0 ELSE 1 END ELSE 0 END) AS ordinary_content_nodes,
            sum(CASE WHEN n.uri=$user AND n:Person
                AND NOT n:KGLegacyAlias AND NOT n:KGLegacyUnresolved AND NOT n:KGQuarantined THEN 1 ELSE 0 END) AS configured_user_matches
    """, {"prefix": config.urn_prefix, "user": config.user_person_urn,
          "entity_labels": list(runtime_entity_labels(schema_for_store(store)).values())})[0]
    # Empty batches have no namespace-bearing entity URIs. Do not guess their
    # ownership or include other profiles' receipts in the health summary.
    receipts = store.run_read("""
        MATCH (r:KGSyncReceipt) WHERE r.complete=true
            AND any(uri IN coalesce(r.entity_uris,[]) WHERE uri STARTS WITH $prefix)
        RETURN count(r) AS completed_nonempty_batches, max(r.committed_at) AS last_committed_at
    """, {"prefix": config.urn_prefix})[0]
    last = receipts.get("last_committed_at")
    return {
        "ok": True, "connected": True,
        "check": {"mode": "read_only_status_probe", "writes_performed": 0},
        "write_capability": {"database_write_permissions": "not_tested_by_this_check"},
        "database": config.database, "namespace": config.urn_prefix,
        "namespace_nodes": counts["namespace_nodes"],
        "searchable_entities": counts["searchable_entities"],
        "quarantined_nodes": counts["quarantined_nodes"],
        "content_quarantined": {
            "nodes": counts["content_quarantined_nodes"],
            "confirmed_alias_nodes": counts["confirmed_alias_nodes"],
            "ordinary_content_nodes": counts["ordinary_content_nodes"],
            "handling": "Original records remain archived and cannot be automatically updated. Confirmed aliases may resolve to a visible canonical entity without restoring the old record; ordinary archived content remains hidden.",
        },
        "legacy_exclusions": {
            "alias_nodes": counts["legacy_alias_nodes"],
            "unresolved_nodes": counts["legacy_unresolved_nodes"],
            "handling": "Retained for audit and excluded from normal search, not deleted.",
            "reason": "Legacy aliases and incomplete old records are excluded to avoid duplicate or unsupported memories. These counts alone do not indicate a new failure or newly lost data.",
        },
        "configured_user": {"uri": config.user_person_urn,
                            "present": counts["configured_user_matches"] == 1},
        "past_commits": {"completed_nonempty_batches": receipts["completed_nonempty_batches"],
                         "last_committed_at": last.isoformat() if hasattr(last, "isoformat") else last},
        "write_probe_performed": False,
        "interpretation": (
            "A successful read proves database connectivity. Empty search results mean no match for that query. "
            "Read-only describes this diagnostic operation ONLY, not a database setting and not a ban on kg_write. "
            "Report current write permissions as not tested, never as read-only based on this probe. "
            "Past receipts are evidence of previous committed writes, not a new write-permission test. "
            "Use the configured user URI verbatim for an exact lookup; do not save synthetic health-check facts."
        ),
    }
