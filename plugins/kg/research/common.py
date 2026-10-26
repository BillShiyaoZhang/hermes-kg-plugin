"""Stable manifests and private, atomic local research artifacts."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from . import FORMAT


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode('utf-8')).hexdigest()


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    def reject(value): raise ValueError('Non-finite JSON number: ' + value)
    with Path(path).open(encoding='utf-8-sig') as stream:
        return json.load(stream, object_pairs_hook=unique, parse_constant=reject)


def write_json(path, value, *, replace=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not replace and path.exists():
        raise ValueError(f'Artifact already exists: {path.name}')
    temporary = path.with_name(path.name + '.pending-' + str(os.getpid()))
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        if not replace and path.exists():
            raise ValueError(f'Artifact already exists: {path.name}')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def check_format(value):
    if value.get('format') != FORMAT:
        raise ValueError('Unsupported research artifact format')
    return value


def relation_key(source, predicate, target):
    return 'relation:' + '|'.join((source, predicate, target))
