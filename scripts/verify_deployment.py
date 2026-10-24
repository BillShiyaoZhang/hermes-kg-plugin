#!/usr/bin/env python3
"""Verify installed KG code through a real local web service and Hermes adapter.

Only a generated synthetic profile and namespace are mutated. Set
KG_TEST_BOLT_URL and KG_TEST_PASSWORD to a disposable Neo4j instance. Credentials
are never accepted as command-line arguments or included in the report.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
import uuid


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', required=True, type=Path)
    parser.add_argument('--core', type=Path)
    parser.add_argument('--model-cache', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--real-agent', action='store_true', help='Opt in to a bounded synthetic conversation through the real configured model')
    parser.add_argument('--host-python', type=Path, help='Installed Hermes interpreter for --real-agent')
    args = parser.parse_args(argv)
    home = args.home.resolve()
    core = (args.core or home / 'hermes-agent').resolve()
    if not os.environ.get('KG_TEST_BOLT_URL') or not os.environ.get('KG_TEST_PASSWORD'):
        parser.error('Set the disposable KG_TEST_BOLT_URL and KG_TEST_PASSWORD environment variables')
    original_environment = dict(os.environ)
    test_url = os.environ['KG_TEST_BOLT_URL']
    test_password = os.environ['KG_TEST_PASSWORD']
    if not (home / 'plugins/kg/provider.py').is_file():
        parser.error('The target profile does not contain an installed KG provider')
    if args.real_agent and not args.host_python:
        parser.error('--real-agent requires --host-python')
    sys.path[:0] = [str(home / 'plugins'), str(core)]
    import httpx
    from kg.config import load_config
    from kg.provider import KGMemoryProvider
    from kg.deployment import console_command
    from kg.store import KGStore

    prefix = 'urn:deploymentverify' + uuid.uuid4().hex + ':kg:'
    report = {'ok': False, 'synthetic_only': True, 'personal_transcripts_sent': 0,
              'checks': [], 'cleanup': False, 'installed_provider_sha256':
              hashlib.sha256((home / 'plugins/kg/provider.py').read_bytes()).hexdigest()}
    started = time.monotonic()
    process = provider = store = None

    def passed(name):
        report['checks'].append(name)
        print('PASS ' + name, flush=True)

    try:
        with tempfile.TemporaryDirectory(prefix='kg-installed-verify-') as directory:
            profile = Path(directory)
            port = free_port()
            for key in list(os.environ):
                if key.startswith(('KG_', 'HERMES_KG_', 'NEO4J_')):
                    os.environ.pop(key)
            os.environ.update(HERMES_HOME=str(profile), KG_HOME=str(profile), HERMES_AGENT_HOME=str(core))
            data = {'enabled': True, 'bolt_url': test_url,
                    'password': test_password, 'urn_prefix': prefix,
                    'user_person_urn': prefix + 'person-self', 'default_shared': False,
                    'timezone': 'Asia/Shanghai', 'console_url': 'http://127.0.0.1:' + str(port),
                    'query_cadence': 3}
            config_path = profile / 'kg.json'
            config_path.write_text(json.dumps(data), encoding='utf-8')
            config_path.chmod(0o600)
            (profile / 'config.yaml').write_text('memory:\n  provider: kg\n', encoding='utf-8')
            syntax = ast.parse((core / 'hermes_state_common.py').read_text(encoding='utf-8'))
            schema = next(ast.literal_eval(node.value) for node in syntax.body
                          if isinstance(node, ast.Assign) and any(isinstance(n, ast.Name) and n.id == 'SCHEMA_SQL' for n in node.targets))
            with closing(sqlite3.connect(profile / 'state.db')) as db:
                db.executescript(schema)
                db.execute('INSERT INTO sessions(id,source,started_at) VALUES(?,?,?)',
                           ('synthetic-deployment', 'cli', time.time()))
                db.commit()
            config = load_config(home=profile)
            assert config.home == profile.resolve() and config.urn_prefix == prefix
            assert config.neo4j_url == test_url and config.password == test_password
            assert config.console_url == data['console_url']
            store = KGStore(config)
            env = dict(os.environ, KG_HOME=str(profile), HERMES_HOME=str(profile),
                       HERMES_AGENT_HOME=str(core), KG_MODEL_CACHE=str(args.model_cache.resolve()),
                       KG_SYNC_SCHEDULER_ENABLED='false', PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
            env.pop('PYTHONHOME', None)
            # The child imports the installed profile wrapper, never this checkout.
            log = (profile / 'web.log').open('w', encoding='utf-8')
            installed = json.loads((home / 'kg-deploy/manifest.json').read_text(encoding='utf-8'))
            report['installed_source_sha256'] = installed['source_sha256']
            runtime_python = Path(installed['runtime_python'])
            if os.name == 'nt':
                # Use the exact deployed runtime's verified bootstrap, including
                # venv prefixes and native package activation, with a test home.
                server_argv = console_command(runtime_python, home)
                server_argv[-1] = 'from kg.console_cli import main; raise SystemExit(main(' + repr([
                    '--home', str(profile), 'serve', '--port', str(port)]) + '))'
            else:
                server_argv = [str(runtime_python), '-B', str(home / 'scripts/kg_console.py'),
                    '--home', str(profile), 'serve', '--port', str(port)]
            process = subprocess.Popen(server_argv, env=env, cwd=directory,
                stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                deadline = time.monotonic() + 120
                client = None
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        report['synthetic_server_exit_code'] = process.returncode
                        raise RuntimeError('Synthetic installed console exited during startup')
                    token_file = profile / 'kg-console.token'
                    if token_file.is_file():
                        client = httpx.Client(base_url=config.console_url, timeout=45, trust_env=False,
                            headers={'Authorization': 'Bearer ' + token_file.read_text(encoding='utf-8').strip()})
                        try:
                            response = client.get('/api/v1/status')
                            if response.is_success and response.json().get('search', {}).get('model_ready'):
                                break
                        except httpx.TransportError:
                            pass
                        client.close()
                        client = None
                    time.sleep(0.5)
                if client is None:
                    raise RuntimeError('Installed console/model readiness timed out')
                with closing(client):
                    def api(path, body=None, expected=200):
                        response = client.get(path) if body is None else client.post(path, json=body)
                        assert response.status_code == expected, (path, response.status_code)
                        return response.json()

                    def detail(uri):
                        return api('/api/v1/memories/detail', {'uri': uri})

                    def mutate(action, uri, changes=None):
                        card = detail(uri)
                        body = {'uri': uri, 'expected_version': card['version'],
                                'operation_id': 'acceptance-' + uuid.uuid4().hex}
                        if changes is not None:
                            body['changes'] = changes
                        return api('/api/v1/memories/' + action, body)

                    def publish(operations):
                        schema = api('/api/v1/ontology')
                        body = {'expected_namespace': prefix, 'expected_version': schema['version'], 'operations': operations}
                        preview = api('/api/v1/ontology/validate', body)
                        assert preview['can_publish'], 'Synthetic definition rejected'
                        risk = preview['risk']
                        body.update(operation_id='acceptance-' + uuid.uuid4().hex)
                        if risk.get('requires_confirmation'):
                            body.update(confirmation=risk['confirmation_phrase'], confirmation_token=risk['confirmation_token'])
                        return api('/api/v1/ontology/publish', body)

                    assert client.get('/').headers.get('content-type', '').startswith('text/html')
                    with httpx.Client(base_url=config.console_url, trust_env=False) as unauthenticated:
                        assert unauthenticated.get('/api/v1/status').status_code == 401
                    passed('installed_ui_and_authenticated_http')
                    # The lightweight adapter contract runs in the console
                    # interpreter. Full host discovery/registration is checked
                    # by deployment_gateway and the optional real AIAgent child.
                    provider = KGMemoryProvider(config=config)
                    provider.initialize('synthetic-deployment', hermes_home=str(profile), platform='cli', agent_context='primary')
                    assert {item['name'] for item in provider.get_tool_schemas()} == {'kg_search', 'kg_write', 'kg_status'}
                    assert json.loads(provider.handle_tool_call('kg_status', {}))['connected']

                    def tool(name, body):
                        value = json.loads(provider.handle_tool_call(name, body))
                        assert value.get('ok') is True, name
                        return value

                    target = prefix + 'concept-target'
                    control = prefix + 'concept-control'
                    tool('kg_write', {'entities': [
                        {'type': 'concept', 'id': 'target', 'name': 'Earlier fictional title'},
                        {'type': 'concept', 'id': 'control', 'name': 'freshness_marker control'}]})
                    # Model readiness can precede the startup indexing job's
                    # final durable receipt. Wait for that owned job to finish.
                    deadline = time.monotonic() + 90
                    while True:
                        response = client.post('/api/v1/search/reindex', json={})
                        if response.status_code != 409:
                            assert response.status_code == 202, response.status_code
                            indexing = response.json()
                            break
                        if time.monotonic() >= deadline:
                            raise TimeoutError('Startup indexing remained busy')
                        time.sleep(0.2)
                    deadline = time.monotonic() + 90
                    while True:
                        job = next((item for item in api('/api/v1/jobs')['items'] if item['id'] == indexing['job_id']), None)
                        if job and job['status'] == 'succeeded':
                            break
                        assert not job or job['status'] not in ('failed', 'interrupted'), 'Synthetic reindex failed'
                        if time.monotonic() >= deadline:
                            raise TimeoutError('Synthetic reindex did not finish')
                        time.sleep(0.2)
                    # Warm identical-query recall before the Web commit, including
                    # another already indexed hit for the target's future keyword.
                    provider.on_turn_start(3, 'freshness_marker')
                    warmed = provider.prefetch('freshness_marker', session_id='synthetic-deployment')
                    assert 'freshness_marker control' in warmed, 'The existing fulltext control hit was not established'
                    assert store.run_read('MATCH (c:KGSearchChunk {source_uri:$uri,namespace:$prefix}) RETURN count(c) AS count',
                        {'uri': control, 'prefix': prefix})[0]['count'] > 0
                    result = mutate('correct', target, {'name': 'freshness_marker corrected',
                        'props': {'description': 'A fictional animal shelter volunteer schedule, every Saturday morning.'}})
                    assert result.get('search_freshness', {}).get('semantic') == 'current', 'Web did not finish semantic refresh'
                    recall = provider.prefetch('freshness_marker', session_id='synthetic-deployment')
                    assert 'freshness_marker corrected' in recall
                    found = tool('kg_search', {'question': 'freshness_marker', 'limit': 10})['result']
                    assert target in {item['uri'] for item in found['kg_results']}
                    semantic = tool('kg_search', {'question': 'When does the fictional person help homeless pets?', 'limit': 10})['result']
                    assert semantic['mode'] == 'hybrid' and target in {item['uri'] for item in semantic['kg_results']}
                    passed('web_correction_visible_to_same_agent_without_restart_or_index_wait')
                    mutate('archive', target)
                    assert target not in {item['uri'] for item in tool('kg_search', {'question': target})['result']['kg_results']}
                    assert 'freshness_marker corrected' not in provider.prefetch('freshness_marker', session_id='synthetic-deployment')
                    mutate('restore', target)
                    assert tool('kg_search', {'question': target})['result']['kg_results'][0]['uri'] == target
                    passed('web_archive_restore_visible_to_next_agent_tool')
                    # Exercise the production snapshot/staging/review path. The
                    # extractor is deterministic here; no validator is bypassed.
                    approved_name = 'freshness_marker approved'
                    quote = 'The project target is now called freshness_marker approved.'
                    with closing(sqlite3.connect(profile / 'state.db')) as db:
                        db.executemany('INSERT INTO messages(session_id,role,content,timestamp) VALUES(?,?,?,?)',
                            [('synthetic-deployment', 'user', text, time.time() + index) for index, text in enumerate([
                                quote, 'Please retain the new title.', 'The project target belongs to this project.',
                                'Use the current title for future summaries.', 'The current project title is freshness_marker approved.'])])
                        db.commit()
                    from kg.runner import run as run_sync
                    def extract_fixture(**request):
                        snapshot = json.loads(request['messages'][1]['content'].split('\n', 1)[1])
                        message = next(item for item in snapshot['messages'] if item['content'] == quote)
                        return json.dumps({'session_id': snapshot['session_id'], 'snapshot_id': snapshot['snapshot_id'],
                            'entities': [{'type': 'concept', 'id': 'target', 'name': approved_name,
                                          'evidence': {'message_id': message['id'], 'quote': quote}}], 'relations': []})
                    code, sync_report = run_sync(home=profile, config=config, llm=extract_fixture)
                    assert code == 0 and sync_report['deferred_batches'] == 1, 'Protected correction did not create a review'
                    pending = api('/api/v1/reviews/query', {})['items']
                    review = next(item for item in pending if item['id'] == sync_report['receipts'][0]['review_batch_id'])
                    approved = api('/api/v1/reviews/' + review['id'] + '/approve', {
                        'operation_id': 'acceptance-' + uuid.uuid4().hex, 'expected_versions': review['base_versions']})
                    assert approved['search_freshness']['semantic'] == 'current'
                    assert detail(target)['name'] == approved_name
                    assert approved_name in provider.prefetch('freshness_marker', session_id='synthetic-deployment')
                    replay_code, replay = run_sync(home=profile, config=config, llm=lambda **request: (_ for _ in ()).throw(AssertionError('Replay invoked a model')))
                    assert replay_code == 0 and replay['llm_calls'] == 0
                    passed('durable_sync_review_approval_visible_to_next_agent_and_idempotent_replay')
                    publish([
                        {'action': 'create', 'kind': 'entity_types', 'key': 'x_acceptance_project', 'definition': {'label': '合成验收项目'}},
                        {'action': 'create', 'kind': 'properties', 'key': 'x_acceptance_score', 'definition': {'label': '合成得分', 'domain': ['x_acceptance_project'], 'range': ['integer'], 'required_for': ['x_acceptance_project']}},
                        {'action': 'create', 'kind': 'relations', 'key': 'X_ACCEPTANCE_LINK', 'definition': {'label': '合成关联', 'domain': ['x_acceptance_project'], 'range': ['concept']}}])
                    project = prefix + 'x_acceptance_project-one'
                    tool('kg_write', {'entities': [{'type': 'x_acceptance_project', 'id': 'one', 'name': 'Synthetic project', 'props': {'x_acceptance_score': 7}}],
                        'relations': [{'from_urn': project, 'to_urn': control, 'relation_type': 'X_ACCEPTANCE_LINK'}]})
                    assert tool('kg_search', {'question': project})['result']['kg_results'][0]['type'] == 'x_acceptance_project'
                    overview = api('/api/v1/graph/overview/query', {})
                    assert overview['total_nodes'] >= 3
                    neighbors = api('/api/v1/graph/neighbors/query', {'center_uri': project})
                    assert control in {node['uri'] for node in neighbors['nodes']}
                    paths = api('/api/v1/graph/paths/query', {'source_uri': project, 'target_uri': control})
                    assert paths['paths']
                    passed('web_custom_ontology_immediately_usable_by_agent_and_graph')
                    publish([{'action': 'deactivate', 'kind': 'entity_types', 'key': 'x_acceptance_project'}])
                    rejected = json.loads(provider.handle_tool_call('kg_write', {'entities': [
                        {'type': 'x_acceptance_project', 'id': 'blocked', 'name': 'Blocked synthetic project', 'props': {'x_acceptance_score': 1}}]}))
                    assert not rejected['ok']
                    assert tool('kg_search', {'question': project})['result']['kg_results']
                    publish([{'action': 'restore', 'kind': 'entity_types', 'key': 'x_acceptance_project'}])
                    publish([{'action': 'create', 'kind': 'properties', 'key': 'x_acceptance_unused', 'definition': {'label': '未使用的合成属性'}}])
                    publish([{'action': 'delete', 'kind': 'properties', 'key': 'x_acceptance_unused'}])
                    passed('ontology_deactivation_restore_and_confirmed_unused_delete')
                    paused = api('/api/v1/sync/pause', {})
                    assert paused.get('ok', True)
                    api('/api/v1/sync/resume', {})
                    assert api('/api/v1/status')['sync']['paused'] is False
                    passed('installed_sync_controls')
                    if args.real_agent:
                        shutil.copytree(home / 'plugins/kg', profile / 'plugins/kg',
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'static'))
                        # Copied-plugin installations carry ontology definitions
                        # beside the provider, in the installed KG skill tree.
                        shutil.copytree(home / 'skills/kg-memory', profile / 'skills/kg-memory')
                        engine_report = profile / 'agent-result.json'
                        # This child sees only the generated profile's facts and
                        # transcript; the real profile supplies the model route.
                        done = subprocess.run([str(args.host_python), '-B', str(Path(__file__).with_name('verify_hermes_agent.py')),
                            '--home', str(profile), '--core', str(core), '--route-profile', str(home),
                            '--uri', target, '--expected-name', approved_name, '--output', str(engine_report)],
                            env=env, capture_output=True, text=True, timeout=180,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                        agent_result = json.loads(engine_report.read_text(encoding='utf-8')) if engine_report.is_file() else {'ok': False}
                        report['real_agent'] = agent_result
                        assert done.returncode == 0 and agent_result['ok'], 'Real synthetic Agent acceptance failed'
                        proof = detail(prefix + 'concept-agent-proof')
                        assert proof['name'] == 'Fictional agent integration record'
                        assert 'isolated acceptance conversation' in json.dumps(proof, ensure_ascii=False)
                        passed('actual_hermes_agent_recalled_web_update_and_executed_write')
                    report['ok'] = True
            finally:
                cleanup_errors = []
                if provider is not None:
                    try:
                        provider.shutdown()
                    except Exception as exc:
                        cleanup_errors.append(type(exc).__name__)
                    provider = None
                if process is not None and process.poll() is None:
                    try:
                        process.terminate()
                        try:
                            process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=15)
                    except Exception as exc:
                        cleanup_errors.append(type(exc).__name__)
                log.close()
                if not report['ok']:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(profile / 'web.log', args.output.with_suffix('.server.log'))
                if store is not None:
                    try:
                        assert process is None or process.poll() is not None, 'Synthetic writer could not be stopped'
                        # This prefix is generated above and never read from user input.
                        assert prefix.startswith('urn:deploymentverify') and prefix.endswith(':kg:')
                        with store.driver.session(database=config.database) as session:
                            session.run('MATCH (n) WHERE n.uri STARTS WITH $prefix OR n.namespace=$prefix DETACH DELETE n', {'prefix': prefix}).consume()
                            remaining = session.run('MATCH (n) WHERE n.uri STARTS WITH $prefix OR n.namespace=$prefix RETURN count(n) AS count', {'prefix': prefix}).single()['count']
                            assert remaining == 0
                        report['cleanup'] = not cleanup_errors
                    except Exception as exc:
                        cleanup_errors.append(type(exc).__name__)
                    finally:
                        store.close()
                        store = None
                if cleanup_errors:
                    report['cleanup_error_types'] = cleanup_errors
                    report['ok'] = False
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        report['error_locations'] = [Path(frame.filename).name + ':' + str(frame.lineno)
                                     for frame in traceback.extract_tb(exc.__traceback__)]
        if isinstance(exc, ModuleNotFoundError):
            report['missing_module'] = exc.name
        if isinstance(exc, AssertionError):
            report['assertion'] = str(exc)
        report['ok'] = False
    finally:
        os.environ.clear()
        os.environ.update(original_environment)
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))
    return 0 if report['ok'] and report['cleanup'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
