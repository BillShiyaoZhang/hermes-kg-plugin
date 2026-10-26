"""Packaged presets must never overwrite a personal namespace or publish half a model."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from kg.config import KGConfig
from kg.ontology_editing import (OntologyEditError, OntologyEditor, effective_schema,
                                 merge_patch, state_from_rows, validate_state)
from kg.ontology_presets import (_assemble, initialize_personal_preset, install_preset,
                                 preset_digest, validate_presets)


def fixture_profile(extra_properties=0):
    kind = 'x_personal_fixture'
    operations = [
        {'action': 'create', 'kind': 'entity_types', 'key': kind,
         'definition': {'label': '合成类型', 'description': 'A synthetic fixture, never personal data.'}},
        {'action': 'create', 'kind': 'properties', 'key': 'x_personal_fixture_score',
         'definition': {'label': '得分', 'domain': [kind], 'range': ['double'], 'required_for': []}},
        {'action': 'create', 'kind': 'relations', 'key': 'X_PERSONAL_FIXTURE',
         'definition': {'label': '合成关系', 'domain': ['person'], 'range': [kind]}},
    ]
    operations += [{'action': 'create', 'kind': 'properties', 'key': f'x_personal_fixture_{index}',
                    'definition': {'label': f'合成字段 {index}', 'domain': [kind], 'range': ['string'], 'required_for': []}}
                   for index in range(extra_properties)]
    bindings = {collection: {} for collection in ('entity_types', 'properties', 'relations')}
    for operation in operations:
        bindings[operation['kind']][operation['key']] = {
            'iri': 'https://example.org/ontology/' + operation['key'], 'mapping': 'reuse',
            'source': 'synthetic', 'source_version': '1', 'direction': 'forward', 'note': 'Synthetic test only.'}
    return {'id': 'personal', 'version': '1.0.0', 'digest': 'a' * 64,
            'sources': [{'id': 'synthetic', 'version': '1', 'iri': 'https://example.org/ontology',
                         'sha256': 'c' * 64, 'license': 'test fixture'}],
            'operations': operations, 'bindings': bindings,
            'provenance': {'record': 'http://www.w3.org/ns/prov#Entity'}}


class PresetAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.config = KGConfig(urn_prefix='urn:presetfixture:kg:')
        self.state = {'version': 0, 'patch': {}}

    def test_more_than_32_definitions_assemble_without_relaxing_public_limit(self):
        profile = fixture_profile(36)
        candidate, changes, _ = _assemble(self.config, self.state, profile)
        self.assertEqual(len(changes), 39)
        self.assertEqual(self.state, {'version': 0, 'patch': {}})
        self.assertEqual(candidate['version'], 0)
        with self.assertRaises(OntologyEditError):
            merge_patch(self.config, self.state, {}, profile['operations'])
        schema = effective_schema(self.config, candidate)
        custom = next(item for item in schema['entity_types'] if item['key'] == 'x_personal_fixture')
        self.assertEqual(custom['semantic']['iri'], 'https://example.org/ontology/x_personal_fixture')
        self.assertEqual(custom['iri'], self.config.urn_prefix + 'ontology:entity_types:x_personal_fixture')
        self.assertEqual(schema['profile_digest'], preset_digest(candidate['presets']))
        self.assertEqual(candidate['presets']['personal']['provenance'], profile['provenance'])

    def test_last_invalid_definition_does_not_mutate_original_state(self):
        profile = fixture_profile(36)
        profile['operations'][-1]['definition']['range'] = ['unsupported-object']
        with self.assertRaises(OntologyEditError):
            _assemble(self.config, self.state, profile)
        self.assertEqual(self.state, {'version': 0, 'patch': {}})

    def test_collision_does_not_adopt_or_replace_user_definition(self):
        existing, _ = merge_patch(self.config, self.state, {}, fixture_profile()['operations'][:1])
        original = deepcopy(existing)
        with self.assertRaisesRegex(ValueError, 'conflicts.*x_personal_fixture'):
            _assemble(self.config, existing, fixture_profile())
        self.assertEqual(existing, original)

    def test_repeated_install_preserves_customizations_deactivation_and_deletion(self):
        state, _, _ = _assemble(self.config, self.state, fixture_profile())
        state, _ = merge_patch(self.config, state, {'entity_types': {'x_personal_fixture': {'label': '我的名称'}}})
        state, _ = merge_patch(self.config, state, {}, [
            {'action': 'deactivate', 'kind': 'entity_types', 'key': 'x_personal_fixture'},
            {'action': 'delete', 'kind': 'properties', 'key': 'x_personal_fixture_score'}])
        repeated, changes, operations = _assemble(self.config, state, fixture_profile())
        self.assertEqual(repeated, state)
        self.assertEqual(changes, [])
        self.assertEqual(operations, [])
        newer = fixture_profile(1)
        newer.update(version='1.1.0', digest='b' * 64)
        upgraded, _, _ = _assemble(self.config, state, newer)
        self.assertNotIn('x_personal_fixture_score', upgraded['patch']['properties'])
        self.assertEqual(upgraded['patch']['entity_types']['x_personal_fixture']['label'], '我的名称')
        self.assertFalse(upgraded['patch']['entity_types']['x_personal_fixture']['active'])

    def test_semantic_floor_allows_display_changes_and_tightening_but_not_redefinition(self):
        state, _, _ = _assemble(self.config, self.state, fixture_profile())
        updated, _ = merge_patch(self.config, state, {'relations': {'X_PERSONAL_FIXTURE': {'label': '本地显示名'}}})
        self.assertIn('presets', updated)
        narrowed, _ = merge_patch(self.config, state, {'properties': {'x_personal_fixture_score': {'range': ['integer']}}})
        self.assertEqual(narrowed['patch']['properties']['x_personal_fixture_score']['range'], ['integer'])
        for change in ({'relations': {'X_PERSONAL_FIXTURE': {'domain': ['any']}}},
                       {'properties': {'x_personal_fixture_score': {'range': ['string']}}},
                       {'properties': {'x_personal_fixture_score': {'domain': ['person']}}}):
            with self.subTest(change=change), self.assertRaises(OntologyEditError) as failure:
                merge_patch(self.config, state, change)
            self.assertEqual(failure.exception.code, 'ontology_semantic_conflict')

    def test_metadata_roundtrip_works_without_loading_installed_bundle(self):
        state, _, _ = _assemble(self.config, self.state, fixture_profile())
        rows = [{'version': 1, 'patch_json': json.dumps(state['patch']), 'presets_json': json.dumps(state['presets'])}]
        loaded = state_from_rows(rows)
        self.assertEqual(loaded['presets'], state['presets'])
        with patch('kg.ontology_presets._profile', side_effect=AssertionError('No bundle reads')):
            self.assertEqual(validate_state(self.config, loaded)['profile_digest'], preset_digest(state['presets']))
        self.assertEqual(state_from_rows([{'version': 0, 'patch_json': '{}'}]), self.state)

    def test_malformed_metadata_and_same_version_mutation_fail_closed(self):
        state, _, _ = _assemble(self.config, self.state, fixture_profile())
        mutated = fixture_profile()
        mutated['digest'] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'different content digest'):
            _assemble(self.config, state, mutated)
        for bad in ([], {'personal': {}}, {'personal': {**state['presets']['personal'], 'digest': 'not-a-hash'}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_presets(bad)
        corrupted = deepcopy(state)
        corrupted['patch']['relations']['X_PERSONAL_FIXTURE']['domain'] = ['any']
        with self.assertRaises(OntologyEditError):
            validate_state(self.config, corrupted)

    def test_existing_user_narrowing_is_not_frozen_as_a_new_standard_constraint(self):
        state, _ = merge_patch(self.config, self.state, {'properties': {'hermes_note': {'domain': ['concept']}}})
        profile = fixture_profile()
        profile['bindings']['properties']['hermes_note'] = {
            'iri': 'https://schema.org/description', 'mapping': 'projection',
            'source': 'synthetic', 'source_version': '1'}
        installed, _, _ = _assemble(self.config, state, profile)
        relaxed, _ = merge_patch(self.config, installed, {'properties': {'hermes_note': {'domain': ['any']}}})
        self.assertEqual(relaxed['patch']['properties']['hermes_note']['domain'], ['any'])

    def test_backup_metadata_rejects_unrenderable_bindings_and_malformed_sources(self):
        state, _, _ = _assemble(self.config, self.state, fixture_profile())
        metadata = state['presets']
        for field, value in (('note', {}), ('note', ['text']), ('note', 'x' * 4001),
                             ('mapping', {}), ('direction', []), ('source_version', 1),
                             ('unexpected', 'unsupported')):
            corrupted = deepcopy(metadata)
            corrupted['personal']['bindings']['entity_types']['x_personal_fixture'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                state_from_rows([{'version': 1, 'patch_json': '{}', 'presets_json': json.dumps(corrupted)}])
        for field, value in (('license', {}), ('sha256', 'wrong'), ('version', []),
                             ('iri', 'javascript:alert(1)'), ('url', 'file:///private'),
                             ('modified', 'false'), ('unexpected', 'unsupported')):
            corrupted = deepcopy(metadata)
            corrupted['personal']['sources'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_presets(corrupted)
        corrupted = deepcopy(metadata)
        corrupted['personal']['bindings']['entity_types']['x_personal_fixture']['source_version'] = 'missing'
        with self.assertRaises(ValueError):
            validate_presets(corrupted)

    def test_older_release_cannot_silently_downgrade_installed_metadata(self):
        installed_profile = fixture_profile()
        installed_profile.update(version='1.10.0', digest='b' * 64)
        state, _, _ = _assemble(self.config, self.state, installed_profile)
        before = deepcopy(state)
        older = fixture_profile()
        older.update(version='1.9.9', digest='c' * 64)
        with self.assertRaisesRegex(ValueError, 'older ontology preset'):
            _assemble(self.config, state, older)
        self.assertEqual(state, before)

    def test_dry_run_selects_read_transaction_and_never_commits(self):
        config = self.config
        calls = []
        store = SimpleNamespace(config=config, driver=SimpleNamespace(session=lambda **_: nullcontext(None)),
                                _execute=lambda _session, mode, action: (calls.append(mode), action(None))[1])
        with patch('kg.ontology_presets._profile', return_value=fixture_profile()), \
             patch('kg.ontology_presets._install_in_transaction', return_value={'ok': True, '_candidate': {}}), \
             patch('kg.ontology_presets._commit') as commit:
            result = install_preset(store, dry_run=True)
        self.assertEqual(calls, ['read'])
        self.assertTrue(result['dry_run'])
        self.assertNotIn('_candidate', result)
        commit.assert_not_called()


@unittest.skipUnless(os.environ.get('KG_TEST_BOLT_URL'), 'isolated Neo4j URL not configured')
class PresetNeo4jTests(unittest.TestCase):
    def setUp(self):
        from kg.store import KGStore
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prefix = 'urn:presettest' + uuid.uuid4().hex + ':kg:'
        self.config = KGConfig(home=Path(self.tmp.name) / 'profile', neo4j_url=os.environ['KG_TEST_BOLT_URL'],
                               password=os.environ['KG_TEST_PASSWORD'], urn_prefix=self.prefix, default_shared=False)
        self.store = KGStore(self.config)
        self.addCleanup(self.store.close)
        self.store.ensure_schema()
        self.addCleanup(self.clear)

    def clear(self):
        with self.store.driver.session(database=self.config.database) as session:
            session.run('MATCH (n) WHERE n.uri STARTS WITH $prefix OR n.namespace=$prefix DETACH DELETE n',
                        {'prefix': self.prefix}).consume()

    def sql(self, query, **params):
        with self.store.driver.session(database=self.config.database) as session:
            return [dict(row) for row in session.run(query, params)]

    def test_atomic_real_install_read_preview_and_single_revision(self):
        profile = fixture_profile(36)
        with patch('kg.ontology_presets._profile', return_value=profile):
            preview = install_preset(self.store, dry_run=True)
            self.assertEqual(preview['version'], 0)
            self.assertEqual(self.sql('MATCH (n) WHERE n.namespace=$prefix RETURN count(n) AS count', prefix=self.prefix)[0]['count'], 0)
            result = install_preset(self.store)
            self.assertEqual(result['version'], 1)
            self.assertTrue(install_preset(self.store)['idempotent'])
        self.assertEqual(len(self.store.ontology_overlay()['patch']['properties']), 37)
        self.assertEqual(self.sql('MATCH (n:KGOntologyRevision {namespace:$prefix}) RETURN count(n) AS count', prefix=self.prefix)[0]['count'], 1)
        self.assertEqual(self.sql('MATCH (n:KGEntity) WHERE n.uri STARTS WITH $prefix RETURN count(n) AS count', prefix=self.prefix)[0]['count'], 0)

    def test_initialization_skips_old_namespace_and_concurrent_fresh_claims_install_once(self):
        profile = fixture_profile()
        with patch('kg.ontology_presets._profile', return_value=profile) as load:
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: initialize_personal_preset(self.store), range(2)))
            self.assertEqual(load.call_count, 1)
        self.assertEqual(sum(result['installed'] for result in results), 1)
        self.assertEqual(self.store.ontology_overlay()['version'], 1)
        self.clear()
        self.sql('CREATE (:Person {uri:$uri,foaf_name:"Synthetic existing owner"})', uri=self.prefix + 'person-old')
        with patch('kg.ontology_presets._profile', side_effect=AssertionError('Old space must not load optional preset')) as load:
            self.assertFalse(initialize_personal_preset(self.store)['installed'])
            load.assert_not_called()
        self.assertEqual(self.store.ontology_overlay()['version'], 0)

    def test_old_workspace_and_legacy_receipt_without_facts_are_not_new(self):
        for query, params in (
            ('CREATE (:KGWorkspace {namespace:$prefix})', {'prefix': self.prefix}),
            ('CREATE (:KGSyncReceipt {snapshot_id:$id,entity_uris:[$uri]})', {'id': 'synthetic-' + uuid.uuid4().hex, 'uri': self.prefix + 'concept-old'}),
        ):
            self.clear()
            self.sql(query, **params)
            with patch('kg.ontology_presets._profile', side_effect=AssertionError('Old space must not compile preset')) as load:
                self.assertFalse(initialize_personal_preset(self.store)['installed'])
                load.assert_not_called()
            self.assertEqual(self.store.ontology_overlay()['version'], 0)
            # Legacy receipts have no namespace, so cleanup them by their IDs.
            if 'id' in params:
                self.sql('MATCH (n:KGSyncReceipt {snapshot_id:$id}) DELETE n', id=params['id'])

    def test_install_validation_failure_rolls_back_workspace_and_every_definition(self):
        profile = fixture_profile(36)
        profile['operations'][-1]['definition']['range'] = ['unsupported']
        with patch('kg.ontology_presets._profile', return_value=profile), self.assertRaises(OntologyEditError):
            initialize_personal_preset(self.store)
        self.assertEqual(self.store.ontology_overlay()['version'], 0)
        self.assertEqual(self.sql('MATCH (n) WHERE n.namespace=$prefix RETURN count(n) AS count', prefix=self.prefix)[0]['count'], 0)

    def test_upgrade_does_not_restore_user_disabled_dependencies(self):
        with patch('kg.ontology_presets._profile', return_value=fixture_profile()):
            install_preset(self.store)
        editor = OntologyEditor(self.config, self.store)
        operations = [{'action': 'deactivate', 'kind': 'entity_types', 'key': 'x_personal_fixture'}]
        request = {'expected_namespace': self.prefix, 'expected_version': 1, 'operations': operations}
        risk = editor.validate(**request)['risk']
        editor.publish(**request, operation_id='fixture-disable-' + uuid.uuid4().hex,
                       confirmation=risk.get('confirmation_phrase'), confirmation_token=risk.get('confirmation_token'))
        before = self.store.ontology_overlay()
        self.assertIn('presets', before)
        newer = fixture_profile(1)
        newer.update(version='1.1.0', digest='b' * 64)
        with patch('kg.ontology_presets._profile', return_value=newer), self.assertRaises(OntologyEditError) as failure:
            install_preset(self.store)
        self.assertEqual(failure.exception.code, 'ontology_data_conflict')
        self.assertEqual(self.store.ontology_overlay(), before)

    def test_preset_metadata_survives_backup_and_read_after_restore(self):
        from kg.maintenance import backup, restore
        profile = fixture_profile()
        with patch('kg.ontology_presets._profile', return_value=profile):
            install_preset(self.store)
        before = self.store.ontology_overlay()
        target = Path(self.tmp.name) / 'backup.zip'
        backup(self.store, target)
        self.clear()
        restore(self.store, target)
        self.assertEqual(self.store.ontology_overlay(), before)
        self.assertEqual(effective_schema(self.config, before)['profile_digest'], preset_digest(before['presets']))

    def test_real_packaged_profile_installs_and_writes_personal_scenarios(self):
        from kg.ontology_profile import load_personal_profile
        from kg.queries import query_entities
        profile = load_personal_profile()
        result = initialize_personal_preset(self.store)
        self.assertTrue(result['installed'])
        self.assertEqual(len(result['changes']), len(profile['operations']))
        schema = self.store.ontology_schema()
        person = next(entry for entry in schema['entity_types'] if entry['key'] == 'person')
        topic = next(entry for entry in schema['entity_types'] if entry['key'] == 'x_personal_topic')
        legacy = next(entry for entry in schema['entity_types'] if entry['key'] == 'concept')
        self.assertEqual(person['semantic']['iri'], 'https://schema.org/Person')
        self.assertEqual(topic['semantic']['iri'], 'http://www.w3.org/2004/02/skos/core#Concept')
        self.assertNotEqual(legacy.get('semantic', {}).get('iri'), topic['semantic']['iri'])
        owner = self.prefix + 'person-owner'
        task = self.prefix + 'x_personal_task-renewal'
        health = self.prefix + 'x_personal_health_record-temperature'
        finance = self.prefix + 'x_personal_finance_record-fee'
        self.store.write_batch([
            {'type': 'person', 'uri': owner, 'name': 'Synthetic owner'},
            {'type': 'x_personal_task', 'uri': task, 'name': 'Synthetic renewal task',
             'props': {'x_personal_state': 'todo', 'x_personal_due_date': '2026-11-01'}},
            {'type': 'x_personal_health_record', 'uri': health, 'name': 'Synthetic temperature record',
             'props': {'x_personal_value': 36.5, 'x_personal_unit': 'C'}},
            {'type': 'x_personal_finance_record', 'uri': finance, 'name': 'Synthetic fee record',
             'props': {'x_personal_amount': '12.30', 'x_personal_currency': 'CNY'}},
        ], [{'from': owner, 'to': task, 'type': 'X_PERSONAL_WORKS_ON'},
            {'from': owner, 'to': health, 'type': 'X_PERSONAL_HAS_RECORD'},
            {'from': owner, 'to': finance, 'type': 'X_PERSONAL_HAS_RECORD'}])
        found = query_entities('Synthetic renewal task', 'x_personal_task', config=self.config, store=self.store)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['uri'], task)
        self.assertTrue(install_preset(self.store)['idempotent'])


if __name__ == '__main__':
    unittest.main()
