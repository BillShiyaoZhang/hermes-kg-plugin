"""Truthful aggregate coverage, stable neighborhoods and bounded private paths."""
from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from kg.config import KGConfig
from kg.graph_exploration import GraphExploration
from kg.ontology import OntologyService, _safe_node


PREFIX = 'urn:explorefixture:kg:'


def fixture_node(name, kind='Concept', **properties):
    return {'labels': [kind], 'properties': {'uri': PREFIX + name, 'hermes_name': name, **properties}}


class ExplorationStore:
    """A small semantic fixture; query-shape assertions complement live tests."""
    def __init__(self):
        self.nodes = {name: fixture_node(name) for name in ('a', 'b', 'c', 'd', 'isolated')}
        self.nodes['foreign'] = fixture_node('foreign')
        self.nodes['foreign']['properties']['uri'] = 'urn:other:kg:foreign'
        self.nodes['disabled'] = fixture_node('disabled', hermes_enabled=False)
        self.edges, self.calls = [], []
        for a, b, kind in (('a', 'b', 'RELATED_TO'), ('b', 'd', 'RELATED_TO'),
                           ('a', 'c', 'LINKED_TO'), ('c', 'd', 'RELATED_TO'),
                           ('d', 'a', 'CAUSED_BY'), ('a', 'a', 'RELATED_TO'),
                           ('a', 'foreign', 'RELATED_TO'), ('a', 'disabled', 'RELATED_TO')):
            self.add_edge(a, b, kind)
        self.failure = None

    def add_edge(self, source, target, kind='RELATED_TO'):
        self.edges.append({'source_labels': self.nodes[source]['labels'],
                           'source_properties': self.nodes[source]['properties'],
                           'target_labels': self.nodes[target]['labels'],
                           'target_properties': self.nodes[target]['properties'],
                           'type': kind, 'properties': {}, 'element_id': 'internal-' + str(len(self.edges))})

    def run_read(self, query, params):
        self.calls.append((query, dict(params)))
        if self.failure:
            raise self.failure
        active = lambda row: _safe_node(row, params['prefix'])
        nodes = [row for row in self.nodes.values() if active(row)]
        edges = [edge for edge in self.edges
                 if active({'labels': edge['source_labels'], 'properties': edge['source_properties']})
                 and active({'labels': edge['target_labels'], 'properties': edge['target_properties']})
                 and (not params['relation_aliases'] or edge['type'].upper() in params['relation_aliases'])]
        if 'graph_overview' in query:
            selected = [row for row in nodes if any(label in params['selected_labels'] for label in row['labels'])
                        and (not params['query'] or params['query'] in json.dumps(row['properties']).lower())]
            if 'graph_overview_nodes' in query:
                return [{'labels': list(labels), 'count': count}
                        for labels, count in Counter(tuple(row['labels']) for row in selected).items()]
            uris = {row['properties']['uri'] for row in selected}
            counts = Counter((tuple(edge['source_labels']), tuple(edge['target_labels']), edge['type'])
                             for edge in edges if edge['source_properties']['uri'] in uris and edge['target_properties']['uri'] in uris)
            return [{'source_labels': list(key[0]), 'target_labels': list(key[1]), 'type': key[2], 'count': count}
                    for key, count in counts.items()]
        if 'graph_center' in query:
            return [row for row in nodes if row['properties']['uri'] == params['center_uri']][:1]
        if 'graph_path_endpoints' in query:
            return [row for row in nodes if row['properties']['uri'] in params['endpoint_uris']]
        if 'graph_path_adjacency' in query:
            both = 'a.uri IN $frontier OR' in query
            selected = [edge for edge in edges if edge['source_properties']['uri'] in params['frontier']
                        or (both and edge['target_properties']['uri'] in params['frontier'])]
            return sorted(selected, key=lambda edge: (edge['source_properties']['uri'], edge['type'],
                                                       edge['target_properties']['uri'], edge['element_id']))[:params['read_limit']]
        center = params['center_uri']
        outgoing = '(center)-[r]->(n)' in query or 'AND a.uri=$center_uri' in query
        incoming = '(center)<-[r]-(n)' in query or 'AND b.uri=$center_uri' in query
        selected = [edge for edge in edges
                    if ((not incoming and edge['source_properties']['uri'] == center)
                        or (not outgoing and edge['target_properties']['uri'] == center))]
        if 'graph_neighbors_relation_counts' in query:
            return [{'type': kind, 'count': count} for kind, count in Counter(edge['type'] for edge in selected).items()]
        if 'graph_neighbors_edges' in query or 'graph_neighbors_edge_total' in query:
            selected = [edge for edge in selected if edge['source_properties']['uri'] in params['node_uris']
                        and edge['target_properties']['uri'] in params['node_uris']]
            if 'graph_neighbors_edge_total' in query:
                return [{'total': len(selected)}]
            return selected[:params['edge_limit']]
        uris = {(edge['target_properties']['uri'] if edge['source_properties']['uri'] == center else edge['source_properties']['uri'])
                for edge in selected} - {center}
        if 'graph_neighbors_total' in query:
            return [{'total': len(uris)}]
        return sorted([row for row in nodes if row['properties']['uri'] in uris and row['properties']['uri'] > params['after_uri']],
                      key=lambda row: row['properties']['uri'])[:params['page_limit']]


class GraphExplorationTests(unittest.TestCase):
    def setUp(self):
        self.store = ExplorationStore()
        self.service = OntologyService(KGConfig(urn_prefix=PREFIX), self.store)

    def test_full_overview_counts_isolated_nodes_actual_directions_and_canonical_aliases(self):
        result = self.service.graph_overview()
        self.assertTrue(result['stats_available'])
        self.assertEqual(result['total_nodes'], 5)
        self.assertEqual(result['total_edges'], 6)
        self.assertEqual(next(group['count'] for group in result['groups'] if group['type'] == 'concept'), 5)
        filtered = self.service.graph_overview(relation_types=['LINKED_TO'])
        self.assertEqual(filtered['total_nodes'], 5)
        self.assertEqual(filtered['total_edges'], 1)
        self.assertEqual(self.service.graph_overview(q='isolated')['total_nodes'], 1)
        self.assertEqual(self.service.graph_overview(q='isolated')['total_edges'], 0)
        failed_store = ExplorationStore()
        failed_store.failure = ConnectionError('private-password')
        failed = GraphExploration(KGConfig(urn_prefix=PREFIX), failed_store).overview()
        self.assertFalse(failed['stats_available'])
        self.assertIsNone(failed['total_nodes'])
        self.assertIsNone(failed['total_edges'])
        self.assertNotIn('private-password', json.dumps(failed))

    def test_neighbors_are_distinct_stable_cursor_pages_with_self_edges_and_parallel_relations(self):
        self.store.add_edge('a', 'b')
        first = self.service.graph_neighbors(center_uri=PREFIX + 'a', limit=1)
        self.assertEqual(first['total_neighbors'], 3)
        self.assertEqual([node['uri'] for node in first['nodes']], [PREFIX + 'a', PREFIX + 'b'])
        self.assertTrue(first['has_more'])
        self.assertEqual(len(first['edges']), 3)  # Two parallel edges plus center self-loop.
        second = self.service.graph_neighbors(center_uri=PREFIX + 'a', limit=1, cursor=first['next_cursor'])
        third = self.service.graph_neighbors(center_uri=PREFIX + 'a', limit=1, cursor=second['next_cursor'])
        self.assertEqual(second['nodes'][1]['uri'], PREFIX + 'c')
        self.assertEqual(third['nodes'][1]['uri'], PREFIX + 'd')
        self.assertFalse(third['has_more'])
        self.assertIsNone(third['next_cursor'])
        with self.assertRaises(ValueError):
            self.service.graph_neighbors(center_uri=PREFIX + 'a', direction='out', cursor=first['next_cursor'])
        for cursor in ('not-base64***', 'eyJhZnRlciI6MX0', 'a' * 4001):
            with self.subTest(cursor=cursor[:20]), self.assertRaises(ValueError):
                self.service.graph_neighbors(center_uri=PREFIX + 'a', cursor=cursor)

    def test_neighbor_direction_relation_filter_and_dense_edges_are_truthful(self):
        incoming = self.service.graph_neighbors(center_uri=PREFIX + 'a', direction='in', relation_types=['CAUSED_BY'])
        self.assertEqual(incoming['total_neighbors'], 1)
        self.assertEqual(incoming['relation_counts'], [{'relation_type': 'CAUSED_BY', 'direction': 'in', 'count': 1}])
        for _ in range(501):
            self.store.add_edge('a', 'b')
        dense = self.service.graph_neighbors(center_uri=PREFIX + 'a')
        self.assertEqual(len(dense['edges']), 500)
        self.assertTrue(dense['truncated_edges'])
        self.assertEqual(dense['total_edges'], 505)

    def test_paths_return_shortest_true_edges_and_respect_direction_relation_and_hop_bounds(self):
        result = self.service.graph_paths(source_uri=PREFIX + 'a', target_uri=PREFIX + 'd', direction='out')
        self.assertEqual(len(result['paths']), 2)
        self.assertTrue(all(len(path['edge_ids']) == 2 for path in result['paths']))
        self.assertEqual({node['uri'] for node in result['nodes']}, {PREFIX + key for key in ('a', 'b', 'c', 'd')})
        edge_ids = {edge['id'] for edge in result['edges']}
        self.assertTrue(all(set(path['edge_ids']) <= edge_ids for path in result['paths']))
        filtered = self.service.graph_paths(source_uri=PREFIX + 'a', target_uri=PREFIX + 'd', direction='out', relation_types=['RELATED_TO'])
        self.assertEqual(len(filtered['paths']), 1)
        one_hop = self.service.graph_paths(source_uri=PREFIX + 'a', target_uri=PREFIX + 'd', direction='out', max_hops=1)
        self.assertEqual(one_hop['paths'], [])
        self.assertFalse(one_hop['timed_out'])
        undirected = self.service.graph_paths(source_uri=PREFIX + 'a', target_uri=PREFIX + 'd', direction='both')
        self.assertEqual(len(undirected['paths'][0]['edge_ids']), 1)
        same = self.service.graph_paths(source_uri=PREFIX + 'a', target_uri=PREFIX + 'a', direction='out')
        self.assertEqual(same['paths'], [{'node_uris': [PREFIX + 'a'], 'edge_ids': []}])
        self.assertNotIn('internal-', json.dumps(result))

    def test_common_neighbors_and_result_limit_have_bounded_reads(self):
        result = self.service.graph_paths(source_uri=PREFIX + 'a', target_uri=PREFIX + 'd', mode='common_neighbors')
        self.assertEqual(len(result['paths']), 2)
        limited = self.service.graph_paths(source_uri=PREFIX + 'a', target_uri=PREFIX + 'd', mode='common_neighbors', limit=1)
        self.assertEqual(len(limited['paths']), 1)
        self.assertTrue(limited['truncated'])
        with patch('kg.graph_exploration.PATH_EDGE_BUDGET', 2):
            bounded = self.service.graph_paths(source_uri=PREFIX + 'a', target_uri=PREFIX + 'd', mode='common_neighbors')
        self.assertTrue(bounded['truncated'])
        self.assertFalse(bounded['timed_out'])
        self.assertTrue(all('LIMIT $read_limit' in query for query, _ in self.store.calls if 'graph_path_adjacency' in query))
        self.store.add_edge('d', 'b')
        outgoing = self.service.graph_paths(source_uri=PREFIX + 'a', target_uri=PREFIX + 'd', mode='common_neighbors', direction='out')
        self.assertEqual(outgoing['paths'][0]['node_uris'], [PREFIX + 'a', PREFIX + 'b', PREFIX + 'd'])
        self.assertEqual({edge['source']['uri'] for edge in outgoing['edges']}, {PREFIX + 'a', PREFIX + 'd'})
        self.assertEqual({edge['target']['uri'] for edge in outgoing['edges']}, {PREFIX + 'b'})

    def test_common_neighbor_node_budget_is_enforced_independently_of_edge_budget(self):
        with patch('kg.graph_exploration.PATH_NODE_BUDGET', 3):
            result = self.service.graph_paths(source_uri=PREFIX + 'a', target_uri=PREFIX + 'd', mode='common_neighbors')
        self.assertTrue(result['truncated'])
        self.assertFalse(result['timed_out'])
        self.assertLessEqual(len(result['nodes']), 3)
        self.assertEqual(len(result['paths']), 1)

    def test_neighbors_reject_nonincident_edges_when_an_adapter_ignores_the_query(self):
        original = self.store.run_read
        def read(query, params):
            rows = original(query, params)
            if 'graph_neighbors_edges' in query:
                rows = [*rows, self.store.edges[1]]  # b -> d, both visible but not incident to a.
            return rows
        with patch.object(self.store, 'run_read', side_effect=read):
            result = self.service.graph_neighbors(center_uri=PREFIX + 'a')
        self.assertTrue(all(PREFIX + 'a' in {edge['source']['uri'], edge['target']['uri']} for edge in result['edges']))

    def test_paths_exclude_inactive_intermediate_nodes_and_foreign_or_absent_endpoints(self):
        self.store.nodes['c']['properties']['hermes_enabled'] = False
        self.store.add_edge('foreign', 'd')
        result = self.service.graph_paths(source_uri=PREFIX + 'a', target_uri=PREFIX + 'd', direction='out')
        self.assertEqual(len(result['paths']), 1)
        self.assertNotIn(PREFIX + 'c', json.dumps(result))
        foreign = self.service.graph_paths(source_uri='urn:other:kg:foreign', target_uri=PREFIX + 'd')
        missing = self.service.graph_paths(source_uri=PREFIX + 'missing', target_uri=PREFIX + 'd')
        self.assertEqual(foreign, missing)
        self.assertEqual(foreign['nodes'], [])
        missing_neighbor = self.service.graph_neighbors(center_uri=PREFIX + 'missing')
        self.assertFalse(missing_neighbor['focus_found'])
        self.assertEqual(missing_neighbor['total_neighbors'], 0)

    def test_timeout_is_distinct_from_no_path_and_has_no_private_error_details(self):
        with patch('kg.graph_exploration.GraphExploration._timed_read', side_effect=TimeoutError('private-query')):
            result = self.service.graph_paths(source_uri=PREFIX + 'a', target_uri=PREFIX + 'd')
        self.assertTrue(result['timed_out'])
        self.assertTrue(result['truncated'])
        self.assertNotIn('private-query', json.dumps(result))
        no_path = self.service.graph_paths(source_uri=PREFIX + 'isolated', target_uri=PREFIX + 'd')
        self.assertFalse(no_path['timed_out'])
        self.assertFalse(no_path['truncated'])
        self.assertEqual(no_path['paths'], [])

    def test_parameters_are_strict_validated_before_access_and_queries_remain_parameterized(self):
        for method, kwargs in (
                (self.service.graph_overview, {'types': ['Device']}),
                (self.service.graph_overview, {'relation_types': ['INVALID']}),
                (self.service.graph_overview, {'q': None}),
                (self.service.graph_neighbors, {'center_uri': PREFIX + 'a', 'limit': True}),
                (self.service.graph_neighbors, {'center_uri': '', 'direction': 'both'}),
                (self.service.graph_paths, {'source_uri': PREFIX + 'a', 'target_uri': PREFIX + 'd', 'max_hops': 7}),
                (self.service.graph_paths, {'source_uri': PREFIX + 'a', 'target_uri': PREFIX + 'd', 'limit': '5'})):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                method(**kwargs)
        self.assertEqual(self.store.calls, [])
        self.service.graph_paths(source_uri=PREFIX + 'a', target_uri=PREFIX + 'd')
        for query, params in self.store.calls:
            self.assertNotIn(PREFIX, query)
            self.assertEqual(params['prefix'], PREFIX)
            self.assertIn('KGLegacyAlias', query)
            self.assertIn('KGQuarantined', query)

    def test_production_path_reads_set_a_real_remaining_transaction_timeout(self):
        timeouts = []
        class Transaction:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def run(self, query, params): return []
        class Session:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def begin_transaction(self, *, timeout):
                timeouts.append(timeout)
                return Transaction()
        driver = SimpleNamespace(session=lambda **kwargs: Session())
        service = GraphExploration(KGConfig(urn_prefix=PREFIX), SimpleNamespace(driver=driver))
        service.paths(source_uri=PREFIX + 'a', target_uri=PREFIX + 'd')
        self.assertEqual(len(timeouts), 1)
        self.assertGreater(timeouts[0], 0)
        self.assertLessEqual(timeouts[0], 5)


try:
    from fastapi.testclient import TestClient
    from kg.console_web import create_app
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, 'console extras not installed')
class GraphExplorationApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ExplorationStore()
        self.app = create_app(KGConfig(home=Path(self.tmp.name), urn_prefix=PREFIX), token='fixture-exploration-token',
                              memory=SimpleNamespace(store=self.store), search=SimpleNamespace(), background=False)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.auth = {'Authorization': 'Bearer fixture-exploration-token'}

    def test_all_exploration_routes_are_authenticated_post_only_and_no_store(self):
        for suffix, body in (('overview', {}), ('neighbors', {'center_uri': PREFIX + 'a'}),
                             ('paths', {'source_uri': PREFIX + 'a', 'target_uri': PREFIX + 'd'})):
            route = '/api/v1/graph/' + suffix + '/query'
            self.assertEqual(self.client.post(route, json=body).status_code, 401)
            self.assertIn(self.client.get(route, headers=self.auth).status_code, (404, 405))
            result = self.client.post(route, headers=self.auth, json=body)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.headers['cache-control'], 'no-store')
            self.assertEqual(self.client.post(route, headers={**self.auth, 'Origin': 'https://foreign.invalid'}, json=body).status_code, 403)

    def test_strict_route_models_and_namespace_override_do_not_echo_private_inputs(self):
        for suffix, body in (('overview', {'namespace': 'private-secret'}), ('overview', {'types': [1]}),
                             ('neighbors', {'center_uri': PREFIX + 'a', 'limit': True}),
                             ('neighbors', {'center_uri': PREFIX + 'a', 'cursor': 4}),
                             ('paths', {'source_uri': PREFIX + 'a', 'target_uri': PREFIX + 'd', 'max_hops': '3'}),
                             ('paths', {'source_uri': PREFIX + 'a', 'target_uri': PREFIX + 'd', 'direction': 'in'})):
            result = self.client.post('/api/v1/graph/' + suffix + '/query', headers=self.auth, json=body)
            self.assertEqual(result.status_code, 422, result.text)
            self.assertNotIn('private-secret', result.text)
        self.assertEqual(self.store.calls, [])


@unittest.skipUnless(os.environ.get('KG_TEST_BOLT_URL'), 'isolated Neo4j test URL not configured')
class GraphExplorationNeo4jTests(unittest.TestCase):
    def setUp(self):
        # Reuse the isolated fixture without inheriting/repeating its test cases.
        from kg.tests.test_graph import GraphNeo4jTests
        GraphNeo4jTests.setUp(self)

    def clear(self):
        with self.store.driver.session(database=self.config.database) as session:
            session.run('MATCH (n) WHERE n.uri STARTS WITH $prefix OR n.uri STARTS WITH $foreign DETACH DELETE n',
                        {'prefix': self.prefix, 'foreign': self.foreign}).consume()

    def test_live_overview_counts_all_records_without_sampling(self):
        result = self.service.graph_overview()
        self.assertTrue(result['stats_available'], result)
        self.assertEqual(result['total_nodes'], 208)
        self.assertGreater(result['total_edges'], 505)
        filtered = self.service.graph_overview(types=['person'])
        self.assertEqual(filtered['total_nodes'], 2)
        self.assertEqual(filtered['total_edges'], 2)  # knows + anchor self-loop
        self.assertEqual(self.service.graph_overview(q='late needle')['total_nodes'], 1)
        self.assertEqual(self.service.graph_overview(q='late needle')['total_edges'], 0)

    def test_live_neighbors_cursor_direction_aliases_and_isolation(self):
        center = self.prefix + 'person-anchor'
        first = self.service.graph_neighbors(center_uri=center, limit=2)
        second = self.service.graph_neighbors(center_uri=center, limit=2, cursor=first['next_cursor'])
        third = self.service.graph_neighbors(center_uri=center, limit=2, cursor=second['next_cursor'])
        self.assertEqual(first['total_neighbors'], 5)
        self.assertEqual(len({node['uri'] for result in (first, second, third) for node in result['nodes']}), 6)
        self.assertFalse(third['has_more'])
        self.assertEqual(self.service.graph_neighbors(center_uri=center, relation_types=['KNOWS'])['total_neighbors'], 1)
        self.assertNotIn(self.foreign, json.dumps(first))

    def test_live_paths_enforce_active_intermediate_nodes_and_server_timeout(self):
        result = self.service.graph_paths(source_uri=self.prefix + 'person-friend',
                                         target_uri=self.prefix + 'event-late', direction='both')
        self.assertFalse(result['timed_out'])
        self.assertEqual(len(result['paths']), 1)
        self.assertEqual(len(result['paths'][0]['edge_ids']), 2)
        self.assertNotIn(self.foreign, json.dumps(result))
        outgoing = self.service.graph_paths(source_uri=self.prefix + 'person-friend',
                                           target_uri=self.prefix + 'event-late', direction='out')
        self.assertEqual(outgoing['paths'], [])
        common = self.service.graph_paths(source_uri=self.prefix + 'person-friend', target_uri=self.prefix + 'event-late',
                                         mode='common_neighbors', direction='both')
        self.assertEqual(len(common['paths']), 1)
        self.assertEqual(self.service.graph_paths(source_uri=self.foreign + 'person-private',
                                                  target_uri=self.prefix + 'event-late')['nodes'], [])
        with self.store.driver.session(database=self.config.database) as session:
            session.run('''MATCH (foreign {uri:$foreign+'person-private'}), (disabled {uri:$prefix+'concept-disabled'})
                CREATE (a:Concept {uri:$prefix+'concept-bridge-source'}),
                    (b:Concept {uri:$prefix+'concept-bridge-target'}),
                    (a)-[:RELATED_TO]->(foreign),(foreign)-[:RELATED_TO]->(b),
                    (a)-[:RELATED_TO]->(disabled),(disabled)-[:RELATED_TO]->(b)''',
                        {'prefix': self.prefix, 'foreign': self.foreign}).consume()
        blocked = self.service.graph_paths(source_uri=self.prefix + 'concept-bridge-source',
                                           target_uri=self.prefix + 'concept-bridge-target', direction='out')
        self.assertEqual(blocked['paths'], [])
        self.assertFalse(blocked['truncated'])
        self.assertFalse(blocked['timed_out'])


if __name__ == '__main__':
    unittest.main()
