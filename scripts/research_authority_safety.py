"""Synthetic-only, real E5/Neo4j/socket-HTTP authority safety checks.

Requires explicit KG_RESEARCH_BOLT_URL and KG_RESEARCH_PASSWORD. Only a
loopback Neo4j on port 28687 is accepted. No default profile is loaded, no model
is downloaded, and no production configuration or frozen research code changes.
The publisher is deliberately in-memory: this is not an atomic production
index-generation manager, ANN benchmark, installed-provider proof or promotion.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import platform
import re
import secrets
import socket
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins'))


def options(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-cache', type=Path, default=ROOT / '.model-cache')
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/retrieval-research/authority-safety.json')
    return parser.parse_args(argv)


def target():
    url, password = os.environ.get('KG_RESEARCH_BOLT_URL'), os.environ.get('KG_RESEARCH_PASSWORD')
    if not url or not password:
        raise ValueError('Explicit research Bolt URL and password are required')
    parsed = urlparse(url)
    if (parsed.scheme not in {'bolt', 'neo4j'} or parsed.hostname not in {'127.0.0.1', 'localhost'}
            or parsed.port != 28687 or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {'', '/'}):
        raise ValueError('Only the isolated loopback research database on port 28687 is permitted')
    return url, password


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def validate_cleanup_scope(marker, namespace, foreign):
    if (not isinstance(marker, str) or not re.fullmatch(r'[0-9a-f]{32}', marker)
            or namespace != 'urn:researchshadow:' + marker + ':kg:'
            or foreign != 'urn:researchshadow:' + marker + ':foreign:'):
        raise ValueError('Cleanup must target this generated synthetic namespace and marker')


class NotificationCounter(logging.Handler):
    def __init__(self):
        super().__init__()
        self.deprecations = 0
        self.other = 0

    def emit(self, record):
        if 'FeatureDeprecationWarning' in record.getMessage():
            self.deprecations += 1
        else:
            self.other += 1


class ExactBackend:
    def __init__(self, store, embedder, marker):
        self.store, self.embedder, self.marker = store, embedder, marker
        self.generation = 'epoch-a'
        self.candidates, self.vectors = (), ()
        self.hooks = {}
        self.capture_count = 0
        self.encoded_passages = 0
        self.lock = threading.Lock()

    def current_context(self, expected):
        from kg.retrieval_authority import capture_context
        from kg.retrieval_documents import DOCUMENT_REVISION
        with self.lock:
            generation = self.generation
        return capture_context(self.store, scope=expected.scope,
            model_fingerprint=self.embedder.fingerprint, representation='entity_relations',
            projection_revision=DOCUMENT_REVISION, index_generation=generation)

    def capture_context(self, *, scope):
        from kg.retrieval_authority import capture_context
        from kg.retrieval_documents import DOCUMENT_REVISION
        with self.lock:
            self.capture_count += 1
            generation = self.generation
        return capture_context(self.store, scope=scope, model_fingerprint=self.embedder.fingerprint,
            representation='entity_relations', projection_revision=DOCUMENT_REVISION, index_generation=generation)

    def status(self):
        return {'ready': True, 'model_ready': self.embedder.status()['ready'],
                'model': self.embedder.model_name, 'model_fingerprint': self.embedder.fingerprint,
                'dimensions': self.embedder.dimensions, 'representation': 'entity_relations',
                'index_generation': self.generation}

    def snapshot(self):
        from kg.schema import schema_for_store
        params = {'marker': self.marker}
        nodes = self.store.run_read('MATCH (n {research_authority_marker:$marker}) RETURN properties(n) AS properties,labels(n) AS labels ORDER BY n.uri', params)
        edges = self.store.run_read('MATCH (a {research_authority_marker:$marker})-[r]->(b {research_authority_marker:$marker}) RETURN a.uri AS source,b.uri AS target,type(r) AS type,properties(r) AS properties', params)
        return {'namespace': self.store.config.urn_prefix, 'schema': schema_for_store(self.store),
                'entities': nodes, 'relations': [dict(row, **{'from': row['source'], 'to': row['target']}) for row in edges]}

    def refresh(self):
        from kg.retrieval_documents import dataset_to_documents
        self.candidates = tuple(dataset_to_documents(self.snapshot(), 'entity_relations'))
        # One real encoding per distinct card text. No reused or fake vector.
        self.vectors = tuple(self.embedder.embed_passages([doc.text for doc in self.candidates]))
        self.encoded_passages += len(self.vectors)

    def retrieve(self, request, context):
        query = self.embedder.embed_query(request.question)
        scores = [(math.fsum(a * b for a, b in zip(vector, query)), doc)
                  for doc, vector in zip(self.candidates, self.vectors)
                  if context.scope == 'shared' or all(ref.startswith(context.namespace) for ref in doc.source_refs)]
        result = tuple(doc for _, doc in sorted(scores, key=lambda pair: (-pair[0], pair[1].id)))
        hook = self.hooks.get(request.question)
        if hook:
            hook()
        return result


def run(args):
    url, password = target()  # Fail before imports, files, sockets or writes.
    from kg.config import KGConfig
    from kg.embedding import LocalEmbedder
    from kg.retrieval_authority import RetrievalAuthority
    from kg.retrieval_documents import DOCUMENT_REVISION
    from kg.retrieval_shadow import create_shadow_app
    from kg.store import KGStore
    import uvicorn

    marker = uuid.uuid4().hex
    namespace = 'urn:researchshadow:' + marker + ':kg:'
    foreign = 'urn:researchshadow:' + marker + ':foreign:'
    assert namespace.startswith('urn:researchshadow:') and len(marker) == 32
    report = {'format': 'kg-retrieval-authority-v1', 'ok': False, 'synthetic_only': True,
              'backend': 'isolated_shadow_http', 'transport': 'real_loopback_socket_http',
              'source': 'independently_created_synthetic_graph', 'representation': 'entity_relations',
              'scope': 'profile_and_shared_cases', 'projection_revision': DOCUMENT_REVISION,
              'index_generation': 'in_memory_synthetic_publisher_epochs',
              'publisher': 'in_memory_test_publisher_not_production_atomic_generation_manager',
              'real_vectors': True, 'threads': 2, 'device': 'cpu',
              'hardware': {'platform': platform.platform(), 'python': platform.python_version()},
              'cases': {}, 'checks': {}, 'promotion_eligible': False,
              'cleanup': {'complete': False, 'remaining': None},
              'limitations': ['Synthetic safety only; no gold, quality gate, ANN or HTTP performance claim.',
                             'Read-committed observations and publish fences are not serializable isolation.',
                             'Restoration requires reprojecting current content; no production index publisher is implemented.',
                             'This service is independent of the installed Hermes provider and never changes its configuration.']}
    report['tool_code_sha256'] = digest({str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (Path(__file__), ROOT/'plugins/kg/retrieval_authority.py', ROOT/'plugins/kg/retrieval_shadow.py')})
    notification_logger = logging.getLogger('neo4j.notifications')
    old_handlers, old_propagate, old_level = notification_logger.handlers[:], notification_logger.propagate, notification_logger.level
    counter = NotificationCounter()
    notification_logger.handlers, notification_logger.propagate = [counter], False
    notification_logger.setLevel(logging.WARNING)
    server, thread, store = None, None, None
    directory = tempfile.TemporaryDirectory(prefix='researchshadow-')
    try:
        config = KGConfig(home=Path(directory.name), neo4j_url=url, user='neo4j', password=password,
                          database='neo4j', urn_prefix=namespace, enabled=True, query_timeout=5)
        store = KGStore(config)
        store.driver.verify_connectivity()
        embedder = LocalEmbedder(args.model_cache, threads=2)
        prepared_at = time.perf_counter()
        embedder.prepare(allow_download=False)
        report.update(model_fingerprint=embedder.fingerprint,
                      model=embedder.model_name, model_revision=embedder.model_revision,
                      model_prepare_seconds=time.perf_counter()-prepared_at)
        backend = ExactBackend(store, embedder, marker)
        authority = RetrievalAuthority(store, current_context=backend.current_context)

        def write(query, extra=None):
            values = {'marker': marker, 'prefix': namespace, 'foreign': foreign,
                      'person': namespace+'person-ada', 'concept': namespace+'concept-beacon',
                      'shared': foreign+'concept-shared', **(extra or {})}
            with store.driver.session(database='neo4j') as session:
                session.run(query, values).consume()

        def clear():
            # The marker and generated namespace are both checked before delete.
            validate_cleanup_scope(marker, namespace, foreign)
            write('MATCH (n {research_authority_marker:$marker}) WHERE n.uri STARTS WITH $prefix OR n.uri STARTS WITH $foreign OR n.namespace=$prefix DETACH DELETE n')

        def fixture():
            backend.store.config = config
            clear()
            backend.hooks.clear()
            backend.generation = 'epoch-a'
            write('CREATE (a:KGEntity:Person {uri:$person,foaf_name:\'SYNTHETIC_ADA_OLD\',hermes_description:\'SYNTHETIC_PERSON_BODY_OLD\',hermes_revision:0,research_authority_marker:$marker}), (b:KGEntity:Concept {uri:$concept,hermes_name:\'SYNTHETIC_BEACON_OLD\',hermes_description:\'SYNTHETIC_CONCEPT_BODY_OLD\',hermes_revision:0,research_authority_marker:$marker}), (c:KGEntity:Concept {uri:$shared,hermes_name:\'SYNTHETIC_SHARED_OLD\',shared:true,hermes_revision:0,research_authority_marker:$marker}), (a)-[:RELATED_TO {hermes_relLabel:\'SYNTHETIC_QUALIFIER_OLD\'}]->(b), (a)-[:RELATED_TO {hermes_relLabel:\'SYNTHETIC_SHARED_EDGE\'}]->(c)')
            backend.refresh()

        fixture()
        report['dataset_sha256'] = digest(backend.snapshot())
        token = secrets.token_urlsafe(32)
        app = create_shadow_app(backend=backend, authority=authority, token=token)
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        listener.close()
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
            log_level='critical', access_log=False, lifespan='off'))
        thread = threading.Thread(target=server.run, name='research-shadow-http', daemon=True)
        thread.start()
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.05)
        if not server.started:
            raise RuntimeError('Synthetic shadow HTTP startup failed')
        opener = build_opener(ProxyHandler({}))
        base = 'http://127.0.0.1:' + str(port)

        def http(question='Synthetic knowledge safety', *, scope='profile', auth=True, path='/search', body=None):
            data = json.dumps(body or {'question': question, 'scope': scope, 'limit': 20}).encode()
            headers = {'Content-Type': 'application/json'}
            if auth:
                headers['Authorization'] = 'Bearer ' + token
            try:
                with opener.open(Request(base+path, data=data, headers=headers), timeout=30) as response:
                    return response.status, json.load(response)
            except HTTPError as error:
                return error.code, json.load(error)

        def check(name, group, passed):
            report['cases'][name] = {'passed': bool(passed)}
            summary = report['checks'].setdefault(group, {'cases': 0, 'passed': 0, 'failed': 0})
            summary['cases'] += 1
            summary['passed' if passed else 'failed'] += 1

        code, response = http(auth=False)
        check('unauthenticated_rejected', 'authentication', code == 401)
        code, response = http(path='/write')
        check('write_route_absent', 'read_only_routes', code == 404)
        code, response = http(body={'question':'Synthetic', 'interpret':True})
        check('interpretation_rejected', 'read_only_routes', code == 422)

        fixture()
        before = backend.capture_count
        code, response = http()
        check('real_e5_current_cards', 'real_cpu_http', code == 200 and response['status'] == 'found'
              and len(response['evidence']) == 3 and backend.capture_count == before+1
              and response['boundaries']['model_fingerprint'] == embedder.fingerprint)
        check('profile_excludes_foreign_shared', 'namespace_isolation',
              all(all(ref.startswith(namespace) for ref in item['source_refs']) for item in response['evidence']))

        mutations = [
            ('entity_renamed_without_revision', 'revision_race', "MATCH (n {uri:$person}) SET n.foaf_name='SYNTHETIC_ADA_NEW'", 'SYNTHETIC_ADA_OLD', 'profile'),
            ('body_changed_without_revision', 'revision_race', "MATCH (n {uri:$concept}) SET n.hermes_description='SYNTHETIC_BODY_NEW'", 'SYNTHETIC_CONCEPT_BODY_OLD', 'profile'),
            ('entity_archived', 'visibility_change', 'MATCH (n {uri:$concept}) SET n:KGQuarantined', 'SYNTHETIC_BEACON_OLD', 'profile'),
            ('endpoint_disabled', 'visibility_change', 'MATCH (n {uri:$concept}) SET n.hermes_enabled=false', 'SYNTHETIC_BEACON_OLD', 'profile'),
            ('shared_visibility_revoked', 'visibility_change', 'MATCH (n {uri:$shared}) SET n.shared=false', 'SYNTHETIC_SHARED_OLD', 'shared'),
            ('directed_relation_deleted', 'direction', 'MATCH (a {uri:$person})-[r:RELATED_TO]->(b {uri:$concept}) DELETE r', 'SYNTHETIC_QUALIFIER_OLD', 'profile'),
            ('relation_qualifier_changed', 'revision_race', "MATCH (a {uri:$person})-[r:RELATED_TO]->(b {uri:$concept}) SET r.hermes_relLabel='SYNTHETIC_QUALIFIER_NEW'", 'SYNTHETIC_QUALIFIER_OLD', 'profile'),
            ('endpoint_name_changed', 'revision_race', "MATCH (n {uri:$concept}) SET n.hermes_name='SYNTHETIC_BEACON_NEW'", 'SYNTHETIC_BEACON_OLD', 'profile'),
            ('direction_reversed', 'direction', "MATCH (a {uri:$person})-[r:RELATED_TO]->(b {uri:$concept}) DELETE r CREATE (b)-[:RELATED_TO {hermes_relLabel:'SYNTHETIC_REVERSED_NEW'}]->(a)", 'SYNTHETIC_QUALIFIER_OLD', 'profile'),
        ]
        for name, group, query, forbidden, scope in mutations:
            fixture()
            backend.hooks[name] = lambda query=query: write(query)
            code, response = http(name, scope=scope)
            check(name, group, code == 200 and response['status'] == 'incomplete'
                  and forbidden not in json.dumps(response['evidence']) and response['rejected_count'] > 0)

        fixture()
        write('MATCH (n {uri:$concept}) SET n:KGQuarantined')
        backend.hooks['restore_changed_body'] = lambda: write("MATCH (n {uri:$concept}) REMOVE n:KGQuarantined SET n.hermes_description='SYNTHETIC_RESTORED_NEW',n.hermes_revision=1")
        code, response = http('restore_changed_body')
        check('restore_does_not_reopen_stale_body', 'visibility_change', code == 200
              and response['status'] == 'incomplete'
              and 'SYNTHETIC_CONCEPT_BODY_OLD' not in json.dumps(response['evidence']))
        backend.hooks.clear()
        backend.refresh()
        code, response = http()
        check('restored_reprojected_current_body', 'visibility_change', code == 200
              and response['status'] == 'found' and 'SYNTHETIC_RESTORED_NEW' in json.dumps(response['evidence']))

        fixture()
        backend.hooks['schema_changed'] = lambda: write('CREATE (:KGOntologySchema {namespace:$prefix,version:1,patch_json:\'{}\',research_authority_marker:$marker})')
        code, response = http('schema_changed')
        check('schema_publish_withholds_entire_request', 'schema_change', code == 200
              and response['status'] == 'incomplete' and not response['evidence'])
        fixture()
        backend.hooks['epoch_changed'] = lambda: setattr(backend, 'generation', 'epoch-b')
        code, response = http('epoch_changed')
        check('index_generation_switch_withholds_entire_request', 'generation_switch', code == 200
              and response['status'] == 'incomplete' and not response['evidence'])

        fixture()
        backend.hooks['database_failure'] = lambda: setattr(store, 'config', replace(config, database='research_missing_'+marker))
        code, response = http('database_failure')
        check('actual_missing_database_is_unavailable_not_empty', 'database_failure', code == 503
              and response['status'] == 'unavailable' and not response['evidence'])

        fixture()
        captured, release = threading.Event(), threading.Event()
        def old_request_wait():
            captured.set()
            if not release.wait(15):
                raise TimeoutError('Concurrent synthetic request did not release')
        backend.hooks['old_epoch_wait'] = old_request_wait
        with ThreadPoolExecutor(max_workers=2) as pool:
            old = pool.submit(http, 'old_epoch_wait')
            if not captured.wait(15):
                raise TimeoutError('Concurrent synthetic context was not captured')
            backend.generation = 'epoch-b'
            new_code, new_response = http('new_epoch_now')
            release.set()
            old_code, old_response = old.result(timeout=30)
        check('concurrent_requests_keep_distinct_epochs', 'concurrent_context',
              old_code == 200 and old_response['status'] == 'incomplete' and not old_response['evidence']
              and old_response['boundaries']['index_generation'] == 'epoch-a'
              and new_code == 200 and new_response['status'] == 'found'
              and new_response['boundaries']['index_generation'] == 'epoch-b')
        report['encoded_passage_count'] = backend.encoded_passages
        report['cases_total'] = len(report['cases'])
        report['failures'] = sum(not item['passed'] for item in report['cases'].values())
        report['ok'] = report['failures'] == 0
    except Exception as error:
        report['error_type'] = type(error).__name__  # Never raw DB/HTTP error text.
        report['ok'] = False
    finally:
        if server:
            server.should_exit = True
        if thread:
            thread.join(timeout=15)
            report['http_server_stopped'] = not thread.is_alive()
        if store:
            try:
                store.config = config
                # This also works when model preparation failed before the
                # normal fixture helpers were initialized.
                validate_cleanup_scope(marker, namespace, foreign)
                with store.driver.session(database='neo4j') as session:
                    session.run('MATCH (n {research_authority_marker:$marker}) '
                        'WHERE n.uri STARTS WITH $prefix OR n.uri STARTS WITH $foreign OR n.namespace=$prefix '
                        'DETACH DELETE n', {'marker': marker, 'prefix': namespace, 'foreign': foreign}).consume()
                rows = store.run_read('MATCH (n {research_authority_marker:$marker}) RETURN count(n) AS total', {'marker': marker})
                remaining = rows[0]['total']
                report['cleanup'] = {'complete': remaining == 0, 'remaining': remaining}
            except Exception as error:
                report['cleanup'] = {'complete': False, 'remaining': None, 'error_type': type(error).__name__}
            store.close()
        directory.cleanup()
        report['temporary_profile_removed'] = not Path(directory.name).exists()
        report['neo4j_notifications'] = {'known_call_deprecation_count': counter.deprecations,
                                         'other_count': counter.other,
                                         'known_deprecation_is_test_failure': False}
        notification_logger.handlers, notification_logger.propagate, notification_logger.level = old_handlers, old_propagate, old_level
        report['ok'] = (report['ok'] and report['cleanup']['complete']
                        and report.get('http_server_stopped') is True and report['temporary_profile_removed'])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return report


def main(argv=None):
    args = options(argv)
    try:
        report = run(args)
        print(json.dumps({key: report.get(key) for key in ('ok', 'backend', 'synthetic_only', 'cases_total', 'failures', 'cleanup', 'http_server_stopped', 'error_type')}))
        return 0 if report['ok'] else 1
    except Exception as error:
        print(json.dumps({'ok': False, 'error_type': type(error).__name__}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
