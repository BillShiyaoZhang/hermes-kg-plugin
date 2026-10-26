"""Read-only HTTP contract tests; fake retrieval is not semantic evidence."""
from dataclasses import FrozenInstanceError, dataclass, replace
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient
from kg.retrieval_authority import AuthorityResult, RequestContext
from kg.retrieval_documents import entity_card, ontology_cards
from kg.retrieval_shadow import MAX_CANDIDATES, create_shadow_app


TOKEN = 'synthetic-shadow-token-123456'
NS = 'urn:researchshadow:kg:'


def context(**changes):
    return replace(RequestContext(NS, 'profile', 2, 's' * 64, 'm' * 64,
                                  'entity', 'projection-v1', 'generation-a'), **changes)


def document(name='CURRENT-authoritative-text'):
    return entity_card({'uri': NS + 'concept-synthetic', 'type': 'concept', 'name': name}, namespace=NS)


class PoisonCandidate:
    def to_dict(self):
        raise AssertionError('Unvalidated cached text must never be serialized')


class Backend:
    def __init__(self):
        self.context = context()
        self.candidates = (PoisonCandidate(),)
        self.capture_calls, self.retrieve_calls, self.fence_calls = 0, 0, 0
        self.hook = None
        self.fail = None
        self.request = None

    def capture_context(self, *, scope):
        self.capture_calls += 1
        if self.fail == 'capture':
            raise RuntimeError('PRIVATE-capture')
        return replace(self.context, scope=scope)

    def retrieve(self, request, fixed):
        self.retrieve_calls += 1
        self.request = request
        if self.hook:
            self.hook(request, fixed)
        if self.fail == 'retrieve':
            raise RuntimeError('PRIVATE-retrieve')
        return self.candidates

    def current_context(self, fixed):
        self.fence_calls += 1
        if self.fail == 'fence':
            raise RuntimeError('PRIVATE-fence')
        return replace(self.context, scope=fixed.scope)

    def status(self):
        if self.fail == 'status':
            raise RuntimeError('PRIVATE-status')
        return {'ready': True, 'model_ready': True, 'model': 'synthetic-contract-only',
                'schema_version': 2, 'password': 'PRIVATE-password',
                'namespace': 'PRIVATE-namespace', 'token': 'PRIVATE-token',
                'source_text': 'PRIVATE-body', 'dimensions': True}


class Authority:
    def __init__(self):
        self.result = AuthorityResult((document(),), (), 'current')
        self.calls = 0
        self.hook = None
        self.fail = False

    def validate(self, candidates, fixed):
        self.calls += 1
        if self.hook:
            self.hook()
        if self.fail:
            raise RuntimeError('PRIVATE-authority')
        return self.result


class ShadowHttpTests(unittest.TestCase):
    def setUp(self):
        self.backend, self.authority = Backend(), Authority()
        self.client = TestClient(create_shadow_app(backend=self.backend, authority=self.authority, token=TOKEN))
        self.addCleanup(self.client.close)
        self.headers = {'Authorization': 'Bearer ' + TOKEN}

    def search(self, body=None):
        return self.client.post('/search', headers=self.headers, json=body or {'question': 'Synthetic question'})

    def test_every_route_requires_authentication_before_backend_work(self):
        for method, path in [('get', '/status'), ('post', '/search'), ('post', '/write'), ('get', '/docs')]:
            with self.subTest(path=path):
                response = getattr(self.client, method)(path)
                self.assertEqual(response.status_code, 401)
                self.assertNotIn('PRIVATE', response.text)
        self.assertEqual(self.backend.capture_calls, 0)
        self.assertEqual(self.backend.retrieve_calls, 0)

    def test_no_write_management_docs_or_workbench_routes_exist(self):
        for path in ('/write', '/sync', '/prepare', '/api/v1/search', '/api/v1/memories/correct', '/openapi.json', '/docs'):
            with self.subTest(path=path):
                response = self.client.post(path, headers=self.headers, json={})
                self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get('/search', headers=self.headers).status_code, 405)

    def test_authenticated_status_is_whitelisted_without_capture_or_download(self):
        response = self.client.get('/status', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['read_only'])
        self.assertFalse(response.json()['promotion_eligible'])
        self.assertNotIn('PRIVATE', response.text)
        self.assertNotIn('dimensions', response.json()['backend'])
        self.assertEqual(self.backend.capture_calls, 0)
        self.assertEqual(self.backend.retrieve_calls, 0)

    def test_status_failure_is_safe_unavailability(self):
        self.backend.fail = 'status'
        response = self.client.get('/status', headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('PRIVATE', response.text)

    def test_rebuilt_documents_only_and_single_capture(self):
        response = self.search()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'found')
        self.assertIn('CURRENT-authoritative-text', response.text)
        self.assertFalse(data['evidence'][0]['is_rule'])
        self.assertEqual(data['evidence'][0]['kind'], 'entity')
        self.assertEqual(data['kg_results'], [])
        self.assertEqual((self.backend.capture_calls, self.backend.retrieve_calls,
                          self.authority.calls, self.backend.fence_calls), (1, 1, 1, 1))
        self.assertTrue(data['boundaries']['read_only'])
        self.assertEqual(data['boundaries']['consistency'], 'read_committed')

    def test_request_is_frozen_and_question_normalized(self):
        def hook(request, fixed):
            with self.assertRaises(FrozenInstanceError):
                request.scope = 'shared'
        self.backend.hook = hook
        self.assertEqual(self.search({'question': '  Synthetic  ', 'limit': 1}).status_code, 200)
        self.assertEqual(self.backend.request.question, 'Synthetic')

    def test_scope_is_explicit_and_forwarded(self):
        response = self.search({'question': 'Synthetic', 'scope': 'shared'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.backend.request.scope, 'shared')
        self.assertEqual(response.json()['boundaries']['scope'], 'shared')

    def test_extra_interpret_plan_filters_and_download_are_rejected_without_echo(self):
        for field in ('interpret', 'query_plan', 'filters', 'allow_download', 'namespace'):
            with self.subTest(field=field):
                response = self.search({'question': 'PRIVATE-question', field: 'PRIVATE-value'})
                self.assertEqual(response.status_code, 422)
                self.assertNotIn('PRIVATE', response.text)
        self.assertEqual(self.backend.capture_calls, 0)

    def test_strict_limits_and_nonblank_question(self):
        for body in ({'question': ''}, {'question': ' '}, {'question': 'x' * 4001},
                     {'question': 10}, {'question': 'q', 'limit': True},
                     {'question': 'q', 'limit': 0}, {'question': 'q', 'limit': 51},
                     {'question': 'q', 'limit': '2'}, {'question': 'q', 'scope': 'other'}):
            with self.subTest(body_type=type(body.get('limit')).__name__):
                self.assertEqual(self.search(body).status_code, 422)
        self.assertEqual(self.backend.capture_calls, 0)

    def test_current_empty_is_distinct_from_rejected_empty(self):
        self.backend.candidates = ()
        self.authority.result = AuthorityResult((), (), 'current')
        response = self.search()
        self.assertEqual(response.json()['status'], 'empty')
        self.assertTrue(response.json()['boundaries']['complete'])
        self.backend.candidates = (PoisonCandidate(),)
        self.authority.result = AuthorityResult((), ({'id': 'opaque', 'reason': 'source_unavailable_or_changed'},), 'incomplete')
        response = self.search()
        self.assertEqual(response.json()['status'], 'incomplete')
        self.assertFalse(response.json()['boundaries']['complete'])

    def test_context_change_after_authority_withholds_all_evidence(self):
        changes = {'namespace': 'urn:researchshadow:changed:', 'scope': 'shared', 'schema_version': 3,
                   'schema_fingerprint': 'changed-schema', 'model_fingerprint': 'changed-model',
                   'representation': 'values', 'projection_revision': 'projection-v2',
                   'index_generation': 'generation-b'}
        for key, value in changes.items():
            with self.subTest(field=key):
                self.backend.context = context()
                self.authority.hook = lambda key=key, value=value: setattr(self.backend, 'context', replace(self.backend.context, **{key: value}))
                # current_context normally preserves caller scope; override it
                # here to exercise an incorrectly switched backend scope too.
                self.backend.current_context = lambda fixed: self.backend.context
                response = self.search()
                self.assertEqual(response.json()['status'], 'incomplete')
                self.assertEqual(response.json()['evidence'], [])
                self.assertNotIn('CURRENT-authoritative-text', response.text)

    def test_context_change_during_retrieval_withholds_results(self):
        self.backend.hook = lambda request, fixed: setattr(self.backend, 'context', replace(self.backend.context, index_generation='generation-b'))
        response = self.search()
        self.assertEqual(response.json()['status'], 'incomplete')
        self.assertEqual(response.json()['evidence'], [])

    def test_authority_unavailable_ignores_even_populated_documents(self):
        self.authority.result = AuthorityResult((document('PRIVATE-stale'),), ({'id': 'opaque', 'reason': 'authority_unavailable'},), 'unavailable')
        response = self.search()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['status'], 'unavailable')
        self.assertEqual(response.json()['evidence'], [])
        self.assertNotIn('PRIVATE', response.text)

    def test_failures_do_not_disclose_exception_or_candidate_text(self):
        for stage in ('capture', 'retrieve', 'fence', 'authority'):
            with self.subTest(stage=stage):
                self.backend.fail = stage
                self.authority.fail = stage == 'authority'
                response = self.search()
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()['evidence'], [])
                self.assertNotIn('PRIVATE', response.text)
                self.assertNotIn('CURRENT', response.text)

    def test_candidate_budget_is_bounded_before_authority(self):
        self.backend.candidates = tuple(PoisonCandidate() for _ in range(MAX_CANDIDATES + 1))
        self.assertEqual(self.search().status_code, 503)
        self.assertEqual(self.authority.calls, 0)

    def test_generator_or_string_candidate_result_is_not_consumed(self):
        for value in ('PRIVATE-candidates', iter([PoisonCandidate()])):
            with self.subTest(type=type(value).__name__):
                self.backend.candidates = value
                self.assertEqual(self.search().status_code, 503)
        self.assertEqual(self.authority.calls, 0)

    def test_authority_cannot_expand_into_unrequested_documents(self):
        self.authority.result = AuthorityResult((document(), document('Other')), (), 'current')
        self.assertEqual(self.search().status_code, 503)

    def test_limit_applies_after_authority_without_reusing_candidate_text(self):
        self.backend.candidates = (PoisonCandidate(), PoisonCandidate())
        self.authority.result = AuthorityResult((document(), document('Second')), (), 'current')
        response = self.search({'question': 'Synthetic', 'limit': 1})
        self.assertEqual(len(response.json()['evidence']), 1)

    def test_rules_remain_rules_and_not_fact_compatibility_results(self):
        rule = ontology_cards({'namespace': NS, 'version': 2,
                               'entity_types': [{'key': 'concept', 'label': 'Synthetic concept'}]}, namespace=NS)[0]
        self.authority.result = AuthorityResult((rule,), (), 'current')
        response = self.search()
        self.assertEqual(response.json()['evidence'][0]['kind'], 'rule')
        self.assertTrue(response.json()['evidence'][0]['is_rule'])
        self.assertEqual(response.json()['kg_results'], [])

    def test_rejection_ids_and_unsafe_reason_text_are_not_exposed(self):
        self.authority.result = AuthorityResult((), ({'id': 'PRIVATE-id', 'reason': 'PRIVATE exception text'},), 'incomplete')
        response = self.search()
        self.assertNotIn('PRIVATE', response.text)
        self.assertEqual(response.json()['rejected_reasons'], ['authority_rejected'])

    def test_no_response_can_be_cached(self):
        for response in (self.search(), self.client.get('/status'), self.search({'question': ' '})):
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')

    def test_explicit_token_required(self):
        for token in ('', 'short', 'has whitespace ' * 2, None):
            with self.subTest(token_type=type(token).__name__), self.assertRaises(ValueError):
                create_shadow_app(backend=self.backend, authority=self.authority, token=token)


if __name__ == '__main__':
    unittest.main()
