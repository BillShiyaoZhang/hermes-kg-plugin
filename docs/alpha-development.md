# Memory Console Alpha

This release is a self-hosted, single-profile workbench. The conversations still
happen in Hermes. The workbench searches memories, shows their sources and
revision history, and lets their owner correct, archive, restore and review them.

## Docker development

```sh
docker compose up --build -d
```

Open http://127.0.0.1:8765/#token=local-development-only. This **development-only**
configuration uses the dedicated `urn:hermes-demo:kg:` namespace, a separate
database volume and visibly synthetic examples. It never mounts your personal
Hermes profile. Neo4j Browser is at http://127.0.0.1:17474, Bolt at port 17687;
development credentials are `neo4j` / `local-development-password`.

The UI and database ports bind to loopback. `KG_CONSOLE_TOKEN` overrides the
development token. Volumes retain the database and local model cache across
restarts. `docker compose stop` stops the environment without deleting data.

Use **准备本地模型** in Settings to download the pinned multilingual E5 ONNX
model and populate the vector index. The initial download is several hundred MB.
Later inference runs on CPU locally. Search never downloads a model. While the
model or index is unavailable, the UI reports lexical search rather than claiming
semantic results. Explicit smart query interpretation uses Hermes's configured
LLM; it is off by default and is never used in per-turn automatic recall.

## Connect a real Hermes profile

Run the Python workbench on the **same host as Hermes**, including on a VPS. Do not
mount a running host SQLite transcript into Docker Desktop. Docker is optional for
Neo4j; an existing Neo4j 5.26 deployment works as well.

Use a dedicated Python 3.11 virtual environment for the workbench. The workbench
reads the core checkout via `HERMES_AGENT_HOME`; installing its dependency lock
must not replace the dependencies in your running Hermes virtual environment.

```sh
cd console
npm ci
npm run build
cd ..
python -m pip install -r requirements-console.lock
python -m pip install --no-deps -e '.[console]'
python scripts/install.py --home /absolute/path/to/hermes
hermes-kg --home /absolute/path/to/hermes setup --name 'Your name'
hermes-kg --home /absolute/path/to/hermes doctor
hermes-kg --home /absolute/path/to/hermes serve
```

Node is needed to build the UI from source, not to run prebuilt UI assets. Set the
existing `HERMES_KG_*` database variables or use an existing `kg.json`. Setup does
not overwrite that file. Configure `memory.provider: kg` in Hermes and restart the
gateway after updating plugin Python modules. `HERMES_AGENT_HOME` can explicitly
locate the core checkout. A single workbench process owns a single profile.

On Windows use the same Python commands with an absolute Windows profile path;
PowerShell environment syntax is `$env:HERMES_KG_PASSWORD = '...'`. New profiles
get their own user identity and private-by-default entities. Existing namespace,
URI, shared and quarantine settings remain unchanged.

The access token is stored in `<profile>/kg-console.token`. Paste it in the local
UI; it is kept in tab session storage. For a VPS, forward port 8765 with SSH:

```sh
ssh -L 8765:127.0.0.1:8765 user@your-host
```

Closing the browser does not stop the Python service. Automatic sync runs every
four hours while the service is running and catches up after restart. Existing
cron invocations share the same runner lock. Pause is persisted and also honored
by the cron runner. No OS service installation or public hosting is included.

## History, corrections and review

Fresh setup starts at the current transcript boundary. Choose skipped sessions
in the workbench to queue limited history backfill, or use `hermes-kg backfill`.
Already-extracted sessions cannot have their cursors silently reset.

An exact quote proves source text existed, not that the model understood it.
Manual corrections are labeled as such and remain protected. A later conflicting
automatic batch becomes a review batch; it does not overwrite the correction.
Review may edit existing cards' names/properties, but cannot change IDs or add and
remove relationships. Invalid grounding remains an error, not an approvable fact.
Source withdrawal blocks accepting the old extraction. Same-name people are not
automatically merged, and semantic ambiguity can still be missed.

Archive means “stop recalling this card”; it is reversible and keeps the source
and history. It is **not permanent erasure of the original Hermes conversation**.
Old quarantine/identity aliases are protected from ordinary restoration.

## Backup and upgrade

```sh
hermes-kg --home /profile migrate
hermes-kg --home /profile backup /safe/place/memory-backup.zip
hermes-kg --home /profile migrate --apply
```

Migration preview detects duplicate identities and never re-extracts facts. Old
typed records remain readable at revision 0; the first change records a legacy
baseline. Backups include typed graph data/revisions/receipts and the product's
SQLite state. They exclude credentials, embedding indexes and Hermes `state.db`.

Restore into an empty namespace and empty sync directory, using the same namespace
and user identity in the destination configuration. `setup --skip-user` can prepare
an empty destination without creating a user card. Restore pauses sync; reconnect
and inspect the transcript source before resuming. Rebuild search indexes afterward.

```sh
hermes-kg --home /empty-profile restore /safe/place/memory-backup.zip
```

An interrupted restore blocks ordinary writes/startup. Rerun restore with the same
backup to finish recovery. Do not remove its recovery marker manually.

## Verification

```sh
HERMES_AGENT_HOME=/path/to/hermes-agent docker compose -f compose.yaml -f compose.test.yaml --profile tools run --rm tests
```

In PowerShell set `$env:HERMES_AGENT_HOME` first. The core checkout is mounted
read-only; tests use random namespaces in the development database. The original
test runner no longer creates a source-tree symlink. The optional actual-model
checks require `KG_SEARCH_MODEL_CACHE`; model downloads are explicit.

CI checks Python on Linux/macOS/Windows, transactions on Neo4j, and the production
frontend. Supported-core checks use revision
`cdceca42e107f0e51ab9ff50e1cc881ad76b577e`; latest upstream compatibility is a separate
nonblocking signal. Local Docker verification does not substitute for native
Windows/macOS release testing.

## Pilot acceptance

Start with two technically assisted users, then about ten Hermes power users for
three weeks. Record successful real recalls, sampled correctness/usefulness,
corrections, repeat usage, installation effort, support time and paid continuation.
The 50-question synthetic retrieval fixture is a regression set, not an advertised
real-user accuracy statistic. Collect feedback voluntarily; no raw-memory telemetry
is enabled by default. Accounts, payments, other agents, teams, proactive reminders
and permanent source deletion remain outside this Alpha.
