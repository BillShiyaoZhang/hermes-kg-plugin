"""One profile-scoped configuration loader for the KG plugin and its CLIs."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


def resolve_home(explicit=None) -> Path:
    value = explicit if explicit is not None else os.environ.get("HERMES_HOME")
    return Path(value).expanduser().resolve() if value else Path(__file__).resolve().parents[2]


def get_hermes_home(hermes_home=None) -> Path:
    return resolve_home(hermes_home)


def _boolean(value, key):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    raise ValueError(f"{key} must be true or false")


@dataclass(frozen=True)
class KGConfig:
    home: Path = field(default_factory=resolve_home)
    neo4j_url: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: str = field(default="", repr=False)
    database: str = "neo4j"
    urn_prefix: str = "urn:hermes:kg:"
    user_person_urn: str = "urn:hermes:kg:person-bill"
    enabled: bool = True
    query_cadence: int = 3
    connection_timeout: float = 5
    query_timeout: float = 10
    query_limit: int = 10
    timezone: str = "Asia/Shanghai"

    def __post_init__(self):
        object.__setattr__(self, "home", Path(self.home).expanduser().resolve())
        object.__setattr__(self, "enabled", _boolean(self.enabled, "enabled"))
        if urlparse(self.neo4j_url).scheme not in {"bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc"}:
            raise ValueError("KG URL must use a Neo4j Bolt scheme")
        if not self.urn_prefix.startswith("urn:") or not self.urn_prefix.endswith(":"):
            raise ValueError("KG urn_prefix must be a URN namespace ending in ':'")
        for key in ("query_cadence", "query_limit", "connection_timeout", "query_timeout"):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{key} must be positive")
        if not isinstance(self.query_cadence, int) or not isinstance(self.query_limit, int):
            raise ValueError("query_cadence and query_limit must be integers")

    @property
    def bolt_url(self):
        return self.neo4j_url

    def get(self, name, default=None):
        return getattr(self, name, default)


def load_config(home=None, hermes_home=None) -> KGConfig:
    if home is not None and hermes_home is not None and resolve_home(home) != resolve_home(hermes_home):
        raise ValueError("Conflicting KG profile homes")
    root = resolve_home(home if home is not None else hermes_home)
    path = root / "kg.json"
    raw = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if not isinstance(raw, dict):
        raise ValueError("kg.json must contain an object")
    aliases = {"bolt_url": "neo4j_url", "url": "neo4j_url", "neo4j_user": "user", "neo4j_password": "password", "neo4j_database": "database"}
    data = {aliases.get(k, k): v for k, v in raw.items() if aliases.get(k, k) in KGConfig.__dataclass_fields__ and k != "home"}
    env_keys = {
        "neo4j_url": ("HERMES_KG_BOLT_URL", "NEO4J_URL"),
        "user": ("HERMES_KG_USER", "NEO4J_USER"),
        "password": ("HERMES_KG_PASSWORD", "NEO4J_PASSWORD"),
        "database": ("HERMES_KG_DATABASE", "NEO4J_DATABASE"),
        "urn_prefix": ("HERMES_KG_URN_PREFIX",),
        "user_person_urn": ("HERMES_KG_USER_PERSON_URN",),
        "enabled": ("HERMES_KG_ENABLED",),
        "query_cadence": ("HERMES_KG_QUERY_CADENCE",),
        "query_limit": ("HERMES_KG_QUERY_LIMIT",),
        "timezone": ("HERMES_KG_TIMEZONE",),
    }
    for key, names in env_keys.items():
        for name in names:
            if name in os.environ:
                data[key] = os.environ[name]
                break
    for key in ("query_cadence", "query_limit"):
        if key in data and isinstance(data[key], str):
            data[key] = int(data[key])
    for key in ("connection_timeout", "query_timeout"):
        if key in data and isinstance(data[key], str):
            data[key] = float(data[key])
    prefix = data.get("urn_prefix", "urn:hermes:kg:")
    data.setdefault("user_person_urn", prefix + "person-bill")
    from .schema import canonical_urn
    data["user_person_urn"] = canonical_urn(data["user_person_urn"], "person", prefix=prefix)
    return KGConfig(home=root, **data)
