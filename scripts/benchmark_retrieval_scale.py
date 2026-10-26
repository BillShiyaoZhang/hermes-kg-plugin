#!/usr/bin/env python3
"""Real legacy-E5/HybridSearch HTTP scale probe on disposable synthetic facts.

The only permitted database target is the dedicated loopback research port.
No LLM, real snapshot, production profile, existing index deletion or model
download is involved. This is baseline engineering evidence, not model quality
or HTTP promotion evidence for a new embedding profile/representation.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
import ctypes
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import socket
import statistics
import struct
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlparse
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins'))
PREFIX = re.compile(r'^urn:researchscale:[0-9a-f]{32}:n(?:1000|10000):$')
TOPICS = ('lunar rover battery thermal control', 'marine biodiversity survey instruments',
          'memory graph visual interaction', 'urban transport signal planning',
          'solar panel inverter maintenance', 'clinical trial document indexing',
          'water quality sensor calibration', 'satellite orbit simulation',
          'distributed database backup recovery', 'wildlife acoustic monitoring')


def assert_namespace(value):
    if not isinstance(value, str) or not PREFIX.fullmatch(value):
        raise ValueError('Refusing non-owned researchscale namespace')
    return value


def fixtures(size, namespace):
    assert_namespace(namespace)
    nodes = []
    for index in range(size):
        props = {'uri': namespace + f'concept-{index:06d}', 'hermes_name': f'Synthetic record {index:06d}',
                 'hermes_description': f'Research item {index:06d}: {TOPICS[index % len(TOPICS)]}. '
                    f'Independent evidence series {index // 10:05d}; section {index % 17}; '
                    '测试用合成记录，包含经核对的工程观察和实施说明。',
                 'hermes_revision': 1, 'hermes_updatedAt': '2026-10-24T00:00:00Z',
                 'hermes_origin': 'manual', 'shared': False}
        nodes.append(props)
    # Reserve the last ten facts as truly isolated. Exactly 3*N distinct stored
    # directed edges remain: hub, ring, and self/parallel cases among active IDs.
    connected = size - 10
    edges = []
    for ordinal in range(size):
        source = ordinal % connected
        targets = (0, (source + 1) % connected,
                   source if ordinal % 41 == 0 else (0 if ordinal % 17 == 0 else (source + 7) % connected))
        for slot, target in enumerate(targets):
            edges.append({'source': nodes[source]['uri'], 'target': nodes[target]['uri'],
                          'ordinal': ordinal * 3 + slot})
    return nodes, edges


def derived_rows(namespace, props, chunks, vectors, model_revision, embedding_revision):
    """Exact current search._index_sources metadata contract, bulk persisted."""
    from kg.search import _json, entity_fingerprint
    from kg.embedding import normalized_vector
    assert_namespace(namespace)
    fingerprint = entity_fingerprint(props)
    revision, updated = props.get('hermes_revision', 0), str(props.get('hermes_updatedAt') or '')
    rows = []
    if len(chunks) != len(vectors):
        raise ValueError('Embedding count mismatch')
    for chunk, vector in zip(chunks, vectors):
        identity = hashlib.sha256(_json([namespace, props['uri'], revision, updated, fingerprint,
                                        model_revision, embedding_revision, chunk.index]).encode()).hexdigest()
        rows.append({'id': identity, 'source_uri': props['uri'], 'source_revision': revision,
                     'source_updated_at': updated, 'source_fingerprint': fingerprint,
                     'namespace': namespace, 'text': chunk.text, 'chunk_index': chunk.index,
                     'token_count': chunk.token_count, 'model_revision': model_revision,
                     'embedding_revision': embedding_revision, 'embedding_state': 'ready',
                     'embedding': normalized_vector(vector)})
    return rows


def percentile(values, proportion):
    return sorted(values)[max(0, math.ceil(len(values) * proportion) - 1)] if values else None


def summary(values):
    return {'samples': len(values), 'p50_ms': percentile(values, .5), 'p95_ms': percentile(values, .95),
            'p99_ms': percentile(values, .99),
            'min_ms': min(values) if values else None, 'max_ms': max(values) if values else None,
            'mean_ms': statistics.fmean(values) if values else None}


def process_memory():
    if os.name != 'nt':
        import resource
        return {'peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)}
    class Counters(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong),
                    ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                    ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                    ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)]
    counters = Counters(); counters.cb = ctypes.sizeof(counters)
    get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
    get_memory.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    get_memory.restype = ctypes.c_int
    if not get_memory(ctypes.c_void_p(-1), ctypes.byref(counters), counters.cb):
        return {'unavailable': True}
    return {'rss_bytes': counters.WorkingSetSize, 'peak_rss_bytes': counters.PeakWorkingSetSize,
            'private_commit_bytes': counters.PagefileUsage}


def write_report(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.pending.json')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
    os.replace(temporary, path)


@contextmanager
def console_server(config, search, token, measurement):
    import httpx
    import uvicorn
    from kg.console_web import create_app
    from kg.lifecycle import MemoryService
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    app = create_app(config, token=token, memory=MemoryService(config, store=search.store),
                     search=search, background=False)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
                                         log_level='error', access_log=False))
    start = time.perf_counter()
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, name='researchscale-console', daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{port}'
    try:
        deadline = time.monotonic() + 30
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError('Synthetic Console failed to start')
            time.sleep(.02)
        measurement['http_server_startup_ms'] = (time.perf_counter() - start) * 1000
        with httpx.Client(base_url=base, trust_env=False, timeout=180,
                          headers={'Authorization': 'Bearer ' + token}) as client:
            yield client, base
    finally:
        server.should_exit = True
        thread.join(30)
        sock.close()
        measurement['server_stopped'] = not thread.is_alive()
        if thread.is_alive():
            raise RuntimeError('Synthetic server did not stop')


def workload_question(ordinal, size, namespace):
    mode = ordinal % 10
    if mode == 0:
        return namespace + f'concept-{(ordinal * 47) % size:06d}'
    if mode == 1:
        return 'Synthetic record ' + f'{(ordinal * 47) % size:06d}'
    if mode == 2:
        return '合成记录 工程观察 实施说明'
    if mode == 3:
        return 'No documented result for imaginary crystal teleportation'
    return TOPICS[(ordinal * 7) % len(TOPICS)]


def request_once(client, question, namespace):
    tick = time.perf_counter()
    response = client.post('/api/v1/search', json={'question': question, 'limit': 10, 'scope': 'profile'})
    elapsed = (time.perf_counter() - tick) * 1000
    response.raise_for_status()
    body = response.json()
    if any(not row['uri'].startswith(namespace) for row in body.get('kg_results', [])):
        raise RuntimeError('Cross-namespace result in synthetic workload')
    warnings=body.get('warnings',[])
    if not isinstance(warnings,list):warnings=['invalid_warning_shape']
    warnings=[value for value in warnings if isinstance(value,str)]
    mode=body.get('mode');semantic=body.get('freshness',{}).get('semantic')
    info={'http_status':response.status_code,'warnings':warnings,'mode':mode,
          'semantic_freshness':semantic,'clean_hybrid':mode=='hybrid' and semantic=='current' and not warnings}
    return elapsed, len(body.get('kg_results', [])), info


def execute_scale(size, args, report, embedder):
    from kg.config import KGConfig
    from kg.store import KGStore
    from kg.search import HybridSearch, entity_document
    namespace = f'urn:researchscale:{uuid.uuid4().hex}:n{size}:'
    assert_namespace(namespace)
    temp_parent = Path(args.temp_root).resolve()
    temp_parent.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix='researchscale-', dir=temp_parent)).resolve()
    if home.parent != temp_parent or not home.name.startswith('researchscale-'):
        raise RuntimeError('Unsafe synthetic profile path')
    config = KGConfig(home=home, neo4j_url=args.bolt_url, password=os.environ['KG_RESEARCH_PASSWORD'],
                      urn_prefix=namespace, user_person_urn=namespace + 'person-self', query_timeout=args.query_timeout)
    store = KGStore(config)
    search = HybridSearch(store, embedder)
    item = {'entities': size, 'requested_edges': size * 3, 'namespace': namespace,
            'status': 'running', 'measurement': {}, 'resources_before': process_memory(),
            'cleanup': {'complete': False}, 'index_contract': 'current search._index_sources ID/source metadata; bulk writes only'}
    item['database_query_timeout_seconds']=config.query_timeout
    report['scales'].append(item)
    save = lambda: write_report(args.output, report)
    def stage(name):
        item['stage']=name;save()
    save()
    try:
        stage('index_schema_setup')
        start = time.perf_counter();store.ensure_schema();search.ensure_indexes()
        item['index_schema_setup_ms'] = (time.perf_counter() - start) * 1000
        nodes, edges = fixtures(size, namespace)
        stage('fixture_write');start = time.perf_counter()
        with store.driver.session(database=config.database) as session:
            for offset in range(0, len(nodes), 250):
                session.run('UNWIND $rows AS props CREATE (n:KGEntity:Concept) SET n=props', {'rows': nodes[offset:offset+250]}).consume()
            for offset in range(0, len(edges), 500):
                session.run('''UNWIND $rows AS row
                    MATCH (a:KGEntity {uri:row.source}),(b:KGEntity {uri:row.target})
                    CREATE (a)-[:RELATED_TO {research_ordinal:row.ordinal}]->(b)''',
                    {'rows': edges[offset:offset+500]}).consume()
        item['fixture_write_ms'] = (time.perf_counter() - start) * 1000
        stage('real_embedding');start = time.perf_counter(); text_hashes, vector_hashes = set(), set(); chunk_count = 0
        encoding_seconds = write_seconds = 0
        for offset in range(0, size, 128):
            sources = nodes[offset:offset+128]
            chunks_by_source = [embedder.chunk(entity_document(props)) for props in sources]
            texts = [chunk.text for chunks in chunks_by_source for chunk in chunks]
            text_hashes.update(hashlib.sha256(text.encode()).hexdigest() for text in texts)
            tick = time.perf_counter();vectors = embedder.embed_passages(texts);encoding_seconds += time.perf_counter()-tick
            if len(vectors) != len(texts):raise RuntimeError('Real embedding batch mismatch')
            for vector in vectors:
                vector_hashes.add(hashlib.sha256(struct.pack('<'+'d'*len(vector),*vector)).hexdigest())
            rows=[];position=0
            for props,chunks in zip(sources,chunks_by_source):
                rows.extend(derived_rows(namespace,props,chunks,vectors[position:position+len(chunks)],
                                         embedder.model_revision,embedder.embedding_revision))
                position+=len(chunks)
            tick=time.perf_counter()
            with store.driver.session(database=config.database) as session:
                session.run('UNWIND $rows AS row CREATE (c:KGSearchChunk) SET c=row',{'rows':rows}).consume()
            write_seconds+=time.perf_counter()-tick;chunk_count+=len(rows)
            if offset % 512 == 0:
                print(json.dumps({'phase':'real_embedding','entities':size,'completed':offset+len(sources)}),flush=True)
        item['index_build']={'elapsed_ms':(time.perf_counter()-start)*1000,'actual_model_encode_ms':encoding_seconds*1000,
                             'bulk_chunk_write_ms':write_seconds*1000,'chunks':chunk_count,'independent_text_sha256_count':len(text_hashes),
                             'distinct_vector_sha256_count':len(vector_hashes),'one_vector_reused':False}
        if len(text_hashes)!=size or len(vector_hashes)!=size:raise RuntimeError('Expected independent unique text and vector per fact')
        stage('index_verify')
        with store.driver.session(database=config.database) as session:
            session.run('CALL db.awaitIndexes(120)').consume()
            integrity=session.run('''MATCH (n:KGEntity) WHERE n.uri STARTS WITH $prefix
                OPTIONAL MATCH (n)-[r]->(m) WHERE m.uri STARTS WITH $prefix
                RETURN count(DISTINCT n) AS nodes,count(r) AS edges''',{'prefix':namespace}).single()
            isolated=session.run('''MATCH(n:KGEntity) WHERE n.uri STARTS WITH $prefix AND NOT (n)--() RETURN count(n) AS total''',{'prefix':namespace}).single()['total']
        item['facts_verified']={'nodes':integrity['nodes'],'edges':integrity['edges'],'isolated':isolated}
        if (integrity['nodes'],integrity['edges'],isolated)!=(size,size*3,10):raise RuntimeError('Synthetic fixture count mismatch')
        status=search.status();item['index_status_before_http']=status
        if status.get('pending_count')!=0 or not status.get('model_ready'):raise RuntimeError('Baseline index not ready')
        item['resources_after_index']=process_memory();save()
        if args.measurement_gate:
            stage('waiting_for_idle_host')
            print(json.dumps({'phase':'waiting_for_idle_host','entities':size}),flush=True)
            while not args.measurement_gate.exists():time.sleep(1)
        stage('http_server_startup')
        with console_server(config,search,secrets.token_urlsafe(32),item['measurement']) as (client,base):
            stage('first_http_query')
            cold,_,cold_info=request_once(client,workload_question(4,size,namespace),namespace)
            item['measurement']['first_http_query_ms']=cold
            item['measurement']['first_http_query_details']=cold_info
            stage('http_warmup')
            warmup=[]
            for index in range(args.warmup):
                latency,count,info=request_once(client,workload_question(index,size,namespace),namespace)
                warmup.append({'ordinal':index,'latency_ms':latency,'result_count':count,**info})
            item['measurement']['warmup_requests']=args.warmup
            item['measurement']['warmup_details']=warmup
            item['measurement']['warmup_degraded_responses']=sum(not event['clean_hybrid'] for event in warmup)
            for concurrency in (1,4):
                key=f'concurrency_{concurrency}'
                item['measurement'][key]={'status':'running','samples_requested':args.requests,
                    'samples_completed':0,'concurrency':concurrency,'http_errors':0,'raw_requests':[]}
                stage(f'http_concurrency_{concurrency}')
                tick=time.perf_counter();events=[]
                def measured(ordinal):
                    started=time.perf_counter()
                    try:
                        elapsed,count,info=request_once(client,workload_question(ordinal,size,namespace),namespace)
                        return {'ordinal':ordinal,'latency_ms':elapsed,'ok':True,'result_count':count,**info}
                    except Exception as exc:
                        return {'ordinal':ordinal,'latency_ms':(time.perf_counter()-started)*1000,
                                'ok':False,'error_type':type(exc).__name__,
                                'http_status':getattr(getattr(exc,'response',None),'status_code',None)}
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    futures=[pool.submit(measured,ordinal) for ordinal in range(args.requests)]
                    for future in as_completed(futures):
                        events.append(future.result())
                        if len(events)%20==0 or len(events)==args.requests:
                            item['measurement'][key].update(samples_completed=len(events),
                                elapsed_seconds=time.perf_counter()-tick,
                                http_errors=sum(not event['ok'] for event in events),
                                degraded_responses=sum(event['ok'] and not event['clean_hybrid'] for event in events),
                                raw_requests=sorted(events,key=lambda event:event['ordinal']))
                            print(json.dumps({'phase':'http_progress','entities':size,'concurrency':concurrency,
                                              'completed':len(events),'requested':args.requests}),flush=True);save()
                good=[event for event in events if event['ok']]
                item['measurement'][key]={**summary([event['latency_ms'] for event in events]),
                    'elapsed_seconds':time.perf_counter()-tick,'concurrency':concurrency,
                    'status':'complete','samples_requested':args.requests,'samples_completed':len(events),
                    'http_errors':len(events)-len(good),'expected_mode':'hybrid',
                    'mode_counts':{mode:sum(str(event.get('mode'))==mode for event in good)
                                   for mode in sorted({str(event.get('mode')) for event in good})},
                    'degraded_responses':sum(not event['clean_hybrid'] for event in good),
                    'warning_counts':{warning:sum(warning in event.get('warnings',[]) for event in events)
                                      for warning in sorted({w for event in events for w in event.get('warnings',[])})},
                    'result_count_min':min((event['result_count'] for event in good),default=None),
                    'result_count_max':max((event['result_count'] for event in good),default=None),
                    'raw_requests':sorted(events,key=lambda event:event['ordinal'])}
                print(json.dumps({'phase':'measured','entities':size,'concurrency':concurrency,
                                  'summary':{k:v for k,v in item['measurement'][key].items()
                                             if k!='raw_requests'}}),flush=True);save()
        item['resources_after_http']=process_memory();item['status']='complete';stage('complete')
    except BaseException as exc:
        item['status']='failed';item['error_type']=type(exc).__name__
        # Texts/credentials/URLs do not enter the public error artifact.
        raise
    finally:
        cleanup_errors=[]
        try:
            assert_namespace(namespace)
            with store.driver.session(database=config.database) as session:
                session.run('MATCH (n) WHERE n.uri STARTS WITH $prefix OR n.namespace=$prefix DETACH DELETE n',{'prefix':namespace}).consume()
                remaining=session.run('MATCH(n) WHERE n.uri STARTS WITH $prefix OR n.namespace=$prefix RETURN count(n) AS total',{'prefix':namespace}).single()['total']
            item['cleanup']['remaining_nodes_and_chunks']=remaining
        except Exception as exc:cleanup_errors.append(type(exc).__name__)
        try:store.close()
        except Exception as exc:cleanup_errors.append(type(exc).__name__)
        try:
            resolved=home.resolve()
            if resolved.parent!=temp_parent or not resolved.name.startswith('researchscale-'):raise RuntimeError('Unsafe cleanup path')
            shutil.rmtree(resolved)
            item['cleanup']['profile_removed']=not resolved.exists()
        except Exception as exc:cleanup_errors.append(type(exc).__name__)
        item['cleanup'].update(errors=cleanup_errors,complete=not cleanup_errors and
            item['cleanup'].get('remaining_nodes_and_chunks')==0 and item['cleanup'].get('profile_removed') is True)
        save()
    return item


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bolt-url',default='bolt://127.0.0.1:28687')
    parser.add_argument('--model-cache',type=Path,default=ROOT/'.model-cache')
    parser.add_argument('--output',type=Path,default=ROOT/'reports/retrieval-research/scale-baseline.json')
    parser.add_argument('--temp-root',type=Path,default=ROOT/'reports/retrieval-research/scale-temp')
    parser.add_argument('--sizes',type=int,nargs='+',choices=(1000,10000),default=[1000,10000])
    parser.add_argument('--requests',type=int,default=200)
    parser.add_argument('--warmup',type=int,default=20)
    parser.add_argument('--query-timeout',type=float,default=10,
                        help='Business DB query budget; default matches production KGConfig (seconds)')
    parser.add_argument('--measurement-gate',type=Path)
    args=parser.parse_args(argv)
    url=urlparse(args.bolt_url)
    if url.scheme!='bolt' or url.hostname not in {'127.0.0.1','localhost'} or url.port!=28687:
        parser.error('Only the explicit disposable loopback Neo4j port 28687 is permitted')
    if not os.environ.get('KG_RESEARCH_PASSWORD'):parser.error('Set dedicated KG_RESEARCH_PASSWORD')
    if args.requests<200 or args.warmup<10:parser.error('Use at least 200 measured requests per concurrency and 10 warmups')
    if not math.isfinite(args.query_timeout) or not 0<args.query_timeout<=120:parser.error('query-timeout must be finite and 0..120 seconds')
    logging.getLogger('neo4j.notifications').setLevel(logging.ERROR)
    for name in tuple(os.environ):
        if name.startswith(('KG_','HERMES_KG_','NEO4J_')) and name!='KG_RESEARCH_PASSWORD':os.environ.pop(name,None)
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    report={'format':'kg-current-legacy-e5-scale-v1','status':'running','scales':[],
            'tool_source_sha256':{str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (Path(__file__).resolve(),ROOT/'plugins/kg/search.py',ROOT/'plugins/kg/console_web.py',ROOT/'plugins/kg/embedding.py')},
            'baseline':'current legacy E5-small 384d / entity_document / HybridSearch / actual create_app HTTP',
            'new_candidate_promotion_evidence':False,'llm_calls':0,'synthetic_only':True,
            'model_downloads':False,'host':{'processor':platform.processor(),'logical_cpus':os.cpu_count(),
                                          'declared_cpu':'Intel Core i7-12700','declared_ram_gib':64},
            'database_limits':{'declared_cpus':4,'declared_ram_gib':4,'dedicated_loopback_port':28687},
            'memory_before':process_memory(),'interpretation':'Single engineering run per scale/concurrency; warm p95 uses real HTTP requests. Cold model prepare, first HTTP and index work are reported separately. No quality or candidate-promotion conclusion.'}
    write_report(args.output,report)
    try:
        tick=time.perf_counter()
        from kg.embedding import LocalEmbedder
        embedder=LocalEmbedder(args.model_cache,threads=2)
        embedder.prepare(allow_download=False)
        report['cold_model_prepare_ms']=(time.perf_counter()-tick)*1000
        report['model']={**embedder.status(),'threads':2,'fingerprint':embedder.fingerprint,'backend':'real fastembed ONNX CPU'}
        for size in args.sizes:execute_scale(size,args,report,embedder)
        report['status']='complete'
        report['ok']=all(s['cleanup']['complete'] and s['measurement']['first_http_query_details']['clean_hybrid']
            and s['measurement']['warmup_degraded_responses']==0 and all(s['measurement'][f'concurrency_{c}']['http_errors']==0
            and s['measurement'][f'concurrency_{c}']['degraded_responses']==0 for c in (1,4)) for s in report['scales'])
    except BaseException as exc:
        report['status']='failed';report['error_type']=type(exc).__name__
        print(json.dumps({'phase':'failed','error_type':type(exc).__name__}),flush=True)
        raise
    finally:write_report(args.output,report)
    print(json.dumps({'status':report['status'],'scales':len(report['scales']),'cleanup':all(s['cleanup']['complete'] for s in report['scales'])}),flush=True)
    return 0 if report['ok'] else 1


if __name__=='__main__':raise SystemExit(main())
