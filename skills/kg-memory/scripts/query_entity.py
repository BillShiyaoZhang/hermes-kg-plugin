"""Keyword/owner/type search through the shared KG query contract."""
import argparse
import json
import _kg_import
from kg.queries import query_entities
from kg.schema import ENTITY_LABELS


def main(argv=None):
    _kg_import.configure_output()
    parser = argparse.ArgumentParser(description="Search KG entities")
    parser.add_argument("--query", default="")
    parser.add_argument("--type", dest="entity_type", choices=[*ENTITY_LABELS, *ENTITY_LABELS.values()])
    parser.add_argument("--owner")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    results = query_entities(args.query, args.entity_type, args.owner, args.limit)
    print(json.dumps({"query": args.query, "type_filter": args.entity_type, "count": len(results), "entities": results}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
