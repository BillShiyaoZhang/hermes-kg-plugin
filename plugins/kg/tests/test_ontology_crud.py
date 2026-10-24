"""Ontology lifecycle, complete references and server-bound confirmation."""
from __future__ import annotations

from copy import deepcopy
import os
import unittest
from unittest.mock import patch

from kg.config import KGConfig
from kg.ontology import OntologyService
from kg.ontology_editing import OntologyEditError, effective_schema, merge_patch, validate_state
from kg.tests import test_ontology_editing as fixtures


def operation(action, kind, key, **definition):
    return {'action': action, 'kind': kind, 'key': key, **({'definition': definition} if definition else {})}


class OntologyCrudPatchTests(unittest.TestCase):
    def test_labels_normalize_whitespace_and_reject_remaining_control_characters(self):
        config, empty = KGConfig(), {'version': 0, 'patch': {}}
        state, _ = merge_patch(config, empty, {}, [operation('create', 'entity_types', 'x_project', label=' 项目\n  事项\t ' )])
        self.assertEqual(effective_schema(config, state)['entity_types'][-1]['label'], '项目 事项')
        state, _ = merge_patch(config, state, {'entity_types': {'x_project': {'label': ' 新\r\n项目 '}}})
        self.assertEqual(effective_schema(config, state)['entity_types'][-1]['label'], '新 项目')
        with self.assertRaises(OntologyEditError):
            merge_patch(config, state, {'entity_types': {'x_project': {'label': 'Bad\x00label'}}})

    def test_create_generates_immutable_safe_storage_and_durable_state(self):
        config = KGConfig(urn_prefix='urn:crudfixture:kg:')
        state, _ = merge_patch(config, {'version': 0, 'patch': {}}, {}, [
            operation('create', 'entity_types', 'x_project', label='项目'),
            operation('create', 'properties', 'x_code', label='编号', domain=['x_project'], range=['string']),
            operation('create', 'relations', 'X_HAS_PROJECT', label='拥有', domain=['person'], range=['x_project'])])
        schema = validate_state(config, state)
        self.assertEqual(schema['entity_types'][-1]['storage_label'], 'KGCustom_x_project')
        self.assertEqual(schema['relations'][-1]['storage_type'], 'X_HAS_PROJECT')
        self.assertTrue(schema['properties'][-1]['custom'])
        damaged = deepcopy(state)
        damaged['patch']['entity_types']['x_project']['storage_label'] = 'Person'
        with self.assertRaises(ValueError): validate_state(config, damaged)
        with self.assertRaises(OntologyEditError):
            merge_patch(config, state, {'entity_types': {'x_project': {'storage_label': 'Injected'}}})

    def test_builtin_delete_mixed_actions_and_unknown_datatypes_are_rejected(self):
        config, empty = KGConfig(), {'version': 0, 'patch': {}}
        for ops in ([operation('delete', 'entity_types', 'person')],
                    [operation('deactivate', 'properties', 'hermes_createdAt')],
                    [operation('create', 'entity_types', 'Person`', label='Bad')],
                    [operation('create', 'properties', 'x_value', label='Value', range=['code'])],
                    [operation('create', 'properties', 'x_value', label='Value', value_shape='array')],
                    [operation('create', 'entity_types', 'x_project', label='Project'), operation('delete', 'entity_types', 'x_project')]):
            with self.subTest(ops=ops), self.assertRaises(OntologyEditError):
                merge_patch(config, empty, {}, ops)


@unittest.skipUnless(os.environ.get('KG_TEST_BOLT_URL'), 'isolated Neo4j URL not configured')
class OntologyCrudNeo4jTests(unittest.TestCase):
    setUp = fixtures.OntologyEditingNeo4jTests.setUp
    clear = fixtures.OntologyEditingNeo4jTests.clear
    sql = fixtures.OntologyEditingNeo4jTests.sql
    preview = fixtures.OntologyEditingNeo4jTests.preview
    publish = fixtures.OntologyEditingNeo4jTests.publish

    def test_create_update_delete_unused_definition_and_idempotent_historic_receipt(self):
        create = [operation('create', 'properties', 'x_code', label='编号')]
        preview = self.preview(operations=create)
        self.assertTrue(preview['can_publish'])
        self.assertEqual(preview['risk']['level'], 'medium')
        self.publish(operations=create)
        self.publish({'properties': {'x_code': {'label': '项目编号'}}})
        delete = [operation('delete', 'properties', 'x_code')]
        preview = self.preview(operations=delete)
        self.assertEqual(preview['risk']['level'], 'high')
        self.assertEqual(preview['impact']['definitions'][0]['reference_count'], 0)
        with self.assertRaises(OntologyEditError) as missing: self.publish(operations=delete)
        self.assertEqual(missing.exception.code, 'ontology_confirmation_conflict')
        result = self.publish(operations=delete, confirm=True, operation_id='synthetic-crud-delete')
        self.assertNotIn('x_code', {item['key'] for item in result['schema']['properties']})
        self.publish({'entity_types': {'concept': {'label': 'Later'}}})
        replay = self.editor.publish(expected_namespace=self.prefix, expected_version=2, operations=delete,
                                     operation_id='synthetic-crud-delete')
        self.assertTrue(replay['idempotent'])
        self.assertEqual(replay['schema']['version'], 3)

    def test_builtin_deactivate_retains_reads_allows_existing_correction_and_restore(self):
        ops = [operation('deactivate', 'entity_types', 'concept')]
        preview = self.preview(operations=ops)
        self.assertTrue(preview['risk']['requires_confirmation'])
        self.assertGreater(preview['impact']['definitions'][0]['active_nodes'], 0)
        self.publish(operations=ops, confirm=True)
        service = OntologyService(self.config, self.store)
        self.assertEqual(service.entities(type='concept')['items'][0]['uri'], self.a)
        self.store.write_batch([{'type': 'concept', 'uri': self.a, 'name': 'Existing correction'}], [])
        with self.assertRaisesRegex(ValueError, 'inactive'):
            self.store.write_batch([{'type': 'concept', 'id': 'new-inactive', 'name': 'Forbidden'}], [])
        restored = self.preview(operations=[operation('restore', 'entity_types', 'concept')])
        self.assertEqual(restored['risk']['level'], 'medium')
        self.publish(operations=[operation('restore', 'entity_types', 'concept')])
        self.store.write_batch([{'type': 'concept', 'id': 'new-active', 'name': 'Allowed'}], [])

    def test_definition_reference_blocks_type_delete_but_deactivation_is_available(self):
        self.publish(operations=[operation('create', 'entity_types', 'x_project', label='项目'),
            operation('create', 'properties', 'x_code', label='编号', domain=['x_project'])])
        preview = self.preview(operations=[operation('delete', 'entity_types', 'x_project')])
        self.assertFalse(preview['can_publish'])
        self.assertEqual(preview['impact']['definitions'][0]['definition_references'], 1)
        self.assertEqual(preview['impact']['violations'][0]['code'], 'definition_in_use')
        self.publish(operations=[operation('deactivate', 'entity_types', 'x_project')], confirm=True)

    def test_empty_custom_type_can_be_deleted_despite_derived_base_name_rule(self):
        self.publish(operations=[operation('create', 'entity_types', 'x_empty', label='空类型')])
        ops = [operation('delete', 'entity_types', 'x_empty')]
        preview = self.preview(operations=ops)
        self.assertEqual(preview['impact']['definitions'][0]['reference_count'], 0)
        self.assertTrue(preview['can_publish'])
        self.publish(operations=ops, confirm=True)
        self.assertNotIn('x_empty', {item['key'] for item in OntologyService(self.config, self.store).describe()['entity_types']})

    def test_archived_fact_blocks_custom_type_deletion(self):
        from kg.lifecycle import MemoryService
        self.publish(operations=[operation('create', 'entity_types', 'x_project', label='项目')])
        uri = self.prefix + 'x_project-archived'
        self.store.write_batch([{'type': 'x_project', 'uri': uri, 'name': 'Archived project'}], [])
        MemoryService(self.config, self.store).archive(uri, expected_version=1, operation_id='synthetic-crud-archive')
        preview = self.preview(operations=[operation('delete', 'entity_types', 'x_project')])
        reference = preview['impact']['definitions'][0]
        self.assertEqual(reference['active_nodes'], 0)
        self.assertEqual(reference['archived_nodes'], 1)
        self.assertGreater(reference['history_revisions'], 0)
        self.assertFalse(preview['can_publish'])

    def test_pending_candidate_blocks_property_delete_until_rejected(self):
        from kg.lifecycle import MemoryService
        self.publish(operations=[operation('create', 'properties', 'x_marker', label='标记')])
        self.sql('MATCH (n {uri:$uri}) SET n.hermes_userConfirmed=true', uri=self.a)
        receipt = self.store.write_batch([{'type': 'concept', 'uri': self.a, 'name': 'Changed candidate',
                                          'props': {'x_marker': 'Proposal'}}], [], origin='auto')
        delete = [operation('delete', 'properties', 'x_marker')]
        preview = self.preview(operations=delete)
        self.assertEqual(preview['impact']['definitions'][0]['pending_reviews'], 1)
        self.assertFalse(preview['can_publish'])
        MemoryService(self.config, self.store).resolve_review(receipt['review_batch_id'], 'reject', operation_id='synthetic-crud-reject')
        self.assertTrue(self.preview(operations=delete)['can_publish'])
        self.publish(operations=delete, confirm=True)

    def test_historical_property_reference_blocks_delete_after_current_value_removed(self):
        self.publish(operations=[operation('create', 'properties', 'x_marker', label='标记')])
        self.store.write_batch([{'type': 'concept', 'uri': self.a, 'name': 'Historical', 'props': {'x_marker': 'old'}}], [])
        self.sql('MATCH (n {uri:$uri}) REMOVE n.x_marker', uri=self.a)
        preview = self.preview(operations=[operation('delete', 'properties', 'x_marker')])
        reference = preview['impact']['definitions'][0]
        self.assertEqual(reference['active_nodes'], 0)
        self.assertGreater(reference['history_revisions'], 0)
        self.assertFalse(preview['can_publish'])

    def test_confirmation_cannot_be_reused_after_reference_counts_change(self):
        ops = [operation('deactivate', 'relations', 'RELATED_TO')]
        preview = self.preview(operations=ops)
        self.store.write_batch([], [{'from': self.a, 'to': self.p, 'type': 'RELATED_TO'}])
        with self.assertRaises(OntologyEditError) as changed:
            self.editor.publish(expected_namespace=self.prefix, expected_version=0, operations=ops,
                operation_id='synthetic-stale-confirm', confirmation=preview['risk']['confirmation_phrase'],
                confirmation_token=preview['risk']['confirmation_token'])
        self.assertEqual(changed.exception.code, 'ontology_confirmation_conflict')
        self.assertEqual(self.store.ontology_overlay()['version'], 0)

    def test_metadata_after_custom_creation_remains_low_risk_without_full_graph_validation(self):
        result = self.publish(operations=[operation('create', 'properties', 'x_marker', label='标记'),
                                          operation('create', 'entity_types', 'x_project', label='项目')])
        name = next(item for item in result['schema']['properties'] if item['key'] == 'hermes_name')
        self.assertIn('x_project', result['schema']['editing']['builtin_constraints']['properties']['hermes_name']['required_for'])
        with patch.object(self.editor, '_impact', wraps=self.editor._impact) as check:
            preview = self.preview({'properties': {'x_marker': {'label': '更清楚的标记'},
                'hermes_name': {'label': '名称', **{field: name[field] for field in ('domain', 'range', 'required_for', 'aliases')}}}})
        self.assertFalse(preview['structural'])
        self.assertEqual(preview['risk']['level'], 'low')
        self.assertEqual(preview['impact']['checked_nodes'], 0)
        self.assertFalse(check.call_args.args[-1])
        with self.assertRaises(OntologyEditError):
            self.preview({'properties': {'hermes_name': {'required_for': [key for key in name['required_for'] if key != 'x_project']}}})

    def test_relation_reference_scan_includes_foreign_target_and_disabled_endpoint(self):
        self.publish(operations=[operation('create', 'relations', 'X_POINTS_TO', label='指向')])
        self.sql('''MATCH (a {uri:$uri}) CREATE (b:Concept {uri:$foreign,hermes_name:'Synthetic foreign',shared:true,hermes_enabled:false}),
                    (a)-[:X_POINTS_TO]->(b)''', uri=self.a, foreign=self.foreign + 'concept-target')
        preview = self.preview(operations=[operation('delete', 'relations', 'X_POINTS_TO')])
        reference = preview['impact']['definitions'][0]
        self.assertEqual(reference['active_relations'], 0)
        self.assertEqual(reference['archived_relations'], 1)
        self.assertFalse(preview['can_publish'])

    def test_lifecycle_api_auth_strict_fields_and_statistics_failure(self):
        from fastapi.testclient import TestClient
        from kg.console_web import create_app
        from types import SimpleNamespace
        client = TestClient(create_app(self.config, token='synthetic-crud-token', memory=SimpleNamespace(store=self.store), search=SimpleNamespace(), background=False), raise_server_exceptions=False)
        self.addCleanup(client.close)
        body = {'expected_namespace': self.prefix, 'expected_version': 0,
                'operations': [operation('create', 'properties', 'x_marker', label='标记')]}
        auth = {'Authorization': 'Bearer synthetic-crud-token'}
        self.assertEqual(client.post('/api/v1/ontology/validate', json=body).status_code, 401)
        self.assertEqual(client.post('/api/v1/ontology/validate', headers=auth, json=body).status_code, 200)
        invalid = deepcopy(body)
        invalid['operations'][0]['namespace'] = self.foreign
        self.assertEqual(client.post('/api/v1/ontology/publish', headers=auth, json={**invalid, 'operation_id': 'synthetic-api-invalid'}).status_code, 422)
        with patch('kg.ontology_editing.OntologyEditor._references', side_effect=RuntimeError('Private synthetic detail')):
            failed = client.post('/api/v1/ontology/publish', headers=auth, json={**body, 'operation_id': 'synthetic-api-count-failed'})
        self.assertEqual(failed.status_code, 503)
        self.assertNotIn('Private', failed.text)
        self.assertEqual(self.store.ontology_overlay()['version'], 0)

    def test_create_requires_active_explicit_type_dependencies(self):
        self.publish(operations=[operation('deactivate', 'entity_types', 'concept')], confirm=True)
        preview = self.preview(operations=[operation('create', 'properties', 'x_marker', label='标记', domain=['concept'])])
        self.assertFalse(preview['can_publish'])
        self.assertEqual(preview['impact']['violations'][-1]['code'], 'inactive_reference')
        combined = self.preview(operations=[operation('restore', 'entity_types', 'concept'),
            operation('create', 'properties', 'x_marker', label='标记', domain=['concept'])])
        self.assertTrue(combined['can_publish'])

    def test_custom_relationship_datatype_is_validated_including_shared_foreign_target(self):
        self.sql('''MATCH (a {uri:$uri}) CREATE (b:Concept {uri:$foreign,hermes_name:'Synthetic foreign',shared:true}),
                    (a)-[:RELATED_TO {x_marker:42}]->(b)''', uri=self.a, foreign=self.foreign + 'concept-typed')
        create = [operation('create', 'properties', 'x_marker', label='标记', range=['string'])]
        preview = self.preview(operations=create)
        self.assertFalse(preview['can_publish'])
        self.assertIn('datatype', {item['code'] for item in preview['impact']['violations']})
        self.sql('MATCH (a {uri:$uri})-[r:RELATED_TO]->(b) WHERE b.uri=$foreign REMOVE r.x_marker', uri=self.a, foreign=self.foreign + 'concept-typed')
        self.publish(operations=create)
        with self.assertRaises(ValueError):
            self.store.write_batch([], [{'from': self.a, 'to': self.p, 'type': 'RELATED_TO', 'props': {'x_marker': 42}}])

    def test_existing_datetime_cannot_be_retyped_as_date_and_datetime_lists_are_not_scalar(self):
        self.publish(operations=[operation('create', 'properties', 'x_time', label='时间', range=['dateTime'])])
        self.store.write_batch([{'type': 'concept', 'uri': self.a, 'name': 'Temporal', 'props': {'x_time': '2026-10-24T09:00:00+08:00'}}], [])
        changed = self.preview({'properties': {'x_time': {'range': ['date']}}})
        self.assertFalse(changed['can_publish'])
        self.assertIn('datatype', {item['code'] for item in changed['impact']['violations']})
        self.sql('MATCH (n {uri:$uri}) SET n.x_other=[datetime("2026-10-24T09:00:00+08:00")]', uri=self.a)
        scalar = self.preview(operations=[operation('create', 'properties', 'x_other', label='另一时间', range=['dateTime'])])
        self.assertFalse(scalar['can_publish'])
        self.assertIn('datatype', {item['code'] for item in scalar['impact']['violations']})

    def test_new_alias_cannot_reinterpret_existing_literal_fields_during_correction(self):
        from kg.lifecycle import MemoryService
        self.sql('MATCH (n {uri:$uri}) SET n.memo="Literal memo",n.x_legacy="Literal custom"', uri=self.a)
        change = {'properties': {'hermes_note': {'aliases': ['note', 'memo', 'x_legacy']}}}
        preview = self.preview(change)
        self.assertFalse(preview['can_publish'])
        self.assertEqual({item['alias'] for item in preview['impact']['violations'] if item['code'] == 'alias_field_in_use'}, {'memo', 'x_legacy'})
        with self.assertRaises(OntologyEditError): self.publish(change)
        service = MemoryService(self.config, self.store)
        service.correct(self.a, {'name': 'Unrelated correction'}, expected_version=1, operation_id='literal-alias-preserved')
        props = service.detail(self.a)['props']
        self.assertEqual((props['memo'], props['x_legacy'], props['hermes_note']), ('Literal memo', 'Literal custom', 'ready'))

    def test_alias_collision_scan_includes_archive_history_pending_and_owned_foreign_edge(self):
        from kg.lifecycle import MemoryService
        self.store.write_batch([{'type': 'concept', 'id': 'archived-literal', 'name': 'Archived', 'props': {'archive_literal': 'old'}}], [])
        service = MemoryService(self.config, self.store)
        service.archive(self.prefix + 'concept-archived-literal', expected_version=1, operation_id='archive-literal-alias')
        self.store.write_batch([{'type': 'concept', 'uri': self.a, 'name': 'History', 'props': {'history_literal': 'old'}}], [])
        self.sql('MATCH (n {uri:$uri}) REMOVE n.history_literal SET n.hermes_userConfirmed=true', uri=self.a)
        self.store.write_batch([{'type': 'concept', 'uri': self.a, 'name': 'Candidate', 'props': {'pending_literal': 'old'}}], [], origin='auto')
        self.sql('''MATCH (n {uri:$uri}) CREATE (b:Concept {uri:$foreign,hermes_name:'Foreign',shared:true}),
            (n)-[:RELATED_TO {edge_literal:'old'}]->(b)''', uri=self.a, foreign=self.foreign + 'concept-literal')
        aliases = ['archive_literal', 'history_literal', 'pending_literal', 'edge_literal']
        preview = self.preview({'properties': {'hermes_note': {'aliases': ['note', *aliases]}}})
        self.assertFalse(preview['can_publish'])
        self.assertEqual({item['alias'] for item in preview['impact']['violations'] if item['code'] == 'alias_field_in_use'}, set(aliases))

    def test_pending_top_level_names_and_implicit_owner_agent_relations_are_counted(self):
        from dataclasses import replace
        self.config = replace(self.config, user_person_urn=self.p)
        self.store.config = self.config
        self.editor.config = self.config
        records = [
            {'type': 'preference', 'id': 'pending-pref', 'name': 'Preference', 'props': {'preference_key': 'key', 'preference_value': 'value'}},
            {'type': 'decision', 'id': 'pending-decision', 'name': 'Decision', 'props': {'decision_text': 'text'}},
            {'type': 'event', 'id': 'pending-event', 'name': 'Event', 'agent': self.p, 'props': {'event_time': '2026-10-24'}},
        ]
        for record in records:
            self.store.write_batch([record], [])
            uri = self.prefix + record['type'] + '-' + record['id']
            self.sql('MATCH (n {uri:$uri}) SET n.hermes_userConfirmed=true', uri=uri)
            receipt = self.store.write_batch([{**record, 'name': 'Changed ' + record['name']}], [], origin='auto')
            self.assertEqual(receipt['disposition'], 'deferred')
        self.sql('MATCH (n {uri:$uri}) SET n.hermes_userConfirmed=true', uri=self.p)
        self.store.write_batch([{'type': 'person', 'uri': self.p, 'name': 'Changed person'}], [], origin='auto')
        for kind, key, expected in [('properties', 'foaf_name', 1), ('properties', 'hermes_name', 3),
                                    ('relations', 'HERMES_hasPreference', 1), ('relations', 'HERMES_hasDecision', 1),
                                    ('relations', 'HERMES_participant', 1)]:
            with self.subTest(key=key):
                preview = self.preview(operations=[operation('deactivate', kind, key)])
                self.assertEqual(preview['impact']['definitions'][0]['pending_reviews'], expected)
