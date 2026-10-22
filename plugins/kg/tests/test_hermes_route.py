"""Real Hermes auxiliary/SDK route, using only synthetic local HTTP and profiles.

Run with a core checkout on HERMES_AGENT_HOME or PYTHONPATH, for example:
  docker exec hermes-kg-full-tests python -m unittest discover \
      -s /app/plugins/kg/tests -p test_hermes_route.py -v

This is an HTTP/SDK contract test, not a real model or extraction quality test.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


def _core_root():
    candidates=[os.environ.get('HERMES_AGENT_HOME'),*sys.path]
    for candidate in candidates:
        if candidate and (Path(candidate)/'agent'/'auxiliary_client.py').is_file():
            return Path(candidate).resolve()
    return None


CORE_ROOT=_core_root()
PLUGIN_ROOT=Path(__file__).resolve().parents[2]

_CHILD=r'''
import json, os, socket, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sandbox=Path.cwd().resolve()
actual_home=Path(os.environ['KG_TEST_REAL_HOME']).resolve()
core=Path(os.environ['HERMES_AGENT_HOME']).resolve()
plugins=Path(sys.argv[1]).resolve()
isolated_os_home=sandbox/'isolated-os-home'
isolated_os_home.mkdir()
selected=sandbox/'selected-profile'
selected.mkdir()
allowed_roots=[sandbox,core,plugins,Path(sys.prefix).resolve(),Path(sys.base_prefix).resolve()]

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
    if event!='open' or not isinstance(args[0],(str,bytes,os.PathLike)):
        return
    path=Path(os.fsdecode(args[0])).resolve()
    if within(path,sandbox):
        return
    # Core source may reside inside ~/.hermes, but its .env/config/auth files
    # must never enter this synthetic test. User-home reads outside explicit
    # source/runtime directories are also prohibited.
    if path.name in {'.env','.op.env','config.yaml','auth.json'}:
        raise AssertionError('Non-synthetic profile file access is forbidden')
    if within(path,actual_home) and not any(within(path,root) for root in allowed_roots):
        raise AssertionError('Real user-home access is forbidden')
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
        from agent.auxiliary_client import shutdown_cached_clients
        shutdown_cached_clients()
        import openai
        print(json.dumps({'ok':True,'sdk':openai.__version__,'requests':len(requests),
                          'path':request['path'],'home_bound':True,'dotenv_key_used':True,
                          'external_network':'blocked','user_profile_reads':'blocked'}))
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
                 'PYTHONPATH':os.pathsep.join((str(PLUGIN_ROOT),str(CORE_ROOT))),
                 'HERMES_AGENT_HOME':str(CORE_ROOT),
                 'HERMES_HOME':str(Path(directory)/'deliberately-wrong-profile'),
                 'HOME':str(Path(directory)/'isolated-os-home'),
                 'USERPROFILE':str(Path(directory)/'isolated-os-home'),
                 'KG_TEST_REAL_HOME':str(Path.home()),
                 'NO_PROXY':'127.0.0.1,localhost,::1'}
            if os.name=='nt' and os.environ.get('SYSTEMROOT'):
                env['SYSTEMROOT']=os.environ['SYSTEMROOT']
            result=subprocess.run([sys.executable,'-c',_CHILD,str(PLUGIN_ROOT)],
                                  cwd=directory,env=env,text=True,capture_output=True,timeout=45)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            report=json.loads(result.stdout.strip().splitlines()[-1])
            self.assertTrue(report['ok'])
            self.assertEqual(report['requests'],1)
            self.assertEqual(report['external_network'],'blocked')
            self.assertEqual(report['user_profile_reads'],'blocked')
            self.assertTrue(report['home_bound'])
            self.assertTrue(report['dotenv_key_used'])


if __name__=='__main__':
    unittest.main()
