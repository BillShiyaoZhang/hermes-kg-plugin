#!/usr/bin/env python3
"""Compatibility entry point for the canonical, wheel-packaged kg.runner.

The old cron path and private extraction helpers remain importable. Runtime
implementation lives in the plugin so a wheel never needs a checkout's scripts.
"""
from pathlib import Path
import os
import sys

_profile_home = Path(__file__).resolve().parents[1]
_plugins = str(_profile_home / "plugins")
if _plugins not in sys.path:
    sys.path.insert(0, _plugins)
from kg import runner as _runner

# Preserve direct imports and importlib.spec_from_file_location users, including
# the historical private extraction helpers used by maintenance/test adapters.
for _name, _value in vars(_runner).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

INSTALL_HOME = _profile_home


def run(*, home=None, **kwargs):
    if home is None and kwargs.get("config") is None and not (os.environ.get("KG_HOME") or os.environ.get("HERMES_HOME")):
        home = _profile_home
    return _runner.run(home=home, **kwargs)


def main(argv=None):
    default_home = None if (os.environ.get("KG_HOME") or os.environ.get("HERMES_HOME")) else _profile_home
    return _runner.main(argv, default_home=default_home)


if __name__ == "__main__":
    raise SystemExit(main())
