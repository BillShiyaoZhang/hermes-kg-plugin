"""Transactional profile deployment; stdlib bootstrap, isolated console runtime.

The managed console is a detached process, not an operating-system login service.
No process is stopped by guessing the owner of a listening port. Its separate
loopback control channel authenticates the exact deployment instance instead.
"""
from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass
import getpass
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
import uuid


class DeploymentError(RuntimeError):
    """A deliberately public diagnostic without subprocess output or secrets."""


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def contained(root, path):
    # The selected root can itself have a legitimate OS alias (/var on macOS,
    # or a Windows short-name TEMP directory). Walk below that lexical boundary,
    # not until an unrelated canonical spelling which may never be reached.
    lexical_root = Path(os.path.abspath(root))
    root, path = lexical_root.resolve(), Path(os.path.abspath(path))
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise DeploymentError('Deployment path escapes its managed directory')
    if path.is_relative_to(lexical_root):
        boundary = lexical_root
    elif path.is_relative_to(root):
        boundary = root
    else:
        raise DeploymentError('Deployment path is outside its lexical managed directory')
    cursor = path
    while cursor != boundary:
        if cursor.is_symlink() or (hasattr(cursor, 'is_junction') and cursor.is_junction()):
            raise DeploymentError('Deployment refuses linked destination paths')
        parent = cursor.parent
        if parent == cursor:
            raise DeploymentError('Deployment path has no managed ancestor')
        cursor = parent
    return resolved


def remove_managed(root, path):
    path = contained(root, path)
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


class DeploymentLock:
    """OS-owned advisory lock; a crashed deploy never leaves a stale lock."""
    def __init__(self, home):
        self.path = Path(home) / 'kg-deploy' / 'deploy.lock'

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open('a+b')
        try:
            self.stream.seek(0, os.SEEK_END)
            if not self.stream.tell():
                self.stream.write(b'\0')
                self.stream.flush()
            self.stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            raise DeploymentError('Another deployment is running for this profile') from exc
        return self

    def __exit__(self, *args):
        self.stream.close()


class SyncRunnerLock(DeploymentLock):
    """Same file and byte-zero OS lock as runner._RunLock, without importing it."""
    def __init__(self, home):
        self.path = contained(home, Path(home) / 'kg-sync/runner.lock')

    def __enter__(self):
        try:
            return super().__enter__()
        except DeploymentError as exc:
            raise DeploymentError('An active KG synchronization runner owns the profile; wait for it to finish and retry') from exc


@dataclass
class DeployOptions:
    home: Path
    source: Path
    core: Path | None = None
    hermes_python: Path | None = None
    dry_run: bool = False
    skip_services: bool = False
    skip_restart: bool = False
    skip_model: bool = False
    non_interactive: bool = False
    port: int | None = None
    name: str = '我'
    autostart: bool = False


def command(argv, *, env=None, cwd=None, timeout=900):
    """Keep dependency manager/model/host output out of deployment diagnostics."""
    try:
        done = subprocess.run([str(arg) for arg in argv], env=env, cwd=cwd,
                              capture_output=True, text=True, encoding='utf-8',
                              errors='replace', timeout=timeout,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeploymentError('Deployment command could not complete: ' + Path(str(argv[0])).name) from exc
    if done.returncode:
        raise DeploymentError('Deployment command failed: ' + Path(str(argv[0])).name)
    return done.stdout.strip()


def find_uv(home):
    for path in (Path(home) / 'bin' / 'uv.exe', Path(home) / 'bin' / 'uv'):
        if path.is_file():
            return str(path)
    found = shutil.which('uv')
    if not found:
        raise DeploymentError('uv is required; install uv or place it in the Hermes profile bin directory')
    return found


def python_in(folder):
    return Path(folder) / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def prepare_runtime(home, lock, uv, run=command):
    lock_hash = digest(lock)
    root = Path(home) / 'kg-runtime'
    runtime = root / lock_hash[:20]
    contained(home, runtime)
    marker = runtime / 'kg-runtime.json'
    python = python_in(runtime)
    build_env = dict(os.environ, UV_PYTHON_INSTALL_DIR=str(root / 'python'), PYTHONNOUSERSITE='1')
    for key in ('VIRTUAL_ENV', 'PYTHONPATH', 'PYTHONHOME', 'UV_PROJECT', 'UV_WORKING_DIRECTORY'):
        build_env.pop(key, None)
    if marker.is_file() and python.is_file():
        state = json.loads(marker.read_text(encoding='utf-8'))
        if state.get('lock_sha256') == lock_hash:
            run([python, '-I', '-c', 'import sys; assert sys.version_info[:2] == (3,11); import fastapi,uvicorn,neo4j,rdflib,fastembed,onnxruntime'], env=build_env)
            run([uv, 'pip', 'check', '--python', python], env=build_env)
            return python
    if runtime.exists():
        remove_managed(home, runtime)
    root.mkdir(parents=True, exist_ok=True)
    try:
        try:
            base = run([uv, 'python', 'find', '--no-project', '--managed-python', '3.11'], env=build_env)
        except DeploymentError:
            run([uv, 'python', 'install', '3.11'], env=build_env)
            base = run([uv, 'python', 'find', '--no-project', '--managed-python', '3.11'], env=build_env)
        if not Path(base).is_file():
            raise DeploymentError('uv did not resolve an installed Python 3.11 interpreter')
        if not Path(base).resolve().is_relative_to((root / 'python').resolve()):
            raise DeploymentError('uv resolved a Python outside the isolated deployment runtime')
        run([uv, 'venv', '--no-project', '--python', base, runtime], env=build_env)
        run([uv, 'pip', 'sync', '--python', python, lock], timeout=1200, env=build_env)
        run([uv, 'pip', 'check', '--python', python], env=build_env)
        run([python, '-I', '-c', 'import sys; assert sys.version_info[:2] == (3,11); import fastapi,uvicorn,neo4j,rdflib,fastembed,onnxruntime'], env=build_env)
        atomic_json(marker, {'format': 1, 'lock_sha256': lock_hash, 'python': '3.11'})
    except Exception:
        if runtime.exists():
            remove_managed(home, runtime)
        raise
    return python


def validate_release_tree(source):
    private_dirs = {'private', '.private', 'reports', 'backups', '.git', 'cache', '.cache', '.model-cache', '.venv', 'venv', '_vendor'}
    suffixes = {'.db', '.db-shm', '.db-wal', '.sqlite', '.sqlite3', '.token', '.log', '.pem', '.key', '.zip', '.bak', '.backup'}
    dated = re.compile(r'(?:^|[-_])(?:audit|report|snapshot|dump|backup)[-_]\d{4}[-_]\d{2}[-_]\d{2}(?:$|[-_.])', re.I)
    for path in Path(source).rglob('*'):
        relative = path.relative_to(source)
        if '__pycache__' in relative.parts or path.suffix in {'.pyc', '.pyo'}:
            continue
        if (path.is_symlink() or any(part.lower() in private_dirs for part in relative.parts)
                or path.suffix.lower() in suffixes or path.name.lower().startswith('.env')
                or re.match(r'kg(?:-console.*)?\.json', path.name, re.I) or dated.search(path.name)):
            raise DeploymentError('Release resources contain private or linked files')


def asset_files(dist):
    dist = Path(dist)
    index = dist / 'index.html'
    if not index.is_file():
        raise DeploymentError('Frontend build did not produce index.html')
    paths = [index]
    for value in re.findall(r'(?:src|href)=["\']([^"\']+)["\']', index.read_text(encoding='utf-8')):
        value = value.split('?', 1)[0].split('#', 1)[0]
        if value.startswith(('http:', 'https:', '//', 'data:')):
            raise DeploymentError('Frontend index must reference local assets')
        if not value or value == '/':
            continue
        path = contained(dist, dist / value.lstrip('/'))
        if not path.is_file():
            raise DeploymentError('Frontend index references a missing asset')
        paths.append(path)
    if not any(path.suffix == '.js' for path in paths):
        raise DeploymentError('Frontend build does not contain its JavaScript bundle')
    return paths


def build_frontend(source, run=command):
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    if not npm:
        raise DeploymentError('Node.js and npm are required to build the frontend')
    console = Path(source) / 'console'
    run([npm, 'ci'], cwd=console)
    run([npm, 'run', 'build'], cwd=console)
    asset_files(console / 'dist')


def stage_release(source, stage, python, uv, run=command):
    """Only explicit shipped trees enter the payload; no profile state is copied."""
    source, stage = Path(source), Path(stage)
    plugin = stage / 'plugins' / 'kg'
    plugin.mkdir(parents=True)
    for path in (source / 'plugins' / 'kg').iterdir():
        if path.is_file() and (path.suffix == '.py' or path.name in {'plugin.yaml', 'requirements.lock', 'requirements.in', 'README.md'}):
            if path.is_symlink():
                raise DeploymentError('Release code cannot contain links')
            shutil.copy2(path, plugin / path.name)
    required = ('__init__.py', 'provider.py', 'console_cli.py', 'deployment.py', 'requirements.lock', 'plugin.yaml')
    if any(not (plugin / name).is_file() for name in required):
        raise DeploymentError('Release is missing required plugin files')
    for name in ('kg-memory', 'kg-memory-sync'):
        origin = source / 'skills' / name
        if not (origin / 'SKILL.md').is_file():
            raise DeploymentError('Release is missing a required Hermes skill')
        validate_release_tree(origin)
        shutil.copytree(origin, stage / 'skills' / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))
    dist = source / 'console' / 'dist'
    asset_files(dist)
    validate_release_tree(dist)
    shutil.copytree(dist, plugin / 'static')
    for relative in ('scripts/kg_console.py', 'scripts/kg_sync_runner.py', 'scripts/kg_service.py', 'kg-sync/check_kg.py', 'kg-sync/verify_counts.py', 'kg-sync/mark_processed.py', 'kg-sync/mark_sessions.py'):
        origin = source / relative
        if not origin.is_file() or origin.is_symlink():
            raise DeploymentError('Release is missing a required CLI helper')
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, target)
    run([uv, 'pip', 'install', '--python', python, '--target', plugin / '_vendor', '--require-hashes', '-r', plugin / 'requirements.lock'])
    return stage


PAYLOAD = ('plugins/kg', 'skills/kg-memory', 'skills/kg-memory-sync', 'scripts/kg_console.py', 'scripts/kg_sync_runner.py', 'scripts/kg_service.py',
           'kg-sync/check_kg.py', 'kg-sync/verify_counts.py', 'kg-sync/mark_processed.py', 'kg-sync/mark_sessions.py')


def tree_hash(folder):
    result = hashlib.sha256()
    for path in sorted(Path(folder).rglob('*')):
        if path.is_file():
            result.update(path.relative_to(folder).as_posix().encode())
            result.update(bytes.fromhex(digest(path)))
    return result.hexdigest()


def release_manifest(source, stage, runtime, run=command):
    try:
        sha = run(['git', '-C', source, 'rev-parse', 'HEAD'], timeout=30)
    except DeploymentError:
        sha = None
    if sha is not None and not re.fullmatch(r'[0-9a-f]{40,64}', sha):
        sha = None
    try:
        dirty = bool(run(['git', '-C', source, 'status', '--porcelain'], timeout=30))
    except DeploymentError:
        dirty = None
    files = {path.relative_to(stage).as_posix(): digest(path) for path in sorted(Path(stage).rglob('*')) if path.is_file() and '_vendor' not in path.parts}
    return {'format': 1, 'release_sha': sha, 'working_tree_dirty': dirty, 'source_sha256': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
            'bundle_sha256': tree_hash(Path(stage) / 'plugins/kg/static'), 'lock_sha256': digest(Path(source) / 'requirements-console.lock'),
            'runtime_python': str(runtime), 'files': files, 'installed_at': time.time()}


def patch_provider(text):
    """Preserve ordinary YAML formatting/comments; reject ambiguous structures."""
    if '\t' in text or re.search(r'^\s*(?:---|\.\.\.)\s*$', text, re.M):
        raise DeploymentError('Hermes YAML uses unsupported tabs/documents; set memory.provider to kg manually')
    newline = '\r\n' if '\r\n' in text else '\n'
    lines = text.splitlines(keepends=True)
    matches = [index for index, line in enumerate(lines) if re.match(r'^(?:memory|"memory"|\'memory\')\s*:', line)]
    if len(matches) > 1:
        raise DeploymentError('Hermes YAML has duplicate memory keys')
    if not matches:
        return text + ('' if not text or text.endswith(('\n', '\r')) else newline) + 'memory:' + newline + '  provider: kg' + newline
    start = matches[0]
    match = re.match(r'^(?P<key>memory|"memory"|\'memory\')\s*:\s*(?P<value>[^#\r\n]*)(?P<comment>#[^\r\n]*)?', lines[start])
    value = match['value'].strip()
    if value not in {'', '{}', 'null', '~'}:
        raise DeploymentError('Hermes memory YAML is not a simple mapping; set memory.provider to kg manually')
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or lines[end].lstrip().startswith('#') or lines[end][0].isspace()):
        end += 1
    children = [(index, re.match(r'^( +)([^ #][^:]*):', lines[index])) for index in range(start + 1, end)]
    widths = [len(match[1]) for _, match in children if match]
    indent = min(widths, default=2)
    providers = [index for index, match in children if match and len(match[1]) == indent and match[2].strip('"\' ') == 'provider']
    if len(providers) > 1:
        raise DeploymentError('Hermes YAML has duplicate memory.provider keys')
    if providers:
        index = providers[0]
        old = lines[index]
        if re.search(r':\s*[|>&*!{\[]', old):
            raise DeploymentError('Hermes memory.provider uses unsupported YAML syntax')
        prefix = old[:old.index(':') + 1]
        comment = re.search(r'\s+#.*?(?=\r?\n|$)', old)
        lines[index] = prefix + ' kg' + (comment.group() if comment else '') + newline
    else:
        comment = match['comment'] or ''
        lines[start] = match['key'] + ':' + (' ' + comment if comment else '') + newline
        lines.insert(start + 1, ' ' * indent + 'provider: kg' + newline)
    return ''.join(lines)


def runtime_settings(home):
    path = Path(home) / 'kg-console-runtime.json'
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise DeploymentError('Existing console runtime settings are malformed')
    return {key: value[key] for key in ('model_cache', 'scheduler_owner', 'sync_scheduler_enabled', 'hermes_python') if key in value}


def identity_hash(config):
    return hashlib.sha256(json.dumps([config.get('urn_prefix'), config.get('user_person_urn')], ensure_ascii=False).encode()).hexdigest()


def deployment_port(home, requested=None):
    from .config import env_value
    path = Path(home) / 'kg.json'
    config = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    environment_url = env_value('console_url')
    url = urlparse(environment_url or config.get('console_url', 'http://127.0.0.1:8765'))
    try:
        selected = url.port or 80
    except ValueError as exc:
        raise DeploymentError('Configured console URL has an invalid port') from exc
    if url.scheme != 'http' or url.hostname not in {'127.0.0.1', 'localhost'} or url.username or url.password or url.path not in {'', '/'} or url.query or url.fragment:
        raise DeploymentError('Deployment requires a loopback HTTP console URL without credentials or paths')
    if requested is not None and requested != selected and (path.exists() or environment_url):
        raise DeploymentError('Requested console port conflicts with the profile or environment console URL; existing configuration was preserved')
    return requested if requested is not None else selected


def guard_existing_environment(home):
    from .config import env_value, KGConfig
    path = Path(home) / 'kg.json'
    if not path.exists():
        return
    raw = json.loads(path.read_text(encoding='utf-8-sig'))
    aliases = {'bolt_url': 'neo4j_url', 'url': 'neo4j_url', 'neo4j_user': 'user', 'neo4j_password': 'password', 'neo4j_database': 'database'}
    values = {aliases.get(key, key): value for key, value in raw.items() if aliases.get(key, key) in KGConfig.__dataclass_fields__ and key != 'home'}
    baseline = KGConfig(home=home, **values)
    for key in ('neo4j_url', 'user', 'password', 'database', 'urn_prefix', 'user_person_urn'):
        value = env_value(key)
        if value is None or (key == 'password' and not baseline.password):
            continue
        if value != getattr(baseline, key):
            raise DeploymentError('Environment conflicts with existing profile setting: ' + key + '; no configuration was changed')


HOST_PYTHON_DISCOVERY = r'''
import contextlib,io,json,os,sys
from pathlib import Path
output=sys.stdout
with contextlib.redirect_stdout(io.StringIO()):
    core=Path(sys.argv[1]).resolve()
    sys.path.insert(0,str(core))
    from hermes_cli._launchers import resolve_store_python
    from pm.environments import owning_home_root
    from hermes_constants import set_hermes_home_override,reset_hermes_home_override
    owner=owning_home_root(core)
    token=set_hermes_home_override(owner) if owner is not None else None
    try:
        value=resolve_store_python(core,publication=True)
    finally:
        if token is not None: reset_hermes_home_override(token)
output.write(json.dumps({'python':str(value) if value else None}))
'''


def discover_host_python(home, core, explicit=None, run=command):
    from .hermes_runtime import discover_python
    supplied = explicit or os.environ.get('KG_HERMES_PYTHON') or os.environ.get('HERMES_PYTHON')
    if supplied:
        candidate = Path(supplied).expanduser().resolve()
        if not candidate.is_file():
            raise DeploymentError('Configured Hermes interpreter was not found')
        return candidate
    if core and (Path(core) / 'hermes_cli/_launchers.py').is_file():
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'):
            env.pop(key, None)
        try:
            result = json.loads(run([sys.executable, '-S', '-X', 'utf8', '-c', HOST_PYTHON_DISCOVERY, core], env=env, cwd=core, timeout=30))
            if result.get('python') and Path(result['python']).is_file():
                return Path(result['python']).resolve()
        except (DeploymentError, ValueError, TypeError):
            pass
    preferred = runtime_settings(home).get('hermes_python')
    if preferred and Path(preferred).expanduser().is_file():
        return Path(preferred).expanduser().resolve()
    if core:
        return discover_python(core)
    raise DeploymentError('Hermes core/interpreter was not found')


def profile_environment(home, core=None, python=None, settings=None, extra=None):
    env = dict(os.environ)
    for key in ('PYTHONHOME', 'VIRTUAL_ENV'):
        env.pop(key, None)
    env.update({'HERMES_HOME': str(home), 'KG_HOME': str(home), 'PYTHONPATH': os.pathsep.join([str(Path(home) / 'plugins')] + ([str(core)] if core else []))})
    if core:
        env['HERMES_AGENT_HOME'] = str(core)
    if python:
        env['KG_HERMES_PYTHON'] = str(python)
    for key, value in (settings or {}).items():
        if key == 'model_cache':
            env['KG_MODEL_CACHE'] = str(value)
        elif key == 'sync_scheduler_enabled':
            env['KG_SYNC_SCHEDULER_ENABLED'] = str(value).lower()
    if (settings or {}).get('scheduler_owner') == 'hermes-cron' and 'sync_scheduler_enabled' not in (settings or {}):
        env['KG_SYNC_SCHEDULER_ENABLED'] = 'false'
    env.update(extra or {})
    return env


def initial_connection(home, *, non_interactive=False, prompt=input, secret=getpass.getpass):
    """Return child env, never a password in argv or a diagnostic."""
    from .config import env_value
    if (Path(home) / 'kg.json').is_file():
        return {}
    values = {key: env_value(key) for key in ('neo4j_url', 'user', 'password', 'database')}
    if not values['password']:
        if non_interactive or not sys.stdin.isatty():
            raise DeploymentError('New installation needs Neo4j credentials; set KG_PASSWORD or run interactively')
        values['neo4j_url'] = values['neo4j_url'] or prompt('Neo4j Bolt URL [bolt://127.0.0.1:7687]: ').strip() or 'bolt://127.0.0.1:7687'
        values['user'] = values['user'] or prompt('Neo4j user [neo4j]: ').strip() or 'neo4j'
        values['database'] = values['database'] or prompt('Neo4j database [neo4j]: ').strip() or 'neo4j'
        values['password'] = secret('Neo4j password (stored only in this new profile): ')
    if not values['password']:
        raise DeploymentError('Neo4j password cannot be empty')
    return {'KG_BOLT_URL': values['neo4j_url'] or 'bolt://127.0.0.1:7687', 'KG_USER': values['user'] or 'neo4j',
            'KG_DATABASE': values['database'] or 'neo4j', 'KG_PASSWORD': values['password']}


def require_free_port(port):
    with socket.socket() as listener:
        try:
            listener.bind(('127.0.0.1', port))
        except OSError as exc:
            raise DeploymentError('Console port is in use; refusing to stop an unverified process') from exc


class ProcessExit:
    """Wait for the authenticated process without signalling a disk-supplied PID."""
    def __init__(self, pid):
        self.pid, self.handle = int(pid), None
    def __enter__(self):
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            self.api = ctypes.WinDLL('kernel32', use_last_error=True)
            self.api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self.api.OpenProcess.restype = wintypes.HANDLE
            self.api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self.api.CloseHandle.argtypes = [wintypes.HANDLE]
            self.handle = self.api.OpenProcess(0x00100000, False, self.pid)
            if not self.handle and ctypes.get_last_error() not in {87, 1168}:
                raise DeploymentError('Cannot wait for the verified console process; deployment aborted')
        return self
    def wait(self, timeout=30):
        if os.name == 'nt':
            if self.handle and self.api.WaitForSingleObject(self.handle, int(timeout * 1000)) != 0:
                raise DeploymentError('Verified console still has running work; wait for it to finish and retry')
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(self.pid, 0)
            except ProcessLookupError:
                return
            except PermissionError as exc:
                raise DeploymentError('Cannot verify console exit') from exc
            stat = Path('/proc') / str(self.pid) / 'stat'
            if stat.exists() and stat.read_text().rsplit(')', 1)[1].lstrip().startswith('Z'):
                return
            time.sleep(.1)
        raise DeploymentError('Verified console still has running work; wait for it to finish and retry')
    def __exit__(self, *args):
        if self.handle:
            self.api.CloseHandle(self.handle)


def read_json_response(url, *, headers=None, data=None, timeout=3):
    request = urllib.request.Request(url, headers=headers or {}, data=data)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise DeploymentError('Service returned an oversized response')
        return json.loads(raw)


RUNTIME_BOOTSTRAP = r'''
import os,site,sys
from pathlib import Path
runtime=Path(sys.argv[1]).resolve()
home=Path(sys.argv[2]).resolve()
program=sys.argv[3]
sys.prefix=sys.exec_prefix=str(runtime)
site.addsitedir(str(runtime/('Lib/site-packages' if os.name=='nt' else 'lib/python3.11/site-packages')))
sys.path.insert(0,str(home/'plugins'))
core=os.environ.get('HERMES_AGENT_HOME')
if core:
    sys.path.insert(1,str(Path(core).resolve()))
sys.executable=str(runtime/('Scripts/python.exe' if os.name=='nt' else 'bin/python'))
sys.argv=['kg-managed-console','serve-managed','--home',str(home)]
exec(compile(program,'<kg-runtime-bootstrap>','exec'))
'''


def runtime_bootstrap_command(base, runtime, home, program):
    """Run the base image directly; Windows venv redirectors fork a second PID."""
    return [str(base), '-I', '-S', '-B', '-X', 'utf8', '-c', RUNTIME_BOOTSTRAP, str(runtime), str(home), program]


def console_command(python, home):
    python, home = Path(python), Path(home)
    if os.name != 'nt':
        return [str(python), '-X', 'utf8', '-m', 'kg.deployment', 'serve-managed', '--home', str(home)]
    runtime = python.parent.parent
    contained(home, runtime)
    try:
        values = {}
        for line in (runtime / 'pyvenv.cfg').read_text(encoding='utf-8-sig').splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                values[key.strip().lower()] = value.strip().strip('"\'')
        candidates = [Path(values[key]) for key in ('executable', 'base-executable') if values.get(key)]
        if values.get('home'):
            candidates.append(Path(values['home']) / 'python.exe')
        base = next(path.resolve() for path in candidates if path.is_file() and path.resolve().is_relative_to((home / 'kg-runtime/python').resolve()))
    except (OSError, StopIteration, ValueError) as exc:
        raise DeploymentError('Isolated runtime base interpreter could not be verified; no console was started') from exc
    return runtime_bootstrap_command(base, runtime, home, 'from kg.deployment import main; raise SystemExit(main())')


class ServiceController:
    def __init__(self, options, python=None, env=None, run=command):
        self.options, self.python, self.env, self.run = options, python, env, run
        self.record_path = options.home / 'kg-deploy/console.json'

    def identity(self, record):
        if record.get('profile') != str(self.options.home) or not isinstance(record.get('control_port'), int):
            raise DeploymentError('Unrecognized console metadata; migrate the legacy service before deploying')
        try:
            identity = read_json_response('http://127.0.0.1:' + str(record['control_port']) + '/identity', headers={'X-KG-Control': record['nonce']})
        except Exception as exc:
            raise DeploymentError('Managed console identity could not be verified; no process was stopped') from exc
        for key in ('instance', 'pid', 'profile', 'release'):
            if identity.get(key) != record.get(key):
                raise DeploymentError('Console ownership does not match this profile; no process was stopped')
        return identity

    def stop(self):
        if not self.record_path.exists():
            try:
                require_free_port(self.options.port)
                return None
            except DeploymentError:
                from .deployment_legacy import migrate_legacy
                def stopped(record):
                    self.stopped_record = record
                return migrate_legacy(self.options, self.env, self.run, on_stopped=stopped)
        record = json.loads(self.record_path.read_text(encoding='utf-8'))
        try:
            self.identity(record)
        except DeploymentError:
            # A stale record is safe to discard only when its configured port
            # is free. We still never signal the recorded (possibly reused) PID.
            require_free_port(int(record.get('port', self.options.port)))
            self.record_path.unlink()
            return None
        with ProcessExit(record['pid']) as process:
            read_json_response('http://127.0.0.1:' + str(record['control_port']) + '/stop', headers={'X-KG-Control': record['nonce']}, data=b'{}')
            process.wait()
        for _ in range(150):
            try:
                require_free_port(int(record['port']))
                break
            except DeploymentError:
                time.sleep(.1)
        else:
            raise DeploymentError('Managed console did not stop gracefully; deployment aborted')
        self.record_path.unlink(missing_ok=True)
        return record

    def start(self, release):
        require_free_port(self.options.port)
        record = {'format': 1, 'profile': str(self.options.home), 'instance': uuid.uuid4().hex, 'nonce': secrets.token_urlsafe(32),
                  'port': self.options.port, 'release': release, 'python': str(self.python), 'pid': None}
        atomic_json(self.record_path, record)
        log = self.options.home / 'kg-deploy/console.log'
        kwargs = {'cwd': self.options.home, 'env': self.env, 'stdin': subprocess.DEVNULL}
        if os.name == 'nt':
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        else:
            kwargs['start_new_session'] = True
        with log.open('ab') as output:
            child = subprocess.Popen(console_command(self.python, self.options.home),
                                     stdout=output, stderr=output, **kwargs)
        try:
            for _ in range(300):
                if child.poll() is not None:
                    raise DeploymentError('Managed console exited during startup; inspect the private console log')
                try:
                    running = json.loads(self.record_path.read_text(encoding='utf-8'))
                    if running.get('pid') == child.pid and running.get('instance') == record['instance'] and running.get('control_port'):
                        self.identity(running)
                        return running
                except (OSError, ValueError, DeploymentError):
                    pass
                time.sleep(.1)
            raise DeploymentError('Managed console startup timed out')
        except Exception:
            # This handle belongs to the child just created by us, unlike a PID
            # read from disk. Do not leave a late-starting child after rollback.
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=15)
            self.record_path.unlink(missing_ok=True)
            raise

    def probe(self, manifest, *, model=True):
        home = self.options.home
        token = os.environ.get('KG_CONSOLE_TOKEN') or os.environ.get('HERMES_KG_CONSOLE_TOKEN') or (home / 'kg-console.token').read_text(encoding='utf-8').strip()
        base = 'http://127.0.0.1:' + str(self.options.port)
        headers = {'Authorization': 'Bearer ' + token}
        for _ in range(120):
            try:
                status = read_json_response(base + '/api/v1/status', headers=headers)
                if status.get('connected') and (not model or status.get('search', {}).get('model_ready') is True):
                    break
            except Exception:
                pass
            time.sleep(.25)
        else:
            raise DeploymentError('Authenticated console/database health check failed')
        try:
            read_json_response(base + '/api/v1/status')
        except urllib.error.HTTPError as exc:
            if exc.code not in {401, 403}:
                raise DeploymentError('Console authentication check failed') from exc
        else:
            raise DeploymentError('Console API unexpectedly accepts unauthenticated requests')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        static = home / 'plugins/kg/static'
        for path in asset_files(static):
            with opener.open(base + '/' + path.relative_to(static).as_posix(), timeout=5) as response:
                if hashlib.sha256(response.read(20_000_001)).hexdigest() != digest(path):
                    raise DeploymentError('Running console does not serve the deployed frontend')
        if model and status.get('search', {}).get('model_ready') is not True:
            raise DeploymentError('Local semantic model is not ready after deployment')
        return {'authenticated_api': True, 'database': True, 'static_assets': True, 'semantic_model': status.get('search', {}).get('model_ready') is True}

    def gateway(self):
        from .deployment_gateway import restart_gateway
        return restart_gateway(self.options.home, self.options.core, self.options.hermes_python, self.env, self.run)

    def restore(self, record):
        if record.get('legacy'):
            from .deployment_legacy import restart_legacy
            return restart_legacy(self.options, record, run=self.run)
        self.python = Path(record['python'])
        return self.start(record.get('release'))


def managed_console(home):
    """Private control endpoint only commands the process which created it."""
    from .config import load_config
    from .console_web import create_app
    import uvicorn
    home = Path(home).resolve()
    path = home / 'kg-deploy/console.json'
    record = json.loads(path.read_text(encoding='utf-8'))
    if record.get('profile') != str(home) or record.get('pid') is not None:
        raise DeploymentError('Managed console launch metadata is not valid')
    server = uvicorn.Server(uvicorn.Config(create_app(load_config(home=home)), host='127.0.0.1', port=int(record['port']), log_level='warning', access_log=False))
    class Control(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.respond(False)
        def do_POST(self):
            self.respond(True)
        def respond(self, stop):
            import hmac
            if not hmac.compare_digest(self.headers.get('X-KG-Control', ''), record['nonce']):
                self.send_error(403)
                return
            if self.path != ('/stop' if stop else '/identity'):
                self.send_error(404)
                return
            data = json.dumps({key: record[key] for key in ('instance', 'pid', 'profile', 'release')}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            if stop:
                server.should_exit = True
    control = ThreadingHTTPServer(('127.0.0.1', 0), Control)
    control.daemon_threads = True
    record.update(pid=os.getpid(), control_port=control.server_port)
    atomic_json(path, record)
    thread = threading.Thread(target=control.serve_forever, daemon=True)
    thread.start()
    try:
        server.run()
    finally:
        control.shutdown()
        control.server_close()
        # Never remove metadata belonging to a later deployment instance.
        if path.exists() and json.loads(path.read_text(encoding='utf-8')).get('instance') == record['instance']:
            path.unlink()


class PayloadTransaction:
    def __init__(self, home, stage, backup):
        self.home, self.stage, self.backup = Path(home), Path(stage), Path(backup)
        self.changes = []

    def install(self):
        for relative in PAYLOAD:
            target = contained(self.home, self.home / relative)
            source = contained(self.stage, self.stage / relative)
            saved = contained(self.backup, self.backup / relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            existed = target.exists()
            if existed:
                saved.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, saved)
            self.changes.append((target, saved, existed))
            os.replace(source, target)

    def rollback(self):
        for target, saved, existed in reversed(self.changes):
            if target.exists():
                remove_managed(self.home, target)
            if existed:
                os.replace(saved, target)


def startup_plan(options, python):
    if options.skip_services:
        if options.autostart:
            raise DeploymentError('Login startup requires service verification; do not combine --autostart with --skip-services')
        return None
    from .deployment_startup import plan_startup
    return plan_startup(options.home, python, autostart=options.autostart)


def startup_apply(plan, backup):
    if plan is None:
        return {}
    from .deployment_startup import apply_startup
    return apply_startup(plan, backup)


def startup_rollback(plan):
    if plan is not None:
        from .deployment_startup import rollback_startup
        rollback_startup(plan)


def deploy(options, *, run=command, services=None, build=build_frontend, runtime=prepare_runtime, stage=stage_release):
    options.home = Path(options.home).expanduser().resolve()
    options.source = Path(options.source).expanduser().resolve()
    home, source = options.home, options.source
    options.port = deployment_port(home, options.port)
    if not 1024 <= options.port <= 65535:
        raise DeploymentError('Console port must be between 1024 and 65535')
    if home == source or home.is_relative_to(source) or source.is_relative_to(home):
        raise DeploymentError('Source checkout and target profile must be separate directories')
    required = (source / 'requirements-console.lock', source / 'console/package-lock.json', source / 'plugins/kg/requirements.lock')
    if not all(path.is_file() for path in required):
        raise DeploymentError('Deployment requires a complete source checkout')
    if options.dry_run:
        return {'ok': True, 'dry_run': True, 'steps': ['build_frontend', 'prepare_isolated_python_3.11', 'stage_release', 'setup_and_doctor',
                'prepare_model' if not options.skip_model else 'model_skipped', 'activate_provider', 'managed_console' if not options.skip_services else 'services_skipped',
                'gateway_restart' if not options.skip_services and not options.skip_restart else 'gateway_skipped', 'authenticated_http_and_static_probe'],
                'writes': False}
    home.mkdir(parents=True, exist_ok=True)
    for relative in (*PAYLOAD, 'kg-deploy', 'kg-runtime', 'config.yaml', 'kg.json', 'kg-console.json', 'kg-console.token'):
        contained(home, home / relative)
    # Bootstrap must run on a stdlib-only Python before the isolated runtime
    # exists. Do not import store/maintenance or any third-party dependency.
    if (home / 'kg-sync/restore-in-progress.json').exists():
        raise DeploymentError('A restore is incomplete; finish it before deployment')
    guard_existing_environment(home)
    connection = initial_connection(home, non_interactive=options.non_interactive)
    settings = runtime_settings(home)
    with DeploymentLock(home):
        journal_path = home / 'kg-deploy/transaction.json'
        if journal_path.exists():
            raise DeploymentError('An interrupted deployment needs recovery; inspect kg-deploy/transaction.json and its payload backup before retrying')
        uv = find_uv(home)
        print('Building frontend and preparing isolated Python 3.11 runtime…')
        build(source, run=run)
        python = runtime(home, source / 'requirements-console.lock', uv, run=run)
        env = profile_environment(home, options.core, options.hermes_python, settings, connection)
        services = services or ServiceController(options, python, env, run)
        services.python, services.env = python, env
        instance = uuid.uuid4().hex
        work = home / 'kg-deploy/staging' / instance
        backup = home / 'kg-deploy/backups' / instance
        work.mkdir(parents=True)
        try:
            stage(source, work, python, uv, run=run)
        except Exception:
            remove_managed(home, work)
            raise
        manifest = release_manifest(source, work, python, run=run)
        manifest.update(hermes_core=str(options.core) if options.core else None,
                        hermes_python=str(options.hermes_python) if options.hermes_python else None,
                        model_required=not options.skip_model)
        old_manifest_path = home / 'kg-deploy/manifest.json'
        old_manifest = old_manifest_path.read_bytes() if old_manifest_path.exists() else None
        previous = {}
        for relative in ('config.yaml', 'kg.json', 'kg-console.json', 'kg-console.token'):
            path = home / relative
            previous[relative] = path.read_bytes() if path.exists() else None
        pending_path = home / 'kg-deploy/pending-install.json'
        pending = json.loads(pending_path.read_text(encoding='utf-8')) if pending_path.exists() else None
        previous_config = json.loads(previous['kg.json'].decode('utf-8-sig')) if previous['kg.json'] is not None else None
        retrying_first_install = bool(pending and previous_config and pending.get('identity_sha256') == identity_hash(previous_config))
        patched = patch_provider((previous['config.yaml'] or b'').decode('utf-8-sig'))
        previous_console = None
        transaction = PayloadTransaction(home, work, backup)
        started = False
        config_written = False
        gateway_attempted = False
        login_plan = None
        login_attempted = False
        runner_lock = SyncRunnerLock(home)
        try:
            login_plan = startup_plan(options, python)
            runner_lock.__enter__()
        except Exception:
            remove_managed(home, work)
            raise
        try:
            # Save profile files before the first rename, so an OS interruption
            # still has a recoverable payload and byte-exact configuration.
            for relative, value in previous.items():
                if value is not None:
                    target = backup / 'profile' / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(value)
                    target.chmod(0o600)
            atomic_json(journal_path, {'format': 1, 'backup_id': instance, 'state': 'prepared', 'payload': list(PAYLOAD),
                                      'existing_payload': {relative: (home / relative).exists() for relative in PAYLOAD},
                                      'startup': {key: login_plan.get(key) for key in ('kind', 'before_sha256', 'after_sha256')} if login_plan else None})
            if not options.skip_services:
                previous_console = services.stop()
            transaction.install()
            def cli(*args, timeout=900):
                output = run([python, '-X', 'utf8', '-m', 'kg.console_cli', '--home', home, *args], env=env, cwd=home, timeout=timeout)
                try:
                    value = json.loads(output)
                except ValueError as exc:
                    raise DeploymentError('Console deployment check returned an invalid response') from exc
                if not isinstance(value, dict) or value.get('ok') is False:
                    raise DeploymentError('Console deployment check did not pass')
                return value
            print('Checking database, schema and local model…')
            cli('setup', '--name', options.name)
            # setup may complete an earlier onboarding marker. A deployment
            # never changes the bytes of an already-existing connection file.
            if previous['kg.json'] is not None:
                if retrying_first_install:
                    previous_config['enabled'] = True
                    atomic_json(home / 'kg.json', previous_config)
                else:
                    (home / 'kg.json').write_bytes(previous['kg.json'])
            elif connection.get('KG_PASSWORD'):
                config = json.loads((home / 'kg.json').read_text(encoding='utf-8'))
                config['password'] = connection['KG_PASSWORD']
                config['console_url'] = 'http://127.0.0.1:' + str(options.port)
                atomic_json(home / 'kg.json', config)
            cli('doctor')
            if not options.skip_model:
                cli('prepare-model', timeout=1800)
            config_target = home / 'config.yaml'
            current_config = config_target.read_bytes() if config_target.exists() else None
            if current_config != previous['config.yaml']:
                raise DeploymentError('Hermes config changed during deployment; retry after resolving concurrent edits')
            temporary = home / 'kg-deploy/config.yaml.pending'
            temporary.write_text(patched, encoding='utf-8', newline='')
            temporary.chmod(0o600)
            os.replace(temporary, config_target)
            config_written = True
            atomic_json(old_manifest_path, manifest)
            login_attempted = True
            login_result = startup_apply(login_plan, backup / 'startup')
            health = None
            gateway_health = None
            if not options.skip_services:
                started = True
                services.start(manifest['source_sha256'])
                health = services.probe(manifest, model=not options.skip_model)
                if not options.skip_restart:
                    gateway_attempted = True
                    gateway_health = services.gateway()
            result = {'ok': True, 'complete': not (options.skip_services or options.skip_restart or options.skip_model),
                      'release_sha': manifest['release_sha'], 'working_tree_dirty': manifest['working_tree_dirty'], 'source_sha256': manifest['source_sha256'], 'bundle_sha256': manifest['bundle_sha256'],
                      'services_skipped': options.skip_services, 'gateway_restart_skipped': options.skip_services or options.skip_restart,
                      'model_skipped': options.skip_model, 'health': health, 'gateway': gateway_health, 'process_startup': 'detached_process_only', 'backup_id': instance,
                      'legacy_migrated': bool(previous_console and previous_console.get('legacy')),
                      'legacy_stop_mode': previous_console.get('stop_mode') if previous_console and previous_console.get('legacy') else None,
                      'startup': {key: login_result[key] for key in ('kind', 'enabled', 'migrated', 'installed') if key in login_result}}
            atomic_json(home / 'kg-deploy/last-result.json', result)
            pending_path.unlink(missing_ok=True)
            journal_path.unlink()
            return result
        except Exception as original:
            failures = []
            previous_console = previous_console or getattr(services, 'stopped_record', None)
            if login_attempted:
                try:
                    startup_rollback(login_plan)
                except Exception:
                    failures.append('console login startup rollback')
            if started:
                try:
                    services.stop()
                except Exception:
                    failures.append('new console stop')
            try:
                transaction.rollback()
            except Exception:
                failures.append('payload rollback')
            for relative, value in previous.items():
                try:
                    target = home / relative
                    if relative == 'config.yaml' and not config_written:
                        continue
                    if relative == 'config.yaml' and target.exists() and target.read_bytes() != patched.encode('utf-8'):
                        failures.append('concurrently edited Hermes configuration')
                        continue
                    if value is None:
                        # A failed first setup must retain its generated private
                        # identity/history boundary for a safe retry. Deleting
                        # these would orphan any already-created DB namespace.
                        if relative == 'config.yaml':
                            target.unlink(missing_ok=True)
                        elif relative == 'kg.json' and target.exists():
                            new_config = json.loads(target.read_text(encoding='utf-8'))
                            new_config['enabled'] = False
                            if connection.get('KG_PASSWORD'):
                                new_config['password'] = connection['KG_PASSWORD']
                            new_config['console_url'] = 'http://127.0.0.1:' + str(options.port)
                            atomic_json(target, new_config)
                            atomic_json(pending_path, {'format': 1, 'identity_sha256': identity_hash(new_config)})
                    else:
                        target.write_bytes(value)
                except Exception:
                    failures.append('profile rollback')
            try:
                if old_manifest is None:
                    old_manifest_path.unlink(missing_ok=True)
                else:
                    old_manifest_path.write_bytes(old_manifest)
                if previous_console:
                    services.restore(previous_console)
                if gateway_attempted:
                    services.gateway()
            except Exception:
                failures.append('previous services restart')
            if failures:
                raise DeploymentError('Deployment failed; manual recovery needed for: ' + ', '.join(failures)) from original
            journal_path.unlink(missing_ok=True)
            if isinstance(original, DeploymentError):
                raise
            raise DeploymentError('Deployment failed; previous payload and configuration restored') from original
        finally:
            try:
                if work.exists():
                    remove_managed(home, work)
            finally:
                runner_lock.__exit__(None, None, None)


def main(argv=None, *, source=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'serve-managed':
        parser = argparse.ArgumentParser()
        parser.add_argument('command')
        parser.add_argument('--home', required=True)
        args = parser.parse_args(argv)
        managed_console(args.home)
        return 0
    parser = argparse.ArgumentParser(description='Install or upgrade KG in an existing Hermes profile. Requires reachable Neo4j; does not install Hermes or Neo4j.')
    parser.add_argument('--home', required=True)
    parser.add_argument('--core')
    parser.add_argument('--hermes-python')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--skip-services', action='store_true')
    parser.add_argument('--skip-restart', action='store_true')
    parser.add_argument('--skip-model', action='store_true')
    parser.add_argument('--non-interactive', action='store_true')
    parser.add_argument('--port', type=int)
    parser.add_argument('--name', default='我')
    parser.add_argument('--autostart', action='store_true', help='Explicitly install console login startup; an existing verified KG startup item is migrated automatically')
    args = parser.parse_args(argv)
    try:
        from .hermes_runtime import discover_core
        core = Path(args.core).expanduser().resolve() if args.core else discover_core(args.home)
        host_python = Path(args.hermes_python).expanduser().resolve() if args.hermes_python else None
        if not args.dry_run and not args.skip_services and not args.skip_restart:
            if core is None:
                raise DeploymentError('Hermes core not found; set HERMES_AGENT_HOME or pass --core')
            host_python = discover_host_python(args.home, core, host_python)
        options = DeployOptions(Path(args.home), Path(source or Path(__file__).resolve().parents[2]), core, host_python,
                                args.dry_run, args.skip_services, args.skip_restart, args.skip_model, args.non_interactive, args.port, args.name, args.autostart)
        result = deploy(options)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        message = str(exc) if isinstance(exc, DeploymentError) else 'Deployment failed; inspect prerequisites and the private deployment backup'
        print(json.dumps({'ok': False, 'error': message}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
