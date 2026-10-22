# hermes-kg-plugin

## Memory Console Alpha

This repository now also includes a self-hosted web workbench: hybrid local
semantic search, source evidence, revision history, protected corrections,
archive/restore, review batches and portable backups. The existing Hermes plugin
and tool names remain compatible.

```bash
docker compose up --build -d
```

Open [the local development workbench](http://127.0.0.1:8765/#token=local-development-only).
It uses isolated Docker volumes and clearly labeled fictional examples, never
your personal Hermes profile. See [Alpha setup, development and pilot guide](docs/alpha-development.md)
for real-profile installation, Windows/macOS/Linux operation, models, recovery
and verification. Docker ports are bound to loopback.
The [implementation verification record](docs/alpha-validation.md) lists measured
checks and the remaining release gates.

---

A Neo4j-backed knowledge-graph memory provider for [Hermes Agent](https://hermes-agent.nousresearch.com), plus the two skills and the scheduled sync runner that feed it.

Hermes exposes a `MemoryProvider` interface and an ecosystem of external memory providers. This repository implements a local knowledge-graph provider: it stores people, dated events, preferences, decisions, locations and concepts as typed graph entities, and exposes them to the agent through three tools — `kg_search`, `kg_write` and a read-only `kg_status`.

> This is a **user plugin**, not part of the upstream `NousResearch/hermes-agent` repository. It installs into a Hermes profile home and is never touched by `hermes-agent` updates.

---

## What it does

| Piece | Role |
|---|---|
| `plugins/kg/` | The memory provider itself: config loader, schema/validation, Neo4j store, query layer, health probe, session source, sync state, evidence grounding, provenance filter. |
| `skills/kg-memory/` | The agent-facing skill: how to read and write memories, how to interpret results, what the ontology and storage contract are. |
| `skills/kg-memory-sync/` | The extraction skill: how a frozen session snapshot becomes a validated, atomically committed graph batch. |
| `scripts/kg_sync_runner.py` | The cron entry point. Freezes snapshots, asks the configured auxiliary model for a structured extraction, validates it, and commits it with a durable receipt. |
| `kg-sync/` | Small read-only CLI helpers (`check_kg.py`, `verify_counts.py`) and the retired manual-marking stubs. |

### Memory model

Six business types — `Person`, `Event`, `Preference`, `Decision`, `Location`, `Concept` — each as a `KGEntity` plus exactly one typed label, with canonical URIs of the form `urn:hermes:kg:<type>-<id>`.

Design rules that the implementation enforces rather than assumes:

- **A relation never creates a missing endpoint.** Both endpoints must already exist as typed entities, or be created in the same transaction. A missing endpoint rolls the whole batch back instead of leaving an empty shell.
- **Every automatic extraction carries evidence.** Each entity and relation must quote the user's own message (`evidence: {message_id, quote}`), and the quote is verified against the frozen snapshot. A quote proves the text existed, not that the model understood it — semantic review is still a human decision.
- **Synthetic text is never evidence.** Compaction summaries, background-review prompts, automatic continuation nudges and tool output are filtered out of both the model input and the evidence lookup, by the same projection, so a filtered message can never be quoted.
- **One transaction, one receipt.** Nodes, edges and the sync receipt commit together. The same snapshot replayed with the same payload is idempotent; replayed with a different payload it fails.
- **Connection state is not memory.** `kg_status` performs read-only queries and reports connectivity, searchable entity counts and past commit receipts separately. It never writes a test event to answer a health question.

---

## Layout

The repository mirrors a Hermes profile home, because the code resolves its own installation paths relative to it:

```
plugins/kg/            →  $HERMES_HOME/plugins/kg/
skills/kg-memory/      →  $HERMES_HOME/skills/kg-memory/
skills/kg-memory-sync/ →  $HERMES_HOME/skills/kg-memory-sync/
scripts/               →  $HERMES_HOME/scripts/
kg-sync/               →  $HERMES_HOME/kg-sync/
```

`kg.json` (connection settings) and `kg-sync/sync-state-v2.db` (sync progress) live in `$HERMES_HOME` and are **not** in this repository. Nothing here contains a credential.

---

## Install

Requirements: Hermes Agent (core), a reachable Neo4j instance, and Python 3.11+.

```bash
git clone https://github.com/BillShiyaoZhang/hermes-kg-plugin.git
python scripts/install.py --home "$HERMES_HOME"        # copy into the profile
python scripts/install.py --home "$HERMES_HOME" --dry-run
```

The installer copies the plugin, both skills and the runner into the target profile, installs the locked Python dependencies into `plugins/kg/_vendor/`, and never overwrites an existing `kg.json`. It prints exactly what it changed.

Then create `$HERMES_HOME/kg.json`:

```json
{
  "enabled": true,
  "bolt_url": "bolt://127.0.0.1:7687",
  "user": "neo4j",
  "password": "<your password>",
  "database": "neo4j",
  "urn_prefix": "urn:hermes:kg:",
  "user_person_urn": "urn:hermes:kg:person-bill",
  "timezone": "Asia/Shanghai"
}
```

`HERMES_KG_*` (and the compatible legacy `NEO4J_*`) environment variables override any field. Credentials stay out of commands and out of model-visible output.

Finally, activate the provider in `$HERMES_HOME/config.yaml`:

```yaml
memory:
  provider: kg
```

and restart the gateway.

### Dependencies

Locked and hash-pinned in `plugins/kg/requirements.lock`; installed into the plugin's own `_vendor/` directory so the provider does not depend on what happens to be in the core virtualenv:

```bash
uv pip install --python <hermes-python> --target plugins/kg/_vendor \
  --require-hashes -r plugins/kg/requirements.lock
```

---

## Scheduled extraction

`scripts/kg_sync_runner.py` is the cron entry point. It reads the authoritative transcript from `$HERMES_HOME/state.db`, freezes an immutable snapshot, and asks the model route configured under `auxiliary.kg_sync` for a structured extraction (at most 16 entities and 32 relations per snapshot). The extraction is validated against the snapshot, staged durably, and only then committed to the graph with its receipt.

```bash
python scripts/kg_sync_runner.py --dry-run --max-sessions 20   # read-only, no model call
python scripts/kg_sync_runner.py --verify-only                 # read-only counters
python scripts/kg_sync_runner.py                              # real run
```

The runner never advances its cursor on a failed write, and a snapshot with no extractable facts still has to earn a receipt before it counts as done.

---

## Tests

The suite uses `unittest`; database and real-model cases are optional and enabled by environment variables. The Hermes core source tree must be available for provider contracts and synthetic-text compatibility checks.

```bash
git clone --depth 1 https://github.com/NousResearch/hermes-agent.git hermes-agent
python scripts/run_tests.py
```

`scripts/run_tests.py` locates a core checkout (via `HERMES_AGENT_HOME`, `./hermes-agent`, or a sibling install) and passes its path to the suite without changing it. See the Alpha guide for Docker integration tests.

### Prompt-template compatibility

The Alpha recognizes both the historical memory-review template and the two-store template introduced in `f02be54d73`. The provenance test remains a drift detector against the selected core checkout. A future unmatched upstream template must be reviewed before expanding the supported version set.

See [known limitations](docs/known-issues.md) and the Alpha guide for compatibility and release checks.

---

## Documentation

| Path | Contents |
|---|---|
| `plugins/kg/README.md` | Operational detail: module map, daily checks, dependency restore, data and evidence conventions. |
| `skills/kg-memory/SKILL.md` | The agent-facing contract for reading and writing memories. |
| `skills/kg-memory/references/entity-schema.md` | The storage contract: labels, properties, relations, visibility, quarantine, dates. |
| `skills/kg-memory/ontology/core.ttl` | RDF ontology, parsed by RDFLib and validated by `skills/kg-memory/scripts/validate.py`. |
| `skills/kg-memory-sync/SKILL.md` | The extraction contract: snapshot analysis, evidence rules, staging and replay. |
| `docs/known-issues.md` | Open problems, including the prompt-template drift above. |
| `docs/catalog-entry.yaml` | A ready-to-submit plugin-catalog entry for the upstream `hermes-agent` catalog. |

---

## License

MIT. See [LICENSE](LICENSE).
