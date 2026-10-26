# Current KG storage contract (2026-09-13)

The implementation in `plugins/kg/schema.py` is the shared runtime contract.
`ontology/core.ttl` describes its RDF mappings. All scripts use these modules.

The table below describes the compatible core, not the full active registry.
Published namespace extensions are read through `schema_for_store()` and use
`x_*` type keys with server-derived labels. New workspaces include the personal
preset; existing workspaces install it explicitly. Its source RDF, fixed public
vocabulary snapshots, generated manifest and SHACL are in `ontology/personal/`.
Standard semantic IRIs do not replace URNs or automatically become tool aliases.
Legacy Concept records are not automatically mapped to SKOS Concept.

| Entity | Labels | Display field | Required content |
|---|---|---|---|
| Person | KGEntity, Person | foaf_name | name |
| Event | KGEntity, Event | hermes_name | name, hermes_eventTime |
| Preference | KGEntity, Preference | hermes_name | name, hermes_preferenceKey, hermes_preferenceValue |
| Decision | KGEntity, Decision | hermes_name | name, hermes_decisionText |
| Location | KGEntity, Location | hermes_name | name |
| Concept | KGEntity, Concept | hermes_name | name |

Shared properties: `uri`, boolean `shared`, native datetime
`hermes_createdAt/hermes_updatedAt`, and provenance
`hermes_sourceSession/hermes_sourceSessions/hermes_extractionIds`.
Managed metadata cannot be overwritten through arbitrary properties.
Missing shared on new nodes defaults to true; ordinary updates preserve the
existing value. External nodes are readable only with boolean shared=true.

Event times are timezone-aware datetimes. Historical ISO strings are tolerated
by queries; malformed legacy times are excluded from date-filtered results.
Automatically extracted events also carry `hermes_timePrecision` (`day` or
`datetime`), returned as `time_precision`. A day-only value normalized to midnight
does not claim that the event actually occurred at midnight.
When a cited month/day omits the year, `hermes_timeYearInferred=true` records
that the year came from that citation's message timestamp in the configured
timezone. Event queries and general entity queries return this as
`time_year_inferred`, alongside `time_precision`. A null value means no
inference annotation is stored; it does not establish that the source gave
an explicit year. The current runtime year and nearest future year are never
used to fill a missing year.
Event types: travel, meeting, social, task, system, daily, general.
For the `kg_write` tool, each entity supplies `id`, lowercase `type`, `name`,
and any type-specific content inside `props`. Tool field names map to the stored
properties above:

| Type | Required tool fields | Optional tool fields |
|---|---|---|
| event | `props.event_time` (actual ISO 8601 date/time) | `props.event_type`: travel, meeting, social, task, system, daily, general |
| preference | `props.preference_key`, `props.preference_value` | `props.description`, `props.note` |
| decision | `props.decision_text` | `props.decision_context` |
| person, location, concept | no additional required props | `props.description`, `props.note`; Location also `props.latitude`, `props.longitude` |

The same runtime constants supply the tool's supported types, event categories,
required-field descriptions and validation errors. Never invent an event time or
a `test`/`connectivity_probe` category for a connection check; call read-only
`kg_status` with `{}`. Only store a real, supported memory.
Locations use numeric `geo_lat` / `geo_long`.
Description/note use `hermes_description` / `hermes_note`.
Decision context is `hermes_decisionContext`.

| Canonical edge | Direction | Accepted aliases |
|---|---|---|
| HERMES_participant | Person → Event | HERMES_PARTICIPANT, PARTICIPATED_IN, METAT |
| HERMES_hasPreference | Person → Preference | EXPRESSED_PREFERENCE |
| HERMES_hasDecision | Person → Decision | MADE_DECISION |
| HERMES_locatedAt | Person/Event → Location | HERMES_eventLocation, AT_LOCATION |
| HERMES_knows | Person → Person | KNOWS |
| LINKED_TO / CAUSED_BY / RELATED_TO | Typed entity → typed entity | same spelling |
| owl_sameAs | Typed entity → typed entity | owl:sameAs |

Case aliases are normalized before query construction. Values are parameterized;
labels and relationship types come from the validated mapping. A relation never
creates a missing endpoint: both endpoints must already exist as typed entities
or be created in the same batch. An invalid direction or missing endpoint rejects
the entire transaction. The tool advertises canonical names; the listed aliases
remain accepted by the runtime for compatibility. Writes originate in the active namespace; external
targets must be shared. Cross-namespace identity links do not grant visibility.

Canonical URI: `<configured prefix><type>-<ASCII identifier>`.
Legacy `<type>:<identifier>` URIs normalize to this format. Entity types are
checked against the URI and existing label. New writes never choose a label based
only on a malformed URI. `KGLegacyAlias` and `KGLegacyUnresolved` are migration
metadata and excluded from normal business queries.

`KGSyncReceipt(snapshot_id)` records committed batch counts, canonical IDs,
payload/extraction hashes, source session and checkpoint. It is part of the same
transaction as its nodes and edges. Same snapshot/payload replay is idempotent;
a conflicting payload fails. Explicit setup creates uniqueness constraints on
`KGEntity(uri)` and `KGSyncReceipt(snapshot_id)`.

The active user Person comes from `user_person_urn`; never use the literal
`hermes` as an owner. Preference and Decision default to this existing anchor.
Event participation must be explicitly supplied.

Run `validate.py --graph` for read-only graph contract counts. Valid RDF alone
does not prove that stored instances meet their contract. Do not use old bulk
deletion recipes to repair aliases or unresolved records.
