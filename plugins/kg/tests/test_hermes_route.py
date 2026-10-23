"""Real Hermes auxiliary/SDK route, using only synthetic local HTTP and profiles.

Run with a core checkout on HERMES_AGENT_HOME or PYTHONPATH, for example:
  docker exec hermes-kg-full-tests python -m unittest discover \
      -s /app/plugins/kg/tests -p test_hermes_route.py -v

This is an HTTP/SDK contract test, not a real model or extraction quality test.
"""
from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from kg.hermes_runtime import discover_python


def _core_root():
    candidates=[os.environ.get('HERMES_AGENT_HOME'),*sys.path]
    for candidate in candidates:
        if candidate and (Path(candidate)/'agent'/'auxiliary_client.py').is_file():
            return Path(candidate).resolve()
    return None


CORE_ROOT=_core_root()
PLUGIN_ROOT=Path(__file__).resolve().parents[2]
DEPENDENCY_ROOTS=[]
for _name in ('neo4j','rdflib','tzdata'):
    _spec=importlib.util.find_spec(_name)
    if _spec is not None and _spec.origin:
        _root=str(Path(_spec.origin).resolve().parents[1])
        if _root not in DEPENDENCY_ROOTS:
            DEPENDENCY_ROOTS.append(_root)

_CHILD=r'''
import json, os, socket, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import url2pathname

sandbox=Path.cwd().resolve()
# Current Hermes imports its entry-point bootstrap while creating an HTTP
# client. Its maintenance-only branch avoids repair/update of the installation;
# the real profile loader, auxiliary router and SDK remain exercised below.
sys.dont_write_bytecode=True
actual_home=Path(os.environ['KG_TEST_REAL_HOME']).resolve()
core=Path(os.environ['HERMES_AGENT_HOME']).resolve()
plugins=Path(sys.argv[1]).resolve()
sys.argv=['kg-route-contract','pm','repair']
isolated_os_home=sandbox/'isolated-os-home'
isolated_os_home.mkdir()
selected=sandbox/'selected-profile'
selected.mkdir()
allowed_roots=[sandbox,core,plugins,Path(sys.prefix).resolve(),Path(sys.base_prefix).resolve()]
allowed_roots.extend(Path(path).resolve() for path in json.loads(os.environ['KG_TEST_DEPENDENCY_ROOTS']))

def within(path,root):
    return path==root or root in path.parents

def guard_profile_reads(event,args):
    # Audit socket operations too, including connect_ex() which can bypass a
    # Python-level connect() wrapper in an alternate SDK transport.
    if event=='socket.connect' and isinstance(args[1],tuple):
        if args[1][0] not in {'127.0.0.1','::1','localhost'}:
            raise AssertionError('Non-loopback socket is forbidden')
    if event=='socket.getaddrinfo' and args[0] not in {'127.0.0.1','::1','localhost'}:
        raise AssertionError('Non-loopback DNS is forbidden')
    if event=='sqlite3.connect' and args[0]!=':memory:':
        value=str(args[0])
        path=Path(url2pathname(urlsplit(value).path) if value.startswith('file:') else value).resolve()
        if not within(path,sandbox):
            raise AssertionError('SQLite access outside the synthetic sandbox is forbidden')
    if event in {'os.remove','os.rmdir','os.mkdir','os.rename','os.replace','os.chmod','os.utime'}:
        paths=args[:2] if event in {'os.rename','os.replace'} else args[:1]
        for value in paths:
            if isinstance(value,(str,bytes,os.PathLike)) and not within(Path(os.fsdecode(value)).resolve(),sandbox):
                raise AssertionError('Writes outside the synthetic sandbox are forbidden')
    if event!='open' or not isinstance(args[0],(str,bytes,os.PathLike)):
        return
    path=Path(os.fsdecode(args[0])).resolve()
    if path==Path(os.devnull).resolve():
        return
    if within(path,sandbox):
        return
    mode=args[1]
    flags=args[2]
    if (isinstance(mode,str) and any(marker in mode for marker in 'wax+')) or \
       (isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):
        raise AssertionError('Writes outside the synthetic sandbox are forbidden')
    # Core source may reside inside ~/.hermes, but its .env/config/auth files
    # must never enter this synthetic test. User-home reads outside explicit
    # source/runtime directories are also prohibited.
    if path.name in {'.env','.op.env','config.yaml','auth.json'}:
        raise AssertionError('Non-synthetic profile file access is forbidden')
    if within(path,actual_home) and not any(within(path,root) for root in allowed_roots):
        raise AssertionError('Real user-home access is forbidden: '+str(path))
sys.addaudithook(guard_profile_reads)

allowed={'127.0.0.1','::1','localhost'}
original_connect=socket.socket.connect
original_getaddrinfo=socket.getaddrinfo
def local_connect(sock,address):
    if isinstance(address,tuple) and address[0] not in allowed:
        raise AssertionError('Non-loopback socket is forbidden')
    return original_connect(sock,address)
def local_getaddrinfo(host,*args,**kwargs):
    if host not in allowed:
        raise AssertionError('Non-loopback DNS is forbidden')
    return original_getaddrinfo(host,*args,**kwargs)
socket.socket.connect=local_connect
socket.getaddrinfo=local_getaddrinfo

requests=[]
plan={'text':'Ada meeting','entity_type':'event','from':None,'to':None}
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass
    def do_POST(self):
        payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        requests.append({'path':self.path,'payload':payload,'auth':self.headers.get('Authorization')})
        response={'id':'synthetic-local-only','object':'chat.completion','created':0,
                  'model':'kg-smoke-profile-model','choices':[{'index':0,'message':{'role':'assistant','content':json.dumps(plan)},'finish_reason':'stop'}],
                  'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
        body=json.dumps(response).encode()
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)

server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
thread=threading.Thread(target=server.serve_forever,daemon=True)
thread.start()
try:
    base='http://127.0.0.1:'+str(server.server_port)+'/v1'
    selected.joinpath('config.yaml').write_text(json.dumps({
        'model':{'provider':'custom','model':'kg-smoke-profile-model','base_url':base},
        'auxiliary':{'kg_query':{'provider':'custom','model':'kg-smoke-profile-model','base_url':base,
                                 'key_env':'KG_SYNTHETIC_ROUTE_KEY','api_mode':'chat_completions'}}}))
    selected.joinpath('.env').write_text('KG_SYNTHETIC_ROUTE_KEY=synthetic-local-only-value\n')
    with patch('pathlib.Path.home',return_value=isolated_os_home):
        from kg.config import KGConfig
        from kg.query_planner import interpret_query
        result=interpret_query('When is the synthetic Ada meeting?',KGConfig(home=selected))
        assert result=={'text':'Ada meeting','filters':{'entity_type':'event'}},result
        assert os.environ['HERMES_HOME']==str(selected.resolve())
        assert len(requests)==1,len(requests)
        request=requests[0]
        assert request['path']=='/v1/chat/completions',request['path']
        assert request['payload']['model']=='kg-smoke-profile-model'
        assert request['auth']=='Bearer synthetic-local-only-value'
        # Use Hermes' real user-plugin loader and MemoryManager. Only the graph
        # transport is replaced: registration, lifecycle, tool dispatch and the
        # durable session queue run through the real adapter and host code.
        from plugins.memory import _load_provider_from_dir
        from agent.memory_manager import MemoryManager
        provider=_load_provider_from_dir(plugins/'kg',register_skills=False)
        assert provider is not None and provider.name=='kg'
        assert provider.is_available()
        class Store:
            def __init__(self,config):
                self.config=config
                self.closed=False
                self.writes=[]
            def close(self):
                self.closed=True
            def write_batch(self,entities,relations,source_session):
                self.writes.append(source_session)
                return {'ok':True,'receipt_id':'synthetic-receipt','disposition':'applied'}
        provider._store_factory=Store
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(selected/'state.db')) as transcript:
            transcript.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY,source TEXT,started_at REAL)')
            transcript.execute('INSERT INTO sessions VALUES(?,?,?)',('synthetic-session','cli',100.0))
            transcript.commit()
        manager=MemoryManager()
        manager.add_provider(provider)
        manager.initialize_all('synthetic-session',hermes_home=str(selected),platform='cli',agent_context='primary')
        assert provider._store is not None
        assert manager.get_all_tool_names()=={'kg_search','kg_write','kg_status'}
        provider._search=lambda question,limit:{'kg_results':[{'name':'Synthetic memory'}]}
        manager.on_turn_start(1,'remember synthetic memory')
        assert 'Synthetic memory' in manager.prefetch_all('remember synthetic memory',session_id='synthetic-session')
        assert 'recalled 1 memory' in manager.describe_recall()
        search=json.loads(manager.handle_tool_call('kg_search',{'question':'synthetic'}))
        assert search['ok'] and search['result']['kg_results'][0]['name']=='Synthetic memory'
        write=json.loads(manager.handle_tool_call('kg_write',{'entities':[],'relations':[]}))
        assert write['saved'] and provider._store.writes==['synthetic-session']
        manager.sync_all('synthetic human message','synthetic reply',session_id='synthetic-session')
        assert manager.flush_pending(timeout=3)
        with closing(sqlite3.connect(selected/'kg-sync'/'sync-state-v2.db')) as queue:
            assert queue.execute('SELECT session_id FROM queue').fetchall()==[('synthetic-session',)]
        manager.on_session_switch('synthetic-session-next',reset=True)
        manager.handle_tool_call('kg_write',{'entities':[],'relations':[]})
        assert provider._store.writes[-1]=='synthetic-session-next'
        provider.shutdown()
        manager.initialize_all('synthetic-cron',hermes_home=str(selected),platform='cron',agent_context='cron')
        assert not json.loads(manager.handle_tool_call('kg_write',{'entities':[]}))['ok']
        store=provider._store
        manager.shutdown_all()
        assert store.closed
        from agent.auxiliary_client import shutdown_cached_clients
        shutdown_cached_clients()
        import openai
        print(json.dumps({'ok':True,'sdk':openai.__version__,'requests':len(requests),
                          'path':request['path'],'home_bound':True,'dotenv_key_used':True,
                          'external_network':'blocked','user_profile_reads':'blocked',
                          'plugin_registration':True,'manager_tool_dispatch':True,'automation_write_blocked':True}))
finally:
    server.shutdown()
    server.server_close()
    thread.join(2)
'''


@unittest.skipUnless(CORE_ROOT,'Hermes core checkout is required for the real auxiliary route contract')
class HermesRouteIntegrationTests(unittest.TestCase):
    def test_selected_profile_dotenv_and_real_sdk_reach_only_local_endpoint(self):
        with TemporaryDirectory(prefix='kg-route-contract-') as directory:
            # Do not inherit keys, proxies, or the caller's active profile.
            env={'PATH':os.environ.get('PATH',''),
                 'PYTHONPATH':os.pathsep.join((str(PLUGIN_ROOT),str(CORE_ROOT),*DEPENDENCY_ROOTS)),
                 'HERMES_AGENT_HOME':str(CORE_ROOT),
                 'HERMES_HOME':str(Path(directory)/'deliberately-wrong-profile'),
                 'HOME':str(Path(directory)/'isolated-os-home'),
                 'USERPROFILE':str(Path(directory)/'isolated-os-home'),
                 'KG_TEST_REAL_HOME':str(Path.home()),
                 'KG_TEST_DEPENDENCY_ROOTS':json.dumps(DEPENDENCY_ROOTS),
                 'NO_PROXY':'127.0.0.1,localhost,::1'}
            if os.name=='nt' and os.environ.get('SYSTEMROOT'):
                env['SYSTEMROOT']=os.environ['SYSTEMROOT']
            for key in ('TEMP','TMP'):
                if os.environ.get(key):
                    env[key]=os.environ[key]
            # Keep the real SDK under this child's audit hooks even when the
            # outer suite runs in the independent console environment.
            result=subprocess.run([str(discover_python(CORE_ROOT)),'-c',_CHILD,str(PLUGIN_ROOT)],
                                  cwd=directory,env=env,text=True,capture_output=True,timeout=45)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            report=json.loads(result.stdout.strip().splitlines()[-1])
            self.assertTrue(report['ok'])
            self.assertEqual(report['requests'],1)
            self.assertEqual(report['external_network'],'blocked')
            self.assertEqual(report['user_profile_reads'],'blocked')
            self.assertTrue(report['home_bound'])
            self.assertTrue(report['dotenv_key_used'])
            self.assertTrue(report['plugin_registration'])
            self.assertTrue(report['manager_tool_dispatch'])
            self.assertTrue(report['automation_write_blocked'])


if __name__=='__main__':
    unittest.main()
