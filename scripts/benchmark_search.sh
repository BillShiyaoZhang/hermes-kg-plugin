#!/usr/bin/env bash
# Isolated 4 CPU / 8 GiB combined ceiling. Requires the prepared dev image/model cache.
set -euo pipefail
task_root="$(cd "$(dirname "$0")/.." && pwd)"
task_suffix="$(date +%s)-$$"
task_network="kg-benchmark-${task_suffix}"
task_database="kg-benchmark-db-${task_suffix}"
task_app="kg-benchmark-app-${task_suffix}"
task_image="${KG_BENCH_IMAGE:-hermes-kg-dev-tests}"
task_cache="${KG_BENCH_CACHE_VOLUME:-hermes-kg-dev_console-data}"
task_report="${KG_BENCH_REPORT:-/tmp/hermes-kg-search-benchmark.json}"
task_password="kg-benchmark-${task_suffix}-temporary"
cleanup() {
  docker rm -f "$task_app" "$task_database" >/dev/null 2>&1 || true
  docker network rm "$task_network" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
docker image inspect "$task_image" >/dev/null
docker volume inspect "$task_cache" >/dev/null
docker network create "$task_network" >/dev/null
docker run -d --rm --name "$task_database" --network "$task_network" --network-alias benchmark-neo4j \
  --cpus=2 --memory=4g --memory-swap=4g \
  -e "NEO4J_AUTH=neo4j/${task_password}" \
  -e NEO4J_server_memory_heap_initial__size=1G -e NEO4J_server_memory_heap_max__size=2G \
  -e NEO4J_server_memory_pagecache_size=1G neo4j:5.26-community >/dev/null
task_ready=0
for task_attempt in $(seq 1 120); do
  if docker exec "$task_database" cypher-shell -u neo4j -p "$task_password" 'RETURN 1' >/dev/null 2>&1; then task_ready=1; break; fi
  sleep 1
done
if [ "$task_ready" != 1 ]; then echo "Temporary benchmark Neo4j failed to become ready" >&2; exit 1; fi
task_docker_info="$(docker info --format 'CPUs={{.NCPU}} MemoryBytes={{.MemTotal}} Architecture={{.Architecture}} OS={{.OperatingSystem}}')"
docker run --rm --name "$task_app" --network "$task_network" --cpus=2 --memory=4g --memory-swap=4g \
  -v "$task_root:/app:ro" -v "$task_cache:/data:ro" -w /app \
  -e KG_BENCH_NEO4J_URL=bolt://benchmark-neo4j:7687 -e "KG_BENCH_NEO4J_PASSWORD=$task_password" \
  -e "KG_BENCH_DOCKER_INFO=$task_docker_info" -e "KG_BENCH_IMAGE=$task_image" \
  --entrypoint python "$task_image" plugins/kg/tests/benchmark_search.py "$@" > "$task_report"
task_db_peak="$(docker exec "$task_database" cat /sys/fs/cgroup/memory.peak)"
task_db_current="$(docker exec "$task_database" cat /sys/fs/cgroup/memory.current)"
docker run --rm --network none --cpus=1 --memory=128m \
  -v "$task_report:/report.json" -e "KG_BENCH_DB_PEAK=$task_db_peak" -e "KG_BENCH_DB_CURRENT=$task_db_current" \
  --entrypoint python "$task_image" -c 'import json,os; from pathlib import Path; p=Path("/report.json"); d=json.loads(p.read_text()); d["database_container_memory"]={"peak_bytes":int(os.environ["KG_BENCH_DB_PEAK"]),"current_bytes":int(os.environ["KG_BENCH_DB_CURRENT"])}; p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+"\n")'
echo "Performance report: $task_report"
