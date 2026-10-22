"""Portable console setup/serve/diagnostics and namespace-scoped maintenance."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import uuid
import time
from pathlib import Path

from .config import load_config,resolve_home


def _core(home):
    candidates=[os.environ.get('HERMES_AGENT_HOME'),str(home/'hermes-agent'),str(Path.home()/'.hermes'/'hermes-agent')]
    for candidate in candidates:
        if candidate and (Path(candidate)/'agent'/'memory_provider.py').exists():
            sys.path.insert(0,str(Path(candidate).resolve()))
            return str(Path(candidate).resolve())
    return None


def setup(home,*,name='我',skip_user=False,seed_history=True):
    home=resolve_home(home)
    from .maintenance import assert_not_restoring
    assert_not_restoring(home)
    home.mkdir(parents=True,exist_ok=True)
    path=home/'kg.json'
    fresh=not path.exists()
    marker=home/'kg-console.json'
    onboarding=json.loads(marker.read_text()) if marker.exists() else {
        'format':1,'new_install':fresh,'ready':False,'history_seeded':not fresh,'capture_started_at':time.time()}
    def save_marker():
        temporary=marker.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(onboarding,ensure_ascii=False,indent=2))
        os.replace(temporary,marker)
    save_marker()
    if fresh:
        prefix=os.environ.get('HERMES_KG_URN_PREFIX','urn:hermes:kg:')
        data={'enabled':False,'bolt_url':os.environ.get('HERMES_KG_BOLT_URL','bolt://127.0.0.1:17687'),
              'user':os.environ.get('HERMES_KG_USER','neo4j'),'database':'neo4j',
              'urn_prefix':prefix,'user_person_urn':os.environ.get('HERMES_KG_USER_PERSON_URN',prefix+'person-'+uuid.uuid4().hex[:12]),
              'timezone':'Asia/Shanghai','default_shared':False}
        # Keep credentials in the environment or the user's pre-existing file.
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as file: json.dump(data,file,ensure_ascii=False,indent=2)
    seeded=0
    if onboarding['new_install'] and not onboarding.get('history_seeded'):
        if seed_history and (home/'state.db').exists():
            from .sync_state import SyncState
            seeded=SyncState(home).seed_existing_history(cutoff=onboarding['capture_started_at'])
            onboarding['history_seeded']=True
        elif not seed_history:
            onboarding['history_seeded']=True
        save_marker()
    config=load_config(home=home)
    from .store import KGStore
    from .maintenance import migrate
    from .search import HybridSearch
    with KGStore(config) as store:
        preview=migrate(store)
        if not skip_user:
            exists=store.run_read('MATCH (n:Person {uri:$uri}) RETURN n.uri AS uri',{'uri':config.user_person_urn})
            if not exists:
                store.write_batch([{'type':'Person','id':config.user_person_urn,'name':name,'props':{'shared':False}}],[],origin='manual',operation_id='setup-user-'+uuid.uuid4().hex)
        indexes=HybridSearch(store).ensure_indexes()
    from .console_web import console_token
    console_token(home)
    if onboarding['new_install']:
        data=json.loads(path.read_text())
        data['enabled']=True
        temporary=path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2))
        temporary.chmod(0o600)
        os.replace(temporary,path)
    onboarding['ready']=True
    save_marker()
    return {'ok':True,'profile':str(home),'fresh':fresh,'migration':preview,'indexes':indexes,'seeded_sessions':seeded,
            'next':'Set memory.provider: kg in Hermes config.yaml, restart the gateway, and run serve.'}


def doctor(home):
    config=load_config(home=home)
    from .store import KGStore
    from .health import check_health
    result={'profile':str(home),'python':sys.version.split()[0],'hermes_core':_core(home),
            'transcript_present':(home/'state.db').exists(),'packages':{name:importlib.util.find_spec(name) is not None for name in ('neo4j','rdflib','fastapi','fastembed','onnxruntime')}}
    try:
        with KGStore(config) as store: result['database']=check_health(config,store)
    except Exception as exc:
        result['database']={'connected':False,'error_type':type(exc).__name__}
    result['ok']=result['database'].get('connected',False) and all(result['packages'].values())
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home',help='Explicit Hermes profile home')
    sub=parser.add_subparsers(dest='command',required=True)
    install=sub.add_parser('setup');install.add_argument('--name',default='我');install.add_argument('--skip-user',action='store_true')
    sub.add_parser('doctor')
    serving=sub.add_parser('serve');serving.add_argument('--host',default='127.0.0.1');serving.add_argument('--port',type=int,default=8765);serving.add_argument('--container',action='store_true')
    preview=sub.add_parser('migrate');preview.add_argument('--apply',action='store_true')
    for name in ('backup','restore'):
        sub.add_parser(name).add_argument('path')
    sub.add_parser('prepare-model')
    backfill=sub.add_parser('backfill');backfill.add_argument('session_ids',nargs='+')
    args=parser.parse_args(argv)
    home=resolve_home(args.home)
    os.environ['HERMES_HOME']=str(home)
    core=_core(home)
    try:
        if core:
            from .hermes_runtime import prepare_runtime
            prepare_runtime(home)
        if args.command not in {'restore','doctor'}:
            from .maintenance import assert_not_restoring
            assert_not_restoring(home)
        if args.command=='setup': result=setup(home,name=args.name,skip_user=args.skip_user)
        elif args.command=='doctor': result=doctor(home)
        elif args.command=='serve':
            if (home/'kg-sync'/'restore-in-progress.json').exists():
                raise ValueError('A restore is incomplete; rerun restore with the same backup before starting the console')
            if args.host not in {'127.0.0.1','localhost','::1'} and not args.container:
                raise ValueError('Public serving is not supported; use loopback and SSH forwarding')
            marker=home/'kg-console.json'
            if not marker.exists() or not json.loads(marker.read_text()).get('ready'):
                setup(home,seed_history=True)
            if os.environ.get('HERMES_KG_DEMO_MODE')=='1':
                from .demo import seed_demo
                seed_demo(load_config(home=home))
            import uvicorn
            from .console_web import create_app
            print('Hermes Memory Console: http://127.0.0.1:'+str(args.port))
            print('Access token: stored in <profile>/kg-console.token (or HERMES_KG_CONSOLE_TOKEN)')
            uvicorn.run(create_app(load_config(home=home)),host=args.host,port=args.port,log_level='info')
            return 0
        elif args.command=='prepare-model':
            from .embedding import LocalEmbedder
            from .search import HybridSearch
            from .store import KGStore
            embedder=LocalEmbedder(os.environ.get('HERMES_KG_MODEL_CACHE',str(home/'cache'/'kg-models')))
            result=embedder.prepare(allow_download=True)
            with KGStore(load_config(home=home)) as store:
                search=HybridSearch(store,embedder);search.ensure_indexes();result['indexing']=search.reindex()
        elif args.command=='backfill':
            from .sync_state import SyncState
            result=SyncState(home).request_backfill(args.session_ids)
        else:
            from .store import KGStore
            from .maintenance import backup,restore,migrate,migration_preview
            with KGStore(load_config(home=home)) as store:
                if args.command=='migrate': result=migrate(store) if args.apply else migration_preview(store)
                elif args.command=='backup': result=backup(store,args.path)
                else: result=restore(store,args.path)
        print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
        return 0 if result.get('ok',True) else 1
    except Exception as exc:
        # Do not dump upstream connection strings or model request contents.
        print(json.dumps({'ok':False,'error_type':type(exc).__name__,'error':str(exc) if isinstance(exc,ValueError) else 'Operation failed; run doctor for diagnostics'},ensure_ascii=False),file=sys.stderr)
        return 1


if __name__=='__main__': raise SystemExit(main())
