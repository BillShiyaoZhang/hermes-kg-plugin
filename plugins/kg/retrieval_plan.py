"""Explicit, finite, read-only graph retrieval plans.

No natural-language planner or arbitrary Cypher/attribute expressions are
accepted. Counts query the complete filtered graph, rather than retrieval
candidates. All fact operations deliberately share the graph explorer's
current-namespace active-fact scope; shared foreign endpoints are excluded.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
import math

from .graph_exploration import (GraphExploration, PATH_EDGE_BUDGET,
                                PATH_NODE_BUDGET, PATH_SECONDS)
from .ontology import (_active, _graph_edge_preview, _safe_node, _schema,
                       _stored_relation)
from .retrieval_documents import ontology_cards
from .schema import (CUSTOM_TYPE, RESERVED_PROPERTIES, TEXT_PROPERTIES,
                     canonical_urn, entity_type, normalize_rel,
                     runtime_entity_labels, runtime_kind, runtime_relations,
                     schema_for_store)

MAX_OFFSET = 1_000_000
_SCHEMA_KINDS = ('entity_types', 'properties', 'relations')
_FIELDS = {
    'schema': {'op', 'query', 'kinds', 'offset', 'limit'},
    'relations': {'op', 'center_uri', 'direction', 'relation_types', 'offset', 'limit'},
    'count': {'op', 'target', 'types', 'relation_types', 'center_uri', 'direction'},
    'path': {'op', 'source_uri', 'target_uri', 'direction', 'relation_types', 'max_hops', 'limit'},
}
_TIME_FIELDS = {'from', 'to', 'start', 'end', 'time', 'time_range', 'date_from', 'date_to',
                'include_unknown', 'includeUnknown'}
_DEFINITION_FIELDS = ('key', 'label', 'description', 'iri', 'aliases', 'domain', 'range',
                      'required_for', 'required_condition', 'allowed_values', 'active',
                      'managed', 'runtime_supported', 'value_shape')


class RetrievalPlanError(ValueError):
    """A public validation failure, with no echo of untrusted plan contents."""


class _RegisteredGraphExploration(GraphExploration):
    def _params(self, relation_types=None):
        params = super()._params(relation_types)
        if not relation_types:
            # An internal all-registered selection is not a user list and may
            # exceed the public 128-name request cap in a large custom schema.
            params['relation_aliases'] = sorted(runtime_relations(self._ontology))
        return params


def _shape(plan):
    if not isinstance(plan, Mapping) or any(not isinstance(key, str) for key in plan):
        raise RetrievalPlanError('Structured plan must be an object')
    operation = plan.get('op')
    if not isinstance(operation, str) or operation not in _FIELDS:
        raise RetrievalPlanError('Structured op must be schema, relations, count or path')
    if operation == 'path' and _TIME_FIELDS.intersection(plan):
        raise RetrievalPlanError('Path time filters are not supported')
    if set(plan) - _FIELDS[operation]:
        raise RetrievalPlanError('Structured plan contains unsupported fields')
    return operation


def _integer(value, name, lower, upper):
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
        raise RetrievalPlanError(f'{name} must be an integer from {lower} to {upper}')
    return value


def _list(value, name):
    if (not isinstance(value, list) or len(value) > 128
            or any(not isinstance(item, str) or not item for item in value)):
        raise RetrievalPlanError(f'{name} must be a list of at most 128 registered names')
    return value


def _uri(value, schema, namespace):
    if not isinstance(value, str) or not 1 <= len(value) <= 2000 or not value.startswith(namespace):
        raise RetrievalPlanError('URI must belong to the current namespace')
    try:
        result = canonical_urn(value, prefix=namespace, ontology=schema)
    except ValueError as exc:
        raise RetrievalPlanError('URI must contain a registered entity type and valid identifier') from exc
    if not result.startswith(namespace):
        raise RetrievalPlanError('URI must belong to the current namespace')
    return result


def _relations(value, schema):
    try:
        return sorted({normalize_rel(item, schema) for item in _list(value, 'relation_types')})
    except ValueError as exc:
        raise RetrievalPlanError('relation_types must contain registered relationships') from exc


def validate_plan(plan, schema, namespace):
    """Validate without I/O and return an independent canonical JSON-shaped plan."""
    operation = _shape(plan)
    if not isinstance(namespace, str) or not namespace:
        raise RetrievalPlanError('Current namespace is required')
    if (schema or {}).get('namespace') not in (None, namespace):
        raise RetrievalPlanError('Schema namespace does not match the current namespace')
    result = {'op': operation}
    if operation == 'schema':
        query = plan.get('query', '')
        if not isinstance(query, str) or len(query) > 200:
            raise RetrievalPlanError('query must be text of at most 200 characters')
        kinds = _list(plan.get('kinds', list(_SCHEMA_KINDS)), 'kinds')
        if any(kind not in _SCHEMA_KINDS for kind in kinds) or not kinds:
            raise RetrievalPlanError('kinds must select entity_types, properties or relations')
        result.update(query=query.strip(), kinds=sorted(set(kinds)))
    elif operation == 'relations':
        result['center_uri'] = _uri(plan.get('center_uri'), schema, namespace)
        if plan.get('direction') not in ('in', 'out', 'both'):
            raise RetrievalPlanError('relations requires an explicit in, out or both direction')
        result.update(direction=plan['direction'], relation_types=_relations(plan.get('relation_types', []), schema))
    elif operation == 'count':
        target = plan.get('target')
        if target not in ('entities', 'relations'):
            raise RetrievalPlanError('count target must be entities or relations')
        result['target'] = target
        if target == 'entities':
            if set(plan) & {'relation_types', 'center_uri', 'direction'}:
                raise RetrievalPlanError('Entity count accepts only registered types')
            try:
                result['types'] = sorted({entity_type(value, schema) for value in _list(plan.get('types', []), 'types')})
            except ValueError as exc:
                raise RetrievalPlanError('types must contain registered entity types') from exc
        else:
            if 'types' in plan:
                raise RetrievalPlanError('Relation count does not accept entity type filters')
            result['relation_types'] = _relations(plan.get('relation_types', []), schema)
            if 'center_uri' in plan:
                result['center_uri'] = _uri(plan['center_uri'], schema, namespace)
                if plan.get('direction') not in ('in', 'out', 'both'):
                    raise RetrievalPlanError('Centered relation count requires an explicit direction')
                result['direction'] = plan['direction']
            elif 'direction' in plan:
                raise RetrievalPlanError('direction requires center_uri')
    else:
        result.update(source_uri=_uri(plan.get('source_uri'), schema, namespace),
                      target_uri=_uri(plan.get('target_uri'), schema, namespace))
        if plan.get('direction') not in ('out', 'both'):
            raise RetrievalPlanError('path requires an explicit out or both direction')
        result.update(direction=plan['direction'], relation_types=_relations(plan.get('relation_types', []), schema),
                      max_hops=_integer(plan.get('max_hops', 6), 'max_hops', 1, 6))
    if operation in ('schema', 'relations'):
        result.update(offset=_integer(plan.get('offset', 0), 'offset', 0, MAX_OFFSET),
                      limit=_integer(plan.get('limit', 30), 'limit', 1, 100))
    elif operation == 'path':
        result['limit'] = _integer(plan.get('limit', 5), 'limit', 1, 5)
    return result


def _json_value(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if type(value).__module__.startswith('neo4j.time') and callable(getattr(value, 'iso_format', None)):
        return value.iso_format()
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in sorted(value.items())}
    return None


def _hash(value):
    return hashlib.sha256(json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def _total(rows):
    if (len(rows) != 1 or isinstance(rows[0].get('total'), bool)
            or not isinstance(rows[0].get('total'), int) or rows[0]['total'] < 0):
        raise RuntimeError('Incomplete aggregate result')
    return rows[0]['total']


class StructuredGraphSearch:
    def __init__(self, store):
        self.store, self.config = store, store.config

    def _load_schema(self):
        # Old read adapters may omit overlay support; never hide an overlay read
        # failure by silently falling back to the packaged definitions.
        schema = schema_for_store(self.store)
        if schema is None:
            schema = deepcopy(_schema())
            schema.update(namespace=self.config.urn_prefix, version=0)
        return schema

    def execute(self, plan):
        operation = _shape(plan)
        try:
            schema = self._load_schema()
        except Exception as exc:
            return {'mode': 'structured', 'kg_results': [], 'plan': {'op': operation},
                    'status': 'unavailable', 'evidence': [],
                    'boundaries': {'namespace': self.config.urn_prefix, 'schema_version': None,
                                   'scope': 'namespace_definitions' if operation == 'schema' else 'current_namespace_active',
                                   'read_only': True, 'shared_cross_namespace': False,
                                   'complete': False, 'truncated': False, 'timed_out': False},
                    'errors': [{'code': 'ontology_schema_unavailable', 'error_type': type(exc).__name__}]}
        plan = validate_plan(plan, schema, self.config.urn_prefix)
        result = {
            'mode': 'structured', 'kg_results': [], 'plan': plan, 'status': 'empty', 'evidence': [],
            'boundaries': {
                'namespace': self.config.urn_prefix,
                'scope': 'namespace_definitions' if plan['op'] == 'schema' else 'current_namespace_active',
                'schema_version': schema.get('version', 0), 'read_only': True,
                'shared_cross_namespace': False, 'inactive_definitions_preserve_history': True,
                'registered_relationships_only': True,
                'complete': True, 'truncated': False, 'timed_out': False,
                'read_consistency': 'read_committed_per_query',
            },
        }
        try:
            getattr(self, '_' + plan['op'])(plan, schema, result)
            # A plan is validated against one registry. Publishing a new one
            # during execution must not stamp old rule meanings as current.
            latest = self._load_schema()
            if latest.get('version', 0) != schema.get('version', 0):
                result.update(status='unavailable', evidence=[])
                result['boundaries'].update(complete=False, truncated=False, schema_changed=True)
                result['errors'] = [{'code': 'ontology_schema_changed'}]
                result.pop('pagination', None)
                result.pop('graph', None)
        except Exception as exc:
            if isinstance(exc, RetrievalPlanError):
                raise
            code = str(getattr(exc, 'code', '')).lower()
            timed_out = isinstance(exc, TimeoutError) or 'timeout' in code or 'timedout' in code
            result.update(status='timed_out' if timed_out else 'unavailable', evidence=[])
            result['boundaries'].update(complete=False, truncated=timed_out, timed_out=timed_out)
            result['errors'] = [{'code': 'graph_query_timed_out' if timed_out else 'graph_query_unavailable',
                                 'error_type': type(exc).__name__}]
            result.pop('pagination', None)
            result.pop('graph', None)
        return result

    def _params(self, plan, schema):
        labels = runtime_entity_labels(schema)
        selected = set(plan.get('relation_types', [])) or set(runtime_relations(schema).values())
        aliases = sorted({alias.upper() for alias, target in runtime_relations(schema).items() if target in selected})
        return {'prefix': self.config.urn_prefix, 'entity_labels': list(labels.values()),
                'selected_labels': [labels[value] for value in plan.get('types', [])] or list(labels.values()),
                'relation_aliases': aliases}

    def _center(self, uri, params):
        rows = self.store.run_read(f'''/* structured_center */ MATCH (n) WHERE {_active('n')}
            AND n.uri=$center_uri RETURN labels(n) AS labels,properties(n) AS properties LIMIT 2''',
            {**params, 'center_uri': uri})
        safe = [row for row in rows if _safe_node(row, self.config.urn_prefix)
                and set(row['labels']) & set(params['entity_labels']) and row['properties']['uri'] == uri]
        return len(safe) == 1

    def _schema(self, plan, schema, result):
        cards = {(card.metadata['definition_kind'], card.metadata['definition_key']): card
                 for card in ontology_cards(schema, namespace=self.config.urn_prefix)}
        query = plan['query'].casefold()
        found = []
        for collection in plan['kinds']:
            for definition in schema.get(collection, []):
                value = {key: _json_value(definition[key]) for key in _DEFINITION_FIELDS if key in definition}
                if query and query not in json.dumps(value, ensure_ascii=False, sort_keys=True).casefold():
                    continue
                card = cards[(collection, definition['key'])]
                found.append({'kind': 'rule', 'is_rule': True, 'definition_kind': collection,
                              'definition_key': definition['key'], 'definition': value,
                              'text': card.text, 'id': card.id, 'source_refs': list(card.source_refs)})
        found.sort(key=lambda item: (item['definition_kind'], item['definition_key']))
        page = found[plan['offset']:plan['offset'] + plan['limit']]
        result.update(status='found' if page else 'empty', evidence=page)
        self._pagination(result, len(found), plan)

    @staticmethod
    def _pagination(result, total, plan):
        has_more = plan['offset'] + plan['limit'] < total
        result['pagination'] = {'offset': plan['offset'], 'limit': plan['limit'], 'total': total,
                                'has_more': has_more,
                                'next_offset': plan['offset'] + plan['limit']
                                    if has_more and plan['offset'] + plan['limit'] <= MAX_OFFSET else None,
                                'total_complete': True}
        result['boundaries'].update(complete=plan['offset'] == 0 and not has_more,
                                    truncated=plan['offset'] > 0 or has_more)

    @staticmethod
    def _relation_match(plan):
        direction = plan.get('direction')
        center = {'out': 'a.uri=$center_uri', 'in': 'b.uri=$center_uri',
                  'both': '(a.uri=$center_uri OR b.uri=$center_uri)'}.get(direction, 'true')
        return f'''MATCH (a)-[r]->(b) WHERE {_active('a')} AND {_active('b')}
            AND {center} AND toUpper(type(r)) IN $relation_aliases'''

    def _relations(self, plan, schema, result):
        params = {**self._params(plan, schema), 'center_uri': plan['center_uri'],
                  'offset': plan['offset'], 'limit': plan['limit']}
        if not self._center(plan['center_uri'], params):
            result['status'] = 'not_found'
            return
        match = self._relation_match(plan)
        total = _total(self.store.run_read('/* structured_relations_total */ ' + match + ' RETURN count(r) AS total', params))
        rows = self.store.run_read('/* structured_relations_page */ ' + match + '''
            RETURN labels(a) AS source_labels,properties(a) AS source_properties,
                labels(b) AS target_labels,properties(b) AS target_properties,
                type(r) AS type,properties(r) AS properties
            ORDER BY a.uri,type(r),b.uri,elementId(r) SKIP $offset LIMIT $limit''', params)
        evidence = [value for row in rows if (value := self._relation_evidence(row, schema, params, plan))]
        result.update(status='found' if evidence else 'empty', evidence=evidence)
        self._pagination(result, total, plan)
        if len(evidence) != len(rows):
            result['boundaries'].update(complete=False, truncated=True)
            result['warnings'] = ['invalid_backend_rows_excluded']

    def _properties_preview(self, properties, schema):
        definitions = {item['key']: item for item in schema.get('properties', [])}
        result, truncated = {}, False
        for key, value in sorted(properties.items()):
            if (key in RESERVED_PROPERTIES or key in {'shared', 'hermes_enabled'}
                    or definitions.get(key, {}).get('managed')
                    or not (key in TEXT_PROPERTIES or key in {'geo_lat', 'geo_long', 'hermes_eventTime', 'hermes_eventEndTime'}
                            or CUSTOM_TYPE.fullmatch(key) or key in definitions)):
                continue
            value = _json_value(value)
            if isinstance(value, str) and len(value) > 1200:
                value, truncated = value[:1200], True
            elif isinstance(value, list):
                truncated = truncated or len(value) > 8 or any(isinstance(item, str) and len(item) > 240 for item in value)
                value = [item[:240] if isinstance(item, str) else item for item in value[:8]]
            result[key] = value
        return result, truncated

    def _relation_evidence(self, row, schema, params, plan=None):
        source = {'labels': row['source_labels'], 'properties': row['source_properties']}
        target = {'labels': row['target_labels'], 'properties': row['target_properties']}
        if any(not _safe_node(node, self.config.urn_prefix)
               or not set(node['labels']) & set(params['entity_labels']) for node in (source, target)):
            return None
        if row['type'].upper() not in params['relation_aliases']:
            return None
        source_uri, target_uri = source['properties']['uri'], target['properties']['uri']
        if plan and plan.get('center_uri'):
            center, direction = plan['center_uri'], plan['direction']
            if not ((direction in ('out', 'both') and source_uri == center)
                    or (direction in ('in', 'both') and target_uri == center)):
                return None
        canonical = _stored_relation(row['type'], schema)
        properties, truncated = self._properties_preview(row['properties'], schema)
        def endpoint(node):
            props = node['properties']
            return {'uri': props['uri'], 'type': runtime_kind(node['labels'], schema),
                    'name': str(props.get('foaf_name') or props.get('hermes_name') or '')[:240],
                    'revision': props.get('hermes_revision', 0)}
        return {'kind': 'relation', 'is_rule': False,
                'id': 'relation-' + _hash([self.config.urn_prefix, source_uri, canonical, target_uri, row['properties']]),
                'source': endpoint(source), 'target': endpoint(target), 'relation_type': canonical,
                'stored_type': row['type'], 'direction': 'out',
                'properties': properties, 'properties_truncated': truncated,
                'provenance': _graph_edge_preview(row['properties']),
                'source_refs': list(dict.fromkeys((source_uri, target_uri)))}

    def _count(self, plan, schema, result):
        params = self._params(plan, schema)
        if plan['target'] == 'entities':
            query = f'''/* structured_count_entities */ MATCH (n) WHERE {_active('n')}
                AND any(label IN labels(n) WHERE label IN $selected_labels)
                RETURN count(DISTINCT n) AS total'''
        else:
            if 'center_uri' in plan:
                params['center_uri'] = plan['center_uri']
                if not self._center(plan['center_uri'], params):
                    result['status'] = 'not_found'
                    return
            query = '/* structured_count_relations */ ' + self._relation_match(plan) + ' RETURN count(r) AS total'
        count = _total(self.store.run_read(query, params))
        result.update(status='found', evidence=[{'kind': 'count', 'is_rule': False,
                      'target': plan['target'], 'value': count, 'complete': True,
                      'scope': 'current_namespace_active', 'filters': deepcopy(plan)}])
        result['boundaries']['count_unit'] = 'distinct_entities' if plan['target'] == 'entities' else 'stored_directed_relationships'

    def _path(self, plan, schema, result):
        result['boundaries'].update(
            budget={'seconds': PATH_SECONDS, 'edges': PATH_EDGE_BUDGET, 'nodes': PATH_NODE_BUDGET,
                    'max_hops': plan['max_hops'], 'result_limit': plan['limit']},
            path_mode='shortest', time_filter_supported=False,
            negative_claim_scope='within max_hops and the explicit direction/relation filters')
        graph = _RegisteredGraphExploration(self.config, self.store).paths(
            source_uri=plan['source_uri'], target_uri=plan['target_uri'], direction=plan['direction'],
            relation_types=plan['relation_types'], max_hops=plan['max_hops'], limit=plan['limit'])
        boundaries = result['boundaries']
        boundaries.update(timed_out=bool(graph['timed_out']), truncated=bool(graph['truncated']),
                          complete=not graph['timed_out'] and not graph['truncated'])
        if graph['timed_out']:
            result['status'] = 'timed_out'
        elif not graph['focus_found']:
            result['status'] = 'not_found'
        elif not graph['paths']:
            result['status'] = 'incomplete' if graph['truncated'] else 'no_path'
        else:
            result['status'] = 'found'
        edges = {edge['id']: edge for edge in graph['edges']}
        for edge in edges.values():
            edge['relation_type'] = _stored_relation(edge['stored_type'], schema)
        for path in graph['paths']:
            steps = []
            for index, edge_id in enumerate(path['edge_ids']):
                edge = edges[edge_id]
                previous, following = path['node_uris'][index:index + 2]
                reverse = edge['source']['uri'] != previous
                steps.append({'from_uri': previous, 'to_uri': following,
                              'relation_source_uri': edge['source']['uri'],
                              'relation_target_uri': edge['target']['uri'],
                              'relation_type': _stored_relation(edge['stored_type'], schema),
                              'traversed_reverse': reverse, 'edge_id': edge_id})
            result['evidence'].append({'kind': 'path', 'is_rule': False,
                                       'node_uris': path['node_uris'], 'edge_ids': path['edge_ids'],
                                       'steps': steps, 'direction': plan['direction'],
                                       'source_refs': path['node_uris']})
        result['graph'] = graph
