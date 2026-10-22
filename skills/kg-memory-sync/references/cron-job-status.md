# KG scheduled runner contract

Updated 2026-09-13. The actual scheduler record is authoritative for enabled state,
schedule and delivery; this document does not claim the job is currently running.

- The fixed runner lives at `$HERMES_HOME/scripts/kg_sync_runner.py`, compatible
  with the scheduler's allowed script directory and current Hermes interpreter.
- Preview/verification uses read-only `SyncState.preview()` and `status()`.
- A real run calls `discover(limit=3, min_messages=5)` on the SQLite transcript.
- Existing durable extraction is replayed. New snapshots are analyzed using the
  Hermes configured auxiliary LLM and staged with their exact snapshot identity.
- `commit(snapshot_id)` writes the batch and receipt atomically, then advances the
  local message cursor. Partial errors never count as a completed session.
- Cron transcripts and memory automation are excluded, preventing feedback from
  the sync report into personal memory. A provider turn hook only queues input.
- A successful no-work runner can return the scheduler's quiet/skip result.
  Delivery must use committed receipt counts, not LLM estimates of success.

The old workflow's manually edited processed JSON, historical Linux helper paths,
automatic label repair and “database offline means mark processed” instructions
are obsolete. They must not be used for failure recovery.

Before resuming a repaired schedule, verify the configured interpreter/dependencies,
SQLite source, graph schema and user anchor, and complete isolated success,
no-fact, failed-write/retry and late-message tests. Restoring the provider alone
is not a reason to start ingestion while the writer or query contract is broken.
