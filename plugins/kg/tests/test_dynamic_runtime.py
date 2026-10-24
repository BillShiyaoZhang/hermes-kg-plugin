"""Custom definitions must work as facts across scoped read/write and backups."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
import os
from pathlib import Path
import tempfile
import unittest
import uuid

from kg.config import KGConfig
from kg.schema import (ENTITY_LABELS, canonical_urn, normalize_rel, prepare_entity,
                       prepare_relation, runtime_entity_labels, runtime_kind)


def registry():
    return {
        'entity_types': [{'key': 'x_project', 'storage_label': 'KGCustom_x_project', 'active': True}],
        'relations': [{'key': 'X_HAS_PROJECT', 'storage_type': 'X_HAS_PROJECT', 'aliases': ['owns_project']}],
        'properties': [{'key': 'x_score', 'range': ['integer'], 'domain': ['x_project']}],
    }


class DynamicRuntimeContractTests(unittest.TestCase):
    def test_custom_identity_is_namespace_local_without_mutating_builtin_maps(self):
        ontology = registry()
        config = KGConfig(urn_prefix='urn:customfixture:kg:')
        entity, implicit = prepare_entity({'type': 'x_project', 'id': 'one', 'name': 'Project',
                                           'props': {'x_score': 4}}, config, ontology)
        self.assertEqual(entity['label'], 'KGCustom_x_project')
        self.assertEqual(entity['uri'], config.urn_prefix + 'x_project-one')
        self.assertEqual(entity['properties']['hermes_name'], 'Project')
        self.assertEqual(entity['properties']['x_score'], 4)
        self.assertEqual(implicit, [])
        self.assertNotIn('x_project', ENTITY_LABELS)
        with self.assertRaises(ValueError):
            prepare_entity({'type': 'x_project', 'id': 'one', 'name': 'Project'}, config)
        self.assertEqual(runtime_kind(['KGEntity', entity['label']]), 'x_project')

    def test_custom_typed_uris_aliases_and_relations_preserve_stable_identifiers(self):
        ontology = registry()
        config = KGConfig(urn_prefix='urn:customfixture:kg:')
        uri = config.urn_prefix + 'x_project-release_2026'
        self.assertEqual(canonical_urn(uri, ontology=ontology, prefix=config.urn_prefix), uri)
        self.assertEqual(normalize_rel('owns_project', ontology), 'X_HAS_PROJECT')
        relation = prepare_relation({'from': config.urn_prefix + 'person-owner', 'to': uri,
                                     'type': 'owns_project'}, config, ontology)
        self.assertEqual(relation['type'], 'X_HAS_PROJECT')
        with self.assertRaises(ValueError):
            canonical_urn(uri, 'person', ontology=ontology, prefix=config.urn_prefix)

    def test_storage_identifiers_are_derived_and_cannot_inject_cypher(self):
        ontology = registry()
        ontology['entity_types'][0]['storage_label'] = 'KGCustom_x_project` SET n.secret=1 //'
        with self.assertRaisesRegex(ValueError, 'server-derived'):
            runtime_entity_labels(ontology)
        ontology = registry()
        ontology['relations'][0]['storage_type'] = 'X_HAS_PROJECT`]->() //'
        with self.assertRaisesRegex(ValueError, 'server-derived'):
            normalize_rel('X_HAS_PROJECT', ontology)

    def test_inactive_definitions_still_have_a_historical_read_identity(self):
        ontology = registry()
        ontology['entity_types'][0]['active'] = False
        self.assertEqual(runtime_entity_labels(ontology)['x_project'], 'KGCustom_x_project')
        self.assertEqual(runtime_kind(['KGCustom_x_project'], ontology), 'x_project')

    def test_custom_temporal_properties_normalize_json_values_by_published_range(self):
        ontology = registry()
        ontology['properties'] += [{'key': 'x_due', 'range': ['dateTime']}, {'key': 'x_day', 'range': ['date']}]
        entity, _ = prepare_entity({'type': 'x_project', 'id': 'one', 'name': 'Project',
            'props': {'x_due': '2026-10-24T09:30:00+08:00', 'x_day': '2026-10-24'}}, KGConfig(), ontology)
        self.assertIsInstance(entity['properties']['x_due'], datetime)
        self.assertEqual(entity['properties']['x_day'], date(2026, 10, 24))
        with self.assertRaises(ValueError):
            prepare_entity({'type': 'x_project', 'id': 'one', 'name': 'Project',
                            'props': {'x_day': '2026-10-24T10:00:00'}}, KGConfig(), ontology)


@unittest.skipUnless(os.environ.get('KG_TEST_BOLT_URL'), 'isolated Neo4j URL not configured')
class DynamicRuntimeNeo4jTests(unittest.TestCase):
    def setUp(self):
        from kg.store import KGStore
        from kg.ontology_editing import OntologyEditor
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prefix = 'urn:dynamicruntime' + uuid.uuid4().hex + ':kg:'
        self.foreign = 'urn:dynamicforeign' + uuid.uuid4().hex + ':kg:'
        self.config = KGConfig(home=Path(self.tmp.name) / 'source', neo4j_url=os.environ['KG_TEST_BOLT_URL'],
                               password=os.environ['KG_TEST_PASSWORD'], urn_prefix=self.prefix, default_shared=False)
        self.store = KGStore(self.config)
        self.addCleanup(self.store.close)
        self.store.ensure_schema()
        self.addCleanup(self.clear)
        self.editor = OntologyEditor(self.config, self.store)
        self.publish([
            {'action': 'create', 'kind': 'entity_types', 'key': 'x_project', 'definition': {'label': '项目'}},
            {'action': 'create', 'kind': 'properties', 'key': 'x_score', 'definition': {
                'label': '得分', 'domain': ['x_project'], 'range': ['integer'], 'required_for': ['x_project']}},
            {'action': 'create', 'kind': 'relations', 'key': 'X_HAS_PROJECT', 'definition': {
                'label': '负责项目', 'domain': ['person'], 'range': ['x_project']}},
        ])
        self.project = self.prefix + 'x_project-one'
        self.person = self.prefix + 'person-owner'

    def clear(self):
        with self.store.driver.session(database=self.config.database) as session:
            session.run('''MATCH (n) WHERE n.uri STARTS WITH $prefix OR n.namespace=$prefix
                OR n.uri STARTS WITH $foreign OR n.namespace=$foreign DETACH DELETE n''',
                        {'prefix': self.prefix, 'foreign': self.foreign}).consume()

    def publish(self, operations):
        version = self.store.ontology_overlay()['version']
        preview = self.editor.validate(expected_namespace=self.prefix, expected_version=version, operations=operations)
        self.assertTrue(preview['can_publish'], preview)
        risk = preview['risk']
        return self.editor.publish(expected_namespace=self.prefix, expected_version=version, operations=operations,
            operation_id='dynamic-' + uuid.uuid4().hex, confirmation=risk.get('confirmation_phrase'),
            confirmation_token=risk.get('confirmation_token'))

    def write_facts(self, extra_props=None):
        return self.store.write_batch([
            {'type': 'person', 'uri': self.person, 'name': 'Synthetic Owner'},
            {'type': 'x_project', 'uri': self.project, 'name': 'Synthetic Project', 'props': {'x_score': 4, **(extra_props or {})}},
        ], [{'from': self.person, 'to': self.project, 'type': 'X_HAS_PROJECT'}])

    def test_custom_facts_round_trip_through_cards_provider_search_stats_and_graph(self):
        from kg.lifecycle import MemoryService
        from kg.queries import query_entities
        from kg.ontology import OntologyService
        from kg.search import HybridSearch, entity_document
        self.publish([{'action': 'create', 'kind': 'properties', 'key': 'x_query_marker',
                       'definition': {'label': '检索标记', 'domain': ['x_project'], 'range': ['string']}}])
        marker = 'unique-runtime-property-value'
        self.write_facts({'x_query_marker': marker})
        memory = MemoryService(self.config, self.store)
        card = memory.detail(self.project)
        self.assertEqual(card['type'], 'x_project')
        self.assertEqual(card['props']['x_score'], 4)
        self.assertEqual(memory.list_memories(entity_type='x_project')[0]['uri'], self.project)
        linked = memory.detail(self.person)['relations'][0]
        self.assertEqual(linked['related_type'], 'x_project')
        self.assertEqual(linked['relation_label'], '负责项目')
        provider = query_entities('Synthetic Project', 'x_project', config=self.config, store=self.store)
        self.assertEqual(provider[0]['type'], 'x_project')
        self.assertEqual(provider[0]['properties']['x_score'], 4)
        search = HybridSearch(self.store)
        result = search.search(self.project, filters={'entity_type': 'x_project'})
        self.assertEqual(result['kg_results'][0]['uri'], self.project)
        self.assertEqual(result['kg_results'][0]['properties']['x_score'], 4)
        self.assertIn('4', entity_document(card['props']))
        ontology = OntologyService(self.config, self.store)
        description = ontology.describe()
        self.assertEqual(next(item['count'] for item in description['entity_types'] if item['key'] == 'x_project'), 1)
        overview = ontology.graph_overview()
        self.assertEqual(next(group['count'] for group in overview['groups'] if group['type'] == 'x_project'), 1)
        self.assertEqual(overview['relations'][0]['relation_type'], 'X_HAS_PROJECT')
        self.assertEqual(ontology.graph_overview(q=marker)['total_nodes'], 1)
        self.assertEqual(ontology.entities(q=marker)['items'][0]['uri'], self.project)
        self.assertEqual(ontology.graph(q=marker)['nodes'][0]['uri'], self.project)
        graph = ontology.graph(types=['x_project'])
        self.assertEqual([item['uri'] for item in graph['nodes']], [self.project])
        neighborhood = ontology.graph_neighbors(center_uri=self.person, relation_types=['X_HAS_PROJECT'])
        self.assertEqual({node['type'] for node in neighborhood['nodes']}, {'person', 'x_project'})
        paths = ontology.graph_paths(source_uri=self.person, target_uri=self.project, relation_types=['X_HAS_PROJECT'])
        self.assertEqual(paths['paths'][0]['node_uris'], [self.person, self.project])

    def test_custom_registry_does_not_enable_writes_in_another_namespace(self):
        from kg.store import KGStore
        other = KGStore(replace(self.config, urn_prefix=self.foreign), driver=self.store.driver)
        other.write_batch([{'type': 'person', 'id': 'owner', 'name': 'Foreign Owner'},
                           {'type': 'concept', 'id': 'one', 'name': 'Foreign Concept'}], [])
        with self.assertRaises(ValueError):
            other.write_batch([{'type': 'x_project', 'id': 'one', 'name': 'Foreign Project', 'props': {'x_score': 4}}], [])
        with self.assertRaisesRegex(ValueError, 'not defined in this namespace'):
            other.write_batch([{'type': 'concept', 'id': 'one', 'name': 'Foreign Concept', 'props': {'x_score': 4}}], [])
        with self.assertRaisesRegex(ValueError, 'not defined in this namespace'):
            other.write_batch([], [{'from': self.foreign + 'person-owner', 'to': self.foreign + 'concept-one',
                                    'type': 'RELATED_TO', 'props': {'x_score': 4}}])
        self.assertEqual(other.ontology_overlay()['version'], 0)

    def test_custom_temporal_json_facts_are_persisted_as_typed_values(self):
        from kg.lifecycle import MemoryService
        self.publish([
            {'action': 'create', 'kind': 'properties', 'key': 'x_due', 'definition': {
                'label': '截止', 'domain': ['x_project'], 'range': ['dateTime']}},
            {'action': 'create', 'kind': 'properties', 'key': 'x_day', 'definition': {
                'label': '日期', 'domain': ['x_project'], 'range': ['date']}},
        ])
        self.write_facts({'x_due': '2026-10-24T09:30:00+08:00', 'x_day': '2026-10-24'})
        props = MemoryService(self.config, self.store).detail(self.project)['props']
        self.assertEqual(props['x_due'].to_native().isoformat(), '2026-10-24T09:30:00+08:00')
        self.assertEqual(props['x_day'].to_native(), date(2026, 10, 24))

    def test_relationship_custom_properties_share_datatype_and_namespace_rules(self):
        from kg.lifecycle import MemoryService
        self.write_facts()
        with self.assertRaisesRegex(ValueError, 'property|properties'):
            self.store.write_batch([], [{'from': self.person, 'to': self.project, 'type': 'RELATED_TO',
                                         'props': {'x_score': 'invalid number'}}])
        self.store.write_batch([], [{'from': self.person, 'to': self.project, 'type': 'RELATED_TO',
                                     'props': {'x_score': 5}}])
        relation = next(edge for edge in MemoryService(self.config, self.store).detail(self.person)['relations']
                        if edge['relation_type'] == 'RELATED_TO')
        self.assertEqual(relation['props']['x_score'], 5)

    def seed_legacy_properties(self):
        self.write_facts()
        with self.store.driver.session(database=self.config.database) as session:
            session.run('''MATCH (a {uri:$person})-[r:X_HAS_PROJECT]->(n {uri:$project})
                SET n.x_legacy='old value',n.x_legacy_day=date('2020-01-02'),n.x_legacy_number=1,r.x_legacy='old edge value' ''',
                        {'person': self.person, 'project': self.project}).consume()

    def test_legacy_unknown_property_can_survive_unrelated_correction_but_not_change(self):
        from kg.lifecycle import MemoryService
        self.seed_legacy_properties()
        memory = MemoryService(self.config, self.store)
        current = memory.detail(self.project)
        memory.correct(self.project, {'name': 'Legacy unrelated correction'}, expected_version=current['version'],
                       operation_id='legacy-keep-' + uuid.uuid4().hex)
        current = memory.detail(self.project)
        self.assertEqual(current['props']['x_legacy'], 'old value')
        self.assertEqual(current['props']['x_legacy_day'].to_native(), date(2020, 1, 2))
        with self.assertRaisesRegex(ValueError, 'not defined in this namespace'):
            memory.correct(self.project, {'props': {'x_legacy': 'changed value'}}, expected_version=current['version'],
                           operation_id='legacy-change-' + uuid.uuid4().hex)
        self.assertEqual(memory.detail(self.project)['props']['x_legacy'], 'old value')
        with self.assertRaisesRegex(ValueError, 'not defined in this namespace'):
            memory.correct(self.project, {'props': {'x_legacy_number': True}}, expected_version=current['version'],
                           operation_id='legacy-bool-' + uuid.uuid4().hex)

    def test_historical_unsafe_alias_cannot_reinterpret_stored_literal_on_correction(self):
        import json
        from kg.lifecycle import MemoryService, VersionConflict
        from kg.ontology import _schema
        self.seed_legacy_properties()
        state = self.store.ontology_overlay()
        builtin_aliases = next(item['aliases'] for item in _schema()['properties'] if item['key'] == 'hermes_note')
        state['patch'].setdefault('properties', {}).setdefault('hermes_note', {})['aliases'] = [*builtin_aliases, 'x_legacy']
        # Simulate a valid old overlay/backup predating literal-alias collision checks.
        with self.store.driver.session(database=self.config.database) as session:
            session.run('''MATCH (s:KGOntologySchema {namespace:$namespace}) SET s.patch_json=$patch''',
                        {'namespace': self.prefix, 'patch': json.dumps(state['patch'])}).consume()
        memory = MemoryService(self.config, self.store)
        before = memory.detail(self.project)
        with self.assertRaisesRegex(VersionConflict, 'withdraw conflicting alias'):
            memory.correct(self.project, {'name': 'Unrelated attempted correction'}, expected_version=before['version'],
                           operation_id='unsafe-alias-' + uuid.uuid4().hex)
        after = memory.detail(self.project)
        self.assertEqual(after['props']['x_legacy'], 'old value')
        self.assertNotIn('hermes_note', after['props'])
        self.assertEqual(after['name'], before['name'])
        self.assertEqual(after['version'], before['version'])
        from fastapi.testclient import TestClient
        from kg.console_web import create_app
        from types import SimpleNamespace
        client = TestClient(create_app(self.config, token='synthetic-alias-token', memory=memory,
                                       search=SimpleNamespace(), background=False), raise_server_exceptions=False)
        self.addCleanup(client.close)
        blocked = client.post('/api/v1/memories/correct', headers={'Authorization': 'Bearer synthetic-alias-token'},
            json={'uri': self.project, 'expected_version': before['version'], 'changes': {'name': 'Still blocked'},
                  'operation_id': 'unsafe-alias-http-' + uuid.uuid4().hex})
        self.assertEqual(blocked.status_code, 409)
        self.assertIn('撤销冲突兼容名称', blocked.json()['detail'])
        self.editor.publish(expected_namespace=self.prefix, expected_version=state['version'],
            patch={'properties': {'hermes_note': {'aliases': builtin_aliases}}}, operation_id='withdraw-unsafe-' + uuid.uuid4().hex)
        memory.correct(self.project, {'name': 'Correction after alias withdrawn'}, expected_version=before['version'],
                       operation_id='safe-after-withdraw-' + uuid.uuid4().hex)
        self.assertEqual(memory.detail(self.project)['props']['x_legacy'], 'old value')

    def test_legacy_edge_requires_same_value_on_every_matching_edge_and_cannot_be_forged(self):
        self.seed_legacy_properties()
        def edge(properties, **extra):
            return {'from': self.person, 'to': self.project, 'type': 'X_HAS_PROJECT', 'props': properties, **extra}
        self.store.write_batch([], [edge({'x_legacy': 'old edge value', 'label': 'Unrelated label'})])
        for relation in (edge({'x_legacy': 'changed'}),
                         edge({'x_unknown': 'new'}, _existing_properties=[{'x_unknown': 'new'}]),
                         {**edge({'x_legacy': 'old edge value'}), 'type': 'RELATED_TO'}):
            with self.subTest(relation=relation), self.assertRaisesRegex(ValueError, 'not defined in this namespace'):
                self.store.write_batch([], [relation])
        with self.store.driver.session(database=self.config.database) as session:
            session.run('''MATCH (a {uri:$person}),(b {uri:$project})
                CREATE (a)-[:X_HAS_PROJECT]->(b)''', {'person': self.person, 'project': self.project}).consume()
        with self.assertRaisesRegex(ValueError, 'not defined in this namespace'):
            self.store.write_batch([], [edge({'x_legacy': 'old edge value'})])
        with self.store.driver.session(database=self.config.database) as session:
            values = [row['value'] for row in session.run('''MATCH (a {uri:$person})-[r:X_HAS_PROJECT]->(b {uri:$project})
                RETURN r.x_legacy AS value''', {'person': self.person, 'project': self.project})]
        self.assertCountEqual(values, ['old edge value', None])

    def test_legacy_unknown_values_remain_editable_after_backup_restore(self):
        from kg.lifecycle import MemoryService
        from kg.maintenance import backup, restore
        from kg.store import KGStore
        self.seed_legacy_properties()
        destination = KGStore(replace(self.config, home=Path(self.tmp.name) / 'legacy-restored'), driver=self.store.driver)
        path = Path(self.tmp.name) / 'legacy-backup.zip'
        backup(self.store, path)
        self.clear()
        restore(destination, path)
        memory = MemoryService(destination.config, destination)
        current = memory.detail(self.project)
        memory.correct(self.project, {'name': 'Restored legacy correction'}, expected_version=current['version'],
                       operation_id='legacy-restored-' + uuid.uuid4().hex)
        self.assertEqual(memory.detail(self.project)['props']['x_legacy'], 'old value')
        destination.write_batch([], [{'from': self.person, 'to': self.project, 'type': 'X_HAS_PROJECT',
                                       'props': {'x_legacy': 'old edge value'}}])

    def test_deleted_custom_property_cannot_be_written_as_an_untyped_extra(self):
        self.publish([{'action': 'delete', 'kind': 'properties', 'key': 'x_score'}])
        with self.assertRaisesRegex(ValueError, 'not defined in this namespace'):
            self.store.write_batch([{'type': 'x_project', 'uri': self.project, 'name': 'Synthetic Project',
                                     'props': {'x_score': 4}}], [])
        result = self.store.write_batch([{'type': 'x_project', 'uri': self.project, 'name': 'Synthetic Project'}], [])
        self.assertEqual(result['entities_written'], 1)

    def test_inactive_property_preserves_history_and_blocks_new_values(self):
        from kg.lifecycle import MemoryService
        self.write_facts()
        self.publish([{'action': 'deactivate', 'kind': 'properties', 'key': 'x_score'}])
        memory = MemoryService(self.config, self.store)
        current = memory.detail(self.project)
        self.assertEqual(current['props']['x_score'], 4)
        memory.correct(self.project, {'name': 'Corrected Project'}, expected_version=current['version'],
                       operation_id='unrelated-' + uuid.uuid4().hex)
        current = memory.detail(self.project)
        with self.assertRaises(ValueError):
            memory.correct(self.project, {'props': {'x_score': 5}}, expected_version=current['version'],
                           operation_id='blocked-' + uuid.uuid4().hex)
        self.assertEqual(memory.detail(self.project)['props']['x_score'], 4)

    def test_inactive_custom_dates_allow_unrelated_name_correction(self):
        from kg.lifecycle import MemoryService
        self.publish([
            {'action': 'create', 'kind': 'properties', 'key': 'x_due', 'definition': {
                'label': '截止', 'domain': ['x_project'], 'range': ['dateTime']}},
            {'action': 'create', 'kind': 'properties', 'key': 'x_day', 'definition': {
                'label': '日期', 'domain': ['x_project'], 'range': ['date']}},
        ])
        self.write_facts({'x_due': '2026-10-24T09:30:00+08:00', 'x_day': '2026-10-24'})
        with self.store.driver.session(database=self.config.database) as session:
            session.run('''MATCH (n {uri:$uri}) SET n.x_due=datetime('2026-10-24T09:30:00.123456789+08:00')''',
                        {'uri': self.project}).consume()
        self.publish([{'action': 'deactivate', 'kind': 'properties', 'key': key} for key in ('x_due', 'x_day')])
        memory = MemoryService(self.config, self.store)
        current = memory.detail(self.project)
        memory.correct(self.project, {'name': 'Unrelated temporal correction'}, expected_version=current['version'],
                       operation_id='inactive-date-keep-' + uuid.uuid4().hex)
        current = memory.detail(self.project)
        self.assertEqual(current['props']['x_day'].to_native(), date(2026, 10, 24))
        self.assertEqual(current['props']['x_due'].to_native().isoformat(), '2026-10-24T09:30:00.123456+08:00')
        self.assertEqual(current['props']['x_due'].nanosecond, 123456789)
        for key, value in (('x_day', '2026-10-25'), ('x_due', '2026-10-25T09:30:00+08:00')):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'inactive'):
                memory.correct(self.project, {'props': {key: value}}, expected_version=current['version'],
                               operation_id='inactive-date-change-' + uuid.uuid4().hex)

    def test_inactive_type_allows_historical_correction_but_blocks_new_facts_and_edges(self):
        from kg.lifecycle import MemoryService
        self.write_facts()
        self.publish([{'action': 'deactivate', 'kind': 'entity_types', 'key': 'x_project'}])
        memory = MemoryService(self.config, self.store)
        current = memory.detail(self.project)
        memory.correct(self.project, {'name': 'Corrected Historical Project'}, expected_version=current['version'],
                       operation_id='historical-' + uuid.uuid4().hex)
        self.assertEqual(memory.detail(self.project)['name'], 'Corrected Historical Project')
        with self.assertRaisesRegex(ValueError, 'inactive'):
            self.store.write_batch([{'type': 'x_project', 'id': 'two', 'name': 'New Project', 'props': {'x_score': 4}}], [])
        with self.assertRaisesRegex(ValueError, 'inactive'):
            self.store.write_batch([], [{'from': self.person, 'to': self.project, 'type': 'RELATED_TO'}])

    def test_custom_schema_and_inactive_history_survive_backup_restore(self):
        from kg.lifecycle import MemoryService
        from kg.maintenance import backup, restore
        from kg.store import KGStore
        self.write_facts()
        self.publish([{'action': 'deactivate', 'kind': 'relations', 'key': 'X_HAS_PROJECT'}])
        destination = KGStore(replace(self.config, home=Path(self.tmp.name) / 'restored'), driver=self.store.driver)
        path = Path(self.tmp.name) / 'synthetic-backup.zip'
        backup(self.store, path)
        self.clear()
        restore(destination, path)
        restored = MemoryService(destination.config, destination).detail(self.project)
        self.assertEqual(restored['type'], 'x_project')
        self.assertEqual(restored['props']['x_score'], 4)
        definition = next(item for item in destination.ontology_schema()['relations'] if item['key'] == 'X_HAS_PROJECT')
        self.assertFalse(definition['active'])
        self.assertEqual(MemoryService(destination.config, destination).detail(self.person)['relations'][0]['relation_type'], 'X_HAS_PROJECT')
