"""Bundle the prebuilt workbench and Hermes skills into the standalone wheel."""
from pathlib import Path
import fnmatch
import re
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py


ROOT = Path(__file__).resolve().parent


def validate_distributable_tree(source):
    """Keep profile state and historical private reports out of shipped resources."""
    private_directories = {"private", ".private", "reports", "backups", ".git",
                           "cache", ".cache", ".model-cache", ".venv", "venv", "_vendor"}
    private_suffixes = {".db", ".db-shm", ".db-wal", ".sqlite", ".sqlite3",
                        ".token", ".log", ".pem", ".key", ".zip", ".bak", ".backup"}
    dated_report = re.compile(r"(?:^|[-_])(?:audit|report|snapshot|dump|backup)[-_]\d{4}[-_]\d{2}[-_]\d{2}(?:$|[-_.])", re.I)
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        name = path.name.lower()
        if (path.is_symlink() or any(part.lower() in private_directories for part in relative.parts)
                or path.suffix.lower() in private_suffixes or name.startswith(".env")
                or fnmatch.fnmatchcase(name, "kg.json*") or fnmatch.fnmatchcase(name, "kg-console*.json*")
                or dated_report.search(name)):
            raise RuntimeError("Refusing to package private/local resource: " + relative.as_posix())


class BuildPy(build_py):
    def run(self):
        assets = ROOT / "console" / "dist"
        if not (assets / "index.html").is_file():
            raise RuntimeError(
                "The wheel requires built frontend assets. Run `npm ci && npm run build` "
                "inside console/ before building the wheel. Source Python/plugin usage "
                "does not require a wheel build."
            )
        package = Path(self.build_lib) / "kg"
        targets = [(assets, package / "static")]
        for name in ("kg-memory", "kg-memory-sync"):
            source = ROOT / "skills" / name
            if not (source / "SKILL.md").is_file():
                raise RuntimeError("Missing required Hermes skill resource: " + name)
            targets.append((source, package / "resources" / "skills" / name))
        for source, _ in targets:
            validate_distributable_tree(source)
        super().run()
        self._bundled_outputs = []
        for source, target in targets:
            # A repeated build must not retain obsolete hashed assets/resources.
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target, ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", "*.pyo", ".DS_Store"))
            self._bundled_outputs.extend(str(path) for path in target.rglob("*") if path.is_file())
        validate_distributable_tree(package)

    def get_outputs(self, include_bytecode=1):
        return super().get_outputs(include_bytecode) + getattr(self, "_bundled_outputs", [])


setup(cmdclass={"build_py": BuildPy})
