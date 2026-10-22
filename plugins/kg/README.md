# Hermes KG 本地插件

本插件通过当前 Hermes 的 MemoryProvider API 注册 `kg`，配合当前 profile 的 `kg.json`、`state.db` 和 `kg-sync/sync-state-v2.db` 工作。插件、依赖和技能均放在 Hermes 用户目录，更新 `hermes-agent` 仓库不会覆盖这些文件。

## 运行路径

- `provider.py`：静态系统提示、按会话和轮次隔离的记忆检索、`kg_search` / `kg_write` / `kg_status`、会话入队。
- `health.py`：只读连接检查、当前 namespace 实体统计、配置用户是否存在及历史非空提交回执统计。
- `config.py` / `schema.py` / `store.py` / `queries.py`：所有入口共用的配置、参数校验、事务和查询。
- `session_source.py`：只读 SQLite 会话快照；排除 cron、工具消息、已撤回消息和压缩摘要。
- `legacy_source.py`：显式导入已检查的历史 JSONL；校验文件哈希，以原文件行号作为游标。
- `sync_state.py`：冻结输入、暂存提取、租约、失败重试和提交回执。
- `grounding.py`：自动提取的用户原话引用与日期证据校验。
- `provenance.py`：提取与证据校验共用的用户正文筛选，排除 Hermes 明确标记的压缩摘要和系统通知。
- `../../scripts/kg_sync_runner.py`：固定 cron 入口。只有提取通过校验且图事务成功，才推进同步进度。

配置解析顺序：显式 profile 路径、`HERMES_HOME`、插件安装目录。连接字段可通过 `HERMES_KG_*` 和兼容的 `NEO4J_*` 环境变量覆盖。凭据使用现有 profile 的配置及 Hermes 官方 dotenv 加载器，不内置 API Key。

## Windows 日常检查

以下 PowerShell 命令从 `C:\Users\zhang\AppData\Local\hermes` 运行：

```powershell
./hermes-agent/venv/Scripts/python.exe kg-sync/check_kg.py
./hermes-agent/venv/Scripts/python.exe scripts/kg_sync_runner.py --dry-run --max-sessions 20
./hermes-agent/venv/Scripts/python.exe scripts/kg_sync_runner.py --verify-only
./hermes-agent/venv/Scripts/python.exe skills/kg-memory/scripts/validate.py
```

这四个命令均不调用模型、不写图谱。`--dry-run` 不创建同步状态文件。

在 Hermes 聊天里询问连接状态时，使用 `kg_status({})`。它明确区分“连接可读”“有可检索实体”“存在历史写入回执”，不会为了诊断而写入测试事件。历史回执证明先前写入成功，不等于本次重新验证了写权限。查询完整 URN 时精确匹配；普通多词主题默认按整句匹配，空结果不能据此推断实体不存在。

`check.mode=read_only_status_probe` 只描述这次诊断，不能解释为“数据库写入模式：只读”。`write_capability` 分别说明当前会话能否调用 `kg_write` 和数据库写权限尚未实测；`legacy_exclusions` 区分保留的旧别名与无法还原的旧记录，均排除于正常检索。

当前 Hermes 网关会缓存已经导入的插件 Python 模块。首次缺失的 provider 可在后续消息重新发现；已经加载后再修改插件代码或工具 schema，需要在修改验证完成后重启当前 profile 网关才能可靠生效。仅 `/new` 或重载 MCP/skills 不会刷新 KG 的 Python 模块。

同步正式执行命令：

```powershell
./hermes-agent/venv/Scripts/python.exe scripts/kg_sync_runner.py
```

正式执行从冻结快照中筛出真实用户正文，发送到 Hermes 当前配置的辅助模型路由（`auxiliary.kg_sync`；未单独指定时沿用主模型优先的官方路由），并写入本地 Neo4j。助手输出、工具消息和具有明确 Hermes 标记的合成摘要/通知不作为提取输入或事实证据；原快照保留供核验。2026-09-13 已获用户授权，使用当前 MiniMax-M3 / minimax-cn 路由完成 6 个历史会话回填，并恢复每 4 小时自动同步。当前统计和回执见修复报告。

默认每轮最多处理 3 个会话，每个快照最多 80 条消息、约 100,000 字符；完整单条消息不会被截断。有效对话达到 5 条才自动进入扫描。`--max-sessions` 可选 1–20。状态以消息游标计数，不以文件修改时间或旧 `processedAt` 判断完成。

每份提取最多 16 个实体、32 条显式关系。输出不符合格式、schema 或证据约束时，允许模型按具体错误修正一次；仍不合格则保留失败状态和原输入。没有真实用户正文时无需调用模型。空提取也必须取得图内回执才能推进游标。

## 数据与证据约定

支持 Person、Event、Preference、Decision、Location、Concept。新实体 ID 使用 `urn:hermes:kg:<type>-<stable-id>`；旧 colon 写法通过统一入口归一化。关系类型经过白名单和端点类型校验，文本全部用数据库参数传入。缺失端点导致整批回滚，不再创建空壳节点。

沿用此共享图谱的既有约定：新节点默认 `shared=true`（原生 boolean），显式 `shared=false` 可保持私有。查询可见性为当前 namespace 或其他 namespace 明确 `shared=true` 的实体；不会返回其他 namespace 的私有实体。图谱提交、回执与实体关系在同一事务内完成。同一快照重试必须使用相同提取，避免重复写入。

自动提取的每个实体和显式关系都必须携带 `evidence: {message_id, quote}`，引用指定用户消息的原文；该证据保存在同步数据库的提取 payload 中。可附最多 4 条 `supporting_evidence`，逐条验证原文，用于日期与改期时间分散在多条消息等情形。模型不能通过自动提取重新命名当前用户。事件日期必须由原文支持；仅有日期时以当地午夜归一化并标记 `hermes_timePrecision=day`，不代表精确发生在午夜。今天/明天按证据消息自身的当地日期解释；无年份的“5月18日”按该条消息的当地年份解释，并标记 `hermes_timeYearInferred=true`。无法确定的事件日期会导致校验失败，保留待处理输入。

引用校验只能证明文本来源。否定、假设、引用他人以及复杂语义是否被正确理解，仍属于模型准确性边界；需要事实级人工审阅时，请使用技能提供的暂存和检查入口。一次语义误判不会因具备原文引用而自动变成正确事实。

旧 `KGLegacyAlias` / `KGLegacyUnresolved` 节点保留供追溯，默认检索排除。无法可靠还原的事实不自动补造。危险的旧批量完成、regex 本体解析和 TTL 清理入口已退役。

`KGQuarantined` 用于内容复核后的可逆归档/隔离，保留业务类型、原属性和关系。对话日志、旧运行状态、有效事实的重复条目和来源不可靠的抽取均可分别归类；此标签不代表原内容一概错误。所有直接、关联和归属查询排除这些节点；普通实体写入与关系端点写入会拒绝已隔离 URI，防止同步再次覆盖或自动恢复。`kg_status.content_quarantined.nodes` 单独统计内容归档/隔离，`legacy_exclusions` 仍只表示旧别名/不完整记录，`quarantined_nodes` 为去重后的总排除数。`validate.py --graph` 检查当前可检索数据，隔离统计由状态入口报告。恢复隔离内容须另行核对证据并进行显式维护。

历史 user-role 行中的后台 skill/memory review 模板，按已核对 Hermes 源码与旧消息的完整长开头，在提取输入和引用证据的共用筛选中排除。普通“请整理记忆/技能”请求和并非位于消息开头的模板引用仍保留；过滤不改原始消息或冻结快照。

人工复核可将已发生但日期不记得的 Event 明确设为 `hermes_timePrecision=unknown`、`hermes_eventStatus=occurred`，并移除起止日期。查询返回 null 时间及明确发生状态，任何日期筛选都排除；仅缺日期而无这些标记仍无效。已知日期区间采用 `hermes_eventEndTime` 排他结束端：5/23–24 是 5/23 零点至 5/25 零点，5/24 的查询按区间重叠命中。自动提取仍保持日期引用校验，不能靠自称 unknown 绕过证据要求。

经用户确认的身份别名保留 `KGQuarantined`，加原生 boolean `hermes_aliasConfirmed=true` 与 `hermes_canonicalUri`。精确旧 URI 查询只对这种明确映射解析一跳，目标仍须可见且不被隔离，返回 `matched_alias_uri`；不会返回旧节点正文。其他归档/隔离 URI 仍不返回。别名 URI 的实体/关系写入会拒绝，普通写入不能创建确认字段。规范实体的 `hermes_aliasNames` 支持按旧名称搜索。`hermes_semanticScope`、`hermes_status` 和 `hermes_statusNote` 随查询返回，保留阅读兴趣、历史选择等事实的解释边界。

结构化 `owner_urn` / `person_urn` 查询也在同一读事务内解析已确认的 Person 别名，结果与规范人物一致；未确认、不可见或含糊映射不会解析。状态保留 `content_quarantined.nodes` 总数，并拆出 `confirmed_alias_nodes`、`ordinary_content_nodes`，明确“别名可解析到可见规范实体”不等于恢复旧记录。

## 依赖恢复与验证

依赖安装在插件自己的 `_vendor` 中，并用哈希锁定版本；不依赖 core venv 是否自带 Neo4j 或 RDFLib。

```powershell
./bin/uv.exe pip install --python ./hermes-agent/venv/Scripts/python.exe --target ./plugins/kg/_vendor --require-hashes -r plugins/kg/requirements.lock --cache-dir ./cache/kg-uv
./hermes-agent/venv/Scripts/python.exe -m unittest discover -s plugins/kg/tests -v
./hermes-agent/venv/Scripts/python.exe reports/kg-repair-2026-09-13/test_kg_sync_runner.py
```

真实 Neo4j 事务回归工具 `reports/kg-repair-2026-09-13/integration_probe.py` 使用随机测试 namespace，并在 finally 中仅清理自身测试节点与回执。运行该工具会写测试数据。模型路由回归工具 `synthetic_model_probe.py` 仅使用脚本写死的虚构句子，不读取真实会话。

修复前备份与迁移计划在 `../../backups/kg-repair-2026-09-13/`。不要把 Neo4j 逻辑备份中的类型化时间当成普通字符串恢复；参阅修复报告和专门的备份恢复说明。
