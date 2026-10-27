"""Portable console setup/serve/diagnostics and namespace-scoped maintenance."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import uuid
import time
from pathlib import Path

from .config import env_value,load_config,resolve_home


def _core(home):
    from .hermes_runtime import discover_core
    core=discover_core(home)
    if core:
        sys.path.insert(0,str(core))
        return str(core)
    return None


def setup(home,*,name='我',skip_user=False,seed_history=True):
    home=resolve_home(home)
    from .maintenance import assert_not_restoring
    assert_not_restoring(home)
    home.mkdir(parents=True,exist_ok=True)
    path=home/'kg.json'
    fresh=not path.exists()
    marker=home/'kg-console.json'
    onboarding=json.loads(marker.read_text(encoding='utf-8')) if marker.exists() else {
        'format':1,'new_install':fresh,'ready':False,'history_seeded':not fresh,'capture_started_at':time.time()}
    def save_marker():
        temporary=marker.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(onboarding,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(temporary,marker)
    save_marker()
    if fresh:
        # Independent fresh profiles must not claim each other's entities when
        # they share a Neo4j database. Explicit and existing namespaces stay put.
        prefix=env_value('urn_prefix')
        owner=env_value('user_person_urn')
        if prefix is None and owner:
            match=re.fullmatch(r'(urn:.*:)person[-:][A-Za-z0-9][A-Za-z0-9_.-]*',owner)
            if match:
                prefix=match.group(1)
        if prefix is None:
            prefix='urn:kg-'+uuid.uuid4().hex+':kg:'
        data={'enabled':False,'bolt_url':env_value('neo4j_url','bolt://127.0.0.1:17687'),
              'user':env_value('user','neo4j'),'database':env_value('database','neo4j'),
              'urn_prefix':prefix,'user_person_urn':owner or prefix+'person-'+uuid.uuid4().hex[:12],
              'timezone':env_value('timezone','Asia/Shanghai'),'default_shared':False}
        # Keep credentials in the environment or the user's pre-existing file.
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as file: json.dump(data,file,ensure_ascii=False,indent=2)
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
    from .ontology_presets import initialize_personal_preset
    from .search import HybridSearch
    with KGStore(config) as store:
        # Pure database DDL precedes namespace claiming. migrate() itself is a
        # namespace writer, so default preset installation must happen first.
        store.ensure_schema()
        preset_result=initialize_personal_preset(store)
        ontology_preset={key:preset_result[key] for key in ('status','installed','preset','version','idempotent')
                         if key in preset_result}
        preview=migrate(store)
        if not skip_user:
            exists=store.run_read('MATCH (n:Person {uri:$uri}) RETURN n.uri AS uri',{'uri':config.user_person_urn})
            if not exists:
                store.write_batch([{'type':'Person','id':config.user_person_urn,'name':name,'props':{'shared':False}}],[],origin='manual',operation_id='setup-user-'+uuid.uuid4().hex)
        indexes=HybridSearch(store).ensure_indexes()
    from .console_web import console_token
    console_token(home)
    if onboarding['new_install']:
        data=json.loads(path.read_text(encoding='utf-8'))
        data['enabled']=True
        temporary=path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.chmod(0o600)
        os.replace(temporary,path)
    onboarding['ready']=True
    save_marker()
    return {'ok':True,'profile':str(home),'fresh':fresh,'migration':preview,'indexes':indexes,'seeded_sessions':seeded,
            'ontology_preset':ontology_preset,
            'next':'Set memory.provider: kg in Hermes config.yaml, restart the gateway, and run serve.'}


def doctor(home):
    config=load_config(home=home)
    from .store import KGStore
    from .health import check_health
    required_packages=('neo4j','rdflib','fastapi')
    semantic_packages=('fastembed','onnxruntime')
    result={'profile':str(home),'python':sys.version.split()[0],'hermes_core':_core(home),
            'transcript_present':(home/'state.db').exists(),'packages':{name:importlib.util.find_spec(name) is not None for name in ('neo4j','rdflib','fastapi','fastembed','onnxruntime')}}
    result['capabilities']={'semantic_dependencies_available':all(result['packages'][name] for name in semantic_packages)}
    try:
        with KGStore(config) as store: result['database']=check_health(config,store)
    except Exception as exc:
        result['database']={'connected':False,'error_type':type(exc).__name__}
    result['ok']=result['database'].get('connected',False) and all(result['packages'][name] for name in required_packages)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home',help='Explicit memory profile home (or set KG_HOME / HERMES_HOME)')
    sub=parser.add_subparsers(dest='command',required=True)
    install=sub.add_parser('setup');install.add_argument('--name',default='我');install.add_argument('--skip-user',action='store_true')
    sub.add_parser('doctor')
    serving=sub.add_parser('serve');serving.add_argument('--host',default='127.0.0.1');serving.add_argument('--port',type=int,default=8765);serving.add_argument('--container',action='store_true')
    preview=sub.add_parser('migrate');preview.add_argument('--apply',action='store_true')
    for name in ('backup','restore'):
        sub.add_parser(name).add_argument('path')
    sub.add_parser('prepare-model')
    backfill=sub.add_parser('backfill');backfill.add_argument('session_ids',nargs='+')
    ontology=sub.add_parser('ontology')
    ontology_commands=ontology.add_subparsers(dest='ontology_command',required=True)
    preset=ontology_commands.add_parser('install-preset')
    preset.add_argument('preset',choices=['personal'])
    preset.add_argument('--dry-run',action='store_true')
    reclassification=ontology_commands.add_parser('reclassify',help='Validate a private evidence-bound classification plan')
    reclassification.add_argument('--plan',required=True)
    reclassification.add_argument('--apply',action='store_true')
    reclassification.add_argument('--backup',help='New backup archive path; required when applying')
    args=parser.parse_args(argv)
    home=resolve_home(args.home)
    os.environ['KG_HOME']=str(home)
    os.environ['HERMES_HOME']=str(home)
    core=_core(home)
    try:
        # Local maintenance and serving must not load the host agent runtime.
        # Model adapters prepare the selected profile only when a model is used.
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
            if not marker.exists() or not json.loads(marker.read_text(encoding='utf-8')).get('ready'):
                setup(home,seed_history=True)
            if env_value('demo_mode')=='1':
                from .demo import seed_demo
                seed_demo(load_config(home=home))
            import uvicorn
            from .console_web import create_app
            print('Hermes Memory Console: http://127.0.0.1:'+str(args.port))
            print('Access token: stored in <profile>/kg-console.token (or KG_CONSOLE_TOKEN / HERMES_KG_CONSOLE_TOKEN)')
            uvicorn.run(create_app(load_config(home=home)),host=args.host,port=args.port,log_level='info')
            return 0
        elif args.command=='prepare-model':
            from .embedding import LocalEmbedder
            from .search import HybridSearch
            from .store import KGStore
            embedder=LocalEmbedder(env_value('model_cache',str(home/'cache'/'kg-models')))
            result=embedder.prepare(allow_download=True)
            with KGStore(load_config(home=home)) as store:
                search=HybridSearch(store,embedder);search.ensure_indexes();result['indexing']=search.reindex()
        elif args.command=='backfill':
            from .sync_state import SyncState
            result=SyncState(home).request_backfill(args.session_ids)
        elif args.command=='ontology':
            from .store import KGStore
            from .ontology_presets import install_preset
            with KGStore(load_config(home=home)) as store:
                if args.ontology_command=='install-preset':
                    result=install_preset(store,args.preset,dry_run=args.dry_run)
                else:
                    from .reclassification import reclassify_memories
                    plan=json.loads(Path(args.plan).read_text(encoding='utf-8-sig'))
                    result=reclassify_memories(store,plan,dry_run=True)
                    if args.apply:
                        if not args.backup:
                            raise ValueError('--backup is required when applying a classification plan')
                        if not result['idempotent']:
                            from .maintenance import backup
                            backup(store,args.backup)
                        result=reclassify_memories(store,plan,dry_run=False)
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
