"""Bind the standalone console/runner to one Hermes profile before LLM imports.

Hermes' auxiliary client reads process configuration and credentials. The
console deliberately serves one profile per process; switching profiles after
loading their environment would mix process-global credentials and caches.
Importing this module does not import Hermes or read profile files.
"""
from __future__ import annotations

import os
import json
import subprocess
import sys
import threading
from pathlib import Path

_lock = threading.RLock()
_bound_home = None
_loaded = False

# This program uses only stdlib until the selected host interpreter has loaded
# its own dependency environment. Request text and credentials never enter argv.
_BRIDGE_SCRIPT = r'''
import contextlib, io, json, os, sys
from pathlib import Path
payload=json.load(sys.stdin)
home=Path(payload['home']).resolve()
core=Path(payload['core']).resolve()
os.environ['HERMES_HOME']=str(home)
os.environ['KG_HOME']=str(home)
sys.path.insert(0,str(core))
sys.argv=['kg-auxiliary-bridge','pm','repair']
sys.dont_write_bytecode=True
output=sys.stdout
try:
    with contextlib.redirect_stdout(io.StringIO()):
        # Managed Hermes Python has no global packages. Its committed PM
        # generation supplies the matching ABI; ordinary venvs own their deps.
        if sys.prefix==sys.base_prefix and (core/'pm'/'environments.py').is_file():
            from pm.environments import activate_dependencies,owning_home_root
            from hermes_constants import set_hermes_home_override,reset_hermes_home_override
            owner=owning_home_root(core)
            token=set_hermes_home_override(owner) if owner is not None else None
            try:
                activate_dependencies(core)
            finally:
                if token is not None:
                    reset_hermes_home_override(token)
        from hermes_cli.env_loader import load_hermes_dotenv
        load_hermes_dotenv(hermes_home=home)
        os.environ['HERMES_HOME']=str(home)
        os.environ['KG_HOME']=str(home)
        from agent.auxiliary_client import call_llm,shutdown_cached_clients
        try:
            response=call_llm(**payload['request'])
            def field(value,key,default=None):
                return value.get(key,default) if isinstance(value,dict) else getattr(value,key,default)
            if isinstance(response,str):
                normalized=response
            else:
                choice=field(response,'choices')[0]
                content=field(field(choice,'message'),'content')
                if isinstance(content,list):
                    content=''.join(field(part,'text','') for part in content
                        if field(part,'type','text') in {'text','output_text'} and isinstance(field(part,'text',''),str))
                if not isinstance(content,str):
                    raise ValueError('No final assistant content')
                normalized={'choices':[{'message':{'content':content},'finish_reason':field(choice,'finish_reason')}]}
                model=field(response,'model')
                if isinstance(model,str):
                    normalized['model']=model
            result={'ok':True,'response':normalized}
        finally:
            shutdown_cached_clients()
    text=json.dumps(result,ensure_ascii=False,allow_nan=False)
except Exception as exc:
    text=json.dumps({'ok':False,'error_type':type(exc).__name__})
output.write(text)
output.flush()
'''


def discover_core(home=None):
    """Find an installed Hermes checkout without importing it or reading secrets.

    The selected profile is a data directory and may be entirely separate from
    the application installation. In particular, Windows installs commonly use
    LocalAppData, and sandboxed shells can redirect that environment variable.
    """
    candidates = [os.environ.get("HERMES_AGENT_HOME")]
    if home is not None:
        candidates.append(Path(home).expanduser() / "hermes-agent")
    candidates.extend(sys.path)
    candidates.extend(Path(sys.executable).resolve().parents)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "hermes" / "hermes-agent")
    user_home = Path.home()
    if os.name == "nt":
        candidates.append(user_home / "AppData" / "Local" / "hermes" / "hermes-agent")
    candidates.append(user_home / ".hermes" / "hermes-agent")
    for candidate in candidates:
        if not candidate:
            continue
        try:
            root = Path(candidate).expanduser().resolve()
            if (root / "agent" / "memory_provider.py").is_file():
                return root
        except (OSError, ValueError):
            continue
    return None


def prepare_runtime(home):
    """Load the selected profile once, before importing/calling a real LLM.

    Locate the installed core when necessary. An unsuccessful load is retryable
    for the same profile. Injected test LLMs should bypass this helper entirely.
    """
    global _bound_home, _loaded
    root = Path(home).expanduser().resolve()
    with _lock:
        if _bound_home is not None and _bound_home != root:
            raise RuntimeError("One Hermes profile is supported per process; restart to select another profile")
        _bound_home = root
        # Set this before importing Hermes: some modules resolve their home at
        # import time, rather than only when the eventual model call is made.
        os.environ["HERMES_HOME"] = str(root)
        os.environ["KG_HOME"] = str(root)
        if not _loaded:
            core = discover_core(root)
            if core is not None and str(core) not in sys.path:
                sys.path.insert(0, str(core))
            from hermes_cli.env_loader import load_hermes_dotenv
            try:
                load_hermes_dotenv(hermes_home=root)
            finally:
                # A legacy .env may itself contain HERMES_HOME. The explicit
                # console profile remains authoritative even after dotenv load.
                os.environ["HERMES_HOME"] = str(root)
                os.environ["KG_HOME"] = str(root)
            _loaded = True
        return root


def discover_python(core):
    """Select an explicit host Python or an installed host-owned virtualenv."""
    explicit=os.environ.get('KG_HERMES_PYTHON') or os.environ.get('HERMES_PYTHON')
    if explicit:
        path=Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise RuntimeError('Configured Hermes Python was not found')
        return path
    for folder in ('venv','.venv'):
        for relative in ('Scripts/python.exe','bin/python'):
            path=Path(core)/folder/relative
            if path.is_file():
                return path.resolve()
    raise RuntimeError('Hermes Python was not found; set KG_HERMES_PYTHON to its installed interpreter')


def call_profile_llm(home,**request):
    """Call the host auxiliary route without adding host deps to the console.

    A running Hermes process keeps its native client caches. A separate product
    console delegates through stdin JSON to the host's Python and dependencies.
    """
    root=Path(home).expanduser().resolve()
    core=discover_core(root)
    explicit=os.environ.get('KG_HERMES_PYTHON') or os.environ.get('HERMES_PYTHON')
    native=not explicit and ('agent.auxiliary_client' in sys.modules or
                             (core is not None and Path(sys.executable).resolve().is_relative_to(core)))
    if native:
        prepare_runtime(root)
        from agent.auxiliary_client import call_llm
        return call_llm(**request)
    if core is None:
        raise RuntimeError('Hermes core was not found; set HERMES_AGENT_HOME')
    python=discover_python(core)
    env=dict(os.environ,HERMES_HOME=str(root),KG_HOME=str(root))
    for key in ('PYTHONHOME','PYTHONPATH','VIRTUAL_ENV'):
        env.pop(key,None)
    timeout=request.get('timeout',90)
    if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or timeout<=0:
        raise ValueError('Model timeout must be positive')
    try:
        done=subprocess.run([str(python),'-X','utf8','-c',_BRIDGE_SCRIPT],
            input=json.dumps({'home':str(root),'core':str(core),'request':request},ensure_ascii=False),
            text=True,encoding='utf-8',capture_output=True,cwd=str(root),env=env,
            timeout=timeout+30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError('Hermes auxiliary model call timed out') from exc
    except OSError as exc:
        raise RuntimeError('Hermes auxiliary process could not start') from exc
    try:
        if len(done.stdout)>2_000_000:
            raise ValueError('Oversized host response')
        result=json.loads(done.stdout)
    except (ValueError,TypeError) as exc:
        raise RuntimeError('Hermes auxiliary process returned an invalid response') from exc
    if done.returncode or not isinstance(result,dict) or result.get('ok') is not True:
        kind=result.get('error_type') if isinstance(result,dict) else None
        if not isinstance(kind,str) or not kind.isidentifier():
            kind='HostError'
        raise RuntimeError('Hermes auxiliary model call failed ('+kind+')')
    return result['response']
