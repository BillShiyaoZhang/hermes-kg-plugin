"""Opt-in URI lookup semantics and small PROFILE regression; no model calls.

Uses only newly created, UUID-marked fixtures on KG_SEARCH_TEST_URL. Run after
performance measurement windows; PROFILE here is not scale benchmark evidence.
"""
import os
import unittest
import uuid

from kg.schema import ENTITY_LABELS
from kg.search import _lookup_uri, _requested_scope, _visible


@unittest.skipUnless(os.environ.get('KG_SEARCH_TEST_URL'), 'Set KG_SEARCH_TEST_URL for disposable Neo4j')
class Neo4jUriLookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from neo4j import GraphDatabase
        cls.driver = GraphDatabase.driver(os.environ['KG_SEARCH_TEST_URL'],
                                         auth=('neo4j', os.environ['KG_SEARCH_TEST_PASSWORD']))
        cls.database = os.environ.get('KG_SEARCH_TEST_DATABASE', 'neo4j')

    @classmethod
    def tearDownClass(cls):
        cls.driver.close()

    def setUp(self):
        self.marker = 'uri-lookup-unit-' + uuid.uuid4().hex
        self.prefix = 'urn:' + self.marker + ':kg:'
        self.canonical = self.prefix + 'person-canonical'
        self.legacy = self.prefix + 'person-legacy'
        self.hidden = self.prefix + 'person-hidden'
        self.missing = self.prefix + 'person-missing'
        self.foreign_shared = 'urn:foreign-' + self.marker + ':kg:person-shared'
        self.foreign_private = 'urn:foreign-' + self.marker + ':kg:person-private'
        with self.driver.session(database=self.database) as session:
            session.run('''CREATE (:KGEntity:Person {uri:$canonical,lookup_test_id:$marker,lookup_role:'canonical'})
                CREATE (:Person {uri:$canonical,lookup_test_id:$marker,lookup_role:'shadow'})
                CREATE (:Person {uri:$legacy,lookup_test_id:$marker,lookup_role:'legacy'})
                CREATE (:KGEntity:Person:KGQuarantined {uri:$hidden,lookup_test_id:$marker,lookup_role:'hidden'})
                CREATE (:Person {uri:$hidden,lookup_test_id:$marker,lookup_role:'visible-shadow'})
                CREATE (:Person {uri:$shared,shared:true,lookup_test_id:$marker,lookup_role:'foreign-shared'})
                CREATE (:Person {uri:$private,shared:false,lookup_test_id:$marker,lookup_role:'foreign-private'})''',
                canonical=self.canonical, legacy=self.legacy, hidden=self.hidden,
                shared=self.foreign_shared, private=self.foreign_private, marker=self.marker).consume()

    def tearDown(self):
        with self.driver.session(database=self.database) as session:
            session.run('MATCH(n) WHERE n.lookup_test_id=$marker DETACH DELETE n', marker=self.marker).consume()
            remaining = session.run('MATCH(n) WHERE n.lookup_test_id=$marker RETURN count(n) AS remaining',
                                    marker=self.marker).single()['remaining']
            self.assertEqual(remaining, 0, 'Owned URI lookup fixture cleanup must leave zero nodes')

    def lookup(self, uris, *, allow_missing=False, visible=False, namespace=None, profile=False):
        query = 'UNWIND $rows AS entry ' + _lookup_uri('entry.uri', 'entry', allow_missing=allow_missing)
        if visible:
            query += f' WITH entry,n WHERE {_visible()} AND {_requested_scope()}'
        query += ' RETURN entry.uri AS requested,n.uri AS uri,n.lookup_role AS role ORDER BY requested'
        params = {'rows': [{'uri': uri} for uri in uris], 'prefix': self.prefix,
                  'entity_labels': list(ENTITY_LABELS.values()) + ['foaf:Person'],
                  'filter_namespace': namespace, 'filter_types': []}
        with self.driver.session(database=self.database, default_access_mode='READ') as session:
            result = session.run(('PROFILE ' if profile else '') + query, params)
            rows = result.data()
            summary = result.consume()
        return rows, summary.profile if profile else None

    def test_canonical_wins_over_duplicate_legacy_and_legacy_fallback_survives(self):
        rows, _ = self.lookup([self.canonical, self.legacy, self.missing])
        self.assertEqual({row['requested']: row['role'] for row in rows},
                         {self.canonical: 'canonical', self.legacy: 'legacy'})
        self.assertEqual(len(rows), 2)

    def test_missing_optional_lookup_retains_one_null_row_for_cleanup(self):
        rows, _ = self.lookup([self.canonical, self.legacy, self.missing], allow_missing=True)
        by_uri = {row['requested']: row for row in rows}
        self.assertEqual(len(rows), 3)
        self.assertEqual(by_uri[self.canonical]['role'], 'canonical')
        self.assertEqual(by_uri[self.legacy]['role'], 'legacy')
        self.assertEqual(by_uri[self.missing], {'requested': self.missing, 'uri': None, 'role': None})

    def test_hidden_primary_cannot_fall_through_to_visible_legacy_shadow(self):
        for allow_missing in (False, True):
            with self.subTest(allow_missing=allow_missing):
                rows, _ = self.lookup([self.hidden], allow_missing=allow_missing, visible=True)
                self.assertEqual(rows, [])

    def test_shared_legacy_keeps_visibility_but_requested_namespace_still_applies(self):
        rows, _ = self.lookup([self.foreign_shared, self.foreign_private], visible=True)
        self.assertEqual(rows, [{'requested': self.foreign_shared, 'uri': self.foreign_shared, 'role': 'foreign-shared'}])
        rows, _ = self.lookup([self.foreign_shared, self.foreign_private], visible=True, namespace=self.prefix)
        self.assertEqual(rows, [])

    def test_canonical_lookup_does_not_execute_fallback_all_node_scan(self):
        rows, profile = self.lookup([self.canonical], profile=True)
        self.assertEqual(rows, [{'requested': self.canonical, 'uri': self.canonical, 'role': 'canonical'}])
        def check(plan):
            if str(plan['operatorType']).split('@')[0] == 'AllNodesScan':
                self.assertEqual(plan['rows'], 0, 'Canonical results must not enter the legacy scan')
                self.assertEqual(plan['dbHits'], 0, 'Canonical results must not read legacy scan nodes')
            for child in plan.get('children', []):
                check(child)
        check(profile)


if __name__ == '__main__':
    unittest.main()
