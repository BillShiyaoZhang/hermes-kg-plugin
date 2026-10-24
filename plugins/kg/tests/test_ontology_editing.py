"""Namespace ontology overlays, publication safety and runtime enforcement."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
import zipfile
from unittest.mock import patch

from kg.config import KGConfig
from kg.ontology import OntologyService, _schema
from kg.ontology_editing import (OntologyEditor, OntologyEditError, effective_schema, merge_patch,
                                 canonical_input, entity_violations)


class OntologyPatchTests(unittest.TestCase):
    def setUp(self):
        self.config = KGConfig(urn_prefix='urn:ontologyfixture:kg:')
        self.state = {'version': 0, 'patch': {}}

    def test_effective_schema_preserves_builtin_ids_and_reports_real_capabilities(self):
        state, changes = merge_patch(self.config, self.state, {'entity_types': {'concept': {'label': '自定义概念'}}})
        result = effective_schema(self.config, state)
        concept = next(item for item in result['entity_types'] if item['key'] == 'concept')
        self.assertEqual(concept['storage_label'], 'Concept')
        self.assertEqual(concept['label'], '自定义概念')
        self.assertEqual(concept['customized_fields'], ['label'])
        self.assertNotEqual(next(item for item in _schema()['entity_types'] if item['key'] == 'concept')['label'], '自定义概念')
        self.assertTrue(result['editing']['can_add_types'])
        self.assertEqual(result['editing']['structural_policy'], 'tighten_only')
        self.assertEqual(result['editing']['datatypes_by_property']['geo_lat'], ['double', 'integer'])
        self.assertEqual(result['editing']['datatypes_by_property']['hermes_createdAt'], [])
        self.assertEqual(result['editing']['builtin_constraints']['properties']['hermes_note']['domain'], ['any'])
        self.assertEqual(result['editing']['builtin_constraints']['properties']['hermes_note']['required_for'], [])
        self.assertEqual(changes[0]['fields'], ['label'])

    def test_immutable_ids_managed_fields_unknown_keys_and_weakened_rules_are_rejected(self):
        for value in ({'entity_types': {'Device': {'label': '设备'}}},
                      {'entity_types': {'concept': {'storage_label': 'Other'}}},
                      {'properties': {'hermes_createdAt': {'label': '修改系统字段'}}},
                      {'properties': {'hermes_preferenceValue': {'required_for': []}}},
                      {'properties': {'hermes_note': {'range': ['integer']}}},
                      {'relations': {'HERMES_knows': {'domain': ['any']}}},
                      {'properties': {'hermes_eventTime': {'domain': ['any']}}},
                      {'properties': {'hermes_note': {'required_for': [True]}}},
                      {'namespace': 'another'}):
            with self.subTest(value=value), self.assertRaises(OntologyEditError):
                merge_patch(self.config, self.state, value)

    def test_aliases_cannot_override_reserved_builtin_or_another_custom_binding(self):
        base = next(item for item in _schema()['properties'] if item['key'] == 'hermes_note')['aliases']
        for aliases in ([*base, 'uri'], [*base, 'hermes_revision'], [*base, 'description'], [*base, 'x`) SET n.x=1']):
            with self.subTest(aliases=aliases), self.assertRaises(OntologyEditError):
                merge_patch(self.config, self.state, {'properties': {'hermes_note': {'aliases': aliases}}})
        with self.assertRaises(OntologyEditError):
            merge_patch(self.config, self.state, {'properties': {'hermes_note': {'aliases': [*base, 'memo']},
                'hermes_description': {'aliases': ['description', 'memo']}}})

    def test_dynamic_aliases_are_canonicalized_without_silent_conflicting_values(self):
        state, _ = merge_patch(self.config, self.state, {'properties': {'hermes_note': {'aliases': ['note', 'memo']}}})
        self.assertEqual(canonical_input({'props': {'memo': 'value'}}, state)['props'], {'hermes_note': 'value'})
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            canonical_input({'props': {'memo': 'A', 'hermes_note': 'B'}}, state)
        with self.assertRaises(ValueError): canonical_input({'memo': 'unsupported top-level'}, state)
        for item in ({'props': {'memo': 'A'}, 'hermes_note': 'B'},
                     {'props': {'memo': 'A'}, 'note': 'B'},
                     {'properties': {'memo': 'A'}, 'props': {'hermes_note': 'B'}}):
            with self.subTest(item=item), self.assertRaisesRegex(ValueError, 'Conflicting'):
                canonical_input(item, state)
        self.assertEqual(canonical_input({'props': {'memo': 'A'}, 'hermes_note': 'A'}, state)['props'], {'hermes_note': 'A'})
        named, _ = merge_patch(self.config, self.state, {'properties': {'hermes_name': {'aliases': ['name', 'title']}}})
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            canonical_input({'type': 'concept', 'name': 'Top-level', 'props': {'title': 'Different'}}, named)
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            canonical_input({'type': 'concept', 'props': {'title': 'Alias name', 'name': 'Container name'}}, named)

    def test_unchanged_structural_arrays_do_not_turn_metadata_changes_into_rules(self):
        base = next(item for item in _schema()['properties'] if item['key'] == 'hermes_name')
        state, changes = merge_patch(self.config, self.state, {'properties': {'hermes_name': {
            'label': '自定义名称', 'domain': list(reversed(base['domain'])), 'range': base['range'],
            'required_for': list(reversed(base['required_for'])), 'aliases': list(reversed(base['aliases']))}}})
        self.assertEqual(changes[0]['fields'], ['label'])
        self.assertEqual(state['patch']['properties']['hermes_name'], {'label': '自定义名称'})
        narrowed, _ = merge_patch(self.config, self.state, {'properties': {'hermes_note': {'domain': ['concept']}}})
        restored, _ = merge_patch(self.config, narrowed, {'properties': {'hermes_note': {'domain': ['any']}}})
        self.assertEqual(effective_schema(self.config, restored)['editing']['builtin_constraints']['properties']['hermes_note']['domain'], ['any'])

    def test_duplicate_corrupt_receipts_fail_closed_before_publication(self):
        from types import SimpleNamespace
        from contextlib import nullcontext
        tx = SimpleNamespace(run=lambda *_: [{'request_hash': 'same', 'result_json': '{}'}] * 2)
        store = SimpleNamespace(driver=SimpleNamespace(session=lambda **_: nullcontext()),
                                _execute=lambda _session, _mode, action: action(tx))
        with self.assertRaisesRegex(RuntimeError, 'Duplicate ontology receipt'):
            OntologyEditor(self.config, store).publish(expected_namespace=self.config.urn_prefix,
                expected_version=0, patch={}, operation_id='synthetic-duplicate')

    def test_unknown_occurred_event_conditional_required_remains_valid(self):
        state = {'version': 1, 'patch': {'properties': {'hermes_eventTime': {'required_for': ['event']}}}}
        schema = effective_schema(self.config, state)
        self.assertEqual(entity_violations(schema, 'event', {'hermes_timePrecision': 'unknown', 'hermes_eventStatus': 'occurred'}), [])
        self.assertTrue(entity_violations(schema, 'event', {'hermes_timePrecision': 'unknown', 'hermes_eventStatus': 'planned'}))


@unittest.skipUnless(os.environ.get('KG_TEST_BOLT_URL'), 'isolated Neo4j URL not configured')
class OntologyEditingNeo4jTests(unittest.TestCase):
    def setUp(self):
        from kg.store import KGStore
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prefix = 'urn:ontologytest' + uuid.uuid4().hex + ':kg:'
        self.foreign = 'urn:ontologyforeign' + uuid.uuid4().hex + ':kg:'
        self.config = KGConfig(home=Path(self.tmp.name) / 'source', neo4j_url=os.environ['KG_TEST_BOLT_URL'],
                               password=os.environ['KG_TEST_PASSWORD'], urn_prefix=self.prefix, default_shared=False)
        self.store = KGStore(self.config)
        self.addCleanup(self.store.close)
        self.store.ensure_schema()
        self.addCleanup(self.clear)
        self.editor = OntologyEditor(self.config, self.store)
        self.a, self.p = self.prefix + 'concept-a', self.prefix + 'person-p'
        self.store.write_batch([{'type': 'concept', 'uri': self.a, 'name': 'Synthetic A', 'props': {'note': 'ready', 'description': 'Synthetic detail'}},
                                {'type': 'person', 'uri': self.p, 'name': 'Synthetic P'}],
                               [{'from': self.p, 'to': self.a, 'type': 'RELATED_TO'}])

    def clear(self):
        with self.store.driver.session(database=self.config.database) as session:
            session.run('''MATCH (n) WHERE n.uri STARTS WITH $prefix OR n.namespace=$prefix
                OR n.uri STARTS WITH $foreign OR n.namespace=$foreign DETACH DELETE n''',
                        {'prefix': self.prefix, 'foreign': self.foreign}).consume()

    def publish(self, patch=None, *, expected=None, operation_id=None, operations=None, confirm=False):
        version = self.store.ontology_overlay()['version'] if expected is None else expected
        approval = self.editor.validate(expected_namespace=self.prefix, expected_version=version, patch=patch, operations=operations)['risk'] if confirm else {}
        return self.editor.publish(expected_namespace=self.prefix,
            expected_version=version, patch=patch, operations=operations,
            operation_id=operation_id or 'ontology-' + uuid.uuid4().hex,
            confirmation=approval.get('confirmation_phrase'), confirmation_token=approval.get('confirmation_token'))

    def preview(self, patch=None, expected=None, operations=None):
        return self.editor.validate(expected_namespace=self.prefix,
            expected_version=self.store.ontology_overlay()['version'] if expected is None else expected, patch=patch, operations=operations)

    def sql(self, query, **params):
        with self.store.driver.session(database=self.config.database) as session:
            return [dict(row) for row in session.run(query, params)]

    def test_metadata_preview_is_nonmutating_and_counts_affected_entities(self):
        patch = {'entity_types': {'concept': {'label': '测试概念'}}}
        result = self.preview(patch)
        self.assertTrue(result['can_publish'])
        self.assertFalse(result['structural'])
        self.assertEqual({key: value for key, value in result['impact'].items() if key != 'definitions'}, {'affected_nodes': 1, 'affected_relations': 0, 'checked_nodes': 0, 'checked_relations': 0, 'violations': []})
        self.assertEqual(result['risk']['level'], 'low')
        self.assertEqual(self.store.ontology_overlay()['version'], 0)
        self.assertEqual(result['schema']['version'], 0)
        self.assertEqual(self.preview({'relations': {'RELATED_TO': {'label': '自定义关联'}}})['impact']['affected_relations'], 1)

    def test_published_overlay_persists_across_service_and_profile_home_with_same_namespace(self):
        result = self.publish({'entity_types': {'concept': {'label': '自定义概念'}}})
        self.assertEqual(result['version'], 1)
        self.assertEqual(result['schema']['version'], 1)
        from kg.store import KGStore
        other = KGStore(replace(self.config, home=Path(self.tmp.name) / 'other-home'), driver=self.store.driver)
        schema = OntologyService(other.config, other).describe()
        self.assertEqual(schema['version'], 1)
        self.assertFalse(schema['read_only'])
        concept = next(item for item in schema['entity_types'] if item['key'] == 'concept')
        self.assertEqual(concept['label'], '自定义概念')
        self.assertEqual(concept['customized_fields'], ['label'])

    def test_namespace_precondition_and_other_namespaces_remain_isolated(self):
        with self.assertRaises(OntologyEditError) as conflict:
            self.editor.validate(expected_namespace=self.foreign, expected_version=0, patch={'entity_types': {'concept': {'label': 'X'}}})
        self.assertEqual(conflict.exception.code, 'ontology_namespace_conflict')
        self.publish({'entity_types': {'concept': {'label': 'local'}}})
        from kg.store import KGStore
        foreign_config = KGConfig(home=Path(self.tmp.name) / 'foreign', neo4j_url=self.config.neo4j_url,
                                   password=self.config.password, urn_prefix=self.foreign)
        other = KGStore(foreign_config, driver=self.store.driver)
        self.assertEqual(other.ontology_overlay()['version'], 0)

    def test_idempotency_conflict_and_historical_receipt_keep_matching_schema_version(self):
        patch = {'entity_types': {'concept': {'label': 'version one'}}}
        self.publish(patch, expected=0, operation_id='synthetic-operation-one')
        self.publish({'entity_types': {'concept': {'label': 'version two'}}})
        replay = self.publish(patch, expected=0, operation_id='synthetic-operation-one')
        self.assertTrue(replay['idempotent'])
        self.assertEqual(replay['version'], replay['schema']['version'])
        self.assertEqual(replay['version'], 1)
        with self.assertRaises(OntologyEditError) as conflict:
            self.publish({'entity_types': {'concept': {'label': 'different'}}}, expected=0, operation_id='synthetic-operation-one')
        self.assertEqual(conflict.exception.code, 'ontology_operation_conflict')

    def test_concurrent_publish_accepts_exactly_one_expected_version(self):
        def publish(label):
            try:
                return self.publish({'entity_types': {'concept': {'label': label}}}, expected=0)['version']
            except OntologyEditError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(publish, ['first', 'second']))
        self.assertCountEqual(results, [1, 'ontology_version_conflict'])
        self.assertEqual(self.store.ontology_overlay()['version'], 1)

    def test_required_property_full_scan_conflicts_and_whitespace_is_missing(self):
        self.sql('MATCH (n {uri:$uri}) SET n.hermes_note=$value', uri=self.a, value='  ')
        patch = {'properties': {'hermes_note': {'required_for': ['concept']}}}
        preview = self.preview(patch)
        self.assertFalse(preview['can_publish'])
        self.assertEqual(preview['impact']['checked_nodes'], 2)
        self.assertEqual(preview['impact']['violations'], [{'kind': 'properties', 'key': 'hermes_note', 'code': 'required', 'count': 1}])
        with self.assertRaises(OntologyEditError) as error: self.publish(patch)
        self.assertEqual(error.exception.code, 'ontology_data_conflict')
        self.assertEqual(self.store.ontology_overlay()['version'], 0)

    def test_property_domain_conflict_is_not_a_display_only_change(self):
        result = self.preview({'properties': {'hermes_note': {'domain': ['event']}}})
        self.assertFalse(result['can_publish'])
        self.assertEqual(result['impact']['violations'][0]['code'], 'domain')

    def test_required_constraint_validates_effective_merged_writes(self):
        self.publish({'properties': {'hermes_note': {'required_for': ['concept']}}})
        self.store.write_batch([{'type': 'concept', 'uri': self.a, 'name': 'Updated without resupplying retained note'}], [])
        with self.assertRaisesRegex(ValueError, 'ontology'):
            self.store.write_batch([{'type': 'concept', 'id': 'new-missing-note', 'name': 'Rejected'}], [])
        self.assertEqual(self.sql('MATCH (n {uri:$uri}) RETURN n.hermes_note AS note', uri=self.a)[0]['note'], 'ready')

    def test_integer_coordinate_numeric_semantics_and_future_storage_normalization(self):
        uri = self.prefix + 'location-a'
        self.store.write_batch([{'type': 'location', 'uri': uri, 'name': 'Synthetic location', 'props': {'latitude': 5.0}}], [])
        self.publish({'properties': {'geo_lat': {'range': ['integer']}}})
        self.store.write_batch([{'type': 'location', 'uri': uri, 'name': 'Synthetic location', 'props': {'latitude': 6.0}}], [])
        self.assertIsInstance(self.sql('MATCH (n {uri:$uri}) RETURN n.geo_lat AS value', uri=uri)[0]['value'], int)
        with self.assertRaises(ValueError):
            self.store.write_batch([{'type': 'location', 'uri': uri, 'name': 'Synthetic location', 'props': {'latitude': 6.5}}], [])

    def test_existing_nonintegral_coordinate_blocks_integer_publication(self):
        self.store.write_batch([{'type': 'location', 'id': 'fraction', 'name': 'Fraction', 'props': {'latitude': 5.5}}], [])
        result = self.preview({'properties': {'geo_lat': {'range': ['integer']}}})
        self.assertFalse(result['can_publish'])
        self.assertEqual(result['impact']['violations'][0]['code'], 'datatype')

    def test_relation_endpoint_conflicts_and_future_write_enforcement(self):
        rejected = self.preview({'relations': {'RELATED_TO': {'domain': ['concept'], 'range': ['person']}}})
        self.assertFalse(rejected['can_publish'])
        self.publish({'relations': {'RELATED_TO': {'domain': ['person'], 'range': ['concept']}}})
        with self.assertRaisesRegex(ValueError, 'ontology'):
            self.store.write_batch([], [{'from': self.a, 'to': self.p, 'type': 'RELATED_TO'}])

    def test_aliases_apply_to_writes_corrections_and_canonical_relation_storage(self):
        from kg.lifecycle import MemoryService
        aliases = next(item for item in _schema()['relations'] if item['key'] == 'RELATED_TO')['aliases']
        self.publish({'properties': {'hermes_note': {'aliases': ['note', 'memo']}},
                      'relations': {'RELATED_TO': {'aliases': [*aliases, 'CONNECTED_WITH']}}})
        self.store.write_batch([{'type': 'concept', 'id': 'alias', 'name': 'Alias', 'props': {'memo': 'custom alias'}}],
                               [{'from': self.p, 'to': self.prefix + 'concept-alias', 'type': 'CONNECTED_WITH'}])
        service = MemoryService(self.config, self.store)
        service.correct(self.a, {'props': {'memo': 'corrected'}}, expected_version=1, operation_id='synthetic-alias-correction')
        self.assertEqual(service.detail(self.a)['props']['hermes_note'], 'corrected')
        self.assertEqual(self.sql('MATCH (a {uri:$uri})-[r]->(b) RETURN type(r) AS type', uri=self.p)[-1]['type'], 'RELATED_TO')

    def test_custom_name_alias_is_honored_by_corrections_and_conflicts_are_atomic(self):
        from kg.lifecycle import MemoryService
        self.publish({'properties': {'hermes_name': {'aliases': ['name', 'title']}, 'hermes_note': {'aliases': ['note', 'memo']}}})
        service = MemoryService(self.config, self.store)
        service.correct(self.a, {'props': {'title': 'Renamed through alias'}}, expected_version=1, operation_id='synthetic-name-alias')
        self.assertEqual(service.detail(self.a)['name'], 'Renamed through alias')
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            self.store.write_batch([{'type': 'concept', 'uri': self.a, 'name': 'Unchanged',
                'props': {'memo': 'Alias value'}, 'hermes_note': 'Top-level value'}], [])
        self.assertEqual(service.detail(self.a)['props']['hermes_note'], 'ready')

    def test_database_constraints_reject_duplicate_ontology_metadata_identities(self):
        from neo4j.exceptions import ConstraintError
        self.publish({'entity_types': {'concept': {'label': 'Unique metadata'}}}, operation_id='synthetic-unique')
        self.store.ensure_schema()
        for query in ('CREATE (:KGOntologySchema {namespace:$namespace,version:1})',
                      'CREATE (:KGOntologyRevision {namespace:$namespace,version:1})',
                      'CREATE (:KGOntologyReceipt {namespace:$namespace,operation_id:"synthetic-unique"})'):
            with self.subTest(query=query), self.assertRaises(ConstraintError):
                self.sql(query, namespace=self.prefix)
        self.assertEqual(self.store.ontology_overlay()['version'], 1)

    def test_failed_impact_count_cannot_publish_or_fabricate_zero(self):
        with patch.object(self.editor, '_affected', side_effect=RuntimeError('Synthetic unavailable count')):
            with self.assertRaises(OntologyEditError) as failure:
                self.publish({'entity_types': {'concept': {'label': 'Uncounted'}}})
            self.assertEqual(failure.exception.status, 503)
        self.assertEqual(self.store.ontology_overlay()['version'], 0)

    def test_alias_preparation_version_race_is_rejected_inside_the_write_lock(self):
        original = self.store._execute
        tripped = []
        def execute(session, mode, operation, *args, **kwargs):
            if mode == 'write' and getattr(operation, '__name__', '') == '_write_transaction' and not tripped:
                tripped.append(True)
                self.publish({'entity_types': {'concept': {'label': 'New schema during prepare'}}})
            return original(session, mode, operation, *args, **kwargs)
        with patch.object(self.store, '_execute', side_effect=execute), self.assertRaisesRegex(ValueError, 'version conflict'):
            self.store.write_batch([{'type': 'concept', 'id': 'race', 'name': 'Must not be written'}], [])
        self.assertEqual(self.sql('MATCH (n {uri:$uri}) RETURN count(n) AS count', uri=self.prefix + 'concept-race')[0]['count'], 0)

    def test_correction_cannot_prepare_against_a_changed_alias_baseline(self):
        from kg.lifecycle import MemoryService
        self.publish({'properties': {'hermes_note': {'aliases': ['note', 'memo']}}})
        original = self.store.write_batch
        def changed_before_write(*args, **kwargs):
            self.publish({'properties': {'hermes_note': {'aliases': ['note']},
                                         'hermes_description': {'aliases': ['description', 'memo']}}})
            return original(*args, **kwargs)
        with patch.object(self.store, 'write_batch', side_effect=changed_before_write):
            with self.assertRaisesRegex(ValueError, 'version conflict'):
                MemoryService(self.config, self.store).correct(self.a, {'props': {'memo': 'Old meaning'}},
                    expected_version=1, operation_id='synthetic-alias-baseline-race')
        detail = MemoryService(self.config, self.store).detail(self.a)
        self.assertEqual(detail['props']['hermes_note'], 'ready')
        self.assertEqual(detail['props']['hermes_description'], 'Synthetic detail')

    def test_restore_cannot_reactivate_an_entity_violating_published_constraints(self):
        from kg.lifecycle import MemoryService
        service = MemoryService(self.config, self.store)
        service.archive(self.a, expected_version=1, operation_id='synthetic-archive')
        self.publish({'properties': {'hermes_note': {'required_for': ['concept']}}})
        self.sql('MATCH (n {uri:$uri}) REMOVE n.hermes_note', uri=self.a)
        with self.assertRaisesRegex(ValueError, 'ontology'):
            service.restore(self.a, expected_version=2, operation_id='synthetic-restore')
        self.assertTrue(service.detail(self.a)['archived'])

    def test_staged_approval_uses_new_rules_but_rejection_is_not_blocked(self):
        from kg.lifecycle import MemoryService
        self.sql('MATCH (n {uri:$uri}) SET n.hermes_userConfirmed=true', uri=self.a)
        candidate = self.store.write_batch([{'type': 'concept', 'uri': self.a, 'name': 'Changed proposal', 'props': {'note': ''}}], [], origin='auto')
        review_id = candidate['review_batch_id']
        self.publish({'properties': {'hermes_note': {'required_for': ['concept']}}})
        service = MemoryService(self.config, self.store, review_validator=lambda payload: None)
        with self.assertRaisesRegex(ValueError, 'ontology'):
            service.resolve_review(review_id, 'approve', operation_id='synthetic-invalid-approve')
        self.assertEqual(service.resolve_review(review_id, 'reject', operation_id='synthetic-valid-reject')['disposition'], 'rejected')

    def test_pending_review_keeps_original_alias_meaning_after_removal_and_rebinding(self):
        from kg.lifecycle import MemoryService
        self.publish({'properties': {'hermes_note': {'aliases': ['note', 'memo']}}})
        service = MemoryService(self.config, self.store, review_validator=lambda payload: None)
        batches = {}
        for scenario in ('removed', 'rebound'):
            uri = self.prefix + 'concept-alias-' + scenario
            self.store.write_batch([{'type': 'concept', 'uri': uri, 'name': scenario,
                'props': {'note': 'Original note', 'description': 'Original description'}}], [])
            self.sql('MATCH (n {uri:$uri}) SET n.hermes_userConfirmed=true', uri=uri)
            payload = {'entities': [{'type': 'concept', 'uri': uri, 'name': scenario,
                                    'props': {'memo': 'Historical alias proposal'}}], 'relations': []}
            receipt = self.store.write_batch(payload['entities'], [], origin='auto', review_payload=payload)
            self.assertEqual(receipt['disposition'], 'deferred')
            batches[scenario] = (uri, receipt['review_batch_id'], payload)
        self.publish({'properties': {'hermes_note': {'aliases': ['note']}}})
        for scenario in ('removed', 'rebound'):
            if scenario == 'rebound':
                self.publish({'properties': {'hermes_description': {'aliases': ['description', 'memo']}}})
            uri, review_id, original = batches[scenario]
            review = service.review(review_id)
            self.assertEqual(review['ontology_aliases']['properties']['memo'], 'hermes_note')
            self.assertEqual(review['payload'], original)
            service.resolve_review(review_id, 'approve', operation_id='synthetic-frozen-' + scenario)
            props = service.detail(uri)['props']
            self.assertEqual(props['hermes_note'], 'Historical alias proposal')
            self.assertEqual(props['hermes_description'], 'Original description')
            self.assertEqual(service.review(review_id)['payload'], original)

    def test_legacy_review_cannot_gain_new_alias_semantics_retroactively(self):
        from kg.lifecycle import MemoryService, VersionConflict
        self.sql('MATCH (n {uri:$uri}) SET n.hermes_userConfirmed=true', uri=self.a)
        receipt = self.store.write_batch([{'type': 'concept', 'uri': self.a, 'name': 'Legacy changed name',
                                          'props': {'memo': 'Literal legacy property'}}], [], origin='auto')
        review_id = receipt['review_batch_id']
        self.sql('MATCH (b:KGReviewBatch {review_id:$id}) REMOVE b.ontology_aliases_json', id=review_id)
        # Simulate a legacy published/restored overlay. New publication now
        # rejects this alias because the pending batch owns the literal field.
        self.sql('''MERGE (s:KGOntologySchema {namespace:$namespace}) SET s.version=1,s.patch_json=$patch''',
                 namespace=self.prefix, patch=json.dumps({'properties': {'hermes_note': {'aliases': ['note', 'memo']}}}))
        service = MemoryService(self.config, self.store, review_validator=lambda payload: None)
        with self.assertRaisesRegex(VersionConflict, 'Review aliases changed'):
            service.resolve_review(review_id, 'approve', operation_id='synthetic-legacy-alias-approval')
        self.assertEqual(service.review(review_id)['status'], 'pending')
        self.assertEqual(service.detail(self.a)['props']['hermes_note'], 'ready')
        self.assertEqual(service.resolve_review(review_id, 'reject', operation_id='synthetic-legacy-alias-reject')['disposition'], 'rejected')

    def test_publish_reruns_data_compatibility_after_a_successful_preview(self):
        patch = {'properties': {'hermes_note': {'required_for': ['concept']}}}
        self.assertTrue(self.preview(patch)['can_publish'])
        self.store.write_batch([{'type': 'concept', 'id': 'new-before-save', 'name': 'No note yet'}], [])
        with self.assertRaises(OntologyEditError) as conflict: self.publish(patch)
        self.assertEqual(conflict.exception.code, 'ontology_data_conflict')

    def test_backup_restore_retains_overlay_version_and_idempotency_receipts(self):
        from kg.maintenance import backup, restore
        from kg.store import KGStore
        patch = {'entity_types': {'concept': {'label': 'Portable label'}}}
        self.publish(patch, operation_id='synthetic-portable-publish')
        path = Path(self.tmp.name) / 'backup.zip'
        backup(self.store, path)
        self.clear()
        destination = KGStore(replace(self.config, home=Path(self.tmp.name) / 'restored'), driver=self.store.driver)
        restore(destination, path)
        self.assertEqual(destination.ontology_overlay()['version'], 1)
        replay = OntologyEditor(destination.config, destination).publish(expected_namespace=self.prefix,
                    expected_version=0, patch=patch, operation_id='synthetic-portable-publish')
        self.assertTrue(replay['idempotent'])
        self.assertEqual(replay['schema']['version'], 1)

    def test_restore_rolls_back_data_incompatible_with_the_restored_schema(self):
        from kg.maintenance import backup, restore
        from kg.store import KGStore
        self.publish({'properties': {'hermes_note': {'required_for': ['concept']}}})
        path = Path(self.tmp.name) / 'consistent.zip'
        backup(self.store, path)
        with zipfile.ZipFile(path) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        graph = json.loads(files['graph.json'])
        for node in graph['nodes']:
            if 'Concept' in node['labels']:
                node['props'].pop('hermes_note', None)
        files['graph.json'] = json.dumps(graph).encode()
        manifest = json.loads(files['manifest.json'])
        manifest['files']['graph.json'] = hashlib.sha256(files['graph.json']).hexdigest()
        files['manifest.json'] = json.dumps(manifest).encode()
        crafted = Path(self.tmp.name) / 'incompatible-synthetic.zip'
        with zipfile.ZipFile(crafted, 'x') as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        self.clear()
        destination = KGStore(replace(self.config, home=Path(self.tmp.name) / 'failed-restore'), driver=self.store.driver)
        with self.assertRaisesRegex(ValueError, 'violates the published ontology'):
            restore(destination, crafted)
        self.assertEqual(self.sql('MATCH (n) WHERE n.uri STARTS WITH $prefix RETURN count(n) AS count', prefix=self.prefix)[0]['count'], 0)
        self.assertEqual(destination.ontology_overlay()['version'], 0)

    def test_api_authentication_strict_models_safe_errors_and_successful_publication(self):
        from fastapi.testclient import TestClient
        from kg.console_web import create_app
        from types import SimpleNamespace
        client = TestClient(create_app(self.config, token='synthetic-ontology-token', memory=SimpleNamespace(store=self.store),
                                       search=SimpleNamespace(), background=False), raise_server_exceptions=False)
        self.addCleanup(client.close)
        auth = {'Authorization': 'Bearer synthetic-ontology-token'}
        body = {'expected_namespace': self.prefix, 'expected_version': 0,
                'patch': {'entity_types': {'concept': {'label': 'Published via API'}}}}
        for route in ('validate', 'publish'):
            self.assertEqual(client.post('/api/v1/ontology/' + route, json=body).status_code, 401)
        invalid = client.post('/api/v1/ontology/validate', headers=auth, json={**body, 'expected_version': True})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(client.post('/api/v1/ontology/validate', headers={**auth, 'Origin': 'https://foreign.invalid'}, json=body).status_code, 403)
        valid = client.post('/api/v1/ontology/validate', headers=auth, json=body)
        self.assertEqual(valid.status_code, 200, valid.text)
        self.assertTrue(valid.json()['can_publish'])
        result = client.post('/api/v1/ontology/publish', headers=auth, json={**body, 'operation_id': 'synthetic-api-publish'})
        self.assertEqual(result.status_code, 200, result.text)
        conflict = client.post('/api/v1/ontology/publish', headers=auth, json={**body, 'operation_id': 'synthetic-api-stale'})
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.json()['code'], 'ontology_version_conflict')
        self.assertEqual(conflict.json()['version'], 1)
