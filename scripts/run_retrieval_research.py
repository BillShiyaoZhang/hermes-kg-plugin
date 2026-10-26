#!/usr/bin/env python3
"""One-command, CPU-only development research in a repository-local Python 3.11.

    python scripts/run_retrieval_research.py --output reports/retrieval-dev --allow-download

Model downloads require --allow-download. Python/dependency setup may fetch
packages unless --offline is specified. This entry point never opens Hermes,
reads a default profile, runs blind queries, promotes a model or deploys code.
The complete CPU lock was validated on Windows; other platforms are untested.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import uuid

FORMAT = 'kg-retrieval-bootstrap-v1'
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODELS = ('e5-small', 'qwen3-0.6b')
REPRESENTATIONS = ('values', 'entity', 'entity_relations')
MODEL_KEYS = ('e5-small', 'e5-base', 'qwen3-0.6b', 'bge-m3', 'embeddinggemma-300m')
_CORE_FIXTURE_FILES = ('dataset.json', 'fixture.json', 'labels-template.json')
_MODULE_LAUNCH = ('import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); '
                  'runpy.run_module("kg.embedding_profiles",run_name="__main__")')
_PYTHON_PROBE = ('import json,site,sys; print(json.dumps({"version":list(sys.version_info[:2]),'
                 '"prefix":sys.prefix,"base_prefix":sys.base_prefix,'
                 '"user_site":site.ENABLE_USER_SITE}))')


class BootstrapError(RuntimeError):
    """A concise local error, excluding child output and private snapshot text."""


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_encoded(value).encode('utf-8')).hexdigest()


def _file_hash(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def _read(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise BootstrapError('Duplicate JSON keys in a research artifact')
            result[key] = value
        return result
    def reject(_):
        raise BootstrapError('Non-finite JSON values in a research artifact')
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique, parse_constant=reject)
    except (OSError, ValueError) as exc:
        raise BootstrapError('A required research JSON artifact is missing or invalid') from exc


def _write(path, value, *, replace=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not replace and path.exists():
        raise BootstrapError('Refusing to overwrite an existing research artifact')
    if replace and path.exists() and _read(path).get('format') != FORMAT:
        raise BootstrapError('Refusing to overwrite an artifact not owned by this bootstrap')
    temporary = path.with_name(path.name + '.pending-' + uuid.uuid4().hex)
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                                        allow_nan=False) + '\n', encoding='utf-8')
        if not replace and path.exists():
            raise BootstrapError('Refusing to overwrite an existing research artifact')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--output', required=True, type=Path, help='A run directory strictly below this checkout\'s reports/')
    cli.add_argument('--fixture-dir', type=Path, help='Reuse an existing reports/ fixture; never overwrite human artifacts')
    cli.add_argument('--real-snapshot', type=Path, help='Explicit authorized private snapshot; read locally only')
    cli.add_argument('--labels', type=Path, help='Optional existing human submission, read without modification')
    cli.add_argument('--labels-lock', type=Path, help='Optional existing human lock, read without modification')
    cli.add_argument('--protocol', type=Path, help='Optional frozen protocol; development runs remain blocked')
    cli.add_argument('--models', nargs='+', choices=MODEL_KEYS, default=list(DEFAULT_MODELS))
    cli.add_argument('--representations', nargs='+', choices=REPRESENTATIONS, default=list(REPRESENTATIONS))
    cli.add_argument('--mode', choices=('vector', 'hybrid', 'hybrid_graph'), default='vector')
    cli.add_argument('--scope', choices=('profile', 'shared'), default='profile')
    cli.add_argument('--threads', type=int, default=8)
    cli.add_argument('--model-cache', type=Path, default=Path('.model-cache'))
    cli.add_argument('--allow-download', action='store_true', help='Permit pinned model downloads during prepare only')
    cli.add_argument('--offline', action='store_true', help='Disable dependency/Python and model downloads')
    cli.add_argument('--resume', action='store_true', help='Reuse only results matching every frozen input and model fingerprint')
    cli.add_argument('--dry-run', action='store_true', help='Print steps without creating files or starting subprocesses')
    cli.add_argument('--uv', default='uv', help='uv executable; never installs uv into the host')
    return cli


def _within(root, value, *, below=None):
    value = value if value.is_absolute() else root / value
    value = value.resolve()
    boundary = (below or root).resolve()
    if not boundary.is_relative_to(root) or not value.is_relative_to(boundary) or value == boundary:
        raise BootstrapError('Research output/cache paths must remain within their workspace boundary')
    return value


def _paths(args, root):
    reports = (root / 'reports').resolve()
    output = _within(root, args.output, below=reports)
    fixture = _within(root, args.fixture_dir, below=reports) if args.fixture_dir else output / 'fixture'
    base = _within(root, Path('.cache/retrieval-research'))
    uv_cache = _within(root, Path('.cache/uv'))
    model_cache = _within(root, args.model_cache)
    for path in (output, fixture, base, uv_cache, model_cache):
        if path.exists() and not path.is_dir():
            raise BootstrapError('A research directory path points to an existing file')
    venv = base / 'venv'
    if venv.resolve() != venv or not venv.resolve().is_relative_to(root):
        raise BootstrapError('The research virtual environment must not be an external symlink')
    return {'root': root, 'output': output, 'fixture': fixture, 'base': base,
            'uv_cache': uv_cache, 'models': model_cache, 'venv': venv,
            'python': venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'),
            'lock': root / 'requirements-research.lock',
            'runner': root / 'scripts/research_retrieval.py',
            'vector_cache': base / 'vector-cache.sqlite3'}


def _input_path(root, value):
    if value is None:
        return None
    result = (value if value.is_absolute() else root / value).resolve()
    if not result.is_file():
        raise BootstrapError('An explicitly selected research input does not exist')
    return result


def _environment(paths, threads, original, *, model_download=False, offline=False):
    # Do not inherit profile routing, credentials, VIRTUAL_ENV, PYTHONPATH or
    # uv/pip configuration from a Hermes shell. Proxy/certificate routing is
    # retained, without printing those values in a manifest.
    retained = {'PATH', 'SystemRoot', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'ComSpec',
                'PATHEXT', 'SYSTEMDRIVE', 'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS',
                'LANG', 'LC_ALL', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'SSL_CERT_FILE',
                'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE'}
    env = {key: value for key, value in original.items() if key in retained}
    base, cache = paths['base'], paths['base'] / 'cache'
    # These are child-process values only. No parent environment or real home
    # configuration is modified, including on Windows.
    env.update({
        'HOME': str(base / 'home'), 'USERPROFILE': str(base / 'home'),
        'APPDATA': str(base / 'home/AppData/Roaming'), 'LOCALAPPDATA': str(base / 'home/AppData/Local'),
        'TEMP': str(base / 'tmp'), 'TMP': str(base / 'tmp'), 'TMPDIR': str(base / 'tmp'),
        'UV_CACHE_DIR': str(paths['uv_cache']), 'UV_PYTHON_INSTALL_DIR': str(base / 'python'),
        'UV_TOOL_DIR': str(base / 'tools'), 'UV_TOOL_BIN_DIR': str(base / 'bin'),
        # Both immutable uv artifacts and this disposable venv live under the
        # same checkout/volume. uv upgrades unlink old package files; they do
        # not edit shared cached bytes in place. Let uv handle link fallbacks.
        'UV_NO_CONFIG': '1', 'UV_NO_PROJECT': '1', 'UV_LINK_MODE': 'hardlink',
        'PIP_CACHE_DIR': str(cache / 'pip'), 'PIP_CONFIG_FILE': os.devnull,
        'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'XDG_CACHE_HOME': str(cache), 'XDG_CONFIG_HOME': str(base / 'config'),
        'XDG_DATA_HOME': str(base / 'data'), 'TORCH_HOME': str(cache / 'torch'),
        'HF_HOME': str(cache / 'huggingface'), 'HF_HUB_CACHE': str(cache / 'huggingface/hub'),
        'HF_ASSETS_CACHE': str(cache / 'huggingface/assets'),
        'SENTENCE_TRANSFORMERS_HOME': str(cache / 'sentence-transformers'),
        'HF_HUB_DISABLE_TELEMETRY': '1', 'TOKENIZERS_PARALLELISM': 'false',
        'HF_HUB_OFFLINE': '0' if model_download and not offline else '1',
        'TRANSFORMERS_OFFLINE': '0' if model_download and not offline else '1',
        'CUDA_VISIBLE_DEVICES': '', 'OMP_NUM_THREADS': str(threads), 'MKL_NUM_THREADS': str(threads),
    })
    if offline:
        env.update(UV_OFFLINE='1', UV_PYTHON_DOWNLOADS='never')
    return env


def _execute(command, paths, env, run, stage):
    try:
        result = run([str(value) for value in command], cwd=str(paths['root']), env=env,
                     shell=False, capture_output=True, text=True, encoding='utf-8', errors='replace', check=False)
    except OSError as exc:
        raise BootstrapError(f'{stage} could not start; no result was accepted') from exc
    if result.returncode != 0:
        extra = ' Check pinned local files, or explicitly permit model downloads.' if stage.startswith('Model preparation') else ''
        raise BootstrapError(f'{stage} failed (exit {result.returncode}).{extra} No smoke fallback or successful result was substituted.')
    return result.stdout


def _output_json(text, stage):
    try:
        result = json.loads(text.lstrip('\ufeff'))
    except (ValueError, AttributeError) as exc:
        raise BootstrapError(f'{stage} did not return verifiable JSON') from exc
    if not isinstance(result, dict):
        raise BootstrapError(f'{stage} did not return a JSON object')
    return result


def _commands(args, paths, uv):
    common = [uv, '--cache-dir', str(paths['uv_cache'])]
    if args.offline:
        common.append('--offline')
    create = [*common, 'venv', '--no-project', '--managed-python', '--python', '3.11', str(paths['venv'])]
    if args.offline:
        create.append('--no-python-downloads')
    sync = [*common, 'pip', 'sync', '--python', str(paths['python']), '--require-hashes',
            '--torch-backend', 'cpu', '--strict', str(paths['lock'])]
    return create, sync


def _python_command(paths):
    return [str(paths['python']), '-I', '-B']


def _prepare_command(args, paths, model):
    command = [*_python_command(paths), '-c', _MODULE_LAUNCH, str(paths['root'] / 'plugins'),
               'prepare', '--model', model, '--cache-dir', str(paths['models']),
               '--device', 'cpu', '--precision', 'float32', '--threads', str(args.threads), '--max-tokens', '512']
    if args.allow_download:
        command.append('--allow-download')
    return command


def _evaluate_command(args, paths, model, representation, output):
    command = [*_python_command(paths), str(paths['runner']), 'evaluate',
               '--fixture-dir', str(paths['fixture']), '--output', str(output), '--model', model,
               '--model-cache', str(paths['models']), '--vector-cache', str(paths['vector_cache']),
               '--representation', representation, '--mode', args.mode, '--scope', args.scope, '--split', 'dev', '--track', 'facts',
               '--candidate-budget', '50', '--graph-direction', 'out',
               '--threads', str(args.threads), '--batch-size', '8', '--max-tokens', '512',
               '--device', 'cpu', '--precision', 'float32']
    for name in ('labels', 'labels_lock', 'protocol'):
        if getattr(args, name):
            command.extend(['--' + name.replace('_', '-'), str(getattr(args, name))])
    return command


def _tool_hashes(root):
    package = root / 'plugins/kg'
    paths = [*sorted((package / 'research').glob('*.py')), package / 'research/protocol-v1.json',
             package / '__init__.py', package / 'retrieval_documents.py', package / 'schema.py', package / 'embedding.py',
             package / 'embedding_profiles.py', root / 'scripts/research_retrieval.py',
             root / 'scripts/run_retrieval_research.py', root / 'requirements-research.lock']
    if any(not path.is_file() for path in paths):
        raise BootstrapError('This checkout is missing required research source or the complete lock')
    return {path.relative_to(root).as_posix(): _file_hash(path) for path in paths}


def _runner_tool_hash(root):
    package = root / 'plugins/kg/research'
    paths = [*package.glob('*.py'), package / 'protocol-v1.json',
             package.parent / 'retrieval_documents.py', package.parent / 'schema.py',
             package.parent / 'embedding.py', package.parent / 'embedding_profiles.py']
    return _digest({path.name: _file_hash(path) for path in sorted(paths)})


def _evaluation_defaults(root, scope='profile'):
    tree = ast.parse((root / 'plugins/kg/retrieval_documents.py').read_text(encoding='utf-8'))
    revision = next((ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == 'DOCUMENT_REVISION' for target in node.targets)), None)
    if not isinstance(revision, str) or not revision:
        raise BootstrapError('Retrieval document revision could not be frozen')
    return {'split': 'dev', 'track': 'facts', 'scope': scope, 'candidate_budget': 50, 'graph_direction': 'out',
            'graph_seeds': 3, 'graph_neighbors': 8, 'acceptance_threshold': None,
            'projection_revision': revision, 'seed': 8128,
            'lexical_algorithm': 'offline-BM25-unicode-word-CJK-bigram-v1', 'rrf_k': 60,
            'graph_score_multiplier': .5, 'ranking_backend': 'exact_cosine', 'smoke_limit': None}


@contextmanager
def _runtime_lock(paths):
    paths['base'].mkdir(parents=True, exist_ok=True)
    lock = paths['base'] / 'bootstrap.lock'
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise BootstrapError('Another research bootstrap is active, or its recovery lock remains; no environment was changed') from exc
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump({'pid': os.getpid()}, stream)
        yield
    finally:
        lock.unlink(missing_ok=True)


def _runtime(args, paths, uv, env, run):
    create, sync = _commands(args, paths, uv)
    if not paths['python'].is_file():
        if paths['venv'].exists() and any(paths['venv'].iterdir()):
            raise BootstrapError('Incomplete research virtual environment; preserve it for inspection rather than overwriting it')
        _execute(create, paths, env, run, 'Isolated Python 3.11 creation')
    probe = _output_json(_execute([*_python_command(paths), '-c', _PYTHON_PROBE], paths, env, run,
                                  'Research Python isolation probe'), 'Research Python isolation probe')
    try:
        isolated = (probe.get('version') == [3, 11] and probe.get('user_site') is False
                    and Path(probe['prefix']).resolve() == paths['venv'].resolve()
                    and Path(probe['base_prefix']).resolve() != paths['venv'].resolve()
                    and Path(probe['base_prefix']).resolve().is_relative_to((paths['base'] / 'python').resolve()))
        configuration = (paths['venv'] / 'pyvenv.cfg').read_text(encoding='utf-8').lower()
    except (OSError, KeyError, TypeError) as exc:
        raise BootstrapError('Research Python isolation could not be verified') from exc
    if not isolated or not re.search(r'include-system-site-packages\s*=\s*false', configuration):
        raise BootstrapError('Research runtime must be Python 3.11 in its own workspace-managed base and venv without system/user site packages')
    _execute(sync, paths, env, run, 'Hash-locked CPU dependency synchronization')


def _fixture(args, paths, env, run):
    folder = paths['fixture']
    present = [bool((folder / name).is_file()) for name in _CORE_FIXTURE_FILES]
    if not all(present):
        if folder.exists() and any(folder.iterdir()):
            raise BootstrapError('Existing fixture is incomplete; it and any human review files were preserved')
        command = [*_python_command(paths), str(paths['runner']), 'prepare-fixture', '--output', str(folder)]
        if args.real_snapshot:
            command.extend(['--real-snapshot', str(args.real_snapshot)])
        _execute(command, paths, env, run, 'Non-overwriting fixture preparation')
    dataset, fixture = _read(folder / 'dataset.json'), _read(folder / 'fixture.json')
    if not isinstance(dataset, dict) or not isinstance(fixture, dict) or fixture.get('dataset_sha256') != _digest(dataset):
        raise BootstrapError('Fixture and dataset hashes do not match')
    _read(folder / 'labels-template.json')
    source = fixture.get('real_source_manifest', {})
    if not isinstance(source, dict):
        raise BootstrapError('Fixture source provenance is invalid')
    if source.get('authorized') or args.real_snapshot:
        if not args.real_snapshot or source.get('snapshot_sha256') != _digest(_read(args.real_snapshot)):
            raise BootstrapError('Private fixture reuse requires the same explicit --real-snapshot')
    return dataset, fixture


def _prepare_model(args, paths, env, run, model):
    result = _output_json(_execute(_prepare_command(args, paths, model), paths, env, run,
                                   'Model preparation: ' + model), 'Model preparation')
    manifest = result.get('manifest', {})
    if not isinstance(manifest, dict) or not isinstance(result.get('status'), dict):
        raise BootstrapError('Model preparation returned invalid manifest/status metadata')
    fingerprint = result.get('fingerprint')
    unsigned = {key: value for key, value in manifest.items() if key not in ('ready', 'fingerprint_requires_prepare')}
    hashes = manifest.get('files_sha256', {})
    if (result.get('status', {}).get('ready') is not True or manifest.get('ready') is not True
            or manifest.get('key') != model or manifest.get('device') != 'cpu'
            or manifest.get('precision') != 'float32' or manifest.get('max_tokens') != 512
            or manifest.get('threads') != args.threads or manifest.get('batch_size') != 8
            or not re.fullmatch(r'[0-9a-f]{40}', str(manifest.get('model_revision', '')))
            or not isinstance(hashes, dict) or not hashes
            or any(not re.fullmatch(r'[0-9a-f]{64}', str(value)) for value in hashes.values())
            or fingerprint != _digest(unsigned)):
        raise BootstrapError('Model preparation did not attest a ready pinned CPU profile and verified artifact hashes')
    return {'fingerprint': fingerprint, 'manifest': manifest}


def _verify_run(path, identity, model, representation):
    result = _read(path)
    if not isinstance(result, dict) or not isinstance(result.get('manifest'), dict):
        raise BootstrapError('A research result does not have a verifiable manifest')
    manifest = result.get('manifest', {})
    if (not isinstance(manifest.get('labels_validation', {}), dict)
            or not isinstance(result.get('promotion', {}), dict)):
        raise BootstrapError('A research result has invalid labels/promotion metadata')
    config = manifest.get('config', {})
    expected = {**identity['evaluation_defaults'], 'representation': representation, 'mode': identity['mode'],
                'model_fingerprint': identity['models'][model]['fingerprint']}
    rows = result.get('rows')
    if (manifest.get('dataset_sha256') != identity['dataset_sha256']
            or manifest.get('fixture_sha256') != identity['fixture_sha256']
            or manifest.get('tool_code_sha256') != identity['runner_tool_sha256']
            or manifest.get('protocol_sha256') != identity['input_sha256']['protocol']
            or manifest.get('labels_validation', {}).get('labels_sha256') != identity['input_sha256']['labels']
            or config != expected
            or manifest.get('model') != identity['models'][model]['manifest']
            or manifest.get('config_sha256') != _digest(config)
            or manifest.get('run_id') != _digest({key: value for key, value in manifest.items() if key != 'run_id'})
            or not isinstance(rows, list) or not rows
            or result.get('results_sha256') != _digest(rows)
            or any(not isinstance(row, dict) or row.get('track') != 'facts'
                   or row.get('id') not in identity['allowed_dev_query_ids'] for row in rows)
            or len({row['id'] for row in rows}) != len(rows)
            or result.get('promotion', {}).get('status') != 'blocked'):
        raise BootstrapError('Existing/new result does not match dataset, tool, model/config hashes; no result was reused')
    return result


def _assert_inputs_unchanged(args, paths, identity):
    if (_digest(_read(paths['fixture'] / 'dataset.json')) != identity['dataset_sha256']
            or _digest(_read(paths['fixture'] / 'fixture.json')) != identity['fixture_sha256']
            or _tool_hashes(paths['root']) != identity['tool_files']
            or any((_digest(_read(getattr(args, name))) if getattr(args, name) else None) != expected
                   for name, expected in identity['input_sha256'].items())):
        raise BootstrapError('Research inputs or tool code changed during execution; no completion was accepted')


def _dry_run(args, paths, uv, platform_name):
    create, sync = _commands(args, paths, uv)
    prepare_fixture = [*_python_command(paths), str(paths['runner']), 'prepare-fixture', '--output', str(paths['fixture'])]
    if args.real_snapshot:
        prepare_fixture.extend(['--real-snapshot', str(args.real_snapshot)])
    return {'format': FORMAT, 'dry_run': True, 'execution_success': False, 'research_ready': False,
            'promotion': {'status': 'blocked', 'reasons': ['dry_run_only', 'blind_not_run']},
            'platform': {'current': platform_name, 'lock_validated': 'Windows CPU', 'other_platforms': 'untested'},
            'steps': [{'stage': 'create_venv_if_missing', 'command': create},
                      {'stage': 'sync_exact_cpu_lock', 'command': sync},
                      {'stage': 'prepare_fixture_only_if_absent', 'command': prepare_fixture},
                      *[{'stage': 'prepare_model', 'command': _prepare_command(args, paths, model)} for model in args.models],
                      *[{'stage': 'evaluate_dev_facts_or_validate_exact_resume',
                         'command': _evaluate_command(args, paths, model, representation,
                             paths['output'] / 'runs' / f'{model}--{representation}--{args.mode}.json')}
                        for model in args.models for representation in args.representations]],
            'resume': 'not_verified; dataset/tool/model/config fingerprints must match after preparation',
            'model_downloads_allowed': args.allow_download, 'scope': args.scope, 'device': 'cpu', 'max_tokens': 512}


def run_bootstrap(args, *, root=REPO_ROOT, run=None, environ=None, platform_name=None):
    root = Path(root).resolve()
    run = run or subprocess.run
    environ = dict(os.environ if environ is None else environ)
    platform_name = platform_name or platform.system()
    if not 1 <= args.threads <= 256:
        raise BootstrapError('threads must be from 1 to 256')
    if args.offline and args.allow_download:
        raise BootstrapError('--offline and --allow-download cannot be combined')
    if args.labels_lock and not args.labels:
        raise BootstrapError('--labels-lock requires an explicit --labels input')
    args.models, args.representations = list(dict.fromkeys(args.models)), list(dict.fromkeys(args.representations))
    paths = _paths(args, root)
    for name in ('real_snapshot', 'labels', 'labels_lock', 'protocol'):
        setattr(args, name, _input_path(root, getattr(args, name)))
    if not paths['lock'].is_file() or not paths['runner'].is_file():
        raise BootstrapError('Run this entry point from a complete source checkout with requirements-research.lock')
    if (root / 'plugins/kg/_vendor').exists():
        raise BootstrapError('A checkout-level kg/_vendor would bypass the locked research environment; use a clean source checkout')
    uv = shutil.which(args.uv, path=environ.get('PATH')) or args.uv
    if args.dry_run:
        return _dry_run(args, paths, uv, platform_name)
    if shutil.which(args.uv, path=environ.get('PATH')) is None and not Path(args.uv).is_file():
        raise BootstrapError('uv is required; this bootstrap never installs tools into the Hermes host')
    top_manifest = paths['output'] / 'bootstrap-manifest.json'
    if top_manifest.exists() and not args.resume:
        raise BootstrapError('This output already has a run manifest; select a new output or use strict --resume')
    tool_files = _tool_hashes(root)
    env = _environment(paths, args.threads, environ, offline=args.offline)
    model_env = _environment(paths, args.threads, environ,
                             model_download=args.allow_download, offline=args.offline)
    for key in ('HOME', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME',
                'UV_CACHE_DIR', 'UV_PYTHON_INSTALL_DIR', 'HF_HOME', 'TORCH_HOME'):
        Path(env[key]).mkdir(parents=True, exist_ok=True)
    with _runtime_lock(paths):
        _runtime(args, paths, uv, env, run)
        dataset, fixture = _fixture(args, paths, env, run)
        models = {model: _prepare_model(args, paths, model_env, run, model) for model in args.models}
        identity = {
            'dataset_sha256': _digest(dataset), 'fixture_sha256': _digest(fixture),
            'tool_files': tool_files, 'runner_tool_sha256': _runner_tool_hash(root),
            'lock_sha256': _file_hash(paths['lock']), 'models': models,
            'mode': args.mode, 'scope': args.scope, 'representations': args.representations,
            'evaluation_defaults': _evaluation_defaults(root, args.scope),
            'allowed_dev_query_ids': sorted(query['id'] for query in fixture.get('queries', [])
                                            if query.get('split') == 'dev' and query.get('track') == 'facts'),
            'config': {'device': 'cpu', 'precision': 'float32', 'threads': args.threads,
                       'batch_size': 8, 'max_tokens': 512, 'split': 'dev', 'track': 'facts'},
            'input_sha256': {name: _digest(_read(getattr(args, name))) if getattr(args, name) else None
                             for name in ('real_snapshot', 'labels', 'labels_lock', 'protocol')},
            'platform': platform_name,
        }
        sealed = {'format': FORMAT, 'identity': identity, 'identity_sha256': _digest(identity)}
        _assert_inputs_unchanged(args, paths, identity)
        if top_manifest.exists():
            previous = _read(top_manifest)
            if previous != sealed:
                raise BootstrapError('Resume dataset/tool/model/config hashes differ; existing fixtures and results were preserved')
        else:
            if (paths['output'] / 'runs').exists() and any((paths['output'] / 'runs').iterdir()):
                raise BootstrapError('Existing results have no matching bootstrap manifest; use a new output directory')
            _write(top_manifest, sealed)
        completed, resumed, promotions = [], 0, set()
        for model in args.models:
            for representation in args.representations:
                _assert_inputs_unchanged(args, paths, identity)
                output = paths['output'] / 'runs' / f'{model}--{representation}--{args.mode}.json'
                if output.exists():
                    if not args.resume:
                        raise BootstrapError('Refusing to overwrite an existing result; strict --resume is required')
                    result = _verify_run(output, identity, model, representation)
                    resumed += 1
                else:
                    _execute(_evaluate_command(args, paths, model, representation, output), paths, env, run,
                             'Development fact evaluation: ' + model + '/' + representation)
                    result = _verify_run(output, identity, model, representation)
                _assert_inputs_unchanged(args, paths, identity)
                completed.append({'model': model, 'representation': representation,
                                  'run_id': result['manifest']['run_id'],
                                  'result_sha256': result['results_sha256']})
                promotions.update(result['promotion'].get('reasons', []))
        summary = {
            'format': FORMAT, 'execution_success': True, 'research_ready': True,
            'readiness_scope': 'pinned_cpu_runtime_and_completed_dev_fact_runs_only',
            'promotion': {'status': 'blocked', 'reasons': sorted(promotions | {'development_only', 'blind_not_run'})},
            'formal_evidence_complete': False, 'blind_queries_executed': 0,
            'identity_sha256': sealed['identity_sha256'], 'runs': completed, 'resumed_runs': resumed,
            'model_downloads_allowed': args.allow_download, 'scope': args.scope,
            'platform': {'current': platform_name, 'lock_validated': 'Windows CPU', 'other_platforms': 'untested'},
        }
        _write(paths['output'] / 'bootstrap-summary.json', summary, replace=True)
        return summary


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = run_bootstrap(args)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (BootstrapError, OSError, ValueError) as exc:
        print(json.dumps({'format': FORMAT, 'execution_success': False, 'research_ready': False,
                          'promotion': {'status': 'blocked'},
                          'error': str(exc) if isinstance(exc, BootstrapError) else 'Research bootstrap failed; no successful result was accepted.'},
                         ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
