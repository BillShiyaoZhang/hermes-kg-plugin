#!/usr/bin/env python3
"""Install this KG plugin into a Hermes profile home.

The repository mirrors a Hermes profile home, so installing is a copy of five
trees into the target home plus a locked dependency install:

    plugins/kg/            ->  <home>/plugins/kg/
    skills/kg-memory/      ->  <home>/skills/kg-memory/
    skills/kg-memory-sync/ ->  <home>/skills/kg-memory-sync/
    scripts/               ->  <home>/scripts/
    kg-sync/               ->  <home>/kg-sync/

The plugin resolves its own paths relative to the home it is installed in, so
the copy layout is part of the contract, not a convenience.

Nothing here overwrites an existing kg.json, and no credential is written by
this script. It prints every path it touches.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# (source relative to the repo, destination relative to the home)
TREES = (
    (Path("plugins") / "kg", Path("plugins") / "kg"),
    (Path("skills") / "kg-memory", Path("skills") / "kg-memory"),
    (Path("skills") / "kg-memory-sync", Path("skills") / "kg-memory-sync"),
    (Path("kg-sync"), Path("kg-sync")),
)
# Individual files rather than whole trees.
FILES = (
    (Path("scripts") / "kg_sync_runner.py", Path("scripts") / "kg_sync_runner.py"),
)

# Never copied into a profile home.
SKIP_NAMES = {"__pycache__", ".git", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def _is_ignored(path: Path) -> bool:
    if path.name in SKIP_NAMES:
        return True
    return path.suffix in SKIP_SUFFIXES


def _copy_tree(src: Path, dst: Path, dry_run: bool) -> list[str]:
    copied: list[str] = []
    for item in sorted(src.rglob("*")):
        if _is_ignored(item.relative_to(src)):
            continue
        rel = item.relative_to(src)
        target = dst / rel
        if item.is_dir():
            if not dry_run:
                target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() == item.read_bytes():
            continue
        if not dry_run:
            shutil.copy2(item, target)
        copied.append(str(target))
    return copied


def _copy_file(src: Path, dst: Path, dry_run: bool) -> list[str]:
    if not src.exists():
        return []
    if dst.exists() and dst.read_bytes() == src.read_bytes():
        return []
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dry_run:
        shutil.copy2(src, dst)
    return [str(dst)]


def _find_uv(home: Path) -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    for candidate in (home / "bin" / "uv.exe", home / "bin" / "uv"):
        if candidate.exists():
            return str(candidate)
    return None


def _find_python(home: Path) -> str | None:
    for candidate in (
        home / "hermes-agent" / "venv" / "Scripts" / "python.exe",
        home / "hermes-agent" / "venv" / "bin" / "python",
    ):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def install_dependencies(home: Path, dry_run: bool) -> int:
    """Restore the locked, hash-pinned dependencies into plugins/kg/_vendor."""
    lock = REPO_ROOT / "plugins" / "kg" / "requirements.lock"
    target = home / "plugins" / "kg" / "_vendor"
    if not lock.exists():
        print("  ! requirements.lock not found; skipping dependency install", file=sys.stderr)
        return 1
    uv = _find_uv(home)
    if not uv:
        print("  ! uv not found; install it or run:", file=sys.stderr)
        print(f"      uv pip install --target {target} --require-hashes -r {lock}", file=sys.stderr)
        return 1
    python = _find_python(home)
    command = [
        uv, "pip", "install",
        "--python", python,
        "--target", str(target),
        "--require-hashes",
        "-r", str(lock),
    ]
    if dry_run:
        print(f"  would run: {' '.join(command)}")
        return 0
    print(f"  installing locked dependencies -> {target}")
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout[-2000:], file=sys.stderr)
        print(result.stderr[-2000:], file=sys.stderr)
        return result.returncode
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, help="Target Hermes profile home")
    parser.add_argument("--dry-run", action="store_true", help="Print what would change")
    parser.add_argument("--skip-deps", action="store_true", help="Do not install locked dependencies")
    args = parser.parse_args()

    home = Path(args.home).expanduser().resolve()
    if not (home / "hermes-agent").exists() and not (home / "config.yaml").exists():
        print(f"  ! {home} does not look like a Hermes profile home", file=sys.stderr)
        print("    (expected hermes-agent/ or config.yaml inside it)", file=sys.stderr)
        return 2

    print(f"source: {REPO_ROOT}")
    print(f"target: {home}")
    if args.dry_run:
        print("mode:   dry run (nothing is written)")

    changed: list[str] = []
    for src_rel, dst_rel in TREES:
        src = REPO_ROOT / src_rel
        if not src.exists():
            print(f"  ! missing source tree {src_rel}", file=sys.stderr)
            return 2
        copied = _copy_tree(src, home / dst_rel, args.dry_run)
        changed.extend(copied)
        print(f"  {src_rel} -> {dst_rel}: {len(copied)} file(s)")

    for src_rel, dst_rel in FILES:
        src = REPO_ROOT / src_rel
        if not src.exists():
            print(f"  ! missing source file {src_rel}", file=sys.stderr)
            return 2
        copied = _copy_file(src, home / dst_rel, args.dry_run)
        changed.extend(copied)
        print(f"  {src_rel} -> {dst_rel}: {len(copied)} file(s)")

    if not args.skip_deps:
        code = install_dependencies(home, args.dry_run)
        if code != 0 and not args.dry_run:
            print("  ! dependency install failed; the plugin will not load without them", file=sys.stderr)

    kg_json = home / "kg.json"
    if kg_json.exists():
        print(f"  kg.json left untouched ({kg_json})")
    else:
        print(f"  ! {kg_json} does not exist; create it with the connection settings")
        print('    {"enabled": true, "bolt_url": "bolt://127.0.0.1:7687", "user": "neo4j",')
        print('     "password": "<password>", "database": "neo4j",')
        print('     "urn_prefix": "urn:hermes:kg:", "user_person_urn": "urn:hermes:kg:person-bill",')
        print('     "timezone": "Asia/Shanghai"}')

    print("  next: set `memory.provider: kg` in config.yaml, then restart the gateway")
    print(f"  {len(changed)} file(s) written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
