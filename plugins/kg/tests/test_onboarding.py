"""First-install privacy boundaries survive setup failures and late transcripts."""
from contextlib import closing
import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from kg.sync_state import SyncState
from kg.console_cli import doctor, setup


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)
        with closing(sqlite3.connect(self.home/'state.db')) as db,db:
            db.executescript("CREATE TABLE sessions(id TEXT PRIMARY KEY,source TEXT,started_at REAL); CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,timestamp REAL);")
            db.execute("INSERT INTO sessions VALUES('fixture','desktop',100)")
            db.executemany("INSERT INTO messages VALUES(?,'fixture','user','Existing historical text',100)",[(i,) for i in range(1,7)])

    def test_doctor_reports_successful_health_probe(self):
        with patch('kg.store.KGStore'), patch('kg.health.check_health',return_value={'connected':True}), patch('kg.console_cli._core',return_value='/core'),patch('kg.console_cli.importlib.util.find_spec',return_value=object()):
            result=doctor(self.home)
        self.assertTrue(result['database']['connected'])
        self.assertTrue(result['ok'])

    def test_doctor_allows_keyword_search_without_optional_semantic_packages(self):
        def available(name): return object() if name in {'neo4j','rdflib','fastapi'} else None
        with patch('kg.store.KGStore'),patch('kg.health.check_health',return_value={'connected':True}), \
             patch('kg.console_cli._core',return_value=None), \
             patch('kg.console_cli.importlib.util.find_spec',side_effect=available):
            result=doctor(self.home)
        self.assertTrue(result['ok'])
        self.assertFalse(result['packages']['fastembed'])
        self.assertFalse(result['packages']['onnxruntime'])
        self.assertFalse(result['capabilities']['semantic_dependencies_available'])

    def test_failed_setup_keeps_history_boundary_and_does_not_activate(self):
        with patch('kg.store.KGStore',side_effect=ConnectionError('fixture offline')):
            with self.assertRaises(ConnectionError): setup(self.home)
        marker=json.loads((self.home/'kg-console.json').read_text())
        self.assertFalse(marker['ready'])
        self.assertTrue(marker['history_seeded'])
        self.assertFalse(json.loads((self.home/'kg.json').read_text())['enabled'])
        self.assertEqual(SyncState(self.home).discover(),[])
        self.assertEqual(SyncState(self.home).status()['committed_sessions'],0)
        with patch('kg.store.KGStore',side_effect=ConnectionError('fixture offline')):
            with self.assertRaises(ConnectionError): setup(self.home)
        self.assertEqual(json.loads((self.home/'kg-console.json').read_text())['capture_started_at'],marker['capture_started_at'])

    def test_fresh_setup_persists_generic_profile_settings_without_credentials(self):
        environment={'KG_BOLT_URL':'bolt://product:7687','HERMES_KG_BOLT_URL':'bolt://legacy:7687',
                     'KG_USER':'product-user','KG_PASSWORD':'never-persist-this',
                     'KG_DATABASE':'product-db','KG_URN_PREFIX':'urn:customer:kg:',
                     'KG_USER_PERSON_URN':'urn:customer:kg:person-owner','KG_TIMEZONE':'UTC'}
        with patch.dict(os.environ,environment,clear=True),patch('kg.store.KGStore',side_effect=ConnectionError('fixture offline')):
            with self.assertRaises(ConnectionError): setup(self.home)
        settings=json.loads((self.home/'kg.json').read_text())
        self.assertEqual(settings['bolt_url'],'bolt://product:7687')
        self.assertEqual(settings['user'],'product-user')
        self.assertEqual(settings['database'],'product-db')
        self.assertEqual(settings['user_person_urn'],'urn:customer:kg:person-owner')
        self.assertEqual(settings['timezone'],'UTC')
        self.assertNotIn('password',settings)
        self.assertNotIn('never-persist-this',(self.home/'kg.json').read_text())

    def test_fresh_profiles_use_distinct_stable_private_namespaces(self):
        homes=[self.home/'first',self.home/'second']
        with patch.dict(os.environ,{},clear=True),patch('kg.store.KGStore',side_effect=ConnectionError('fixture offline')):
            for home in homes:
                with self.assertRaises(ConnectionError): setup(home,seed_history=False)
            first=json.loads((homes[0]/'kg.json').read_text(encoding='utf-8'))
            second=json.loads((homes[1]/'kg.json').read_text(encoding='utf-8'))
            self.assertNotEqual(first['urn_prefix'],second['urn_prefix'])
            self.assertTrue(first['user_person_urn'].startswith(first['urn_prefix']))
            self.assertFalse(first['default_shared'])
            with self.assertRaises(ConnectionError): setup(homes[0],seed_history=False)
            self.assertEqual(json.loads((homes[0]/'kg.json').read_text(encoding='utf-8'))['urn_prefix'],first['urn_prefix'])

    def test_fresh_setup_preserves_explicit_owner_namespace_without_prefix_override(self):
        for index,key in enumerate(('KG_USER_PERSON_URN','HERMES_KG_USER_PERSON_URN')):
            home=self.home/str(index)
            with self.subTest(key=key),patch.dict(os.environ,{key:'urn:customer:kg:person-owner'},clear=True), \
                 patch('kg.store.KGStore',side_effect=ConnectionError('fixture offline')):
                with self.assertRaises(ConnectionError): setup(home,seed_history=False)
            settings=json.loads((home/'kg.json').read_text(encoding='utf-8'))
            self.assertEqual(settings['urn_prefix'],'urn:customer:kg:')
            self.assertEqual(settings['user_person_urn'],'urn:customer:kg:person-owner')

    def test_setup_round_trips_unicode_profile_and_user_name_as_utf8(self):
        environment={'KG_USER':'记忆用户','KG_DATABASE':'知识图谱','KG_TIMEZONE':'UTC'}
        web=types.ModuleType('kg.console_web');web.console_token=lambda home:'fixture-token'
        with patch.dict(os.environ,environment),patch.dict(sys.modules,{'kg.console_web':web}), \
             patch('kg.store.KGStore') as store_class, \
             patch('kg.maintenance.migrate',return_value={}), \
             patch('kg.search.HybridSearch.ensure_indexes',return_value={}):
            store=store_class.return_value.__enter__.return_value
            store.run_read.return_value=[]
            result=setup(self.home,name='张三')
            self.assertTrue(result['ok'])
            self.assertEqual(store.write_batch.call_args.args[0][0]['name'],'张三')
            settings=json.loads((self.home/'kg.json').read_bytes().decode('utf-8'))
            self.assertEqual(settings['user'],'记忆用户')
            self.assertEqual(settings['database'],'知识图谱')
            self.assertTrue(settings['enabled'])
            marker=self.home/'kg-console.json'
            state=json.loads(marker.read_bytes().decode('utf-8'));state['label']='现有配置'
            marker.write_text(json.dumps(state,ensure_ascii=False),encoding='utf-8')
            setup(self.home,name='张三')
            self.assertEqual(json.loads(marker.read_bytes().decode('utf-8'))['label'],'现有配置')

    def test_selected_seeded_history_can_backfill_without_resetting_real_receipts(self):
        state=SyncState(self.home)
        self.assertEqual(state.seed_existing_history(),1)
        self.assertEqual(state.discover(),[])
        state.request_backfill(['fixture'])
        snapshot=state.discover()[0]
        self.assertEqual(snapshot['after_message_id'],0)
        with self.assertRaisesRegex(ValueError,'extraction history'):
            state.request_backfill(['fixture'])

    def test_capture_cutoff_keeps_messages_written_after_activation(self):
        with closing(sqlite3.connect(self.home/'state.db')) as db,db:
            db.execute("INSERT INTO messages VALUES(7,'fixture','user','New intended memory',300)")
        state=SyncState(self.home)
        state.seed_existing_history(cutoff=200)
        self.assertEqual(state.discover()[0]['messages'][0]['content'],'New intended memory')

    def test_host_provenance_metadata_is_preserved_and_changes_invalidate_snapshot(self):
        with closing(sqlite3.connect(self.home/'state.db')) as db,db:
            db.execute('ALTER TABLE messages ADD COLUMN display_kind TEXT')
            db.execute("UPDATE messages SET display_kind='hidden' WHERE id=1")
        state=SyncState(self.home)
        snapshot=state.discover()[0]
        from kg.provenance import groundable_user_messages
        self.assertEqual(len(groundable_user_messages(snapshot)),5)
        with closing(sqlite3.connect(self.home/'state.db')) as db,db:
            db.execute("UPDATE messages SET display_kind='hidden' WHERE id=2")
        from kg.session_source import SourceChangedError
        with self.assertRaises(SourceChangedError): state.source.validate_snapshot(snapshot)
