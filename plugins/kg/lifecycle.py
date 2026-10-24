"""Small, host-independent contracts for memory revisions and review operations."""
from __future__ import annotations

import hashlib
import json
import unicodedata

ORIGINS = {"auto", "manual", "user_correction", "review_approved", "legacy"}
REVIEWED_ORIGINS = {"user_correction", "review_approved"}


def normalized_person_name(value):
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold() if isinstance(value, str) else ""


def corrected_properties(current, changes, kind, config):
    """A user-entered ISO date supersedes the extractor's old precision notes."""
    from .schema import normalize_properties
    updates = normalize_properties(changes, kind=kind, tz=config.timezone)
    if kind == 'event' and 'hermes_eventTime' in updates:
        updates['hermes_timeYearInferred'] = False
        if 'hermes_timePrecision' not in updates:
            from .schema import PROPERTY_ALIASES
            raw = next((value for key, value in changes.items()
                        if PROPERTY_ALIASES.get(key, key.replace(':', '_')) == 'hermes_eventTime'), None)
            import re
            updates['hermes_timePrecision'] = 'day' if isinstance(raw, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw) else 'datetime'
    result = {**current, **updates}
    if result.get('hermes_timePrecision') == 'unknown':
        result.pop('hermes_eventTime', None)
        result.pop('hermes_eventEndTime', None)
        result.pop('hermes_timeYearInferred', None)
    return result


class VersionConflict(ValueError):
    """The caller must refresh before changing a newer memory card."""


class MemoryNotFound(LookupError):
    pass


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      default=lambda item: item.isoformat() if hasattr(item, "isoformat") else str(item))


def revision_id(uri, version):
    return hashlib.sha256(dumps([uri, version]).encode()).hexdigest()


def validate_operation_id(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError("operation_id must contain 1 to 200 characters")
    return value


def validate_version(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("expected_version must be a nonnegative integer")
    return value


def evidence_for(evidence, uri, *, relation=False):
    """Preserve supplied citations, never manufacture provenance for old data."""
    if evidence is None:
        return []
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be an entity/relation citation mapping")
    values = evidence.get("relations" if relation else "entities", {}).get(uri, [])
    if isinstance(values, dict):
        values = [values]
    if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
        raise ValueError("Evidence citations must be objects")
    # JSON round-trip also makes the caller's mutable dictionaries independent.
    return json.loads(dumps(values))


def payload_evidence(payload, config, ontology=None):
    from .schema import prepare_entity, prepare_relation
    result = {"entities": {}, "relations": {}}
    if not payload:
        return result
    for key, prepare in (("entities", prepare_entity), ("relations", prepare_relation)):
        for item in payload.get(key, []):
            prepared = prepare(item, config, ontology)
            if key == "entities":
                identifier = prepared[0]["uri"]
            else:
                identifier = "|".join((prepared["from"], prepared["type"], prepared["to"]))
            citations = ([item["evidence"]] if isinstance(item.get("evidence"), dict) else [])
            citations += item.get("supporting_evidence", [])
            result[key][identifier] = [dict(citation, snapshot_id=payload.get("snapshot_id"),
                                            session_id=payload.get("session_id")) for citation in citations]
    return result


class MemoryService:
    """Memory-card use cases shared by the local web API and host adapters."""

    def __init__(self, config, store=None, review_validator=None):
        from .store import KGStore
        self.config = config
        self.store = store or KGStore(config)
        self.review_validator = review_validator

    def close(self):
        self.store.close()

    def _uri(self, uri):
        from .schema import canonical_urn, schema_for_store
        result = canonical_urn(uri, prefix=self.config.urn_prefix, ontology=schema_for_store(self.store))
        if not result.startswith(self.config.urn_prefix):
            raise ValueError("The workbench only manages this profile's namespace")
        return result

    @staticmethod
    def _card(row):
        from .schema import runtime_kind
        props = dict(row.get("properties", {}))
        kind = runtime_kind(row.get("labels", []))
        return {"uri": props.get("uri"), "type": kind,
            "name": props.get("foaf_name") if kind == "person" else props.get("hermes_name"),
            "props": props, "version": props.get("hermes_revision", 0),
            "origin": props.get("hermes_origin", "legacy"),
            "user_confirmed": props.get("hermes_userConfirmed") is True,
            "archived": "KGQuarantined" in row.get("labels", []),
            "evidence": json.loads(props.get("hermes_evidenceJson", "[]")),
            "updated_at": props.get("hermes_updatedAt")}

    def list_memories(self, *, include_archived=False, entity_type=None, query="", limit=50, offset=0):
        from .schema import runtime_entity_labels, schema_for_store, entity_type as normalize_type
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ValueError("limit must be between 1 and 10000")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be nonnegative")
        ontology = schema_for_store(self.store)
        known = runtime_entity_labels(ontology)
        labels = [known[normalize_type(entity_type, ontology)]] if entity_type else list(known.values())
        rows = self.store.run_read("""MATCH (n)
            WHERE n.uri STARTS WITH $prefix AND any(label IN labels(n) WHERE label IN $labels)
                AND NOT n:KGLegacyAlias AND NOT n:KGLegacyUnresolved
                AND coalesce(n.hermes_aliasConfirmed,false)=false
                AND ($include_archived OR NOT n:KGQuarantined)
                AND ($query='' OR any(value IN [n.foaf_name,n.hermes_name,n.hermes_description,n.uri]
                    WHERE toLower(toString(value)) CONTAINS $query))
            RETURN labels(n) AS labels, properties(n) AS properties
            ORDER BY n.hermes_updatedAt DESC, n.uri SKIP $offset LIMIT $limit""",
            {"prefix": self.config.urn_prefix, "labels": labels, "include_archived": include_archived,
             "query": query.strip().lower(), "offset": offset, "limit": limit})
        return [self._card(row) for row in rows]

    # Keep a straightforward alias for non-HTTP consumers.
    list = list_memories

    def detail(self, uri):
        uri = self._uri(uri)
        from .schema import runtime_entity_labels, schema_for_store
        ontology = schema_for_store(self.store)
        entity_labels = list(runtime_entity_labels(ontology).values())
        rows = self.store.run_read("""MATCH (n {uri:$uri})
            WHERE NOT n:KGLegacyAlias AND NOT n:KGLegacyUnresolved
                AND any(label IN labels(n) WHERE label IN $entity_labels)
                AND coalesce(n.hermes_aliasConfirmed,false)=false
            RETURN labels(n) AS labels, properties(n) AS properties""",
            {"uri": uri, "entity_labels": entity_labels})
        if len(rows) != 1:
            raise MemoryNotFound("Memory was not found")
        result = self._card(rows[0])
        result["relations"] = self.store.run_read("""MATCH (a)-[r]->(b)
            WHERE (a.uri=$uri OR b.uri=$uri)
                AND (a.uri STARTS WITH $prefix OR a.shared=true)
                AND (b.uri STARTS WITH $prefix OR b.shared=true)
                AND NOT a:KGLegacyAlias AND NOT a:KGLegacyUnresolved
                AND NOT b:KGLegacyAlias AND NOT b:KGLegacyUnresolved
                AND coalesce(a.hermes_aliasConfirmed,false)=false
                AND coalesce(b.hermes_aliasConfirmed,false)=false
            WITH a,b,r,CASE WHEN a.uri=$uri THEN b ELSE a END AS other
            WHERE NOT other:KGQuarantined AND any(label IN labels(other) WHERE label IN $entity_labels)
            RETURN a.uri AS from_urn,b.uri AS to_urn,type(r) AS relation_type,
                coalesce(a.foaf_name,a.hermes_name,a['foaf:name']) AS from_name,
                coalesce(b.foaf_name,b.hermes_name,b['foaf:name']) AS to_name,
                other.uri AS related_uri,coalesce(other.foaf_name,other.hermes_name,other['foaf:name']) AS related_name,
                head([label IN labels(other) WHERE label IN $entity_labels]) AS related_type,
                CASE WHEN other.hermes_timePrecision='unknown' THEN null ELSE other.hermes_eventTime END AS related_time,
                other.hermes_timePrecision AS related_time_precision,
                NOT other.uri STARTS WITH $prefix AS related_read_only,
                properties(r) AS props ORDER BY from_urn,relation_type,to_urn""",
            {"uri": uri, "prefix": self.config.urn_prefix, "entity_labels": entity_labels})
        from .schema import normalize_rel, runtime_kind
        for relation in result["relations"]:
            outgoing = relation["from_urn"] == uri
            labels = {
                "HERMES_knows": "认识", "HERMES_participant": "参与的事件" if outgoing else "参与者",
                "HERMES_hasPreference": "偏好" if outgoing else "所属人物",
                "HERMES_hasDecision": "决策" if outgoing else "决策者",
                "HERMES_locatedAt": "地点" if outgoing else "相关人物或事件",
                "RELATED_TO": "相关", "LINKED_TO": "关联",
                "CAUSED_BY": "原因" if outgoing else "影响", "owl_sameAs": "同一身份",
            }
            try:
                canonical_type = normalize_rel(relation["relation_type"], ontology)
            except ValueError:
                canonical_type = relation["relation_type"]
            definition = next((entry for entry in (ontology or {}).get('relations', []) if entry['key'] == canonical_type), None)
            relation["relation_label"] = (definition['label'] if definition and
                (definition.get('custom') or 'label' in definition.get('customized_fields', [])) else labels.get(canonical_type, "关联"))
            if relation.get("related_type"):
                relation["related_type"] = runtime_kind([relation['related_type']], ontology) or relation["related_type"].lower()
        return result

    def revisions(self, uri, *, limit=100):
        uri = self._uri(uri)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        rows = self.store.run_read("""MATCH (v:KGMemoryRevision {entity_uri:$uri,namespace:$namespace})
            RETURN properties(v) AS revision ORDER BY v.version DESC LIMIT $limit""",
            {"uri": uri, "namespace": self.config.urn_prefix, "limit": limit})
        return [{"revision_id": row["revision"]["revision_id"], "uri": uri,
            "version": row["revision"]["version"], "origin": row["revision"].get("origin", "legacy"),
            "action": row["revision"].get("action", "write"), "reason": row["revision"].get("reason", ""),
            "created_at": row["revision"].get("created_at"),
            "state": json.loads(row["revision"].get("state_json", "{}")),
            "evidence": json.loads(row["revision"].get("evidence_json", "[]"))} for row in rows]

    @staticmethod
    def _review(row):
        value = row["review"]
        return {"id": value["review_id"], "status": value["status"],
            "origin": value.get("origin", "auto"),
            "reason_codes": value.get("reason_codes", []),
            "possible_duplicate_uris": value.get("possible_duplicate_uris", []),
            "base_versions": json.loads(value.get("base_versions_json", "{}")),
            "snapshot_id": value.get("snapshot_id"), "source_session": value.get("source_session"),
            "payload": json.loads(value["payload_json"]),
            "ontology_aliases": json.loads(value.get("ontology_aliases_json", "{}")),
            "evidence": json.loads(value.get("evidence_json", "{}")),
            "payload_hash": value["payload_hash"], "created_at": value.get("created_at"),
            "resolved_at": value.get("resolved_at"), "resolution_reason": value.get("resolution_reason")}

    def reviews(self, *, status="pending", limit=100):
        if status not in {"pending", "approved", "rejected", "all"}:
            raise ValueError("Invalid review status")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        rows = self.store.run_read("""MATCH (b:KGReviewBatch {namespace:$namespace})
            WHERE $status='all' OR b.status=$status
            RETURN properties(b) AS review ORDER BY b.created_at DESC LIMIT $limit""",
            {"namespace": self.config.urn_prefix, "status": status, "limit": limit})
        return [self._review(row) for row in rows]

    def query_reviews(self, *, q="", offset=0, limit=20):
        """Search the complete pending inbox before returning a bounded page."""
        if not isinstance(q, str) or len(q) > 200:
            raise ValueError('q must be text of at most 200 characters')
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 1_000_000:
            raise ValueError('offset must be an integer from 0 to 1000000')
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError('limit must be an integer from 1 to 50')
        params = {'namespace': self.config.urn_prefix, 'query': q.strip().lower(), 'offset': offset, 'limit': limit}
        match = "MATCH (b:KGReviewBatch {namespace:$namespace}) WHERE b.status='pending'"
        selected = """($query='' OR any(value IN [b.review_id,b.payload_json,b.evidence_json]
            + coalesce(b.reason_codes,[]) WHERE toLower(toString(value)) CONTAINS $query))"""
        counts = self.store.run_read(match + f''' RETURN count(b) AS total_pending,
            sum(CASE WHEN {selected} THEN 1 ELSE 0 END) AS total''', params)[0]
        rows = self.store.run_read(match + f''' AND {selected}
            RETURN properties(b) AS review ORDER BY b.created_at DESC,b.review_id
            SKIP $offset LIMIT $limit''', params)
        return {'items': [self._review(row) for row in rows],
                'total': counts['total'], 'total_pending': counts['total_pending'],
                'offset': offset, 'limit': limit, 'has_more': offset + limit < counts['total']}

    def review(self, review_id):
        rows = self.store.run_read("""MATCH (b:KGReviewBatch {review_id:$review_id,namespace:$namespace})
            RETURN properties(b) AS review""", {"review_id": review_id, "namespace": self.config.urn_prefix})
        if len(rows) != 1:
            raise MemoryNotFound("Review batch was not found")
        return self._review(rows[0])

    def _request(self, operation_id, intent):
        validate_operation_id(operation_id)
        digest = hashlib.sha256(dumps(intent).encode()).hexdigest()
        rows = self.store.run_read("""MATCH (r:KGSyncReceipt {snapshot_id:$snapshot_id,namespace:$namespace})
            WHERE r.complete=true RETURN properties(r) AS receipt""",
            {"snapshot_id": "operation-" + operation_id, "namespace": self.config.urn_prefix})
        if rows:
            receipt = rows[0]["receipt"]
            if receipt.get("client_request_hash") != digest:
                raise ValueError("operation_id already exists with different arguments")
            return digest, self.store._receipt_result(receipt, True)
        return digest, None

    def correct(self, uri, changes, *, expected_version, operation_id, reason=""):
        from .schema import RESERVED_PROPERTIES, normalize_properties
        uri = self._uri(uri)
        validate_version(expected_version)
        if not isinstance(changes, dict) or not set(changes) <= {"name", "props"}:
            raise ValueError("Correction accepts name and props only")
        request_hash, cached = self._request(operation_id, ["correct", uri, changes, expected_version, reason])
        if cached:
            return cached
        current = self.detail(uri)
        if current["archived"]:
            raise ValueError("Restore the memory before correcting it")
        if current["version"] != expected_version:
            raise VersionConflict("Memory version changed; refresh before correcting it")
        props = {key: value for key, value in current["props"].items() if key not in RESERVED_PROPERTIES}
        from .ontology_editing import canonical_input, aliases_for
        state = self.store.ontology_overlay() if callable(getattr(self.store, 'ontology_overlay', None)) else {'version': 0, 'patch': {}}
        if set(props) & set(aliases_for(state)['properties']):
            raise VersionConflict('Ontology alias conflicts with stored literal fields; withdraw conflicting alias before correcting')
        canonical_changes = canonical_input({'type': current['type'], **changes}, state)
        updates = canonical_changes.get('props', {})
        props = corrected_properties(props, updates, current['type'], self.config)
        name_key = 'foaf_name' if current['type'] == 'person' else 'hermes_name'
        entity = {"type": current["type"], "uri": uri, "name": changes.get("name", updates.get(name_key, current["name"])), "props": props}
        return self.store.write_batch([entity], [], origin="user_correction", evidence={"entities": {uri: [
            {"kind": "user_edit", "operation_id": operation_id, "changes": changes, "reason": reason}]}},
            expected_versions={uri: expected_version}, operation_id=operation_id,
            reason=reason, request_hash=request_hash, include_implicit_relations=False,
            expected_ontology_version=state['version'])

    def archive(self, uri, *, expected_version, operation_id, reason=""):
        return self._transition(uri, "archive", expected_version, operation_id, reason)

    def restore(self, uri, *, expected_version, operation_id, reason=""):
        return self._transition(uri, "restore", expected_version, operation_id, reason)

    def _transition(self, uri, action, expected_version, operation_id, reason):
        uri = self._uri(uri)
        request_hash, cached = self._request(operation_id, [action, uri, expected_version, reason])
        if cached:
            return cached
        return self.store.transition_memory(uri, action, expected_version=expected_version,
            operation_id=operation_id, reason=reason, request_hash=request_hash)

    def _validate_review(self, payload, origin="auto"):
        if self.review_validator:
            self.review_validator(payload)
        elif payload.get("snapshot_id"):
            from .sync_state import SyncState
            from .extraction import validate_extraction
            state = SyncState(self.config.home)
            snapshot = state.get_snapshot(snapshot_id=payload["snapshot_id"])
            if snapshot.get("source") == "legacy-jsonl":
                state._legacy_reader(snapshot).validate_snapshot(snapshot)
            else:
                state.source.validate_snapshot(snapshot)
            checked = validate_extraction(dumps(payload), snapshot, self.config)
            if checked != payload:
                raise ValueError("Review payload requires an explicit schema migration")
        elif origin == "auto":
            raise ValueError("An automatic review requires its original durable snapshot")

    def resolve_review(self, review_id, action, *, operation_id, expected_versions=None, reason="",
                       edited_entities=None, changes=None):
        if action not in {"approve", "reject"}:
            raise ValueError("Review action must be approve or reject")
        edits = edited_entities if edited_entities is not None else changes
        if edits and (action != "approve" or not isinstance(edits, dict)):
            raise ValueError("Entity edits are only allowed when approving a review")
        request_hash, cached = self._request(operation_id, ["review", review_id, action, expected_versions, reason, edits])
        if cached:
            return cached
        review = self.review(review_id)
        payload = review["payload"]
        from .ontology_editing import canonical_input, alias_snapshot_state
        state = self.store.ontology_overlay() if callable(getattr(self.store, 'ontology_overlay', None)) else {'version': 0, 'patch': {}}
        if action == "approve":
            frozen = alias_snapshot_state(review.get('ontology_aliases', {}))
            canonical_payload = {**payload, 'entities': [canonical_input(item, frozen) for item in payload['entities']],
                                 'relations': [canonical_input(item, frozen, relation=True) for item in payload['relations']]}
            # An old batch without a snapshot may contain literal properties
            # which a newly introduced alias would otherwise reinterpret.
            if (any(canonical_input(item, state) != item for item in canonical_payload['entities'])
                    or any(canonical_input(item, state, relation=True) != item for item in canonical_payload['relations'])):
                raise VersionConflict('Review aliases changed; reject and regenerate this batch before approval')
            self._validate_review(canonical_payload, review["origin"])
        entities = json.loads(dumps(canonical_payload["entities"])) if action == "approve" else []
        relations = canonical_payload["relations"] if action == "approve" else []
        evidence = json.loads(dumps(review["evidence"]))
        if edits:
            from .schema import prepare_entity, normalize_properties
            from .ontology_editing import effective_schema
            ontology = effective_schema(self.config, state)
            by_uri = {prepare_entity(canonical_input(item, state), self.config, ontology)[0]["uri"]: item for item in entities}
            if not set(edits) <= set(by_uri):
                raise ValueError("Review edits cannot add or rename entity identifiers")
            for uri, edit in edits.items():
                if not isinstance(edit, dict) or not set(edit) <= {"name", "props"}:
                    raise ValueError("Review edits accept name and props only")
                item = by_uri[uri]
                prepared, _ = prepare_entity(canonical_input(item, state), self.config, ontology)
                canonical_edit = canonical_input({'type': prepared['type'], **edit}, state)
                updates = canonical_edit.get('props', {})
                props = corrected_properties(prepared['properties'], updates, prepared['type'], self.config)
                name_key = 'foaf_name' if prepared['type'] == 'person' else 'hermes_name'
                edited = {"type": prepared["type"], "uri": uri,
                    "name": edit.get("name", updates.get(name_key, prepared['properties'][name_key])), "props": props}
                edited.update({key: item[key] for key in ("owner", "owner_urn", "agent", "agent_urn") if key in item})
                item.clear()
                item.update(edited)
                # The old quotation is background, not evidence for the user's edit.
                background = evidence_for(evidence, uri)
                evidence.setdefault("entities", {})[uri] = [dict(citation, kind="source_background") for citation in background]
                evidence["entities"][uri].append({"kind": "user_edit", "operation_id": operation_id,
                    "changes": edit, "reason": reason})
                item.pop("evidence", None)
                item.pop("supporting_evidence", None)
                prepare_entity(item, self.config, ontology)
        versions = dict(review["base_versions"] if expected_versions is None else expected_versions) if action == "approve" else {}
        if action == "approve" and set(versions) != set(review["base_versions"]):
            raise ValueError("Review approval requires current versions for every original endpoint")
        return self.store.write_batch(entities, relations,
            source_session=review.get("source_session"), origin="review_approved" if action == "approve" else "manual",
            evidence=evidence, expected_versions=versions, operation_id=operation_id,
            reason=reason, request_hash=request_hash,
            review_payload=canonical_payload if action == "approve" and not edits else None,
            review_resolution={"review_id": review_id, "action": action, "payload_hash": review["payload_hash"]},
            expected_ontology_version=state['version'] if action == 'approve' else None)
