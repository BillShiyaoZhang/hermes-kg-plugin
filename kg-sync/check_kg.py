"""Read-only health/aggregate check using the unified KG config and driver."""
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"plugins"))
from kg.config import load_config
from kg.store import KGStore


def main():
    try:
        config=load_config()
        with KGStore(config) as store:
            alive=store.run_read("RETURN 1 AS ready")[0]["ready"]==1
            nodes=store.run_read("MATCH (n) WHERE n.uri STARTS WITH $prefix RETURN labels(n) AS labels,count(n) AS count ORDER BY count DESC",{"prefix":config.urn_prefix})
            relations=store.run_read("MATCH (a)-[r]->(b) WHERE a.uri STARTS WITH $prefix OR b.uri STARTS WITH $prefix RETURN type(r) AS type,count(r) AS count ORDER BY count DESC",{"prefix":config.urn_prefix})
        print(json.dumps({"ok":alive,"database":config.database,"namespace":config.urn_prefix,"nodes_by_labels":nodes,"relations_by_type":relations},ensure_ascii=False,indent=2))
        return 0
    except Exception as exc:
        print(f"KG health check failed: {type(exc).__name__}: {exc}",file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
