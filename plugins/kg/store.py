"""Parameterized graph persistence with atomic batches and durable receipts."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from .config import KGConfig, load_config
from .schema import (ENTITY_LABELS, RELATION_ENDPOINTS, canonical_urn, normalize_rel,
                     prepare_entity, prepare_relation, validate_event_timing,
                     runtime_entity_labels, runtime_kind, schema_for_store)
from .lifecycle import (ORIGINS, REVIEWED_ORIGINS, MemoryNotFound, VersionConflict,
                        evidence_for, payload_evidence, revision_id, validate_operation_id,
                        validate_version, normalized_person_name)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=lambda v: v.isoformat() if hasattr(v, "isoformat") else str(v))


def _safe_temporals(value):
    """Keep named ZoneInfo objects away from the driver's custom DateTime.

    Neo4j 5.28's DateTime calls tzinfo methods with its own datetime class;
    CPython's ZoneInfo C implementation can segfault on that object. Preserve
    the wall-clock components and actual offset, including a native DST fold.
    A driver DateTime keeps its nanoseconds via replace rather than to_native.
    """
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            offset = value.utcoffset()
            if offset is not None:
                return value.replace(tzinfo=timezone(offset))
        return value
    if type(value).__module__.startswith('neo4j.time'):
        if type(value).__name__ == 'DateTime' and value.tzinfo is not None:
            native = value.to_native()
            offset = native.utcoffset()
            if offset is not None:
                return value.replace(tzinfo=timezone(offset))
        # Date/Time/Duration are native Bolt values. In particular Duration is
        # tuple-like and must not be converted into an ordinary array below.
        return value
    if isinstance(value, dict):
        return {key: _safe_temporals(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_temporals(item) for item in value]
    return value


class _SafeResult:
    def __init__(self, result):
        self._result = result

    @staticmethod
    def _row(row):
        return None if row is None else {key: _safe_temporals(value) for key, value in dict(row).items()}

    def __iter__(self):
        return (self._row(row) for row in self._result)

    def single(self, *args, **kwargs):
        return self._row(self._result.single(*args, **kwargs))

    def __getattr__(self, key):
        return getattr(self._result, key)


class _SafeTransaction:
    def __init__(self, transaction):
        self._transaction = transaction

    def run(self, query, parameters=None, **kwargs):
        return _SafeResult(self._transaction.run(query, _safe_temporals(parameters),
            **_safe_temporals(kwargs)))

    def __getattr__(self, key):
        return getattr(self._transaction, key)


class KGStore:
    def __init__(self, config=None, driver=None):
        self.config = config or load_config()
        self._driver = driver
        self._owns_driver = driver is None

    @property
    def driver(self):
        if self._driver is None:
            try:
                from neo4j import GraphDatabase
            except ImportError as exc:
                raise RuntimeError("KG dependency neo4j is unavailable; install the KG plugin's locked dependencies") from exc
            auth = (self.config.user, self.config.password) if self.config.user else None
            self._driver = GraphDatabase.driver(
                self.config.neo4j_url, auth=auth,
                connection_timeout=self.config.connection_timeout,
                connection_acquisition_timeout=self.config.connection_timeout,
                max_transaction_retry_time=0,
            )
        return self._driver

    def close(self):
        if self._driver is not None and self._owns_driver:
            self._driver.close()
            self._driver = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _query(self, text):
        return text

    def _execute(self, session, mode, work, *args, maintenance=False):
        # Managed transactions take timeout on their unit of work, not Query objects.
        if mode == "write" and not maintenance:
            from .maintenance import assert_not_restoring
            assert_not_restoring(self.config.home)
        def operation(tx):
            tx = _SafeTransaction(tx)
            if mode == "write":
                workspace = tx.run("""MERGE (w:KGWorkspace {namespace:$namespace})
                    SET w.lock_counter=coalesce(w.lock_counter,0)+1
                    RETURN coalesce(w.restore_pending,false) AS restore_pending""",
                    {"namespace": self.config.urn_prefix}).single()
                if not maintenance:
                    from .maintenance import RestoreInProgress, assert_not_restoring
                    if workspace and workspace.get("restore_pending"):
                        raise RestoreInProgress("This namespace has an incomplete restore; resume it before writing")
                    assert_not_restoring(self.config.home)
            return work(tx, *args)
        try:
            from neo4j import unit_of_work
        except ImportError:
            pass
        else:
            operation = unit_of_work(timeout=self.config.query_timeout)(operation)
        return getattr(session, "execute_" + mode)(operation)

    def ensure_schema(self):
        """Explicit setup operation; normal reads/writes never run schema DDL."""
        with self.driver.session(database=self.config.database) as session:
            for statement in (
                "CREATE CONSTRAINT kg_entity_uri IF NOT EXISTS FOR (n:KGEntity) REQUIRE n.uri IS UNIQUE",
                "CREATE CONSTRAINT kg_sync_receipt IF NOT EXISTS FOR (r:KGSyncReceipt) REQUIRE r.snapshot_id IS UNIQUE",
                "CREATE CONSTRAINT kg_memory_revision IF NOT EXISTS FOR (r:KGMemoryRevision) REQUIRE r.revision_id IS UNIQUE",
                "CREATE CONSTRAINT kg_review_batch IF NOT EXISTS FOR (r:KGReviewBatch) REQUIRE r.review_id IS UNIQUE",
                "CREATE CONSTRAINT kg_workspace_namespace IF NOT EXISTS FOR (w:KGWorkspace) REQUIRE w.namespace IS UNIQUE",
                "CREATE CONSTRAINT kg_ontology_schema_namespace IF NOT EXISTS FOR (s:KGOntologySchema) REQUIRE s.namespace IS UNIQUE",
                "CREATE CONSTRAINT kg_ontology_revision IF NOT EXISTS FOR (r:KGOntologyRevision) REQUIRE (r.namespace,r.version) IS UNIQUE",
                "CREATE CONSTRAINT kg_ontology_receipt IF NOT EXISTS FOR (r:KGOntologyReceipt) REQUIRE (r.namespace,r.operation_id) IS UNIQUE",
            ):
                session.run(self._query(statement)).consume()

    def run_read(self, query, params=None):
        # Only library-owned read templates should reach this API. Also reject accidental
        # write/procedure clauses, independently of the driver's routing access mode.
        clean = re.sub(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:``|[^`])*`|/\*.*?\*/|//[^\n]*", " ", query, flags=re.S)
        if re.search(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|CALL|LOAD|FOREACH)\b", clean, re.I):
            raise ValueError("run_read accepts read-only templates")
        with self.driver.session(database=self.config.database, default_access_mode="READ") as session:
            return self._execute(session, "read", lambda tx: [dict(row) for row in tx.run(query, params or {})])

    def ontology_overlay(self):
        from .ontology_editing import STATE_QUERY, state_from_rows
        return state_from_rows(self.run_read(STATE_QUERY, {'namespace': self.config.urn_prefix}))

    def ontology_schema(self):
        return schema_for_store(self)

    def write_batch(self, entities, relations, source_session=None, checkpoint=None, *,
                    origin=None, evidence=None, expected_versions=None, operation_id=None,
                    review_payload=None, reason="", request_hash=None, review_resolution=None,
                    include_implicit_relations=True, expected_ontology_version=None):
        if not isinstance(entities, list) or not isinstance(relations, list):
            raise ValueError("entities and relations must be arrays")
        if source_session is not None and (not isinstance(source_session, str) or not source_session.strip()):
            raise ValueError("source_session must be a nonempty session ID")
        checkpoint = dict(checkpoint or {})
        if checkpoint and not source_session:
            raise ValueError("A checkpoint requires source_session")
        origin = origin or ("auto" if checkpoint else "manual")
        if origin not in ORIGINS:
            raise ValueError("Unsupported memory origin")
        if operation_id is not None:
            validate_operation_id(operation_id)
        expected_versions = dict(expected_versions or {})
        if not isinstance(reason, str) or len(reason) > 4000:
            raise ValueError("reason must be text of at most 4000 characters")
        from .ontology_editing import canonical_input, aliases_for, effective_schema
        ontology_state = self.ontology_overlay()
        ontology = effective_schema(self.config, ontology_state)
        for uri, version in expected_versions.items():
            canonical_urn(uri, prefix=self.config.urn_prefix, ontology=ontology)
            validate_version(version)
        if expected_ontology_version is not None:
            validate_version(expected_ontology_version)
            if expected_ontology_version != ontology_state['version']:
                raise ValueError('Ontology version conflict; retry against the current schema')
        prepared, extra_relations = [], []
        for entity in entities:
            item, implicit = prepare_entity(canonical_input(entity, ontology_state), self.config, ontology)
            prepared.append(item)
            extra_relations.extend(implicit)
        if len({item["uri"] for item in prepared}) != len(prepared):
            raise ValueError("A batch cannot contain the same canonical entity twice")
        prepared_relations = [prepare_relation(canonical_input(rel, ontology_state, relation=True), self.config, ontology)
                              for rel in relations + (extra_relations if include_implicit_relations else [])]
        # Identical explicit/implicit owner edges collapse; conflicting properties fail.
        unique_relations = {}
        for rel in prepared_relations:
            key = (rel["from"], rel["type"], rel["to"])
            if key in unique_relations and unique_relations[key]["properties"] != rel["properties"]:
                if rel["properties"] and unique_relations[key]["properties"]:
                    raise ValueError("Conflicting properties for one relationship")
                if not rel["properties"]:
                    continue
            unique_relations[key] = rel
        prepared_relations = list(unique_relations.values())
        reviewed_identifiers = {item["uri"] for item in prepared} | {
            rel[key] for rel in prepared_relations for key in ("from", "to")
            if rel[key].startswith(self.config.urn_prefix)}
        if origin in REVIEWED_ORIGINS and not reviewed_identifiers <= set(expected_versions):
            raise ValueError("Reviewed changes require expected_versions for every entity and local endpoint")
        if review_payload is not None:
            if not isinstance(review_payload, dict) or review_payload.get("entities") != entities or review_payload.get("relations") != relations:
                raise ValueError("review_payload must preserve the exact extraction entities and relations")
            if checkpoint and review_payload.get("snapshot_id") != checkpoint.get("snapshot_id"):
                raise ValueError("Review payload does not match the snapshot")
        evidence_payload = {**review_payload,
                            'entities': [canonical_input(item, ontology_state) for item in review_payload.get('entities', [])],
                            'relations': [canonical_input(item, ontology_state, relation=True) for item in review_payload.get('relations', [])]} if review_payload else None
        evidence = evidence if evidence is not None else payload_evidence(evidence_payload, self.config, ontology)
        for item in prepared:
            evidence_for(evidence, item["uri"])
        digest = hashlib.sha256(_json({"entities": prepared, "relations": prepared_relations, "source_session": source_session, "checkpoint": checkpoint}).encode()).hexdigest()
        snapshot_id = checkpoint.get("snapshot_id") or ("operation-" + operation_id if operation_id else "manual-" + uuid.uuid4().hex)
        extraction_hash = checkpoint.get("extraction_hash") or digest
        if not isinstance(snapshot_id, str) or not snapshot_id or not isinstance(extraction_hash, str) or not extraction_hash:
            raise ValueError("Checkpoint IDs must be nonempty strings")
        for key in ("after_message_id", "last_message_id"):
            if key in checkpoint and (isinstance(checkpoint[key], bool) or not isinstance(checkpoint[key], int) or checkpoint[key] < 0):
                raise ValueError("Checkpoint message IDs must be nonnegative integers")
        if checkpoint.get("last_message_id", 0) < checkpoint.get("after_message_id", 0):
            raise ValueError("Checkpoint cannot move backwards")
        operation_hash = hashlib.sha256(_json({"payload": digest, "origin": origin, "evidence": evidence,
            "expected_versions": expected_versions, "review_payload": review_payload, "reason": reason}).encode()).hexdigest()
        metadata = {"snapshot_id": snapshot_id, "extraction_hash": extraction_hash, "payload_hash": digest,
            "operation_hash": operation_hash, "source_session": source_session, "checkpoint_json": _json(checkpoint),
            "last_message_id": checkpoint.get("last_message_id"), "origin": origin,
            "evidence": evidence, "expected_versions": expected_versions,
            "review_payload": review_payload or {"entities": entities, "relations": relations}, "reason": reason}
        metadata["request_hash"] = request_hash
        metadata['ontology_version'] = ontology_state['version']
        metadata['ontology_aliases'] = aliases_for(ontology_state)
        metadata['entity_labels'] = list(runtime_entity_labels(ontology).values())
        metadata["review_resolution"] = review_resolution
        if review_resolution:
            metadata["operation_hash"] = hashlib.sha256(_json([operation_hash, review_resolution]).encode()).hexdigest()
        with self.driver.session(database=self.config.database) as session:
            return self._execute(session, "write", self._write_transaction, prepared, prepared_relations, metadata)

    def _write_transaction(self, tx, entities, relations, metadata):
        receipt = self._begin_receipt(tx, metadata)
        if receipt.get("complete"):
            return self._receipt_result(receipt, True)
        resolution = metadata.get("review_resolution")
        if resolution:
            self._claim_review(tx, resolution)
            if resolution["action"] == "reject":
                receipt.update(entities_written=0, relations_written=0, entity_uris=[], relation_ids=[], disposition="rejected")
                return self._finish_receipt(tx, metadata, receipt)
        from .ontology_editing import STATE_QUERY, state_from_rows, validate_batch
        ontology_state = state_from_rows(tx.run(STATE_QUERY, {'namespace': self.config.urn_prefix}))
        if metadata.get('ontology_version', ontology_state['version']) != ontology_state['version']:
            raise ValueError('Ontology version conflict; retry against the current schema')
        existing_by_uri = self._preflight(tx, entities, relations, metadata)
        validate_batch(self.config, ontology_state, entities, relations, existing_by_uri)
        reasons = []
        if metadata["origin"] == "auto":
            duplicate_uris = self._possible_duplicate_people(tx, entities)
            if duplicate_uris:
                reasons.append("possible_duplicate_person")
                metadata["possible_duplicate_uris"] = duplicate_uris
        if metadata["origin"] not in REVIEWED_ORIGINS:
            for entity in entities:
                current = existing_by_uri.get(entity["uri"])
                if current and current.get("properties", {}).get("hermes_userConfirmed") is True:
                    if any(current["properties"].get(key) != value for key, value in entity["properties"].items()):
                        reasons.append("user_confirmed_conflict")
                if current and "KGQuarantined" in current["labels"] and metadata["origin"] == "auto":
                    reasons.append("archived_memory")
            for rel in relations:
                for uri in (rel["from"], rel["to"]):
                    current = existing_by_uri.get(uri)
                    if current and current.get("properties", {}).get("hermes_userConfirmed") is True:
                        reasons.append("user_confirmed_relationship")
                    if current and "KGQuarantined" in current["labels"] and metadata["origin"] == "auto":
                        reasons.append("archived_memory")
        if reasons:
            return self._defer(tx, entities, relations, metadata, receipt, reasons, existing_by_uri)
        return self._apply_batch(tx, entities, relations, metadata, receipt, existing_by_uri)

    def _possible_duplicate_people(self, tx, entities):
        people = [item for item in entities if item['label'] == 'Person']
        if not people:
            return []
        rows = [dict(row) for row in tx.run(self._query("""MATCH (p:Person)
            WHERE p.uri STARTS WITH $prefix AND NOT p:KGQuarantined
                AND NOT p:KGLegacyAlias AND NOT p:KGLegacyUnresolved
            RETURN p.uri AS candidate_uri,coalesce(p.foaf_name,p['foaf:name']) AS candidate_name,
                coalesce(p.hermes_aliasNames,[]) AS candidate_aliases"""), {'prefix': self.config.urn_prefix})]
        rows += [{'candidate_uri': item['uri'], 'candidate_name': item['properties'].get('foaf_name'),
                  'candidate_aliases': item['properties'].get('hermes_aliasNames', [])} for item in people]
        collisions = set()
        for person in people:
            names = {normalized_person_name(name) for name in [person['properties'].get('foaf_name'),
                *person['properties'].get('hermes_aliasNames', [])]} - {''}
            for row in rows:
                if row['candidate_uri'] == person['uri']:
                    continue
                other_names = {normalized_person_name(name) for name in [row.get('candidate_name'),
                    *(row.get('candidate_aliases') or [])]} - {''}
                if names & other_names:
                    collisions.add(row['candidate_uri'])
        return sorted(collisions)

    def _begin_receipt(self, tx, metadata):
        row = tx.run(self._query("""
            MERGE (r:KGSyncReceipt {snapshot_id:$snapshot_id})
            ON CREATE SET r.extraction_hash=$extraction_hash, r.payload_hash=$payload_hash,
                r.source_session=$source_session, r.checkpoint_json=$checkpoint_json,
                r.last_message_id=$last_message_id, r.operation_hash=$operation_hash,
                r.client_request_hash=$request_hash, r.namespace=$namespace, r.complete=false
            RETURN properties(r) AS receipt
        """), {**metadata, "namespace": self.config.urn_prefix}).single()
        if not row:
            raise RuntimeError("Could not obtain KG transaction receipt")
        receipt = dict(row["receipt"])
        if receipt.get("namespace") not in (None, self.config.urn_prefix):
            raise ValueError("Receipt belongs to another profile namespace")
        if receipt.get("extraction_hash") != metadata["extraction_hash"] or receipt.get("payload_hash") != metadata["payload_hash"]:
            raise ValueError("Snapshot already exists with a different extraction payload")
        if receipt.get("operation_hash") and receipt["operation_hash"] != metadata["operation_hash"]:
            raise ValueError("Operation already exists with different arguments")
        return receipt

    def _locked_entity(self, tx, uri):
        # A dependent SET acquires the node's write lock before reading its revision.
        # Version zero is the explicit baseline of an existing legacy card.
        lock = "SET n.hermes_revision=coalesce(n.hermes_revision,0)" if uri.startswith(self.config.urn_prefix) else ""
        rows = list(tx.run(self._query(f"""MATCH (n {{uri:$uri}}) {lock}
            RETURN labels(n) AS labels, properties(n) AS properties,
                n.hermes_aliasConfirmed AS alias_confirmed,
                n.hermes_eventTime AS event_time, n.hermes_eventEndTime AS event_end_time,
                n.hermes_timePrecision AS time_precision, n.hermes_eventStatus AS event_status"""), {"uri": uri}))
        if len(rows) > 1:
            raise ValueError("Duplicate URI must be migrated before writing")
        return dict(rows[0]) if rows else None

    def _preflight(self, tx, entities, relations, metadata):
        entity_labels = set(metadata.get('entity_labels', ENTITY_LABELS.values()))
        incoming = {item["uri"]: item for item in entities}
        identifiers = set(incoming) | {rel[key] for rel in relations for key in ("from", "to")}
        current = {uri: self._locked_entity(tx, uri) for uri in sorted(identifiers)}
        for uri, expected in metadata["expected_versions"].items():
            if uri not in identifiers:
                raise ValueError("Expected version names an entity outside this operation")
            actual = (current[uri] or {}).get("properties", {}).get("hermes_revision", 0)
            if actual != expected:
                raise VersionConflict(f"Memory version changed: expected {expected}, current {actual}")
        for uri, node in current.items():
            if not node:
                if uri not in incoming:
                    raise ValueError("Relationship endpoints must exist uniquely and be visible")
                continue
            props, labels = node.get("properties", {}), node["labels"]
            if props.get("hermes_aliasConfirmed") is True or node.get("alias_confirmed") is True:
                if uri in incoming:
                    raise ValueError("A confirmed alias cannot be written; use its reviewed canonical URI")
                raise ValueError("Confirmed aliases cannot be relationship endpoints; use reviewed canonical URIs")
            if "KGLegacyAlias" in labels:
                raise ValueError("A legacy alias must be resolved before updating its entity")
            if "KGQuarantined" in labels and metadata["origin"] != "auto":
                if uri in incoming:
                    raise ValueError("A content-quarantined entity requires explicit review and restoration before updating")
                raise ValueError("Relationship endpoints must not be quarantined or legacy-excluded")
            if uri in incoming:
                known = set(labels) & entity_labels
                if known and known != {incoming[uri]["label"]}:
                    raise ValueError("Existing entity label disagrees with incoming type")
            if not uri.startswith(self.config.urn_prefix) and props.get("shared") is not True:
                raise ValueError("Relationship endpoints must be visible")
        for rel in relations:
            rel['_existing_properties'] = []
            labels = []
            for uri in (rel["from"], rel["to"]):
                node = current[uri]
                known = {incoming[uri]["label"]} if uri in incoming else set((node or {}).get("labels", [])) & entity_labels
                if not known or (node and "KGLegacyUnresolved" in node["labels"] and uri not in incoming):
                    raise ValueError("Relationship endpoints must be typed entities")
                labels.append(known)
            expected = RELATION_ENDPOINTS.get(rel["type"])
            if expected and (not labels[0] & expected[0] or not labels[1] & expected[1]):
                raise ValueError("Relationship endpoint types violate the graph schema")
            if rel['properties']:
                # Keep every matching historical edge: MERGE may match several,
                # and a legacy field must be unchanged on each of those facts.
                rel['_existing_properties'] = [dict(row['properties']) for row in tx.run(self._query(f'''
                    MATCH (a {{uri:$from}})-[r:{rel['type']}]->(b {{uri:$to}})
                    RETURN properties(r) AS properties'''), {'from': rel['from'], 'to': rel['to']})]
        return current

    def _save_revision(self, tx, uri, labels, properties, metadata, action="write"):
        version = properties.get("hermes_revision", 0)
        citations = evidence_for(metadata.get("evidence"), uri)
        outgoing = [dict(row) for row in tx.run(self._query("""MATCH (a {uri:$uri})-[r]->(b)
            WHERE b.uri IS NOT NULL AND (b.uri STARTS WITH $prefix OR b.shared=true)
            RETURN type(r) AS type,b.uri AS to_uri,properties(r) AS properties
            ORDER BY type,to_uri"""), {"uri": uri, "prefix": self.config.urn_prefix})]
        tx.run(self._query("""
            MERGE (v:KGMemoryRevision {revision_id:$revision_id})
            ON CREATE SET v.entity_uri=$uri, v.namespace=$namespace, v.version=$version,
                v.origin=$origin, v.action=$action, v.operation_id=$operation_id,
                v.reason=$reason, v.state_json=$state_json, v.evidence_json=$evidence_json,
                v.created_at=datetime()
        """), {"revision_id": revision_id(uri, version), "uri": uri,
            "namespace": self.config.urn_prefix, "version": version,
            "origin": metadata.get("origin", "legacy"), "action": action,
            "operation_id": metadata["snapshot_id"], "reason": metadata.get("reason", ""),
            "state_json": _json({"labels": labels, "properties": properties, "relations": outgoing}),
            "evidence_json": _json(citations)}).consume()

    def _defer(self, tx, entities, relations, metadata, receipt, reasons, current):
        review_id = "review-" + metadata["snapshot_id"]
        base_versions = {uri: (node or {}).get("properties", {}).get("hermes_revision", 0) for uri, node in current.items() if uri.startswith(self.config.urn_prefix)}
        tx.run(self._query("""
            MERGE (b:KGReviewBatch {review_id:$review_id})
            ON CREATE SET b.namespace=$namespace, b.status='pending', b.snapshot_id=$snapshot_id,
                b.origin=$origin,
                b.extraction_hash=$extraction_hash, b.payload_hash=$payload_hash,
                b.source_session=$source_session, b.payload_json=$payload_json,
                b.evidence_json=$evidence_json, b.base_versions_json=$base_versions_json,
                b.ontology_aliases_json=$ontology_aliases_json,
                b.reason_codes=$reason_codes, b.created_at=datetime(),
                b.possible_duplicate_uris=$possible_duplicate_uris
        """), {**{key: metadata[key] for key in ("snapshot_id", "extraction_hash", "payload_hash", "source_session")},
            "review_id": review_id, "namespace": self.config.urn_prefix,
            "origin": metadata["origin"],
            "payload_json": _json(metadata["review_payload"]), "evidence_json": _json(metadata["evidence"]),
            "base_versions_json": _json(base_versions), "reason_codes": sorted(set(reasons)),
            "ontology_aliases_json": _json(metadata.get('ontology_aliases', {})),
            "possible_duplicate_uris": metadata.get("possible_duplicate_uris", [])}).consume()
        receipt.update(entities_written=0, relations_written=0, entity_uris=[], relation_ids=[],
                       disposition="deferred", review_batch_id=review_id)
        return self._finish_receipt(tx, metadata, receipt)

    def _apply_batch(self, tx, entities, relations, metadata, receipt, existing_by_uri):
        entity_labels = set(metadata.get('entity_labels', ENTITY_LABELS.values()))
        provenance = {"source_session": metadata["source_session"], "extraction_id": metadata["snapshot_id"]}
        for entity in entities:
            # Match uri globally so a pre-migration node is upgraded instead of duplicated.
            existing = [existing_by_uri[entity["uri"]]] if existing_by_uri.get(entity["uri"]) else []
            if len(existing) > 1:
                raise ValueError("Duplicate URI must be migrated before writing")
            if existing:
                if existing[0].get("alias_confirmed") is True:
                    raise ValueError("A confirmed alias cannot be written; use its reviewed canonical URI")
                if "KGQuarantined" in existing[0]["labels"]:
                    raise ValueError("A content-quarantined entity requires explicit review and restoration before updating")
                if "KGLegacyAlias" in existing[0]["labels"]:
                    raise ValueError("A legacy alias must be resolved before updating its entity")
                known = set(existing[0]["labels"]) & entity_labels
                if known and known != {entity["label"]}:
                    raise ValueError("Existing entity label disagrees with incoming type")
            if entity["label"] == "Event":
                # Validate the effective dates too: omitting end_time on an update
                # preserves the stored end, which must remain later than the new start.
                fields = {"event_time": "hermes_eventTime", "event_end_time": "hermes_eventEndTime",
                          "time_precision": "hermes_timePrecision", "event_status": "hermes_eventStatus"}
                effective = {prop: existing[0].get(key) for key, prop in fields.items()} if existing else {}
                effective.update(entity["properties"])
                if entity["properties"].get("hermes_timePrecision") == "unknown":
                    effective.pop("hermes_eventTime", None)
                    effective.pop("hermes_eventEndTime", None)
                elif "hermes_eventTime" in entity["properties"] and "hermes_timePrecision" not in entity["properties"] and effective.get("hermes_timePrecision") == "unknown":
                    effective["hermes_timePrecision"] = "datetime"
                validate_event_timing(effective, self.config.timezone)
            old_props = existing[0].get("properties", {}) if existing else {}
            if existing and old_props.get("hermes_revision", 0) == 0:
                self._save_revision(tx, entity["uri"], existing[0]["labels"], old_props,
                    {"origin": "legacy", "snapshot_id": metadata["snapshot_id"], "evidence": {}}, "legacy_baseline")
            citations = evidence_for(metadata["evidence"], entity["uri"])
            params = {"uri": entity["uri"], "props": entity["properties"], **provenance,
                "revision": old_props.get("hermes_revision", 0) + 1,
                "origin": metadata["origin"], "evidence_json": _json(citations),
                "user_confirmed": metadata["origin"] in REVIEWED_ORIGINS or old_props.get("hermes_userConfirmed") is True,
                "default_shared": getattr(self.config, "default_shared", True)}
            recovered = bool(existing and "KGLegacyUnresolved" in existing[0]["labels"])
            recovery_clause = "REMOVE n:KGLegacyUnresolved, n.hermes_repairReason" if recovered else ""
            timing_clause = "REMOVE n.hermes_eventTime, n.hermes_eventEndTime" if entity["properties"].get("hermes_timePrecision") == "unknown" else ""
            if "hermes_eventTime" in entity["properties"] and "hermes_timePrecision" not in entity["properties"]:
                timing_clause = "SET n.hermes_timePrecision=CASE WHEN n.hermes_timePrecision='unknown' THEN 'datetime' ELSE n.hermes_timePrecision END"
            if entity['label'] == 'Event' and (entity['properties'].get('hermes_timePrecision') == 'unknown' or
                    ('hermes_eventTime' in entity['properties'] and 'hermes_timeYearInferred' not in entity['properties'])):
                timing_clause += '\nREMOVE n.hermes_timeYearInferred'
            tx.run(self._query(f"""
                MERGE (n {{uri:$uri}})
                ON CREATE SET n.shared=$default_shared, n.hermes_createdAt=datetime()
                SET n:KGEntity:{entity['label']}, n += $props, n.hermes_updatedAt=datetime(),
                    n.hermes_revision=$revision, n.hermes_origin=$origin,
                    n.hermes_userConfirmed=$user_confirmed, n.hermes_evidenceJson=$evidence_json,
                    n.hermes_lastOperationId=$extraction_id,
                    n.hermes_sourceSession=coalesce($source_session,n.hermes_sourceSession),
                    n.hermes_sourceSessions=CASE WHEN $source_session IS NULL OR $source_session IN coalesce(n.hermes_sourceSessions,[]) THEN coalesce(n.hermes_sourceSessions,[]) ELSE coalesce(n.hermes_sourceSessions,[])+$source_session END,
                    n.hermes_extractionIds=CASE WHEN $extraction_id IN coalesce(n.hermes_extractionIds,[]) THEN coalesce(n.hermes_extractionIds,[]) ELSE coalesce(n.hermes_extractionIds,[])+$extraction_id END
                {recovery_clause}
                {timing_clause}
            """), params).consume()
        changed_sources = set()
        entity_uris = {entity["uri"] for entity in entities}
        for rel in relations:
            endpoints = list(tx.run(self._query("""
                MATCH (a {uri:$from}), (b {uri:$to})
                WHERE b.uri STARTS WITH $prefix OR b.shared=true
                RETURN labels(a) AS source_labels, labels(b) AS target_labels,
                    a.hermes_aliasConfirmed AS source_alias_confirmed, b.hermes_aliasConfirmed AS target_alias_confirmed
            """), {"from": rel["from"], "to": rel["to"], "prefix": self.config.urn_prefix}))
            if len(endpoints) != 1:
                raise ValueError("Relationship endpoints must exist uniquely and be visible")
            if endpoints[0].get("source_alias_confirmed") is True or endpoints[0].get("target_alias_confirmed") is True:
                raise ValueError("Confirmed aliases cannot be relationship endpoints; use reviewed canonical URIs")
            source_labels = {label.split(":")[-1] for label in endpoints[0]["source_labels"]}
            target_labels = {label.split(":")[-1] for label in endpoints[0]["target_labels"]}
            if (source_labels | target_labels).intersection({"KGQuarantined", "KGLegacyAlias", "KGLegacyUnresolved"}):
                raise ValueError("Relationship endpoints must not be quarantined or legacy-excluded")
            if not source_labels.intersection(entity_labels) or not target_labels.intersection(entity_labels):
                raise ValueError("Relationship endpoints must be typed entities")
            expected = RELATION_ENDPOINTS.get(rel["type"])
            if expected and (not source_labels.intersection(expected[0]) or not target_labels.intersection(expected[1])):
                raise ValueError("Relationship endpoint types violate the graph schema")
            previous_relation = tx.run(self._query(f"""MATCH (a {{uri:$from}})-[r:{rel['type']}]->(b {{uri:$to}})
                RETURN properties(r) AS properties"""), {"from": rel["from"], "to": rel["to"]}).single()
            if previous_relation is None or any(previous_relation["properties"].get(key) != value for key, value in rel["properties"].items()):
                if rel["from"] not in entity_uris and rel["from"] not in changed_sources:
                    old = existing_by_uri[rel["from"]]
                    if old["properties"].get("hermes_revision", 0) == 0:
                        self._save_revision(tx, rel["from"], old["labels"], old["properties"],
                            {"origin": "legacy", "snapshot_id": metadata["snapshot_id"], "evidence": {}}, "legacy_baseline")
                changed_sources.add(rel["from"])
            tx.run(self._query(f"""
                MATCH (a {{uri:$from}}), (b {{uri:$to}})
                MERGE (a)-[r:{rel['type']}]->(b)
                SET r += $props, r.hermes_updatedAt=datetime(),
                    r.hermes_origin=$origin, r.hermes_evidenceJson=$evidence_json,
                    r.hermes_sourceSession=coalesce($source_session,r.hermes_sourceSession),
                    r.hermes_sourceSessions=CASE WHEN $source_session IS NULL OR $source_session IN coalesce(r.hermes_sourceSessions,[]) THEN coalesce(r.hermes_sourceSessions,[]) ELSE coalesce(r.hermes_sourceSessions,[])+$source_session END,
                    r.hermes_extractionIds=CASE WHEN $extraction_id IN coalesce(r.hermes_extractionIds,[]) THEN coalesce(r.hermes_extractionIds,[]) ELSE coalesce(r.hermes_extractionIds,[])+$extraction_id END
            """), {"from": rel["from"], "to": rel["to"], "props": rel["properties"], **provenance,
                "origin": metadata["origin"], "evidence_json": _json(evidence_for(metadata["evidence"],
                    "|".join((rel["from"], rel["type"], rel["to"])), relation=True))}).consume()
        for uri in sorted(changed_sources - entity_uris):
            tx.run(self._query("""MATCH (n {uri:$uri})
                SET n.hermes_revision=coalesce(n.hermes_revision,0)+1,
                    n.hermes_updatedAt=datetime(), n.hermes_lastOperationId=$operation_id"""),
                {"uri": uri, "operation_id": metadata["snapshot_id"]}).consume()
        for uri in sorted(entity_uris | changed_sources):
            updated = self._locked_entity(tx, uri)
            revision_metadata = metadata
            if uri not in entity_uris:
                revision_metadata = {**metadata, "evidence": {"entities": {
                    uri: json.loads(updated["properties"].get("hermes_evidenceJson", "[]"))}}}
            self._save_revision(tx, uri, updated["labels"], updated["properties"], revision_metadata,
                "write" if uri in entity_uris else "relationship_write")
        receipt.update({"entities_written": len(entities), "relations_written": len(relations), "entity_uris": [e["uri"] for e in entities], "relation_ids": ["|".join((r["from"], r["type"], r["to"])) for r in relations], "disposition": "applied"})
        return self._finish_receipt(tx, metadata, receipt)

    def _finish_receipt(self, tx, metadata, receipt):
        resolution = metadata.get("review_resolution")
        if resolution:
            tx.run(self._query("""MATCH (b:KGReviewBatch {review_id:$review_id})
                SET b.status=$status, b.resolved_at=datetime(), b.resolution_operation_id=$operation_id,
                    b.resolution_reason=$reason"""), {"review_id": resolution["review_id"],
                    "status": "approved" if resolution["action"] == "approve" else "rejected",
                    "operation_id": metadata["snapshot_id"], "reason": metadata.get("reason", "")}).consume()
        receipt["complete"] = True
        tx.run(self._query("""
            MATCH (r:KGSyncReceipt {snapshot_id:$snapshot_id})
            SET r.entities_written=$entities_written, r.relations_written=$relations_written,
                r.entity_uris=$entity_uris, r.relation_ids=$relation_ids,
                r.disposition=$disposition, r.review_batch_id=$review_batch_id,
                r.complete=true, r.committed_at=datetime()
        """), {**metadata, **{key: receipt[key] for key in ("entities_written", "relations_written", "entity_uris", "relation_ids", "disposition")}, "review_batch_id": receipt.get("review_batch_id")}).consume()
        return self._receipt_result(receipt, False)

    def _claim_review(self, tx, resolution):
        if resolution.get("action") not in {"approve", "reject"}:
            raise ValueError("Review action must be approve or reject")
        row = tx.run(self._query("""MATCH (b:KGReviewBatch {review_id:$review_id, namespace:$namespace})
            SET b.status=b.status RETURN properties(b) AS review"""),
            {"review_id": resolution["review_id"], "namespace": self.config.urn_prefix}).single()
        if not row:
            raise MemoryNotFound("Review batch was not found")
        batch = row["review"]
        if batch.get("status") != "pending":
            raise VersionConflict("This review batch has already been resolved")
        if batch.get("payload_hash") != resolution.get("payload_hash"):
            raise VersionConflict("Review batch changed; reload it before resolving")

    def transition_memory(self, uri, action, *, expected_version, operation_id, reason="", request_hash=None):
        """Archive/restore without losing content, with one graph transaction and receipt."""
        uri = canonical_urn(uri, prefix=self.config.urn_prefix, ontology=self.ontology_schema())
        if not uri.startswith(self.config.urn_prefix) or action not in {"archive", "restore"}:
            raise ValueError("Invalid memory transition")
        validate_version(expected_version)
        validate_operation_id(operation_id)
        if not isinstance(reason, str) or len(reason) > 4000:
            raise ValueError("reason must be text of at most 4000 characters")
        digest = hashlib.sha256(_json([uri, action, expected_version, reason]).encode()).hexdigest()
        metadata = {"snapshot_id": "operation-" + operation_id, "extraction_hash": digest,
            "payload_hash": digest, "operation_hash": digest, "source_session": None,
            "checkpoint_json": "{}", "last_message_id": None, "request_hash": request_hash,
            "origin": "user_correction", "evidence": {}, "reason": reason}
        with self.driver.session(database=self.config.database) as session:
            return self._execute(session, "write", self._transition_transaction, uri, action, expected_version, metadata)

    def _transition_transaction(self, tx, uri, action, expected_version, metadata):
        receipt = self._begin_receipt(tx, metadata)
        if receipt.get("complete"):
            return self._receipt_result(receipt, True)
        current = self._locked_entity(tx, uri)
        if not current:
            raise MemoryNotFound("Memory was not found")
        props, labels = current["properties"], current["labels"]
        if props.get("hermes_revision", 0) != expected_version:
            raise VersionConflict("Memory version changed; refresh before changing it")
        if not runtime_kind(labels) or set(labels) & {"KGLegacyAlias", "KGLegacyUnresolved"} or props.get("hermes_aliasConfirmed") is True:
            raise ValueError("Legacy aliases and unresolved records require explicit maintenance")
        if action == "restore" and ("KGQuarantined" not in labels or props.get("hermes_archivedByWorkbench") is not True):
            raise ValueError("Only memories archived by the workbench can be restored")
        if action == "archive" and "KGQuarantined" in labels:
            raise ValueError("Memory is already quarantined")
        if expected_version == 0:
            self._save_revision(tx, uri, labels, props,
                {"origin": "legacy", "snapshot_id": metadata["snapshot_id"], "evidence": {}}, "legacy_baseline")
        clause = "SET n:KGQuarantined, n.hermes_archivedByWorkbench=true, n.hermes_archiveReason=$reason" if action == "archive" else "REMOVE n:KGQuarantined, n.hermes_archivedByWorkbench, n.hermes_archiveReason"
        tx.run(self._query(f"""MATCH (n {{uri:$uri}}) {clause}
            SET n.hermes_revision=$revision, n.hermes_updatedAt=datetime(),
                n.hermes_lastOperationId=$operation_id"""), {"uri": uri,
                "reason": metadata["reason"], "revision": expected_version + 1,
                "operation_id": metadata["snapshot_id"]}).consume()
        if action == 'restore':
            from .ontology_editing import STATE_QUERY, state_from_rows, OntologyEditor
            state = state_from_rows(tx.run(STATE_QUERY, {'namespace': self.config.urn_prefix}))
            structural = any(set(fields) & {'domain', 'range', 'required_for'}
                             for group in state['patch'].values() for fields in group.values())
            if structural and OntologyEditor(self.config, self)._impact(tx, state, True)['violations']:
                raise ValueError('Published ontology constraints reject restoration')
        updated = self._locked_entity(tx, uri)
        metadata["evidence"] = {"entities": {uri: json.loads(props.get("hermes_evidenceJson", "[]"))}}
        self._save_revision(tx, uri, updated["labels"], updated["properties"], metadata, action)
        receipt.update(entities_written=1, relations_written=0, entity_uris=[uri], relation_ids=[], disposition="applied")
        return self._finish_receipt(tx, metadata, receipt)

    @staticmethod
    def _receipt_result(receipt, idempotent):
        return {"ok": True, "receipt_id": receipt["snapshot_id"], "extraction_hash": receipt["extraction_hash"], "source_session": receipt.get("source_session"), "checkpoint": json.loads(receipt.get("checkpoint_json", "{}")), "idempotent": idempotent, "entities_written": receipt.get("entities_written", 0), "relations_written": receipt.get("relations_written", 0), "entity_uris": receipt.get("entity_uris", []), "relation_ids": receipt.get("relation_ids", []), "disposition": receipt.get("disposition", "applied"), "review_batch_id": receipt.get("review_batch_id")}

    def delete_entity(self, uri, type_=None):
        uri = canonical_urn(uri, type_, prefix=self.config.urn_prefix, ontology=self.ontology_schema())
        if not uri.startswith(self.config.urn_prefix):
            raise ValueError("Cannot delete another namespace's entity")
        with self.driver.session(database=self.config.database) as session:
            return self._execute(session, "write", lambda tx: tx.run(self._query("MATCH (n {uri:$uri}) WITH collect(n) AS nodes FOREACH (n IN nodes | DETACH DELETE n) RETURN size(nodes) AS deleted"), {"uri": uri}).single()["deleted"])

    def delete_relation(self, source, target, rel_type):
        rel = prepare_relation({"from": source, "to": target, "type": rel_type}, self.config, self.ontology_schema())
        with self.driver.session(database=self.config.database) as session:
            query = f"MATCH (a {{uri:$from}})-[r:{rel['type']}]->(b {{uri:$to}}) WITH collect(r) AS edges FOREACH (r IN edges | DELETE r) RETURN size(edges) AS deleted"
            return self._execute(session, "write", lambda tx: tx.run(self._query(query), {"from": rel["from"], "to": rel["to"]}).single()["deleted"])


def write_batch(entities, relations, source_session=None, checkpoint=None, *, config=None, **kwargs):
    with KGStore(config=config) as store:
        return store.write_batch(entities, relations, source_session, checkpoint, **kwargs)
