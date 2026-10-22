# Alpha local hybrid search performance

This is a measured synthetic performance run, not a retrieval-quality result or a claim about a user's computer. The raw measurements are in `search-performance-alpha.json`.

## Measured environment

- Linux aarch64 containers on Docker Desktop; its VM reported 6 CPUs and 16,748,396,544 bytes of RAM.
- Dedicated application container: 2 CPU quota, 4 GiB RAM, swap disabled beyond that cap. Dedicated Neo4j container: the same limits. Combined ceiling: **4 CPUs / 8 GiB**, separate from the development service.
- Python 3.11.17, FastEmbed 0.9.0, ONNX Runtime CPU 1.23.2, Neo4j driver 5.28.1, Neo4j Community 5.26.31.
- Real local `intfloat/multilingual-e5-small`, revision `ccc66d3bcd826f577e26b9a4072cc5fe3a7ad6a3`; prepared cache, no model download or remote LLM in measured searches.
- Routine Neo4j missing-label/property notifications were suppressed in this benchmark process.

## Dataset and method

The isolated database contained **10,000 short private synthetic memories and 10,000 derived chunks**, with no relationships. Every chunk reused one real E5 passage vector to avoid encoding 10,000 documents; this deliberately does not measure semantic accuracy. Each timed search still encoded its query with the real local model and exercised full-text retrieval, ANN, RRF, bounded one-hop lookup and current-source filtering. The one-hop lookup had no edges to expand in this dataset.

After three warm-up queries, 30 mixed Chinese/English queries were timed with `perf_counter`. Ten additional status calls were timed separately. P95 uses the nearest-rank percentile. Full-text, vector and source-lookup indexes were ONLINE; indexed count was 10,000, pending count zero. EXPLAIN confirmed a composite `NodeIndexSeek` on `KGSearchChunk(namespace, source_uri)`.

| Measurement | P50 | P95 |
|---|---:|---:|
| Local hybrid search, 30 queries | 432.563 ms | 505.211 ms |
| Index/status lookup, 10 calls | 54.619 ms | 55.254 ms |

The application container's measured peak cgroup memory was **1,210,834,944 bytes**; Python's peak RSS was 1,245,126,656 bytes. The database container's peak cgroup memory was **1,969,508,352 bytes**. These are separate container peaks, not a simultaneous memory sample. Batch seeding and index population took 5.302 seconds. The Python benchmark took 22.175 seconds; this excludes temporary database startup.

The run meets the Alpha target of warm local hybrid-search P95 below 1.5 seconds in this constrained container setup. It does not cover cold model load, slow storage, long documents, graph fan-out, concurrent clients, native Windows/macOS runtimes, or semantic relevance at 10,000 records.

## Reproduce

Prepare the development Docker image and local model cache first. The runner creates an independent Docker network and database; the application source and prepared cache are mounted read-only. It uses a random private benchmark namespace, removes that namespace in `finally`, and removes both containers/network on exit. No published service ports or main-service state are changed.

```bash
KG_BENCH_IMAGE=hermes-kg-dev-tests \
KG_BENCH_CACHE_VOLUME=hermes-kg-dev_console-data \
KG_BENCH_REPORT=/tmp/hermes-kg-search-benchmark.json \
bash scripts/benchmark_search.sh --count 10000 --queries 30 --max-seconds 180
```

Override the cache-volume name for a different Compose project. A missing model cache fails explicitly rather than downloading during measurement. A time-budget stop reports an incomplete run; it must not be treated as a passed result.

The acceptance run verified `cleanup_complete=true`; no benchmark containers remained afterward. The performance work also replaced per-result unlabeled URI scans with canonical `KGEntity` unique-index seeks, retaining a gated fallback for legacy typed entities without that label.
