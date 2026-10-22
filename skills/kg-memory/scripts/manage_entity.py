"""Entity/relation CRUD using the shared KG schema and atomic parameterized store."""
import argparse
import json
import _kg_import
from kg.config import load_config
from kg.schema import ENTITY_LABELS, EVENT_TYPES, canonical_urn
from kg.store import KGStore


def _parse_props(prop_list):
    props = {}
    for value in prop_list or []:
        if "=" not in value:
            raise ValueError("--prop must be key=value")
        key, raw = value.split("=", 1)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        props[key.strip()] = parsed
    return props


def _make_urn(id_, type_):
    return canonical_urn(id_, type_, prefix=load_config().urn_prefix)


def _write_entity(kind, id_, name, props=None, **extra):
    config = load_config()
    with KGStore(config) as store:
        receipt = store.write_batch([{"type": kind, "id": id_, "name": name, "properties": props or {}, **extra}], [])
    return receipt["entity_uris"][0]


def add_person(id_, name, props):
    return _write_entity("person", id_, name, props)


def add_event(id_, name, description="", event_type="general", event_time=None, agent_urn=None):
    return _write_entity("event", id_, name, {"description": description, "event_type": event_type or "general", "time": event_time}, agent=agent_urn)


def add_preference(id_, name, pref_key, pref_value, owner_urn=None):
    return _write_entity("preference", id_, name, {"pref_key": pref_key, "pref_value": pref_value}, owner=owner_urn)


def add_decision(id_, name, decision_text, decision_context="", owner_urn=None):
    return _write_entity("decision", id_, name, {"decision_text": decision_text, "decision_context": decision_context}, owner=owner_urn)


def add_location(id_, name, description="", latitude=None, longitude=None):
    return _write_entity("location", id_, name, {"description": description, "latitude": latitude, "longitude": longitude})


def add_concept(id_, name, props=None):
    return _write_entity("concept", id_, name, props)


def add_relation(from_urn, to_urn, rel_type, rel_label=""):
    with KGStore() as store:
        return store.write_batch([], [{"from": from_urn, "to": to_urn, "type": rel_type, "label": rel_label}])


def delete_entity(type_, id_):
    with KGStore() as store:
        return store.delete_entity(id_, type_)


def main(argv=None):
    _kg_import.configure_output()
    parser = argparse.ArgumentParser(description="KG Memory entity and relationship CRUD")
    parser.add_argument("--type", required=True, choices=[*ENTITY_LABELS, "relation"])
    parser.add_argument("--id")
    parser.add_argument("--name")
    parser.add_argument("--prop", action="append")
    parser.add_argument("--description")
    parser.add_argument("--event-type", choices=sorted(EVENT_TYPES))
    parser.add_argument("--event-time")
    parser.add_argument("--agent")
    parser.add_argument("--owner")
    parser.add_argument("--pref-key")
    parser.add_argument("--pref-value")
    parser.add_argument("--decision-text")
    parser.add_argument("--decision-context")
    parser.add_argument("--latitude", type=float)
    parser.add_argument("--longitude", type=float)
    parser.add_argument("--shared", choices=["true", "false"])
    parser.add_argument("--from", dest="from_urn")
    parser.add_argument("--to", dest="to_urn")
    parser.add_argument("--rel-type")
    parser.add_argument("--rel-label")
    parser.add_argument("--delete", action="store_true")
    args = parser.parse_args(argv)
    try:
        with KGStore() as store:
            if args.type == "relation":
                if not all((args.from_urn, args.to_urn, args.rel_type)):
                    parser.error("relation requires --from, --to, --rel-type")
                if args.delete:
                    result = {"ok": True, "deleted": store.delete_relation(args.from_urn, args.to_urn, args.rel_type)}
                else:
                    result = store.write_batch([], [{"from": args.from_urn, "to": args.to_urn, "type": args.rel_type, "label": args.rel_label}])
            else:
                if not args.id:
                    parser.error("--id is required")
                if args.delete:
                    result = {"ok": True, "deleted": store.delete_entity(args.id, args.type)}
                else:
                    if not args.name:
                        parser.error("--name is required")
                    props = _parse_props(args.prop)
                    fields = {"description": args.description, "event_type": args.event_type, "time": args.event_time,
                              "pref_key": args.pref_key, "pref_value": args.pref_value, "decision_text": args.decision_text,
                              "decision_context": args.decision_context, "latitude": args.latitude, "longitude": args.longitude, "shared": args.shared}
                    props.update({key: value for key, value in fields.items() if value is not None})
                    result = store.write_batch([{"type": args.type, "id": args.id, "name": args.name, "properties": props, "owner": args.owner, "agent": args.agent}], [])
        print(json.dumps(result, ensure_ascii=False, default=str))
    except (ValueError, RuntimeError) as exc:
        parser.exit(1, f"KG operation failed: {exc}\n")


if __name__ == "__main__":
    main()
