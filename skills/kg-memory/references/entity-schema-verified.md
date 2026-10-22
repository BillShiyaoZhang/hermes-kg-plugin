# Schema reference moved

The dated 2026-05 schema and cleanup recipes formerly in this file are superseded
by [the current shared storage contract](entity-schema.md) and
`plugins/kg/schema.py`. The old Person name, coordinate and relationship mappings
did not match the installed writer, and its broad deletion recipe is not a valid
migration method. Historical evidence is preserved in the pre-repair backup.

Use `scripts/validate.py` for real RDF validation and `--graph` for read-only
instance checks. Use explicit migration mappings and backups for legacy data.
