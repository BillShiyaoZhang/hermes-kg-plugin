# CI 修复验证（2026-10-24）

基线提交 `dc6ad46` 的 [Alpha checks](https://github.com/BillShiyaoZhang/hermes-kg-plugin/actions/runs/40063956895) 中，前端和上游兼容检查通过；三个 Python 平台与 Neo4j 检查失败。根因包括真实实现兼容问题和测试环境假设，不能统一归为 CI 故障。

| 根因 | 分类 | 修复与回归 |
| --- | --- | --- |
| 固定 Hermes core 的旧版 combined review 提示词未被识别，可能进入用户事实提取与依据 | 实现兼容缺陷 | 精确识别旧版长前缀，继续兼容新版；验证提取和证据均排除自动提示，普通用户请求与引用保留。 |
| CI 仅检出 core 源码，真实 SDK 测试却依赖 core 安装目录中的解释器 | 测试环境声明缺失 | 测试 runner 显式给子进程选择当前测试解释器，并保留两种显式 host 覆盖；真实 SDK 测试先绑定合成 profile，再导入原生 client。生产解释器发现规则保持严格。 |
| macOS 符号链接和 Windows 8.3 目录别名导致同一目录出现不同字符串 | 跨平台测试断言错误 | 比较解析后的路径，新增目录别名回归；仍要求优先使用指定 profile 的 core，不能误选其他安装。 |
| 两个旧单元测试可能读取默认 profile 并连接本机 console，绕过预设的 store mock | 本地测试隔离缺陷 | 使用临时 profile 并显式阻断 HTTP 分支，保留原查询与隔离条件断言。 |

没有修改 CI 工作流、放宽生产解释器回退、增加跳过项或调整依赖锁。

## 本地验证

参考环境：Windows、Python 3.11，使用与 CI 完全一致的 core 提交 `cdceca42e107f0e51ab9ff50e1cc881ad76b577e`。该检出没有 `venv` / `.venv`；测试解释器使用现有 `requirements-console.lock` 依赖。

- 无数据库完整套件：415 项，314 通过、101 按既有条件跳过，退出码 0。
- 隔离 Neo4j `bolt://127.0.0.1:17687` 完整套件：415 项，413 通过、2 个真实模型用例按既有条件跳过，退出码 0，耗时 71.514 秒。包含本体 CRUD、图探索、生命周期、备份及真实索引检索回归，使用合成命名空间。
- 真实 SDK 契约实际调用合成 loopback HTTP 服务；保留模型、dotenv 凭证、一次请求、MemoryManager 调度、外网阻断与真实用户 profile 读取阻断断言。它不调用外部模型。
- 对固定 core 三个完整后台提示模板进行静态解析，确认均被正确识别和排除；不导入或执行该模板模块。
- 差异检查通过；诊断日志与下载的 core 均在忽略目录 `reports/ci/`，不进入提交。

## 复现

将 `HERMES_AGENT_HOME` 设为上述固定 core 检出，安装锁定依赖以及 CI 声明的 `jsonschema`、`setuptools>=75`，运行 `python scripts/run_tests.py -v`。如需另一个已安装 host 解释器，显式设置 `KG_HERMES_PYTHON` 或 `HERMES_PYTHON`。

数据库套件另需配置 `KG_TEST_BOLT_URL` / `KG_TEST_PASSWORD` 和 `KG_SEARCH_TEST_URL` / `KG_SEARCH_TEST_PASSWORD`，指向独立测试实例。真实模型的两个用例仍要求显式准备 `KG_SEARCH_MODEL_CACHE`；CI 不自动下载模型。
