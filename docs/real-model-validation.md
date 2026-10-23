# Real model and local Hermes validation

Verified on 2026-10-23 using Windows, Neo4j 5.26 Community, an independent
Python 3.11 console environment, and the installed Hermes Python 3.14.7
runtime. The external model was `MiniMax-M3.1-Flash` through Hermes' configured
`minimax-cn` route. Local embeddings used `intfloat/multilingual-e5-small`.

External extraction and agent acceptance used synthetic conversations and
synthetic memory. Personal historical transcripts were not sent for these
evaluations. Credentials, machine paths, runtime configuration and raw private
logs are excluded from this report and from Git. The earlier isolated migration
stage is recorded in [local integration history](hermes-local-integration.md).

## Results and limits

| Check | Observed result |
| --- | --- |
| Final Python regression suite | 283 passed; zero failures and zero skips |
| Actual Hermes AIAgent and MemoryManager | Real model selected and executed `kg_status` and `kg_search`; connected, correct synthetic memory returned in hybrid mode |
| Existing Hermes cron entry | Actual managed-interpreter bootstrap and installed runner completed read-only verification; zero model calls or graph writes |
| Canonical Hermes CLI with a fresh profile | Not accepted: dependency bootstrap did not reach conversation startup within the bounded observation window |
| External extraction structure/provenance | 16/16 cases valid; 16 model calls, zero correction retries in the final run |
| Required extracted facts | 8 true positives, zero false negatives |
| Strict extraction fixture precision/recall | 88.9% precision; 100% recall; one extra Location entity counted as a false positive |
| Extraction exclusion cases | All eight returned empty entity and relation arrays |
| Default runner commit and replay | One real model call committed five entities, five relationships and one complete extraction receipt; replay made zero model calls and zero graph writes |
| Real E5 retrieval | 47/48 positive queries hit within the fixture's declared result budget; MRR 0.9375 |
| Archived/disabled retrieval exclusions | Zero forbidden results in the 50-query E5 evaluation |

These results establish the tested local Hermes integration and the measured
synthetic workloads. They do not establish compatibility with every agent,
performance on arbitrary personal memory, or commercial release readiness.

The final wheel was rebuilt and inspected after removing two historical personal
audit/research documents from shipped skill resources. Generic synthetic
identities replaced personal examples. Packaging now rejects profile state,
credentials, caches, private exports and dated audit reports; three release
resource tests are included in the 283-test total.

## Real agent tool chain

A separate temporary profile and disposable database contained only a synthetic
user and two private preferences. A separate authenticated console loaded the
cached E5 model; automatic extraction and its scheduler were disabled.

The actual Hermes `AIAgent` and `MemoryManager`, running under Python 3.14.7,
completed a real external-model conversation in 7.41 seconds with a maximum of
three iterations and a 90-second conversation budget. The model executed:

1. `kg_status` with an empty argument object: the provider reported
   `connected=true`.
2. `kg_search` with `question="What style do I want for weekly updates?"` and
   `limit=1`: the provider reached the console's real E5 search and returned the
   Chinese synthetic preference named `周报表达偏好`.

The authentic search result reported `mode=hybrid`, `model_ready=true`, 384
dimensions and no warnings. The final answer reproduced the synthetic
preference for concise bullet points. No `kg_write` call occurred. The temporary
console and database were removed after acceptance; production services were
excluded from their cleanup.

The canonical CLI attempt is a separate, unsuccessful check. Hermes began
building a dependency environment for the fresh profile and produced no
conversation output for more than four minutes. Its conversation budget had
not started. Only that isolated process tree was terminated. The successful
agent-engine run used the same installed Hermes runtime and real tool loop,
with dependency activation performed before entering the engine; it does not
convert the CLI attempt into a pass.

## External extraction quality

The reusable [synthetic fixture](../plugins/kg/tests/fixtures/extraction_quality.json)
and [evaluation runner](../plugins/kg/tests/evaluate_extraction.py) exercise
Chinese and English preferences and decisions, date-only and relative-date
events, and a later clock-time correction. Every accepted output passes the
same schema, exact-quote, timestamp and relationship validation as normal
extraction. The final fixture uses SHA-256 snapshot identifiers in the source
reader's format.

All eight required facts were recovered. All eight negative cases returned
empty memory: a denied fictional event, a hypothetical scenario, anonymous
third-party survey answers, synthetic credentials, prompt injection, a
one-off operation request, an approval lacking its prior context, and quoted
diagnostic text. A separate positive case also verified that an assistant's
invented preference was excluded while the user's correction was retained.

The strict score remains **8 TP / 0 FN / 1 FP**, or **88.9% precision and 100%
recall**. The extra entity was a Location named `图书馆`, taken from the user's
synthetic library-volunteering statement. It has literal source evidence and
is plausible context, but was outside that case's declared allowed entities;
it remains a false positive in the reported score. Three separately declared
supporting Concept entities were allowed context and were not required facts.
The scorer checks types, required content and event times; it is not a general
semantic-entailment evaluator.

Earlier real-model trials exposed fictional-event retention and third-party
preferences/decisions that could be implicitly attributed to the user. The
resulting fixes strengthen the extraction instructions, reject explicit
fictional/hypothetical flags, and reject automatic preference/decision owner
fields or ownership relationships naming another person. Bounded correction
also repeats the exact authoritative identifiers. Exact quotes alone still do
not prove arbitrary semantic truth or ownership; the synthetic evaluation and
deterministic guards address the observed cases without claiming completeness.

## Real extraction commit and replay

A further isolated acceptance test exercised the default runner from its
authoritative SQLite transcript through `SyncState`, the actual Hermes model
bridge, validation and the real graph writer. State, model and writer were not
replaced with injected test implementations. The transcript contained six
synthetic user messages and twelve messages in total; no personal messages
were sent.

The first run made one external-model call and committed one extraction batch:
five entities and five relationships. Exactly one extraction receipt was
present and marked complete, with its extraction hash matching the staged
payload. The durable checkpoint advanced to message ID 12 and the session
queue was empty.

An immediate second run returned `no_changes`: zero model calls, zero graph
writes and no new receipt. Entity, receipt and revision counts remained
unchanged. The temporary synthetic namespace was cleaned up after these
checks. This verifies the observed commit/checkpoint/replay path alongside the
separate extraction-quality evaluation above.

## Real local E5 retrieval

The ONNX model used the immutable revision
`ccc66d3bcd826f577e26b9a4072cc5fe3a7ad6a3`, CPU execution and 384-dimensional
normalized vectors. Mixed-language text was split into chunks of at most 512
model tokens, preserving its final text. The evaluation indexed 26 synthetic
entities with real vectors and no pending embeddings.

The [50-query authored corpus](../plugins/kg/tests/fixtures/search_50.json)
contains 48 positive queries. Real hybrid retrieval hit 47 of them within the
declared result budget, giving **97.9% hit rate** and **MRR 0.9375**. The two
exclusion cases returned no archived/disabled records. Measured search latency
on this run was 63 ms at the median and 219 ms at the 95th percentile; these are
observations from one local machine, not service guarantees.

The missed positive query asks why product investment should not immediately
expand. The expected budget decision did not rank sufficiently high. A
follow-up diagnostic also ranked it below the result budget for an equivalent
English question. This is an inference/ranking limitation that remains open;
the evaluation did not replace real embeddings with deterministic vectors or
silently count lexical fallback as real-model success.

## Runtime separation and reproduction

Local serving, diagnostics and maintenance no longer eagerly import the Hermes
model runtime. When a standalone console requests an auxiliary model call, it
uses the host's Python through a stdin/JSON adapter. `KG_HERMES_PYTHON` can select
the installed interpreter; ordinary installed virtual environments are also
discovered. A managed runtime activates its installation owner's dependencies,
then restores the selected profile before reading model configuration or
credentials. Normalized final assistant content and basic completion metadata
cross the process boundary. Request text and credentials are absent from command-line
arguments, and host errors are redacted and time-bounded.

Use an isolated profile with the intended Hermes model route and a disposable
database for reproduction. The extraction evaluator opens no graph:

```text
python scripts/run_tests.py -v
python plugins/kg/tests/evaluate_extraction.py --home <isolated-profile> --output reports/extraction-quality.json
python plugins/kg/tests/evaluate_search.py --seed --real-model --cache-dir <prepared-model-cache>
```

The search evaluator requires explicit `KG_EVAL_NEO4J_*` connection settings;
seeding uses a private synthetic namespace. Model downloads require the separate
`--allow-download` flag. Raw local evidence remains outside the distributable
source. A broader user pilot, additional host/agent contracts, native platform
checks and the unresolved ranking case remain necessary release work.
