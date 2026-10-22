> Historical design/research note. It does not describe the current installed runtime.
> For executable paths, schema, configuration and migration rules use [the active skill](../SKILL.md)
> and [current storage contract](entity-schema.md). Do not execute old cleanup or deployment examples.

# Hermes vs OpenClaw KG Architecture — Session Findings

## Source
Comparison of Hermes Agent and OpenClaw official documentation + local implementation review (2026-05-15).

---

## Core Difference in KG Integration

| | Hermes | OpenClaw |
|---|---|---|
| **KG as** | Memory Provider plugin (opt-in) | Native first-class (AGENTS.md + tools + cron) |
| **接入时机** | initialize() 时初始化，每轮 prefetch/sync | "Always check KG first" 写死在 AGENTS.md |
| **实现路径** | skill 层指令（SKILL.md 里的"KG is source of truth"） | agent loop native（无 provider 层） |

**结论**: OpenClaw 的 KG 是 agent loop 的第一公民，Hermes 的 KG 目前是 skill-level 的知识注，需要通过 Memory Provider 接入才能自动化。

---

## Honcho Memory Provider 实现参考

路径: `~/.hermes/hermes-agent/plugins/memory/honcho/`

架构三层:
```
HonchoMemoryProvider (__init__.py)
  ├── MemoryProvider ABC 实现
  ├── 5 tool schemas: honcho_profile/search/reasoning/context/conclude
  └── 持有 HonchoSessionManager

HonchoSessionManager (session.py)
  ├── session 生命周期管理
  ├── write_frequency: async/turn/session/int
  └── 持有 honcho SDK client

外部 honcho-ai 包
```

关键设计（KG Memory Provider 应复用）:
- **cron guard**: `if agent_context in ("cron", "flush"): return` — 避免 cron 的系统 prompt 污染用户记忆
- **write_frequency**: async（后台队列）/ turn（同步）/ session（仅结束）
- **cadence 控制**: 每 N 轮才做一次 context fetch，避免每轮都查 KG
- **prefetch()**: background thread 预热，结果缓存供 turn 使用

---

## KG Memory Provider 推荐骨架

```
plugins/memory/kg/
  __init__.py    # KGMemoryProvider（MemoryProvider ABC + 4 tool schemas）
  session.py     # KGSessionManager（复用现有 scripts）
  client.py      # Neo4j subprocess 封装
```

**Tool schemas**（复用 kg-memory skill 的 scripts）:
- `kg_search` → query_natural.py
- `kg_write` → manage_entity.py
- `kg_query_events` → query_events.py
- `kg_manage_entity` → manage_entity.py

**Config key**: `HERMES_KG_URN_PREFIX`（默认 `urn:hermes:kg:*`，与 OpenClaw 的 `urn:openclaw:kg:*` 天然隔离）

---

## OpenClaw 的 AGENTS.md 模式（参考）

OpenClaw 在 AGENTS.md 开头写死 KG 指令，Hermes 不需要这样——因为 Memory Provider 的 `system_prompt_block()` 会自动注入。但 SOUL.md 里仍应保留一句作为纵深防御:

> "KG is your memory. Always read from KG before answering."

---

## 与 OpenClaw 共用 Neo4j 的前提

- Hermes URN 前缀: `urn:hermes:kg:*`
- OpenClaw URN 前缀: `urn:openclaw:kg:*`
- 两边数据隔离，无冲突
- **共享机制**: `shared = true` (boolean) 字段标记公用实体，`shared = null` 为私有
- `manage_entity.py` 自动写入 `shared = true`
- `query_natural.py` 默认只查 `shared = true` 的实体
- OpenClaw 也用 boolean `shared = true`，两边完全兼容
- 建议: Hermes KG Provider 开发完成后，OpenClaw 那边的 sync cron 仍可保留（做兜底）
