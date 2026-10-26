# 本地研究 readiness 审计

`scripts/research_readiness.py` 是独立的只读汇总工具，不属于已冻结的研究核心
`93afbf295eb252c809a2de49b2dbc0b7f91f30a589310fc6fff09b1a9c1a9a8e`。
它不打开默认 Hermes profile，不扫描 Downloads，不调用模型，不执行盲测或部署，
也不生成或签署人工 labels、lock、freeze。输入仅限当前工作区的 `reports/`、`private/`。

当前已完成的合成开发集模型运行，不能替代锁定真实 gold 后的运行。脚本分别列出
`run_integrity` 和 `run_integrity_and_target_binding`，避免把“开发报告完整”写成
“已覆盖真实数据、入围配置和盲测”。结果中的 `promotion` 始终为 `blocked`；
`ready_for_release_review` 仅表示汇总检查完成，不会增加额外授权步骤或执行动作。

## 一条命令核对当前状态

从仓库根目录运行，现阶段返回退出码 2 是准确的 blocked 状态：

```powershell
python scripts/research_readiness.py --fixture-dir reports/retrieval-research/private/human-review-sealed --review-source reports/retrieval-research/private/human-review-v2 --run reports/retrieval-research/bootstrap-final/runs/e5-small--entity--vector.json --run reports/retrieval-research/bootstrap-final/runs/qwen3-0.6b--entity--vector.json --output reports/retrieval-research/readiness-private.json
```

不传 `--output` 时只输出 JSON；指定输出必须是新文件，工具拒绝覆盖任何已有产物。
退出码 0 表示所有汇总项完成，2 表示操作成功但存在缺口，1 表示输入不可用。
输出只包含状态、计数、哈希和原因码，不包含题目、正文、来源 URI 或审阅人姓名。

## v2 人工工作台到 sealed fixture

旧 v2 页面不覆盖。它导出的 labels 可以在 sealed fixture 上重新验证：审计会核对
两套 dataset 的内容指纹及所有 `real_pending_human` 条目完全一致。20/40 修正只改了
合成本体问题，未修改真实候选或来源。labels 自带的 `submission_sha256` 也须有效。

旧 lock 不能直接复用：它的 validation 还绑定旧 `fixture_sha256`。收到真实人工导出后，
用现有 CLI 在 sealed 上验证，并将新 lock 写入新路径：

```powershell
python scripts/research_retrieval.py validate-labels --fixture-dir reports/retrieval-research/private/human-review-sealed --labels reports/retrieval-research/private/reviewed-labels.json --lock reports/retrieval-research/private/reviewed-labels-sealed.lock.json
```

只有全部 100 条通过现有严格验证才会生成 lock；模板、模型自评、已填写的问题和
单条 UI 测试导出都不能代替人工 gold。原模板保留；另有机器预标注草稿供人工复核，尚无已审导出。

## 机器预标注后再人工复核

2026-10-26 起，使用私有目录
`reports/retrieval-research/private/preannotation-2026-10-26/` 中的
`human-review-prefilled.html`。页面预填问题、建议答案、相关性0/1/2、完整充分证据集合和
疑点说明；`uncertain-items.md` 单列内容疑点、来源归属缺失与题集覆盖限制，
`prelabels.json` 是可恢复的机器草稿。

所有初始条目均为 `status=pending, review_origin=machine`，不填复核人、复核时间或人审声明。
高置信度只说明冻结快照字段直接支撑该建议，不证明当前业务状态有效或来源已由人确认。
原始fixture、旧工作台及空模板保持原样。没有调用MiniMax生成接口或读取候选盲测排名。

页面支持按内容疑点、来源归属缺失和人工确认状态筛选。输入复核人及明确声明后，
仅勾选实际检查过的条目，再导出JSON；未勾选行仍为机器草稿且人审字段为空。
修改问题、证据或充分集合会撤销该条旧确认；多个替代充分集合不会被合并为一个集合。
全部100条通过人工校验后，仍须在sealed fixture上新建lock，之后才能继续正式实验。

组装入口为 `scripts/build_retrieval_preannotation.py`。它仅合并显式机器批次并验证当前快照、
来源引用与分组，不读取默认profile、运行检索、签署人审或覆盖已有草稿。
新模块位于冻结研究核心之外，原 `93af…a9a8e` 指纹保持不变。

## 可追踪的引用清单

需要固定预期哈希时，使用 `--manifest reports/.../readiness-manifest.json`。
格式为 `kg-research-readiness-v1`，所有引用均为 `{ "path": "reports/...json", "sha256": "原始文件SHA256" }`：

```json
{
  "format": "kg-research-readiness-v1",
  "scope": "profile",
  "dataset": {"path": "reports/.../dataset.json", "sha256": "..."},
  "fixture": {"path": "reports/.../fixture.json", "sha256": "..."},
  "labels": null,
  "labels_lock": null,
  "protocol": null,
  "targets": [{"id": "baseline", "run": null}, {"id": "candidate", "run": null}],
  "comparison": null,
  "e2e": {"freeze": null, "results": null, "human_scores": null}
}
```

每个 target 可以指定自己的 `protocol` 引用和 `evidence`，键为 `ann_evidence`
（可为引用数组）、`installed_safety_evidence`、`http_performance_evidence`、
`authority_evidence`。未提供时读取该 target 协议里的引用。相对引用按工作区根目录解释；
冻结核心的协议 helper 使用当前目录，因此含相对引用的协议须从仓库根目录审计。
所有嵌套引用先经过工作区路径和原始 SHA 检查，不允许借协议读取外部文件。

审计保留冻结核心的全部 evidence blockers，并追加目标绑定：HTTP、安全证据须匹配
tool、dataset、representation、scope、model fingerprint；ANN 还须匹配 namespace 和
实际卡片指纹。旧 E5 HTTP 基线不能冒充新模型或关系卡片路径的验收。
ANN v1 的 `scope: synthetic_development_queries` 描述实验性质，实际范围用
`retrieval_scope: profile|shared` 匹配；不将实验标签当作 namespace 范围。
HTTP / safety 证据的目标范围则要求显式 `scope: profile|shared`。
静态投影、离线 ANN 指纹和普通 `run_id` 不能证明在线权威复核或请求固定代。

当前 authority 仅为受控 harness 中的实验组件；完整生产代注册表尚未实现。
真实 installed safety / 候选 HTTP 证据缺失仍标 blocked。E2E 引用会核对原始哈希，
其执行与人工评分语义当前显式 `not_run`，不将模板计为通过。

完整正式闭环仍需人工 gold、新 lock、预先冻结的目标配置和证据、盲测、成对比较、
真实 E2E 执行及人工评分。readiness 命令只报告这些条件，不会为补齐状态制造证据。
核心 v1 协议的单个 HTTP 引用只绑定一个模型；多模型目标必须分别提供证据，
不能把共享协议或一份旧报告解释为所有候选均已达标。
