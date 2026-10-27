"""Synthetic fixtures only: explicit type changes preserve resolvable history."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from kg.config import KGConfig
from kg.lifecycle import VersionConflict
from kg.ontology_editing import effective_schema
from kg.ontology_presets import _assemble, install_preset
from kg.ontology_profile import load_personal_profile
from kg.reclassification import (_validate_plan, classification_snapshot, digest,
                                 edge_key, reclassify_memories)


def plan_for(config, snapshot, entries, overrides=None):
    return {'format': 1, 'operation_id': 'reclassify-fixture-' + uuid.uuid4().hex,
            'namespace_sha256': hashlib.sha256(config.urn_prefix.encode()).hexdigest(),
            'ontology_version': snapshot['schema']['version'],
            'profile_digest': snapshot['schema']['profile_digest'],
            'snapshot_sha256': snapshot['snapshot_sha256'],
            'entries': entries, 'relation_overrides': overrides or []}


def entry(uri, kind, version=0):
    return {'uri': uri, 'target_type': kind, 'expected_version': version,
            'reason': 'The saved description identifies a reference document.',
            'support': [{'property': 'hermes_description', 'quote': 'reference document'}]}


class ClassificationPlanTests(unittest.TestCase):
    def setUp(self):
        self.config = KGConfig(urn_prefix='urn:classification-fixture:kg:')
        state, _, _ = _assemble(self.config, {'version': 0, 'patch': {}}, load_personal_profile())
        self.schema = effective_schema(self.config, state)
        self.uri = self.config.urn_prefix + 'concept-handbook'
        self.snapshot = {'schema': self.schema, 'snapshot_sha256': 'a' * 64, 'edges': [],
                         'nodes': [{'labels': ['KGEntity', 'Concept'], 'properties': {
                             'uri': self.uri, 'hermes_name': 'Synthetic handbook',
                             'hermes_description': 'A reference document for a synthetic project.',
                             'x_legacy_data': 'retained on original identity'}}]}
        self.store = type('Store', (), {'config': self.config})()
        self.plan = plan_for(self.config, self.snapshot, [entry(self.uri, 'x_personal_resource')])

    def test_compatible_properties_and_name_survive_unknown_data_stays_in_history(self):
        selected, edges = _validate_plan(self.store, self.plan, self.snapshot)
        target = selected[self.uri]
        self.assertEqual(target['properties']['hermes_name'], 'Synthetic handbook')
        self.assertEqual(target['history_only_properties'], ['x_legacy_data'])
        self.assertTrue(target['uri'].startswith(self.config.urn_prefix + 'x_personal_resource-'))
        self.assertEqual(edges, [])

    def test_snapshot_ontology_version_and_namespace_conflicts_fail(self):
        for field, value in [('snapshot_sha256', 'changed'), ('ontology_version', 20),
                             ('profile_digest', 'changed'), ('namespace_sha256', 'changed')]:
            plan = {**self.plan, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                _validate_plan(self.store, plan, self.snapshot)

    def test_ungrounded_type_change_missing_source_and_disabled_type_fail(self):
        for change in [{'support': []}, {'support': [{'property': 'hermes_description', 'quote': 'invented'}]},
                       {'expected_version': 5}, {'uri': self.config.urn_prefix + 'concept-absent'},
                       {'target_type': 'concept'}, {'target_type': 'x_undefined'}]:
            plan = {**self.plan, 'entries': [{**self.plan['entries'][0], **change}]}
            with self.subTest(change=change), self.assertRaises(ValueError):
                _validate_plan(self.store, plan, self.snapshot)
        snapshot = deepcopy(self.snapshot)
        next(t for t in snapshot['schema']['entity_types'] if t['key'] == 'x_personal_resource')['active'] = False
        with self.assertRaisesRegex(ValueError, 'active ontology type'):
            _validate_plan(self.store, self.plan, snapshot)

    def test_task_time_is_not_inferred_as_a_deadline(self):
        from neo4j.time import DateTime
        node = self.snapshot['nodes'][0]
        node['labels'] = ['KGEntity', 'Event']
        node['properties'].update(hermes_eventTime=DateTime(2026, 10, 27, 9),
                                  hermes_eventType='task', hermes_eventStatus='planned')
        plan = {**self.plan, 'entries': [entry(self.uri, 'x_personal_task')]}
        target = _validate_plan(self.store, plan, self.snapshot)[0][self.uri]
        self.assertNotIn('hermes_eventTime', target['properties'])
        self.assertNotIn('x_personal_due_date', target['properties'])
        self.assertIn('hermes_eventTime', target['history_only_properties'])


@unittest.skipUnless(os.environ.get('KG_TEST_BOLT_URL'), 'isolated Neo4j URL not configured')
class ReclassificationNeo4jTests(unittest.TestCase):
    def setUp(self):
        from kg.store import KGStore
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = KGConfig(home=Path(self.tmp.name) / 'profile',
                               neo4j_url=os.environ['KG_TEST_BOLT_URL'], password=os.environ['KG_TEST_PASSWORD'],
                               urn_prefix='urn:classificationtest' + uuid.uuid4().hex + ':kg:', default_shared=False)
        self.store = KGStore(self.config)
        self.addCleanup(self.store.close)
        self.addCleanup(self.clear)
        self.store.ensure_schema()
        install_preset(self.store)
        self.uri = self.config.urn_prefix + 'concept-handbook'
        self.owner = self.config.urn_prefix + 'person-owner'
        self.store.write_batch([
            {'type': 'person', 'uri': self.owner, 'name': 'Synthetic owner'},
            {'type': 'concept', 'uri': self.uri, 'name': 'Synthetic handbook',
             'props': {'description': 'A reference document for a synthetic project.'}}], [])

    def sql(self, query, **params):
        with self.store.driver.session(database=self.config.database) as session:
            return [dict(row) for row in session.run(query, params)]

    def clear(self):
        self.sql('MATCH (n) WHERE n.uri STARTS WITH $prefix OR n.namespace=$prefix DETACH DELETE n',
                 prefix=self.config.urn_prefix)

    def plan(self, kind='x_personal_resource'):
        snapshot = classification_snapshot(self.store)
        node = next(node for node in snapshot['nodes'] if node['properties']['uri'] == self.uri)
        return plan_for(self.config, snapshot, [entry(self.uri, kind, node['properties']['hermes_revision'])])

    def test_dry_run_commit_alias_lookup_parallel_edges_audit_and_idempotency(self):
        for _ in range(2):
            self.sql('MATCH (a {uri:$owner}),(b {uri:$uri}) CREATE (a)-[:RELATED_TO {hermes_note:"synthetic"}]->(b)',
                     owner=self.owner, uri=self.uri)
        plan = self.plan()
        before = classification_snapshot(self.store)['snapshot_sha256']
        preview = reclassify_memories(self.store, plan)
        self.assertEqual(classification_snapshot(self.store)['snapshot_sha256'], before)
        self.assertEqual(preview['relationships_copied'], 2)
        result = reclassify_memories(self.store, plan, dry_run=False)
        target = result['mappings'][0]['to']
        self.assertEqual(self.sql('MATCH (n {uri:$uri}) RETURN n.hermes_canonicalUri AS target,n:KGQuarantined AS hidden', uri=self.uri),
                         [{'target': target, 'hidden': True}])
        self.assertEqual(self.sql('MATCH (a {uri:$owner})-[r:RELATED_TO]->(b) RETURN count(r) AS count', owner=self.owner)[0]['count'], 4)
        from kg.queries import query_entities
        self.assertEqual(query_entities(self.uri, config=self.config, store=self.store)[0]['uri'], target)
        self.assertTrue(reclassify_memories(self.store, plan, dry_run=False)['idempotent'])
        self.assertEqual(len(classification_snapshot(self.store)['nodes']), 2)
        self.assertGreaterEqual(self.sql('MATCH (r:KGMemoryRevision {namespace:$prefix,action:"reclassify"}) RETURN count(r) AS count', prefix=self.config.urn_prefix)[0]['count'], 3)
        altered = {**plan, 'entries': [{**plan['entries'][0], 'reason': 'different operation'}]}
        with self.assertRaisesRegex(ValueError, 'different plan'):
            reclassify_memories(self.store, altered, dry_run=False)

    def test_mid_transaction_failure_rolls_back_all_nodes_edges_and_receipt(self):
        plan = self.plan()
        before = classification_snapshot(self.store)['snapshot_sha256']
        with patch.object(self.store, '_finish_receipt', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
                reclassify_memories(self.store, plan, dry_run=False)
        self.assertEqual(classification_snapshot(self.store)['snapshot_sha256'], before)
        self.assertEqual(self.sql('MATCH (r:KGSyncReceipt {snapshot_id:$id}) RETURN count(r) AS count', id='operation-' + plan['operation_id'])[0]['count'], 0)

    def test_backup_restore_preserves_new_identity_alias_and_idempotent_receipt(self):
        from kg.maintenance import backup, restore
        plan = self.plan()
        result = reclassify_memories(self.store, plan, dry_run=False)
        before = classification_snapshot(self.store)['snapshot_sha256']
        archive = Path(self.tmp.name) / 'reclassified.zip'
        backup(self.store, archive)
        self.clear()
        restore(self.store, archive)
        self.assertEqual(classification_snapshot(self.store)['snapshot_sha256'], before)
        self.assertTrue(reclassify_memories(self.store, plan, dry_run=False)['idempotent'])
        target = self.sql('MATCH (n {uri:$uri}) RETURN n.hermes_canonicalUri AS target', uri=self.uri)[0]['target']
        self.assertEqual(target, result['mappings'][0]['to'])

    def test_existing_confirmed_aliases_stay_one_hop_and_are_snapshot_bound(self):
        alias = self.config.urn_prefix + 'concept-older-handbook'
        self.sql('CREATE (:KGEntity:Concept:KGQuarantined {uri:$alias,hermes_name:"Synthetic former name",hermes_aliasConfirmed:true,hermes_canonicalUri:$uri})',
                 alias=alias, uri=self.uri)
        plan = self.plan()
        self.sql('MATCH (n {uri:$alias}) SET n.hermes_note="concurrent alias edit"', alias=alias)
        with self.assertRaises(VersionConflict):
            reclassify_memories(self.store, plan, dry_run=False)
        plan = self.plan()
        result = reclassify_memories(self.store, plan, dry_run=False)
        self.assertEqual(result['aliases_redirected'], 1)
        from kg.queries import query_entities
        self.assertEqual(query_entities(alias, config=self.config, store=self.store)[0]['uri'], result['mappings'][0]['to'])
        self.assertEqual(self.sql('MATCH (n:KGMemoryRevision {entity_uri:$uri,action:"reclassify"}) RETURN count(n) AS count', uri=alias)[0]['count'], 1)

    def test_concurrent_edit_invalidates_plan_and_target_collision_never_merges(self):
        plan = self.plan()
        self.sql('MATCH (n {uri:$uri}) SET n.hermes_note="a concurrent edit"', uri=self.uri)
        with self.assertRaises(VersionConflict):
            reclassify_memories(self.store, plan, dry_run=False)
        plan = self.plan()
        preview = reclassify_memories(self.store, plan)
        self.sql('CREATE (:KGQuarantined {uri:$uri,hermes_name:"unrelated history"})', uri=preview['mappings'][0]['to'])
        with self.assertRaisesRegex(ValueError, 'already exists'):
            reclassify_memories(self.store, plan, dry_run=False)

    def test_incompatible_participant_requires_evidenced_explicit_mapping(self):
        self.sql('MATCH (n {uri:$uri}) REMOVE n:Concept SET n:Event,n.hermes_eventType="task",n.hermes_timePrecision="unknown",n.hermes_eventStatus="occurred"', uri=self.uri)
        self.sql('MATCH (a {uri:$owner}),(b {uri:$uri}) CREATE (a)-[:HERMES_participant]->(b)', owner=self.owner, uri=self.uri)
        snapshot = classification_snapshot(self.store)
        plan = self.plan('x_personal_task')
        with self.assertRaisesRegex(ValueError, 'incompatible'):
            reclassify_memories(self.store, plan)
        relation = snapshot['edges'][0]
        plan['relation_overrides'] = [{'edge_sha256': edge_key(relation), 'new_type': 'X_PERSONAL_WORKS_ON',
                                     'reason': 'Synthetic explicit work-on decision',
                                     'support': [{'uri': self.uri, 'property': 'hermes_description', 'quote': 'reference document'}]}]
        self.assertEqual(reclassify_memories(self.store, plan)['relationships_copied'], 1)
        plan['relation_overrides'][0]['support'][0]['quote'] = 'invented'
        with self.assertRaisesRegex(ValueError, 'support'):
            reclassify_memories(self.store, plan)


if __name__ == '__main__':
    unittest.main()
