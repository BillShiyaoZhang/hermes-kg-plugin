# Offline retrieval experiments

This package never reads a default Hermes profile or writes a business graph.
`prepare-fixture` reads only an explicitly supplied authorized snapshot. Model
preparation is offline; use the separately authorized model setup process to
download weights. Research artifacts may contain private source text and belong
in ignored, local report directories.

```powershell
python scripts/research_retrieval.py prepare-fixture --output reports/retrieval-research/fixture-v1
python scripts/research_retrieval.py evaluate --fixture-dir reports/retrieval-research/fixture-v1 --output reports/retrieval-research/e5-entity-dev.json --model e5-small --model-cache .model-cache --representation entity --mode vector --split dev --track facts
python scripts/research_retrieval.py report --baseline reports/retrieval-research/baseline.json --candidate reports/retrieval-research/candidate.json --output reports/retrieval-research/comparison.json
```

The model protocol is `prepare(allow_download=False)`, `embed_query`,
`embed_passages`, `chunk`, `fingerprint`, and a ready `model_manifest`. The CLI
uses `kg.embedding_profiles.create_embedder`. It accepts `--device cpu`, `cuda`
or `cuda:N`, and `--precision float32|float16|bfloat16`; unsupported combinations
fail instead of silently switching hardware. Default context limit is 512.

Representations come from `kg.retrieval_documents`: `values`, `entity`, and
`entity_relations`. Rules/ontology cards are a separate track. Modes are exact
cosine, an explicitly named offline BM25 baseline (Unicode words/CJK bigrams),
RRF hybrid, and hybrid with bounded directed graph expansion. This lexical
baseline is **not** a reproduction of Neo4j Lucene. ANN and installed HTTP
performance are measured by separate tools and supplied as evidence.

Passage vectors are cached in SQLite by model fingerprint, query/passage role,
and content hash. They can be reused across identical text in different runs.
Chunks/cards collapse to unique evidence units before metrics/RRF; synthetic
relation and entity cards that express the same authored fact share an explicit
gold alias. Query encoding is not cached during latency measurement. Local
exact-ranking latency is not HTTP or cold-start latency.

## Prepared data

- 200 authored synthetic fact questions: 60 dev, 140 blind. Twenty complete
  source families stay within one split. Includes 40 genuine no-answer prompts,
  20 multi-evidence questions and 20 aggregate diagnostics.
- 100 **pending** real-human slots: 40 dev, 60 blind. Without an explicit source
  there is no invented real question or gold. With `--real-snapshot`, distinct
  existing properties, identity lookups and directed relations produce draft
  information needs; the same source family can support multiple questions.
  Explicit test/placeholder markers are excluded from automatic candidates.
- 60 synthetic ontology questions, scored separately.
- 120 static snapshot safety scenarios. They do not demonstrate live permission
  changes, races, actual restore transactions or the production write path.
- The old 50-question corpus is regression-only, never model-selection gold.

The generated `human-review.html` is a local page with network access disabled.
It displays candidate source objects and directed relations using escaped JSON
and DOM text nodes. Reviewers can edit questions, add evidence from the retained
authorized snapshot, assign grades, and declare real no-answer cases. A grade-2
set requires **all** members to be retrieved; alternative sets can be edited in
JSON. Unassigned slots may select sources from an existing same-split family.
Do not invent new families to inflate the independent sample count.

The downloaded JSON contains a content digest, not an authenticated electronic
signature. Human review must still be explicit on each row and globally attested.
No external LLM creates or reviews these real questions.

```powershell
python scripts/research_retrieval.py prepare-fixture --output reports/retrieval-research/private/review-v1 --real-snapshot reports/retrieval-research/private/snapshot.json
python scripts/research_retrieval.py validate-labels --fixture-dir reports/retrieval-research/private/review-v1 --labels reviewed-labels.json --lock reviewed-labels.lock.json
python scripts/research_retrieval.py evaluate --fixture-dir reports/retrieval-research/private/review-v1 --labels reviewed-labels.json --labels-lock reviewed-labels.lock.json --model e5-small --model-cache .model-cache --split blind --output reports/retrieval-research/private/blind.json
```

`validate-labels` checks all 100 IDs, source/snapshot fingerprints, graded
relevance, sufficient evidence sets, answerability, reviewer/time/attestation,
answer-copy heuristics and source-family/evidence split leakage. Incomplete
review returns exit 2. A lock is created only after complete validation and is
never overwritten. `evaluate` ignores unbound real labels; it does not promote
unreviewed candidates by filling in generated answers.

## Metrics and decisions

Recall@5/20/50, nDCG@10, MRR@10, Precision@5, sufficiency@5, no-answer false
positive rate, acceptance rate, and answerable coverage use explicit denominators.
Coverage requires acceptance **and** a complete sufficient evidence set; merely
accepting irrelevant results is not coverage. Aggregate/path task success is
unexecuted and their coverage/sufficiency is N/A. Ten-source counts cannot be
judged as five-card retrieval tasks. `selection_facts` excludes such structural
diagnostics; a later graph executor must report count/binding/task correctness.

Reports contain both query means and source-family macro means. Selection CIs
and p-values concern the latter: paired cluster bootstrap (95%) plus paired
cluster sign-randomization, with Holm correction across reported comparisons.
Many paraphrases from a few source families do not become independent evidence.

`protocol-v1.json` fixes gates; a generated `protocol-template.json` declares
allowed config hashes, freeze time and content-addressed evidence references.
Report-time arbitrary thresholds cannot relax the packaged rules. Required are
100 locked human gold rows, blind/frozen configuration, observed family-macro
nDCG gain at least .03 with CI lower bound above zero and Holm significance,
coverage at least .9, no-answer FPR at most .05, no critical Recall@5 decline
above .03, ANN Recall@50 at least .98, and installed HTTP p95 at most 3000 ms.

Each evidence reference is `{ "path": "local.json", "sha256": "raw-file-sha256" }`.
The ANN contract is `kg-retrieval-ann-v1`, `backend:neo4j_ann`, `real_vectors:true`,
matching profile/dataset/representation, `top_k:50`, recall and successful cleanup.
The HTTP report must identify the matching model fingerprint, real HTTP
transport, at least 100 samples and p95. Live safety must exercise namespace,
visibility-change, revision-race and direction checks through installed HTTP,
with zero failures and successful cleanup. Static snapshot results cannot fill
those fields. Local hashes verify integrity, not authenticity of a person or a
deployment. Eligible results can be activated by the authorized deployment step;
this offline evaluator never changes the active production configuration.

Missing gold, unavailable real weights, smoke vectors, limited subsets, missing
freeze/ANN/HTTP/safety evidence, or unproven gain leave promotion **blocked**.
`--model smoke --limit 5` proves the tool loop only, never model quality.
