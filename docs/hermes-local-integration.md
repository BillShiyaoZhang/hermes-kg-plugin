# Local Hermes compatibility and migration verification

This is the historical record of the initial isolated backup/migration stage.
Subsequent real external-model extraction, real E5 retrieval and actual Hermes
agent-engine acceptance are recorded in [real model validation](real-model-validation.md).
Statements below about paused extraction, an unchanged live provider and
unverified models describe that earlier stage, rather than the final deployment.

Verified on Windows on 2026-10-23 against installed Hermes core
`0e427d788d253630d38a3a51cd76849e73cd6925`, Python 3.11.16 and a separate
Neo4j 5.26 Community instance. This record contains counts and procedures only;
personal memory, credentials, transcripts, backups and local connection settings
are excluded from Git under `reports/` and from the Docker build context.

## Backup and migration

Before any migration, a read-only transaction exported the complete source
graph: 2,205 nodes and 1,917 relationships, including historical data outside
the current KG namespace. SQLite's online backup API copied the transcript and
sync databases. Original configuration and provider files were copied locally;
59 backup files have SHA-256 checksums.

The full graph backup was restored into an additional empty Neo4j instance.
Every node's labels/properties and the multiset of attributed relationships
matched before commit. That temporary recovery instance was then removed.
The original graph, KG configuration and installed provider files were checked
again and remained identical to their backups.

The project test profile received 1,884 owned nodes and 1,782 internal
relationships, with exact property/label/relationship comparison. These include
164 active memories, 630 content-quarantined entities, 58 historical aliases,
1,028 unresolved historical nodes and four nonempty commit receipts. Thirty-nine
cross-namespace relationships remain in the full backup; unrelated historical
entities were not imported. Namespace-less empty receipts cannot prove ownership
and remain only in the full backup and copied sync ledger.

At the end of this initial stage, the copied profile retained the transcript and
sync state, with extraction paused. Original memories were neither re-extracted
nor sent to an external model during this stage. The source provider and gateway
had not yet been replaced.

## Verified behavior

- Initial Windows native Python regression: 270 tests, 268 passed and two explicitly
  skipped real-model checks; no failures. Neo4j integration tests ran alongside
  the migrated profile rather than requiring an otherwise empty database.
  The subsequent final run passed 283 tests with zero skips; see the
  [updated validation results](real-model-validation.md#results-and-limits).
- All 164 active memories were recalled by exact URI; 97 events were queryable.
- Real Neo4j reads/writes, lexical indexing, authenticated console API and the
  provider's actual loopback HTTP connection worked on the migrated copy.
- A disposable synthetic memory completed provider write, correction, archive,
  recall exclusion and restore. Its node, revisions and receipts were removed.
- The copied authoritative session database and sync progress were readable by
  the runner without model calls; normal execution respected the restored pause.
- An isolated subprocess exercised Hermes' actual user-plugin loader,
  MemoryManager, tool dispatch, recall indicator, session switch, asynchronous
  durable queue, cron write restriction and shutdown.
- The real Hermes auxiliary client and OpenAI SDK reached a synthetic loopback
  endpoint using the selected temporary profile. Audit guards rejected external
  network access, real user configuration reads and writes outside the fixture.
- Frontend unit tests: 14 passed. TypeScript/Vite production build and the native
  wheel build passed. The wheel contains its UI and no local backup/profile data;
  its doctor, bundled UI and authenticated API also passed outside the source
  directory against the copied profile.

## Fixes driven by this verification

- Include legacy-only audit labels in backups and restore ownership checks;
  preserve recognized historical relationship spellings and temporal values.
- Batch restore by label/type within the existing atomic transaction and receipt.
  The real copy then restored under the source's five-second query timeout;
  interrupted recovery resumed safely.
- Read historical relationship aliases in owner, event, location, graph expansion
  and detail queries without rewriting original audit edges.
- Initialize the complete console schema on restore, including jobs; old
  settings-only restored databases also support read-only status.
- Discover installed Hermes independently of the selected profile, including
  Windows installation paths and redirected shell environments.
- Exclude Hermes' combined background review prompt from extraction and evidence.
  The template drift test now resolves static string concatenation without
  importing host runtime state.
- Use explicit UTF-8 for profile/onboarding JSON on Windows.
- Support neutral `KG_HOME`/`KG_*` controls with legacy aliases; remove personal
  name assumptions. Fresh setup generates a private unique namespace, while
  existing configured identities/namespaces remain stable.
- Restrict installation to shipped helpers and exclude nested runtime state;
  report dependency installation failure with a nonzero exit status.
- Prioritize owned memories in bounded indexing batches. Apply namespace/type
  search scope before channel limits so shared records cannot consume a selected
  profile's ordinary keyword result budget.

## Reproduction and boundaries

Run the complete suite with a disposable database and a local Hermes checkout:

```powershell
$env:HERMES_AGENT_HOME = '<installed Hermes core>'
$env:HERMES_HOME = '<isolated test profile>'
$env:KG_TEST_BOLT_URL = '<disposable Neo4j Bolt URL>'
$env:KG_TEST_PASSWORD = '<test credential>'
$env:KG_SEARCH_TEST_URL = $env:KG_TEST_BOLT_URL
$env:KG_SEARCH_TEST_PASSWORD = $env:KG_TEST_PASSWORD
python scripts/run_tests.py -v
```

Provide dependencies through a project virtual environment or the documented
locked installation. No fixture requires a particular user's profile or data.
Database tests choose random namespaces and remove their own fixtures.

Historical boundary: live external-model extraction quality and real E5 ONNX
inference were not verified in this initial stage because the prepared local
E5 cache was absent. Its SDK/routing check used a local synthetic server and
its ANN mechanics checks used deterministic vectors. Those gaps were later
tested with real models, as detailed in [real model validation](real-model-validation.md),
including the remaining ranking limitation and unsuccessful canonical CLI cold
bootstrap check. Neither stage establishes commercial release readiness; the
user pilot and native platform checks remain release gates.

Neo4j 5 ANN retrieval has no namespace/model prefilter. Search oversamples a
bounded global candidate set (at most 10,000), applies scope and rehydrates live
visible records. Very large foreign vector populations can still reduce
approximate recall; separate databases or scope-specific indexes should be
evaluated before a shared service deployment. Date filters are validated during
final hydration, so a bounded candidate search is not an exhaustive date query;
use the structured event query for exhaustive filtered history.
