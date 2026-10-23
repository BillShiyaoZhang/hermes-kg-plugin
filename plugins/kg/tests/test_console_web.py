"""API trust boundaries and mutation contracts, with no live memory data."""
from __future__ import annotations
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from kg.config import KGConfig

try:
    from fastapi.testclient import TestClient
    from kg.console_web import console_token,create_app
except ImportError:
    TestClient=None


class FakeMemory:
    store=object()
    def list_memories(self,**kwargs):
        return [{'uri':'urn:hermes:kg:concept-example','type':'concept','name':'测试记忆','props':{'hermes_description':'真实 API fixture'},'version':2,'evidence':[]}]
    def detail(self,uri): return self.list_memories()[0]
    def revisions(self,uri): return [{'version':2,'origin':'manual'}]
    def reviews(self): return []
    def correct(self,uri,changes,**kwargs):
        if kwargs['expected_version']!=2: raise ValueError('Memory version changed')
        return {'ok':True,'receipt_id':kwargs['operation_id'],'entities_written':1}
    archive=restore=correct


class FakeSearch:
    embedder=None
    def search(self,question,**kwargs): return {'kg_results':FakeMemory().list_memories(),'mode':'keyword'}
    def status(self): return {'mode':'keyword','model':{'ready':False}}
    def ensure_indexes(self): return {}
    def reindex(self): return {'indexed':237,'vector_indexed':0,'pending':237}
    def index_pending(self): return {'indexed':100}


@unittest.skipIf(TestClient is None,'console extras are not installed')
class ConsoleWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app=create_app(KGConfig(home=Path(self.tmp.name)),token='fixture-access-token',memory=FakeMemory(),search=FakeSearch(),background=False)
        self.client=TestClient(self.app,raise_server_exceptions=False)
        self.auth={'Authorization':'Bearer fixture-access-token'}
        self.addCleanup(self.client.close)

    def test_all_memory_endpoints_require_token(self):
        for path in ('/api/v1/status','/api/v1/memories','/api/v1/settings','/api/v1/reviews'):
            self.assertEqual(self.client.get(path).status_code,401)

    def test_generic_console_token_overrides_legacy_without_persisting_a_secret(self):
        environment={'KG_CONSOLE_TOKEN':'product-console-token','HERMES_KG_CONSOLE_TOKEN':'legacy-console-token'}
        with patch.dict(os.environ,environment):
            self.assertEqual(console_token(Path(self.tmp.name)),'product-console-token')
        self.assertFalse((Path(self.tmp.name)/'kg-console.token').exists())

    def test_legacy_console_token_remains_supported(self):
        environment={key:value for key,value in os.environ.items() if key!='KG_CONSOLE_TOKEN'}
        environment['HERMES_KG_CONSOLE_TOKEN']='legacy-console-token'
        with patch.dict(os.environ,environment,clear=True):
            self.assertEqual(console_token(Path(self.tmp.name)),'legacy-console-token')

    def test_invalid_generic_console_token_cannot_fall_back_to_another_token(self):
        with patch.dict(os.environ,{'KG_CONSOLE_TOKEN':'short','HERMES_KG_CONSOLE_TOKEN':'legacy-console-token'}):
            with self.assertRaisesRegex(ValueError,'at least 16'):
                console_token(Path(self.tmp.name))

    def test_generic_model_cache_and_static_directory_reach_console_runtime(self):
        home=Path(self.tmp.name);assets=home/'assets';assets.mkdir()
        (assets/'index.html').write_text('product asset fixture',encoding='utf-8')
        cache=str(home/'models')
        environment={'KG_MODEL_CACHE':cache,'HERMES_KG_MODEL_CACHE':str(home/'wrong-models'),
                     'KG_STATIC_DIR':str(assets),'HERMES_KG_STATIC_DIR':str(home/'wrong-assets')}
        with patch.dict(os.environ,environment),patch('kg.embedding.LocalEmbedder') as embedder, \
             patch('kg.search.HybridSearch',return_value=FakeSearch()):
            app=create_app(KGConfig(home=home),token='fixture-access-token',memory=FakeMemory(),background=False)
            with TestClient(app) as client:
                self.assertEqual(client.get('/').text,'product asset fixture')
            embedder.assert_called_once_with(cache)

    def test_cross_site_and_host_rejected(self):
        result=self.client.post('/api/v1/sync/pause',headers={**self.auth,'Origin':'https://untrusted.example'})
        self.assertEqual(result.status_code,403)
        result=self.client.get('/api/v1/status',headers={**self.auth,'Host':'untrusted.example'})
        self.assertEqual(result.status_code,400)

    def test_list_detail_search_use_normalized_contract(self):
        result=self.client.get('/api/v1/memories',headers=self.auth)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['items'][0]['properties']['hermes_description'],'真实 API fixture')
        result=self.client.get('/api/v1/memories/detail',params={'uri':'urn:hermes:kg:concept-example'},headers=self.auth)
        self.assertEqual(result.json()['revisions'][0]['version'],2)
        self.assertEqual(self.client.post('/api/v1/search',json={'question':'测试'},headers=self.auth).json()['mode'],'keyword')

    def test_mutation_needs_version_and_success_receipt(self):
        body={'uri':'urn:hermes:kg:concept-example','changes':{'name':'新名称'},'expected_version':1,'operation_id':'fixture-operation'}
        self.assertEqual(self.client.post('/api/v1/memories/correct',json=body,headers=self.auth).status_code,409)
        body['expected_version']=2
        result=self.client.post('/api/v1/memories/correct',json=body,headers=self.auth)
        self.assertEqual(result.status_code,200,result.text)
        self.assertTrue(result.json()['ok'])

    def test_pause_persists_without_hiding_existing_memories(self):
        self.assertTrue(self.client.post('/api/v1/sync/pause',headers=self.auth).json()['paused'])
        self.assertEqual(self.client.get('/api/v1/memories',headers=self.auth).json()['total'],1)
        self.assertFalse(self.client.post('/api/v1/sync/resume',headers=self.auth).json()['paused'])

    def test_backfill_literal_route_reaches_queue_instead_of_sync_control(self):
        selected=['synthetic-session-one','synthetic-session-two']
        with patch('kg.sync_state.SyncState.request_backfill',return_value={'ok':True,'queued_sessions':selected}) as enqueue:
            response=self.client.post('/api/v1/sync/backfill',json={'session_ids':selected},headers=self.auth)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json(),{'ok':True,'queued_sessions':selected})
        enqueue.assert_called_once_with(selected)

    def test_status_does_not_claim_database_connected_on_failure(self):
        result=self.client.get('/api/v1/status',headers=self.auth)
        self.assertEqual(result.status_code,200,result.text)
        self.assertFalse(result.json()['connected'])
        self.assertNotIn('password',self.client.get('/api/v1/settings',headers=self.auth).text)

    def test_external_sync_scheduler_can_disable_console_schedule(self):
        from unittest.mock import patch
        with patch.dict('os.environ',{'KG_SYNC_SCHEDULER_ENABLED':'false'}):
            app=create_app(KGConfig(home=Path(self.tmp.name)),token='fixture-access-token',memory=FakeMemory(),search=FakeSearch(),background=False)
        with TestClient(app) as client:
            result=client.get('/api/v1/status',headers=self.auth)
            self.assertEqual(result.status_code,200,result.text)
            self.assertFalse(result.json()['sync']['scheduler_enabled'])
        with patch.dict('os.environ',{'KG_SYNC_SCHEDULER_ENABLED':'invalid'}):
            with self.assertRaisesRegex(ValueError,'sync_scheduler_enabled'):
                create_app(KGConfig(home=Path(self.tmp.name)),token='fixture-access-token',memory=FakeMemory(),search=FakeSearch(),background=False)

    def test_explicit_reindex_completes_all_cards_instead_of_one_background_batch(self):
        response=self.client.post('/api/v1/search/reindex',headers=self.auth)
        self.assertEqual(response.status_code,202,response.text)
        job_id=response.json()['job_id']
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            job=next(row for row in self.app.state.console_state.jobs() if row['id']==job_id)
            if job['status'] in {'succeeded','failed','interrupted'}: break
            time.sleep(.01)
        self.assertEqual(job['status'],'succeeded',job)
        self.assertEqual(job['result']['indexed'],237)

    def test_running_job_is_distinct_from_queued_and_is_durable_after_page_leaves(self):
        entered=threading.Event();release=threading.Event()
        def reindex():
            entered.set()
            release.wait(3)
            return {'indexed':3}
        with patch.object(self.app.state.search,'reindex',reindex):
            response=self.client.post('/api/v1/search/reindex',headers=self.auth)
            self.assertEqual(response.status_code,202)
            self.assertEqual(response.json()['status'],'queued')
            try:
                self.assertTrue(entered.wait(2))
                jobs=self.client.get('/api/v1/jobs',headers=self.auth).json()['items']
                job=next(row for row in jobs if row['id']==response.json()['job_id'])
                self.assertEqual(job['status'],'running')
                self.assertIsNotNone(job['started_at'])
                self.assertIsNone(job['finished_at'])
                self.assertEqual(self.client.post('/api/v1/search/reindex',headers=self.auth).status_code,409)
            finally:
                release.set()
                deadline=time.monotonic()+2
                while time.monotonic()<deadline:
                    rows=self.app.state.console_state.jobs()
                    job=next(row for row in rows if row['id']==response.json()['job_id'])
                    if job['status'] in {'succeeded','failed','interrupted'}: break
                    time.sleep(.01)
                self.assertEqual(job['status'],'succeeded')

    def test_legacy_job_errors_and_raw_result_json_never_reach_diagnostics(self):
        state=self.app.state.console_state
        job=state.create_job('sync')
        state.finish(job,'failed',{'error':'Bearer fixture-secret; private text; neo4j://user:password@example',
                                 'committed':2,'password':'fixture-secret','receipts':[{'body':'private text'}]})
        for path in ('/api/v1/jobs','/api/v1/status'):
            response=self.client.get(path,headers=self.auth)
            self.assertEqual(response.status_code,200)
            self.assertNotIn('fixture-secret',response.text)
            self.assertNotIn('private text',response.text)
            self.assertNotIn('result_json',response.text)
        row=self.client.get('/api/v1/jobs',headers=self.auth).json()['items'][0]
        self.assertEqual(row['result'],{'committed':2})
        self.assertIn('可重试',row['summary'])

    def test_queue_count_and_snapshot_count_are_separate_and_status_partially_recovers(self):
        with patch('kg.sync_state.SyncState.status',return_value={
            'queued_sessions':4,'pending':[{'snapshot_id':'synthetic','attempts':2,'error':'fixture-private'}]}):
            value=self.client.get('/api/v1/status',headers=self.auth).json()
        self.assertEqual(value['sync']['queued_count'],4)
        self.assertEqual(value['sync']['pending_count'],1)
        self.assertEqual(value['sync']['failed_count'],1)
        self.assertNotIn('fixture-private',str(value))
        with patch('kg.sync_state.SyncState.status',side_effect=RuntimeError('secret-state-path')):
            value=self.client.get('/api/v1/status',headers=self.auth).json()
        self.assertFalse(value['sync']['available'])
        self.assertIsNone(value['sync']['queued_count'])
        self.assertIsNone(value['sync']['pending_count'])
        self.assertEqual(value['search']['mode'],'keyword')
        self.assertNotIn('secret-state-path',str(value))
        with patch.object(self.app.state.search,'status',side_effect=RuntimeError('private-search-config')):
            value=self.client.get('/api/v1/status',headers=self.auth).json()
        self.assertEqual(value['search']['state'],'unavailable')
        self.assertIsNone(value['search']['model_ready'])
        self.assertNotIn('private-search-config',str(value))

    def test_validation_and_upstream_errors_do_not_echo_private_input(self):
        body={'uri':'urn:hermes:kg:concept-example','changes':{},'expected_version':'private-credential',
              'operation_id':'fixture-operation'}
        response=self.client.post('/api/v1/memories/correct',json=body,headers=self.auth)
        self.assertEqual(response.status_code,422)
        self.assertNotIn('private-credential',response.text)
        body['expected_version']=2
        with patch.object(self.app.state.memory,'correct',side_effect=ValueError('private-credential')):
            response=self.client.post('/api/v1/memories/correct',json=body,headers=self.auth)
        self.assertEqual(response.status_code,400)
        self.assertNotIn('private-credential',response.text)
        with patch.object(self.app.state.search,'search',side_effect=RuntimeError('password=private-credential')):
            response=self.client.post('/api/v1/search',json={'question':'synthetic'},headers=self.auth)
        self.assertEqual(response.status_code,503)
        self.assertNotIn('private-credential',response.text)
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertIn("frame-ancestors 'none'",response.headers['content-security-policy'])

    def test_authentication_failures_are_not_cacheable_and_have_security_headers(self):
        response=self.client.get('/api/v1/settings')
        self.assertEqual(response.status_code,401)
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(response.headers['referrer-policy'],'no-referrer')

    def test_list_capacity_is_explicit_instead_of_claiming_an_exact_total(self):
        row=FakeMemory().list_memories()[0]
        with patch.object(self.app.state.memory,'list_memories',return_value=[row]*10000):
            value=self.client.get('/api/v1/memories',headers=self.auth).json()
        self.assertTrue(value['truncated'])
        self.assertFalse(value['total_is_exact'])
        self.assertEqual(len(value['items']),30)

    def test_search_defaults_to_current_namespace_and_does_not_call_external_interpreter(self):
        with patch.object(self.app.state.search,'search',wraps=FakeSearch().search) as search, \
             patch('kg.query_planner.interpret_query',side_effect=AssertionError('must stay local')):
            response=self.client.post('/api/v1/search',json={'question':'synthetic','filters':{'namespace':'urn:outside:kg:'}},headers=self.auth)
        self.assertEqual(response.status_code,200)
        self.assertEqual(search.call_args.kwargs['filters']['namespace'],'urn:hermes:kg:')

    def test_interpretation_failure_offers_local_recovery_without_upstream_secrets(self):
        with patch('kg.query_planner.interpret_query',side_effect=RuntimeError('Authorization: private-credential')):
            response=self.client.post('/api/v1/search',json={'question':'synthetic','interpret':True},headers=self.auth)
        self.assertEqual(response.status_code,503)
        self.assertIn('本地搜索',response.text)
        self.assertNotIn('private-credential',response.text)

    def test_default_diagnostics_exclude_host_and_person_identity(self):
        with patch('kg.health.check_health',return_value={
            'connected':True,'database':'private-db','namespace':'urn:private:kg:',
            'configured_user':{'uri':'urn:private:kg:person-owner','present':True}}):
            response=self.client.get('/api/v1/status',headers=self.auth)
        self.assertNotIn('urn:private',response.text)
        self.assertNotIn('private-db',response.text)
        self.assertNotIn(str(Path(self.tmp.name)),response.text)
        settings=self.client.get('/api/v1/settings',headers=self.auth).json()
        for key in ('profile','namespace','user_person_urn','database'):
            self.assertNotIn(key,settings)

    def test_private_read_inputs_use_authenticated_post_bodies_and_keep_get_compatibility(self):
        response=self.client.post('/api/v1/memories/query',json={'q':'真实 API fixture','type':'concept'},headers=self.auth)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['total'],1)
        self.assertNotIn('fixture',str(response.request.url))
        response=self.client.post('/api/v1/memories/detail',json={'uri':'urn:hermes:kg:concept-example'},headers=self.auth)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['version'],2)
        self.assertNotIn('urn:',str(response.request.url))
        for route,method,body in (
            ('/api/v1/ontology/entities/query','entities',{'type':'concept','q':'private query','offset':20,'limit':20}),
            ('/api/v1/ontology/relations/query','relations',{'relation':'RELATED_TO','q':'private query','limit':20}),
        ):
            self.assertEqual(self.client.post(route,json=body).status_code,401)
            with patch('kg.ontology.OntologyService.'+method,return_value={'items':[],'total':0}) as query:
                response=self.client.post(route,json=body,headers=self.auth)
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(query.call_args.kwargs['q'],'private query')
                self.assertNotIn('private',str(response.request.url))
        self.assertEqual(self.client.get('/api/v1/memories',headers=self.auth).status_code,200)
        response=self.client.post('/api/v1/ontology/entities/query',json={'q':'private query'*30},headers=self.auth)
        self.assertEqual(response.status_code,422)
        self.assertNotIn('private query',response.text)
