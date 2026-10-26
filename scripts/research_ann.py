#!/usr/bin/env python3
"""Compare frozen real embeddings with a disposable Neo4j vector index.

Set KG_RESEARCH_BOLT_URL and KG_RESEARCH_PASSWORD explicitly. This script only
creates library-owned, random research labels and deletes them in a finally.
No production facts are written. It is not an HTTP or authority-safety test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", default="e5-small")
    parser.add_argument("--model-cache", type=Path, default=Path(".model-cache"))
    parser.add_argument("--vector-cache", required=True, type=Path)
    parser.add_argument("--representation", choices=("values", "entity", "entity_relations"), default="entity")
    parser.add_argument("--split", choices=("dev", "blind"), default="dev")
    parser.add_argument("--scope", choices=("profile", "shared"), default="profile")
    parser.add_argument("--oversample", type=int, choices=(1, 5, 10, 20), default=10)
    parser.add_argument("--background", type=int, default=0)
    parser.add_argument("--own-background", type=int, default=0, help="Synthetic in-scope scale diagnostic; cannot serve as evidence for the unaugmented corpus")
    parser.add_argument("--exact-rescore", action="store_true", help="Recompute exact cosine on oversampled ANN candidates")
    parser.add_argument("--partition-by-namespace", action="store_true", help="Index only this namespace; retain foreign rows as an isolated competition control")
    args = parser.parse_args(argv)
    if not os.environ.get("KG_RESEARCH_BOLT_URL") or not os.environ.get("KG_RESEARCH_PASSWORD"):
        parser.error("Set explicit disposable KG_RESEARCH_BOLT_URL and KG_RESEARCH_PASSWORD")
    if not 0 <= args.background <= 100000 or not 0 <= args.own_background <= 100000:
        parser.error("background counts must be 0..100000")
    if args.split == "blind":
        parser.error("Blind ANN runs require the final frozen suite; use dev during development")
    from neo4j import GraphDatabase
    from kg.embedding_profiles import create_embedder
    from kg.retrieval_documents import dataset_to_documents
    from kg.retrieval_ann import ResearchVectorIndex, exact_card_ranking, agreement_at_k
    from kg.research.common import digest, read_json, write_json
    from kg.research.metrics import percentile
    from kg.research.runner import (VectorCache, _allowed, _source_state,
                                    tool_fingerprint, documents_fingerprint)
    logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
    dataset = read_json(args.fixture_dir / "dataset.json")
    fixture = read_json(args.fixture_dir / "fixture.json")
    namespace = dataset["namespace"]
    queries = [q for q in fixture["queries"] if q["split"] == args.split and q["track"] == "facts"
               and q.get("gold") is not None and q.get("namespace", namespace) == namespace]
    if not queries:
        parser.error("No labeled synthetic development queries")
    embedder = create_embedder(args.model, args.model_cache, max_tokens=512)
    embedder.prepare(allow_download=False)
    fingerprint = embedder.fingerprint
    source_state = _source_state(dataset)
    documents = [doc for doc in dataset_to_documents(dataset, args.representation)
                 if _allowed(doc, source_state, namespace=namespace, scope=args.scope)]
    cache = VectorCache(args.vector_cache)
    driver = index = None
    report = {"format": "kg-retrieval-ann-v1", "backend": "neo4j_ann", "real_vectors": True,
              "source": "synthetic_scale_diagnostic" if args.own_background else "isolated_snapshot", "model": embedder.model_manifest,
              "profile_fingerprint": fingerprint, "dataset_sha256": digest(dataset),
              "namespace": namespace,
              "tool_code_sha256": tool_fingerprint(),
              "ann_implementation_sha256": {
                  path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in (Path(__file__), Path(__file__).resolve().parents[1] / "plugins/kg/retrieval_ann.py")
              },
              "actualcards_digest": documents_fingerprint(documents),
              "representation": args.representation, "top_k": 50, "oversample": args.oversample,
              "retrieval_scope": args.scope,
              "exact_rescore": args.exact_rescore,
              "partition_by_namespace": args.partition_by_namespace,
              "background_chunks": args.background, "ok": False,
              "own_background_chunks": args.own_background,
              "scope": "synthetic_development_queries", "promotion": "blocked",
              "interpretation": "ANN agreement with prefiltered exact cosine on immutable cards; not semantic quality, HTTP or live authorization testing."}
    try:
        rows = []
        for doc in documents:
            for ordinal, chunk in enumerate(embedder.chunk(doc.text)):
                vector = cache.get(fingerprint, "passage", chunk.text)
                if vector is None:
                    vector = cache.put(fingerprint, "passage", chunk.text, embedder.embed_passages([chunk.text])[0])
                rows.append({"id": doc.id + ":" + str(ordinal), "card_id": doc.id,
                             "namespace": doc.namespace, "kind": doc.kind, "embedding": vector})
        # Deliberately competing foreign content, encoded from distinct text.
        # This must not reuse one fixture vector as a fake large semantic index.
        for start in range(0, args.background, 16):
            texts = ["共享背景记录 " + str(i) + "。" + documents[i % len(documents)].text
                     for i in range(start, min(args.background, start + 16))]
            vectors = embedder.embed_passages([embedder.chunk(text)[0].text for text in texts])
            for offset, vector in enumerate(vectors):
                identity = "background-" + str(start + offset)
                rows.append({"id": identity, "card_id": identity, "namespace": "urn:foreign-research:kg:",
                             "kind": "entity", "embedding": vector})
        own_text_hashes, own_vector_hashes = set(), set()
        for start in range(0, args.own_background, 16):
            texts = ["本空间规模诊断记录 " + str(i) + "。" + documents[i % len(documents)].text
                     for i in range(start, min(args.own_background, start + 16))]
            vectors = embedder.embed_passages([embedder.chunk(text)[0].text for text in texts])
            for offset, (text, vector) in enumerate(zip(texts, vectors)):
                identity = "own-background-" + str(start + offset)
                own_text_hashes.add(hashlib.sha256(text.encode()).hexdigest())
                own_vector_hashes.add(digest(vector))
                rows.append({"id": identity, "card_id": identity, "namespace": namespace,
                             "kind": "entity", "embedding": vector})
        report["own_background_integrity"] = {
            "independent_text_sha256_count": len(own_text_hashes),
            "distinct_vector_sha256_count": len(own_vector_hashes), "one_vector_reused": False}
        driver = GraphDatabase.driver(os.environ["KG_RESEARCH_BOLT_URL"],
                                      auth=("neo4j", os.environ["KG_RESEARCH_PASSWORD"]))
        index = ResearchVectorIndex(driver, dimensions=embedder.dimensions, profile_fingerprint=fingerprint,
                                    indexed_namespace=namespace if args.partition_by_namespace else None)
        started = time.perf_counter()
        report["index"] = index.build(rows)
        report["indexing_ms"] = (time.perf_counter() - started) * 1000
        results = []
        # Warm the ANN procedure separately; measured vectors are also encoded
        # below rather than silently treating vector-cache hits as model latency.
        index.query(embedder.embed_query(queries[0]["question"]), namespace=namespace)
        for query in queries:
            vector = embedder.embed_query(query["question"])
            tick = time.perf_counter()
            exact = exact_card_ranking(rows, vector, namespace=namespace)
            exact_ms = (time.perf_counter() - tick) * 1000
            tick = time.perf_counter()
            approximate = index.query(vector, namespace=namespace, oversample=args.oversample,
                                      exact_rescore=args.exact_rescore)
            results.append({"id": query["id"], "recall": agreement_at_k(exact, approximate),
                            "ann_returned": len(approximate), "exact_available": len(exact),
                            "shortfall_at_50": max(0, min(50, len(exact)) - len(approximate)),
                            "ann_ms": (time.perf_counter() - tick) * 1000, "exact_ms": exact_ms})
        values = [row["recall"] for row in results if row["recall"] is not None]
        report.update({"query_count": len(results), "ann_recall_at_50": sum(values) / len(values),
                       "query_latency_ms": {"p50": percentile([r["ann_ms"] for r in results], .5),
                                            "p95": percentile([r["ann_ms"] for r in results], .95)},
                       "exact_latency_ms": {"p50": percentile([r["exact_ms"] for r in results], .5),
                                            "p95": percentile([r["exact_ms"] for r in results], .95)},
                       "per_query": results, "ok": True,
                       "gate_passed": sum(values) / len(values) >= .98})
    finally:
        if index:
            report["cleanup"] = index.cleanup()
        if driver:
            driver.close()
        cache.close()
        if not report.get("cleanup", {}).get("complete"):
            report["ok"] = False
        write_json(args.output, report)
    print(json.dumps({key: report[key] for key in ("ok", "query_count", "ann_recall_at_50", "gate_passed", "cleanup")}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
