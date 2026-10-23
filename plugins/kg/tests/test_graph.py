"""Bounded graph exploration, global search, focus semantics and private scopes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import uuid

from kg.config import KGConfig
from kg.ontology import OntologyService


def node(uri, kind='Concept', **properties):
    return {'labels': [kind], 'properties': {'uri': uri, 'hermes_name': 'Fixture', **properties}}


class GraphStore:
    def __init__(self):
        self.calls = []
        self.nodes = [node('urn:graphfixture:kg:concept-one'), node('urn:graphfixture:kg:concept-two')]
        self.edges = [{'source_labels': self.nodes[0]['labels'], 'source_properties': self.nodes[0]['properties'],
                       'target_labels': self.nodes[1]['labels'], 'target_properties': self.nodes[1]['properties'],
                       'type': 'UNKNOWN_LEGACY', 'properties': {'hermes_relLabel': 'Fixture relation'}, 'element_id': 'fixture-edge-1'}]
        self.failure = None

    def run_read(self, query, params):
        self.calls.append((query, dict(params)))
        if self.failure:
            raise self.failure
        if 'count(focus)' in query:
            return [{'total': int(any(item['properties']['uri'] == params['focus_uri'] for item in self.nodes))}]
        if 'count(n)' in query:
            return [{'total': len(self.nodes)}]
        if 'count(r)' in query:
            return [{'total': len(self.edges)}]
        return self.nodes if 'RETURN labels(n)' in query else self.edges


class GraphServiceTests(unittest.TestCase):
    def setUp(self):
        self.config = KGConfig(urn_prefix='urn:graphfixture:kg:')
        self.store = GraphStore()
        self.service = OntologyService(self.config, self.store)

    def test_queries_parameterize_all_private_inputs_and_bound_both_return_sets(self):
        text = "O'Brien'); MATCH (private) //"
        result = self.service.graph(q=text, types=['Concept'], node_limit=1, edge_limit=1)
        self.assertEqual(len(result['nodes']), 1)
        self.assertEqual(result['total_nodes'], 2)
        self.assertTrue(result['truncated_nodes'])
        # The fake intentionally ignores its query; the response must still
        # reject an edge to a node that was not returned.
        self.assertEqual(result['edges'], [])
        self.assertEqual(result['edge_scope'], 'returned_nodes')
        self.assertIsNone(result['focus_found'])
        for query, params in self.store.calls:
            self.assertNotIn(text, query)
            self.assertEqual(params['query'], text.lower())
            self.assertEqual(params['prefix'], self.config.urn_prefix)
            self.assertIn('KGQuarantined', query)
            self.assertIn('KGLegacyAlias', query)
            self.assertIn('KGLegacyUnresolved', query)
        reads = [query for query, _ in self.store.calls if 'count(' not in query]
        self.assertTrue(any('LIMIT $node_limit' in query for query in reads))
        self.assertTrue(any('LIMIT $edge_limit' in query for query in reads))

    def test_graph_edge_ids_are_stable_parallel_safe_and_unknown_types_remain_honest(self):
        self.store.edges.append({**self.store.edges[0], 'element_id': 'fixture-edge-2'})
        self.store.nodes[0]['properties'].update(hermes_name='x' * 500, hermes_description='d' * 5000,
                                                hermes_evidenceJson='private-evidence-' * 10000)
        self.store.edges[0]['properties'].update(hermes_sourceSessions=['session-' + str(i) for i in range(30)],
                                                hermes_evidenceJson='private-relation-evidence-' * 10000)
        first = self.service.graph()
        second = self.service.graph()
        self.assertEqual(first['edges'], second['edges'])
        self.assertEqual(len({edge['id'] for edge in first['edges']}), 2)
        self.assertEqual(first['edges'][0]['relation_type'], 'UNKNOWN_LEGACY')
        self.assertEqual(first['edges'][0]['stored_type'], 'UNKNOWN_LEGACY')
        self.assertNotIn('fixture-edge-', json.dumps(first))
        self.assertEqual(len(first['nodes'][0]['name']), 240)
        self.assertEqual(len(first['nodes'][0]['description']), 1200)
        self.assertTrue(first['nodes'][0]['content_truncated'])
        self.assertTrue(first['nodes'][0]['preview'])
        self.assertTrue(first['edges'][0]['properties_truncated'])
        self.assertEqual(len(first['edges'][0]['properties']['hermes_sourceSessions']), 8)
        self.assertNotIn('private-evidence', json.dumps(first))
        self.assertNotIn('private-relation-evidence', json.dumps(first))

    def test_defence_in_depth_rejects_foreign_disabled_and_nonreturned_endpoints(self):
        self.store.nodes += [node('urn:foreign:kg:concept-hidden', shared=True),
                             node('urn:graphfixture:kg:concept-disabled', hermes_enabled=False)]
        self.store.nodes[3]['labels'].append('KGQuarantined')
        self.store.edges += [{**self.store.edges[0], 'element_id': 'foreign-edge',
                              'target_properties': self.store.nodes[2]['properties']}]
        result = self.service.graph()
        self.assertEqual(len(result['nodes']), 2)
        self.assertEqual(len(result['edges']), 1)
        self.assertNotIn('hidden', json.dumps(result))
        self.assertNotIn('disabled', json.dumps(result))

    def test_invalid_query_shapes_fail_before_database_access(self):
        for kwargs in ({'q': 'a' * 201}, {'q': None}, {'types': 'person'}, {'types': [None]},
                       {'types': ['Device']}, {'types': ['person'] * 7}, {'node_limit': 0},
                       {'node_limit': 201}, {'node_limit': True}, {'edge_limit': 501},
                       {'edge_limit': False}, {'edge_limit': 1.5}, {'focus_uri': ''},
                       {'focus_uri': ' '}, {'focus_uri': 42}, {'focus_uri': 'a' * 2001}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.service.graph(**kwargs)
        self.assertEqual(self.store.calls, [])

    def test_foreign_and_absent_focus_have_the_same_empty_result(self):
        foreign = self.service.graph(focus_uri='urn:foreign:kg:person-private')
        self.assertEqual(self.store.calls, [])
        absent = self.service.graph(focus_uri='urn:graphfixture:kg:person-absent')
        self.assertEqual(foreign, absent)
        self.assertFalse(absent['focus_found'])
        self.assertEqual(absent['nodes'], [])


try:
    from fastapi.testclient import TestClient
    from kg.console_web import create_app
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, 'console extras not installed')
class GraphApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = GraphStore()
        self.app = create_app(KGConfig(home=Path(self.tmp.name), urn_prefix='urn:graphfixture:kg:'),
                              token='fixture-graph-token', memory=SimpleNamespace(store=self.store),
                              search=SimpleNamespace(), background=False)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.auth = {'Authorization': 'Bearer fixture-graph-token'}

    def test_only_authenticated_post_reads_graph_and_private_inputs_stay_in_the_body(self):
        route = '/api/v1/graph/query'
        self.assertEqual(self.client.post(route, json={}).status_code, 401)
        self.assertIn(self.client.get(route, headers=self.auth).status_code, (404, 405))
        response = self.client.post(route, headers=self.auth, json={'q': 'Fixture', 'types': ['concept']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['scope'], 'current_namespace_active')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(str(response.request.url), 'http://testserver/api/v1/graph/query')
        self.assertEqual(self.client.post(route, headers={**self.auth, 'Origin': 'https://foreign.invalid'}, json={}).status_code, 403)

    def test_malformed_and_namespace_override_inputs_are_safely_rejected(self):
        for body in ({'namespace': 'urn:private-secret:kg:'}, {'node_limit': True}, {'node_limit': '100'},
                     {'edge_limit': 501}, {'types': 'concept'}, {'types': [3]}, {'q': 'private-secret-' * 30},
                     {'focus_uri': ''}):
            with self.subTest(body=body):
                response = self.client.post('/api/v1/graph/query', headers=self.auth, json=body)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertNotIn('private-secret', response.text)
        response = self.client.post('/api/v1/graph/query', headers=self.auth, json={'types': ['Device']})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.store.calls, [])

    def test_database_failure_does_not_repeat_credentials_or_private_inputs(self):
        self.store.failure = ConnectionError('private-endpoint-password')
        response = self.client.post('/api/v1/graph/query', headers=self.auth, json={'q': 'private search'})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('password', response.text)
        self.assertNotIn('private search', response.text)


@unittest.skipUnless(os.environ.get('KG_TEST_BOLT_URL'), 'isolated Neo4j test URL not configured')
class GraphNeo4jTests(unittest.TestCase):
    def setUp(self):
        from kg.store import KGStore
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prefix = 'urn:graphtest' + uuid.uuid4().hex + ':kg:'
        self.foreign = 'urn:graphforeign' + uuid.uuid4().hex + ':kg:'
        self.config = KGConfig(home=Path(self.tmp.name), neo4j_url=os.environ['KG_TEST_BOLT_URL'],
            password=os.environ['KG_TEST_PASSWORD'], urn_prefix=self.prefix, default_shared=False)
        self.store = KGStore(self.config)
        self.addCleanup(self.store.close)
        self.addCleanup(self.clear)
        with self.store.driver.session(database=self.config.database) as session:
            session.run('UNWIND range(0,204) AS i CREATE (:Concept {uri:$prefix+"concept-"+toString(i),hermes_name:"Bulk "+toString(i)})',
                        {'prefix': self.prefix}).consume()
            session.run('''CREATE (anchor:Person {uri:$prefix+'person-anchor',foaf_name:'Anchor'}),
                (friend:Person {uri:$prefix+'person-friend',foaf_name:'Friend'}),
                (event:Event {uri:$prefix+'event-late',hermes_name:'Outside initial view',hermes_aliasNames:['Late needle']}),
                (held:Concept:KGQuarantined {uri:$prefix+'concept-held'}),
                (:Concept:KGLegacyAlias {uri:$prefix+'concept-alias'}),
                (:Concept:KGLegacyUnresolved {uri:$prefix+'concept-unresolved'}),
                (disabled:Concept {uri:$prefix+'concept-disabled',hermes_enabled:false}),
                (:Concept {uri:$prefix+'concept-status',hermes_status:'disabled'}),
                (:Concept {uri:$prefix+'concept-confirmed',hermes_aliasConfirmed:true}),
                (shared:Person {uri:$foreign+'person-shared',foaf_name:'Foreign shared',shared:true}),
                (private:Person {uri:$foreign+'person-private',foaf_name:'Foreign private',shared:false}),
                (anchor)-[:HERMES_KNOWS]->(friend),(anchor)-[:HERMES_participant]->(event),
                (anchor)-[:RELATED_TO]->(anchor),(anchor)-[:RELATED_TO]->(held),
                (anchor)-[:RELATED_TO]->(disabled),(anchor)-[:HERMES_KNOWS]->(shared),
                (private)-[:HERMES_KNOWS]->(anchor)''', {'prefix': self.prefix, 'foreign': self.foreign}).consume()
            session.run('''MATCH (anchor {uri:$prefix+'person-anchor'}), (a {uri:$prefix+'concept-0'}),
                (b {uri:$prefix+'concept-1'}), (c {uri:$prefix+'concept-2'})
                CREATE (anchor)-[:LINKED_TO]->(a),(b)-[:CAUSED_BY]->(anchor),(anchor)-[:UNKNOWN_LEGACY]->(c)
                WITH a,b UNWIND range(0,504) AS i CREATE (a)-[:RELATED_TO {sequence:i}]->(b)''', {'prefix': self.prefix}).consume()
        self.service = OntologyService(self.config, self.store)

    def clear(self):
        with self.store.driver.session(database=self.config.database) as session:
            session.run('MATCH (n) WHERE n.uri STARTS WITH $prefix OR n.uri STARTS WITH $foreign DETACH DELETE n',
                        {'prefix': self.prefix, 'foreign': self.foreign}).consume()

    def test_global_sampling_and_dense_edges_are_bounded_with_truthful_totals(self):
        result = self.service.graph()
        self.assertEqual(result['total_nodes'], 208)
        self.assertEqual(len(result['nodes']), 120)
        self.assertTrue(result['truncated_nodes'])
        self.assertEqual(result['total_edges'], 505)
        self.assertEqual(len(result['edges']), 240)
        self.assertTrue(result['truncated_edges'])
        self.assertEqual(result, self.service.graph())
        self.assertEqual(len({edge['id'] for edge in result['edges']}), 240)

    def test_search_finds_an_alias_match_outside_initial_sampling(self):
        with self.store.driver.session(database=self.config.database) as session:
            session.run('''MATCH (n {uri:$event}) SET n.hermes_description=$description,n.hermes_evidenceJson=$evidence,
                n.custom_payload=$evidence WITH n MATCH ()-[r:HERMES_participant]->(n)
                SET r.hermes_relLabel=$label,r.hermes_sourceSession=$source,r.hermes_sourceSessions=$sources,
                    r.hermes_origin='auto',r.hermes_evidenceJson=$evidence,r.custom_payload=$evidence''',
                {'event': self.prefix+'event-late', 'description': 'D' * 5000, 'evidence': 'large-private-evidence-' * 10000,
                 'label': 'L' * 500, 'source': 'S' * 500,
                 'sources': ['session-' + str(i) for i in range(30)]}).consume()
        initial = self.service.graph(node_limit=2)
        self.assertNotIn(self.prefix+'event-late', [item['uri'] for item in initial['nodes']])
        found = self.service.graph(q='LATE NEEDLE', node_limit=2)
        self.assertEqual(found['total_nodes'], 1)
        self.assertEqual(found['nodes'][0]['uri'], self.prefix+'event-late')
        self.assertEqual(len(found['nodes'][0]['description']), 1200)
        self.assertTrue(found['nodes'][0]['content_truncated'])
        self.assertNotIn('large-private-evidence', json.dumps(found))
        self.assertEqual(found['edges'], [])
        focused = self.service.graph(focus_uri=self.prefix+'person-anchor', types=['event'])
        relation = next(item for item in focused['edges'] if item['relation_type'] == 'HERMES_participant')
        self.assertEqual(relation['properties']['hermes_origin'], 'auto')
        self.assertEqual(len(relation['properties']['hermes_relLabel']), 240)
        self.assertEqual(len(relation['properties']['hermes_sourceSession']), 240)
        self.assertEqual(len(relation['properties']['hermes_sourceSessions']), 8)
        self.assertTrue(relation['properties_truncated'])
        self.assertNotIn('large-private-evidence', json.dumps(focused))
        self.assertEqual(self.service.graph(q="O'Brien'); MATCH (private) //")['nodes'], [])

    def test_focus_is_undirected_retains_centre_and_filters_neighbours(self):
        result = self.service.graph(focus_uri=self.prefix+'person-anchor', types=['event'], q='late needle')
        self.assertTrue(result['focus_found'])
        self.assertEqual(result['total_nodes'], 2)
        self.assertEqual(result['nodes'][0]['uri'], self.prefix+'person-anchor')
        self.assertEqual({item['type'] for item in result['nodes']}, {'person', 'event'})
        self.assertEqual(len(result['edges']), 2)  # Includes the centre's self relationship.
        focused = self.service.graph(focus_uri=self.prefix+'person-anchor')
        self.assertEqual(focused['total_nodes'], 6)
        incoming = next(edge for edge in focused['edges'] if edge['stored_type'] == 'CAUSED_BY')
        self.assertEqual(incoming['target']['uri'], self.prefix+'person-anchor')
        self.assertEqual(incoming['source']['uri'], self.prefix+'concept-1')
        self.assertIn('UNKNOWN_LEGACY', {edge['relation_type'] for edge in focused['edges']})
        one = self.service.graph(focus_uri=self.prefix+'person-anchor', node_limit=1)
        self.assertEqual(one['nodes'][0]['uri'], self.prefix+'person-anchor')
        self.assertTrue(one['truncated_nodes'])

    def test_foreign_or_inactive_focus_and_cross_space_edges_never_escape(self):
        for uri in (self.foreign+'person-shared', self.foreign+'person-private', self.prefix+'concept-held',
                    self.prefix+'concept-disabled', self.prefix+'concept-status', self.prefix+'concept-confirmed',
                    self.prefix+'concept-alias', self.prefix+'concept-unresolved', self.prefix+'person-missing'):
            with self.subTest(uri=uri):
                result = self.service.graph(focus_uri=uri)
                self.assertFalse(result['focus_found'])
                self.assertEqual(result['nodes'], [])
                self.assertEqual(result['edges'], [])
        result = self.service.graph(node_limit=200, edge_limit=500)
        self.assertEqual(len(result['nodes']), 200)
        self.assertLessEqual(len(result['edges']), 500)
        self.assertNotIn(self.foreign, json.dumps(result))


if __name__ == '__main__':
    unittest.main()
