> Historical design/research note. It does not describe the current installed runtime.
> For executable paths, schema, configuration and migration rules use [the active skill](../SKILL.md)
> and [current storage contract](entity-schema.md). Do not execute old cleanup or deployment examples.

# Morning Briefing — Cross-System Integration

## Overview

This example daily briefing pulls data from external APIs (Microsoft Graph), writes confirmed events to KG, and delivers a digest through a configured messaging channel. It does not describe a particular user's installed job.

**Schedule:** `0 7 * * *` (07:00 Asia/Shanghai)  
**Skills attached:** `microsoft-graph-calendar`, `microsoft-graph-todo`, `knowledge-graph`

## Data Flow

```
Microsoft Graph API
  ├── graph_calendar.py --view yesterday → KG (Event nodes)
  ├── graph_calendar.py --view today → briefing output
  └── graph_todo.py --action lists/tasks → briefing output
                                              ↓
                                    Feishu (deliver=origin)
```

## Skill Transplant Pattern (OpenClaw → Hermes)

When transplanting skills from OpenClaw's `~/.openclaw/workspace/skills/` to Hermes `~/.hermes/skills/`:

1. **Copy scripts** — copy the `.py` files into `~/.hermes/skills/<category>/scripts/`
2. **Write SKILL.md** — document the skill with exact call commands
3. **Share token/config paths** — tokens at `~/.outlook/tokens.json` are shared; no re-auth needed
4. **Attach to cron job** — add skill names to cron job's `skills` array so the agent loads them at runtime
5. **Verify paths** — confirm copied scripts use shared token paths (not hardcoded OpenClaw paths)

**Common pitfall:** OpenClaw scripts may hardcode `~/.openclaw/` paths for token stores. Hermes copies must not change token paths — both systems share `~/.outlook/tokens.json`.

## External Data Sources (read-only, no KG mirror)

| Source | Script | Token Path |
|--------|--------|------------|
| Outlook Calendar | `graph_calendar.py` | `~/.outlook/tokens.json` |
| Microsoft To-Do | `graph_todo.py` | `~/.outlook/tokens.json` |

KG only stores **memory/events**, not data mirrors of external sources.
