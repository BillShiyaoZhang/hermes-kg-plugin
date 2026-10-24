"""Ontology definitions, authenticated API validation and isolated graph privacy."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

from kg.config import KGConfig
from kg.ontology import OntologyService, _resource_text, parse_ontology
from kg.schema import ENTITY_LABELS, RELATION_ALIASES


def fixture_node(uri='urn:fixture:kg:person-one', labels=None, **props):
    return {'labels': labels or ['Person'], 'properties': {'uri': uri, 'foaf_name': 'Fixture person', **props}}


class CaptureStore:
    def __init__(self, nodes=None, relations=None, failure=None):
        self.nodes = nodes or []
        self.relations = relations or []
        self.failure = failure
        self.calls = []

    def run_read(self, query, params):
        self.calls.append((query, params))
        if self.failure:
            raise self.failure
        if ' AS total' in query:
            return [{'total': len(self.nodes if 'count(n)' in query else self.relations)}]
        if 'count(n) AS count' in query:
            return [{'labels': ['Person'], 'count': len(self.nodes)}] if self.nodes else []
        if 'count(r) AS count' in query:
            return [{'type': 'HERMES_PARTICIPANT', 'count': len(self.relations)}] if self.relations else []
        return self.nodes if 'RETURN labels(n)' in query else self.relations


class OntologyDefinitionTests(unittest.TestCase):
    def test_rdf_definitions_match_runtime_and_distinguish_any_from_restricted_endpoints(self):
        schema = parse_ontology(_resource_text())
        self.assertEqual({item['key'] for item in schema['entity_types']}, set(ENTITY_LABELS))
        self.assertEqual({item['key'] for item in schema['relations']}, set(RELATION_ALIASES.values()))
        relations = {item['key']: item for item in schema['relations']}
        location = relations['HERMES_locatedAt']
        self.assertEqual(location['declared_domain'], ['any'])
        self.assertEqual(location['domain'], ['event', 'person'])
        self.assertEqual(location['range'], ['location'])
        self.assertEqual(relations['RELATED_TO']['domain'], ['any'])
        self.assertEqual(relations['RELATED_TO']['range'], ['any'])
        self.assertIn('AT_LOCATION', location['aliases'])
        self.assertTrue(all(item['properties'] == [] for item in schema['relations']))
        properties = {item['key']: item for item in schema['properties']}
        self.assertEqual(properties['hermes_eventTime']['range'], ['dateTime'])
        self.assertEqual(properties['hermes_eventTime']['required_for'], ['event'])
        self.assertIn('unknown', properties['hermes_eventTime']['required_condition'])
        self.assertTrue(properties['hermes_createdAt']['managed'])
        self.assertIn('pref_key', properties['hermes_preferenceKey']['aliases'])
        self.assertEqual(properties['hermes_timePrecision']['allowed_values'], ['day', 'datetime', 'unknown'])
        self.assertEqual(properties['hermes_sourceSessions']['value_shape'], 'array')
        self.assertEqual(properties['foaf_name']['required_for'], ['person'])

    def test_rdf_union_and_unknown_runtime_definitions_are_reported_without_regex_assumptions(self):
        from rdflib import BNode, Graph, Literal, Namespace
        from rdflib.collection import Collection
        from rdflib.namespace import OWL, RDF, RDFS
        h = Namespace('urn:hermes:ontology:')
        graph = Graph().parse(data=_resource_text(), format='turtle')
        union, members = BNode(), BNode()
        graph.set((h.locatedAt, RDFS.domain, union))
        graph.add((union, OWL.unionOf, members))
        Collection(graph, members, [h.Person, h.Event])
        graph.add((h.Device, RDF.type, OWL.Class))
        graph.add((h.Device, h.neo4jLabel, Literal('Device')))
        schema = parse_ontology(graph.serialize(format='turtle'))
        relation = next(item for item in schema['relations'] if item['key'] == 'HERMES_locatedAt')
        self.assertEqual(relation['declared_domain'], ['event', 'person'])
        device = next(item for item in schema['entity_types'] if item['key'] == 'Device')
        self.assertFalse(device['runtime_supported'])
        self.assertIn({'code': 'unsupported_entity_type', 'key': 'Device'}, schema['schema_warnings'])

    def test_repo_and_packaged_resource_locations_work_independently(self):
        text = _resource_text()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packaged = root/'package'
            resource = packaged/'resources/skills/kg-memory/ontology/core.ttl'
            resource.parent.mkdir(parents=True)
            resource.write_text(text, encoding='utf-8')
            with patch('kg.ontology.resources.files', return_value=packaged), patch('kg.ontology.__file__', str(root/'unrelated/kg/ontology.py')):
                self.assertEqual(_resource_text(), text)
            sibling = root/'profile/skills/kg-memory/ontology/core.ttl'
            sibling.parent.mkdir(parents=True)
            sibling.write_text(text, encoding='utf-8')
            with patch('kg.ontology.resources.files', return_value=root/'empty'), patch('kg.ontology.__file__', str(root/'profile/plugins/kg/ontology.py')):
                self.assertEqual(_resource_text(), text)

    def test_isolated_plugin_package_reads_its_own_resource_without_top_level_kg(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'isolated_plugin'
            package.mkdir()
            source = Path(__file__).resolve().parents[1]
            for filename in ('__init__.py', 'ontology.py', 'schema.py'):
                shutil.copy2(source / filename, package / filename)
            resource = package / 'resources/skills/kg-memory/ontology/core.ttl'
            resource.parent.mkdir(parents=True)
            resource.write_text(_resource_text(), encoding='utf-8')
            program = '''
import importlib.abc, importlib.util, sys
from pathlib import Path
class BlockTopLevelKG(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'kg' or fullname.startswith('kg.'):
            raise ModuleNotFoundError('No top-level kg in isolated host', name='kg')
sys.meta_path.insert(0, BlockTopLevelKG())
spec = importlib.util.spec_from_file_location('_hermes_isolated_kg', Path(sys.argv[1]) / '__init__.py')
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from _hermes_isolated_kg.ontology import _resource_text, parse_ontology
assert len(parse_ontology(_resource_text())['entity_types']) == 6
assert 'kg' not in sys.modules
'''
            done = subprocess.run([sys.executable, '-I', '-B', '-c', program, str(package)],
                                  capture_output=True, text=True, timeout=30)
            self.assertEqual(done.returncode, 0, done.stderr)


class OntologyServiceTests(unittest.TestCase):
    def setUp(self):
        self.config = KGConfig(urn_prefix='urn:fixture:kg:')

    def test_schema_survives_database_failure_and_does_not_claim_zero_counts(self):
        service = OntologyService(self.config, CaptureStore(failure=ConnectionError('secret connection detail')))
        result = service.describe()
        self.assertEqual(len(result['entity_types']), 6)
        self.assertFalse(result['stats_available'])
        self.assertEqual(result['stats_error'], {'error_type': 'ConnectionError'})
        self.assertTrue(all(item['count'] is None for item in result['entity_types'] + result['relations']))
        self.assertNotIn('secret', json.dumps(result))
        empty = OntologyService(self.config, CaptureStore()).describe()
        self.assertTrue(empty['stats_available'])
        self.assertEqual(empty['stats']['entities'], 0)
        self.assertTrue(all(item['count'] == 0 for item in empty['entity_types'] + empty['relations']))
        # A previous request must never mutate the cached static definitions.
        self.assertIsNone(service.describe()['entity_types'][0]['count'])

    def test_queries_parameterize_search_and_recheck_scope_before_returning_records(self):
        owned = fixture_node()
        foreign = fixture_node('urn:other:kg:person-hidden', shared=True)
        store = CaptureStore(nodes=[owned, foreign])
        service = OntologyService(self.config, store)
        text = "O'Brien'); MATCH (private) //"
        result = service.entities(type='Person', q=text)
        self.assertEqual([item['uri'] for item in result['items']], [owned['properties']['uri']])
        for query, params in store.calls:
            self.assertNotIn(text, query)
            self.assertEqual(params['query'], text.lower())
            self.assertEqual(params['prefix'], self.config.urn_prefix)
            self.assertIn('NOT n:KGQuarantined', query)
            self.assertIn('NOT n:KGLegacyAlias', query)
            self.assertIn('NOT n:KGLegacyUnresolved', query)
        row = {'source_labels': owned['labels'], 'source_properties': owned['properties'],
               'target_labels': foreign['labels'], 'target_properties': foreign['properties'],
               'type': 'HERMES_KNOWS', 'properties': {}}
        self.assertEqual(OntologyService(self.config, CaptureStore(relations=[row])).relations()['items'], [])

    def test_invalid_filters_and_bounds_fail_before_any_database_read(self):
        store = CaptureStore()
        service = OntologyService(self.config, store)
        for kwargs in ({'limit': 0}, {'limit': 51}, {'limit': True}, {'offset': -1},
                       {'offset': 1000001}, {'offset': False}, {'q': 'x' * 201}, {'type': 'Person`) SET n.secret=1'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                service.entities(**kwargs)
        for relation in ('ARBITRARY', 'RELATED_TO`]->() //'):
            with self.subTest(relation=relation), self.assertRaises(ValueError):
                service.relations(relation=relation)
        self.assertEqual(store.calls, [])


try:
    from fastapi.testclient import TestClient
    from kg.console_web import create_app
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, 'console extras not installed')
class OntologyApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = CaptureStore(nodes=[fixture_node()])
        self.app = create_app(KGConfig(home=Path(self.tmp.name), urn_prefix='urn:fixture:kg:'),
                              token='fixture-ontology-token', memory=SimpleNamespace(store=self.store),
                              search=SimpleNamespace(), background=False)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.auth = {'Authorization': 'Bearer fixture-ontology-token'}

    def test_all_routes_require_auth_and_expose_only_read_methods(self):
        for route in ('/api/v1/ontology', '/api/v1/ontology/entities', '/api/v1/ontology/relations'):
            self.assertEqual(self.client.get(route).status_code, 401)
            self.assertEqual(self.client.post(route, headers=self.auth, json={}).status_code, 405)
        result = self.client.get('/api/v1/ontology', headers=self.auth)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['scope'], 'current_namespace_active')
        self.assertTrue(result.json()['read_only'])
        self.assertTrue(result.json()['stats_available'])

    def test_pagination_validation_and_schema_fallback_are_explicit(self):
        result = self.client.get('/api/v1/ontology/entities', headers=self.auth, params={'type': 'person', 'q': 'fixture'})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['total'], 1)
        self.assertEqual(result.json()['items'][0]['type'], 'person')
        for route, params in (('entities', {'limit': 51}), ('entities', {'offset': -1}),
                              ('entities', {'q': 'x' * 201}), ('entities', {'type': 'Secret'}),
                              ('relations', {'relation': 'UNKNOWN'}), ('relations', {'limit': 0})):
            response = self.client.get('/api/v1/ontology/' + route, headers=self.auth, params=params)
            self.assertEqual(response.status_code, 400, response.text)
        self.store.failure = ConnectionError('private endpoint and password must not appear')
        schema = self.client.get('/api/v1/ontology', headers=self.auth)
        self.assertEqual(schema.status_code, 200)
        self.assertFalse(schema.json()['stats_available'])
        self.assertNotIn('password', schema.text)
        self.assertTrue(all(item['count'] is None for item in schema.json()['entity_types']))
        unavailable = self.client.get('/api/v1/ontology/entities', headers=self.auth)
        self.assertEqual(unavailable.status_code, 503)
        self.assertNotIn('password', unavailable.text)


@unittest.skipUnless(os.environ.get('KG_TEST_BOLT_URL'), 'isolated Neo4j test URL not configured')
class OntologyNeo4jTests(unittest.TestCase):
    def setUp(self):
        from kg.store import KGStore
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prefix = 'urn:ontologytest' + uuid.uuid4().hex + ':kg:'
        self.foreign = 'urn:ontologyforeign' + uuid.uuid4().hex + ':kg:'
        self.config = KGConfig(home=Path(self.tmp.name), neo4j_url=os.environ['KG_TEST_BOLT_URL'],
            password=os.environ['KG_TEST_PASSWORD'], urn_prefix=self.prefix, default_shared=False)
        self.store = KGStore(self.config)
        self.addCleanup(self.store.close)
        self.addCleanup(self.clear)
        with self.store.driver.session(database=self.config.database) as session:
            session.run("""CREATE (p:Person {uri:$prefix+'person-owner',foaf_name:'Fixture person'}),
                (e:Event {uri:$prefix+'event-meeting',hermes_name:'Fixture meeting'}),
                (l:Location {uri:$prefix+'location-room',hermes_name:'Fixture room'}),
                (c:Concept {uri:$prefix+'concept-one',hermes_name:'Active one'}),
                (:Concept {uri:$prefix+'concept-two',hermes_name:'Active two'}),
                (held:Person:KGQuarantined {uri:$prefix+'person-held',foaf_name:'Held'}),
                (:Concept:KGLegacyAlias {uri:$prefix+'concept-alias'}),
                (:Concept:KGLegacyUnresolved {uri:$prefix+'concept-unresolved'}),
                (disabled:Concept {uri:$prefix+'concept-disabled',hermes_enabled:false}),
                (:Concept {uri:$prefix+'concept-status',hermes_status:'disabled'}),
                (:Concept {uri:$prefix+'concept-confirmed',hermes_aliasConfirmed:true}),
                (shared:Person {uri:$foreign+'person-shared',foaf_name:'Foreign shared',shared:true}),
                (private:Person {uri:$foreign+'person-private',foaf_name:'Foreign private',shared:false}),
                (p)-[:HERMES_PARTICIPANT {hermes_relLabel:'Historical participation'}]->(e),
                (p)-[:HERMES_participant]->(e), (e)-[:AT_LOCATION]->(l),
                (p)-[:KNOWS]->(held), (p)-[:KNOWS]->(shared), (shared)-[:KNOWS]->(p),
                (p)-[:LINKED_TO]->(private), (p)-[:RELATED_TO]->(disabled), (p)-[:UNKNOWN_LEGACY]->(c)""",
                {'prefix': self.prefix, 'foreign': self.foreign}).consume()
        self.service = OntologyService(self.config, self.store)

    def clear(self):
        with self.store.driver.session(database=self.config.database) as session:
            session.run('MATCH (n) WHERE n.uri STARTS WITH $prefix OR n.uri STARTS WITH $foreign OR n.namespace=$prefix DETACH DELETE n',
                        {'prefix': self.prefix, 'foreign': self.foreign}).consume()

    def test_counts_are_live_active_and_namespace_scoped_with_legacy_relation_aliases(self):
        result = self.service.describe()
        self.assertTrue(result['stats_available'], result['stats_error'])
        self.assertEqual(result['stats'], {'entities': 5, 'relationships': 4, 'unmapped_relationships': 1})
        self.assertEqual(next(item for item in result['entity_types'] if item['key'] == 'concept')['count'], 2)
        self.assertEqual(next(item for item in result['relations'] if item['key'] == 'HERMES_participant')['count'], 2)
        self.assertEqual(next(item for item in result['entity_types'] if item['key'] == 'preference')['count'], 0)

    def test_pagination_search_alias_filter_and_both_relationship_endpoints_remain_private(self):
        first = self.service.entities(type='Concept', q='active', limit=1)
        second = self.service.entities(type='Concept', q='active', offset=1, limit=1)
        self.assertEqual(first['total'], 2)
        self.assertTrue(first['has_more'])
        self.assertFalse(second['has_more'])
        self.assertNotEqual(first['items'][0]['uri'], second['items'][0]['uri'])
        relations = self.service.relations(relation='PARTICIPATED_IN', q='meeting', limit=1)
        self.assertEqual(relations['total'], 2)
        self.assertEqual(relations['items'][0]['relation_type'], 'HERMES_participant')
        self.assertEqual(self.service.relations(relation='KNOWS')['total'], 0)
        all_relations = self.service.relations()['items']
        self.assertEqual(len(all_relations), 4)
        self.assertTrue(all(row['source']['uri'].startswith(self.prefix) and row['target']['uri'].startswith(self.prefix) for row in all_relations))


if __name__ == '__main__':
    unittest.main()
