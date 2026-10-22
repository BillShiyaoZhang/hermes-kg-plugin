"""Canonical graph contract. Only validated labels/types enter Cypher text."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

ENTITY_LABELS = {name.lower(): name for name in ("Person", "Event", "Preference", "Decision", "Location", "Concept")}
EVENT_TYPES = {"travel", "meeting", "social", "task", "system", "daily", "general"}
# Public tool names and canonical required fields share one definition with the
# runtime validator; tool-schema documentation must not invent extra enum values.
ENTITY_REQUIRED_PROPERTIES = {
    "event": ("hermes_eventTime",),
    "preference": ("hermes_preferenceKey", "hermes_preferenceValue"),
    "decision": ("hermes_decisionText",),
}
PREFERRED_PROPERTY_NAMES = {
    "hermes_eventTime": "event_time", "hermes_eventType": "event_type",
    "hermes_preferenceKey": "preference_key", "hermes_preferenceValue": "preference_value",
    "hermes_decisionText": "decision_text", "hermes_decisionContext": "decision_context",
}
RELATION_ALIASES = {
    "KNOWS": "HERMES_knows", "HERMES_KNOWS": "HERMES_knows",
    "PARTICIPATED_IN": "HERMES_participant", "HERMES_PARTICIPANT": "HERMES_participant", "METAT": "HERMES_participant", "HERMES_METAT": "HERMES_participant",
    "EXPRESSED_PREFERENCE": "HERMES_hasPreference", "HERMES_HASPREFERENCE": "HERMES_hasPreference",
    "MADE_DECISION": "HERMES_hasDecision", "HERMES_HASDECISION": "HERMES_hasDecision",
    "AT_LOCATION": "HERMES_locatedAt", "HERMES_EVENTLOCATION": "HERMES_locatedAt", "HERMES_LOCATEDAT": "HERMES_locatedAt",
    "LINKED_TO": "LINKED_TO", "CAUSED_BY": "CAUSED_BY", "RELATED_TO": "RELATED_TO",
    "OWL_SAMEAS": "owl_sameAs", "OWL:SAMEAS": "owl_sameAs",
}
RELATION_ENDPOINTS = {
    "HERMES_knows": ({"Person"}, {"Person"}),
    "HERMES_participant": ({"Person"}, {"Event"}),
    "HERMES_hasPreference": ({"Person"}, {"Preference"}),
    "HERMES_hasDecision": ({"Person"}, {"Decision"}),
    "HERMES_locatedAt": ({"Person", "Event"}, {"Location"}),
}
PROPERTY_ALIASES = {
    "description": "hermes_description", "note": "hermes_note", "event_type": "hermes_eventType", "eventType": "hermes_eventType",
    "time": "hermes_eventTime", "event_time": "hermes_eventTime", "event:time": "hermes_eventTime",
    "end_time": "hermes_eventEndTime", "event_end_time": "hermes_eventEndTime",
    "time_precision": "hermes_timePrecision", "event_status": "hermes_eventStatus",
    "alias_names": "hermes_aliasNames", "semantic_scope": "hermes_semanticScope",
    "status": "hermes_status", "status_note": "hermes_statusNote",
    "pref_key": "hermes_preferenceKey", "key": "hermes_preferenceKey", "pref_value": "hermes_preferenceValue", "value": "hermes_preferenceValue",
    "preference_key": "hermes_preferenceKey", "preference_value": "hermes_preferenceValue",
    "decision": "hermes_decisionText", "decision_text": "hermes_decisionText", "decision_context": "hermes_decisionContext",
    "latitude": "geo_lat", "hermes_lat": "geo_lat", "longitude": "geo_long", "hermes_lng": "geo_long",
    "label": "hermes_relLabel", "rel_label": "hermes_relLabel",
}
RESERVED_PROPERTIES = {"uri", "hermes_updatedAt", "hermes_createdAt", "hermes_sourceSession", "hermes_sourceSessions", "hermes_extractionIds", "hermes_aliasConfirmed", "hermes_canonicalUri"}
RESERVED_PROPERTIES |= {
    "hermes_revision", "hermes_userConfirmed", "hermes_origin", "hermes_evidenceJson",
    "hermes_archivedByWorkbench", "hermes_lastOperationId", "hermes_archiveReason",
}
TEXT_PROPERTIES = {"foaf_name", "hermes_name", "hermes_description", "hermes_note", "hermes_preferenceKey", "hermes_preferenceValue", "hermes_decisionText", "hermes_decisionContext", "hermes_relLabel", "hermes_timePrecision", "hermes_eventStatus", "hermes_semanticScope", "hermes_status", "hermes_statusNote"}


def entity_type(value):
    key = str(value).split(":")[-1].lower()
    if key not in ENTITY_LABELS:
        raise ValueError("Unsupported KG entity type. Allowed types: " + ", ".join(ENTITY_LABELS) + ". Use Concept for a supported abstract concept; do not invent a new type.")
    return key


def canonical_urn(value, type_=None, *, prefix="urn:hermes:kg:"):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Entity identifier is required")
    value = value.strip()
    kind = entity_type(type_) if type_ else None
    if value.startswith("urn:"):
        if not value.startswith(prefix):
            # External endpoints retain their own namespace but must have the same typed form.
            match = re.fullmatch(r"(urn:[A-Za-z0-9_-]+:kg:)([A-Za-z]+)[-:]([A-Za-z0-9][A-Za-z0-9_.-]*)", value)
            if not match:
                raise ValueError("Malformed external KG URI")
            external_kind = entity_type(match.group(2))
            if kind and external_kind != kind:
                raise ValueError("Entity type disagrees with URI")
            return match.group(1) + external_kind + "-" + match.group(3)
        value = value[len(prefix):]
        match = re.fullmatch(r"([A-Za-z]+)[-:]([A-Za-z0-9][A-Za-z0-9_.-]*)", value)
        if not match:
            raise ValueError("KG URI must contain a known type and a valid identifier")
        actual_kind = entity_type(match.group(1))
        if kind and kind != actual_kind:
            raise ValueError("Entity type disagrees with URI")
        kind, value = actual_kind, match.group(2)
    elif kind is None:
        raise ValueError("A type is required for a local entity identifier")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ValueError("Entity identifiers require ASCII letters/digits and ._- separators")
    return f"{prefix}{kind}-{value}"


def normalize_rel(value):
    key = str(value).lstrip(":").upper()
    if key not in RELATION_ALIASES:
        raise ValueError("Unsupported KG relationship type. Allowed names/aliases: " + ", ".join(sorted(set(RELATION_ALIASES)|set(RELATION_ALIASES.values()))) + ". Both typed endpoints must exist or be created in the same batch.")
    return RELATION_ALIASES[key]


def parse_time(value, tz="Asia/Shanghai"):
    # Database reads return neo4j.time.DateTime/Date rather than stdlib objects.
    # Corrections of an unrelated field must preserve those stored timestamps.
    if callable(getattr(value, "to_native", None)):
        value = value.to_native()
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Event time must be a valid ISO 8601 date/time") from exc
    else:
        raise ValueError("Event time must be an ISO date/time")
    return parsed.replace(tzinfo=ZoneInfo(tz)) if parsed.tzinfo is None else parsed


def normalize_properties(properties, *, kind=None, tz="Asia/Shanghai"):
    if not isinstance(properties, dict):
        raise ValueError("Entity properties must be an object")
    result = {}
    for key, value in properties.items():
        if not isinstance(key, str):
            raise ValueError("Property names must be strings")
        mapped = PROPERTY_ALIASES.get(key, key.replace(":", "_"))
        if mapped == "name":
            mapped = "foaf_name" if kind == "person" else "hermes_name"
        if mapped in RESERVED_PROPERTIES or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", mapped):
            raise ValueError("Invalid or managed KG property name")
        if value is None:
            continue
        if mapped in TEXT_PROPERTIES and not isinstance(value, str):
            # Preference values frequently arrive as JSON bool/number; represent
            # those explicitly as text while preserving native shared/coordinates.
            if mapped == "hermes_preferenceValue" and isinstance(value, (bool, int, float)):
                import json
                value = json.dumps(value, allow_nan=False)
            else:
                raise ValueError(f"{mapped} must be text")
        if mapped == "shared":
            if isinstance(value, str) and value.lower() in {"true", "false"}:
                value = value.lower() == "true"
            if not isinstance(value, bool):
                raise ValueError("shared must be boolean")
        elif mapped in {"hermes_eventTime", "hermes_eventEndTime"}:
            value = parse_time(value, tz)
        elif mapped == "hermes_timePrecision" and value not in {"day", "datetime", "unknown"}:
            raise ValueError("time_precision must be day, datetime or unknown")
        elif mapped == "hermes_aliasNames":
            if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
                raise ValueError("alias_names must be an array of nonempty names")
        elif mapped in {"geo_lat", "geo_long"}:
            if isinstance(value, bool):
                raise ValueError("Coordinates must be numbers")
            value = float(value)
            bound = 90 if mapped == "geo_lat" else 180
            if not -bound <= value <= bound:
                raise ValueError("Coordinate out of range")
        elif mapped == "hermes_eventType" and (not isinstance(value,str) or value not in EVENT_TYPES):
            raise ValueError("Unsupported event type. props.event_type must be one of: " + ", ".join(sorted(EVENT_TYPES)) + ". For connectivity or health checks use kg_status; do not create a synthetic test event.")
        elif isinstance(value, (dict, tuple, set)):
            raise ValueError("Nested KG properties are not supported")
        elif isinstance(value, list):
            if any(not isinstance(v, (str, bool, int, float)) for v in value):
                raise ValueError("KG arrays must contain primitive non-null values")
            if len({type(v) for v in value}) > 1:
                raise ValueError("KG arrays must have one value type")
        elif not isinstance(value, (str, bool, int, float, date, datetime)):
            raise ValueError("Unsupported KG property value")
        result[mapped] = value
    return result


def validate_event_timing(properties, tz="Asia/Shanghai"):
    """A date is required unless an occurred event explicitly has unknown timing."""
    if properties.get("hermes_timePrecision") == "unknown":
        if properties.get("hermes_eventStatus") != "occurred":
            raise ValueError("Unknown event time requires event_status=occurred")
        if properties.get("hermes_eventTime") is not None or properties.get("hermes_eventEndTime") is not None:
            raise ValueError("Unknown event time must not retain a start or end timestamp")
        return
    value = properties.get("hermes_eventTime")
    start = parse_time(value.to_native() if hasattr(value, "to_native") else value, tz)
    value = properties.get("hermes_eventEndTime")
    if value is not None:
        end = parse_time(value.to_native() if hasattr(value, "to_native") else value, tz)
        if end <= start:
            raise ValueError("Exclusive event end_time must be later than event_time")


def prepare_entity(entity, config):
    if not isinstance(entity, dict):
        raise ValueError("Each entity must be an object")
    kind = entity_type(entity.get("type", ""))
    urn = canonical_urn(entity.get("uri") or entity.get("urn") or entity.get("id"), kind, prefix=config.urn_prefix)
    if not urn.startswith(config.urn_prefix):
        raise ValueError("Cannot write entities outside this profile's namespace")
    raw_properties=entity.get("properties",entity.get("props",{}))
    if not isinstance(raw_properties,dict):
        raise ValueError("Entity props must be an object; provide type-specific fields inside props.")
    raw = dict(raw_properties)
    raw.update({k: v for k, v in entity.items() if k.startswith(("hermes_", "foaf_", "geo_")) or k in PROPERTY_ALIASES or k == "shared"})
    props = normalize_properties(raw, kind=kind, tz=config.timezone)
    name_key = "foaf_name" if kind == "person" else "hermes_name"
    name = entity.get("name", props.get(name_key))
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Entity name is required")
    props[name_key] = name.strip()
    required = ENTITY_REQUIRED_PROPERTIES.get(kind, ())
    if kind == "event" and props.get("hermes_timePrecision") == "unknown":
        required = ()
    missing=[key for key in required if key not in props or props[key] == ""]
    if missing:
        fields=", ".join("props."+PREFERRED_PROPERTY_NAMES.get(key,key) for key in missing)
        detail=" Use a real ISO 8601 date/time grounded in the event, not the time of this tool call." if kind=="event" else ""
        raise ValueError(f"Missing required {kind} properties: {fields}.{detail}")
    if kind == "event":
        validate_event_timing(props, config.timezone)
    relations = []
    anchor = entity.get("owner") or entity.get("owner_urn")
    if kind in {"preference", "decision"}:
        anchor = anchor or config.user_person_urn
        relations.append({"from": canonical_urn(anchor, "person", prefix=config.urn_prefix), "to": urn, "type": "HERMES_hasPreference" if kind == "preference" else "HERMES_hasDecision"})
    if kind == "event" and (entity.get("agent") or entity.get("agent_urn")):
        relations.append({"from": canonical_urn(entity.get("agent") or entity.get("agent_urn"), "person", prefix=config.urn_prefix), "to": urn, "type": "HERMES_participant"})
    return {"uri": urn, "type": kind, "label": ENTITY_LABELS[kind], "properties": props}, relations


def prepare_relation(relation, config):
    if not isinstance(relation, dict):
        raise ValueError("Each relationship must be an object")
    source = canonical_urn(relation.get("from") or relation.get("from_urn"), prefix=config.urn_prefix)
    target = canonical_urn(relation.get("to") or relation.get("to_urn"), prefix=config.urn_prefix)
    rel_type = normalize_rel(relation.get("type") or relation.get("rel_type") or relation.get("relation_type"))
    expected=RELATION_ENDPOINTS.get(rel_type)
    if expected:
        source_label=ENTITY_LABELS[source.rsplit(":",1)[-1].split("-",1)[0]]
        target_label=ENTITY_LABELS[target.rsplit(":",1)[-1].split("-",1)[0]]
        if source_label not in expected[0] or target_label not in expected[1]:
            direction="/".join(sorted(expected[0]))+" -> "+"/".join(sorted(expected[1]))
            raise ValueError(f"Relationship {rel_type} requires {direction}; received {source_label} -> {target_label}. Reverse or correct the relation only when the facts support it.")
    if not source.startswith(config.urn_prefix):
        raise ValueError("Relationships must originate in this profile's namespace")
    props = normalize_properties(relation.get("properties", relation.get("props", {})), tz=config.timezone)
    label = relation.get("label", relation.get("rel_label"))
    if label is not None:
        props["hermes_relLabel"] = str(label)
    return {"from": source, "to": target, "type": rel_type, "properties": props}
