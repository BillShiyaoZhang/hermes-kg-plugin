# Native Windows KG installation

The installed skill resolves its library at the installation root's
`plugins/kg/`. On this machine the default root is
`%LOCALAPPDATA%\\hermes`, with Hermes Python at
`hermes-agent\\venv\\Scripts\\python.exe`.
An explicit `HERMES_HOME` selects the data/config profile, without changing the
library source to a hardcoded WSL or Miniconda directory.

Dependencies are locked and privately installed in `plugins/kg/_vendor`.
The package bootstrap exposes that directory to the same interpreter that loads
Hermes; wrappers import the package before RDFLib/Neo4j. Core Hermes dependency
files are not modified.

Use native absolute Windows paths with PowerShell's call operator. Do not pass
`/c/Users/...` or `~/.hermes/venvs/kg/bin/python3` to native Python.
Do not launch a second visible console or expose connection passwords.

All connections use `kg.config.load_config()`, the active home's `kg.json`,
and documented environment overrides. The current canonical contract is in
[entity-schema.md](entity-schema.md); the active skill has command arguments for
querying, writing, validation and schema discovery.

`validate.py` is read-only. A successful RDF validation does not test the database.
Database availability, driver availability, provider discovery and query behavior
are distinct checks. Do not silently fall back to unauthenticated connections
after authentication fails.

For setup/recovery use the current plugin setup and dependency lock, then an
isolated namespace integration test. Existing graph migrations require a snapshot
and explicit mappings; no blanket deletion of unlabeled nodes.
