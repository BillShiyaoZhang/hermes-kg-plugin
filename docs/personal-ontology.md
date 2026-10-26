# 个人记忆本体

个人预设是一份应用本体：从公开词汇选择适合个人记忆的概念和属性，为缺口增加少量本地定义，并保留与当前 Neo4j 存储的显式绑定。日常运行不需要联网读取本体，也不把推导结论写成用户事实。

## 使用

全新的数据库 namespace 在 setup 时默认安装全部定义；已经有事实、工作空间或历史元数据的 namespace 保持原样。是否有 `kg.json`、是否已有本体覆盖层，都不能单独决定它是新空间。

已有空间通过以下命令检查并安装：

```text
hermes-kg --home <profile> ontology install-preset personal --dry-run
hermes-kg --home <profile> ontology install-preset personal
```

预览不写数据库。提交时在工作空间锁内重新读取当前版本并检查冲突，整套定义和安装回执一次提交。失败不会留下半套本体；重复执行不会覆盖用户改名、约束收紧、停用或删除。安装只添加定义，不创建示例人物、任务或其他业务记忆。

知识图谱中的本体检查器可查看标准 URI、来源版本、映射方式及说明。标准 URI 是语义身份，工具输入仍使用当前空间发布的类型和关系 key；它不会自动成为写入别名。界面显示名称可以修改，保留标准绑定的结构修改必须继续符合该绑定。

## 公开来源及职责

| 来源 | 固定基线 | 职责 |
|---|---|---|
| [Schema.org](https://schema.org/version/30.1/) | 30.1，2026-09-16 | Person、Organization、Place、Event、CreativeWork 及选用关系 |
| [SKOS](https://www.w3.org/TR/2009/REC-skos-reference-20090818/) | 2009 Recommendation | 知识主题、术语、名称和概念联系 |
| [PROV-O](https://www.w3.org/TR/2013/REC-prov-o-20130430/) | 2013 Recommendation | 记忆记录、消息、快照及抽取活动的来源语义 |
| [Dublin Core](https://www.dublincore.org/specifications/dublin-core/dcmi-terms/) | 2020-01-20 | 资料、本体包元数据及 PhysicalObject |

原始快照、下载地址、内容摘要和许可信息随本体资源分发。Schema.org 的词汇资源使用 [CC BY-SA 3.0](https://schema.org/docs/terms.html)，与应用代码的许可分别保存；其他资源保留各自声明。中文名称、本地扩展和生成说明明确标记为本项目的改动。

## 语义边界

- 人物、组织、地点、事件及信息内容分别采用 Schema.org 的对应概念；实物采用 Dublin Core PhysicalObject。信息内容和它的实物载体可以分别存在，不能仅因同名合并。
- 新的主题类型采用 SKOS。旧 `Concept` 曾用于项目、文档等兜底内容，继续保留原身份，不自动移动历史事实。
- 本地 Project 表示组织起来的一组工作；Schema.org Project 继承 Organization，不能直接等价。Task 表示行动承诺，Goal 表示期望结果；截止日期不是实际发生时间。
- Skill 表示特定人物明确陈述的能力和熟练度；课程报名不证明掌握技能。Habit 表示明确的持续行为；一次行动不证明习惯。
- HealthRecord 和 FinanceRecord 保存用户陈述的记录。它们不是疾病、药物或金融产品。日期、测量值、单位、金额和币种缺少依据时省略；金额保留为十进制字符串。
- 来源记录和它描述的现实对象分别建模。PROV 的记录实体、抽取活动只用于溯源语义投影，不把每个业务节点统一变成来源实体。
- 类、属性与实例身份的对齐分别处理。不用 `owl:sameAs` 合并相似类，也不因名称近似增加等价公理。需要改变关系方向时只在标准语义投影中转换，不改写既有存储边。
- Schema.org 的 `domainIncludes` / `rangeIncludes` 是候选范围；不能机械转换为多个 `rdfs:domain` / `range` 造成交集语义。

名称、说明和备注可复用；本地业务状态使用 `x_personal_state` 等独立字段。现有 `status=disabled` 会影响记忆可见性，不应用来表示暂停的任务或习惯。日期和具体时刻分字段保存，不给日期强补发生时刻。

## 维护与验证

`skills/kg-memory/ontology/personal/` 中的 RDF profile 是人工维护源；安装清单及 SHACL 是确定性生成物，不手工编辑。现有 `core.ttl` 仍描述兼容的存储契约，上游原文件不会直接送入它的解析器。

```text
uv pip install --python .venv-console/Scripts/python.exe -r requirements-ontology.lock
.venv-console/Scripts/python.exe scripts/build_personal_ontology.py --check
.venv-console/Scripts/python.exe scripts/run_tests.py ontology_profile
```

`--check` 验证固定资源及生成物，无网络下载。修改 profile 后使用构建脚本重新生成，并运行对应单元测试。SHACL 校验针对随包发布的应用基线；空间中的自定义约束、身份和证据仍由现有运行时检查。SHACL 是离线开发能力，运行中的记忆提供器不依赖 pySHACL 或 OWL 推理服务。

升级上游版本时更新快照、摘要和出处，审查定义差异，重新生成并执行验证，再发布新的预设版本。已有空间由用户显式安装新版。首版采用增量安装策略：添加历次未安装的新键，已有定义、绑定及用户的修改、停用和删除保持原状；涉及已有定义语义变更时需要单独迁移，不静默重写。预设降级被拒绝。备份保存定义、语义绑定和预设版本；旧备份没有这些元数据时仍按原数据恢复。

验收覆盖来源术语存在、依赖闭包、身份与别名冲突、方向、数据类型、继承和互斥关系，以及个人场景的正反例。选用模块检查通过表示本应用的已声明约束一致，不是对所有外部本体和任意数据的完整逻辑证明。语义绑定和预设摘要参与检索身份，升级后不会把旧语义缓存误作当前结果。

实现和已执行检查见[个人本体验收记录](personal-ontology-validation.md)。
