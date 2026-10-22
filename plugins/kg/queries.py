"""Read templates shared by the provider and legacy command-line interfaces."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import load_config
from .schema import ENTITY_LABELS, EVENT_TYPES, canonical_urn, entity_type as normalize_entity_type, parse_time
from .store import KGStore


def _visible(alias):
    return f"(({alias}.uri STARTS WITH $prefix OR {alias}.shared=true) AND NOT {alias}:KGLegacyAlias AND NOT {alias}:KGLegacyUnresolved AND NOT {alias}:KGQuarantined)"


def _name(alias):
    return f"coalesce({alias}.foaf_name,{alias}.hermes_name,{alias}['foaf:name'],{alias}['openclaw:hasName'])"


def _owner_lookup(parameter):
    # Resolve a reviewed identity once, within the same read transaction as the
    # owner query. An inaccessible, ambiguous or ordinary archive never resolves.
    return f"""
        OPTIONAL MATCH (owner_alias:KGQuarantined {{uri:${parameter}}})
        WHERE owner_alias.hermes_aliasConfirmed=true
            AND (owner_alias.uri STARTS WITH $prefix OR owner_alias.shared=true)
        OPTIONAL MATCH (canonical_owner:Person)
        WHERE canonical_owner.uri=owner_alias.hermes_canonicalUri AND {_visible('canonical_owner')}
        WITH CASE WHEN count(canonical_owner)=1 THEN head(collect(canonical_owner.uri))
            ELSE ${parameter} END AS resolved_owner
    """


def _time(alias):
    value = f"coalesce({alias}.hermes_eventTime,{alias}['event:time'])"
    return f"CASE WHEN valueType({value}) STARTS WITH 'STRING' THEN datetime({value}) ELSE {value} END"


def _limit(value, default):
    value = default if value is None else value
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 500:
        raise ValueError("Query limit must be an integer from 1 to 500")
    return value


def _exact_uri(value):
    # Preserve supplied case/separators. Only separately confirmed aliases may
    # resolve to a visible canonical target; ordinary legacy aliases stay hidden.
    return value if re.fullmatch(r"urn:[A-Za-z0-9][A-Za-z0-9:._%~-]*", value) else None


def _read(query, params, config, store):
    if store is not None:
        return store.run_read(query, params)
    with KGStore(config) as connection:
        return connection.run_read(query, params)


def query_entities(keyword="", entity_type=None, owner_urn=None, limit=20, *, config=None, store=None):
    config = config or getattr(store, "config", None) or load_config()
    kind = normalize_entity_type(entity_type) if entity_type else None
    params = {"prefix": config.urn_prefix, "keyword": keyword.strip().lower(), "limit": _limit(limit, config.query_limit)}
    clauses = [_visible("n"), "any(label IN labels(n) WHERE label IN $entity_labels)"]
    params["entity_labels"] = [ENTITY_LABELS[kind]] if kind else list(ENTITY_LABELS.values())
    # Include known external labels without permitting arbitrary Cypher labels.
    params["entity_labels"] += ["foaf:Person"] if not kind or kind == "person" else []
    uri = _exact_uri(keyword.strip())
    matched_alias = "CASE WHEN n.uri<>$uri THEN $uri ELSE null END" if uri else "null"
    if uri:
        params["uri"] = uri
        clauses.append("""(n.uri=$uri OR EXISTS {
            MATCH (alias:KGQuarantined {uri:$uri})
            WHERE alias.hermes_aliasConfirmed=true AND alias.hermes_canonicalUri=n.uri
                AND (alias.uri STARTS WITH $prefix OR alias.shared=true)
        })""")
    elif keyword.strip():
        clauses.append(f"any(value IN [{_name('n')},n.hermes_description,n.hermes_note,n.hermes_preferenceKey,n.hermes_preferenceValue,n.hermes_decisionText,n.hermes_decisionContext,n.uri] + coalesce(n.hermes_aliasNames,[]) WHERE toLower(toString(value)) CONTAINS $keyword)")
    if owner_urn:
        params["owner"] = canonical_urn(owner_urn, "person", prefix=config.urn_prefix)
        clauses.append(f"(n.uri=resolved_owner OR EXISTS {{ MATCH (owner:Person {{uri:resolved_owner}})-[:HERMES_participant|HERMES_hasPreference|HERMES_hasDecision]->(n) WHERE {_visible('owner')} }})")
    query = f"""
        {_owner_lookup('owner') if owner_urn else ''}
        MATCH (n) WHERE {' AND '.join(clauses)}
        OPTIONAL MATCH (p:Person)-[:HERMES_participant]->(n)
        WHERE {_visible('p')}
        WITH n, collect(DISTINCT {_name('p')}) AS linked_persons
        ORDER BY n.hermes_updatedAt DESC, n.uri
        LIMIT $limit
        RETURN head([label IN labels(n) WHERE label IN $entity_labels]) AS type,
            n.uri AS uri, {_name('n')} AS name, n.hermes_description AS description,
            n.hermes_note AS note,
            CASE WHEN n.hermes_timePrecision='unknown' THEN null ELSE n.hermes_eventTime END AS time,
            CASE WHEN n.hermes_timePrecision='unknown' THEN null ELSE n.hermes_eventEndTime END AS end_time,
            CASE WHEN coalesce(n.hermes_timePrecision,'datetime')<>'unknown' AND n.hermes_eventEndTime IS NOT NULL THEN true ELSE null END AS end_time_exclusive,
            n.hermes_timePrecision AS time_precision,
            n.hermes_timeYearInferred AS time_year_inferred,
            n.hermes_eventStatus AS event_status, n.hermes_aliasNames AS alias_names,
            n.hermes_semanticScope AS semantic_scope, n.hermes_status AS status,
            n.hermes_statusNote AS status_note,
            {matched_alias} AS matched_alias_uri,
            n.hermes_preferenceKey AS preference_key, n.hermes_preferenceValue AS preference_value,
            n.hermes_decisionText AS decision_text, n.hermes_decisionContext AS decision_context,
            linked_persons
    """
    return _read(query, params, config, store)


def query_events(from_date=None, to_date=None, person_urn=None, event_type=None, limit=50, *, config=None, store=None, exclusive_to=None):
    config = config or getattr(store, "config", None) or load_config()
    limit = _limit(limit, config.query_limit)
    params = {"prefix": config.urn_prefix}
    clauses = [_visible("e")]
    start = parse_time(from_date, config.timezone) if from_date else None
    end = None
    end_is_exclusive = exclusive_to is not None
    if exclusive_to is not None:
        end = parse_time(exclusive_to, config.timezone)
    elif to_date:
        end = parse_time(to_date, config.timezone)
        if isinstance(to_date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", to_date):
            end += timedelta(days=1)
            end_is_exclusive = True
    if start is not None and end is not None and start > end:
        raise ValueError("Event range starts after its end")
    if event_type:
        if event_type not in EVENT_TYPES:
            raise ValueError("Unsupported event type")
        params["event_type"] = event_type
        clauses.append("e.hermes_eventType=$event_type")
    if person_urn:
        params["person"] = canonical_urn(person_urn, "person", prefix=config.urn_prefix)
        clauses.append(f"EXISTS {{ MATCH (owner:Person {{uri:resolved_owner}})-[:HERMES_participant]->(e) WHERE {_visible('owner')} }}")
    # Some external/legacy events have invalid dates or time-only strings. Read only
    # visible/person-filtered candidates and parse in Python, where failures can be
    # handled per row. Cypher datetime() would abort the whole query on one bad row.
    query = f"""
        {_owner_lookup('person') if person_urn else ''}
        MATCH (e:Event) WHERE {' AND '.join(clauses)}
        OPTIONAL MATCH (p:Person)-[:HERMES_participant]->(e)
        WHERE {_visible('p')}
        OPTIONAL MATCH (e)-[:HERMES_locatedAt]->(loc:Location)
        WHERE {_visible('loc')}
        WITH e, collect(DISTINCT {_name('p')}) AS participants,
            collect(DISTINCT {_name('loc')}) AS locations
        RETURN e.uri AS uri, {_name('e')} AS name,
            CASE WHEN e.hermes_timePrecision='unknown' THEN null ELSE coalesce(e.hermes_eventTime,e['event:time']) END AS time,
            CASE WHEN e.hermes_timePrecision='unknown' THEN null ELSE e.hermes_eventEndTime END AS end_time,
            CASE WHEN coalesce(e.hermes_timePrecision,'datetime')<>'unknown' AND e.hermes_eventEndTime IS NOT NULL THEN true ELSE null END AS end_time_exclusive,
            e.hermes_timePrecision AS time_precision,
            e.hermes_timeYearInferred AS time_year_inferred,
            e.hermes_eventStatus AS event_status, e.hermes_aliasNames AS alias_names,
            e.hermes_semanticScope AS semantic_scope, e.hermes_status AS status,
            e.hermes_statusNote AS status_note,
            e.hermes_eventType AS event_type, e.hermes_description AS description,
            participants, locations, head(locations) AS location
    """
    candidates = _read(query, params, config, store)
    results = []
    for candidate in candidates:
        row = dict(candidate)
        unknown = row.get("time_precision") == "unknown"
        raw = None if unknown else row.get("time")
        if unknown:
            row["time"] = None
            row["end_time"] = None
            if start is not None or end is not None:
                continue
            results.append(row)
            continue
        try:
            event_time = parse_time(raw.to_native() if hasattr(raw, "to_native") else raw, config.timezone).astimezone(ZoneInfo(config.timezone))
        except (ValueError, TypeError, OverflowError):
            if start is not None or end is not None:
                continue
            row["time_unparsed"] = str(raw) if raw is not None else None
            row["time"] = None
        else:
            raw_end = row.get("end_time")
            try:
                event_end = parse_time(raw_end.to_native() if hasattr(raw_end, "to_native") else raw_end, config.timezone).astimezone(ZoneInfo(config.timezone)) if raw_end is not None else None
            except (ValueError, TypeError, OverflowError):
                event_end = None
            row["end_time"] = event_end
            if event_end is None and row.get("time_precision") == "day":
                event_end = event_time + timedelta(days=1)
            if event_end is not None and event_end <= event_time:
                event_end = None
            if start is not None and (event_end <= start if event_end is not None else event_time < start):
                continue
            if end is not None and (event_time >= end if end_is_exclusive else event_time > end):
                continue
            row["time"] = event_time
        results.append(row)
    results.sort(key=lambda row: (row["time"] is not None, row["time"].timestamp() if row["time"] is not None else 0, row.get("uri", "")), reverse=True)
    return results[:limit]


def parse_natural_query(question, schema=None, *, config=None, now=None):
    config = config or load_config()
    if not isinstance(question, str) or not question.strip():
        raise ValueError("A query is required")
    q = question.strip()
    lower = q.lower()
    if _exact_uri(q):
        return {"intent": "query_general", "entity_type": None, "person_urn": None,
                "time_range": {"from": None, "to": None, "end_exclusive": True},
                "event_type": None, "keywords": [q]}
    now = now or datetime.now(ZoneInfo(config.timezone))
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo(config.timezone))
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end = None
    if any(k in lower for k in ("上个月", "last month")):
        end = today.replace(day=1)
        start = (end - timedelta(days=1)).replace(day=1)
    elif any(k in lower for k in ("上周", "上星期", "last week")):
        end = today - timedelta(days=today.weekday())
        start = end - timedelta(days=7)
    elif any(k in lower for k in ("本周", "这周", "this week")):
        start, end = today - timedelta(days=today.weekday()), today + timedelta(days=1)
    elif any(k in lower for k in ("昨天", "yesterday")):
        start, end = today - timedelta(days=1), today
    elif any(k in lower for k in ("今天", "today")):
        start, end = today, today + timedelta(days=1)
    elif any(k in lower for k in ("最近", "recent", "lately")):
        start, end = today - timedelta(days=30), today + timedelta(days=1)
    intent, kind = "query_general", None
    if any(k in lower for k in ("见过", "见了谁", "见谁", "met ", "meet ")):
        intent, kind = "query_person_related", "event"
    elif any(k in lower for k in ("去了", "去过", "地点", "where did", "位置")):
        intent, kind = "query_location", "event"
    elif any(k in lower for k in ("做了", "活动", "event", "what did")):
        intent, kind = "query_event", "event"
    elif any(k in lower for k in ("是谁", "谁是", "who is", "who's", "是哪位")):
        intent, kind = "query_person", "person"
    elif any(k in lower for k in ("偏好", "喜好", "preference")):
        intent, kind = "query_preference", "preference"
    elif any(k in lower for k in ("决定", "决策", "decision")):
        intent, kind = "query_decision", "decision"
    elif any(k in lower for k in ("记得", "记忆", "remember", "memory", "知道")):
        intent = "query_memory"
    quoted = re.findall(r'"([^\"]+)"|“([^”]+)”', q)
    keyword = " ".join(a or b for a, b in quoted)
    if not keyword and kind == "person":
        keyword = re.sub(r"^(?:请问|你知道|你认识|请介绍一下|who\s+is\s+|who's\s+|谁是)", "", q, flags=re.I)
        keyword = re.sub(r"(?:是谁|是哪位|是什么人|是做什么的)[？?。.!！吗]*$", "", keyword).strip(" ?？。.!！")
    elif not keyword and intent == "query_general":
        keyword = q
    event_type = next((kind for kind in EVENT_TYPES if re.search(r"\b" + re.escape(kind) + r"\b", lower)), None)
    person = config.user_person_urn if re.search(r"我|\b(?:i|my|me|bill|user)\b", lower) else None
    return {"intent": intent, "entity_type": kind, "person_urn": person,
            "time_range": {"from": start.isoformat() if start else None, "to": end.isoformat() if end else None, "end_exclusive": True},
            "event_type": event_type, "keywords": [keyword] if keyword else []}


def execute_kg_query(parsed, *, config=None, store=None, limit=None):
    config = config or getattr(store, "config", None) or load_config()
    limit = _limit(limit, config.query_limit)
    intent = parsed.get("intent", "query_general")
    person = parsed.get("person_urn")
    times = parsed.get("time_range") or {}
    if intent in {"query_event", "query_location", "query_person_related"}:
        records = query_events(times.get("from"), None, person, parsed.get("event_type"), limit,
                               config=config, store=store, exclusive_to=times.get("to"))
        return records
    keyword = " ".join(parsed.get("keywords", []))
    # Memory summaries use one owned-entity query rather than an invalid UNION or
    # a Cartesian product of preferences, decisions and events.
    if intent == "query_memory":
        return query_entities(keyword, owner_urn=person or config.user_person_urn, limit=limit, config=config, store=store)
    owner = person if intent in {"query_preference", "query_decision"} else None
    return query_entities(keyword, parsed.get("entity_type"), owner, limit, config=config, store=store)


def query_natural(question, *, config=None, store=None, limit=None):
    config = config or getattr(store, "config", None) or load_config()
    parsed = parse_natural_query(question, config=config)
    return {"query": question, "parsed_params": parsed, "kg_results": execute_kg_query(parsed, config=config, store=store, limit=limit)}
