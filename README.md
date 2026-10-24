# hermes-kg-plugin

## Memory Console Alpha

This repository now also includes a self-hosted web workbench: hybrid local
semantic search, source evidence, revision history, protected corrections,
archive/restore, review batches and portable backups. The existing Hermes plugin
and tool names remain compatible.

The **知识图谱 / Knowledge Graph** workspace offers **本体 / 探索 / 审阅**
perspectives with separate, recoverable cameras, filters and selections. Small
graphs show all records; larger graphs use complete server-side type and
relationship aggregates, with member paging and incremental neighborhood
exploration. The SVG canvas supports drag/pin, multiple and box selection,
topological layouts, spotlight, minimap, history, view undo, bounded paths,
comparison, local saved views and PNG/SVG export. Definitions, actual record
collections, saved facts and candidates retain distinct identities. One shared
inspector preserves evidence, attributes and the protected review/edit flows;
pending writes and drafts prevent accidental navigation away.
The ontology perspective includes a **管理本体 / Manage ontology** directory for
creating, finding and editing custom types, properties and relations, as well as
deactivating, restoring and deleting definitions within the current namespace.
Published custom definitions participate in actual storage validation, queries
and graph exploration. Display edits use an inline preview; structural changes
show compatibility effects; deactivation and deletion require an explicit,
version- and reference-bound confirmation. Deletion is restricted to custom
definitions with no current, archived, historical, pending-review or definition
references, and never cascades into facts. Built-in identities remain protected.
Concurrent changes and connection failures preserve drafts. See the
[CRUD risk and verification record](docs/ontology-crud-validation.md) and the
earlier [ontology editing verification](docs/ontology-editing-validation.md).
The “语义与停用” view preserves semantic lookup, archive/restore and permitted
shared read-only details. Saved graph browsing is bounded and restricted to
active records in the current namespace, including both ends of each edge.

Use `?view=graph` on your workbench URL to bookmark the workspace; old
`memories`, `ontology` and `reviews` bookmarks still resolve there. Graph
browsing uses the existing authentication and makes no external model calls.
Optional external query interpretation remains an explicit search submission.
See the [complete product and UI/UX design](docs/product-ui-ux-design.md) for
personas, storyboards, use cases, requirements and the Apple HIG mapping, and the
[ontology UI verification](docs/ontology-ui-validation.md) for the initial feature.
The [whole-workbench P0 verification](docs/web-p0-validation.md) records the
subsequent search, correction, review, task, backfill, authentication and layout
checks, including fault recovery, privacy and the remaining release gates.
The [v3 verification record](docs/graph-v3-validation.md) distinguishes current
implementation checks from the historical [unified graph verification](docs/unified-graph-validation.md).
Screen-reader, additional browser-engine and physical touch-device acceptance
remain explicit requirements before release.

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

## Deploy or upgrade with one command

With Hermes already configured, a reachable Neo4j instance, Python 3.11+, Node.js
and `uv` available, run the same command for a fresh KG installation or an upgrade:

```powershell
python scripts/deploy.py --home "$env:LOCALAPPDATA\hermes"
```

On macOS/Linux, pass your Hermes profile path (usually `~/.hermes`). Run from this
checkout. A fresh installation asks for database settings and masks the password;
an upgrade preserves the existing connection, namespace, identity, token and sync
state. The command builds the UI, prepares an independent locked Python 3.11
runtime, stages the complete plugin and skills, activates the provider, starts
the local console, restarts the target Hermes gateway and verifies the actual
installed provider and authenticated service. Semantic model preparation is
included. Code changes need this deployment; Web data changes take effect through
current database reads, synchronous index refresh and Agent cache invalidation.

See the [deployment and daily-use guide](docs/deployment.md) for prerequisites,
authentication, upgrades, startup behavior, recovery and the Agent integration
diagram. `--dry-run` makes no changes. The explicit `--skip-*` options are partial
development deployments and do not claim complete readiness.

## Low-level plugin-only install

The older installer below copies the provider and helpers only. It does not
replace the complete deployment command above.

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
  "urn_prefix": "urn:my-memory:kg:",
  "user_person_urn": "urn:my-memory:kg:person-self",
  "timezone": "UTC"
}
```

Choose your namespace, user identifier and IANA timezone explicitly. Existing
profiles retain their configured URNs, including `urn:hermes:kg:` identities.
An omitted user identifier defaults to `<urn_prefix>person-self`; it never
assumes a particular person's name. Fresh console setup generates a unique
namespace and user ID, so independent profiles can safely use the same database.

`KG_HOME` selects a memory profile without an agent-specific environment variable;
`HERMES_HOME` remains supported. An explicit `--home` takes priority over both.
Connection and identity overrides use `KG_BOLT_URL`, `KG_USER`, `KG_PASSWORD`,
`KG_DATABASE`, `KG_URN_PREFIX`, `KG_USER_PERSON_URN` and `KG_TIMEZONE`.
These override the compatible `HERMES_KG_*` and legacy `NEO4J_*` names, then
`kg.json`. `KG_ENABLED`, `KG_QUERY_CADENCE`, `KG_QUERY_LIMIT`,
`KG_CONNECTION_TIMEOUT`, `KG_QUERY_TIMEOUT`, `KG_DEFAULT_SHARED` and
`KG_CONSOLE_URL` follow the same precedence. Credentials stay out of commands
and out of model-visible output. The console, schema, storage and backup modules
run without Hermes; transcript extraction and the `MemoryProvider` adapter use
Hermes contracts.
Console runtime settings also accept `KG_CONSOLE_TOKEN`, `KG_MODEL_CACHE`,
`KG_STATIC_DIR` and `KG_DEMO_MODE`, with their `HERMES_KG_*` aliases supported.
When an external scheduler already runs the sync runner, set
`KG_SYNC_SCHEDULER_ENABLED=false` for the console to avoid duplicate schedules.
Index maintenance and explicitly requested sync jobs remain available.

Indexing prioritizes this profile's own memories before shared external data.
Search applies requested namespace and entity-type filters before channel result
limits. Neo4j 5 vector search uses a bounded global ANN oversample (up to 10,000
chunks); a much larger competing profile can still reduce semantic recall.
Exact and keyword search remain available independently of that ANN limit.

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

A standalone console can use `KG_HERMES_PYTHON` to select the installed Hermes
interpreter for model calls. Requests pass through stdin to that interpreter;
the console keeps its own locked dependencies. Set `HERMES_AGENT_HOME` if the
host checkout cannot be discovered. These are deployment settings, with no
machine-specific paths embedded in the package.

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
The [native Windows integration and real-profile migration record](docs/hermes-local-integration.md)
documents the installed-Hermes checks, backup/recovery validation and remaining limits.
The [real-model validation report](docs/real-model-validation.md) records the
subsequent live cutover, actual Hermes engine/tool calls, external extraction
quality and cached E5 inference, including the remaining failures.

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
| `docs/product-ui-ux-design.md` | Product personas, scenarios, requirements, interaction design and Apple HIG references. |
| `docs/ontology-ui-validation.md` | Ontology API, interface and responsive acceptance evidence and limitations. |
| `docs/web-p0-validation.md` | Whole-workbench P0 behavior, fault recovery, privacy, accessibility evidence and remaining release gates. |
| `docs/unified-graph-validation.md` | Unified ontology, saved knowledge and review graph requirements, acceptance evidence and release gates. |
| `docs/catalog-entry.yaml` | A ready-to-submit plugin-catalog entry for the upstream `hermes-agent` catalog. |

---

## License

MIT. See [LICENSE](LICENSE).
