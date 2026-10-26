"""Read-only authority fence for experimental retrieval cards.

Cached text is never evidence. Selected sources and actual directed edges are
read twice, rebuilt with the pinned schema, and checked by both content identity
and dependency fingerprint. These are observed read-committed boundaries, not a
serializable snapshot or a relevance/sufficiency judgment. Nothing activates this
experimental path in the production search service.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Callable

from .retrieval_documents import (REPRESENTATIONS, RetrievalDocument, _json,
                                  _schema_fingerprint, dataset_to_documents)
from .schema import runtime_entity_labels, schema_for_store
from .search import _lookup_uri


@dataclass(frozen=True)
class RequestContext:
    namespace: str
    scope: str
    schema_version: int
    schema_fingerprint: str
    model_fingerprint: str
    representation: str
    projection_revision: str
    index_generation: str

    def __post_init__(self):
        for key in ('namespace', 'schema_fingerprint', 'model_fingerprint',
                    'projection_revision', 'index_generation'):
            if not isinstance(getattr(self, key), str) or not getattr(self, key).strip():
                raise ValueError('Request context identifiers must be explicit')
        if self.scope not in ('profile', 'shared') or self.representation not in REPRESENTATIONS:
            raise ValueError('Unsupported request scope or representation')
        if type(self.schema_version) is not int or self.schema_version < 0:
            raise ValueError('Schema version must be a nonnegative integer')


@dataclass(frozen=True)
class AuthorityResult:
    documents: tuple[RetrievalDocument, ...]
    rejected: tuple[dict, ...]
    status: str

    @property
    def incomplete(self):
        return self.status != 'current'


def capture_context(store, *, scope, model_fingerprint, representation,
                    projection_revision, index_generation):
    """Capture without mutating a shared search instance or preparing a model."""
    schema = schema_for_store(store) or {}
    return RequestContext(store.config.urn_prefix, scope, schema.get('version', 0),
                          _schema_fingerprint(schema), model_fingerprint, representation,
                          projection_revision, index_generation)


def _active(alias):
    return (f"({alias}.uri STARTS WITH $prefix OR ($shared AND {alias}.shared=true)) "
            f"AND any(label IN labels({alias}) WHERE label IN $labels) "
            f"AND NOT {alias}:KGQuarantined AND NOT {alias}:KGLegacyAlias "
            f"AND NOT {alias}:KGLegacyUnresolved "
            f"AND coalesce({alias}.hermes_enabled,true) "
            f"AND coalesce({alias}.hermes_status,'') <> 'disabled' "
            f"AND NOT coalesce({alias}.hermes_archivedByWorkbench,false)")


class RetrievalAuthority:
    def __init__(self, store, *, current_context: Callable[[RequestContext], RequestContext],
                 max_candidates=100, max_refs=200, max_relations=500, schema_reader=None):
        self.store = store
        self.current_context = current_context
        self.schema_reader = schema_reader or (lambda: schema_for_store(store) or {})
        for value in (max_candidates, max_refs, max_relations):
            if type(value) is not int or value < 1:
                raise ValueError('Authority budgets must be positive integers')
        self.max_candidates, self.max_refs, self.max_relations = max_candidates, max_refs, max_relations

    def _schema_matches(self, schema, context):
        return (schema.get('namespace', context.namespace) == context.namespace
                and schema.get('version', 0) == context.schema_version
                and _schema_fingerprint(schema) == context.schema_fingerprint)

    def _read_sources(self, refs, pairs, schema, context):
        # Fixed library-owned templates. KGStore.run_read intentionally rejects
        # CALL; use its bounded read transaction without weakening that guard.
        params = {'prefix': context.namespace, 'shared': context.scope == 'shared',
                  'labels': list(runtime_entity_labels(schema).values()) + ['foaf:Person'],
                  'uris': sorted(refs), 'pairs': [dict(source=a, target=b) for a, b in sorted(pairs)],
                  'edge_limit': self.max_relations + 1, 'node_limit': self.max_refs + 1}
        nodes_query = f"""/* retrieval_authority_nodes */
            UNWIND $uris AS source_uri
            {_lookup_uri('source_uri', 'source_uri')}
            WITH n WHERE {_active('n')}
            RETURN properties(n) AS properties, labels(n) AS labels ORDER BY n.uri LIMIT $node_limit"""
        edges_query = f"""/* retrieval_authority_edges */
            UNWIND $pairs AS pair
            {_lookup_uri('pair.source', 'pair')}
            WITH pair,n AS a
            {_lookup_uri('pair.target', 'pair')}
            WITH pair,a,n AS b
            MATCH (a)-[r]->(b) WHERE {_active('a')} AND {_active('b')}
                AND coalesce(r.hermes_enabled,true)
                AND coalesce(r.hermes_status,'') <> 'disabled'
                AND NOT coalesce(r.hermes_archivedByWorkbench,false)
            RETURN a.uri AS source,b.uri AS target,type(r) AS type,properties(r) AS properties
            ORDER BY a.uri,type(r),b.uri LIMIT $edge_limit"""

        def read(tx):
            nodes = [dict(row) for row in tx.run(nodes_query, params)] if refs else []
            edges = [dict(row) for row in tx.run(edges_query, params)] if pairs else []
            if len(nodes) > self.max_refs or len(edges) > self.max_relations:
                raise ValueError('Authority source budget exceeded')
            return {'entities': nodes, 'relations': [
                {'from': row['source'], 'to': row['target'], 'type': row['type'],
                 'properties': row['properties']} for row in edges]}

        with self.store.driver.session(database=self.store.config.database,
                                       default_access_mode='READ') as session:
            return self.store._execute(session, 'read', read)

    def validate(self, candidates, context):
        candidates = tuple(candidates)

        def withheld(reason, status='incomplete'):
            return AuthorityResult((), tuple({'id': doc.id, 'reason': reason}
                                   for doc in candidates if isinstance(doc, RetrievalDocument)), status)

        if not isinstance(context, RequestContext) or len(candidates) > self.max_candidates:
            return withheld('invalid_or_over_budget')
        if context.namespace != self.store.config.urn_prefix:
            return withheld('context_changed')
        if any(not isinstance(doc, RetrievalDocument) for doc in candidates):
            return withheld('invalid_candidate')
        refs, pairs, valid, rejected = set(), set(), [], []
        for doc in candidates:
            usable = (doc.namespace == context.namespace and doc.kind in ('entity', 'relation', 'ontology')
                      and isinstance(doc.metadata, dict)
                      and isinstance(doc.source_refs, tuple) and len(doc.source_refs) in (1, 2)
                      and all(isinstance(ref, str) and 0 < len(ref) <= 4096 for ref in doc.source_refs))
            if context.scope == 'profile':
                usable = usable and all(ref.startswith(context.namespace) for ref in doc.source_refs)
            if doc.kind == 'relation':
                a, b = (doc.metadata.get('from_uri'), doc.metadata.get('to_uri')) if isinstance(doc.metadata, dict) else (None, None)
                usable = (usable and isinstance(a, str) and isinstance(b, str)
                          and a.startswith(context.namespace)
                          and set(doc.source_refs) == {a, b}
                          and context.representation == 'entity_relations')
                if usable:
                    pairs.add((a, b))
            elif doc.kind == 'entity':
                usable = usable and len(doc.source_refs) == 1
            if not usable:
                rejected.append({'id': doc.id, 'reason': 'source_unavailable'})
                continue
            valid.append(doc)
            if doc.kind != 'ontology':
                refs.update(doc.source_refs)
        if len(refs) > self.max_refs:
            return withheld('invalid_or_over_budget')
        try:
            if self.current_context(context) != context:
                return withheld('context_changed')
            schema = deepcopy(self.schema_reader())
            if not self._schema_matches(schema, context):
                return withheld('context_changed')
            first = self._read_sources(refs, pairs, schema, context)
            second = self._read_sources(refs, pairs, schema, context)
            # Physical parallel edges have no persistent ordering/element IDs.
            signature = lambda snap: _json({key: sorted(value, key=_json) for key, value in snap.items()})
            if signature(first) != signature(second):
                return withheld('sources_changed')
            if (not self._schema_matches(self.schema_reader(), context)
                    or self.current_context(context) != context):
                return withheld('context_changed')
            rebuilt = dataset_to_documents({'namespace': context.namespace, **second},
                context.representation, schema=schema, include_ontology=True)
            by_id = {doc.id: doc for doc in rebuilt}
            documents, seen = [], set()
            for old in valid:
                current = by_id.get(old.id)
                if (current is None or current.kind != old.kind or current.source_key != old.source_key
                        or current.source_refs != old.source_refs
                        or current.dependency_fingerprint != old.dependency_fingerprint):
                    rejected.append({'id': old.id, 'reason': 'source_unavailable_or_changed'})
                elif current.id not in seen:
                    documents.append(current)
                    seen.add(current.id)
            if (not self._schema_matches(self.schema_reader(), context)
                    or self.current_context(context) != context):
                return withheld('context_changed')
            return AuthorityResult(tuple(documents), tuple(rejected), 'incomplete' if rejected else 'current')
        except Exception:
            # Do not expose exception messages which can contain private URI or
            # database details. This is unavailability, never a no-answer verdict.
            return withheld('authority_unavailable', 'unavailable')
