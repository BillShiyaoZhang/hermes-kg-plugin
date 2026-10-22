"""User-installed KG memory provider; shared runtime for the accompanying CLIs.

The package can be imported without Hermes. The host calls register() to create
the MemoryProvider, so standalone maintenance commands do not import agent core.
"""
from pathlib import Path
import sys

_vendor = Path(__file__).resolve().parent / "_vendor"
if _vendor.is_dir() and str(_vendor) not in sys.path:
    sys.path.insert(0, str(_vendor))


def register(ctx):
    from .provider import KGMemoryProvider
    ctx.register_memory_provider(KGMemoryProvider())
