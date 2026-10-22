"""API trust boundaries and mutation contracts, with no live memory data."""
from __future__ import annotations
import tempfile
import time
import unittest
from pathlib import Path

from kg.config import KGConfig

try:
    from fastapi.testclient import TestClient
    from kg.console_web import create_app
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

    def test_status_does_not_claim_database_connected_on_failure(self):
        result=self.client.get('/api/v1/status',headers=self.auth)
        self.assertEqual(result.status_code,200,result.text)
        self.assertFalse(result.json()['connected'])
        self.assertNotIn('password',self.client.get('/api/v1/settings',headers=self.auth).text)

    def test_explicit_reindex_completes_all_cards_instead_of_one_background_batch(self):
        response=self.client.post('/api/v1/search/reindex',headers=self.auth)
        self.assertEqual(response.status_code,202,response.text)
        job_id=response.json()['job_id']
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            job=next(row for row in self.app.state.console_state.jobs() if row['id']==job_id)
            if job['status']!='queued': break
            time.sleep(.01)
        self.assertEqual(job['status'],'succeeded',job)
        self.assertEqual(job['result']['indexed'],237)
