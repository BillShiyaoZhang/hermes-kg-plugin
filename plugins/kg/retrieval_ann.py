"""Disposable, generation-isolated Neo4j ANN experiments.

This is not an authority or a production search endpoint. Inputs are immutable
research snapshots. The index contains no business nodes and returns references
only; callers score them against the same frozen snapshot as exact cosine.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid

from .embedding import normalized_vector


class ResearchVectorIndex:
    def __init__(self, driver, *, database="neo4j", dimensions, profile_fingerprint,
                 indexed_namespace=None):
        if isinstance(dimensions, bool) or not isinstance(dimensions, int) or not 1 <= dimensions <= 4096:
            raise ValueError("Research index dimensions must be between 1 and 4096")
        if not isinstance(profile_fingerprint, str) or not profile_fingerprint:
            raise ValueError("An immutable profile fingerprint is required")
        if indexed_namespace is not None and (not isinstance(indexed_namespace, str)
                                               or not indexed_namespace.strip()):
            raise ValueError("indexed_namespace must be a nonempty string or None")
        self.driver, self.database, self.dimensions = driver, database, dimensions
        self.profile_fingerprint = profile_fingerprint
        self.indexed_namespace = indexed_namespace
        self.run_id = uuid.uuid4().hex
        digest = hashlib.sha256((profile_fingerprint + self.run_id).encode()).hexdigest()[:24]
        # Every identifier interpolated into Cypher is library-generated hex.
        self.label = "KGResearchVector_" + digest
        self.partition_label = "KGResearchPartition_" + digest if indexed_namespace is not None else None
        self.index_label = self.partition_label or self.label
        self.index_name = "kg_research_vector_" + digest
        self._created = False
        self.count = 0
        self.indexed_count = 0

    def _run(self, query, parameters=None, *, write=False):
        with self.driver.session(database=self.database, default_access_mode="WRITE" if write else "READ") as session:
            return [dict(row) for row in session.run(query, parameters or {})]

    def build(self, rows, *, batch_size=250):
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 1000:
            raise ValueError("batch_size must be between 1 and 1000")
        if self._created:
            raise ValueError("A research index can only be built once")
        checked, identities = [], set()
        for row in rows:
            required = ("id", "card_id", "namespace", "kind")
            if any(not isinstance(row.get(key), str) or not row[key] for key in required):
                raise ValueError("Research vector rows require id/card_id/namespace/kind")
            if row["id"] in identities:
                raise ValueError("Duplicate research chunk identity")
            identities.add(row["id"])
            checked.append({key: row[key] for key in required} | {
                "embedding": normalized_vector(row["embedding"], dimensions=self.dimensions),
                "run_id": self.run_id, "profile_fingerprint": self.profile_fingerprint})
        self._created = True  # cleanup must also work if a later batch fails.
        self._run(f"""CREATE VECTOR INDEX {self.index_name} FOR (c:{self.index_label}) ON c.embedding
            OPTIONS {{indexConfig: {{`vector.dimensions`: {self.dimensions},
                                     `vector.similarity_function`: 'cosine'}}}}""", write=True)
        for start in range(0, len(checked), batch_size):
            query = f"UNWIND $rows AS row CREATE (c:{self.label}) SET c=row"
            params = {"rows": checked[start:start + batch_size]}
            if self.partition_label is not None:
                query += (" FOREACH (_ IN CASE WHEN row.namespace=$indexed_namespace THEN [1] ELSE [] END | "
                          f"SET c:{self.partition_label})")
                params["indexed_namespace"] = self.indexed_namespace
            self._run(query, params, write=True)
        self._run("CALL db.awaitIndex($name,120)", {"name": self.index_name})
        self.count = len(checked)
        self.indexed_count = sum(row["namespace"] == self.indexed_namespace for row in checked) if self.partition_label else self.count
        return {"index": self.index_name, "dimensions": self.dimensions,
                "chunks": self.count, "run_id": self.run_id,
                "indexed_namespace": self.indexed_namespace, "indexed_count": self.indexed_count,
                "partition_by_namespace": self.partition_label is not None,
                "profile_fingerprint": self.profile_fingerprint}

    def query(self, vector, *, namespace, kind=None, limit=50, oversample=10, exact_rescore=False):
        if not self._created:
            raise ValueError("Build the research index explicitly before querying")
        if not isinstance(namespace, str) or not namespace:
            raise ValueError("A query namespace is required")
        if self.indexed_namespace is not None and namespace != self.indexed_namespace:
            raise ValueError("Query namespace must match indexed_namespace")
        if kind is not None and kind not in {"entity", "relation", "ontology"}:
            raise ValueError("Unsupported card kind")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if isinstance(oversample, bool) or not isinstance(oversample, int) or not 1 <= oversample <= 100:
            raise ValueError("oversample must be between 1 and 100")
        if not isinstance(exact_rescore, bool):
            raise ValueError("exact_rescore must be boolean")
        if not self.indexed_count:
            return []
        score_expression = "vector.similarity.cosine(c.embedding,$vector)" if exact_rescore else "score"
        return self._run("""CALL db.index.vector.queryNodes($index,$candidates,$vector)
            YIELD node AS c,score
            WHERE c.run_id=$run_id AND c.profile_fingerprint=$profile
                AND c.namespace=$namespace AND ($kind IS NULL OR c.kind=$kind)
            RETURN c.card_id AS card_id,max(""" + score_expression + """) AS score
            ORDER BY score DESC,card_id LIMIT $limit""", {
                "index": self.index_name, "run_id": self.run_id,
                "profile": self.profile_fingerprint, "namespace": namespace,
                "kind": kind, "limit": limit,
                "candidates": min(self.indexed_count, 10000, limit * oversample),
                "vector": normalized_vector(vector, dimensions=self.dimensions)})

    def cleanup(self):
        if not self._created:
            return {"complete": True, "remaining": 0}
        self._run(f"DROP INDEX {self.index_name} IF EXISTS", write=True)
        self._run(f"MATCH (c:{self.label} {{run_id:$run_id}}) DELETE c", {"run_id": self.run_id}, write=True)
        rows = self._run(f"MATCH (c:{self.label}) RETURN count(c) AS remaining")
        remaining = rows[0]["remaining"] if rows else None
        self._created = False
        return {"complete": remaining == 0, "remaining": remaining}


def exact_card_ranking(rows, query_vector, *, namespace, kind=None, limit=50):
    """Prefiltered exact ground truth, with max-chunk aggregation per card."""
    query = normalized_vector(query_vector, dimensions=len(query_vector))
    scores = {}
    for row in rows:
        if row["namespace"] != namespace or (kind is not None and row["kind"] != kind):
            continue
        vector = normalized_vector(row["embedding"], dimensions=len(query))
        score = math.fsum(a * b for a, b in zip(vector, query))
        scores[row["card_id"]] = max(scores.get(row["card_id"], -math.inf), score)
    return [{"card_id": identity, "score": score} for identity, score in
            sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def agreement_at_k(exact, approximate, *, k=50):
    expected = {row["card_id"] for row in exact[:k]}
    return len(expected & {row["card_id"] for row in approximate[:k]}) / len(expected) if expected else None
