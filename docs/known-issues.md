# Known issues

## 1. Prompt-template drift (original incident resolved in Alpha)

**Test:** `plugins/kg/tests/test_provenance.py::ProvenanceTests::test_review_prefixes_match_installed_core_templates`

**Status:** The Alpha now recognizes both historical and two-store review
openings. This incident is retained as context for the compatibility test.
The supported core revision and latest-upstream check are recorded in CI.

**What happened.** The Hermes core changed the wording of its background
memory-review prompt in commit `f02be54d73` (2026-09-19, *"fix(review): route
memory facts to USER.md vs MEMORY.md and forbid dual-store writes"*):

| | before `f02be54d73` | after `f02be54d73` |
|---|---|---|
| `_MEMORY_REVIEW_PROMPT` | `Review the conversation above and consider saving to memory if appropriate.\n\nFocus on:\n1. Has the user revealed things about themselves …` | `Review the conversation above and consider saving to memory if appropriate.\n\nMemory has TWO distinct stores — pick the right one for each fact: …` |
| `_SKILL_REVIEW_PROMPT` | unchanged | unchanged |

The original plugin only carried the pre-change text in
`MEMORY_REVIEW_PREFIX`. The test compares the plugin's prefix against the
core's current template and fails — it is a drift detector doing its job, not
a broken test.

**Impact.** Before the Alpha fix, the core's new background memory-review prompt was not
recognised as synthetic. It is a user-role row in the transcript, so it
survives the provenance filter and reaches the extraction model as if it were
something the user said. It can also be quoted as evidence, because the
evidence validator uses the same projection.

The skill-review prompt is unaffected — `_SKILL_REVIEW_PROMPT` did not change,
and `SKILL_REVIEW_PREFIX` still matches.

**Fix.** Update `MEMORY_REVIEW_PREFIX` in `plugins/kg/provenance.py` to the
current core template's opening, keeping the same "long, exact opening"
rule the other prefixes follow: a prefix has to be long enough that an
ordinary request like *"请回顾对话整理记忆"* or *"Review the conversation above
and consider saving to memory if appropriate."* is still retained as human
text. A one-line prefix would swallow real user requests, which is why the
plugin matches on the long template openings rather than on the first
sentence.

The core can change again, so the test is the thing that has to keep passing
against `main`, not against a pinned core revision.

## 2. Hermes adapter boundary

`plugins/kg/provider.py` imports `MemoryProvider`, `RecallStatus` and
`is_trivial_prompt` from `agent.memory_provider` in the Hermes core. The
provider adapter therefore needs a Hermes installation, and host-contract tests
need a core checkout (see the README's Tests section). The package itself,
configuration, schema, storage, console and backups import without Hermes;
`kg.register()` loads the host adapter lazily. Core installation discovery is
independent of the memory profile, and neutral `KG_*` configuration is supported.

The [local Hermes/migration verification](hermes-local-integration.md) records
native Windows execution. The subsequent [real-model report](real-model-validation.md)
records external extraction, real E5 and actual Hermes tool-chain results,
including the remaining semantic and CLI bootstrap limitations.

## Alpha product boundaries

- Core schema and quote validation do not prove semantic truth. Negation,
  hypothetical statements and semantic duplicates still require evaluation.
- The console is single-profile and loopback-only; remote use requires SSH
  forwarding. No public multi-user account service is included.
- Archive is reversible exclusion from recall, not erasure of the Hermes source.
- Model download/index availability is reported explicitly; lexical fallback is
  supported. Explicit smart query interpretation requires an installed Hermes
  model route; the isolated Docker demo has no personal model credentials.
- Native macOS/Windows checks are configured in CI. Local verification includes
  native Windows Hermes and Linux Neo4j containers; the ten-user pilot is still required.
