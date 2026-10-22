"""Parameterized graph persistence with atomic batches and durable receipts."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime

from .config import KGConfig, load_config
from .schema import ENTITY_LABELS, RELATION_ENDPOINTS, canonical_urn, normalize_rel, prepare_entity, prepare_relation, validate_event_timing


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=lambda v: v.isoformat() if hasattr(v, "isoformat") else str(v))


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

    def _execute(self, session, mode, work, *args):
        # Managed transactions take timeout on their unit of work, not Query objects.
        def operation(tx):
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

    def write_batch(self, entities, relations, source_session=None, checkpoint=None):
        if not isinstance(entities, list) or not isinstance(relations, list):
            raise ValueError("entities and relations must be arrays")
        if source_session is not None and (not isinstance(source_session, str) or not source_session.strip()):
            raise ValueError("source_session must be a nonempty session ID")
        checkpoint = dict(checkpoint or {})
        if checkpoint and not source_session:
            raise ValueError("A checkpoint requires source_session")
        prepared, extra_relations = [], []
        for entity in entities:
            item, implicit = prepare_entity(entity, self.config)
            prepared.append(item)
            extra_relations.extend(implicit)
        if len({item["uri"] for item in prepared}) != len(prepared):
            raise ValueError("A batch cannot contain the same canonical entity twice")
        prepared_relations = [prepare_relation(rel, self.config) for rel in relations + extra_relations]
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
        digest = hashlib.sha256(_json({"entities": prepared, "relations": prepared_relations, "source_session": source_session, "checkpoint": checkpoint}).encode()).hexdigest()
        snapshot_id = checkpoint.get("snapshot_id") or "manual-" + uuid.uuid4().hex
        extraction_hash = checkpoint.get("extraction_hash") or digest
        if not isinstance(snapshot_id, str) or not snapshot_id or not isinstance(extraction_hash, str) or not extraction_hash:
            raise ValueError("Checkpoint IDs must be nonempty strings")
        for key in ("after_message_id", "last_message_id"):
            if key in checkpoint and (isinstance(checkpoint[key], bool) or not isinstance(checkpoint[key], int) or checkpoint[key] < 0):
                raise ValueError("Checkpoint message IDs must be nonnegative integers")
        if checkpoint.get("last_message_id", 0) < checkpoint.get("after_message_id", 0):
            raise ValueError("Checkpoint cannot move backwards")
        metadata = {"snapshot_id": snapshot_id, "extraction_hash": extraction_hash, "payload_hash": digest, "source_session": source_session, "checkpoint_json": _json(checkpoint), "last_message_id": checkpoint.get("last_message_id")}
        with self.driver.session(database=self.config.database) as session:
            return self._execute(session, "write", self._write_transaction, prepared, prepared_relations, metadata)

    def _write_transaction(self, tx, entities, relations, metadata):
        row = tx.run(self._query("""
            MERGE (r:KGSyncReceipt {snapshot_id:$snapshot_id})
            ON CREATE SET r.extraction_hash=$extraction_hash, r.payload_hash=$payload_hash,
                r.source_session=$source_session, r.checkpoint_json=$checkpoint_json,
                r.last_message_id=$last_message_id, r.complete=false
            RETURN properties(r) AS receipt
        """), metadata).single()
        if not row:
            raise RuntimeError("Could not obtain KG transaction receipt")
        receipt = dict(row["receipt"])
        if receipt.get("extraction_hash") != metadata["extraction_hash"] or receipt.get("payload_hash") != metadata["payload_hash"]:
            raise ValueError("Snapshot already exists with a different extraction payload")
        if receipt.get("complete"):
            return self._receipt_result(receipt, True)
        provenance = {"source_session": metadata["source_session"], "extraction_id": metadata["snapshot_id"]}
        for entity in entities:
            # Match uri globally so a pre-migration node is upgraded instead of duplicated.
            existing = list(tx.run(self._query("MATCH (n {uri:$uri}) RETURN labels(n) AS labels, n.hermes_aliasConfirmed AS alias_confirmed, n.hermes_eventTime AS event_time, n.hermes_eventEndTime AS event_end_time, n.hermes_timePrecision AS time_precision, n.hermes_eventStatus AS event_status"), {"uri": entity["uri"]}))
            if len(existing) > 1:
                raise ValueError("Duplicate URI must be migrated before writing")
            if existing:
                if existing[0].get("alias_confirmed") is True:
                    raise ValueError("A confirmed alias cannot be written; use its reviewed canonical URI")
                if "KGQuarantined" in existing[0]["labels"]:
                    raise ValueError("A content-quarantined entity requires explicit review and restoration before updating")
                if "KGLegacyAlias" in existing[0]["labels"]:
                    raise ValueError("A legacy alias must be resolved before updating its entity")
                known = set(existing[0]["labels"]) & set(ENTITY_LABELS.values())
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
            params = {"uri": entity["uri"], "props": entity["properties"], **provenance}
            recovered = bool(existing and "KGLegacyUnresolved" in existing[0]["labels"])
            recovery_clause = "REMOVE n:KGLegacyUnresolved, n.hermes_repairReason" if recovered else ""
            timing_clause = "REMOVE n.hermes_eventTime, n.hermes_eventEndTime" if entity["properties"].get("hermes_timePrecision") == "unknown" else ""
            if "hermes_eventTime" in entity["properties"] and "hermes_timePrecision" not in entity["properties"]:
                timing_clause = "SET n.hermes_timePrecision=CASE WHEN n.hermes_timePrecision='unknown' THEN 'datetime' ELSE n.hermes_timePrecision END"
            tx.run(self._query(f"""
                MERGE (n {{uri:$uri}})
                ON CREATE SET n.shared=true, n.hermes_createdAt=datetime()
                SET n:KGEntity:{entity['label']}, n += $props, n.hermes_updatedAt=datetime(),
                    n.hermes_sourceSession=coalesce($source_session,n.hermes_sourceSession),
                    n.hermes_sourceSessions=CASE WHEN $source_session IS NULL OR $source_session IN coalesce(n.hermes_sourceSessions,[]) THEN coalesce(n.hermes_sourceSessions,[]) ELSE coalesce(n.hermes_sourceSessions,[])+$source_session END,
                    n.hermes_extractionIds=CASE WHEN $extraction_id IN coalesce(n.hermes_extractionIds,[]) THEN coalesce(n.hermes_extractionIds,[]) ELSE coalesce(n.hermes_extractionIds,[])+$extraction_id END
                {recovery_clause}
                {timing_clause}
            """), params).consume()
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
            if not source_labels.intersection(ENTITY_LABELS.values()) or not target_labels.intersection(ENTITY_LABELS.values()):
                raise ValueError("Relationship endpoints must be typed entities")
            expected = RELATION_ENDPOINTS.get(rel["type"])
            if expected and (not source_labels.intersection(expected[0]) or not target_labels.intersection(expected[1])):
                raise ValueError("Relationship endpoint types violate the graph schema")
            tx.run(self._query(f"""
                MATCH (a {{uri:$from}}), (b {{uri:$to}})
                MERGE (a)-[r:{rel['type']}]->(b)
                SET r += $props, r.hermes_updatedAt=datetime(),
                    r.hermes_sourceSession=coalesce($source_session,r.hermes_sourceSession),
                    r.hermes_sourceSessions=CASE WHEN $source_session IS NULL OR $source_session IN coalesce(r.hermes_sourceSessions,[]) THEN coalesce(r.hermes_sourceSessions,[]) ELSE coalesce(r.hermes_sourceSessions,[])+$source_session END,
                    r.hermes_extractionIds=CASE WHEN $extraction_id IN coalesce(r.hermes_extractionIds,[]) THEN coalesce(r.hermes_extractionIds,[]) ELSE coalesce(r.hermes_extractionIds,[])+$extraction_id END
            """), {"from": rel["from"], "to": rel["to"], "props": rel["properties"], **provenance}).consume()
        receipt.update({"entities_written": len(entities), "relations_written": len(relations), "entity_uris": [e["uri"] for e in entities], "relation_ids": ["|".join((r["from"], r["type"], r["to"])) for r in relations], "complete": True})
        tx.run(self._query("""
            MATCH (r:KGSyncReceipt {snapshot_id:$snapshot_id})
            SET r.entities_written=$entities_written, r.relations_written=$relations_written,
                r.entity_uris=$entity_uris, r.relation_ids=$relation_ids,
                r.complete=true, r.committed_at=datetime()
        """), {**metadata, **{key: receipt[key] for key in ("entities_written", "relations_written", "entity_uris", "relation_ids")}}).consume()
        return self._receipt_result(receipt, False)

    @staticmethod
    def _receipt_result(receipt, idempotent):
        return {"ok": True, "receipt_id": receipt["snapshot_id"], "extraction_hash": receipt["extraction_hash"], "source_session": receipt.get("source_session"), "checkpoint": json.loads(receipt.get("checkpoint_json", "{}")), "idempotent": idempotent, "entities_written": receipt.get("entities_written", 0), "relations_written": receipt.get("relations_written", 0), "entity_uris": receipt.get("entity_uris", []), "relation_ids": receipt.get("relation_ids", [])}

    def delete_entity(self, uri, type_=None):
        uri = canonical_urn(uri, type_, prefix=self.config.urn_prefix)
        if not uri.startswith(self.config.urn_prefix):
            raise ValueError("Cannot delete another namespace's entity")
        with self.driver.session(database=self.config.database) as session:
            return self._execute(session, "write", lambda tx: tx.run(self._query("MATCH (n {uri:$uri}) WITH collect(n) AS nodes FOREACH (n IN nodes | DETACH DELETE n) RETURN size(nodes) AS deleted"), {"uri": uri}).single()["deleted"])

    def delete_relation(self, source, target, rel_type):
        rel = prepare_relation({"from": source, "to": target, "type": rel_type}, self.config)
        with self.driver.session(database=self.config.database) as session:
            query = f"MATCH (a {{uri:$from}})-[r:{rel['type']}]->(b {{uri:$to}}) WITH collect(r) AS edges FOREACH (r IN edges | DELETE r) RETURN size(edges) AS deleted"
            return self._execute(session, "write", lambda tx: tx.run(self._query(query), {"from": rel["from"], "to": rel["to"]}).single()["deleted"])


def write_batch(entities, relations, source_session=None, checkpoint=None, *, config=None):
    with KGStore(config=config) as store:
        return store.write_batch(entities, relations, source_session, checkpoint)
