"""Bundle the prebuilt workbench and Hermes skills into the standalone wheel."""
from pathlib import Path
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py


ROOT = Path(__file__).resolve().parent


class BuildPy(build_py):
    def run(self):
        assets = ROOT / "console" / "dist"
        if not (assets / "index.html").is_file():
            raise RuntimeError(
                "The wheel requires built frontend assets. Run `npm ci && npm run build` "
                "inside console/ before building the wheel. Source Python/plugin usage "
                "does not require a wheel build."
            )
        super().run()
        package = Path(self.build_lib) / "kg"
        targets = [(assets, package / "static")]
        for name in ("kg-memory", "kg-memory-sync"):
            source = ROOT / "skills" / name
            if not (source / "SKILL.md").is_file():
                raise RuntimeError("Missing required Hermes skill resource: " + name)
            targets.append((source, package / "resources" / "skills" / name))
        self._bundled_outputs = []
        for source, target in targets:
            # A repeated build must not retain obsolete hashed assets/resources.
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target, ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", "*.pyo", ".DS_Store"))
            self._bundled_outputs.extend(str(path) for path in target.rglob("*") if path.is_file())

    def get_outputs(self, include_bytecode=1):
        return super().get_outputs(include_bytecode) + getattr(self, "_bundled_outputs", [])


setup(cmdclass={"build_py": BuildPy})
