"""Historical relationship spellings remain useful without rewriting audit data."""
from __future__ import annotations

import logging
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from kg.config import KGConfig
from kg.schema import relationship_pattern, relationship_types

logging.getLogger('neo4j.notifications').setLevel(logging.ERROR)


class RelationshipPatternTests(unittest.TestCase):
    def test_pattern_accepts_only_schema_relationship_names(self):
        names = relationship_types('PARTICIPATED_IN')
        self.assertIn('HERMES_participant', names)
        self.assertIn('HERMES_PARTICIPANT', names)
        self.assertIn('PARTICIPATED_IN', names)
        self.assertNotIn('HERMES_hasPreference', names)
        self.assertIn('`HERMES_PARTICIPANT`', relationship_pattern('HERMES_participant'))
        for name in ('UNKNOWN', 'RELATED_TO`]->() //'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                relationship_pattern(name)


class QueryIdentityTests(unittest.TestCase):
    def test_literal_person_names_do_not_select_an_installations_owner(self):
        from kg.queries import parse_natural_query
        config = KGConfig(urn_prefix='urn:customer:kg:', user_person_urn='urn:customer:kg:person-owner')
        for question in ('Morgan', 'Who is Morgan?', 'Who is MORGAN?'):
            with self.subTest(question=question):
                parsed = parse_natural_query(question, config=config)
                self.assertIsNone(parsed['person_urn'])
                self.assertEqual([word.lower() for word in parsed['keywords']], ['morgan'])
        for question in ('my preferences', 'I remember', 'tell me', 'user preference', '我偏好'):
            with self.subTest(question=question):
                parsed = parse_natural_query(question, config=config)
                self.assertEqual(parsed['person_urn'], config.user_person_urn)


@unittest.skipUnless(os.environ.get('KG_TEST_BOLT_URL'), 'isolated Neo4j test URL not configured')
class LegacyReaderTests(unittest.TestCase):
    def setUp(self):
        from kg.store import KGStore
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prefix = 'urn:readtest' + uuid.uuid4().hex + ':kg:'
        self.config = KGConfig(home=Path(self.tmp.name), neo4j_url=os.environ['KG_TEST_BOLT_URL'],
                               password=os.environ['KG_TEST_PASSWORD'], urn_prefix=self.prefix,
                               user_person_urn=self.prefix + 'person-owner')
        self.store = KGStore(self.config)
        self.addCleanup(self.store.close)
        self.addCleanup(self.clear)
        self.store.ensure_schema()
        with self.store.driver.session(database=self.config.database) as session:
            session.run("""CREATE (p:Person {uri:$prefix+'person-owner',foaf_name:'Anchorname'}),
                (e:Event {uri:$prefix+'event-first',hermes_name:'First record',hermes_eventTime:'2026-10-23T10:00:00+08:00'}),
                (e2:Event {uri:$prefix+'event-second',hermes_name:'Second record',hermes_eventTime:'2026-10-23T11:00:00+08:00'}),
                (f:Preference {uri:$prefix+'preference-fixture',hermes_name:'Fixture choice',hermes_preferenceKey:'theme',hermes_preferenceValue:'blue'}),
                (d:Decision {uri:$prefix+'decision-fixture',hermes_name:'Fixture decision',hermes_decisionText:'Use the fixture'}),
                (l:Location {uri:$prefix+'location-room',hermes_name:'Fixture room'}),
                (hidden:Person:KGQuarantined {uri:$prefix+'person-held',foaf_name:'Held person'}),
                (p)-[:HERMES_PARTICIPANT]->(e), (p)-[:PARTICIPATED_IN]->(e2),
                (p)-[:EXPRESSED_PREFERENCE]->(f), (p)-[:MADE_DECISION]->(d),
                (e)-[:AT_LOCATION]->(l), (hidden)-[:HERMES_PARTICIPANT]->(e)""",
                {'prefix': self.prefix}).consume()

    def clear(self):
        with self.store.driver.session(database=self.config.database) as session:
            session.run('MATCH (n) WHERE n.uri STARTS WITH $prefix OR n.namespace=$prefix DETACH DELETE n',
                        {'prefix': self.prefix}).consume()

    def test_owner_event_and_location_queries_read_historical_edges(self):
        from kg.queries import query_entities, query_events
        owner = self.config.user_person_urn
        owned = query_entities(owner_urn=owner, store=self.store)
        self.assertEqual({row['uri'] for row in owned}, {
            owner, self.prefix + 'event-first', self.prefix + 'event-second',
            self.prefix + 'preference-fixture', self.prefix + 'decision-fixture'})
        events = query_events(person_urn=owner, store=self.store)
        self.assertEqual({row['uri'] for row in events}, {self.prefix + 'event-first', self.prefix + 'event-second'})
        first = next(row for row in events if row['uri'].endswith('event-first'))
        self.assertEqual(first['participants'], ['Anchorname'])
        self.assertEqual(first['locations'], ['Fixture room'])

    def test_detail_keeps_literal_edge_type_but_explains_known_legacy_meaning(self):
        from kg.lifecycle import MemoryService
        service = MemoryService(self.config, self.store)
        relations = service.detail(self.config.user_person_urn)['relations']
        labels = {row['relation_type']: row['relation_label'] for row in relations}
        self.assertEqual(labels, {'HERMES_PARTICIPANT': '参与的事件', 'PARTICIPATED_IN': '参与的事件',
                                  'EXPRESSED_PREFERENCE': '偏好', 'MADE_DECISION': '决策'})
        participant = next(row for row in service.detail(self.prefix + 'event-first')['relations']
                           if row['relation_type'] == 'HERMES_PARTICIPANT')
        self.assertEqual(participant['relation_label'], '参与者')

    def test_graph_search_expands_historical_edges_without_exposing_held_entities(self):
        from kg.search import HybridSearch
        result = HybridSearch(self.store).search('Anchorname', limit=20)
        rows = {row['uri']: row for row in result['kg_results']}
        for suffix in ('event-first', 'event-second', 'preference-fixture', 'decision-fixture'):
            self.assertIn(self.prefix + suffix, rows)
            self.assertIn('graph', rows[self.prefix + suffix]['channels'])
        self.assertNotIn(self.prefix + 'person-held', rows)


if __name__ == '__main__':
    unittest.main()
