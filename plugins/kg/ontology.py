"""RDF-backed, read-only ontology definitions and profile-scoped instance browsing."""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
from importlib import resources
from pathlib import Path

from rdflib import Graph, Namespace
from rdflib.namespace import OWL, RDF, RDFS, XSD

from .schema import (ENTITY_LABELS, ENTITY_REQUIRED_PROPERTIES, EVENT_TYPES, PROPERTY_ALIASES,
                     RELATION_ALIASES, RELATION_ENDPOINTS, RESERVED_PROPERTIES,
                     TEXT_PROPERTIES, entity_type, normalize_rel)
from .schema import runtime_entity_labels, runtime_kind, runtime_relations, schema_for_store

SCOPE = 'current_namespace_active'
HERMES = Namespace('urn:hermes:ontology:')
MAX_OFFSET = 1_000_000
GRAPH_NAME_LIMIT = 240
GRAPH_DESCRIPTION_LIMIT = 1200
GRAPH_PROVENANCE_ITEMS = 8
GRAPH_NODE_PREVIEW_KEYS = ('uri', 'foaf_name', 'hermes_name', 'hermes_description', 'hermes_revision',
                           'hermes_updatedAt', 'hermes_enabled', 'hermes_status', 'hermes_aliasConfirmed',
                           'hermes_eventTime', 'hermes_timePrecision')
GRAPH_EDGE_PREVIEW_KEYS = ('hermes_relLabel', 'hermes_origin', 'hermes_createdAt', 'hermes_updatedAt',
                           'hermes_sourceSession', 'hermes_sourceSessions', 'hermes_extractionIds')


def _resource_text():
    """Wheels carry skills as resources; copied plugins/repo trees keep siblings."""
    packaged = resources.files('kg').joinpath('resources', 'skills', 'kg-memory', 'ontology', 'core.ttl')
    if packaged.is_file():
        return packaged.read_text(encoding='utf-8')
    sibling = Path(__file__).resolve().parents[2] / 'skills' / 'kg-memory' / 'ontology' / 'core.ttl'
    if sibling.is_file():
        return sibling.read_text(encoding='utf-8')
    raise FileNotFoundError('The installed ontology resource is missing')


def parse_ontology(text):
    """Parse local Turtle without resolving imports or executing schema content."""
    graph = Graph().parse(data=text, format='turtle')
    roots = list(graph.subjects(RDF.type, OWL.Ontology))
    if len(roots) != 1:
        raise ValueError('The ontology must contain one ontology declaration')
    root = roots[0]
    warnings = []
    classes = []
    class_keys = {}
    for subject in graph.subjects(RDF.type, OWL.Class):
        storage = graph.value(subject, HERMES.neo4jLabel)
        if storage is None:
            continue
        label = str(storage)
        supported = label in ENTITY_LABELS.values()
        key = next((key for key, value in ENTITY_LABELS.items() if value == label), label)
        class_keys[subject] = key
        classes.append({'key': key, 'label': str(graph.value(subject, RDFS.label) or label),
                        'description': str(graph.value(subject, RDFS.comment) or ''),
                        'iri': str(subject), 'storage_label': label, 'runtime_supported': supported})
        if not supported:
            warnings.append({'code': 'unsupported_entity_type', 'key': label})

    def terms(subject, predicate):
        values = []
        for term in graph.objects(subject, predicate):
            if term == OWL.Thing:
                values.append('any')
            elif term in class_keys:
                values.append(class_keys[term])
            elif str(term).startswith(str(XSD)):
                values.append(str(term)[len(str(XSD)):])
            elif graph.value(term, OWL.unionOf) is not None:
                for member in graph.items(graph.value(term, OWL.unionOf)):
                    values.append(class_keys.get(member, 'any' if member == OWL.Thing else str(member)))
            else:
                values.append(str(term))
        # No domain/range assertion is unrestricted, rather than an empty class.
        return sorted(set(values)) or ['any']

    properties = []
    known_properties = (set(PROPERTY_ALIASES.values()) | RESERVED_PROPERTIES | TEXT_PROPERTIES |
                        {'shared', 'geo_lat', 'geo_long', 'foaf_name', 'hermes_name', 'hermes_timeYearInferred'})
    for subject in graph.subjects(RDF.type, OWL.DatatypeProperty):
        storage = graph.value(subject, HERMES.neo4jProperty)
        if storage is None:
            continue
        key = str(storage)
        required = [kind for kind, fields in ENTITY_REQUIRED_PROPERTIES.items() if key in fields]
        if key in {'foaf_name', 'hermes_name'}:
            required = ['person'] if key == 'foaf_name' else [kind for kind in ENTITY_LABELS if kind != 'person']
        aliases = sorted(alias for alias, target in PROPERTY_ALIASES.items() if target == key and alias != key)
        if key in {'foaf_name', 'hermes_name'}:
            aliases.append('name')
        item = {'key': key, 'label': str(graph.value(subject, RDFS.label) or key),
                'description': str(graph.value(subject, RDFS.comment) or ''), 'iri': str(subject),
                'domain': terms(subject, RDFS.domain), 'range': terms(subject, RDFS.range),
                'aliases': aliases,
                'required_for': required, 'managed': key in RESERVED_PROPERTIES,
                'runtime_supported': key in known_properties}
        if key == 'hermes_eventTime':
            item['required_condition'] = 'Required unless time_precision=unknown and event_status=occurred'
        if key == 'hermes_timePrecision':
            item['allowed_values'] = ['day', 'datetime', 'unknown']
        elif key == 'hermes_eventType':
            item['allowed_values'] = sorted(EVENT_TYPES)
        item['value_shape'] = 'array' if key in {'hermes_sourceSessions', 'hermes_extractionIds'} else 'scalar'
        properties.append(item)
        if not item['runtime_supported']:
            warnings.append({'code': 'unsupported_property', 'key': key})

    relations = []
    for subject in graph.subjects(RDF.type, OWL.ObjectProperty):
        storage = graph.value(subject, HERMES.neo4jRelationship)
        if storage is None:
            continue
        key = str(storage)
        declared_domain, declared_range = terms(subject, RDFS.domain), terms(subject, RDFS.range)
        supported = key in RELATION_ALIASES.values()
        expected = RELATION_ENDPOINTS.get(key)
        domain = sorted(label.lower() for label in expected[0]) if expected else declared_domain
        range_ = sorted(label.lower() for label in expected[1]) if expected else declared_range
        if expected and (declared_domain != domain or declared_range != range_):
            warnings.append({'code': 'runtime_endpoint_constraint', 'key': key,
                             'declared_domain': declared_domain, 'declared_range': declared_range,
                             'domain': domain, 'range': range_})
        relations.append({'key': key, 'label': str(graph.value(subject, RDFS.label) or key),
                          'description': str(graph.value(subject, RDFS.comment) or ''), 'iri': str(subject),
                          'storage_type': key,
                          'aliases': sorted(alias for alias, target in RELATION_ALIASES.items() if target == key and alias != key),
                          'domain': domain, 'range': range_, 'declared_domain': declared_domain,
                          'declared_range': declared_range, 'properties': [], 'runtime_supported': supported})
        if not supported:
            warnings.append({'code': 'unsupported_relationship', 'key': key})
    for key in set(ENTITY_LABELS) - {item['key'] for item in classes}:
        warnings.append({'code': 'runtime_entity_not_declared', 'key': key})
    for key in set(RELATION_ALIASES.values()) - {item['key'] for item in relations}:
        warnings.append({'code': 'runtime_relationship_not_declared', 'key': key})
    undeclared = sorted(set(PROPERTY_ALIASES.values()) - {item['key'] for item in properties})
    if undeclared:
        warnings.append({'code': 'runtime_properties_not_declared', 'properties': undeclared})
    properties.sort(key=lambda item: item['key'])
    for item in classes:
        item['properties'] = [deepcopy(prop) for prop in properties if 'any' in prop['domain'] or item['key'] in prop['domain']]
    return {'ontology': {'iri': str(root), 'label': str(graph.value(root, RDFS.label) or ''),
                         'description': str(graph.value(root, RDFS.comment) or ''),
                         'version': str(graph.value(root, OWL.versionInfo) or '')},
            'entity_types': sorted(classes, key=lambda item: item['key']),
            'relations': sorted(relations, key=lambda item: item['key']),
            'properties': properties, 'schema_warnings': warnings}


@lru_cache(maxsize=1)
def _schema():
    return parse_ontology(_resource_text())


def _active(alias):
    return f"""({alias}.uri STARTS WITH $prefix
        AND any(label IN labels({alias}) WHERE label IN $entity_labels)
        AND NOT {alias}:KGQuarantined AND NOT {alias}:KGLegacyAlias AND NOT {alias}:KGLegacyUnresolved
        AND coalesce({alias}.hermes_aliasConfirmed,false)=false
        AND coalesce({alias}.hermes_enabled,true)=true AND coalesce({alias}.hermes_status,'')<>'disabled')"""


def _safe_node(row, prefix):
    props, labels = row['properties'], set(row['labels'])
    return (isinstance(props.get('uri'), str) and props['uri'].startswith(prefix)
            and runtime_kind(labels) is not None
            and not labels & {'KGQuarantined', 'KGLegacyAlias', 'KGLegacyUnresolved'}
            and (props.get('hermes_aliasConfirmed') is None or props.get('hermes_aliasConfirmed') is False)
            and (props.get('hermes_enabled') is None or props.get('hermes_enabled') is True)
            and props.get('hermes_status') != 'disabled')


def _stored_relation(value, ontology=None):
    # API inputs may start with ':', but literal stored identifiers must match
    # the same case-insensitive alias set used by the parameterized query.
    return runtime_relations(ontology).get(value.upper(), value)


def _graph_relation_aliases(relation_types, ontology=None):
    if relation_types is None:
        relation_types = []
    if (not isinstance(relation_types, list) or len(relation_types) > 128
            or any(not isinstance(value, str) for value in relation_types)):
        raise ValueError('relation_types must be a list of at most 128 supported relationships')
    canonical = {normalize_rel(value, ontology) for value in relation_types}
    return sorted(alias for alias, target in runtime_relations(ontology).items() if target in canonical)


def _node(row):
    props = dict(row['properties'])
    kind = runtime_kind(row['labels'])
    if kind is None:
        raise ValueError('Unknown graph entity type')
    return {'uri': props['uri'], 'type': kind,
            'name': props.get('foaf_name', props.get('foaf:name')) if kind == 'person' else props.get('hermes_name'),
            'description': props.get('hermes_description', ''), 'version': props.get('hermes_revision', 0),
            'properties': props, 'updated_at': props.get('hermes_updatedAt')}


def _graph_node_properties(alias):
    """A small database-side projection; full evidence stays in detail reads."""
    return f'''{{uri:{alias}.uri,
        foaf_name:left(toString(coalesce({alias}.foaf_name,{alias}['foaf:name'])),$name_limit),
        hermes_name:left(toString({alias}.hermes_name),$name_limit),
        hermes_description:left(toString({alias}.hermes_description),$description_limit),
        hermes_eventTime:left(toString({alias}.hermes_eventTime),$name_limit),
        hermes_timePrecision:left(toString({alias}.hermes_timePrecision),$name_limit),
        hermes_revision:{alias}.hermes_revision,hermes_updatedAt:left(toString({alias}.hermes_updatedAt),$name_limit),
        hermes_enabled:{alias}.hermes_enabled,hermes_status:left(toString({alias}.hermes_status),$name_limit),
        hermes_aliasConfirmed:{alias}.hermes_aliasConfirmed}}'''


def _graph_node(row):
    # Reapply the public preview contract for adapters that do not execute the
    # Cypher projection (and avoid carrying a custom property's large payload).
    source = row['properties']
    props = {key: source[key] for key in GRAPH_NODE_PREVIEW_KEYS if key in source}
    name = source.get('foaf_name', source.get('foaf:name'))
    if name is not None:
        props['foaf_name'] = str(name)[:GRAPH_NAME_LIMIT]
    if source.get('hermes_name') is not None:
        props['hermes_name'] = str(source['hermes_name'])[:GRAPH_NAME_LIMIT]
    props['hermes_description'] = str(source.get('hermes_description') or '')[:GRAPH_DESCRIPTION_LIMIT]
    for key in ('hermes_eventTime', 'hermes_timePrecision'):
        if source.get(key) is not None:
            props[key] = str(source[key])[:GRAPH_NAME_LIMIT]
    return {**_node({'labels': row['labels'], 'properties': props}), 'preview': True,
            'properties_truncated': True,
            'content_truncated': bool(row.get('content_truncated')) or any(
                len(str(value or '')) > limit for value, limit in (
                    (name, GRAPH_NAME_LIMIT), (source.get('hermes_name'), GRAPH_NAME_LIMIT),
                    (source.get('hermes_description'), GRAPH_DESCRIPTION_LIMIT)))}


def _graph_edge_properties():
    return '''{hermes_relLabel:left(toString(r.hermes_relLabel),$name_limit),
        hermes_origin:left(toString(r.hermes_origin),$name_limit),
        hermes_createdAt:left(toString(r.hermes_createdAt),$name_limit),
        hermes_updatedAt:left(toString(r.hermes_updatedAt),$name_limit),
        hermes_sourceSession:left(toString(r.hermes_sourceSession),$name_limit),
        hermes_sourceSessions:[value IN coalesce(r.hermes_sourceSessions,[])[..$provenance_items] |
            left(toString(value),$name_limit)],
        hermes_extractionIds:[value IN coalesce(r.hermes_extractionIds,[])[..$provenance_items] |
            left(toString(value),$name_limit)]}'''


def _graph_edge_preview(props):
    result = {}
    for key in GRAPH_EDGE_PREVIEW_KEYS:
        value = props.get(key)
        if value is None or value == []:
            continue
        if key in {'hermes_sourceSessions', 'hermes_extractionIds'}:
            result[key] = [str(item)[:GRAPH_NAME_LIMIT] for item in value[:GRAPH_PROVENANCE_ITEMS]]
        elif isinstance(value, str):
            result[key] = value[:GRAPH_NAME_LIMIT]
        else:
            result[key] = value
    return result


class OntologyService:
    def __init__(self, config, store):
        self.config, self.store = config, store

    def _params(self, schema=None):
        return {'prefix': self.config.urn_prefix, 'entity_labels': list(runtime_entity_labels(schema if schema is not None else schema_for_store(self.store)).values())}

    def describe(self):
        result = deepcopy(_schema())
        overlay_reader = getattr(self.store, 'ontology_overlay', None)
        overlay_error = None
        if callable(overlay_reader):
            try:
                from .ontology_editing import effective_schema
                result = effective_schema(self.config, overlay_reader())
            except Exception as exc:
                overlay_error = {'error_type': type(exc).__name__}
                result.update(version=None, editing={'scope': 'namespace_overlay', 'can_edit': False}, overlay_error=overlay_error)
        result.update(namespace=self.config.urn_prefix, scope=SCOPE, read_only=True,
                      stats_available=False, stats_error=None, stats={'entities': None, 'relationships': None, 'unmapped_relationships': None})
        if callable(overlay_reader) and overlay_error is None:
            result['read_only'] = False
        for item in result['entity_types'] + result['relations']:
            item['count'] = None
        try:
            entities = self.store.run_read(f'MATCH (n) WHERE {_active("n")} RETURN labels(n) AS labels,count(n) AS count', self._params(result))
            relations = self.store.run_read(f'MATCH (a)-[r]->(b) WHERE {_active("a")} AND {_active("b")} RETURN type(r) AS type,count(r) AS count', self._params(result))
            registry = runtime_entity_labels(result)
            counts = {key: 0 for key in registry}
            relation_counts = {item['key']: 0 for item in result['relations']}
            unmapped = 0
            for row in entities:
                for key, label in registry.items():
                    if label in row['labels']:
                        counts[key] += row['count']
            for row in relations:
                canonical = _stored_relation(row['type'], result)
                if canonical in relation_counts:
                    relation_counts[canonical] += row['count']
                else:
                    unmapped += row['count']
            for item in result['entity_types']:
                item['count'] = counts.get(item['key'])
            for item in result['relations']:
                item['count'] = relation_counts.get(item['key'])
            result.update(stats_available=True, stats={'entities': sum(row['count'] for row in entities),
                'relationships': sum(row['count'] for row in relations), 'unmapped_relationships': unmapped})
        except Exception as exc:
            result['stats_error'] = {'error_type': type(exc).__name__}
        return result

    def _page(self, q, offset, limit):
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= MAX_OFFSET:
            raise ValueError('offset must be an integer from 0 to 1000000')
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError('limit must be an integer from 1 to 50')
        if not isinstance(q, str) or len(q) > 200:
            raise ValueError('q must be text of at most 200 characters')
        return {**self._params(), 'query': q.strip().lower(), 'offset': offset, 'limit': limit}

    def _result(self, items, total, offset, limit):
        return {'namespace': self.config.urn_prefix, 'scope': SCOPE, 'items': items,
                'total': total, 'offset': offset, 'limit': limit, 'has_more': offset + limit < total}

    def entities(self, *, type=None, q='', offset=0, limit=20):
        schema = schema_for_store(self.store)
        registry = runtime_entity_labels(schema)
        params = self._page(q, offset, limit)
        params['selected_labels'] = [registry[entity_type(type, schema)]] if type else list(registry.values())
        match = f"""MATCH (n) WHERE {_active('n')}
            AND any(label IN labels(n) WHERE label IN $selected_labels)
            AND ($query='' OR any(value IN [n.uri,n.foaf_name,n.hermes_name,n.hermes_description]
                + coalesce(n.hermes_aliasNames,[]) + [key IN keys(n) WHERE key STARTS WITH 'x_' | n[key]]
                WHERE toLower(toString(value)) CONTAINS $query))"""
        total = self.store.run_read(match + ' RETURN count(n) AS total', params)[0]['total']
        rows = self.store.run_read(match + ''' RETURN labels(n) AS labels,properties(n) AS properties
            ORDER BY n.uri,elementId(n) SKIP $offset LIMIT $limit''', params)
        items = [_node(row) for row in rows if _safe_node(row, self.config.urn_prefix)]
        return self._result(items, total, offset, limit)

    def relations(self, *, relation=None, q='', offset=0, limit=20):
        schema = schema_for_store(self.store)
        params = self._page(q, offset, limit)
        canonical = normalize_rel(relation, schema) if relation else None
        params['relation_aliases'] = sorted(alias for alias, target in runtime_relations(schema).items() if target == canonical) if canonical else []
        match = f"""MATCH (a)-[r]->(b) WHERE {_active('a')} AND {_active('b')}
            AND (size($relation_aliases)=0 OR toUpper(type(r)) IN $relation_aliases)
            AND ($query='' OR any(value IN [a.uri,b.uri,a.foaf_name,a.hermes_name,b.foaf_name,b.hermes_name,r.hermes_relLabel]
                WHERE toLower(toString(value)) CONTAINS $query))"""
        total = self.store.run_read(match + ' RETURN count(r) AS total', params)[0]['total']
        rows = self.store.run_read(match + ''' RETURN labels(a) AS source_labels,properties(a) AS source_properties,
            labels(b) AS target_labels,properties(b) AS target_properties,type(r) AS type,properties(r) AS properties
            ORDER BY a.uri,type(r),b.uri,elementId(r) SKIP $offset LIMIT $limit''', params)
        items = []
        for row in rows:
            source = {'labels': row['source_labels'], 'properties': row['source_properties']}
            target = {'labels': row['target_labels'], 'properties': row['target_properties']}
            if not _safe_node(source, self.config.urn_prefix) or not _safe_node(target, self.config.urn_prefix):
                continue
            kind = _stored_relation(row['type'], schema)
            items.append({'relation_type': kind, 'stored_type': row['type'],
                          'source': {key: value for key, value in _node(source).items() if key in {'uri', 'name', 'type'}},
                          'target': {key: value for key, value in _node(target).items() if key in {'uri', 'name', 'type'}},
                          'properties': row['properties']})
        return self._result(items, total, offset, limit)

    def graph(self, *, q='', types=None, relation_types=None, focus_uri=None, node_limit=120, edge_limit=240):
        """Return a bounded, profile-only induced graph for interactive browsing.

        Search is evaluated against the whole active namespace before limiting.
        A focus query always retains its active centre; type/search filters apply
        to its one-hop neighbours. Relationship totals concern returned nodes,
        rather than claiming relationships to omitted nodes are displayed.
        """
        schema = schema_for_store(self.store)
        registry = runtime_entity_labels(schema)
        if not isinstance(q, str) or len(q) > 200:
            raise ValueError('q must be text of at most 200 characters')
        if types is None:
            types = []
        if not isinstance(types, list) or len(types) > 128 or any(not isinstance(kind, str) for kind in types):
            raise ValueError('types must be a list of at most six supported entity types')
        selected_labels = sorted({registry[entity_type(kind, schema)] for kind in types}) or list(registry.values())
        relation_aliases = _graph_relation_aliases(relation_types, schema)
        for name, value, maximum in (('node_limit', node_limit, 200), ('edge_limit', edge_limit, 500)):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
                raise ValueError(f'{name} must be an integer from 1 to {maximum}')
        if focus_uri is not None and (not isinstance(focus_uri, str) or not focus_uri.strip() or len(focus_uri) > 2000):
            raise ValueError('focus_uri must be non-empty text of at most 2000 characters')
        focus_uri = focus_uri.strip() if focus_uri is not None else None
        result = {'namespace': self.config.urn_prefix, 'scope': SCOPE,
                  'edge_scope': 'returned_nodes', 'nodes': [], 'edges': [],
                  'total_nodes': 0, 'total_edges': 0,
                  'truncated_nodes': False, 'truncated_edges': False,
                  'focus_found': None if focus_uri is None else False}
        params = {**self._params(), 'query': q.strip().lower(), 'selected_labels': selected_labels,
                  'relation_aliases': relation_aliases,
                  'focus_uri': focus_uri, 'node_limit': node_limit, 'edge_limit': edge_limit,
                  'name_limit': GRAPH_NAME_LIMIT, 'description_limit': GRAPH_DESCRIPTION_LIMIT,
                  'provenance_items': GRAPH_PROVENANCE_ITEMS}
        if focus_uri is not None:
            # A caller cannot use this endpoint as an oracle for another space.
            if not focus_uri.startswith(self.config.urn_prefix):
                return result
            found = self.store.run_read(f'''MATCH (focus) WHERE {_active('focus')}
                AND focus.uri=$focus_uri RETURN count(focus) AS total''', params)[0]['total']
            if not found:
                return result
            result['focus_found'] = True
        selection = """any(label IN labels(n) WHERE label IN $selected_labels)
            AND ($query='' OR any(value IN [n.uri,n.foaf_name,n.hermes_name,n.hermes_description]
                + coalesce(n.hermes_aliasNames,[]) + [key IN keys(n) WHERE key STARTS WITH 'x_' | n[key]]
                WHERE toLower(toString(value)) CONTAINS $query))"""
        match = f'''MATCH (n) WHERE {_active('n')}
            AND (($focus_uri IS NOT NULL AND n.uri=$focus_uri) OR ({selection}))
            AND ($focus_uri IS NULL OR n.uri=$focus_uri OR EXISTS {{
                MATCH (focus)-[neighbor_relation]-(n) WHERE focus.uri=$focus_uri AND {_active('focus')}
                    AND (size($relation_aliases)=0 OR toUpper(type(neighbor_relation)) IN $relation_aliases)
            }})'''
        total = self.store.run_read(match + ' RETURN count(n) AS total', params)[0]['total']
        rows = self.store.run_read(match + f''' RETURN labels(n) AS labels,{_graph_node_properties('n')} AS properties,
            (coalesce(size(toString(coalesce(n.foaf_name,n['foaf:name']))),0)>$name_limit
                OR coalesce(size(toString(n.hermes_name)),0)>$name_limit
                OR coalesce(size(toString(n.hermes_description)),0)>$description_limit) AS content_truncated
            ORDER BY CASE WHEN n.uri=$focus_uri THEN 0 ELSE 1 END,n.uri,elementId(n)
            LIMIT $node_limit''', params)
        nodes = [_graph_node(row) for row in rows if _safe_node(row, self.config.urn_prefix)][:node_limit]
        result.update(nodes=nodes, total_nodes=total, truncated_nodes=total > len(nodes))
        if not nodes:
            return result
        params['node_uris'] = [node['uri'] for node in nodes]
        edge_match = f'''MATCH (a)-[r]->(b) WHERE {_active('a')} AND {_active('b')}
            AND a.uri IN $node_uris AND b.uri IN $node_uris
            AND (size($relation_aliases)=0 OR toUpper(type(r)) IN $relation_aliases)'''
        edge_total = self.store.run_read(edge_match + ' RETURN count(r) AS total', params)[0]['total']
        rows = self.store.run_read(edge_match + f''' RETURN labels(a) AS source_labels,{_graph_node_properties('a')} AS source_properties,
            labels(b) AS target_labels,{_graph_node_properties('b')} AS target_properties,type(r) AS type,
            {_graph_edge_properties()} AS properties,elementId(r) AS element_id
            ORDER BY CASE WHEN a.uri=$focus_uri OR b.uri=$focus_uri THEN 0 ELSE 1 END,
                a.uri,type(r),b.uri,elementId(r) LIMIT $edge_limit''', params)
        edges, allowed_uris = [], set(params['node_uris'])
        for row in rows:
            source = {'labels': row['source_labels'], 'properties': row['source_properties']}
            target = {'labels': row['target_labels'], 'properties': row['target_properties']}
            if (not _safe_node(source, self.config.urn_prefix) or not _safe_node(target, self.config.urn_prefix)
                    or source['properties']['uri'] not in allowed_uris or target['properties']['uri'] not in allowed_uris
                    or (relation_aliases and row['type'].upper() not in relation_aliases)):
                continue
            # Opaque, stable within the stored graph, and distinct for parallel
            # relationships. The database's internal identifier is not exposed.
            edge_id = hashlib.sha256(str(row['element_id']).encode('utf-8')).hexdigest()
            edges.append({'id': 'edge-' + edge_id, 'relation_type': _stored_relation(row['type'], schema),
                          'stored_type': row['type'],
                          'source': {key: value for key, value in _graph_node(source).items() if key in {'uri', 'name', 'type'}},
                          'target': {key: value for key, value in _graph_node(target).items() if key in {'uri', 'name', 'type'}},
                          'properties': _graph_edge_preview(row['properties']),
                          'preview': True, 'properties_truncated': True})
        edges = edges[:edge_limit]
        result.update(edges=edges, total_edges=edge_total, truncated_edges=edge_total > len(edges))
        return result

    def graph_overview(self, **kwargs):
        from .graph_exploration import GraphExploration
        return GraphExploration(self.config, self.store).overview(**kwargs)

    def graph_neighbors(self, **kwargs):
        from .graph_exploration import GraphExploration
        return GraphExploration(self.config, self.store).neighbors(**kwargs)

    def graph_paths(self, **kwargs):
        from .graph_exploration import GraphExploration
        return GraphExploration(self.config, self.store).paths(**kwargs)
