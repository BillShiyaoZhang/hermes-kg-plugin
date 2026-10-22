"""Load the independent user plugin from this skill's installation root."""
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[3] / "plugins"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

# The package bootstrap exposes its private, locked dependency directory.
import kg


def configure_output():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
