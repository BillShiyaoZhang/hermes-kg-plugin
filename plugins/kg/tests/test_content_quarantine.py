"""Content quarantine boundaries: atomic write rejection and all read templates."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'plugins'), str(ROOT / 'hermes-agent'),
               str(ROOT / 'plugins/kg/tests'), str(ROOT / 'skills/kg-memory/scripts')]
from kg.config import KGConfig
from kg.health import check_health
from kg.provider import KGMemoryProvider
from kg.queries import query_entities, query_events, query_natural
from kg.store import KGStore
from test_health import ReadOnlyStore
from test_store_contract import CaptureStore, FakeDriver
from validate import validate_graph


class ContentQuarantineTests(unittest.TestCase):
    def setUp(self):
        self.driver = FakeDriver()
        self.store = KGStore(KGConfig(), self.driver)
        self.person = 'urn:hermes:kg:person-fixture'
        self.event = 'urn:hermes:kg:event-fixture'
        self.driver.state['nodes'] = {
            self.person: {'labels': ['KGEntity', 'Person'], 'properties': {'foaf_name': 'Fixture'}},
            self.event: {'labels': ['KGEntity', 'Event'], 'properties': {'hermes_name': 'Fixture', 'hermes_eventTime': '2026-09-13'}},
        }

    def quarantine(self, uri):
        self.driver.state['nodes'][uri]['labels'].append('KGQuarantined')
        self.driver.state['nodes'][uri]['properties']['hermes_quarantineReason'] = 'Fixture review'

    def test_entity_update_is_rejected_without_changing_data_or_receipts(self):
        self.quarantine(self.person)
        before = copy.deepcopy(self.driver.state)
        with self.assertRaisesRegex(ValueError, 'explicit review and restoration'):
            self.store.write_batch([{'type': 'person', 'id': 'fixture', 'name': 'Replacement'}], [])
        self.assertEqual(self.driver.state, before)

    def test_quarantine_takes_precedence_over_legacy_recovery(self):
        self.quarantine(self.person)
        self.driver.state['nodes'][self.person]['labels'].append('KGLegacyUnresolved')
        before = copy.deepcopy(self.driver.state)
        with self.assertRaisesRegex(ValueError, 'explicit review and restoration'):
            self.store.write_batch([{'type': 'person', 'id': 'fixture', 'name': 'Replacement'}], [])
        self.assertEqual(self.driver.state, before)

    def test_either_quarantined_endpoint_rolls_back_whole_batch(self):
        baseline = copy.deepcopy(self.driver.state)
        for uri in (self.person, self.event):
            with self.subTest(endpoint=uri):
                self.driver.state = copy.deepcopy(baseline)
                self.quarantine(uri)
                before = copy.deepcopy(self.driver.state)
                with self.assertRaisesRegex(ValueError, 'must not be quarantined'):
                    self.store.write_batch([{'type': 'concept', 'id': 'new', 'name': 'Transient'}],
                                           [{'from': self.person, 'to': self.event, 'type': 'HERMES_participant'}])
                self.assertEqual(self.driver.state, before)

    def test_implicit_preference_owner_cannot_be_quarantined(self):
        self.quarantine(self.person)
        before = copy.deepcopy(self.driver.state)
        with self.assertRaisesRegex(ValueError, 'must not be quarantined'):
            self.store.write_batch([{'type': 'preference', 'id': 'new', 'name': 'Fixture preference',
                                    'owner': self.person, 'props': {'pref_key': 'x', 'pref_value': 'y'}}], [])
        self.assertEqual(self.driver.state, before)

    def test_completed_receipt_replay_does_not_restore_quarantined_content(self):
        entities = [{'type': 'person', 'id': 'fixture', 'name': 'Fixture'}]
        checkpoint = {'snapshot_id': 'fixture-complete', 'extraction_hash': 'fixture-hash'}
        self.store.write_batch(entities, [], 'fixture-session', checkpoint)
        self.quarantine(self.person)
        before = copy.deepcopy(self.driver.state)
        receipt = self.store.write_batch(entities, [], 'fixture-session', checkpoint)
        self.assertTrue(receipt['idempotent'])
        self.assertEqual(self.driver.state, before)

    def assert_excluded(self, query, *aliases):
        for alias in aliases:
            self.assertIn(f'NOT {alias}:KGQuarantined', query)

    def test_direct_related_owner_and_date_queries_all_apply_quarantine(self):
        capture = CaptureStore()
        query_entities(self.person, owner_urn=self.person, store=capture)
        self.assert_excluded(capture.calls[-1][0], 'n', 'owner', 'p')
        query_events('2026-09-13', person_urn=self.person, store=capture)
        self.assert_excluded(capture.calls[-1][0], 'e', 'owner', 'p', 'loc')
        for text in (self.person, 'remember me', '我的偏好', '我最近做了什么'):
            with self.subTest(text=text):
                query_natural(text, store=capture)
                self.assertIn('KGQuarantined', capture.calls[-1][0])

    def test_provider_search_and_prefetch_use_filtered_queries(self):
        capture = CaptureStore()
        capture.close = lambda: None
        provider = KGMemoryProvider(KGConfig(), store_factory=lambda config: capture)
        provider.initialize('fixture-session')
        self.addCleanup(provider.shutdown)
        self.assertTrue(json.loads(provider.handle_tool_call('kg_search', {'question': self.person}))['ok'])
        self.assert_excluded(capture.calls[-1][0], 'n', 'p')
        provider.prefetch('remember me', session_id='fixture-session')
        self.assert_excluded(capture.calls[-1][0], 'n', 'owner', 'p')

    def test_status_separates_content_quarantine_and_legacy_exclusions(self):
        capture = ReadOnlyStore(KGConfig())
        capture.counts.update(namespace_nodes=7, quarantined_nodes=5, content_quarantined_nodes=3)
        result = check_health(capture.config, capture)
        self.assertEqual(result['searchable_entities'], 2)
        self.assertEqual(result['quarantined_nodes'], 5)
        self.assertEqual(result['content_quarantined']['nodes'], 3)
        self.assertEqual(result['legacy_exclusions']['alias_nodes'], 1)
        self.assertEqual(result['legacy_exclusions']['unresolved_nodes'], 1)
        query = capture.calls[1][0]
        self.assertIn('OR n:KGQuarantined THEN 1 ELSE 0 END) AS quarantined_nodes', query)
        self.assertEqual(query.count('NOT n:KGQuarantined'), 2)
        self.assertEqual(capture.calls[2][1]['prefix'], capture.config.urn_prefix)

    def test_graph_validator_excludes_retained_quarantine_nodes_and_edges(self):
        capture = CaptureStore()
        self.assertEqual(validate_graph(capture), [])
        self.assert_excluded(capture.calls[0][0], 'n')
        self.assert_excluded(capture.calls[1][0], 'a', 'b')


if __name__ == '__main__':
    unittest.main(verbosity=2)
