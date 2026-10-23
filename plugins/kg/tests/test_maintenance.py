"""Portable backup round-trip against a disposable, namespace-isolated graph."""
from __future__ import annotations
import os
import hashlib
import json
import sqlite3
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kg.config import KGConfig


class BackupValueTests(unittest.TestCase):
    def test_nested_temporal_values_preserve_their_driver_type_and_precision(self):
        from neo4j.time import DateTime, Date, Time, Duration
        from kg.maintenance import encode, decode
        times = [DateTime(2026, 10, 23, 11, 22, 33, 123456789),
                 Date(2026, 10, 23), Time(11, 22, 33, 123456789),
                 Duration(months=14, days=3, seconds=65, nanoseconds=123456789)]
        original = {'props': {'times': times, 'duration': times[-1]}, 'empty': []}
        encoded = encode(original)
        self.assertEqual(encoded['props']['duration']['__kg_type__'], 'Duration')
        restored = decode(json.loads(json.dumps(encoded)))
        for expected, actual in zip(times, restored['props']['times']):
            self.assertIs(type(actual), type(expected))
            self.assertEqual(actual, expected)
        self.assertIs(type(restored['props']['duration']), Duration)
        self.assertEqual(restored, original)

    def test_known_legacy_relationship_spellings_are_preserved_and_query_text_is_rejected(self):
        from kg.maintenance import _validate_graph
        prefix = 'urn:portable:kg:'
        store = SimpleNamespace(config=SimpleNamespace(urn_prefix=prefix))
        graph = {'nodes': [
            {'id': 'person', 'labels': ['Person'], 'props': {'uri': prefix + 'person-owner'}},
            {'id': 'event', 'labels': ['Event'], 'props': {'uri': prefix + 'event-legacy'}},
        ], 'relations': []}
        for spelling in ('HERMES_PARTICIPANT', 'HERMES_participant', 'PARTICIPATED_IN',
                         'HERMES_hasPreference', 'owl_sameAs', 'OWL:SAMEAS'):
            with self.subTest(spelling=spelling):
                graph['relations'] = [{'source': 'person', 'target': 'event',
                                       'type': spelling, 'props': {}}]
                _validate_graph(store, graph)
                self.assertEqual(graph['relations'][0]['type'], spelling)
        for spelling in ('UNKNOWN_TYPE', ':HERMES_participant', 'RELATED_TO`]->() //'):
            with self.subTest(spelling=spelling):
                graph['relations'][0]['type'] = spelling
                with self.assertRaisesRegex(ValueError, 'Unsupported relationship'):
                    _validate_graph(store, graph)


@unittest.skipUnless(os.environ.get('KG_TEST_BOLT_URL'),'isolated Neo4j test URL not configured')
class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        from kg.store import KGStore
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)/'original';self.home.mkdir()
        self.prefix='urn:test-'+uuid.uuid4().hex+':kg:'
        self.config=KGConfig(home=self.home,neo4j_url=os.environ['KG_TEST_BOLT_URL'],password=os.environ['KG_TEST_PASSWORD'],urn_prefix=self.prefix,user_person_urn=self.prefix+'person-owner',default_shared=False)
        self.store=KGStore(self.config);self.addCleanup(self.store.close)
        self.store.ensure_schema()
        self.addCleanup(self.clear)

    def clear(self):
        with self.store.driver.session(database=self.config.database) as session:
            session.run('''MATCH (n) WHERE n.uri STARTS WITH $prefix OR n.namespace=$prefix
                OR (n:KGSyncReceipt AND any(uri IN coalesce(n.entity_uris,[]) WHERE uri STARTS WITH $prefix))
                DETACH DELETE n''',{'prefix':self.prefix}).consume()

    def destination(self, name='restored'):
        from dataclasses import replace
        from kg.store import KGStore
        home = Path(self.tmp.name) / name
        home.mkdir()
        store = KGStore(replace(self.config, home=home))
        self.addCleanup(store.close)
        return store

    def graph_archive(self, graph, name='crafted.zip'):
        path = Path(self.tmp.name) / name
        data = json.dumps(graph).encode()
        manifest = {'format': 1, 'namespace': self.prefix,
                    'files': {'graph.json': hashlib.sha256(data).hexdigest()}}
        with zipfile.ZipFile(path, 'x') as archive:
            archive.writestr('manifest.json', json.dumps(manifest))
            archive.writestr('graph.json', data)
        return path

    def test_graph_revision_time_and_sqlite_round_trip(self):
        from kg.maintenance import backup,restore
        from kg.lifecycle import MemoryService
        from kg.console_state import ConsoleState
        from kg.store import KGStore
        self.store.write_batch([{'type':'Event','id':'test-meeting','name':'Synthetic meeting','props':{'event_time':'2026-10-23T10:00:00+08:00'}}],[])
        uri=self.prefix+'event-test-meeting'
        service=MemoryService(self.config,self.store)
        service.correct(uri,{'name':'Corrected meeting'},expected_version=1,operation_id=self.prefix+'fixture-correction')
        ConsoleState(self.home).set('fixture','retained')
        path=Path(self.tmp.name)/'backup.zip'
        self.assertTrue(backup(self.store,path)['ok'])
        with self.assertRaisesRegex(ValueError,'empty destination'):
            restore(self.store,path)
        self.clear()
        restored_home=Path(self.tmp.name)/'restored';restored_home.mkdir()
        from dataclasses import replace
        destination=replace(self.config,home=restored_home)
        with KGStore(destination) as target:
            result=restore(target,path)
            self.assertTrue(result['sync_paused'])
            card=MemoryService(destination,target).detail(uri)
            self.assertEqual(card['name'],'Corrected meeting')
            self.assertEqual(card['version'],2)
            self.assertEqual(card['props']['hermes_eventTime'].hour,10)
            self.assertEqual(len(MemoryService(destination,target).revisions(uri)),2)
        self.assertEqual(ConsoleState(restored_home).get('fixture'),'retained')
        self.assertTrue(ConsoleState(restored_home).get('paused'))

    def test_legacy_audit_nodes_relation_spellings_and_temporal_arrays_round_trip(self):
        from neo4j.time import Date, Duration
        from kg.maintenance import backup, restore
        elapsed = Duration(months=1, days=2, seconds=3, nanoseconds=4)
        dates = [Date(2026, 10, 22), Date(2026, 10, 23)]
        with self.store.driver.session(database=self.config.database) as session:
            session.run("""CREATE (p:Person {uri:$prefix+'person-owner',foaf_name:'Fixture owner'}),
                (e:Event {uri:$prefix+'event-legacy',hermes_name:'Legacy fixture',elapsed:$elapsed,dates:$dates}),
                (a:KGLegacyAlias {uri:$prefix+'person-alias',hermes_canonicalUri:$prefix+'person-owner'}),
                (u:KGLegacyUnresolved {uri:$prefix+'legacy-unresolved',hermes_note:'Review evidence retained'}),
                (q:KGQuarantined {uri:$prefix+'concept-held',hermes_name:'Held fixture'}),
                (:KGSyncReceipt {snapshot_id:$prefix+'legacy-fixture',entity_uris:[$prefix+'person-owner']}),
                (p)-[:HERMES_PARTICIPANT {hermes_note:'Original spelling'}]->(e),
                (p)-[:HERMES_participant]->(e),
                (a)-[:OWL_SAMEAS]->(p),
                (u)-[:RELATED_TO]->(q)""",
                {'prefix': self.prefix, 'elapsed': elapsed, 'dates': dates}).consume()
        path = Path(self.tmp.name) / 'legacy-audit.zip'
        self.assertEqual(backup(self.store, path)['entities_and_metadata'], 6)
        # URI-only legacy audit nodes must also make a namespace nonempty.
        self.clear()
        with self.store.driver.session(database=self.config.database) as session:
            session.run('CREATE (:KGLegacyUnresolved {uri:$uri})',
                        {'uri': self.prefix + 'legacy-existing'}).consume()
        target = self.destination()
        with self.assertRaisesRegex(ValueError, 'empty destination namespace'):
            restore(target, path)
        self.clear()
        result = restore(target, path)
        self.assertEqual((result['nodes'], result['relations']), (6, 4))
        with self.store.driver.session(database=self.config.database) as session:
            node = session.run('MATCH (e:Event {uri:$uri}) RETURN e.elapsed AS elapsed,e.dates AS dates',
                               {'uri': self.prefix + 'event-legacy'}).single()
            self.assertIs(type(node['elapsed']), Duration)
            self.assertEqual(node['elapsed'], elapsed)
            self.assertEqual(node['dates'], dates)
            spellings = session.run('MATCH (a)-[r]->(b) WHERE a.uri STARTS WITH $prefix RETURN collect(type(r)) AS types',
                                    {'prefix': self.prefix}).single()['types']
            self.assertCountEqual(spellings, ['HERMES_PARTICIPANT', 'HERMES_participant', 'OWL_SAMEAS', 'RELATED_TO'])

    def test_cross_namespace_entities_metadata_and_duplicate_ids_are_rejected_before_writing(self):
        from kg.maintenance import restore, restore_in_progress
        foreign = 'urn:foreign:kg:concept-secret'
        cases = [
            [{'id': 'x', 'labels': ['KGEntity', 'Concept'], 'props': {'uri': foreign, 'hermes_name': 'Foreign'}}],
            [{'id': 'x', 'labels': ['KGMemoryRevision'], 'props': {'namespace': 'urn:foreign:kg:', 'entity_uri': self.prefix + 'concept-x'}}],
            [{'id': 'x', 'labels': ['KGReviewBatch'], 'props': {'namespace': 'urn:foreign:kg:'}}],
            [{'id': 'x', 'labels': ['KGSyncReceipt'], 'props': {'namespace': 'urn:foreign:kg:', 'entity_uris': [self.prefix + 'concept-x']}}],
            [{'id': 'x', 'labels': ['Concept'], 'props': {'uri': self.prefix + 'concept-x'}},
             {'id': 'x', 'labels': ['Concept'], 'props': {'uri': self.prefix + 'concept-y'}}],
        ]
        for index, nodes in enumerate(cases):
            with self.subTest(index=index):
                path = self.graph_archive({'nodes': nodes, 'relations': []}, 'crafted-' + str(index) + '.zip')
                with self.assertRaises(ValueError):
                    restore(self.store, path)
                self.assertFalse(restore_in_progress(self.home))
        self.assertEqual(self.store.run_read('MATCH (n) WHERE n.uri=$uri RETURN count(n) AS count', {'uri': foreign})[0]['count'], 0)

    def test_invalid_sqlite_is_rejected_before_starting_restore(self):
        from kg.maintenance import restore, restore_in_progress
        graph = json.dumps({'nodes': [], 'relations': []}).encode()
        files = {'graph.json': graph, 'console.db': b'not a SQLite database'}
        manifest = {'format': 1, 'namespace': self.prefix,
            'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
        path = Path(self.tmp.name) / 'invalid-sqlite.zip'
        with zipfile.ZipFile(path, 'x') as archive:
            archive.writestr('manifest.json', json.dumps(manifest))
            for name, data in files.items():
                archive.writestr(name, data)
        with self.assertRaisesRegex(ValueError, 'SQLite'):
            restore(self.store, path)
        self.assertFalse(restore_in_progress(self.home))

    def test_interrupted_sqlite_install_resumes_same_receipt_and_keeps_writers_blocked(self):
        from kg import maintenance
        from kg.lifecycle import MemoryService
        from kg.console_state import ConsoleState
        from kg.sync_state import SyncState
        self.store.write_batch([{'type': 'Concept', 'id': 'x', 'name': 'Recoverable'}], [])
        ConsoleState(self.home).set('fixture', 'preserved')
        with SyncState(self.home).transaction():
            pass
        path = Path(self.tmp.name) / 'recoverable.zip'
        maintenance.backup(self.store, path)
        self.clear()
        target = self.destination()
        original_install = maintenance._install_staged_file
        def crash_after_install(*args):
            original_install(*args)
            raise OSError('synthetic crash after atomic SQLite install')
        with patch('kg.maintenance._install_staged_file', side_effect=crash_after_install):
            with self.assertRaises(OSError):
                maintenance.restore(target, path)
        self.assertTrue(maintenance.restore_in_progress(target.config.home))
        self.assertEqual(MemoryService(target.config, target).detail(self.prefix + 'concept-x')['name'], 'Recoverable')
        with self.assertRaises(maintenance.RestoreInProgress):
            target.write_batch([{'type': 'Concept', 'id': 'blocked', 'name': 'Blocked'}], [])
        # Another profile home connected to the same namespace is blocked by the
        # graph receipt too, even though it cannot see the local guard file.
        with self.assertRaises(maintenance.RestoreInProgress):
            self.store.write_batch([{'type': 'Concept', 'id': 'blocked', 'name': 'Blocked'}], [])
        other = Path(self.tmp.name) / 'different-hash.zip'
        other.write_bytes(path.read_bytes())
        with zipfile.ZipFile(other, 'a') as archive:
            archive.comment = b'different archive identity'
        with self.assertRaises(maintenance.RestoreInProgress):
            maintenance.restore(target, other)
        result = maintenance.restore(target, path)
        self.assertTrue(result['resumed'])
        self.assertFalse(maintenance.restore_in_progress(target.config.home))
        self.assertEqual(len(MemoryService(target.config, target).list_memories()), 1)
        self.assertEqual(ConsoleState(target.config.home).get('fixture'), 'preserved')
        self.assertTrue(ConsoleState(target.config.home).get('paused'))
        target.write_batch([{'type': 'Concept', 'id': 'after', 'name': 'Unblocked'}], [])

    def test_external_shared_references_are_preserved_without_copying_other_nodes(self):
        from kg.maintenance import backup, restore
        from kg.lifecycle import MemoryService
        uri = self.prefix + 'concept-x'
        foreign = 'urn:foreign_' + uuid.uuid4().hex + ':kg:concept-shared'
        self.store.write_batch([{'type': 'Concept', 'uri': uri, 'name': 'Owned'}], [])
        with self.store.driver.session(database=self.config.database) as session:
            session.run("CREATE (:KGEntity:Concept {uri:$uri,hermes_name:'Shared external',shared:true})", {'uri': foreign}).consume()
        try:
            self.store.write_batch([], [{'from': uri, 'to': foreign, 'type': 'RELATED_TO'}])
            path = Path(self.tmp.name) / 'references.zip'
            result = backup(self.store, path)
            self.assertEqual(result['external_reference_count'], 1)
            self.clear()
            target = self.destination()
            restored = restore(target, path)
            self.assertEqual(restored['external_relations_restored'], 1)
            self.assertEqual(MemoryService(target.config, target).detail(uri)['relations'][0]['to_urn'], foreign)
            with self.store.driver.session(database=self.config.database) as session:
                count = session.run('MATCH (n {uri:$uri}) RETURN count(n) AS count', {'uri': foreign}).single()['count']
                self.assertEqual(count, 1)
                session.run('MATCH (n {uri:$uri}) SET n.shared=false', {'uri': foreign}).consume()
            self.clear()
            private_target = self.destination('restored-private')
            private_result = restore(private_target, path)
            self.assertEqual(private_result['external_relations_restored'], 0)
            self.assertEqual(len(private_result['external_relations_unresolved']), 1)
            self.assertEqual(MemoryService(private_target.config, private_target).detail(uri)['relations'], [])
        finally:
            with self.store.driver.session(database=self.config.database) as session:
                session.run('MATCH (n {uri:$uri}) DETACH DELETE n', {'uri': foreign}).consume()

    def test_lost_graph_commit_response_resumes_from_graph_receipt(self):
        from kg import maintenance
        from kg.lifecycle import MemoryService
        self.store.write_batch([{'type': 'Concept', 'id': 'committed', 'name': 'Committed once'}], [])
        path = Path(self.tmp.name) / 'lost-response.zip'
        maintenance.backup(self.store, path)
        self.clear()
        target = self.destination()
        save = maintenance._save_guard
        def crash_before_receipt_is_journaled(path, guard):
            if guard['state'] == 'graph_committed':
                raise OSError('synthetic crash between graph commit and local receipt')
            return save(path, guard)
        with patch('kg.maintenance._save_guard', side_effect=crash_before_receipt_is_journaled):
            with self.assertRaises(OSError):
                maintenance.restore(target, path)
        guard = json.loads(maintenance.restore_guard_path(target.config.home).read_text())
        self.assertEqual(guard['state'], 'staged')
        result = maintenance.restore(target, path)
        self.assertTrue(result['resumed'])
        self.assertEqual(len(MemoryService(target.config, target).list_memories()), 1)
        self.assertFalse(maintenance.restore_in_progress(target.config.home))
