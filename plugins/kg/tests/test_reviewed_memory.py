"""Confirmed identity/semantics and explicitly unknown or interval event dates."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'plugins'), str(ROOT / 'hermes-agent'), str(ROOT / 'plugins/kg/tests'), str(ROOT / 'skills/kg-memory/scripts')]
from kg.config import KGConfig
from kg.grounding import validate_entity_evidence
from kg.provider import KGMemoryProvider
from kg.queries import query_entities, query_events, query_natural
from kg.schema import normalize_properties, prepare_entity, validate_event_timing
from kg.store import KGStore
from kg.health import check_health
from test_health import ReadOnlyStore
from test_store_contract import CaptureStore, FakeDriver
from validate import validate_graph


class ReviewedMemoryTests(unittest.TestCase):
    def unknown(self):
        return {'type': 'event', 'id': 'fixture', 'name': 'Occurred event, date unknown',
                'props': {'time_precision': 'unknown', 'event_status': 'occurred'}}

    def test_unknown_requires_explicit_precision_and_occurrence(self):
        prepared, _ = prepare_entity(self.unknown(), KGConfig())
        self.assertNotIn('hermes_eventTime', prepared['properties'])
        self.assertEqual(prepared['properties']['hermes_eventStatus'], 'occurred')
        for props in ({}, {'time_precision': 'unknown'}, {'event_status': 'occurred'},
                      {'time_precision': 'unknown', 'event_status': 'planned'},
                      {'time_precision': 'unknown', 'event_status': 'occurred', 'event_time': '2026-05-23'},
                      {'time_precision': 'unknown', 'event_status': 'occurred', 'end_time': '2026-05-25'}):
            with self.subTest(props=props), self.assertRaises(ValueError):
                prepare_entity({**self.unknown(), 'props': props}, KGConfig())

    def test_unknown_update_removes_stale_dates_atomically(self):
        driver = FakeDriver()
        uri = 'urn:hermes:kg:event-fixture'
        driver.state['nodes'][uri] = {'labels': ['KGEntity', 'Event'], 'properties': {
            'hermes_eventTime': '2026-05-23', 'hermes_eventEndTime': '2026-05-25', 'hermes_timePrecision': 'day'}}
        KGStore(KGConfig(), driver).write_batch([self.unknown()], [])
        props = driver.state['nodes'][uri]['properties']
        self.assertNotIn('hermes_eventTime', props)
        self.assertNotIn('hermes_eventEndTime', props)
        self.assertEqual(props['hermes_timePrecision'], 'unknown')
        KGStore(KGConfig(), driver).write_batch([{'type': 'event', 'id': 'fixture', 'name': 'Dated explicitly', 'props': {'event_time': '2026-05-24T12:00:00+08:00'}}], [])
        self.assertEqual(driver.state['nodes'][uri]['properties']['hermes_timePrecision'], 'datetime')

    def test_unknown_event_is_returned_without_filter_and_never_in_date_ranges(self):
        row = {'uri': 'urn:hermes:kg:event-fixture', 'time': '2026-05-23', 'end_time': '2026-05-25',
               'time_precision': 'unknown', 'event_status': 'occurred', 'semantic_scope': 'Confirmed event; date unremembered'}
        rows = query_events(store=CaptureStore([row]))
        self.assertIsNone(rows[0]['time'])
        self.assertIsNone(rows[0]['end_time'])
        self.assertEqual(rows[0]['event_status'], 'occurred')
        self.assertEqual(rows[0]['semantic_scope'], row['semantic_scope'])
        for kwargs in ({'from_date': '2026-05-01'}, {'to_date': '2026-05-31'},
                       {'from_date': '2026-05-23', 'to_date': '2026-05-24'}, {'exclusive_to': '2026-06-01'}):
            self.assertEqual(query_events(store=CaptureStore([row]), **kwargs), [])

    def test_weekend_interval_matches_second_day_and_excludes_end_boundary(self):
        row = {'uri': 'urn:hermes:kg:event-weekend', 'time': '2026-05-23T00:00:00+08:00',
               'end_time': '2026-05-25T00:00:00+08:00', 'time_precision': 'day', 'event_status': 'planned'}
        self.assertEqual(len(query_events('2026-05-24', '2026-05-24', store=CaptureStore([row]))), 1)
        self.assertEqual(query_events('2026-05-25', '2026-05-25', store=CaptureStore([row])), [])
        self.assertEqual(query_events('2026-05-22', exclusive_to='2026-05-23', store=CaptureStore([row])), [])
        self.assertEqual(query_events('2026-05-24T23:59:00+08:00', store=CaptureStore([row]))[0]['end_time'].day, 25)

    def test_day_precision_without_end_represents_whole_day(self):
        row = {'uri': 'urn:hermes:kg:event-day', 'time': '2026-05-24T00:00:00+08:00', 'time_precision': 'day'}
        self.assertEqual(len(query_events('2026-05-24T12:00:00+08:00', store=CaptureStore([row]))), 1)
        self.assertEqual(query_events('2026-05-25', store=CaptureStore([row])), [])

    def test_invalid_or_reversed_end_fails_schema(self):
        for end in ('2026-05-23', '2026-05-22', 'not-a-date'):
            with self.assertRaises(ValueError):
                prepare_entity({'type': 'event', 'id': 'fixture', 'name': 'Fixture',
                                'props': {'event_time': '2026-05-23', 'end_time': end, 'time_precision': 'day'}}, KGConfig())

    def test_changed_start_cannot_leave_a_stored_end_before_it(self):
        driver = FakeDriver()
        uri = 'urn:hermes:kg:event-fixture'
        driver.state['nodes'][uri] = {'labels': ['KGEntity', 'Event'], 'properties': {
            'hermes_eventTime': '2026-05-23', 'hermes_eventEndTime': '2026-05-25', 'hermes_timePrecision': 'day'}}
        before = copy.deepcopy(driver.state)
        with self.assertRaisesRegex(ValueError, 'end_time must be later'):
            KGStore(KGConfig(), driver).write_batch([{'type': 'event', 'id': 'fixture', 'name': 'Moved event',
                                                     'props': {'event_time': '2026-05-26'}}], [])
        self.assertEqual(driver.state, before)

    def test_graph_validator_accepts_explicit_unknown_but_rejects_missing_date(self):
        props = {'uri': 'urn:hermes:kg:event-fixture', 'shared': True, 'hermes_name': 'Fixture',
                 'hermes_timePrecision': 'unknown', 'hermes_eventStatus': 'occurred'}
        class GraphRows:
            config = KGConfig()
            def run_read(self, query, params=None):
                return [{'labels': ['KGEntity', 'Event'], 'properties': props}] if 'properties(n)' in query else []
        self.assertEqual(validate_graph(GraphRows()), [])
        props.pop('hermes_timePrecision')
        self.assertEqual(validate_graph(GraphRows()), ['invalid_event_time: 1'])

    def test_exact_alias_resolution_has_confirmation_and_both_visibility_guards(self):
        capture = CaptureStore()
        query_entities('urn:hermes:kg:person-old', store=capture)
        query, params = capture.calls[0]
        self.assertIn('MATCH (alias:KGQuarantined {uri:$uri})', query)
        self.assertIn('alias.hermes_aliasConfirmed=true', query)
        self.assertIn('alias.hermes_canonicalUri=n.uri', query)
        self.assertIn('alias.uri STARTS WITH $prefix OR alias.shared=true', query)
        self.assertIn('NOT n:KGQuarantined', query)
        self.assertIn('n.uri STARTS WITH $prefix OR n.shared=true', query)
        self.assertIn('AS matched_alias_uri', query)
        self.assertEqual(params['uri'], 'urn:hermes:kg:person-old')

    def test_alias_name_and_semantic_scope_are_available_to_provider(self):
        capture = CaptureStore([{'uri': 'urn:hermes:kg:preference-reading', 'alias_names': ['Old reading title'],
                                'semantic_scope': 'Reading interest, not completed reading', 'status': 'historical', 'status_note': 'Past choice only'}])
        capture.close = lambda: None
        provider = KGMemoryProvider(KGConfig(), store_factory=lambda config: capture)
        provider.initialize('fixture')
        self.addCleanup(provider.shutdown)
        answer = json.loads(provider.handle_tool_call('kg_search', {'question': 'Old reading title'}))
        fact = answer['result']['kg_results'][0]
        self.assertEqual(fact['status'], 'historical')
        self.assertEqual(fact['semantic_scope'], 'Reading interest, not completed reading')
        query = capture.calls[0][0]
        self.assertIn('coalesce(n.hermes_aliasNames,[])', query)
        self.assertIn('n.hermes_statusNote AS status_note', query)

    def test_owner_and_person_filters_resolve_only_confirmed_visible_person_aliases(self):
        capture = CaptureStore()
        old = 'urn:hermes:kg:person-old'
        query_entities(owner_urn=old, store=capture)
        query_events(person_urn=old, store=capture)
        for query, params in capture.calls:
            self.assertIn('owner_alias:KGQuarantined', query)
            self.assertIn('owner_alias.hermes_aliasConfirmed=true', query)
            self.assertIn('owner_alias.uri STARTS WITH $prefix OR owner_alias.shared=true', query)
            self.assertIn('canonical_owner:Person', query)
            self.assertIn('NOT canonical_owner:KGQuarantined', query)
            self.assertIn('canonical_owner.uri STARTS WITH $prefix OR canonical_owner.shared=true', query)
            self.assertIn('count(canonical_owner)=1', query)
            self.assertIn('uri:resolved_owner', query)
            self.assertIn('NOT owner:KGQuarantined', query)
            self.assertNotIn(old, query)
            self.assertIn(old, params.values())

    def test_status_distinguishes_confirmed_aliases_without_changing_total(self):
        capture = ReadOnlyStore(KGConfig())
        capture.counts.update(content_quarantined_nodes=630, confirmed_alias_nodes=13, ordinary_content_nodes=617)
        result = check_health(capture.config, capture)['content_quarantined']
        self.assertEqual(result['nodes'], 630)
        self.assertEqual(result['confirmed_alias_nodes'], 13)
        self.assertEqual(result['ordinary_content_nodes'], 617)
        self.assertIn('without restoring the old record', result['handling'])

    def test_confirmed_alias_writes_and_relationships_are_rejected_even_without_label(self):
        driver = FakeDriver()
        alias = 'urn:hermes:kg:person-old'
        event = 'urn:hermes:kg:event-fixture'
        driver.state['nodes'] = {
            alias: {'labels': ['KGEntity', 'Person'], 'properties': {'hermes_aliasConfirmed': True, 'hermes_canonicalUri': 'urn:hermes:kg:person-bill'}},
            event: {'labels': ['KGEntity', 'Event'], 'properties': {}},
        }
        before = copy.deepcopy(driver.state)
        store = KGStore(KGConfig(), driver)
        with self.assertRaisesRegex(ValueError, 'confirmed alias'):
            store.write_batch([{'type': 'person', 'id': 'old', 'name': 'Recreated alias'}], [])
        self.assertEqual(driver.state, before)
        with self.assertRaisesRegex(ValueError, 'Confirmed aliases'):
            store.write_batch([], [{'from': alias, 'to': event, 'type': 'HERMES_participant'}])
        self.assertEqual(driver.state, before)

    def test_regular_writes_cannot_create_confirmation_metadata(self):
        for props in ({'hermes_aliasConfirmed': True}, {'hermes_canonicalUri': 'urn:hermes:kg:person-bill'}, {'alias_names': ['Valid', 2]}):
            with self.assertRaises(ValueError):
                normalize_properties(props)

    def test_automatic_extractor_cannot_self_declare_unknown_to_bypass_date_grounding(self):
        entity = self.unknown()
        entity['evidence'] = {'message_id': 1, 'quote': 'We met, I do not remember when.'}
        with self.assertRaisesRegex(ValueError, 'Automatic extraction still requires quoted event dates'):
            validate_entity_evidence(entity, {'messages': [{'id': 1, 'role': 'user', 'content': entity['evidence']['quote']}]}, KGConfig())

    def test_exclusive_range_end_also_requires_date_evidence(self):
        quote = 'We met on 2026-05-23 and 2026-05-24.'
        snapshot = {'messages': [{'id': 1, 'role': 'user', 'content': quote}]}
        entity = {'type': 'event', 'id': 'fixture', 'name': 'Meeting', 'props': {
            'event_time': '2026-05-23', 'end_time': '2026-05-25', 'time_precision': 'day'},
            'evidence': {'message_id': 1, 'quote': quote}}
        validate_entity_evidence(entity, snapshot, KGConfig())
        entity['props']['end_time'] = '2026-05-26'
        with self.assertRaisesRegex(ValueError, 'Event date needs'):
            validate_entity_evidence(entity, snapshot, KGConfig())


if __name__ == '__main__':
    unittest.main(verbosity=2)
