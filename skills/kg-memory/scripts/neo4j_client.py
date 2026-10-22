"""Compatibility imports backed by the independent profile-scoped KG plugin."""
import _kg_import
from kg.config import load_config
from kg.store import KGStore


def get_driver():
    """Caller owns the returned driver and must close it; prefer KGStore context."""
    store = KGStore()
    return store.driver


def run_query(query: str, params=None):
    """Read-only compatibility entry. Mutations belong in KGStore.write_batch."""
    with KGStore() as store:
        return store.run_read(query, params)
