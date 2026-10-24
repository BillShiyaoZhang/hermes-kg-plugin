# 本体编辑验收

本轮编辑对象是当前数据库、当前 namespace 的本体覆盖层。内建定义的 key、IRI、存储标签、关系存储类型和实体类型集合保持稳定；显示名称、说明、别名和受支持的收窄规则由服务端能力声明控制。编辑定义不等同于迁移既有事实。

本轮新增关系约束的范围为**两个端点均属于当前 namespace 的有效实体**，与本体图的读取范围一致。指向其他空间共享实体的关系仍受原有内建写入规则约束，不套用本轮 namespace 覆盖层的关系规则。别名输入只支持 `props` / `properties` 容器与关系类型字段；它不改变规范标识或增加新的存储类型。

服务返回 `builtin_constraints` 时，可以撤回自己新增的必填要求、别名或类型范围限制，仍不可越过内建最低规则。旧编辑服务未返回该能力时，界面保守保护当前有效约束；更旧的 `read_only=true` 查看服务继续可用，不显示编辑入口。

## 独立架构审查

| 风险 | 最小完整实现要求 | 验证位置 |
| --- | --- | --- |
| 显示规则与真实写校验分离 | 原 `ontology.py` 读取 RDF，`schema.py` 执行固定运行时规则。发布后的收窄规则必须在真实写事务内统一执行；保留事件未知日期、系统属性、内建枚举及方向等原有规则。 | 后端约束测试；浏览器只能验证请求与用户反馈，不能证明数据库约束生效。 |
| profile 文件与 namespace 不一致 | 多个 profile home 可以连接同一数据库、同一 namespace。版本和覆盖层应按数据库中的 namespace 保存，避免仅修改本地安装资源或各 profile 私有文件。 | 后端跨 namespace、同 namespace 多服务及重启回读测试。 |
| 预览后的并发变化 | 预览是只读检查。发布事务须重新检查本体版本、当前事实兼容性并与事实写入采用同一 workspace 锁；过期预览不能授权跳过检查。 | 版本冲突、数据冲突和并发测试。 |
| 部分写入或其他入口绕过规则 | 检查合并后的持久化实体/关系；批准候选、人工更正和自动写入均使用同一约束。归档恢复绕过普通批次写入口，也须检查恢复后的有效记录。拒绝候选不应被无关实体规则阻断。 | 后端事务/恢复/审阅测试。 |
| 别名改变代码或覆盖已有含义 | 规范身份不可编辑；别名先正规化，拒绝歧义、重复和规范名称冲突，最终类型仍受原有存储白名单限制。拒绝未支持的字段及 arbitrary Turtle。 | 后端别名、保留字段、额外字段和注入用例。 |
| 草稿或写入被导航打断 | 打开编辑器即锁定工作区，首次字段修改不依赖异步 effect 才生效；改字段撤销旧预览；写入等待中不能取消、切换或再次发布。 | 合成 API 浏览器的草稿和 pending 操作。 |
| 刷新定义意外重排画布 | 使用返回的有效 schema 原位更新。相同 key 的节点坐标、镜头、选择和检查器上下文保留；新图读取或自动 fit 不应由纯元数据发布触发。 | 合成 API 浏览器比较相机和节点坐标、统计图请求次数。 |
| 409 或未知结果丢失输入 | 保留所有草稿。冲突后显式读取最新基线并重新预览；发布重试使用与相同逻辑操作一致的 operation ID，不能把网络失败当作确定未写入。 | 浏览器冲突/失连；后端幂等回执。 |

## API 验收边界

契约使用 `GET /api/v1/ontology` 返回版本和编辑能力；`POST /api/v1/ontology/validate` 接收 `expected_namespace`、`expected_version` 与 `patch`，返回可发布状态、变更字段、兼容影响和有效草稿；`POST /api/v1/ontology/publish` 增加 `operation_id` 与可选原因，返回已发布 schema。namespace 从服务配置确定，`expected_namespace` 仅作为防止错空间提交的前置条件，不接受请求切换写入范围。

发布冲突应区分版本冲突、数据兼容冲突和操作标识冲突。验证错误不能带入原始数据库查询、访问令牌或任意用户输入；既有同源认证与 `no-store` 策略适用于新增端点。

## 浏览器实测

独立脚本 `scripts/ontology_edit_browser.cjs` 拦截所有 `/api/` 请求，只返回虚构内容；未识别的 API 请求直接使验收失败，不透传到真实服务。最终运行针对 **198 项前端测试通过后生成的生产构建**，使用 `http://127.0.0.1:5175` 的 Vite 预览。它验证完整 App 的浏览器界面操作，不连接记忆服务或真实数据库，也不等于已部署服务的端到端写入验收。

参考环境为 Windows、Chrome 155.0.8059.85 无头模式，桌面 1440 × 1000 与窄屏 390 × 844。全部交互断言通过，两个视口下工作区 axe WCAG 2 A/AA、2.1 AA 扫描均为零违规，浏览器 `pageerror` 为空。触控硬件和人工辅助技术没有在本轮实测。

| 任务 | 实际取得的证据 | 结果 |
| --- | --- | --- |
| 进入编辑 | 从选中概念定义进入；按能力仅开放显示名称与说明。系统维护属性没有编辑按钮。所有提交的字段均在服务端能力白名单内。 | 通过 |
| 草稿锁 | 修改后用鼠标尝试切换探索视角，编辑器、选择和输入保持；取消弹出放弃确认，选择继续编辑保留输入，未发送发布。 | 通过 |
| 预览后修改 | 元数据预览展示影响 1 条实体；改变说明立即令发布失效，第二次预览后才能提交。 | 通过 |
| 元数据发布 | 核对正文中的 namespace、版本、patch 和 operation ID；名称与说明实际显示更新；镜头 transform、各节点 transform 和选择与编辑前完全一致，新增图查询数为零。 | 通过 |
| 409 | 发布返回冲突后名称、说明完整保留；显式读取版本 2 不自动预览，旧预览不可发布；手动预览使用版本 2，再明确发布。 | 通过 |
| 兼容冲突 | 将内容说明设为概念必填，返回一条既有记录缺失；`can_publish=false` 显示阻断，发布按钮禁用且没有写请求。此影响由合成接口返回。 | 通过 |
| 自定义要求撤回 | 先实际操作发布人物必填，再打开属性编辑取消该要求，重新预览并发布 `required_for=[]`。 | 通过 |
| 旧能力保护 | 删除 `builtin_constraints` 能力后，已存在的必填要求保持禁用；旧只读服务仍显示本体，编辑入口不出现。 | 通过 |
| 等待中的发布 | 用受控 Promise 延迟发布回执，期间发布与取消禁用；鼠标切视角及 Escape 均不能关闭草稿，也没有重复发布。 | 通过 |
| 503 预览失败 | 显示脱敏错误，全部输入保留，未伪造成功；重试预览后才恢复发布。 | 通过 |
| 401 重新连接 | 预览返回 401 后编辑器和输入保留；在编辑器点击重新连接，真实操作 App 令牌表单并连接；回到原草稿，必须重新预览后才可发布。 | 通过 |
| 桌面和窄屏 | 主编辑器位于检查器内的具名 region，只有放弃确认使用对话框；1440 与 390 px axe 均为空。390 px 无横溢出，取消按钮命中测试可达，发布说明和可复制草稿可滚至 sticky 操作栏上方。 | 通过 |

运行过程中发现并修复了 409 重新读取后父级旧版本使编辑器永久要求重新核对、违例细节及窄屏辅助文字对比不足的问题。浏览器测试保留了这些失败场景对应的断言。检查器内联编辑还避免了主编辑模态与重新认证模态互相争夺焦点。

结果保存在忽略的 `reports/ontology-editing/results.json`，合成请求正文保存在 `requests.json`；截图包括元数据发布后、冲突保留草稿、约束阻断、窄屏字段与窄屏操作栏。脚本不会将访问令牌写入该请求日志。

```powershell
# console/：先完成生产构建并启动本机预览
npm test
npm run build
npm run preview -- --port 5175 --strictPort

# 仓库根目录：可用已有 Playwright 与 Chrome，也可使用默认安装
$env:KG_ONTOLOGY_BROWSER_URL = 'http://127.0.0.1:5175'
$env:PLAYWRIGHT_MODULE = 'C:/path/to/node_modules/playwright'
$env:KG_CHROME_PATH = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
node scripts/ontology_edit_browser.cjs
```

真实 Neo4j 的约束执行、事务竞争、幂等和 namespace 隔离应独立记录，不能由本脚本的合成响应替代。真实触屏、屏幕阅读器及其他浏览器仍需对应环境验证。

## 后端实测

2026-10-24 在 Windows、本机隔离 Neo4j 开发实例 `bolt://127.0.0.1:17687` 运行以下 9 个模块，**163 项全部通过**，末次运行 10.058 秒。这是一次功能回归运行的总时间，不是接口延迟或性能分位数；前述 198 项前端测试与浏览器合成接口证据另行记录。

| 模块 | 测试数 |
| --- | ---: |
| `kg.tests.test_ontology_editing` | 32 |
| `kg.tests.test_store_contract` | 24 |
| `kg.tests.test_reviewed_memory` | 16 |
| `kg.tests.test_graph_exploration` | 16 |
| `kg.tests.test_graph` | 14 |
| `kg.tests.test_ontology` | 10 |
| `kg.tests.test_console_web` | 23 |
| `kg.tests.test_lifecycle` | 19 |
| `kg.tests.test_maintenance` | 9 |

本体专项的 32 项包含 7 项纯函数/事务替身测试和 25 项真实 Neo4j 测试。纯函数覆盖固定技术身份、能力声明、内建约束下限、按集合比较的未改变结构字段、歧义别名、未知日期的内建条件及损坏重复回执的拒绝。真实数据库覆盖 namespace 前置条件、同 namespace 跨 home 回读、版本竞争、历史幂等回执、既有数据冲突、合并后记录验证、整数坐标语义、关系端点、人工更正、批准/拒绝候选、归档恢复和发布前的再次核对。

新增系统节点通过显式 `ensure_schema()` 建立唯一约束：状态按 namespace，修订按 namespace/version，回执按 namespace/operation ID。测试实际尝试创建重复节点并确认数据库拒绝；损坏的重复回执读取也会失败，不任取其中一条。API 用 `TestClient` 验证认证、同源限制、严格类型、额外字段、版本冲突和成功发布，它不代表已部署浏览器与真实服务的一体化端到端验收。

待审批次额外保存暂存时的 `ontology_aliases_json`。批准时先按历史绑定正规化候选，再按当前版本与约束写入；原始 `payload_json` 和业务证据保留。真实回归证明：`memo → hermes_note` 暂存后，移除该别名仍批准到原 note；将 memo 重绑到 description 后，旧批次仍写 note，原 description 保持不变。旧批次没有该快照时按无自定义别名解释；如当前新别名会改写其原字面字段，批准返回冲突，批次保留待审并仍可拒绝。更正与批准的单次请求还会核对别名预处理时的本体版本，防止预处理与写事务之间重绑。

备份恢复真实验证了覆盖层版本和发布幂等回执往返。另将合成备份中实体的必填 note 移除、重新计算校验和后恢复，确认约束检查拒绝并回滚整个图事务，未落下部分实体或本体状态。原有无覆盖层备份、历史关系拼写和时间值回归也在上述模块内通过。

所有数据库用例使用随机、独立的合成 namespace 和临时 home，通过测试清理移除自己创建的实体与元数据；未查询真实业务空间。provider 单元测试现明确 mock HTTP 不可用，避免本机 console 抢先响应、绕过 `CaptureStore`；最初一次 historical/active 断言失败在隔离这个环境耦合后消失，生产 provider 的回退行为没有改动。

复现命令（先在当前进程配置隔离实例的 `KG_TEST_PASSWORD`；不使用业务数据库）：

```powershell
$env:PYTHONPATH = 'plugins'
$env:KG_TEST_BOLT_URL = 'bolt://127.0.0.1:17687'
.venv-console/Scripts/python.exe -c "import sys,logging,unittest; from scripts.run_tests import find_core; sys.path.insert(0,str(find_core())); logging.getLogger('neo4j.notifications').setLevel(logging.ERROR); unittest.main(module=None,argv=['unittest','kg.tests.test_ontology_editing','kg.tests.test_store_contract','kg.tests.test_reviewed_memory','kg.tests.test_graph_exploration','kg.tests.test_graph','kg.tests.test_ontology','kg.tests.test_console_web','kg.tests.test_lifecycle','kg.tests.test_maintenance','-q'])"
```
