"""Adopt only the known profile-owned legacy console, never a port's unknown PID.

Windows legacy launchers have no graceful control channel. After independent OS
ownership, process-generation, listener and idle checks, migration explicitly
terminates that verified process handle. POSIX uses SIGTERM (pidfd when present).
"""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import shlex
import signal
import sqlite3
import subprocess
import time


def error(message):
    from .deployment import DeploymentError
    return DeploymentError(message)


def same_path(left, right):
    return os.path.normcase(str(Path(left).expanduser().resolve())) == os.path.normcase(str(Path(right).expanduser().resolve()))


def python_images(configured):
    configured = Path(configured).expanduser()
    if not configured.is_file():
        raise error('Legacy console interpreter metadata is unavailable')
    result = {str(configured.resolve())}
    for folder in (configured.parent, configured.parent.parent):
        config = folder / 'pyvenv.cfg'
        if not config.is_file():
            continue
        values = {}
        for line in config.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                values[key.strip().lower()] = value.strip().strip('"\'')
        for key in ('executable', 'base-executable'):
            if values.get(key) and Path(values[key]).is_file():
                result.add(str(Path(values[key]).resolve()))
        if values.get('home'):
            for name in ('python.exe', 'python', 'python3'):
                candidate = Path(values['home']) / name
                if candidate.is_file():
                    result.add(str(candidate.resolve()))
    return result


def windows_argv(value):
    import ctypes
    from ctypes import wintypes
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    count = ctypes.c_int()
    pointer = shell.CommandLineToArgvW(value, ctypes.byref(count))
    if not pointer:
        raise error('Legacy console command line could not be parsed')
    try:
        return [pointer[index] for index in range(count.value)]
    finally:
        api = ctypes.WinDLL('kernel32')
        api.LocalFree.argtypes = [wintypes.HLOCAL]
        api.LocalFree(ctypes.cast(pointer, wintypes.HLOCAL))


def process_snapshot(pid, port, run):
    """Return actual argv/image/creation generation and ownership of the listener."""
    pid, port = int(pid), int(port)
    if pid <= 0 or not 1024 <= port <= 65535:
        raise error('Legacy console process metadata is invalid')
    if os.name == 'nt':
        script = (
            '[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);'
            f'$p=Get-CimInstance Win32_Process -Filter "ProcessId = {pid}" -ErrorAction Stop;'
            'if($null -eq $p){exit 2};'
            f'$listeners=@(Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess -Unique);'
            '@{pid=[int]$p.ProcessId;exe=$p.ExecutablePath;command_line=$p.CommandLine;'
            'created=$p.CreationDate.ToFileTimeUtc().ToString();listeners=$listeners}|ConvertTo-Json -Compress'
        )
        try:
            result = json.loads(run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script], timeout=30))
            result['argv'] = windows_argv(result.pop('command_line'))
            return result
        except Exception as exc:
            raise error('OS ownership of the legacy console could not be verified; no process was stopped') from exc
    proc = Path('/proc') / str(pid)
    if proc.is_dir():
        try:
            fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
            inodes = set()
            for fd in (proc / 'fd').iterdir():
                try:
                    value = os.readlink(fd)
                except OSError:
                    continue
                if value.startswith('socket:['):
                    inodes.add(value[8:-1])
            owns_listener = False
            for name in ('tcp', 'tcp6'):
                table = proc / 'net' / name
                if table.exists():
                    for line in table.read_text().splitlines()[1:]:
                        row = line.split()
                        if len(row) > 9 and row[3] == '0A' and int(row[1].rsplit(':', 1)[1], 16) == port and row[9] in inodes:
                            owns_listener = True
            return {'pid': pid, 'exe': str((proc / 'exe').resolve()), 'argv': (proc / 'cmdline').read_bytes().rstrip(b'\0').decode().split('\0'),
                    'created': fields[19], 'listeners': [pid] if owns_listener else []}
        except (OSError, ValueError, IndexError) as exc:
            raise error('OS ownership of the legacy console could not be verified; no process was stopped') from exc
    # macOS has no procfs. Only one explicitly selected PID is inspected.
    try:
        argv = shlex.split(run(['ps', '-p', str(pid), '-o', 'command='], timeout=10))
        created = run(['ps', '-p', str(pid), '-o', 'lstart='], timeout=10).strip()
        listeners = run(['lsof', '-nP', '-a', '-p', str(pid), '-iTCP:' + str(port), '-sTCP:LISTEN', '-Fp'], timeout=10)
        return {'pid': pid, 'exe': argv[0], 'argv': argv, 'created': created, 'listeners': [pid] if 'p' + str(pid) in listeners.splitlines() else []}
    except Exception as exc:
        raise error('OS ownership of the legacy console could not be verified; no process was stopped') from exc


def validate_snapshot(snapshot, home, python, port):
    expected = Path(home) / 'scripts/kg_console_local.py'
    argv = snapshot.get('argv')
    images = python_images(python)
    if (not expected.is_file() or not isinstance(argv, list) or len(argv) != 4
            or set(argv[1:3]) != {'-B', '-u'} or not same_path(argv[3], expected)
            or not any(same_path(snapshot.get('exe', ''), candidate) for candidate in images)
            or not any(same_path(argv[0], candidate) for candidate in images)
            or not snapshot.get('created') or snapshot.get('listeners') != [snapshot.get('pid')]):
        raise error('Legacy console OS identity/listener does not match this profile; no process was stopped')


def assert_idle(home, port, request):
    from .config import env_value, load_config
    from .deployment import contained
    token_path = contained(home, Path(home) / 'kg-console.token')
    token = env_value('console_token') or token_path.read_text(encoding='utf-8').strip()
    if not token or len(token) < 16:
        raise error('Legacy console authentication is unavailable')
    headers = {'Authorization': 'Bearer ' + token}
    base = 'http://127.0.0.1:' + str(port)
    try:
        status = request(base + '/api/v1/status', headers=headers)
        schema = request(base + '/api/v1/ontology', headers=headers)
        jobs = request(base + '/api/v1/jobs', headers=headers)
        config = load_config(home=home)
        if schema.get('namespace') != config.urn_prefix:
            raise error('Legacy console namespace does not match the target profile')
        sync = status.get('sync', {})
        if (status.get('connected') is not True or sync.get('available') is not True
                or any(isinstance(sync.get(key), bool) or not isinstance(sync.get(key), int) or sync[key] < 0 for key in ('queued_count', 'pending_count'))
                or not isinstance(sync.get('jobs'), list) or not isinstance(jobs.get('items'), list)):
            raise error('Legacy console activity state is unknown; wait and retry')
        # queued_count/pending_count describe durable sessions/snapshots. They
        # are preserved during upgrade; only actual ConsoleState workers block.
        # The enclosing deployment holds runner.lock through service acceptance
        # so a separate cron runner cannot start between this check and swap.
        if any(not isinstance(job, dict) or job.get('status') not in {'succeeded', 'completed', 'failed', 'interrupted', 'cancelled'} for job in [*sync['jobs'], *jobs['items']]):
            raise error('Legacy console has running or queued jobs; wait and retry')
        # API lists are bounded. Check all durable job statuses without reading
        # any job content, so an older running job cannot hide behind 30 results.
        ledger = contained(home, Path(home) / 'kg-sync/console.db')
        if not ledger.is_file():
            raise error('Legacy console job ledger is unavailable; idle state cannot be verified')
        with contextlib.closing(sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
            db.execute('PRAGMA query_only=ON')
            if db.execute("SELECT count(*) FROM jobs WHERE status NOT IN ('succeeded','completed','failed','interrupted','cancelled') OR status IS NULL").fetchone()[0]:
                raise error('Legacy console has unfinished durable jobs; wait and retry')
    except Exception as exc:
        from .deployment import DeploymentError
        if isinstance(exc, DeploymentError):
            raise
        raise error('Legacy console authenticated identity/idle check failed; no process was stopped') from exc


class VerifiedProcess:
    """Hold the same Windows process generation across verification and stop."""
    def __init__(self, snapshot):
        self.snapshot, self.handle, self.fd = snapshot, None, None
    def __enter__(self):
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            self.api = ctypes.WinDLL('kernel32', use_last_error=True)
            self.api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self.api.OpenProcess.restype = wintypes.HANDLE
            self.api.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
            self.api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            self.api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self.api.CloseHandle.argtypes = [wintypes.HANDLE]
            self.handle = self.api.OpenProcess(0x00100000 | 0x1000 | 0x0001, False, int(self.snapshot['pid']))
            if not self.handle:
                raise error('Verified legacy process is no longer accessible; no process was stopped')
            values = [wintypes.FILETIME() for _ in range(4)]
            if not self.api.GetProcessTimes(self.handle, *(ctypes.byref(value) for value in values)):
                self.__exit__()
                raise error('Legacy process creation time could not be verified')
            created = (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime
            if created // 10 != int(self.snapshot['created']) // 10:
                self.__exit__()
                raise error('Legacy PID was reused; no process was stopped')
        elif hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'):
            self.fd = os.pidfd_open(int(self.snapshot['pid']))
        return self
    def terminate(self):
        if os.name == 'nt':
            if not self.api.TerminateProcess(self.handle, 0):
                raise error('Verified legacy process could not be terminated')
            if self.api.WaitForSingleObject(self.handle, 30000) != 0:
                raise error('Verified legacy process did not exit')
        else:
            if self.fd is not None:
                signal.pidfd_send_signal(self.fd, signal.SIGTERM)
            else:
                os.kill(int(self.snapshot['pid']), signal.SIGTERM)
            from .deployment import ProcessExit
            with ProcessExit(self.snapshot['pid']) as process:
                process.wait()
    def __exit__(self, *args):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def migrate_legacy(options, env, run, *, inspect=process_snapshot, process_factory=VerifiedProcess, request=None, on_stopped=None):
    from .deployment import contained, read_json_response, runtime_settings, profile_environment, require_free_port
    request = request or read_json_response
    home = options.home
    pid_path = contained(home, home / 'kg-console.pid')
    runtime_path = contained(home, home / 'kg-console-runtime.json')
    try:
        metadata = json.loads(pid_path.read_text(encoding='utf-8-sig'))
        runtime = json.loads(runtime_path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise error('Console port belongs to an unrecognized process; no process was stopped') from exc
    if (metadata.get('kind') != 'kg-console' or not metadata.get('home') or not same_path(metadata['home'], home)
            or isinstance(metadata.get('pid'), bool) or not isinstance(metadata.get('pid'), int)
            or runtime.get('port') != options.port or not isinstance(runtime.get('python'), str)):
        raise error('Legacy console metadata does not match this profile; no process was stopped')
    snapshot = inspect(metadata['pid'], options.port, run)
    if snapshot.get('pid') != metadata['pid']:
        raise error('Legacy console PID identity does not match')
    validate_snapshot(snapshot, home, runtime['python'], options.port)
    restore_env = profile_environment(home, options.core, runtime.get('hermes_python') or options.hermes_python, runtime_settings(home))
    # Keep any explicit target-profile environment supplied to this deployment,
    # then reapply the old runtime's scheduler/model/host selection.
    old_settings_env = {key: value for key, value in restore_env.items() if key in {'KG_HOME', 'HERMES_HOME', 'KG_MODEL_CACHE', 'KG_SYNC_SCHEDULER_ENABLED', 'KG_HERMES_PYTHON', 'HERMES_AGENT_HOME', 'PYTHONPATH'}}
    restart_env = dict(env, **old_settings_env)
    record = {'legacy': True, 'profile': str(home), 'pid': metadata['pid'], 'port': options.port,
              'python': runtime['python'], 'argv': snapshot['argv'], 'created': snapshot['created'], 'restart_env': restart_env,
              'release': None, 'stop_mode': 'verified_idle_termination' if os.name == 'nt' else 'verified_sigterm'}
    with process_factory(snapshot) as process:
        assert_idle(home, options.port, request)
        current = inspect(metadata['pid'], options.port, run)
        validate_snapshot(current, home, runtime['python'], options.port)
        if current != snapshot or json.loads(pid_path.read_text(encoding='utf-8-sig')) != metadata:
            raise error('Legacy console changed during verification; no process was stopped')
        assert_idle(home, options.port, request)
        process.terminate()
        if on_stopped:
            on_stopped(record)
    require_free_port(options.port)
    if json.loads(pid_path.read_text(encoding='utf-8-sig')) != metadata:
        raise error('Legacy console metadata changed after shutdown; deployment aborted')
    pid_path.unlink()
    return record


def restart_legacy(options, record, *, popen=subprocess.Popen, request=None, inspect=process_snapshot, run=None):
    from .deployment import atomic_json, read_json_response, command, require_free_port
    request, run = request or read_json_response, run or command
    if record.get('legacy') is not True or record.get('profile') != str(options.home):
        raise error('Legacy rollback identity is invalid')
    require_free_port(record['port'])
    kwargs = {'cwd': options.home, 'env': record['restart_env'], 'stdin': subprocess.DEVNULL}
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    else:
        kwargs['start_new_session'] = True
    log = options.home / 'kg-deploy/legacy-console.log'
    with log.open('ab') as output:
        child = popen(record['argv'], stdout=output, stderr=output, **kwargs)
    atomic_json(options.home / 'kg-console.pid', {'pid': child.pid, 'kind': 'kg-console', 'home': str(options.home)})
    for _ in range(120):
        if child.poll() is not None:
            raise error('Previous legacy console exited during rollback recovery')
        try:
            snapshot = inspect(child.pid, record['port'], run)
            validate_snapshot(snapshot, options.home, record['python'], record['port'])
            assert_idle(options.home, record['port'], request)
            return {'restored': True, 'legacy': True}
        except Exception:
            time.sleep(.25)
    raise error('Previous legacy console could not be verified after rollback; inspect the private legacy log')
