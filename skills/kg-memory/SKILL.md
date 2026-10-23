---
name: kg-memory
version: 2.0.0
description: >-
  Read and explicitly write Hermes knowledge graph memory: people, dated events,
  preferences, decisions, locations and concepts. Use the shared KG plugin API
  or these command-line wrappers; respect the active profile's visibility rules.
---

# KG Memory

The independent user plugin at `$HERMES_HOME/plugins/kg/` owns the storage,
configuration and query contracts. The scripts in this skill are compatibility
entry points into that library. Current implementation details are in
`references/entity-schema.md`; older architecture/research documents are historical.

This is an independently distributed plugin. Install or update it through its
own repository/package; updating the host agent does not update this plugin.

## Active installation and configuration

Resolve the selected profile from an explicit path, `KG_HOME` or the compatible
`HERMES_HOME`. A copied plugin otherwise uses its installation root. Do not
substitute WSL `/c/Users/...` paths into native Windows Python.

Use the configured host Python or the supported standalone environment.
Scripts bootstrap the plugin's private locked dependencies in `plugins/kg/_vendor`;
there is no requirement for a separate `venvs/kg` interpreter or Miniconda.

All entry points use `kg.config.load_config()`. Configuration comes from the
active home's `kg.json`; explicit `KG_*` environment variables override it,
followed by the compatible `HERMES_KG_*` names.
Legacy `NEO4J_URL/USER/PASSWORD/DATABASE` are compatible environment names.
The loader does not silently fall back to another profile's configuration.

Fields include `bolt_url` (or `neo4j_url`), `user`, `password`, `database`,
`urn_prefix`, `user_person_urn`, `enabled`, `query_cadence`,
`connection_timeout`, `query_timeout`, `query_limit`, and `timezone`.
Keep credentials out of commands, reports and model-visible output.

## Query and write entry points

For "is KG connected?" use `kg_status` with `{}`. It performs read-only database
queries and reports the configured user URI, searchable entity count and past
nonempty commit receipts. A successful empty search is not a connectivity failure
and does not prove a user/entity is absent. Past receipts prove prior writes;
`kg_status` does not claim to test current write permissions. Do not create fake
events or memories to diagnose connectivity. If the provider tool is unavailable,
the read-only CLI fallback is `kg-sync/check_kg.py` under the active Hermes home.

`check.mode=read_only_status_probe` describes this diagnostic operation only.
Never label it as the database's "write mode: read-only". `write_capability`
separates permission to call the tool in this session from untested database write
permissions. Report the `legacy_exclusions` breakdown when explaining quarantined
counts; retained aliases and incomplete legacy records are not newly deleted data.

For a specific entity, pass its actual complete URN to `kg_search.question`, not
an invented phrase such as `owner URN person`. Complete URNs are exact matches;
legacy alias/unresolved nodes remain excluded unless an old URI has the explicit
reviewed alias mapping described below. Ordinary multiword topic input is
a literal phrase unless a supported question pattern applies.

Resolve the installed Python and script paths once; use those absolute paths.
The examples below show arguments after the Python executable:

```text
skills/kg-memory/scripts/query_natural.py "Alice是谁"
skills/kg-memory/scripts/query_natural.py "上个月我做了什么"
skills/kg-memory/scripts/query_entity.py --type Person --query "Alice"
skills/kg-memory/scripts/query_entity.py --owner urn:hermes:kg:person-legacy-owner
skills/kg-memory/scripts/query_events.py --from 2026-09-01 --to 2026-09-13
skills/kg-memory/scripts/query_events.py --all-persons --event-type meeting
skills/kg-memory/scripts/manage_entity.py --type person --id alice --name "Alice"
skills/kg-memory/scripts/manage_entity.py --type event --id meeting-example --name "Meeting" --event-time 2026-09-13T12:00:00+08:00 --agent urn:hermes:kg:person-legacy-owner
skills/kg-memory/scripts/manage_entity.py --type preference --id response-style --name "Response style" --pref-key response_style --pref-value concise
skills/kg-memory/scripts/manage_entity.py --type concept --id ontology --name "Ontology"
skills/kg-memory/scripts/manage_entity.py --type relation --from urn:hermes:kg:person-alice --to urn:hermes:kg:event-meeting-example --rel-type HERMES_participant
```

The relation endpoints must already exist as typed entities. A bad endpoint fails
the transaction and cannot create an empty shell. Preference and Decision owners
default to the configured user Person; that anchor must exist. Event participants
are explicit `--agent` or relationship records, rather than inferred from the
fact that a memory was stored.

`--prop key=value` works for every entity type. JSON booleans and numeric values
retain their types; `--shared true|false` explicitly sets visibility.
Event writes require a valid date/time, or explicit `time_precision=unknown` and
`event_status=occurred` with no start/end timestamp for an occurred event whose
date is unknown. Omitted dates alone are invalid. Coordinates must be numeric and in range.
Entity deletion and relationship deletion both use `--delete`; only perform
deletion when the user's request authorizes it. A read helper never accepts
mutating Cypher.

Batch extraction uses `kg.store.KGStore.write_batch(entities, relations,
source_session, checkpoint)`, rather than one subprocess per entity.
All nodes, edges and the durable sync receipt commit in one transaction.
Failures raise and cannot advance the sync cursor. Replaying the same checkpoint
and payload returns the existing receipt; changing its payload is rejected.
The session discovery/queue/retry workflow belongs to `kg-memory-sync`.

## Visibility, identities and query behavior

- Canonical entity URIs are `urn:hermes:kg:<type>-<id>` under the configured prefix.
  Legacy `<type>:<id>` input is normalized before writing.
- Each business node has `KGEntity` plus exactly one of Person, Event, Preference,
  Decision, Location or Concept. Shared receipt and migration metadata are not
  business memories.
- Own-namespace entities are readable. External entities require boolean
  `shared=true`; related people/locations are filtered by the same rule.
- `KGLegacyAlias` and `KGLegacyUnresolved` nodes are excluded from normal queries.
- `KGQuarantined` marks content archived or withheld after review while retaining original
  business labels, properties and relationships. All direct, related and owner
  queries exclude these nodes. `kg_status.content_quarantined.nodes` counts them
  separately from the unchanged legacy exclusion breakdown; the total
  `quarantined_nodes` includes all excluded nodes without double counting.
  Categories can include conversation logs, outdated operational status, valid
  duplicate facts, or unsupported extraction; the label alone does not mean false.
  Normal writes reject quarantined entity URIs and relationship endpoints;
  retrying extraction cannot restore them. Restore only through an explicit,
  reviewed maintenance operation, never by writing over a quarantined memory.
- A user-confirmed identity alias may retain `KGQuarantined` with boolean
  `hermes_aliasConfirmed=true` and `hermes_canonicalUri`. An exact old-URI lookup
  can resolve one hop to that canonical target only while both namespaces are
  readable and the target is not quarantined/legacy-excluded; it returns the
  target's content and `matched_alias_uri`, never the archived alias's content.
  Other quarantined URIs do not resolve. Normal writes to confirmed alias URIs
  or relation endpoints fail; use the canonical URI. These confirmation fields
  are maintenance-managed and cannot be supplied through ordinary entity props.
  Canonical `hermes_aliasNames` are searchable and returned as `alias_names`.
  Structured entity owner/event person filters resolve confirmed Person aliases
  under the same visibility rules, so their results equal the canonical person's
  results. `content_quarantined` keeps its total `nodes` and separately reports
  `confirmed_alias_nodes` and `ordinary_content_nodes`; resolving an alias never
  restores the old record.
- New entities default to boolean shared=true. Updating a private entity preserves
  its setting unless shared is explicitly supplied.
- Canonical relationships are case sensitive. Legacy aliases are accepted at the
  write boundary and normalized; see the current schema reference.
- Event date endpoints include the full local day. Internally ranges use
  start-inclusive/end-exclusive boundaries with the configured timezone.
  An event interval has `hermes_eventTime` and exclusive `hermes_eventEndTime`;
  May 23–24 with day precision means May 23 midnight through May 25 midnight.
  Date queries use interval overlap, so May 24 matches that event. Unknown-date
  events return null `time`/`end_time`, `time_precision=unknown` and their explicit
  `event_status`; they are retained in unfiltered results and excluded from every
  date filter, even if a stale legacy date field exists. Automatic extraction
  still requires quoted dates; it cannot self-declare unknown to bypass grounding.
  Invalid historical time strings cannot abort all results: dated searches skip
  them; unfiltered searches retain them with a missing parsed time.
- Query results retain `semantic_scope`, `status` and `status_note` from their
  corresponding `hermes_` properties. Respect these limits: a reading interest
  does not establish completed reading, and a historical decision is not a claim
  about the user's current choice.
- Natural queries handle common Chinese/English questions. They are a deterministic
  parser, not an LLM-to-Cypher service. For precise filtering use the structured
  entity/event APIs. Query success with no result does not prove a fact never
  existed, particularly while legacy unresolved nodes remain.

## Ontology and maintenance

`ontology/core.ttl` is parsed by RDFLib. Run:

```text
skills/kg-memory/scripts/validate.py
skills/kg-memory/scripts/validate.py --graph
skills/kg-memory/scripts/discover_gaps.py
skills/kg-memory/scripts/add_concept.py --class Project --desc "A project concept" --dry-run
```

The first validation checks RDF syntax and storage mappings; `--graph` adds
read-only checks of searchable instances and relationship endpoint types without
printing their private text. Content quarantine is excluded from these checks and
remains separately counted by `kg_status`. Ontology extensions serialize RDF safely, validate the complete
draft and atomically replace the file. Adding a new ontology class alone does not
add a seventh writable business type: update the explicit Python schema and
queries with tests, or use Concept for domain concepts.

Do not run historical bulk-deletion recipes. Existing alias/unresolved nodes need
an evidence-based migration; schema labels alone cannot recover their missing
content. Database backup/restore and production migration are separate maintenance
operations.

## Verification

The plugin includes `plugins/kg/tests/test_store_contract.py` with isolated
transaction, visibility, typing, date, CLI and real RDF parser tests. Production
read/write integration must use the normal plugin API and a dedicated test
namespace with cleanup. Never diagnose a query contract merely by counting rows
from a mocked database result.
