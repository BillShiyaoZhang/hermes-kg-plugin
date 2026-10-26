# 个人预制本体验收记录

本轮实现以公开本体选用、对齐及兼容读写为范围。全部测试数据为合成内容，数据库检查使用既有隔离验证容器和独立 namespace；没有安装或修改用户的真实记忆空间。

## 冻结内容

- 个人预设：`personal` / `1.0.0`，摘要 `44cb1e21b9e76ccbef315ded3ee25ea845b9f151d3c0b31144ac12d1bedbd699`。
- 新增 50 项定义：11 类实体、17 个可选标量字段、22 种关系；与兼容核心合计 17 类业务实体。PROV 记录、消息、快照及抽取活动属于临时语义投影，不新增业务节点。
- 来源：Schema.org 30.1、SKOS 2009、PROV-O 2013、DCMI 2020；5 个原始资源文件保留各自摘要及许可。
- 生产前端构建：`index-DTAbf41P.js` / `index-CbAa3P16.css`。
- 验证 wheel：`hermes_memory_console-0.1.0-py3-none-any.whl`；SHA-256 `4d264703f852dbc188cef6ef343a35bf6e93e9611d44dc609f9241788b42b110`。

## 执行结果

| 检查 | 结果 |
|---|---|
| 完整后端套件 | 863 项：835 通过，28 跳过；127.6 秒。包含真实 Neo4j 的本体 CRUD、原子安装、并发、备份恢复及个人事实写入查询。 |
| 补充真实检索检查 | 原先依赖单独环境变量的 12 项检索权威校验与 URI 查找测试全部通过。合并后为 847 项通过，16 项环境跳过。 |
| 前端完整套件 | 22 个测试文件、219 项全部通过。 |
| RDF profile 与 SHACL | 21 项测试通过，含核心合同的 13 个正反对照场景、来源校验、域值兼容、必填、类型错误、记录分离及确定性生成。已计入后端总数。 |
| 发布资源检查 | `build_personal_ontology.py --check` 通过；禁网编译及不同 Python 哈希种子结果一致。 |
| Windows checkout | 在独立临时 Git 仓库以 `core.autocrlf=true` 实际 checkout，验证 profile 与锁定原始资源字节一致。 |
| 独立 wheel 烟测 | 包外目录、Python `-I`、禁止 socket 的进程中加载完整预设并组装 17 类 schema 成功；未导入 pySHACL 或 owlrl。 |
| 浏览器 | Chrome 155，桌面 1440 与窄屏 390：来源、属性、反向映射、物品的 Dublin Core 来源及安全链接通过；无横溢出，console/page errors 为空，两档 axe 均零违规。截图已人工查看。 |

16 项剩余跳过包括未配置的其他混合检索/ANN 实验、未准备的真实 E5 模型缓存，以及当前平台不支持的符号链接情境。没有把这些项目计为通过，也没有执行真实 LLM 抽取质量评估。

## 关键边界

- 新空间判定基于数据库 namespace，已有事实、历史回执或工作空间节点即不自动安装。旧空间甚至不加载可选预设包；显式 `--dry-run` 不写数据库。
- 超过 32 项的预设在内存中分块构建后单事务提交；公开编辑接口仍维持原限制。失败、冲突或依赖停用定义均整批回滚；同版本幂等、降级拒绝，用户修改和删除不复活。
- `HERMES_locatedAt` 允许 Person/Event，而锁定的 Schema.org 30.1 `location` 不含 Person；因此只保存参考注释，不投影成错误的公开断言。事件参与关系只在标准投影中反向，不修改存储边。
- SHACL 以临时 runtime-type marker 限定应用形状，不全局收紧公开属性。记忆记录可描述现实 Event，HealthRecord 也不会因继承 CreativeWork 而获得 Resource 专用字段。
- 验证检查选用语义及应用基线的一致性，不声称完整 OWL 证明或自动判断所有用户陈述的真伪。证据、身份、权限及 namespace 自定义约束继续由运行时负责。
- 实际完整本体浏览器测试发现了异步布局重合端点造成的 SVG NaN，已修复并增加 5 种图层回归测试。

可复现浏览器入口为 `scripts/personal_ontology_browser.cjs`，所有 API 均被合成数据拦截。完整测试日志、浏览器报告和截图、wheel 与包烟测结果留在 Git 忽略的 `reports/personal-ontology/`；临时浏览器预览服务已关闭。
