#!/usr/bin/env python3
"""Optional synthetic retrieval evaluation against an explicit disposable Neo4j.

Run inside the development Docker image, never against a personal memory graph.
KG_EVAL_NEO4J_URL and KG_EVAL_NEO4J_PASSWORD must be supplied. --seed changes only
the fixture namespace. A real model run is opt-in, downloads require another
explicit flag, and every report identifies whether it actually used the model.
"""
from __future__ import annotations

import argparse
import json
import os
import logging
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kg.config import KGConfig
from kg.embedding import LocalEmbedder
from kg.search import HybridSearch
from kg.store import KGStore

logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

FIXTURE = Path(__file__).parent / "fixtures" / "search_50.json"


def load_fixture():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if len(data["queries"]) != 50 or len({q["id"] for q in data["queries"]}) != 50:
        raise ValueError("The fixture must have exactly 50 uniquely identified queries")
    return data


def seed_fixture(store, data):
    prefix = data["namespace"]
    if store.config.urn_prefix != prefix or prefix != "urn:hermes-eval:kg:":
        raise ValueError("Evaluation seeding is restricted to its fixed synthetic namespace")
    ids = {e["id"]: prefix + e["type"].lower() + "-" + e["id"] for e in data["entities"]}
    with store.driver.session(database=store.config.database) as session:
        session.run("MATCH (n) WHERE n.uri STARTS WITH $prefix DETACH DELETE n", {"prefix": prefix}).consume()
        session.run("MATCH (c:KGSearchChunk {namespace:$prefix}) DELETE c", {"prefix": prefix}).consume()
    entities = [{k: v for k, v in e.items() if k not in {"archived", "disabled"}} for e in data["entities"]]
    entities.insert(0, {"id": "eval-user", "type": "Person", "name": "Synthetic evaluation user"})
    relations = [{"from": ids[r["from"]], "to": ids[r["to"]], "type": r["type"]} for r in data["relations"]]
    store.write_batch(entities, relations, source_session="synthetic-search-eval-" + uuid.uuid4().hex)
    # Exclusions are intentionally applied after indexing by the caller to test
    # stale derived chunks, not just the happy-path index filter.
    return ids


def apply_exclusions(store, data, ids):
    with store.driver.session(database=store.config.database) as session:
        for e in data["entities"]:
            if e.get("archived"):
                session.run("MATCH (n {uri:$uri}) SET n:KGQuarantined, n.hermes_archivedByWorkbench=true, n.hermes_revision=1", {"uri": ids[e["id"]]}).consume()
            if e.get("disabled"):
                session.run("MATCH (n {uri:$uri}) SET n.hermes_enabled=false, n.hermes_revision=1", {"uri": ids[e["id"]]}).consume()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="store_true", help="Replace the fixed synthetic namespace in the explicitly configured disposable DB")
    parser.add_argument("--real-model", action="store_true", help="Load E5 locally and evaluate real vectors")
    parser.add_argument("--allow-download", action="store_true", help="Explicitly permit first-time pinned model download")
    parser.add_argument("--cache-dir", default="/models/kg-e5")
    args = parser.parse_args(argv)
    url, password = os.environ.get("KG_EVAL_NEO4J_URL"), os.environ.get("KG_EVAL_NEO4J_PASSWORD")
    if not url or not password:
        parser.error("Set KG_EVAL_NEO4J_URL and KG_EVAL_NEO4J_PASSWORD for a disposable evaluation DB")
    if args.allow_download and not args.real_model:
        parser.error("--allow-download requires --real-model")
    data = load_fixture()
    config = KGConfig(neo4j_url=url, user=os.environ.get("KG_EVAL_NEO4J_USER", "neo4j"), password=password,
                      database=os.environ.get("KG_EVAL_NEO4J_DATABASE", "neo4j"),
                      urn_prefix=data["namespace"], user_person_urn=data["namespace"] + "person-eval-user")
    model = LocalEmbedder(args.cache_dir) if args.real_model else None
    if model is not None:
        model.prepare(allow_download=args.allow_download)
    with KGStore(config) as store:
        search = HybridSearch(store, model)
        if args.seed:
            store.ensure_schema()
            ids = seed_fixture(store, data)
            search.ensure_indexes()
            index_report = search.reindex()
            with store.driver.session(database=config.database) as session:
                session.run("CALL db.awaitIndexes(120)").consume()
            apply_exclusions(store, data, ids)
        else:
            index_report = None
        rows = []
        forbidden_global = {data["namespace"] + "concept-" + name for name in ("archived", "disabled")}
        for query in data["queries"]:
            start = time.monotonic()
            answer = search.search(query["question"], limit=5,
                                   filters={"namespace": data["namespace"], **query.get("filters", {})})
            uris = [r["uri"] for r in answer["kg_results"]]
            expected = {data["namespace"] + next(e["type"].lower() for e in data["entities"] if e["id"] == name) + "-" + name for name in query["expected"]}
            hits = expected.intersection(uris)
            declared_k = query.get("top_k", 5)
            declared_hits = expected.intersection(uris[:declared_k])
            rows.append({"id": query["id"], "category": query["category"], "mode": answer["mode"],
                         "expected_count": len(expected), "declared_k": declared_k,
                         "hit": bool(declared_hits) if expected else None,
                         "hit_at_5": bool(hits) if expected else None,
                         "recall": len(hits) / len(expected) if expected else None,
                         "reciprocal_rank": next((1 / (i + 1) for i, uri in enumerate(uris) if uri in expected), 0) if expected else None,
                         "forbidden_returned": bool(forbidden_global.intersection(uris)),
                         "milliseconds": round((time.monotonic() - start) * 1000, 2), "uris": uris})
        measured = [row for row in rows if row["hit"] is not None]
        report = {"fixture": FIXTURE.name, "synthetic": True, "real_model_requested": args.real_model,
                  "model": model.status() if model else None, "indexing": index_report,
                  "questions": len(rows), "hit_at_k": sum(r["hit"] for r in measured) / len(measured),
                  "positive_questions": len(measured),
                  "hit_at_5": sum(r["hit_at_5"] for r in measured) / len(measured),
                  "mean_recall_at_k": sum(r["recall"] for r in measured) / len(measured),
                  "mrr": sum(r["reciprocal_rank"] for r in measured) / len(measured),
                  "forbidden_returned": sum(r["forbidden_returned"] for r in rows), "results": rows,
                  "interpretation": "Synthetic authored set; this is not evidence of performance on real user memory."}
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        if report["forbidden_returned"]:
            return 1
        if args.real_model and any(r["mode"] != "hybrid" for r in rows):
            return 2  # Never silently claim a real-model evaluation after fallback.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
