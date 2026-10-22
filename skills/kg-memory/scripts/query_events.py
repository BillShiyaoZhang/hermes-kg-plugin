"""Date/person/type-filtered events with inclusive CLI dates and typed parameters."""
import argparse
import json
import _kg_import
from kg.config import load_config
from kg.queries import query_events
from kg.schema import EVENT_TYPES


def main(argv=None):
    _kg_import.configure_output()
    config = load_config()
    parser = argparse.ArgumentParser(description="Query KG events")
    parser.add_argument("--from", dest="from_date")
    parser.add_argument("--to", dest="to_date")
    parser.add_argument("--person", default=config.user_person_urn)
    parser.add_argument("--all-persons", action="store_true")
    parser.add_argument("--event-type", choices=sorted(EVENT_TYPES))
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)
    person = None if args.all_persons else args.person
    results = query_events(args.from_date, args.to_date, person, args.event_type, args.limit, config=config)
    print(json.dumps({"filters": {"from": args.from_date, "to": args.to_date, "person": person, "event_type": args.event_type}, "count": len(results), "events": results}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
