> Historical design/research note. It does not describe the current installed runtime.
> For executable paths, schema, configuration and migration rules use [the active skill](../SKILL.md)
> and [current storage contract](entity-schema.md). Do not execute old cleanup or deployment examples.

# KG Memory Provider — Implementation Reference

Built in this session. File layout:

```
~/.hermes/hermes-agent/plugins/memory/kg/
  __init__.py      # KGMemoryProvider class (MemoryProvider ABC)
  client.py        # KGConfig dataclass + _run_script() subprocess wrapper
  session.py       # KGSessionManager (queue, query, extraction)
  cli.py           # cmd_setup() — `hermes kg setup` wizard
  plugin.yaml      # name/version/hooks registration
```

## Key Implementation Notes

### Config Loading (client.py)
Uses same 3-layer priority as honcho provider:
```python
KGConfig.from_global_config()
  1. $HERMES_HOME/kg.json
  2. ~/.hermes/kg.json
  3. env vars: HERMES_KG_BOLT_URL, HERMES_KG_USER, HERMES_KG_PASSWORD,
               HERMES_KG_URN_PREFIX, HERMES_KG_USER_PERSON_URN
```

### Script Delegation
All KG operations delegate to existing skill scripts via subprocess:
- `query_natural.py` — natural language query
- `manage_entity.py` — structured entity CRUD
- `query_events.py` — event queries

`_run_script()` passes Neo4j credentials via env vars (same keys as `HERMES_KG_*`).

### Write Queue Pattern (session.py)
```
queue_turn(user, assistant) → Queue.put()
_write_loop() [background thread] → extract_and_write()
```
Background thread drains queue continuously. `flush_write_queue()` called on shutdown.

### Rule-Based Extraction (no LLM)
`sync_turn()` calls `extract_and_write()` which uses regex patterns:
- Person: Chinese (`[\u4e00-\u9fff]{2,4}`) + English capitalized names
- Event type: keyword matching (meeting/travel/social/task/system)
- Preferences: Chinese + English patterns ("喜欢", "prefer", "hate")
- Decisions: Chinese + English patterns ("决定", "decided", "going to")

### Cadence Control
`query_cadence` (default 3): automatic prefetch only fires every N turns.
Set `query_cadence=1` for every-turn prefetch.

### URN Isolation
Both Hermes and OpenClaw share the same Neo4j. Prefix collision avoided:
- Hermes entities: `urn:hermes:kg:*`
- OpenClaw entities: `urn:openclaw:kg:*`

### KG Skill vs Provider
| Layer | Purpose |
|-------|---------|
| `kg-memory` skill (SKILL.md) | LLM-facing: what KG is, how to interpret results, when to call tools |
| `plugins/memory/kg/` provider | Agent loop infrastructure: prefetch, sync_turn, tool dispatch |
| `kg-memory-sync` cron | Deep LLM extraction: `run_full_sync.py` with MiniMax LLM |

## Future Iteration

For the LLM-driven iteration roadmap (Phase 1-3: session extraction → NL→Cypher → ontology alignment), see `references/kg-llm-iteration-research.md` and `~/.hermes/plans/kg-llm-iteration-proposal.md`.
