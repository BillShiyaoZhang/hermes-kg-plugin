#!/usr/bin/env python3
"""Isolated performance measurement; synthetic vectors are NOT a quality test.

Use scripts/benchmark_search.sh to create a dedicated capped Docker pair. The
script seeds a fresh random private namespace and removes its nodes in finally.
It never reads the user's Hermes transcript or modifies the main service.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import logging
import math
import os
import platform
import resource
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kg.config import KGConfig
from kg.embedding import LocalEmbedder
from kg.search import HybridSearch, SOURCE_INDEX, entity_fingerprint
from kg.store import KGStore

logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)


def percentile(values, fraction):
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * fraction) - 1)], 3) if ordered else None


def memory_snapshot():
    values = {"process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024}
    for name in ("memory.current", "memory.peak", "memory.max", "cpu.max"):
        path = Path("/sys/fs/cgroup") / name
        try:
            raw = path.read_text().strip()
            values[name] = int(raw) if raw.isdigit() else raw
        except OSError:
            values[name] = None
    return values


def timing_summary(values):
    return {"count": len(values), "p50_ms": percentile(values, .5), "p95_ms": percentile(values, .95),
            "min_ms": round(min(values), 3) if values else None,
            "max_ms": round(max(values), 3) if values else None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=10000)
    parser.add_argument("--queries", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--cache-dir", default="/data/models")
    parser.add_argument("--max-seconds", type=int, default=180)
    args = parser.parse_args(argv)
    if args.count < 1 or args.queries < 21 or not 1 <= args.batch_size <= 1000:
        parser.error("count must be positive, queries >=21, batch size 1..1000")
    url = os.environ.get("KG_BENCH_NEO4J_URL")
    password = os.environ.get("KG_BENCH_NEO4J_PASSWORD")
    if not url or not password:
        parser.error("Explicit KG_BENCH_NEO4J_URL/PASSWORD for a disposable DB are required")
    prefix = "urn:kgbench" + uuid.uuid4().hex + ":kg:"
    config = KGConfig(neo4j_url=url, password=password, urn_prefix=prefix,
                      user_person_urn=prefix + "person-user", query_timeout=30)
    report = {"started_at_utc": datetime.now(timezone.utc).isoformat(), "complete": False,
              "dataset": {"memories": args.count, "chunks": args.count, "relations": 0,
                          "namespace": prefix, "shared": False, "synthetic_vectors": True,
                          "quality_test": False, "description": "Short synthetic memories; all chunks reuse one real E5 passage vector. No quality conclusions."},
              "environment": {"platform": platform.platform(), "architecture": platform.machine(),
                              "python": platform.python_version(), "app_cpu_cap": 2, "app_memory_cap_bytes": 4 * 1024**3,
                              "database_cpu_cap": 2, "database_memory_cap_bytes": 4 * 1024**3,
                              "combined_caps": "4 CPU / 8 GiB across two independent benchmark containers",
                              "docker_host": os.environ.get("KG_BENCH_DOCKER_INFO"),
                              "image": os.environ.get("KG_BENCH_IMAGE"),
                              "routine_schema_warning_logging": "suppressed in benchmark runner",
                              "packages": {name: importlib.metadata.version(name) for name in ("fastembed", "onnxruntime", "neo4j")}},
              "warmup_queries": 3, "requested_timed_queries": args.queries,
              "memory_before_model": memory_snapshot(), "cleanup_complete": False}
    store = KGStore(config)
    samples, status_samples = [], []
    started = time.monotonic()
    try:
        model = LocalEmbedder(args.cache_dir, threads=2)
        model.prepare(allow_download=False)
        report["model"] = model.status()
        report["memory_after_model"] = memory_snapshot()
        search = HybridSearch(store, model)
        store.ensure_schema()
        search.ensure_indexes()
        # Encode one passage once; repeated vectors are explicitly performance-only.
        sample = "Synthetic benchmark memory about persistent project context. 合成性能测试记忆。"
        sample_vector = model.embed_passages([sample])[0]
        seed_started = time.monotonic()
        for offset in range(0, args.count, args.batch_size):
            records = []
            for i in range(offset, min(args.count, offset + args.batch_size)):
                uri = prefix + "concept-memory-" + str(i)
                props = {"uri": uri, "hermes_name": "Benchmark memory " + str(i),
                         "hermes_description": sample + " Record " + str(i), "hermes_revision": 1, "shared": False}
                text = props["hermes_name"] + "\n" + props["hermes_description"]
                token_count = len(model._tokenizer.encode("passage: " + text, add_special_tokens=True).ids)
                if token_count > 512:
                    raise ValueError("Benchmark document unexpectedly exceeds model token limit")
                chunk = {"id": prefix + str(i), "source_uri": uri, "source_revision": 1,
                         "source_fingerprint": entity_fingerprint(props), "namespace": prefix,
                         "text": text, "chunk_index": 0, "token_count": token_count,
                         "model_revision": model.model_revision, "embedding_revision": model.embedding_revision,
                         "embedding": sample_vector, "embedding_state": "ready"}
                records.append({"properties": props, "chunk": chunk})
            search._run("""UNWIND $records AS record
                CREATE (n:KGEntity:Concept) SET n=record.properties,n.hermes_updatedAt=datetime()
                CREATE (c:KGSearchChunk) SET c=record.chunk,c.source_updated_at=toString(n.hermes_updatedAt)
                RETURN count(n) AS written""", {"records": records}, write=True)
        with store.driver.session(database=config.database) as session:
            session.run("CALL db.awaitIndexes(120)").consume()
            report["environment"]["database"] = [dict(row) for row in session.run("CALL dbms.components() YIELD name,versions,edition RETURN name,versions,edition")]
            report["indexes"] = [dict(row) for row in session.run("SHOW INDEXES YIELD name,type,state,populationPercent,properties WHERE name STARTS WITH 'kg_search_' RETURN name,type,state,populationPercent,properties")]
            summary = session.run("EXPLAIN MATCH (c:KGSearchChunk {namespace:$prefix,source_uri:$uri}) RETURN c.id",
                                  {"prefix": prefix, "uri": prefix + "concept-memory-1"}).consume()
            report["source_index_plan"] = summary.plan
        report["seed_and_index_seconds"] = round(time.monotonic() - seed_started, 3)
        report["index_status"] = search.status()
        report["memory_after_indexing"] = memory_snapshot()
        if report["index_status"].get("pending_count") != 0 or report["index_status"].get("indexed_count") != args.count:
            raise RuntimeError("Benchmark indexes are incomplete")
        if not any(i["name"] == SOURCE_INDEX and i["state"] == "ONLINE" for i in report["indexes"]):
            raise RuntimeError("Source lookup index is not ONLINE")
        for i in range(3):
            answer = search.search("persistent project memory benchmark " + str(i), limit=10)
            if answer["mode"] != "hybrid":
                raise RuntimeError("Warmup degraded instead of using local hybrid search")
        for i in range(args.queries):
            if time.monotonic() - started > args.max_seconds:
                report["stopped_reason"] = "explicit_wall_time_budget"
                break
            question = ("persistent project memory benchmark " if i % 2 else "持久项目上下文 合成记忆 ") + str(i)
            tick = time.perf_counter()
            answer = search.search(question, limit=10)
            samples.append((time.perf_counter() - tick) * 1000)
            if answer["mode"] != "hybrid":
                raise RuntimeError("A measured search degraded from hybrid")
            if any(not r["uri"].startswith(prefix) for r in answer["kg_results"]):
                raise RuntimeError("Benchmark namespace isolation failed")
        for _ in range(10):
            tick = time.perf_counter()
            search.status()
            status_samples.append((time.perf_counter() - tick) * 1000)
        report["complete"] = len(samples) == args.queries
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        report["error"] = "Benchmark did not complete; inspect disposable container diagnostics."
    finally:
        report["hybrid_search"] = timing_summary(samples)
        report["status_lookup"] = timing_summary(status_samples)
        report["search_samples_ms"] = [round(v, 3) for v in samples]
        report["memory_at_end"] = memory_snapshot()
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        try:
            with store.driver.session(database=config.database) as session:
                session.run("MATCH (c:KGSearchChunk {namespace:$prefix}) DELETE c", {"prefix": prefix}).consume()
                session.run("MATCH (n) WHERE n.uri STARTS WITH $prefix DETACH DELETE n", {"prefix": prefix}).consume()
                session.run("MATCH (w:KGWorkspace {namespace:$prefix}) DELETE w", {"prefix": prefix}).consume()
                remaining = session.run("MATCH (n) WHERE n.uri STARTS WITH $prefix OR n.namespace=$prefix RETURN count(n) AS remaining", {"prefix": prefix}).single()["remaining"]
                report["cleanup_complete"] = remaining == 0
        except Exception:
            report["cleanup_complete"] = False
        store.close()
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["complete"] and report["cleanup_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
