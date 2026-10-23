"""Whole-inbox pending review search and bounded authenticated pagination."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid

from kg.config import KGConfig
from kg.lifecycle import MemoryService


def review_record(prefix='urn:reviewfixture:kg:', index=0, *, status='pending'):
    return {'review_id': prefix+'review-'+str(index), 'namespace': prefix, 'status': status,
            'origin': 'auto', 'payload_json': json.dumps({'entities': [
                {'type': 'concept', 'id': 'late', 'name': 'Synthetic review candidate'}], 'relations': []}),
            'evidence_json': json.dumps({'entities': {prefix+'concept-late': [{'quote': 'Synthetic quotation'}]}}),
            'base_versions_json': json.dumps({prefix+'concept-late': 3}), 'payload_hash': 'synthetic-payload-hash',
            'created_at': 100, 'reason_codes': ['synthetic_reason']}


class ReviewStore:
    def __init__(self):
        self.calls = []
        self.row = review_record()
        self.failure = None

    def run_read(self, query, params):
        self.calls.append((query, params))
        if self.failure:
            raise self.failure
        if 'AS total_pending' in query:
            return [{'total_pending': 105, 'total': 1}]
        return [{'review': self.row}]


class ReviewQueryServiceTests(unittest.TestCase):
    def setUp(self):
        self.store = ReviewStore()
        self.service = MemoryService(KGConfig(urn_prefix='urn:reviewfixture:kg:'), self.store)

    def test_private_inputs_are_parameterized_and_totals_precede_bounded_pagination(self):
        text = "O'Brien'); MATCH (private) //"
        result = self.service.query_reviews(q=text, offset=4, limit=1)
        self.assertEqual(result['total_pending'], 105)
        self.assertEqual(result['total'], 1)
        self.assertFalse(result['has_more'])
        self.assertEqual(result['offset'], 4)
        for query, params in self.store.calls:
            self.assertNotIn(text, query)
            self.assertIn("b.status='pending'", query)
            self.assertIn('{namespace:$namespace}', query)
            self.assertEqual(params['namespace'], 'urn:reviewfixture:kg:')
            self.assertEqual(params['query'], text.lower())
        self.assertIn('count(b) AS total_pending', self.store.calls[0][0])
        page = self.store.calls[1][0]
        self.assertIn('b.payload_json,b.evidence_json', page)
        self.assertIn('b.reason_codes', page)
        self.assertIn('b.created_at DESC,b.review_id', page)
        self.assertIn('SKIP $offset LIMIT $limit', page)

    def test_invalid_shapes_and_bounds_do_not_read_database(self):
        for kwargs in ({'q': None}, {'q': 'x' * 201}, {'offset': True}, {'offset': -1}, {'offset': 1000001},
                       {'limit': False}, {'limit': 0}, {'limit': 51}, {'limit': 2.5}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.service.query_reviews(**kwargs)
        self.assertEqual(self.store.calls, [])


try:
    from fastapi.testclient import TestClient
    from kg.console_web import create_app
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, 'console extras not installed')
class ReviewQueryApiTests(unittest.TestCase):
    def setUp(self):
        from types import SimpleNamespace
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ReviewStore()
        config = KGConfig(home=Path(self.tmp.name), urn_prefix='urn:reviewfixture:kg:')
        app = create_app(config, token='synthetic-review-query-token', memory=MemoryService(config, self.store),
                         search=SimpleNamespace(), background=False)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.auth = {'Authorization': 'Bearer synthetic-review-query-token'}

    def test_authenticated_post_uses_the_existing_normalized_entity_and_evidence_contract(self):
        route = '/api/v1/reviews/query'
        self.assertEqual(self.client.post(route, json={}).status_code, 401)
        response = self.client.post(route, headers=self.auth, json={'q': 'private query', 'limit': 1})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        legacy = self.client.get('/api/v1/reviews', headers=self.auth).json()
        self.assertEqual(body['items'], legacy['items'])
        self.assertEqual(body['total'], 1)
        self.assertEqual(body['total_pending'], 105)
        self.assertEqual(body['items'][0]['entities'][0]['version'], 3)
        self.assertEqual(body['items'][0]['entities'][0]['evidence'][0]['quote'], 'Synthetic quotation')
        self.assertEqual(body['items'][0]['reason'], 'synthetic_reason')
        self.assertNotIn('?', str(response.request.url))
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(self.client.post(route, headers={**self.auth, 'Origin': 'https://foreign.invalid'}, json={}).status_code, 403)

    def test_strict_paging_shapes_and_scope_overrides_are_rejected_without_echoing(self):
        for body in ({'namespace': 'urn:private-secret:kg:'}, {'status': 'all'}, {'limit': True}, {'limit': '20'},
                     {'limit': 51}, {'offset': False}, {'offset': -1}, {'offset': 1000001}, {'q': 12}, {'q': 'private-secret' * 20}):
            with self.subTest(body=body):
                response = self.client.post('/api/v1/reviews/query', headers=self.auth, json=body)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertNotIn('private-secret', response.text)
        self.assertEqual(self.store.calls, [])

    def test_database_errors_do_not_expose_private_query_or_upstream_details(self):
        self.store.failure = ConnectionError('private-database-password')
        response = self.client.post('/api/v1/reviews/query', headers=self.auth, json={'q': 'private user question'})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private', response.text)
        self.assertNotIn('password', response.text)


@unittest.skipUnless(os.environ.get('KG_TEST_BOLT_URL'), 'isolated Neo4j test URL not configured')
class ReviewQueryNeo4jTests(unittest.TestCase):
    def setUp(self):
        from kg.store import KGStore
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prefix = 'urn:reviewquery' + uuid.uuid4().hex + ':kg:'
        self.foreign = 'urn:reviewforeign' + uuid.uuid4().hex + ':kg:'
        config = KGConfig(home=Path(self.tmp.name), neo4j_url=os.environ['KG_TEST_BOLT_URL'],
            password=os.environ.get('KG_TEST_PASSWORD', ''), urn_prefix=self.prefix, default_shared=False)
        self.store = KGStore(config)
        self.addCleanup(self.store.close)
        self.addCleanup(self.clear)
        records = []
        for index in range(105):
            record = review_record(self.prefix, index)
            record['order'] = 104 if index == 103 else index
            record['payload_json'] = json.dumps({'entities': [
                {'type': 'concept', 'id': 'candidate-'+str(index),
                 'name': 'oldest-payload-needle' if index == 0 else 'Synthetic candidate '+str(index)}], 'relations': []})
            record['evidence_json'] = json.dumps({'quote': 'oldest-evidence-needle' if index == 1 else 'Synthetic quotation'})
            record['reason_codes'] = ['oldest-reason-needle' if index == 2 else 'synthetic_reason']
            records.append(record)
        approved = review_record(self.prefix, 'approved', status='approved')
        approved['payload_json'] = records[0]['payload_json']
        approved['order'] = 300
        foreign = review_record(self.foreign, 'foreign')
        foreign['payload_json'] = records[0]['payload_json']
        foreign['order'] = 400
        with self.store.driver.session(database=config.database) as session:
            session.run('''UNWIND $rows AS row CREATE (b:KGReviewBatch)
                SET b += row,b.created_at=datetime('2026-10-23T00:00:00Z')+duration({seconds:row.order})
                REMOVE b.order''', {'rows': records + [approved, foreign]}).consume()
        self.service = MemoryService(config, self.store)

    def clear(self):
        with self.store.driver.session(database=self.store.config.database) as session:
            session.run('MATCH (b:KGReviewBatch) WHERE b.namespace=$prefix OR b.namespace=$foreign DETACH DELETE b',
                        {'prefix': self.prefix, 'foreign': self.foreign}).consume()

    def test_search_reaches_beyond_the_legacy_hundred_and_matches_all_search_fields(self):
        self.assertEqual(len(self.service.reviews()), 100)
        self.assertNotIn(self.prefix+'review-0', [item['id'] for item in self.service.reviews()])
        for query, index in (('OLDEST-PAYLOAD-NEEDLE', 0), ('oldest-evidence-needle', 1), ('oldest-reason-needle', 2),
                             (self.prefix+'review-0', 0)):
            with self.subTest(query=query):
                result = self.service.query_reviews(q=query)
                self.assertEqual(result['total_pending'], 105)
                self.assertEqual(result['total'], 1)
                self.assertEqual(result['items'][0]['id'], self.prefix+'review-'+str(index))
                self.assertFalse(result['has_more'])
                self.assertNotIn(self.foreign, json.dumps(result, default=str))
        empty = self.service.query_reviews(q="O'Brien'); MATCH (private) //")
        self.assertEqual(empty['total'], 0)
        self.assertEqual(empty['total_pending'], 105)
        self.assertEqual(empty['items'], [])

    def test_pagination_is_bounded_stable_and_reports_matching_vs_overall_totals(self):
        first = self.service.query_reviews(limit=20)
        self.assertEqual(first['total'], 105)
        self.assertEqual(first['total_pending'], 105)
        self.assertEqual(len(first['items']), 20)
        self.assertTrue(first['has_more'])
        self.assertEqual([item['id'] for item in first['items'][:2]], [self.prefix+'review-103', self.prefix+'review-104'])
        self.assertEqual(first, self.service.query_reviews(limit=20))
        second = self.service.query_reviews(offset=20, limit=20)
        self.assertFalse(set(item['id'] for item in first['items']) & set(item['id'] for item in second['items']))
        last = self.service.query_reviews(offset=100, limit=20)
        self.assertEqual(len(last['items']), 5)
        self.assertFalse(last['has_more'])
        filtered = self.service.query_reviews(q='oldest-', limit=1)
        self.assertEqual(filtered['total'], 3)
        self.assertEqual(filtered['total_pending'], 105)
        self.assertTrue(filtered['has_more'])
        self.assertEqual(len(filtered['items']), 1)
        self.assertEqual(self.service.query_reviews(offset=500)['items'], [])


if __name__ == '__main__':
    unittest.main()
