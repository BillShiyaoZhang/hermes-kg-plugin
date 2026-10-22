#!/usr/bin/env python3
"""Run the plugin's unit tests.

The suite is plain unittest and needs no database, but one test file reads the
Hermes core source tree to assert that the plugin's synthetic-text filters
still match the core's prompt templates, and two test files import
`agent.memory_provider` from the core.

So a core checkout has to be reachable at <repo>/hermes-agent. This script
finds one — via HERMES_AGENT_HOME, an existing ./hermes-agent, a sibling
install, or the standard per-platform install location — links it into place
if it is not already there, and runs the suite.

    python scripts/run_tests.py
    python scripts/run_tests.py -v
    python scripts/run_tests.py test_provenance
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CORE_LINK = REPO_ROOT / "hermes-agent"
TESTS = REPO_ROOT / "plugins" / "kg" / "tests"


def candidate_cores() -> list[Path]:
    candidates: list[Path] = []
    env = os.environ.get("HERMES_AGENT_HOME")
    if env:
        candidates.append(Path(env).expanduser())
    candidates.append(CORE_LINK)
    candidates.append(REPO_ROOT.parent / "hermes-agent")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "hermes" / "hermes-agent")
    home = Path.home()
    candidates.append(home / ".hermes" / "hermes-agent")
    candidates.append(home / "Developer" / "hermes-agent")
    return candidates


def find_core() -> Path | None:
    for candidate in candidate_cores():
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if (resolved / "agent" / "memory_provider.py").exists():
            return resolved
    return None


def link_core(core: Path) -> Path:
    """Expose the core at <repo>/hermes-agent without copying it."""
    if CORE_LINK.exists():
        return CORE_LINK
    try:
        CORE_LINK.symlink_to(core, target_is_directory=True)
        return CORE_LINK
    except (OSError, NotImplementedError):
        pass
    # Windows without symlink privilege: a directory junction needs no elevation.
    if os.name == "nt":
        result = subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(CORE_LINK), str(core)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and CORE_LINK.exists():
            return CORE_LINK
    print(f"  ! could not link the core into {CORE_LINK}", file=sys.stderr)
    print(f"    clone it manually: git clone --depth 1 https://github.com/NousResearch/hermes-agent.git {CORE_LINK}", file=sys.stderr)
    return core


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pattern", nargs="?", help="Only run tests whose name contains this")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    core = find_core()
    if core is None:
        print("  ! no Hermes core checkout found", file=sys.stderr)
        print("    set HERMES_AGENT_HOME, or clone one:", file=sys.stderr)
        print(f"      git clone --depth 1 https://github.com/NousResearch/hermes-agent.git {CORE_LINK}", file=sys.stderr)
        return 2
    print(f"core:  {core}")
    core = link_core(core)

    command = [sys.executable, "-m", "unittest", "discover", "-s", str(TESTS), "-t", str(TESTS)]
    if args.verbose:
        command.append("-v")
    if args.pattern:
        command.extend(["-k", args.pattern])
    print(f"tests: {TESTS}")
    result = subprocess.run(command, cwd=str(REPO_ROOT))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
