"""Profile-scoped aggregation, cursor neighborhoods and bounded path exploration.

Path search uses bounded breadth-first reads rather than an unbounded variable
length Cypher expansion. Every production read runs in a transaction carrying
the remaining five-second request budget; it does not change store defaults.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time

from .ontology import (SCOPE, GRAPH_NAME_LIMIT, GRAPH_DESCRIPTION_LIMIT, GRAPH_PROVENANCE_ITEMS,
                       _active, _safe_node, _graph_node, _graph_node_properties,
                       _graph_edge_properties, _graph_edge_preview, _stored_relation,
                       _graph_relation_aliases)
from .schema import (ENTITY_LABELS, entity_type, runtime_entity_labels,
                     runtime_kind, schema_for_store)

PATH_SECONDS = 5.0
PATH_EDGE_BUDGET = 5000
PATH_NODE_BUDGET = 2000
EDGE_LIMIT = 500


def _selection(alias):
    return f'''any(label IN labels({alias}) WHERE label IN $selected_labels)
        AND ($query='' OR any(value IN [{alias}.uri,{alias}.foaf_name,{alias}.hermes_name,{alias}.hermes_description]
            + coalesce({alias}.hermes_aliasNames,[])
            + [key IN keys({alias}) WHERE key STARTS WITH 'x_' | {alias}[key]]
            WHERE toLower(toString(value)) CONTAINS $query))'''


def _node_return(alias='n'):
    return f'''labels({alias}) AS labels,{_graph_node_properties(alias)} AS properties,
        {_content_truncated(alias)} AS content_truncated'''


def _content_truncated(alias):
    return f'''(coalesce(size(toString(coalesce({alias}.foaf_name,{alias}['foaf:name']))),0)>$name_limit
            OR coalesce(size(toString({alias}.hermes_name)),0)>$name_limit
            OR coalesce(size(toString({alias}.hermes_description)),0)>$description_limit)'''


def _edge_return():
    return f'''labels(a) AS source_labels,{_graph_node_properties('a')} AS source_properties,
        labels(b) AS target_labels,{_graph_node_properties('b')} AS target_properties,
        type(r) AS type,{_graph_edge_properties()} AS properties,elementId(r) AS element_id,
        {_content_truncated('a')} AS source_content_truncated,{_content_truncated('b')} AS target_content_truncated'''


def _kind(labels):
    return runtime_kind(labels)


def _integer(name, value, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f'{name} must be an integer from 1 to {maximum}')


def _uri(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValueError('uri must be nonempty text of at most 2000 characters')
    return value.strip()


class GraphExploration:
    def __init__(self, config, store):
        self.config, self.store = config, store
        self._ontology = None
        self._labels = dict(ENTITY_LABELS)

    def _params(self, relation_types=None):
        self._ontology = schema_for_store(self.store)
        self._labels = runtime_entity_labels(self._ontology)
        return {'prefix': self.config.urn_prefix, 'entity_labels': list(self._labels.values()),
                'name_limit': GRAPH_NAME_LIMIT, 'description_limit': GRAPH_DESCRIPTION_LIMIT,
                'provenance_items': GRAPH_PROVENANCE_ITEMS,
                'relation_aliases': _graph_relation_aliases(relation_types, self._ontology)}

    def _snapshot(self):
        return {'namespace': self.config.urn_prefix, 'scope': SCOPE, 'edge_scope': 'returned_nodes',
                'nodes': [], 'edges': [], 'total_nodes': 0, 'total_edges': 0,
                'truncated_nodes': False, 'truncated_edges': False, 'focus_found': False}

    def _nodes(self, rows):
        result = {}
        for row in rows:
            if _safe_node(row, self.config.urn_prefix) and set(row['labels']) & set(self._labels.values()):
                node = _graph_node(row)
                result[node['uri']] = node
        return result

    def _edge_nodes(self, row):
        return self._nodes([{'labels': row[side + '_labels'], 'properties': row[side + '_properties'],
                             'content_truncated': row.get(side + '_content_truncated', False)}
                            for side in ('source', 'target')])

    def _edge(self, row, params):
        source = {'labels': row['source_labels'], 'properties': row['source_properties']}
        target = {'labels': row['target_labels'], 'properties': row['target_properties']}
        if (not _safe_node(source, self.config.urn_prefix) or not _safe_node(target, self.config.urn_prefix)
                or not set(source['labels']) & set(self._labels.values())
                or not set(target['labels']) & set(self._labels.values())
                or (params['relation_aliases'] and row['type'].upper() not in params['relation_aliases'])):
            return None
        return {'id': 'edge-' + hashlib.sha256(str(row['element_id']).encode('utf-8')).hexdigest(),
                'relation_type': _stored_relation(row['type']), 'stored_type': row['type'],
                'source': {key: value for key, value in _graph_node(source).items() if key in {'uri', 'name', 'type'}},
                'target': {key: value for key, value in _graph_node(target).items() if key in {'uri', 'name', 'type'}},
                'properties': _graph_edge_preview(row['properties']), 'preview': True, 'properties_truncated': True}

    def overview(self, *, q='', types=None, relation_types=None):
        if not isinstance(q, str) or len(q) > 200:
            raise ValueError('q must be text of at most 200 characters')
        types = [] if types is None else types
        params = self._params(relation_types)
        if not isinstance(types, list) or len(types) > len(self._labels) or any(not isinstance(value, str) for value in types):
            raise ValueError('types must contain supported entity types')
        selected = sorted({self._labels[entity_type(value, self._ontology)] for value in types}) or list(self._labels.values())
        params.update(query=q.strip().lower(), selected_labels=selected)
        result = {'namespace': self.config.urn_prefix, 'scope': SCOPE, 'stats_available': False,
                  'stats_error': None, 'total_nodes': None, 'total_edges': None, 'groups': [], 'relations': []}
        try:
            nodes = self.store.run_read(f'''/* graph_overview_nodes */ MATCH (n) WHERE {_active('n')}
                AND {_selection('n')} RETURN labels(n) AS labels,count(n) AS count''', params)
            edges = self.store.run_read(f'''/* graph_overview_edges */ MATCH (a)-[r]->(b)
                WHERE {_active('a')} AND {_active('b')} AND {_selection('a')} AND {_selection('b')}
                AND (size($relation_aliases)=0 OR toUpper(type(r)) IN $relation_aliases)
                RETURN labels(a) AS source_labels,labels(b) AS target_labels,type(r) AS type,count(r) AS count''', params)
            groups = {key: 0 for key, label in self._labels.items() if label in selected}
            relations = {}
            for row in nodes:
                kind = _kind(row['labels'])
                if kind in groups:
                    groups[kind] += row['count']
            for row in edges:
                source, target = _kind(row['source_labels']), _kind(row['target_labels'])
                if source not in groups or target not in groups:
                    continue
                key = (source, target, _stored_relation(row['type']))
                relations[key] = relations.get(key, 0) + row['count']
            result.update(stats_available=True, total_nodes=sum(groups.values()), total_edges=sum(relations.values()),
                          groups=[{'type': key, 'count': value} for key, value in sorted(groups.items())],
                          relations=[{'source_type': key[0], 'target_type': key[1], 'relation_type': key[2], 'count': value}
                                     for key, value in sorted(relations.items())])
        except Exception as exc:
            result['stats_error'] = {'error_type': type(exc).__name__}
        return result

    def _cursor(self, center, direction, aliases, cursor):
        scope = hashlib.sha256(json.dumps([self.config.urn_prefix, center, direction, aliases],
                                         separators=(',', ':')).encode()).hexdigest()
        after = ''
        if cursor is not None:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 4000:
                raise ValueError('invalid cursor')
            try:
                decoded = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
                if (not isinstance(decoded, dict) or decoded.get('scope') != scope
                        or not isinstance(decoded.get('after'), str) or not 1 <= len(decoded['after']) <= 2000
                        or not decoded['after'].startswith(self.config.urn_prefix)):
                    raise ValueError('invalid cursor')
                after = decoded['after']
            except (ValueError, TypeError, UnicodeDecodeError) as exc:
                raise ValueError('invalid cursor') from exc
        return scope, after

    def neighbors(self, *, center_uri, direction='both', relation_types=None, cursor=None, limit=30):
        center_uri = _uri(center_uri)
        if not isinstance(direction, str) or direction not in {'in', 'out', 'both'}:
            raise ValueError('unsupported direction')
        _integer('limit', limit, 100)
        params = {**self._params(relation_types), 'center_uri': center_uri, 'page_limit': limit + 1,
                  'edge_limit': EDGE_LIMIT}
        scope, params['after_uri'] = self._cursor(center_uri, direction, params['relation_aliases'], cursor)
        result = {**self._snapshot(), 'center_uri': center_uri, 'total_neighbors': 0,
                  'next_cursor': None, 'has_more': False, 'relation_counts': []}
        if not center_uri.startswith(self.config.urn_prefix):
            return result
        rows = self.store.run_read(f'''/* graph_center */ MATCH (n) WHERE {_active('n')}
            AND n.uri=$center_uri RETURN {_node_return()} LIMIT 1''', params)
        centers = self._nodes(rows)
        if center_uri not in centers:
            return result
        pattern = {'out': '(center)-[r]->(n)', 'in': '(center)<-[r]-(n)', 'both': '(center)-[r]-(n)'}[direction]
        match = f'''MATCH {pattern} WHERE {_active('center')} AND {_active('n')}
            AND center.uri=$center_uri AND n.uri<>$center_uri
            AND (size($relation_aliases)=0 OR toUpper(type(r)) IN $relation_aliases)'''
        total = self.store.run_read('/* graph_neighbors_total */ ' + match + ' RETURN count(DISTINCT n.uri) AS total', params)[0]['total']
        rows = self.store.run_read('/* graph_neighbors_page */ ' + match + f'''
            AND n.uri>$after_uri WITH n.uri AS uri,head(collect(DISTINCT n)) AS n
            RETURN {_node_return()} ORDER BY uri LIMIT $page_limit''', params)
        page = sorted((uri, value) for uri, value in self._nodes(rows).items() if uri != center_uri and uri > params['after_uri'])
        has_more = len(page) > limit
        page = page[:limit]
        if has_more and page:
            result['next_cursor'] = base64.urlsafe_b64encode(json.dumps({'scope': scope, 'after': page[-1][0]},
                                                                      separators=(',', ':')).encode()).decode().rstrip('=')
        params['node_uris'] = [center_uri, *(uri for uri, _ in page)]
        direction_filter = {'out': 'a.uri=$center_uri', 'in': 'b.uri=$center_uri',
                            'both': '(a.uri=$center_uri OR b.uri=$center_uri)'}[direction]
        edge_match = f'''MATCH (a)-[r]->(b) WHERE {_active('a')} AND {_active('b')}
            AND a.uri IN $node_uris AND b.uri IN $node_uris AND {direction_filter}
            AND (size($relation_aliases)=0 OR toUpper(type(r)) IN $relation_aliases)'''
        edge_total = self.store.run_read('/* graph_neighbors_edge_total */ ' + edge_match + ' RETURN count(r) AS total', params)[0]['total']
        edge_rows = self.store.run_read('/* graph_neighbors_edges */ ' + edge_match + f'''
            RETURN {_edge_return()} ORDER BY a.uri,type(r),b.uri,elementId(r) LIMIT $edge_limit''', params)
        edges = []
        for row in edge_rows:
            edge = self._edge(row, params)
            if (edge and edge['source']['uri'] in params['node_uris'] and edge['target']['uri'] in params['node_uris']
                    and ((direction != 'in' and edge['source']['uri'] == center_uri)
                         or (direction != 'out' and edge['target']['uri'] == center_uri))):
                edges.append(edge)
        counts = {}
        for orientation in ('out', 'in') if direction == 'both' else (direction,):
            count_pattern = '(center)-[r]->(n)' if orientation == 'out' else '(center)<-[r]-(n)'
            rows = self.store.run_read(f'''/* graph_neighbors_relation_counts */ MATCH {count_pattern}
                WHERE {_active('center')} AND {_active('n')} AND center.uri=$center_uri
                AND (size($relation_aliases)=0 OR toUpper(type(r)) IN $relation_aliases)
                RETURN type(r) AS type,count(r) AS count''', params)
            for row in rows:
                key = (_stored_relation(row['type']), orientation)
                counts[key] = counts.get(key, 0) + row['count']
        result.update(nodes=[centers[center_uri], *(value for _, value in page)], edges=edges[:EDGE_LIMIT],
                      total_nodes=total + 1, total_edges=edge_total, focus_found=True,
                      truncated_nodes=has_more or bool(params['after_uri']), truncated_edges=edge_total > len(edges[:EDGE_LIMIT]),
                      total_neighbors=total, has_more=has_more,
                      relation_counts=[{'relation_type': key[0], 'direction': key[1], 'count': value}
                                       for key, value in sorted(counts.items())])
        return result

    def _timed_read(self, query, params, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('path query budget exhausted')
        # Production KGStore exposes its driver. A plain run_read adapter is
        # useful for tests; adapters must provide their own cancellation policy.
        if hasattr(self.store, 'driver'):
            timeout = min(remaining, float(getattr(self.config, 'query_timeout', remaining)))
            with self.store.driver.session(database=self.config.database, default_access_mode='READ') as session:
                with session.begin_transaction(timeout=max(0.001, timeout)) as transaction:
                    rows = [dict(row) for row in transaction.run(query, params)]
        else:
            rows = self.store.run_read(query, params)
        if time.monotonic() >= deadline:
            raise TimeoutError('path query budget exhausted')
        return rows

    def _adjacency(self, frontier, direction, params, read_limit, deadline):
        endpoint_filter = 'a.uri IN $frontier' if direction == 'out' else '(a.uri IN $frontier OR b.uri IN $frontier)'
        return self._timed_read(f'''/* graph_path_adjacency */ MATCH (a)-[r]->(b)
            WHERE {_active('a')} AND {_active('b')} AND {endpoint_filter}
            AND (size($relation_aliases)=0 OR toUpper(type(r)) IN $relation_aliases)
            RETURN {_edge_return()} ORDER BY a.uri,type(r),b.uri,elementId(r) LIMIT $read_limit''',
            {**params, 'frontier': sorted(frontier), 'read_limit': read_limit + 1}, deadline)

    def paths(self, *, source_uri, target_uri, mode='shortest', relation_types=None,
              direction='both', max_hops=6, limit=5):
        source_uri, target_uri = _uri(source_uri), _uri(target_uri)
        if (not isinstance(direction, str) or not isinstance(mode, str)
                or direction not in {'out', 'both'} or mode not in {'shortest', 'common_neighbors'}):
            raise ValueError('unsupported path mode or direction')
        _integer('max_hops', max_hops, 6)
        _integer('limit', limit, 5)
        params = {**self._params(relation_types), 'endpoint_uris': sorted({source_uri, target_uri})}
        result = {**self._snapshot(), 'paths': [], 'max_hops': max_hops, 'truncated': False, 'timed_out': False}
        if any(not uri.startswith(self.config.urn_prefix) for uri in (source_uri, target_uri)):
            return result
        deadline = time.monotonic() + PATH_SECONDS
        try:
            rows = self._timed_read(f'''/* graph_path_endpoints */ MATCH (n) WHERE {_active('n')}
                AND n.uri IN $endpoint_uris RETURN {_node_return()} ORDER BY n.uri LIMIT 3''', params, deadline)
            nodes = self._nodes(rows)
            if not all(uri in nodes for uri in (source_uri, target_uri)):
                return result
            result['focus_found'] = True
            edges, found = {}, []
            if mode == 'common_neighbors':
                if max_hops >= 2:
                    neighborhoods = []
                    for center in (source_uri, target_uri):
                        rows = self._adjacency({center}, direction, params, PATH_EDGE_BUDGET // 2, deadline)
                        if len(rows) > PATH_EDGE_BUDGET // 2:
                            result['truncated'] = True
                        neighbors = {}
                        for row in rows[:PATH_EDGE_BUDGET // 2]:
                            edge = self._edge(row, params)
                            if not edge:
                                continue
                            a, b = edge['source']['uri'], edge['target']['uri']
                            if a == center:
                                other = b
                            elif direction == 'both' and b == center:
                                other = a
                            else:
                                continue
                            if other in {source_uri, target_uri}:
                                continue
                            if other not in nodes and len(nodes) >= PATH_NODE_BUDGET:
                                result['truncated'] = True
                                continue
                            neighbors.setdefault(other, []).append(edge['id'])
                            edges[edge['id']] = edge
                            nodes.update(self._edge_nodes(row))
                        neighborhoods.append(neighbors)
                    for common in sorted(neighborhoods[0].keys() & neighborhoods[1].keys()):
                        for left in neighborhoods[0][common]:
                            for right in neighborhoods[1][common]:
                                found.append({'node_uris': [source_uri, common, target_uri], 'edge_ids': [left, right]})
                                if len(found) > limit:
                                    break
                            if len(found) > limit:
                                break
                        if len(found) > limit:
                            break
            else:
                distances, predecessors = {source_uri: 0}, {}
                frontier, consumed = {source_uri}, 0
                for depth in range(max_hops):
                    if source_uri == target_uri or not frontier or target_uri in distances:
                        break
                    remaining = PATH_EDGE_BUDGET - consumed
                    if remaining <= 0:
                        result['truncated'] = True
                        break
                    rows = self._adjacency(frontier, direction, params, remaining, deadline)
                    if len(rows) > remaining:
                        result['truncated'] = True
                    consumed += min(len(rows), remaining)
                    next_frontier = set()
                    for row in rows[:remaining]:
                        edge = self._edge(row, params)
                        if not edge:
                            continue
                        a, b = edge['source']['uri'], edge['target']['uri']
                        traversals = [(a, b)] + ([(b, a)] if direction == 'both' and a != b else [])
                        for previous, following in traversals:
                            if previous not in frontier or distances.get(following, depth + 1) != depth + 1:
                                continue
                            if following not in distances:
                                if len(distances) >= PATH_NODE_BUDGET:
                                    result['truncated'] = True
                                    continue
                                distances[following] = depth + 1
                                next_frontier.add(following)
                            entries = predecessors.setdefault(following, [])
                            if len(entries) <= limit:
                                entries.append((previous, edge['id']))
                            else:
                                result['truncated'] = True
                            edges[edge['id']] = edge
                            nodes.update(self._edge_nodes(row))
                    frontier = next_frontier
                def collect(current, uris, edge_ids):
                    if time.monotonic() >= deadline:
                        raise TimeoutError('path query budget exhausted')
                    if len(found) > limit:
                        return
                    if current == source_uri:
                        found.append({'node_uris': list(reversed(uris)), 'edge_ids': list(reversed(edge_ids))})
                        return
                    for previous, edge_id in predecessors.get(current, []):
                        collect(previous, [*uris, previous], [*edge_ids, edge_id])
                        if len(found) > limit:
                            return
                if target_uri in distances:
                    collect(target_uri, [target_uri], [])
            if time.monotonic() >= deadline:
                raise TimeoutError('path query budget exhausted')
            result['truncated'] = result['truncated'] or len(found) > limit
            result['paths'] = found[:limit]
            displayed = {source_uri, target_uri} | {uri for path in result['paths'] for uri in path['node_uris']}
            displayed_edges = {edge_id for path in result['paths'] for edge_id in path['edge_ids']}
            result.update(nodes=[nodes[uri] for uri in sorted(displayed) if uri in nodes],
                          edges=[edges[edge_id] for edge_id in sorted(displayed_edges)],
                          total_nodes=len(displayed), total_edges=len(displayed_edges))
        except Exception as exc:
            code = str(getattr(exc, 'code', '')).lower()
            if isinstance(exc, TimeoutError) or 'timedout' in code or 'timeout' in code:
                result.update(timed_out=True, truncated=True)
            else:
                raise
        return result
