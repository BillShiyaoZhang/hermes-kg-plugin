"""Bind the standalone console/runner to one Hermes profile before LLM imports.

Hermes' auxiliary client reads process configuration and credentials. The
console deliberately serves one profile per process; switching profiles after
loading their environment would mix process-global credentials and caches.
Importing this module does not import Hermes or read profile files.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

_lock = threading.RLock()
_bound_home = None
_loaded = False


def prepare_runtime(home):
    """Load the selected profile once, before importing/calling a real LLM.

    The caller must first put its Hermes core checkout on ``sys.path``. An
    unsuccessful load is retryable for the same profile. Injected test LLMs
    should bypass this helper entirely.
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
        if not _loaded:
            from hermes_cli.env_loader import load_hermes_dotenv
            try:
                load_hermes_dotenv(hermes_home=root)
            finally:
                # A legacy .env may itself contain HERMES_HOME. The explicit
                # console profile remains authoritative even after dotenv load.
                os.environ["HERMES_HOME"] = str(root)
            _loaded = True
        return root
