"""Pure semantic-card projections: no database, model or network required."""
from copy import deepcopy
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kg.retrieval_documents import (dataset_to_documents, entity_card, ontology_cards,
                                    relation_cards)

NS = 'urn:retrieval-fixture:kg:'


def schema():
    return {
        'namespace': NS, 'version': 3,
        'entity_types': [
            {'key': 'person', 'label': '人物', 'storage_label': 'Person', 'active': True},
            {'key': 'x_project', 'label': '项目', 'storage_label': 'KGCustom_x_project', 'active': True},
        ],
        'properties': [
            {'key': 'foaf_name', 'label': '姓名', 'range': ['string'], 'domain': ['person'], 'managed': False},
            {'key': 'hermes_name', 'label': '名称', 'range': ['string'], 'domain': ['any'], 'managed': False},
            {'key': 'hermes_description', 'label': '说明', 'range': ['string'], 'domain': ['any'], 'managed': False},
            {'key': 'x_score', 'label': '得分', 'description': '原始记录的数值，无声明单位。',
             'aliases': ['rating'], 'range': ['integer'], 'domain': ['x_project'],
             'required_for': ['x_project'], 'managed': False, 'active': True},
            {'key': 'hermes_evidenceJson', 'label': '系统证据', 'managed': True},
        ],
        'relations': [
            {'key': 'X_LEADS', 'label': '负责', 'description': '来源人物负责目标项目。',
             'storage_type': 'X_LEADS', 'aliases': ['leads_project'],
             'domain': ['person'], 'range': ['x_project'], 'active': True},
            {'key': 'RELATED_TO', 'label': '有关', 'domain': ['any'], 'range': ['any'], 'active': True},
        ],
    }


def fixture():
    return {
        'namespace': NS, 'schema': schema(),
        'entities': [
            {'id': 'lin', 'type': 'Person', 'name': '林舟',
             'props': {'alias_names': ['Lin'], 'description': '负责试点项目。'}},
            {'id': 'alpha', 'type': 'x_project', 'name': 'Alpha', 'props': {'rating': 4}},
        ],
        'relations': [{'from': 'lin', 'to': 'alpha', 'type': 'leads_project',
                       'props': {'note': '在本季度负责'}}],
    }


class RetrievalDocumentTests(unittest.TestCase):
    def test_representations_have_explicit_rule_and_fact_boundaries(self):
        data = fixture()
        self.assertEqual([doc.kind for doc in dataset_to_documents(data, 'values')], ['entity', 'entity'])
        self.assertEqual([doc.kind for doc in dataset_to_documents(data, 'entity')], ['entity', 'entity'])
        documents = dataset_to_documents(data, 'entity_relations', include_ontology=True)
        self.assertEqual(sum(doc.kind == 'relation' for doc in documents), 1)
        rules = [doc for doc in documents if doc.kind == 'ontology']
        self.assertEqual(len(rules), 9)
        self.assertTrue(all(doc.metadata['is_rule'] and '不是已发生的事实' in doc.text for doc in rules))
        self.assertTrue(all(not doc.metadata['is_rule'] for doc in documents if doc.kind != 'ontology'))
        # An allowed type pair produces only a rule, never synthetic fact edges.
        data['relations'] = []
        self.assertFalse(any(doc.kind == 'relation' for doc in
                             dataset_to_documents(data, 'entity_relations', include_ontology=True)))

    def test_values_matches_current_value_only_projection_without_system_fields(self):
        from kg.search import entity_document
        props = {'uri': NS + 'x_project-alpha', 'hermes_name': 'Alpha', 'x_score': 4,
                 'hermes_note': '已完成', 'hermes_aliasNames': ['A', '试点'],
                 'hermes_eventTime': datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc),
                 'hermes_revision': 2, 'hermes_sourceSession': 'secret-session',
                 'hermes_evidenceJson': 'private-source-body', 'token': 'private-token'}
        doc = entity_card({'properties': props, 'labels': ['KGEntity', 'KGCustom_x_project']},
                          schema=schema(), representation='values')
        self.assertEqual(doc.text, entity_document(props))
        self.assertNotIn('private', doc.text)
        self.assertNotIn('secret-session', doc.text)
        self.assertEqual(doc.metadata['source_revision'], 2)

    def test_typed_custom_field_uses_published_label_without_inventing_units(self):
        doc = entity_card(fixture()['entities'][1], schema=schema())
        self.assertIn('类型: 项目 (x_project)', doc.text)
        self.assertIn('得分 [x_score; integer]: 4', doc.text)
        self.assertNotIn('4分', doc.text)
        self.assertNotIn('rating', doc.text)
        self.assertEqual(doc.source_refs, (NS + 'x_project-alpha',))
        self.assertEqual(doc.metadata['fixture_id'], 'alpha')

    def test_property_order_and_schema_array_order_do_not_change_documents(self):
        data = fixture()
        first = [doc.to_dict() for doc in dataset_to_documents(data, 'entity_relations', include_ontology=True)]
        changed = deepcopy(data)
        changed['entities'].reverse()
        changed['schema']['entity_types'].reverse()
        changed['schema']['properties'].reverse()
        changed['schema']['relations'].reverse()
        for entity in changed['entities']:
            entity['props'] = dict(reversed(list(entity['props'].items())))
        self.assertEqual(first, [doc.to_dict() for doc in
                                dataset_to_documents(changed, 'entity_relations', include_ontology=True)])

    def test_metadata_label_edit_changes_content_but_retains_source_identity(self):
        before = entity_card(fixture()['entities'][1], schema=schema())
        changed = schema()
        changed['properties'][3]['label'] = '评估值'
        changed['version'] += 1
        after = entity_card(fixture()['entities'][1], schema=changed)
        self.assertEqual(before.source_key, after.source_key)
        self.assertNotEqual(before.id, after.id)
        self.assertNotEqual(before.dependency_fingerprint, after.dependency_fingerprint)
        self.assertIn('评估值 [x_score; integer]: 4', after.text)

    def test_same_text_new_revision_invalidates_dependency_without_new_content_id(self):
        entity = {'properties': {'uri': NS + 'person-lin', 'foaf_name': '林舟', 'hermes_revision': 1},
                  'labels': ['Person']}
        before = entity_card(entity, schema=schema())
        entity['properties']['hermes_revision'] = 2
        after = entity_card(entity, schema=schema())
        self.assertEqual(before.id, after.id)
        self.assertNotEqual(before.dependency_fingerprint, after.dependency_fingerprint)

    def test_stored_literals_are_not_reinterpreted_by_new_custom_aliases(self):
        unsafe = schema()
        unsafe['properties'][3]['aliases'].append('legacy_field')
        stored = {'properties': {'uri': NS + 'x_project-old', 'hermes_name': '旧记录', 'legacy_field': 9},
                  'labels': ['KGCustom_x_project']}
        doc = entity_card(stored, schema=unsafe)
        self.assertNotIn('得分', doc.text)
        self.assertNotIn('x_score', doc.metadata['property_keys'])
        # Authored fixture aliases are explicitly normalized instead.
        authored = {'id': 'new', 'type': 'x_project', 'name': '新记录', 'props': {'legacy_field': 9}}
        self.assertIn('得分 [x_score; integer]: 9', entity_card(authored, schema=unsafe).text)

    def test_relation_direction_and_self_loop_are_preserved(self):
        data = fixture()
        data['relations'] = [
            {'from': 'lin', 'to': 'alpha', 'type': 'RELATED_TO'},
            {'from': 'alpha', 'to': 'lin', 'type': 'RELATED_TO'},
            {'from': 'lin', 'to': 'lin', 'type': 'RELATED_TO'},
        ]
        docs = relation_cards(data['relations'], data['entities'], schema=data['schema'])
        self.assertEqual(len({doc.source_key for doc in docs}), 3)
        forward = next(doc for doc in docs if doc.metadata['from_id'] == 'lin' and doc.metadata['to_id'] == 'alpha')
        self.assertIn('林舟 → Alpha', forward.text)
        self.assertEqual(forward.metadata['direction'], 'out')
        loop = next(doc for doc in docs if doc.metadata['self_loop'])
        self.assertEqual(loop.source_refs, (NS + 'person-lin',))
        self.assertEqual(loop.metadata['from_uri'], loop.metadata['to_uri'])

    def test_parallel_qualifiers_sources_and_exact_duplicate_count_survive(self):
        data = fixture()
        relations = [
            {'from': 'lin', 'to': 'alpha', 'type': 'X_LEADS', 'props': {'weight': 1}},
            {'from': 'lin', 'to': 'alpha', 'type': 'X_LEADS', 'props': {'weight': 2}},
            {'from': 'lin', 'to': 'alpha', 'type': 'leads_project', 'props': {'weight': 1}},
            {'from': 'lin', 'to': 'alpha', 'type': 'X_LEADS',
             'props': {'weight': 1, 'hermes_sourceSession': 'session-b'}},
        ]
        docs = relation_cards(relations, data['entities'], schema=data['schema'])
        self.assertEqual(len(docs), 3)
        self.assertEqual(len({doc.source_key for doc in docs}), 1)
        self.assertEqual(sorted(doc.metadata['duplicate_count'] for doc in docs), [1, 1, 2])
        duplicate = next(doc for doc in docs if doc.metadata['duplicate_count'] == 2)
        self.assertEqual(duplicate.metadata['stored_types'], ['X_LEADS', 'leads_project'])
        self.assertEqual([doc.to_dict() for doc in docs], [doc.to_dict() for doc in
            relation_cards(list(reversed(relations)), list(reversed(data['entities'])), schema=data['schema'])])

    def test_relation_provenance_is_not_embedding_text_and_evidence_body_is_not_exposed(self):
        data = fixture()
        relation = {'from': 'lin', 'to': 'alpha', 'type': 'X_LEADS',
                    'properties': {'hermes_sourceSession': 'source-id-only',
                                   'hermes_evidenceJson': 'private-conversation-body'}}
        first = relation_cards([relation], data['entities'], schema=data['schema'])[0]
        self.assertNotIn('source-id-only', first.text)
        self.assertNotIn('private-conversation-body', json.dumps(first.to_dict(), ensure_ascii=False))
        relation['properties']['hermes_evidenceJson'] = 'different-private-body'
        second = relation_cards([relation], data['entities'], schema=data['schema'])[0]
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(first.source_key, second.source_key)

    def test_endpoint_version_or_name_invalidates_relation_dependencies(self):
        data = fixture()
        before = relation_cards(data['relations'], data['entities'], schema=data['schema'])[0]
        data['entities'][1]['hermes_revision'] = 9
        revision = relation_cards(data['relations'], data['entities'], schema=data['schema'])[0]
        self.assertEqual(before.id, revision.id)
        self.assertNotEqual(before.dependency_fingerprint, revision.dependency_fingerprint)
        data['entities'][1]['name'] = 'Beta'
        renamed = relation_cards(data['relations'], data['entities'], schema=data['schema'])[0]
        self.assertNotEqual(before.id, renamed.id)
        self.assertEqual(before.source_key, renamed.source_key)
        self.assertIn('林舟 → Beta', renamed.text)

    def test_relation_identity_is_rebuildable_without_physical_edge_ids(self):
        data = fixture()
        data['relations'][0]['element_id'] = 'old-store:12'
        before = relation_cards(data['relations'], data['entities'], schema=data['schema'])[0]
        data['relations'][0]['element_id'] = 'restored-store:48'
        after = relation_cards(data['relations'], data['entities'], schema=data['schema'])[0]
        self.assertEqual(before.to_dict(), after.to_dict())

    def test_normalized_memory_version_is_preserved_as_dependency(self):
        memory = {'uri': NS + 'person-lin', 'type': 'person', 'name': '林舟',
                  'props': {}, 'version': 7, 'updated_at': '2026-10-24T12:00:00Z'}
        doc = entity_card(memory, schema=schema())
        self.assertEqual(doc.metadata['source_revision'], 7)
        self.assertEqual(doc.metadata['source_updated_at'], '2026-10-24T12:00:00Z')

    def test_archive_disabled_quarantine_and_private_foreign_are_excluded(self):
        data = fixture()
        data['entities'].extend([
            {'id': 'archived', 'type': 'Person', 'name': '归档', 'archived': True},
            {'id': 'disabled', 'type': 'Person', 'name': '停用', 'disabled': True},
            {'properties': {'uri': NS + 'person-quarantined', 'foaf_name': '隔离'},
             'labels': ['Person', 'KGQuarantined']},
            {'uri': 'urn:other:kg:person-private', 'id': 'private', 'type': 'Person', 'name': '私有'},
            {'uri': 'urn:other:kg:person-shared', 'id': 'shared', 'type': 'Person', 'name': '共享', 'shared': True},
        ])
        data['relations'].extend([{'from': 'lin', 'to': key, 'type': 'RELATED_TO'}
                                  for key in ('archived', 'disabled', 'private', 'shared')])
        docs = dataset_to_documents(data, 'entity_relations')
        self.assertEqual(sum(doc.kind == 'entity' for doc in docs), 3)
        self.assertEqual(sum(doc.kind == 'relation' for doc in docs), 2)
        self.assertFalse(any('private' in ref for doc in docs for ref in doc.source_refs))
        # The research opt-in for inactive facts cannot widen tenant access.
        historic = dataset_to_documents(data, 'entity_relations', include_inactive=True)
        self.assertFalse(any('private' in ref for doc in historic for ref in doc.source_refs))

    def test_inactive_definition_does_not_hide_historical_facts(self):
        data = fixture()
        data['schema']['entity_types'][1]['active'] = False
        data['schema']['relations'][0]['active'] = False
        docs = dataset_to_documents(data, 'entity_relations', include_ontology=True)
        self.assertTrue(any(doc.kind == 'entity' and doc.metadata['type'] == 'x_project' for doc in docs))
        self.assertTrue(any(doc.kind == 'relation' and doc.metadata['relation_type'] == 'X_LEADS' for doc in docs))
        self.assertTrue(any(doc.kind == 'ontology' and not doc.metadata['active'] for doc in docs))

    def test_ontology_ignores_instance_counts_and_managed_props_never_become_values(self):
        first = ontology_cards(schema())
        changed = schema()
        changed['stats'] = {'entities': 99}
        changed['properties'][3]['count'] = 25
        changed['properties'][3]['allowed_actions'] = ['update']
        self.assertEqual([doc.to_dict() for doc in first], [doc.to_dict() for doc in ontology_cards(changed)])
        entity = fixture()['entities'][1]
        entity['props']['hermes_evidenceJson'] = 'SYSTEM-SOURCE-BODY'
        entity['props']['hermes_lastOperationId'] = 'SYSTEM-OPERATION'
        entity['props']['token'] = 'SYSTEM-TOKEN'
        doc = entity_card(entity, schema=changed)
        self.assertNotIn('SYSTEM-', json.dumps(doc.to_dict(), ensure_ascii=False))

    def test_json_temporals_preserve_types_and_nanoseconds_without_driver_dependency(self):
        data = fixture()
        data['entities'][1]['props']['x_date'] = date(2026, 10, 24)
        data['entities'][1]['hermes_updatedAt'] = datetime(2026, 10, 24, tzinfo=timezone.utc)
        docs = dataset_to_documents(data)
        payload = json.loads(json.dumps([doc.to_dict() for doc in docs], ensure_ascii=False))
        self.assertTrue(any('2026-10-24' in doc['text'] for doc in payload))
        self.assertTrue(all(isinstance(doc['source_refs'], list) for doc in payload))
        from neo4j.time import DateTime
        data['entities'][1]['props']['x_time'] = DateTime(2026, 10, 24, 12, 0, 0, 123456789)
        doc = next(doc for doc in dataset_to_documents(data) if doc.metadata['fixture_id'] == 'alpha')
        self.assertIn('123456789', doc.text)

    def test_fixture_helper_is_nonmutating_and_accepts_existing_search_50(self):
        data = fixture()
        original = deepcopy(data)
        dataset_to_documents(data, 'entity_relations', include_ontology=True)
        self.assertEqual(data, original)
        existing = json.loads((Path(__file__).parent / 'fixtures' / 'search_50.json').read_text(encoding='utf-8'))
        docs = dataset_to_documents(existing, 'entity_relations')
        self.assertEqual(sum(doc.kind == 'entity' for doc in docs), 23)
        self.assertEqual(sum(doc.kind == 'relation' for doc in docs), 6)

    def test_invalid_scope_ambiguous_ids_missing_endpoints_and_nonfinite_values_fail(self):
        data = fixture()
        with self.assertRaisesRegex(ValueError, 'namespaces differ'):
            dataset_to_documents(data, namespace='urn:other:kg:')
        with self.assertRaisesRegex(ValueError, 'representation'):
            dataset_to_documents(data, 'unknown')
        data['entities'].append(deepcopy(data['entities'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            dataset_to_documents(data)
        data = fixture()
        data['relations'][0]['to'] = 'missing'
        with self.assertRaisesRegex(ValueError, 'endpoints'):
            dataset_to_documents(data, 'entity_relations')
        data['entities'][0]['props']['x_bad'] = float('nan')
        with self.assertRaisesRegex(ValueError, 'finite'):
            dataset_to_documents(data)


if __name__ == '__main__':
    unittest.main()
