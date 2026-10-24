"""Official, profile-scoped gateway restart and installed-host verification."""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import time


HOST_PROBE = r'''
import contextlib, hashlib, io, json, os, sys
from pathlib import Path
home=Path(sys.argv[1]).resolve()
core=Path(sys.argv[2]).resolve()
sys.path.insert(0,str(core))
sys.argv=['kg-deployment-host-check','pm','repair']
sys.dont_write_bytecode=True
output=sys.stdout
phase='dependency_activation'
try:
    with contextlib.redirect_stdout(io.StringIO()):
        if sys.prefix==sys.base_prefix and (core/'pm/environments.py').is_file():
            from pm.environments import activate_dependencies,owning_home_root
            from hermes_constants import set_hermes_home_override,reset_hermes_home_override
            owner=owning_home_root(core)
            token=set_hermes_home_override(owner) if owner is not None else None
            try:
                activate_dependencies(core)
            finally:
                if token is not None: reset_hermes_home_override(token)
        os.environ['HERMES_HOME']=str(home)
        os.environ['KG_HOME']=str(home)
        phase='profile_binding'
        from hermes_cli.env_loader import load_hermes_dotenv
        load_hermes_dotenv(hermes_home=home)
        os.environ['HERMES_HOME']=str(home)
        os.environ['KG_HOME']=str(home)
        phase='gateway_liveness'
        from gateway.status import resolve_gateway_liveness,read_runtime_status
        live=resolve_gateway_liveness(profile_dir=home,use_cache=False)
        state=live.runtime or read_runtime_status(home/'gateway_state.json') or {}
        import yaml
        cfg=yaml.safe_load((home/'config.yaml').read_text(encoding='utf-8-sig')) or {}
        phase='provider_registration'
        from agent.memory_manager import MemoryManager
        from plugins.memory import _load_provider_from_dir
        manager=MemoryManager()
        provider=_load_provider_from_dir(home/'plugins/kg',register_skills=False)
        registered=provider is not None and provider.name=='kg'
        connected=False
        if registered:
            manager.add_provider(provider)
            try:
                phase='provider_connection'
                manager.initialize_all('kg-deployment-read-only-check',hermes_home=str(home),platform='cron',agent_context='cron')
                status=json.loads(manager.handle_tool_call('kg_status',{}))
                connected=status.get('connected') is True
            finally:
                manager.shutdown_all()
        result={'ok':True,'running':live.running,'pid':live.pid,'liveness_source':live.source,
                'gateway_state':state.get('gateway_state'),'updated_at':state.get('updated_at'),
                'start_identity':state.get('start_time'),
                'provider_configured':(cfg.get('memory') or {}).get('provider')=='kg',
                'provider_registered':registered,'database_connected':connected,
                'provider_sha256':hashlib.sha256((home/'plugins/kg/provider.py').read_bytes()).hexdigest()}
except Exception as exc:
    result={'ok':False,'error_type':type(exc).__name__,'phase':phase}
    if isinstance(exc,ModuleNotFoundError): result['missing_module']=exc.name
output.write(json.dumps(result))
'''


def launcher(home, core, host_python):
    """Prefer the profile's official launcher; never select another profile."""
    from .deployment import DeploymentError
    for name in ('hermes.exe', 'hermes'):
        candidate = Path(home) / 'bin' / name
        if candidate.is_file():
            return [str(candidate)]
    found = shutil.which('hermes')
    if found:
        return [found]
    if host_python and core:
        return [str(host_python), '-m', 'hermes_cli.main']
    raise DeploymentError('Official Hermes launcher was not found')


def current_host(home, core, host_python, env, run):
    from .deployment import DeploymentError
    text = run([host_python, '-B', '-X', 'utf8', '-c', HOST_PROBE, home, core],
               env=env, cwd=home, timeout=60)
    try:
        result = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise DeploymentError('Hermes host verification returned an invalid response') from exc
    if not isinstance(result, dict) or result.get('ok') is not True:
        raise DeploymentError('Installed Hermes provider could not be verified')
    return result


def verified(result, started):
    if not all(result.get(key) is True for key in ('running', 'provider_configured', 'provider_registered', 'database_connected')):
        return False
    if result.get('gateway_state') not in ('running', 'degraded'):
        return False
    try:
        updated = datetime.fromisoformat(result['updated_at'].replace('Z', '+00:00')).timestamp()
    except (KeyError, AttributeError, TypeError, ValueError):
        return False
    return updated >= started - 2


def restart_gateway(home, core, host_python, env, run, *, timeout=120, now=time.time, sleep=time.sleep):
    """Restart through Hermes, then prove OS liveness and a fresh serving state.

    The host verification loads the installed provider with Hermes' actual
    MemoryManager in a read-only cron context. It makes no model calls, writes
    no memories, and never treats retained PID/status files as process liveness.
    """
    from .deployment import DeploymentError
    if not core or not host_python:
        raise DeploymentError('Gateway verification needs the installed Hermes core and interpreter')
    home, core = Path(home).resolve(), Path(core).resolve()
    target_env = dict(env, HERMES_HOME=str(home), KG_HOME=str(home), HERMES_AGENT_HOME=str(core))
    # CLI profile overrides inherited from the caller must not select another
    # named profile. The explicit home remains the authoritative target.
    target_env.pop('HERMES_PROFILE', None)
    before = current_host(home, core, host_python, target_env, run)
    started = now()
    run([*launcher(home, core, host_python), 'gateway', 'restart'], env=target_env, cwd=home, timeout=180)
    deadline = now() + timeout
    while now() < deadline:
        result = current_host(home, core, host_python, target_env, run)
        replaced = (not before.get('running') or result.get('pid') != before.get('pid') or
                    (result.get('start_identity') is not None and
                     result.get('start_identity') != before.get('start_identity')))
        if verified(result, started) and replaced:
            return result
        sleep(1)
    raise DeploymentError('Hermes restart did not produce a live serving gateway with the installed KG provider')
