"""Profile-local recall invalidation, containing no memory or credential data."""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid


def read_recall_generation(home):
    """Read without creating files; an unreadable marker disables recall reuse."""
    path = Path(home) / "kg-sync" / "recall-generation.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        generation = value.get("generation") if isinstance(value, dict) else None
        if value.get("version") == 1 and isinstance(generation, str) and len(generation) == 32:
            int(generation, 16)
            return generation
    except FileNotFoundError:
        return ""
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    # Do not reuse cached evidence while the notification cannot be trusted.
    return "unreadable-" + uuid.uuid4().hex


def bump_recall_generation(home):
    """Atomically notify other processes after commit; I/O errors are explicit.

    A random generation avoids lost increments between concurrent publishers.
    Callers must retain an already committed fact receipt if notification fails.
    """
    directory = Path(home) / "kg-sync"
    directory.mkdir(parents=True, exist_ok=True)
    generation = uuid.uuid4().hex
    path = directory / "recall-generation.json"
    temporary = directory / (".recall-generation-" + generation + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump({"version": 1, "generation": generation}, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return generation
