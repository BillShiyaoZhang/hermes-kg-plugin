"""Opt-in authority integration against newly owned disposable graph fixtures."""
from dataclasses import replace
import logging
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kg.config import KGConfig
from kg.retrieval_authority import RequestContext, RetrievalAuthority
from kg.retrieval_documents import _schema_fingerprint, dataset_to_documents
from kg.store import KGStore


@unittest.skipUnless(os.environ.get('KG_RESEARCH_TEST_URL'), 'Set disposable KG_RESEARCH_TEST_URL')
class Neo4jRetrievalAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from neo4j import GraphDatabase
        logging.getLogger('neo4j.notifications').setLevel(logging.ERROR)
        cls.driver = GraphDatabase.driver(os.environ['KG_RESEARCH_TEST_URL'],
            auth=('neo4j', os.environ['KG_RESEARCH_TEST_PASSWORD']))

    @classmethod
    def tearDownClass(cls):
        cls.driver.close()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.marker = 'authority-' + uuid.uuid4().hex
        self.namespace = 'urn:' + self.marker + ':'
        self.foreign = 'urn:foreign-' + self.marker + ':person-one'
        self.a, self.b = self.namespace + 'person-a', self.namespace + 'person-b'
        self.schema = {'namespace': self.namespace, 'version': 1, 'entity_types': [
            {'key': 'person', 'label': 'Person', 'storage_label': 'Person'}],
            'properties': [], 'relations': [{'key': 'RELATED_TO', 'label': 'related'}]}
        self.context = RequestContext(self.namespace, 'profile', 1, _schema_fingerprint(self.schema),
            'synthetic-model', 'entity_relations', 'retrieval-cards-v1', 'generation-one')
        config = KGConfig(home=Path(self.directory.name), urn_prefix=self.namespace,
            neo4j_url=os.environ['KG_RESEARCH_TEST_URL'],
            password=os.environ['KG_RESEARCH_TEST_PASSWORD'], query_timeout=5)
        self.store = KGStore(config, driver=self.driver)
        with self.driver.session() as session:
            session.run('''CREATE (a:KGEntity:Person {uri:$a,authority_marker:$marker,foaf_name:'A',hermes_revision:1})
                CREATE (b:KGEntity:Person {uri:$b,authority_marker:$marker,foaf_name:'B',hermes_revision:1})
                CREATE (f:Person {uri:$foreign,authority_marker:$marker,foaf_name:'Shared',shared:true,hermes_revision:1})
                CREATE (a)-[:RELATED_TO {hermes_note:'first'}]->(b)
                CREATE (a)-[:RELATED_TO {hermes_note:'second'}]->(b)
                CREATE (b)-[:RELATED_TO {hermes_note:'reverse'}]->(a)
                CREATE (a)-[:RELATED_TO {hermes_note:'self'}]->(a)
                CREATE (a)-[:RELATED_TO {hermes_note:'shared'}]->(f)''',
                a=self.a, b=self.b, foreign=self.foreign, marker=self.marker).consume()
        self.authority = RetrievalAuthority(self.store, current_context=lambda request: self.context,
                                            schema_reader=lambda: self.schema)

    def tearDown(self):
        try:
            with self.driver.session() as session:
                session.run('MATCH(n) WHERE n.authority_marker=$marker DETACH DELETE n', marker=self.marker).consume()
                self.assertEqual(session.run('MATCH(n) WHERE n.authority_marker=$marker RETURN count(n) AS count',
                                            marker=self.marker).single()['count'], 0)
        finally:
            self.directory.cleanup()

    def cards(self):
        with self.driver.session() as session:
            nodes = session.run('MATCH(n) WHERE n.authority_marker=$marker '
                'RETURN properties(n) AS properties,labels(n) AS labels', marker=self.marker).data()
            relations = session.run('MATCH(a)-[r]->(b) WHERE a.authority_marker=$marker AND b.authority_marker=$marker '
                'RETURN a.uri AS from,b.uri AS to,type(r) AS type,properties(r) AS properties', marker=self.marker).data()
        return dataset_to_documents({'namespace': self.namespace, 'entities': nodes, 'relations': relations},
                                    'entity_relations', schema=self.schema, include_ontology=True)

    def mutate(self, query, **params):
        with self.driver.session() as session:
            session.run(query, {'a': self.a, 'b': self.b, 'foreign': self.foreign, **params}).consume()

    def test_actual_directed_parallel_reverse_self_loop_and_rule_cards(self):
        self.context = replace(self.context, scope='shared')
        old = self.cards()
        result = self.authority.validate(old, self.context)
        self.assertEqual(result.status, 'current')
        self.assertEqual(len([doc for doc in result.documents if doc.kind == 'relation']), 5)
        self.assertEqual({doc.id for doc in result.documents}, {doc.id for doc in old})

    def test_profile_does_not_return_foreign_shared_nodes_or_edges(self):
        result = self.authority.validate(self.cards(), self.context)
        self.assertEqual(result.status, 'incomplete')
        self.assertTrue(all(self.foreign not in doc.source_refs for doc in result.documents))
        self.assertEqual(len([doc for doc in result.documents if doc.kind == 'relation']), 4)

    def test_shared_revocation_is_observed_without_reindexing(self):
        self.context = replace(self.context, scope='shared')
        old = [doc for doc in self.cards() if self.foreign in doc.source_refs]
        self.assertEqual(len(self.authority.validate(old, self.context).documents), 2)
        self.mutate('MATCH(n {uri:$foreign}) SET n.shared=false')
        self.assertEqual(self.authority.validate(old, self.context).documents, ())

    def test_current_entity_body_and_endpoint_name_changes(self):
        old = [doc for doc in self.cards() if self.b in doc.source_refs]
        self.mutate("MATCH(n:KGEntity {uri:$b}) SET n.foaf_name='changed',n.hermes_note='new body'")
        self.assertEqual(self.authority.validate(old, self.context).documents, ())

    def test_archive_primary_does_not_fall_through_to_visible_legacy(self):
        old = [doc for doc in self.cards() if doc.kind == 'entity' and doc.source_refs == (self.a,)]
        self.mutate('MATCH(n:KGEntity {uri:$a}) SET n.hermes_archivedByWorkbench=true '
                    "CREATE(:Person {uri:$a,foaf_name:'legacy shadow',authority_marker:$marker})", marker=self.marker)
        self.assertEqual(self.authority.validate(old, self.context).documents, ())

    def test_actual_relation_removal_qualifier_change_and_direction(self):
        old = [doc for doc in self.cards() if doc.kind == 'relation' and self.foreign not in doc.source_refs]
        self.mutate("MATCH(a:KGEntity {uri:$a})-[r]->(b:KGEntity {uri:$b}) WHERE r.hermes_note='first' DELETE r")
        result = self.authority.validate(old, self.context)
        self.assertEqual(len(result.documents), 3)
        self.mutate("MATCH(a:KGEntity {uri:$a})-[r]->(b:KGEntity {uri:$b}) SET r.hermes_note='changed'")
        result = self.authority.validate(old, self.context)
        self.assertEqual(len(result.documents), 2)
        self.assertEqual({doc.metadata['self_loop'] for doc in result.documents}, {True, False})

    def test_disabled_relation_and_bounded_overflow_withhold(self):
        old = [doc for doc in self.cards() if doc.kind == 'relation' and self.foreign not in doc.source_refs]
        self.mutate('MATCH(a:KGEntity {uri:$a})-[r]->(b:KGEntity {uri:$b}) SET r.hermes_enabled=false')
        self.assertEqual(len(self.authority.validate(old, self.context).documents), 2)
        self.authority.max_relations = 1
        self.assertEqual(self.authority.validate(old, self.context).status, 'unavailable')


if __name__ == '__main__':
    unittest.main()
