"""Profile-owned Windows login entry and idempotent managed-console bootstrap.

Only the one known KG VBS entry is considered. Other services and startup files
are never enumerated. Import and bootstrap use only Python's standard library.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid

from .deployment import (DeploymentError, DeploymentLock, DeployOptions, ServiceController,
                         contained, deployment_port, digest, guard_existing_environment,
                         profile_environment, require_free_port, runtime_settings)


ENTRY_NAME = 'Hermes_KG_Console.vbs'


def _same_path(left, right):
    return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(os.path.abspath(str(right)))


def windows_startup_directory():
    """Ask Windows for this user's Startup directory, not an inherited APPDATA."""
    if os.name != 'nt':
        raise DeploymentError('Windows login startup is not supported on this operating system')
    import ctypes
    from ctypes import wintypes
    buffer = ctypes.create_unicode_buffer(32768)
    api = ctypes.WinDLL('shell32', use_last_error=True)
    api.SHGetFolderPathW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR]
    if api.SHGetFolderPathW(None, 7, None, 0, buffer) != 0 or not buffer.value:
        raise DeploymentError('The current user Windows Startup directory could not be resolved')
    return Path(buffer.value)


def _entry_bytes(path):
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise DeploymentError('KG startup refuses a linked entry')
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 32_768:
        raise DeploymentError('KG startup entry is not a small regular file')
    return path.read_bytes()


def _command(content):
    try:
        text = content.decode('utf-16') if content.startswith((b'\xff\xfe', b'\xfe\xff')) else content.decode('utf-8-sig')
    except UnicodeError:
        return None
    match = re.fullmatch(r'\s*Set\s+([A-Za-z][A-Za-z0-9_]*)\s*=\s*(?:WScript\.)?CreateObject\("WScript\.Shell"\)\s*\r?\n'
                         r'\1\.Run\s+"((?:[^"\r\n]|"")*)"\s*,\s*0\s*,\s*False\s*', text, re.I)
    return match[2].replace('""', '"') if match else None


def _launcher(home, python):
    values = [str(python), str(home / 'scripts/kg_service.py'), str(home)]
    if any(any(marker in value for marker in ('"', '\r', '\n', '\0')) for value in values):
        raise DeploymentError('Startup paths contain unsupported command characters')
    command = '"' + values[0] + '" -B -X utf8 "' + values[1] + '" start --home "' + values[2] + '"'
    text = 'Set shell = CreateObject("WScript.Shell")\r\nshell.Run "' + command.replace('"', '""') + '", 0, False\r\n'
    return text.encode('utf-16')


def _known_command(home, command):
    """Accept exact argv shapes and profile metadata, never arbitrary VBS."""
    legacy = re.fullmatch(r'"([^"]+)" -B -u "([^"]+)"', command or '')
    if legacy and _same_path(legacy[2], home / 'scripts/kg_console_local.py'):
        path = contained(home, home / 'kg-console-runtime.json')
        try:
            previous = json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError) as exc:
            raise DeploymentError('Legacy KG startup names this profile but interpreter ownership cannot be verified') from exc
        if not isinstance(previous, dict) or not isinstance(previous.get('python'), str) or not _same_path(legacy[1], previous['python']):
            raise DeploymentError('Legacy KG startup interpreter does not match this profile metadata')
        return True
    managed = re.fullmatch(r'"([^"]+)" -B -X utf8 "([^"]+)" start --home "([^"]+)"', command or '')
    if managed and _same_path(managed[2], home / 'scripts/kg_service.py') and _same_path(managed[3], home):
        try:
            previous = json.loads(contained(home, home / 'kg-deploy/manifest.json').read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise DeploymentError('Managed startup ownership cannot be verified from the profile manifest') from exc
        return isinstance(previous.get('runtime_python'), str) and _same_path(managed[1], previous['runtime_python'])
    return False


def plan_startup(home, python, autostart=False, *, startup_dir=None, platform=None):
    """Plan only; existing exact owned entries migrate without a new opt-in."""
    home = Path(home).expanduser().resolve()
    if (platform or sys.platform) != 'win32':
        if autostart:
            raise DeploymentError('Automatic login startup is currently supported only on Windows')
        return {'kind': 'none', 'enabled': False}
    folder = Path(startup_dir) if startup_dir is not None else windows_startup_directory()
    folder = folder.expanduser().resolve()
    entry = contained(folder, folder / ENTRY_NAME)
    before = _entry_bytes(entry)
    if before is None and not autostart:
        return {'kind': 'none', 'enabled': False}
    owned = before is not None and _known_command(home, _command(before))
    if before is not None and not owned:
        if autostart:
            raise DeploymentError('An unrecognized KG-named Startup entry exists; it was not overwritten')
        return {'kind': 'none', 'enabled': False}
    after = _launcher(home, python)
    return {'kind': 'unchanged' if before == after else 'migrate' if before is not None else 'install',
            'enabled': True, 'home': str(home), 'entry': str(entry), 'before': before, 'after': after,
            'before_sha256': hashlib.sha256(before).hexdigest() if before is not None else None,
            'after_sha256': hashlib.sha256(after).hexdigest()}


def _write_entry(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + ENTRY_NAME + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def apply_startup(plan, backup_dir):
    kind = plan['kind']
    result = {'kind': kind, 'enabled': kind != 'none', 'migrated': kind == 'migrate', 'installed': kind == 'install'}
    if kind == 'none':
        return result
    entry = Path(plan['entry'])
    if _entry_bytes(entry) != plan['before']:
        raise DeploymentError('KG Startup entry changed during deployment; concurrent contents were preserved')
    if kind == 'unchanged':
        return result
    backup = contained(Path(plan['home']), Path(backup_dir))
    backup.mkdir(parents=True, exist_ok=True)
    if plan['before'] is not None:
        target = contained(backup, backup / ENTRY_NAME)
        if target.exists() and target.read_bytes() != plan['before']:
            raise DeploymentError('KG startup backup already contains different data')
        _write_entry(target, plan['before'])
        plan['backup_path'] = str(target)
    _write_entry(entry, plan['after'])
    plan['applied'] = True
    return result


def rollback_startup(plan):
    if plan['kind'] in {'none', 'unchanged'}:
        return
    entry = Path(plan['entry'])
    current = _entry_bytes(entry)
    if current == plan['before']:
        return
    if current != plan['after']:
        raise DeploymentError('KG Startup changed after deployment; concurrent contents were preserved for manual recovery')
    if plan['before'] is None:
        entry.unlink()
    else:
        _write_entry(entry, plan['before'])


def _installed_manifest(home):
    path = contained(home, home / 'kg-deploy/manifest.json')
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
        files = manifest['files']
        if (manifest.get('format') != 1 or not isinstance(files, dict) or not files
                or not {'plugins/kg/deployment.py', 'plugins/kg/deployment_startup.py', 'scripts/kg_service.py'} <= set(files)
                or not re.fullmatch(r'[0-9a-f]{64}', manifest['source_sha256'])
                or not re.fullmatch(r'[0-9a-f]{64}', manifest['lock_sha256'])
                or hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest() != manifest['source_sha256']):
            raise ValueError('Invalid manifest')
        for relative, expected in files.items():
            if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
                raise ValueError('Invalid file digest')
            target = contained(home, home / relative)
            if not target.is_file() or digest(target) != expected:
                raise ValueError('Installed release changed')
        python = Path(os.path.abspath(manifest['runtime_python']))
        runtime = home / 'kg-runtime' / manifest['lock_sha256'][:20]
        contained(home, runtime)
        if python not in (runtime / 'Scripts/python.exe', runtime / 'bin/python') or not python.is_file():
            raise ValueError('Runtime is outside its locked environment')
        if python.is_symlink():
            # uv's POSIX venv executable points to its managed base Python.
            # Permit only this verified leaf link, never linked payload paths
            # or interpreters outside the profile's managed Python tree.
            base = contained(home, home / 'kg-runtime/python')
            contained(base, python.resolve(strict=True))
        else:
            contained(home, python)
        marker = json.loads(contained(home, runtime / 'kg-runtime.json').read_text(encoding='utf-8'))
        if marker.get('lock_sha256') != manifest['lock_sha256']:
            raise ValueError('Runtime lock does not match')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DeploymentError('The installed KG release/runtime could not be verified; deploy it again before starting') from exc
    return manifest, python


def start(home, *, controller_factory=ServiceController):
    """Start the verified installed release; never restart another live process."""
    home = Path(home).expanduser().resolve()
    if not home.is_dir():
        raise DeploymentError('The KG profile has not been deployed')
    with DeploymentLock(home):
        if (home / 'kg-deploy/transaction.json').exists():
            raise DeploymentError('A deployment needs recovery before the console can start')
        manifest, python = _installed_manifest(home)
        guard_existing_environment(home)
        port = deployment_port(home)
        if not 1024 <= port <= 65535:
            raise DeploymentError('Managed console port must be between 1024 and 65535')
        settings = runtime_settings(home)
        core = manifest.get('hermes_core')
        host_python = manifest.get('hermes_python') or settings.get('hermes_python')
        if core is None and (home / 'hermes-agent/agent/memory_provider.py').is_file():
            core = str(home / 'hermes-agent')
        env = profile_environment(home, core, host_python, settings)
        env.pop('HERMES_PROFILE', None)
        env['KG_STATIC_DIR'] = str(home / 'plugins/kg/static')
        options = DeployOptions(home, home, port=port)
        services = controller_factory(options, python, env)
        record_path = contained(home, home / 'kg-deploy/console.json')
        model = manifest.get('model_required', False) is True
        if record_path.exists():
            original = record_path.read_bytes()
            record = None
            try:
                record = json.loads(original)
                if not isinstance(record, dict):
                    raise ValueError('Invalid console metadata')
                services.identity(record)
            except (ValueError, TypeError, DeploymentError):
                require_free_port(port)
                recorded_port = record.get('port') if isinstance(record, dict) else None
                if isinstance(recorded_port, int) and not isinstance(recorded_port, bool) and 1024 <= recorded_port <= 65535 and recorded_port != port:
                    require_free_port(recorded_port)
                if record_path.read_bytes() != original:
                    raise DeploymentError('Console metadata changed while starting; retry safely')
                record_path.unlink()
            else:
                if (record.get('release') != manifest['source_sha256'] or record.get('port') != port
                        or not _same_path(record.get('python', ''), python)):
                    raise DeploymentError('A verified console from another release is running; use deployment to upgrade it')
                return {'ok': True, 'started': False, 'already_running': True,
                        'source_sha256': manifest['source_sha256'], 'health': services.probe(manifest, model=model)}
        require_free_port(port)
        services.start(manifest['source_sha256'])
        try:
            health = services.probe(manifest, model=model)
        except Exception:
            services.stop()
            raise
        return {'ok': True, 'started': True, 'already_running': False,
                'source_sha256': manifest['source_sha256'], 'health': health}


def main(argv=None, *, default_home=None):
    parser = argparse.ArgumentParser(description='Start the current installed KG console without a source checkout')
    parser.add_argument('command', choices=['start'])
    parser.add_argument('--home', type=Path, default=default_home)
    args = parser.parse_args(argv)
    try:
        if args.home is None:
            raise DeploymentError('Pass the installed profile home')
        result = start(args.home)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        message = str(exc) if isinstance(exc, DeploymentError) else 'KG service could not start; inspect the private deployment log'
        print(json.dumps({'ok': False, 'error': message}, ensure_ascii=False), file=sys.stderr)
        return 1
