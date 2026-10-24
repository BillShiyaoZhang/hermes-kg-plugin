# 部署与下一次 Agent 生效验收

日期：2026-10-24。使用说明见 [本机部署、升级与日常使用](deployment.md)。本记录把临时夹具单测、隔离真实数据库/模型、合成浏览器和本机实际服务分开列出，不把其中一种证据代替其他层次。

## 本轮环境与已有结果

- Windows 本机，Python 3.11；Neo4j 5.26 Community 使用可丢弃测试实例及每次生成的合成 namespace。真实 E5 测试从已准备本地缓存进行 CPU 推理，不依赖测试时下载模型。
- 修复后的最终后端套件：**534 项，OK，跳过 1 项，95.178 秒**。跳过项是 Windows 无权限创建实际 symlink 的 runtime leaf-link 用例；并非数据库或真实模型用例被跳过。完整日志为忽略的 `reports/deployment-v1/full-tests-final.txt`，包含不提供顶层 `kg` 包路径时的隔离插件资源加载回归。总数包括各定向模块，不能再把子模块数量相加；旧 `full-tests-release.txt` 的 533 项仅为此前候选记录。
- 前端完整套件 **211 项通过**，生产构建通过。`graph_v3_browser.cjs`、`ontology_edit_browser.cjs`、`ontology_crud_browser.cjs` 三套浏览器回归通过，报告无浏览器错误，所测 axe 扫描无违规。浏览器接口使用合成拦截，不能据此宣称浏览器已写入生产记忆库。
- 本机既有 profile 的生产部署及同命令重复升级通过，`repeat-upgrade.json` 记录 `ok:true, complete:true`：连接身份与令牌、Hermes 配置不变，耐久队列保持 **17→17**。当次安装 `source_sha256` 为 `d735b85b31ab5ac03589100f7f0c00b00e145f086eb760432b32a6e592c05bc2`；它标识实际发行内容，不是 Git 提交 SHA。控制台与 Hermes 网关已启动，严格宿主探测通过；PID 仅是一次进程身份，不能当作后续健康保证。
- 独立合成 profile 的真实新装及同命令重复部署通过 **23 项检查**，见 `fresh-install.json`：两次真实 API、数据库、静态资源和 E5 健康检查全部通过，身份/令牌/历史边界及 1 条合成待处理会话保留。两轮 source hash 与上文一致，bundle hash 均为 `d51903d93869c32022119036e35e507af7245a752f203bcfae059880fedce69e`，`changed_release_files:[]`。此 profile 没有模型路由，**仅跳过其网关重启，两次均明确为 `complete:false`**，不能替代生产 profile 已通过的完整网关重启；测试未调用外部模型。临时控制台、namespace 和 profile 均已清理。
- 已安装入口由 `python -S <profile>/scripts/kg_service.py start` 实测通过，`installed-startup.json` 为 `ok:true, already_running:true`，认证 API、数据库、静态资源和语义模型健康检查全部通过。它证明在本机同发行版本运行中能从 stdlib 引导并仅探测，不等于实际 OS 重启后的冷启动已测。
- 安装目录的隔离真实 HTTP→Agent 验收 **8 项全部通过**，`installed-acceptance.json` 为 `ok:true, cleanup:true`，个人历史发送数为 **0**，整体耗时 **77.718 秒**。其中实际 AIAgent 合成会话耗时 **43.006 秒**，依次调用 `kg_status → kg_search → kg_write → kg_search`，工具结果全部成功；连接、更新事实召回、混合检索及保存均为 true。写入 1 个合成实体，并通过独立 HTTP 详情读取核实。安装 provider hash 为 `a2085d5b8b83f10a2e6285c1ef97f0c9896b95cf42514c206ecf62bfb6ca90e2`。
- 小规模真实模型抽取夹具共 **16 项，API/校验有效 16/16，TP 8、FP 1、FN 0**，误报为多提取了一个日期实体，事实精确率约 88.9%、召回率 100%，见 `model-extraction.json`。保留该原始报告的 `quality_pass:false`，以及附加动态项未提取自定义类型/必填属性和关系的结果。独立单次诊断 `model-extraction-dynamic-single.json` 成功输出 `x_project` 类型及同名 URI kind、`x_score:4` 和 `X_LEADS`，原文证据与严格 prepare/normalize 校验通过，且未写生产图；它不替换原始分数，也不证明每次都能准确抽取自定义本体。

## 验收矩阵

| 编号 | 要验证的行为 | 当前证据与状态 | 边界 |
| --- | --- | --- | --- |
| DEP-01 | 源码构建；完整暂存；锁定且独立的 Python runtime；安装文件和静态资源比对 | 单测及本机升级、重复升级通过；manifest 区分 Git SHA、dirty 状态、实际源码/资源哈希，安装内容哈希见上文。 | 不把本地 dirty 工作树称为已发布 GitHub 发行版；哈希对应本轮已验发行内容。 |
| DEP-02 | 新装配置、namespace、用户身份、令牌、provider 激活；失败后安全重试 | 单测通过；合成 profile 的真实新装及同命令重复部署通过 23 项检查，真实数据库/E5/API 就绪，身份、令牌、历史边界和 1 条待处理会话保留，临时资源全部清理。 | 合成 profile 无模型路由，仅跳过网关重启，明确 `complete:false`；生产业务 profile 的完整重启另已通过。不能推广到所有系统和无缓存首装。初次失败保留生成身份和历史边界，不反向删除已成功的幂等 DB DDL。 |
| DEP-03 | 升级保留已有连接/身份/令牌/会话/同步状态；清除过期代码与旧 bundle | 临时 profile 回归通过；本机既有 profile 升级及重复执行通过，连接身份/令牌与 Hermes 配置不变，队列 17→17。 | 私有备份只本地保留，不附在仓库中；配置并发修改拒绝覆盖。 |
| DEP-04 | 精确旧控制台归属、空闲检查、同步锁、进程代次；未知监听绝不盲杀 | OS 所有权和回滚回归通过；本机旧服务已受控迁移。 | Windows 旧 launcher 没有控制通道时，仅允许终止独立核验的精确进程句柄；不能推广到任意端口上的进程。 |
| DEP-05 | 新控制台认证 API、数据库、安装版 JS/CSS、语义就绪 | 完整部署的现场探测通过，包含未认证请求拒绝及资源哈希。 | 这是部署时刻的烟测，不是长期可用性或持续监控保证。 |
| DEP-06 | 官方 Hermes 重启；OS 身份更新；新鲜状态；实际 MemoryManager 隔离加载安装 provider | **严格只读宿主检查通过**：移除额外插件路径后，经官方入口重启、核对进程身份与新鲜状态，由实际 MemoryManager 隔离加载安装 provider 并执行 `kg_status`。生产重复完整升级仍通过，队列 17→17；隔离资源加载修复及回归已进入最终 534 项。 | 只读宿主检查没有调用模型或保存事实，实际 AIAgent 工具链证据另列 DEP-12。不宣称任意 Hermes 版本兼容。 |
| DEP-07 | 已确认旧 Startup 自动迁移；新装仅 `--autostart`；备份、字节回滚、并发编辑保护 | Windows 临时 Startup 夹具通过；本机精确归属的旧 KG 项已成功迁移，同命令重复升级为 `kind:unchanged, enabled:true`。 | 实际 OS 重启/退出重登未测。其他 profile、网关和 AgentComm 项不属于迁移目标。 |
| DEP-08 | 任意 Python 3.11+ stdlib 引导已安装 `kg_service.py start`；同发行版本仅 probe；未知监听拒绝 | `-S` bootstrap、manifest/runtime 校验、同版本、过期 metadata、未知端口和不健康新进程的单测通过。本机安装入口 `python -S` 实测 `ok:true, already_running:true`，四项健康检查均通过。 | 实测为已有同发行版本服务运行时的幂等入口；实际 OS 重启冷启动未测。POSIX uv leaf-link 分支在 Windows 因权限跳过，Linux/macOS 登录服务未测。 |
| DEP-09 | Web 更正/归档/恢复/批准后，同轮或下轮检索不复用旧事实 | provider/generation、精确索引、版本 hydrate、跨进程通知和提交后降级回执回归通过。安装目录真实 HTTP/Neo4j/E5 场景验证同一个 Agent 无重启/索引等待读取更正，归档/恢复及耐久审阅批准在下次工具读取生效，幂等重放通过。 | 已开始回答和既有历史上下文不会被追溯改写；有限合成任务不等于长期运行稳定性。 |
| DEP-10 | 自定义本体进入动态抽取提示；停用/结构变更/版本竞争/alias 意义；审阅再次校验 | 动态提示与最终约束回归通过；安装 HTTP 场景确认新本体立即可被 Agent 显式写入和图查询使用，停用/恢复/确认删除通过。真实模型原始 16 项 TP 8、FP 1、FN 0；独立单次自定义类型/属性/关系诊断严格校验通过。 | 原始报告有日期实体误报，附加动态项曾漏提自定义结构；成功诊断不替换原分数。显式工具可写与自动抽取准确率是不同证据，不能宣称抽取无误或稳定泛化。 |
| DEP-11 | 受保护人工事实冲突形成候选；批准/拒绝；耐久回执成功后才推进同步 | lifecycle、sync、schema 和真实 DB 回归包含在最终 534 项中；旧 provenance 与安全限制保留。 | 权威会话 ID 先入队，抽取按调度发生；不承诺每轮对话立即生成可用事实。 |
| DEP-12 | 从安装目录实际走 HTTP、数据库、MemoryManager 与真实模型工具调用 | **8 项检查通过，清理完成**。真实 AIAgent 执行 status/search/write/search 全部成功，召回 Web 更新、使用混合检索并写入 1 个实体，独立 HTTP 详情再次核实；Agent 43.006 秒，整体 77.718 秒。 | 仅随机合成 namespace 和会话，个人历史发送数 0；安装 provider hash 见上文。单次真实模型成功不代表总体准确率或延迟保证。 |
| DEP-13 | 界面主任务、草稿锁、409/401 恢复、窄屏、命名视图失效筛选与基本自动可访问性 | 211 前端测试和三套合成浏览器回归通过。细项见 [CRUD](ontology-crud-validation.md)、[编辑](ontology-editing-validation.md)及 [Graph v3](graph-v3-validation.md)。 | axe 零违规不能代替屏幕阅读器、完整人工键盘和真实触屏验收。 |
| DEP-14 | 异常回滚与恢复边界 | 临时 profile 测试覆盖配置/payload/startup/服务回滚、并发编辑保留、journal 阻断及新装身份保留。 | 没有通过真实 OS 断电/崩溃注入证明恢复；DB schema/index 和已经提交的业务数据不作代码回滚。 |

## 性能目标与当前证据

本轮最终部署套件的 **95.178 秒是测试总耗时**，不是服务响应、安装耗时、Agent 推理耗时或 p95。此轮未重新采集已部署完整工作台的稳定性能分布。

| 项目 | 目标或参考要求 | 已有测量 | 本轮结论 |
| --- | --- | --- | --- |
| 本地选择反馈 | 参考设备上 p95 ≤100 ms | 前一轮独立 Canvas：300 节点/800 边，50 次浏览器原生鼠标点击，p95 67.5 ms，中位 26.8 ms，最大 208 ms；完整原始值保留。 | 该设备/夹具达到 p95 目标；不是“每次点击都≤100 ms”，也不是本次安装后重测。 |
| 持续平移/缩放 | 参考设备约 60 FPS | 前一轮同 Canvas 六个约 4 秒样本：59.995–60.000 FPS，帧间隔 p95 16.8 ms；输入为 rAF 调度的程序事件。 | 只支持该样本；不能代替物理触屏、冷布局或完整 HTTP 详情响应。 |
| 类型定义/过滤首屏/详情 | 设计参考：热态 p95 ≤1 s / ≤2 s / ≤1.5 s，冷模型另列 | 有真实 10,000 实体/30,000 边的直接服务三次样本，见 Graph v3 记录；不经过浏览器/HTTP。 | 尚无本次部署端到端稳定 p95，不标为全面达到。 |
| Web 提交→索引→Agent 使用 | 成功提交后下一次实际检索读取当前版本；失败必须区分写入回执与检索降级 | 功能回归及安装目录真实 HTTP→同一 Agent 的更正/归档/恢复/批准场景通过；真实 AIAgent 召回更新并完成保存。 | 这是正确性语义；没有统一毫秒 SLA，也不保证一次模型回答准确性。 |
| 真实部署与 Agent 样本耗时 | 仅记录，不预设跨设备时限 | 有缓存合成 profile 新装 59.844 秒、重复部署 47.281 秒，二者跳过其网关重启；安装目录验收整体 77.718 秒，其中真实 AIAgent 43.006 秒。 | 单次计时不是稳定 p95、无缓存首装、模型 SLA 或生产完整部署时限。 |

Canvas 参考设备为 Windows、Intel Core i7-12700、20 逻辑核、64 GB RAM、Chrome 155.0.8059.85 无头、1200×1000 视口。样本方法和局限保留在 [Graph v3 验证记录](graph-v3-validation.md)，不将历史微基准包装为新部署性能。

## 仍未实测

- Windows 实际重启、退出再登录后的自动启动；长期后台运行、机器睡眠/唤醒及异常断电恢复。
- macOS/Linux 的完整实机安装与登录/开机服务、Safari/Firefox/Edge 的全任务矩阵。
- NVDA、VoiceOver 等屏幕阅读器，真实触屏与手势、原生 400% 缩放和强制颜色的完整人工验收。
- 新装无缓存全离线流程、跨硬件性能、冷缓存大图、万级真实 HTTP/浏览器路径查询、并发吞吐与长时间稳定性。
- 除本轮限定合成任务外的真实模型质量与耗时分布；不得以单个合成 Agent 成功代替总体准确率。

## 复现与证据管理

完整测试需按锁文件准备依赖，将 `KG_TEST_BOLT_URL` / `KG_TEST_PASSWORD` 和搜索合同使用的 `KG_SEARCH_TEST_URL` / `KG_SEARCH_TEST_PASSWORD` 指向可丢弃 Neo4j，将 `KG_SEARCH_MODEL_CACHE` 指向已准备的本地 E5 缓存，再运行 `python scripts/run_tests.py`；未配置数据库/模型的跳过结果不能冒充本轮全范围实测。前端在 `console/` 运行 `npm test` 和 `npm run build`；浏览器各脚本在仓库根目录对本机生产预览运行。

安装后隔离验收入口为 `scripts/verify_deployment.py --home <已安装profile> --model-cache <已准备E5缓存> --output <忽略的报告路径>`。真实模型路径另显式增加 `--real-agent --host-python <Hermes宿主解释器>`；它允许调用已配置路由，仍只创建自己的合成会话与 namespace。本轮 `installed-acceptance.json` 已逐项记录 8 个通过检查、安装 provider hash、工具调用和清理结果。

本轮忽略的 `reports/deployment-v1/` 证据包括 `full-tests-final.txt`、`repeat-upgrade.json`、`fresh-install.json`、`installed-startup.json`、`installed-acceptance.json`、`model-extraction.json` 和 `model-extraction-dynamic-single.json`。后一个诊断明确标记不替换质量样本，原始漏项和误报继续保留。

测试报告不是发行 payload。部署备份、访问令牌、私有连接配置、个人会话和真实记忆不得加入公开验收附件；只提交本说明和脱敏后的结论。
