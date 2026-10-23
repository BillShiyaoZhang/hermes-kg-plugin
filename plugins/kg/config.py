"""One profile-scoped configuration loader for the KG plugin and its CLIs."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


def resolve_home(explicit=None) -> Path:
    value = explicit if explicit is not None else (os.environ.get("KG_HOME") or os.environ.get("HERMES_HOME"))
    if value:
        return Path(value).expanduser().resolve()
    module=Path(__file__).resolve()
    if module.parent.parent.name == "plugins":
        return module.parents[2]
    return (Path.home()/".hermes").resolve()


def get_hermes_home(hermes_home=None) -> Path:
    return resolve_home(hermes_home)


def _boolean(value, key):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    raise ValueError(f"{key} must be true or false")


# Product-neutral names take precedence; the original Hermes names and Neo4j
# aliases remain supported for existing installations.
ENV_KEYS = {
    "neo4j_url": ("KG_BOLT_URL", "KG_NEO4J_URL", "HERMES_KG_BOLT_URL", "NEO4J_URL"),
    "user": ("KG_USER", "HERMES_KG_USER", "NEO4J_USER"),
    "password": ("KG_PASSWORD", "HERMES_KG_PASSWORD", "NEO4J_PASSWORD"),
    "database": ("KG_DATABASE", "HERMES_KG_DATABASE", "NEO4J_DATABASE"),
    "urn_prefix": ("KG_URN_PREFIX", "HERMES_KG_URN_PREFIX"),
    "user_person_urn": ("KG_USER_PERSON_URN", "HERMES_KG_USER_PERSON_URN"),
    "enabled": ("KG_ENABLED", "HERMES_KG_ENABLED"),
    "query_cadence": ("KG_QUERY_CADENCE", "HERMES_KG_QUERY_CADENCE"),
    "query_limit": ("KG_QUERY_LIMIT", "HERMES_KG_QUERY_LIMIT"),
    "connection_timeout": ("KG_CONNECTION_TIMEOUT", "HERMES_KG_CONNECTION_TIMEOUT"),
    "query_timeout": ("KG_QUERY_TIMEOUT", "HERMES_KG_QUERY_TIMEOUT"),
    "timezone": ("KG_TIMEZONE", "HERMES_KG_TIMEZONE"),
    "default_shared": ("KG_DEFAULT_SHARED", "HERMES_KG_DEFAULT_SHARED"),
    "console_url": ("KG_CONSOLE_URL", "HERMES_KG_CONSOLE_URL"),
}


def env_value(key, default=None):
    """Read a KG setting using the same aliases in setup and normal loading."""
    for name in ENV_KEYS.get(key,("KG_"+key.upper(),"HERMES_KG_"+key.upper())):
        if name in os.environ:
            return os.environ[name]
    return default


@dataclass(frozen=True)
class KGConfig:
    home: Path = field(default_factory=resolve_home)
    neo4j_url: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: str = field(default="", repr=False)
    database: str = "neo4j"
    urn_prefix: str = "urn:hermes:kg:"
    user_person_urn: str | None = None
    enabled: bool = True
    query_cadence: int = 3
    connection_timeout: float = 5
    query_timeout: float = 10
    query_limit: int = 10
    timezone: str = "Asia/Shanghai"
    default_shared: bool = True
    console_url: str = "http://127.0.0.1:8765"

    def __post_init__(self):
        object.__setattr__(self, "home", Path(self.home).expanduser().resolve())
        object.__setattr__(self, "enabled", _boolean(self.enabled, "enabled"))
        object.__setattr__(self, "default_shared", _boolean(self.default_shared, "default_shared"))
        if urlparse(self.neo4j_url).scheme not in {"bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc"}:
            raise ValueError("KG URL must use a Neo4j Bolt scheme")
        if not self.urn_prefix.startswith("urn:") or not self.urn_prefix.endswith(":"):
            raise ValueError("KG urn_prefix must be a URN namespace ending in ':'")
        from .schema import canonical_urn
        owner = self.user_person_urn if self.user_person_urn is not None else self.urn_prefix + "person-self"
        object.__setattr__(self, "user_person_urn", canonical_urn(owner, "person", prefix=self.urn_prefix))
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
    for key in ENV_KEYS:
        value = env_value(key)
        if value is not None:
            data[key] = value
    for key in ("query_cadence", "query_limit"):
        if key in data and isinstance(data[key], str):
            data[key] = int(data[key])
    for key in ("connection_timeout", "query_timeout"):
        if key in data and isinstance(data[key], str):
            data[key] = float(data[key])
    return KGConfig(home=root, **data)
