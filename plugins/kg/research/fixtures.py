"""Authored synthetic questions and fail-closed, explicit human-gold workflow."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
import re

from . import FORMAT
from .common import check_format, digest, read_json, relation_key, write_json

ATTESTATION = 'I manually inspected the authorized source and gold relevance.'
NAMESPACE = 'urn:kg-research-synthetic:kg:'
NOANSWER_TOPICS = ('联系人私人手机号码', '尚未提供的采购订单号', '未决定的公开发布日期', '没有记录的供应商合同编号',
                  '未确认的主讲人护照号码', '未提供的工资明细', '尚未发生的下一次航班号', '未选择的酒店房间号',
                  '从未告知的过敏诊断', '未提及的家属饮食禁忌', '尚未说明的文章字数上限', '不存在的邮件抄送名单',
                  '未记录的永久删除日期', '未提供的纸质档案柜编号', '从未创建的异地保险单号', '未告知的加密密钥内容',
                  '不存在的仓库传送带序列号', '尚未记录的运货司机姓名', '未实测的屏幕阅读器版本', '未说明的触屏设备型号',
                  '没有测试的图形卡功率', '尚未确定的下一款处理器型号', '不存在的审批人签名文件', '未指定的监管机构编号',
                  '未授权的公开下载链接', '没有生成的纸质出版ISBN', '未提供的身份证号码', '未核对的家庭住址',
                  '尚未发生的来年庆典时间', '未知的冬令时会议偏移', '未创建的第三方空间定义键', '未经发布的新关系名字',
                  '没有配置的远程监听地址', '未知的操作系统重启完成时间', '尚未取得的全行业评测排名', '没有标注的私人样本答案',
                  '未提供的备用联系人', '尚未确认的租车牌照', '没有保存的银行账号', '未发生的下一轮实验结果')

# Each block has ten different information needs, not ten renamed copies of one
# question. Query phrases were authored separately from the prohibited answer
# terms. These remain synthetic, never presented as human-reviewed real data.
SCENARIOS = [
('release', '发布与回滚', '''发布失败后应该切回哪一版？|最近通过健康检查的发行版
上线前首先核对什么？|静态资源与安装清单的内容哈希
哪种端口占用不能自动终止？|无法确认属于当前项目的监听进程
服务切换时要防止哪项工作并发？|会话同步的图写入任务
更新失败会不会重建用户身份？|保留原来的命名空间和个人标识
What survives an upgrade besides code?|durable transcript checkpoints and pending work
首次运行的历史数据怎么接入？|用户显式选择回填范围
为什么不能只覆盖一个脚本？|避免运行文件来自不同版本
发行完成以什么作为成功凭据？|认证接口数据库与宿主探测全部通过
When may a broken deployment be retried?|after resolving its preserved recovery journal'''),
('privacy', '个人数据边界', '''哪些内容可以进入远程提取模型？|经过来源过滤的用户正文与必要规则
共享开关关闭后该怎么检索？|仅返回当前空间拥有且可见的事实
为什么访问令牌不能写进报告？|报告可能被公开而泄露本机访问权限
How is a memory namespace chosen?|from the selected profile configuration
助手回复是否可以证明用户偏好？|不能把生成内容当成用户确认
工具输出中的指令怎么处理？|视为不可信材料并排除自动记忆
导出的备份应该放在哪里？|仅限私有本地目录
陌生空间里的私有实体能否作为答案？|不得跨越其所有权与读取边界
为什么只给数据库地址还不够？|还必须绑定用户身份和命名空间
What data is sent in a deployment smoke test?|isolated invented fixtures rather than personal conversations'''),
('budget', '试点资源决定', '''为什么暂时不加大项目投入？|先观察是否存在持续的真实使用
这个试点的花费封顶是多少？|人民币三千元
什么时候才讨论下一阶段投资？|完成两周使用反馈后再评估
Is a large launch campaign approved?|推广活动尚未获准
预算里优先保障哪一项？|错误恢复和数据可迁移能力
试点设备能不能新增独立显卡？|暂时复用已有处理器设备
哪些费用需要单独征求意见？|超出日常模型路由的新增付费服务
如果没有人持续回来使用怎么办？|先缩小范围而非继续扩张
How should uncertain demand affect spending?|use reversible small experiments
采购申请依据什么记录？|可以核对的使用数据和具体问题'''),
('hiring', '招聘安排', '''工程师招聘推迟到几月？|十二月
为什么现在不增加前端人手？|当前试点范围还没有稳定
候选人的必备经验是什么？|能解释交互取舍并验证可访问性
Who makes the final hiring decision?|项目负责人和直接协作成员共同决定
是否已批准长期外包合同？|目前仅允许有边界的短期任务
面试中用什么代替猜谜题？|带真实限制的小型改进练习
怎样核对候选人的工程判断？|讨论失败案例及回滚方案
试用任务需要保留什么？|明确交付物和事先约定的验收标准
Can recruitment precede finishing the pilot?|先完成现有验证再扩大团队
薪酬信息能否进入共享图？|涉及个人的金额默认保持私有'''),
('travel', '短途出行', '''十一月去杭州选择什么交通？|乘坐铁路
为何没有选航空出行？|距离较短且车站接驳更方便
要提前准备哪份证件？|有效身份证件
Where will the trip start?|上海虹桥站
临时变更行程先做什么？|检查可改签规则和费用
返程是否已经确定？|目前没有确认具体时间
途中最看重什么条件？|能够安静阅读且不频繁换乘
住宿能否自动下单？|需要本人确认后才能预订
Which information must not be inferred?|没有提供的同行人身份
应该如何记录待定安排？|标记计划状态并保留未定日期'''),
('meeting', '演示与会议', '''产品演示安排在什么城市？|上海
会议主要讨论哪两项能力？|记忆证据查看和错误更正
谁负责演示操作？|交互设计负责人
What must be available during the demo?|an isolated namespace with synthetic examples
如果投影效果不好怎么办？|切到放大后的单条事实详情
会后如何处理未解决问题？|登记负责人和下一次核对时间
演示能否使用个人聊天原文？|仅展示提前获准的脱敏材料
什么时候发送会议记录？|确认事实和决定后再分发
Which outcome is still a proposal?|尚未确认的跨团队集成
评审发生冲突时怎么做？|保留不同意见及其证据而非覆盖'''),
('coffee', '饮食选择', '''日常拿铁里想换成什么？|燕麦奶
甜度应该怎样安排？|不要另外加糖
What should be served late in the day?|a caffeine-free herbal drink
下午茶具体选哪种味道？|薄荷
是否可以据此推断乳制品过敏？|没有本人明确说明就不能推断疾病
出差时找不到常喝饮品怎么办？|先问可接受的替代品
热饮通常放在哪个时段？|上午工作开始之后
Can an assistant suggestion become a preference?|只有用户明确确认后才可保存
偏好突然变化怎么记录？|保留修订来源和当前版本
给别人点饮料能否套用我的习惯？|每个人的选择需要独立确认'''),
('writing', '沟通习惯', '''回答应先给出哪一部分？|直接说明结论
需要多长的正文？|先简洁表达再按需补充解释
What format is preferred for comparisons?|a small table with concrete tradeoffs
技术故障应怎样描述？|说明影响和下一步核查方法
为什么不把所有日志贴出来？|冗长输出会掩盖关键证据
不确定的信息该怎么表达？|标出假设与已核实内容的边界
收到不同意见时先做什么？|核对证据再修正判断
How should a follow-up be answered?|connect it to the existing task context
写文档时如何避免空泛表述？|使用具体动作和可观察结果
任务结束怎样报告？|交代实际结果验证方法与限制'''),
('retention', '历史与保留', '''普通归档是否删除正文？|内容仍保留但退出正常召回
谁可以恢复已经归档的记录？|经过授权的工作台显式操作
How are older values inspected?|through versioned revision history
源会话被撤回会发生什么？|停止使用其冻结提案并要求核对
为什么不能凭旧游标宣布已保存？|必须存在匹配的图事务回执
待审提案被拒绝后是否重写历史？|保留审计记录但不改变活跃事实
相同操作重放应该得到什么？|原来的耐久回执
记录过期是否会自动消失？|需要明确的生命周期操作
Which history is skipped on first installation?|the preexisting transcript boundary unless backfill is requested
如何避免历史文件泄漏？|不要把本地备份提交到版本仓库'''),
('backup', '备份与恢复', '''执行恢复前先停止哪类任务？|暂停同期图写入与同步
怎么确认备份没有损坏？|核对清单和内容摘要
Where should recovery be rehearsed?|a disposable database namespace
恢复过程中断后可以继续写吗？|先解决未完成恢复标记
完整备份需保留哪些审计对象？|修订历史提案回执和本体覆盖层
为什么不能反向删除已创建索引？|幂等结构创建不属于代码回滚
恢复个人图时能否覆盖其他空间？|只能处理明确授权的目标范围
再次导入同一份文件怎么处理？|识别重复身份并避免重复事实
What remains outside a graph backup?|private runtime credentials and unrelated profile files
恢复完成用什么核对？|对象数量版本和已知只读查询'''),
('inventory', '仓库同步', '''来源重复投递同一批如何处理？|按操作身份进行幂等确认
游标应该在什么时候前移？|图回执持久化并匹配以后
What is frozen before extraction?|an immutable bounded source snapshot
失败任务优先重算模型吗？|先复用已经暂存的原始提案
如何处理源消息被编辑？|拒绝跳过变化并保留待核对状态
并发进程怎么避免同时提交？|使用操作系统锁和快照租约
写入响应丢失如何恢复？|按相同摘要重放并找回原回执
单次处理数量为什么有限？|控制后台成本并保留其他会话进度
How are missed notifications recovered?|scan the authoritative message store
什么内容不应该成为数据源？|任意请求转储或未经选择的旧文件'''),
('accessibility', '可访问性交付', '''选中节点的文字最小可读要求是什么？|随缩放补偿到可辨认的屏幕字号
键盘用户怎样执行主要动作？|提供焦点顺序和有名称的快捷键入口
What does a clean axe result prove?|only the automated checks actually exercised
触屏能力该怎样验收？|在真实设备上测量手势与误触
风险颜色还应搭配什么？|明确的文字和图标说明
切换检查器时如何避免迷路？|保持当前焦点和对象上下文
长表单的发布按钮怎么安排？|在滚动区域保留可用操作栏
非相关节点是否可以不可读？|降低形状强调但保持文字对比度
Why test a screen reader separately?|automatic scanning cannot verify the whole spoken workflow
缩小窗口以后图应留在哪里？|保持世界中心并避免默认内容落在屏外'''),
('models', '本地向量服务', '''搜索时会自动联网下载模型吗？|下载只由显式准备操作触发
查询文本使用哪种角色标记？|以检索问题的编码约定处理
How are oversized documents handled?|tokenizer-aware overlapping chunks
用什么防止坏向量进入索引？|校验维度有限数值和非零范数
模型不可用时如何表现？|返回关键词降级并说明语义尚未就绪
更换模型后能直接复用旧索引吗？|必须隔离模型版本并重新生成投影
向量相似分数能否作为事实真伪概率？|相似性不能代替权威事实验证
为什么把推理放在控制台？|避免让每个代理进程加载重型依赖
What is measured separately from warm latency?|cold initialization and index throughput
选型需要哪些证据？|同一独立题集上的成对质量与成本比较'''),
('approval', '人工审阅', '''自动提案与人工更正冲突怎么处理？|把整批候选送入待审区
待审进入列表是否等于已经记住？|尚未成为活跃事实
What must approval check again?|current endpoint versions and effective ontology constraints
审批期间能不能偷偷改原证据？|保留原文并另记用户编辑
关系端点已被确认会有什么保护？|关联更新同样需要审阅
归档事实能否被自动同步复活？|必须先显式恢复其生命周期
为何要保存当时的别名映射？|防止后来的重新绑定改变旧提案含义
怎样拒绝重复审批请求？|核对原操作身份并返回幂等结果
If a required field was added later what can a reviewer do?|explicitly repair the draft then validate the final batch
批准失败应如何解释？|说明版本冲突并保留草稿供重新核对'''),
('exports', '数据导出', '''导出图片前需要解释什么范围？|当前可见画布还是全部图内容
SVG 转图片时受哪项限制？|部署页面的内容安全策略
How should private fields be exported?|only under an explicit authorized scope
为什么不把缩略图当完整备份？|图像不保留可恢复的结构事实
超出渲染预算如何提示？|显示截断数量并提供进一步过滤
导出文件需要哪些版本信息？|数据时间与本体投影版本
分享前应检查什么？|可见范围内是否包含敏感正文
关系箭头丢失会导致什么？|把方向不同的事实表达混淆
What is the source of exported labels?|the effective current definitions and entity values
旧命名视图包含删除类型时怎么办？|剔除无效筛选并给出明确提醒'''),
('mapping', '实体定位', '''同名的人物可以自动合并吗？|先保留为疑似重复供人工核对
已经确认的别名怎么查询？|解析到经过审阅的规范实体
What uniquely identifies a card?|a stable namespace-scoped URI
名字改变后会生成新身份吗？|展示名称更新但规范标识保留
一个对象可以有多个显示称呼吗？|使用经确认的别名集合
不同来源的描述冲突怎么保留？|记录各自依据并控制当前版本
哪些端点不能连接？|无法唯一确认或不可见的对象
为什么不能从标识符猜姓名？|技术编码不是用户身份的证据
How is relation direction established?|from the stored source predicate and target
旧别名记录应如何维护？|不直接当作可以更新的规范实体'''),
('time', '事件时间', '''只说昨天怎样确定日期？|结合原消息时间和配置时区
未知日期可以命中时间刷选吗？|不参与具体日期区间
What does a day-precision event cover?|the local calendar day interval
事件结束时间有什么约束？|必须晚于开始时间
为何不能使用抽取当天作为事发日？|记录时刻不是事件发生证据
只提供月份和日子怎样补年份？|使用对应来源消息的年份并记推断状态
计划和已经发生的活动应如何区分？|保存明确的发生状态
时间窗口结束点默认是否包含？|按排他的上界处理
What happens when quoted time disagrees with the payload?|reject the proposal instead of guessing
日期改正后旧向量能直接返回吗？|必须通过当前修订与更新时间核验'''),
('schema', '定义生命周期', '''新建类型的规范键可以改名吗？|稳定键不可变更只修改展示说明
内置定义可以永久删除吗？|只开放契约允许的受控动作
When may a custom definition be removed?|when all reference counts are known and zero
为什么统计失败不能继续删除？|未知影响必须按高风险阻止
停用是否清空历史图？|保留已有事实但拒绝新的使用
新属性如何限制适用对象？|通过域和值类型以及必填范围
结构发布前要做什么？|按期望版本预览实际数据影响
如何撤回自己的收窄规则？|恢复到内置最低约束允许的范围
What is the scope of customization?|the active namespace overlay
另一空间的私有定义可以借用吗？|不能跨越其命名空间边界'''),
('operations', '服务日常运行', '''登录自启项升级后指向哪里？|独立安装的运行环境和服务入口
已经运行同一版还要启动第二个进程吗？|只执行认证健康探测
What is required after changing source code?|run the deployment command again
修改普通事实需要重启代理吗？|下一次实际检索读取当前版本
短寒暄是否每次都会注入记忆？|没有语义信号时跳过召回
关闭网页会停止后台服务吗？|浏览器页面不是进程所有者
模型缓存移动前要做什么？|更新稳定路径并重新核对语义就绪
未知进程的端口被占用怎么办？|拒绝盲杀并说明冲突
What must survive a repeat deployment?|existing credentials identity and pending sessions
为什么需要保存发行清单？|核对真正安装的内容而非假定源码提交'''),
('evaluation', '检索实验', '''对比两个模型怎样避免偷看测试集？|先冻结开发选择再运行独立盲测
相同事实的改写应该怎样分组？|整组进入同一数据划分
Which metric detects missing candidates?|recall at a declared retrieval budget
哪类指标评价前几位顺序？|带等级相关性的排序折损得分
为什么零泄漏还不能说绝对安全？|有限样本不能证明所有未来情况
图扩展收益如何隔离？|固定模型表示后做有无该通道的消融
近似索引应和什么比较？|相同真实向量的精确相似度排序
置信区间为什么按来源抽样？|改写之间不是独立观测
What is wrong with reusing one vector for every fact?|it measures throughput rather than semantic quality
没有人工复核题目可以发布选型结论吗？|正式提升必须保持阻断状态'''),
]


def _query(key, family, split, track, question, gold, **extra):
    return {'id': key, 'source_family': family, 'split': split, 'track': track,
            'question': question, 'gold': gold, 'provenance': 'authored_synthetic', **extra}


def prepare_fixture(*, real_snapshot=None):
    dataset = {'namespace': NAMESPACE, 'entities': [], 'relations': [], 'schema': {'namespace': NAMESPACE,
               'version': 0, 'entity_types': [], 'properties': [], 'relations': []}}
    queries, aliases = [], {}
    for index, (key, title, rows) in enumerate(SCENARIOS):
        family, split = 'synthetic:' + key, 'dev' if index < 6 else 'blind'
        anchor = NAMESPACE + 'concept-' + key
        dataset['entities'].append({'uri': anchor, 'type': 'concept', 'name': title, 'props': {}, 'source_family': family})
        pairs = [line.split('|', 1) for line in rows.splitlines() if line.strip()]
        if len(pairs) != 10:
            raise ValueError('Each authored source family needs exactly ten questions')
        for n, (question, answer) in enumerate(pairs):
            uri = anchor + '-' + str(n + 1)
            dataset['entities'].append({'uri': uri, 'type': 'concept', 'name': title + '记录' + str(n + 1),
                                       'props': {'description': answer}, 'source_family': family})
            dataset['relations'].append({'from': anchor, 'to': uri, 'type': 'RELATED_TO', 'props': {'description': answer}})
            aliases[relation_key(anchor, 'RELATED_TO', uri)] = uri
            gold = {'answerable': True, 'relevance': {uri: 2}, 'answer_terms': [answer], 'sufficient_sets': [[uri]]}
            task_type = 'semantic'
            if n >= 8:
                question = f'关于{title}，{NOANSWER_TOPICS[index * 2 + n - 8]}是什么？'
                gold = {'answerable': False, 'relevance': {}, 'answer_terms': [], 'sufficient_sets': []}
                task_type = 'noanswer'
            elif n == 6:
                other = anchor + '-1'
                question = pairs[0][0] + '同时，' + question
                gold.update(relevance={other: 2, uri: 2}, sufficient_sets=[[other, uri]],
                            answer_terms=[pairs[0][1], answer])
                task_type = 'multi_evidence'
            elif n == 7:
                question = f'{title}主题目前保存了多少条具体记录？需要完整核对各条来源。'
                evidence = [anchor + '-' + str(j + 1) for j in range(10)]
                gold.update(relevance={key: 2 for key in evidence}, sufficient_sets=[evidence], answer_terms=[], expected_answer=10)
                task_type = 'aggregate'
            queries.append(_query(f'syn-{index + 1:02}-{n + 1:02}', family, split, 'facts', question, gold,
                                  task_type=task_type))
    # Separate definition track: it never contributes to fact-retrieval metrics.
    for n, (_, title, rows) in enumerate(SCENARIOS):
        key = 'x_research_' + str(n)
        definition = {'key': key, 'label': title + '定义', 'description': rows.splitlines()[0].split('|')[1],
                      'domain': ['concept'], 'range': ['string'], 'active': True, 'managed': False}
        dataset['schema']['properties'].append(definition)
        canonical = 'ontology:property:' + key
        questions = [f'哪项属性表达{title}的规则？', f'查找关于{title}的字段含义', f'What field documents topic {n + 1} ({title})?']
        # Keep complete definition families in one split while meeting the
        # declared 20-development / 40-blind ontology-question allocation.
        if n == 6:
            questions = questions[:2]
        elif n == 7:
            questions.append(f'如果要确认{title}的字段语义，应检索哪个定义？')
        for j, question in enumerate(questions):
            queries.append(_query(f'ont-{n + 1:02}-{j + 1}', 'ontology:' + key, 'dev' if n < 7 else 'blind', 'ontology',
                                  question, {'answerable': True, 'relevance': {canonical: 2}, 'answer_terms': [key]}))
    real = _real_slots(real_snapshot)
    dataset['real_schema'] = deepcopy(real.get('schema'))
    dataset['entities'].extend(real['entities'])
    dataset['relations'].extend(real['relations'])
    queries.extend(real['queries'])
    safety_categories = ('foreign_private', 'archived', 'disabled', 'legacy_alias', 'legacy_unresolved',
                         'visibility_changed', 'stale_revision', 'missing_source', 'shared_visible',
                         'current_revision', 'inactive_definition_history', 'namespace_boundary')
    for category in safety_categories:
        for n in range(10):
            uri = NAMESPACE + f'concept-safety-{category}-{n}'
            blocked = category not in {'shared_visible', 'current_revision', 'inactive_definition_history'}
            item = {'uri': uri, 'type': 'concept', 'name': f'安全夹具 {category} {n}',
                    'props': {'description': '仅用于离线研究边界核验', 'hermes_revision': 2}, 'source_family': 'safety:' + category}
            if category in {'foreign_private', 'namespace_boundary'}:
                item['uri'] = uri = uri.replace(NAMESPACE, 'urn:kg-research-foreign:kg:')
            if category in {'archived', 'legacy_alias', 'legacy_unresolved'}:
                item['labels'] = ['Concept', {'archived': 'KGQuarantined', 'legacy_alias': 'KGLegacyAlias', 'legacy_unresolved': 'KGLegacyUnresolved'}[category]]
            if category == 'disabled': item['props']['hermes_enabled'] = False
            if category == 'visibility_changed': item['research_visible'] = False
            if category == 'stale_revision': item['research_expected_revision'] = 3
            if category == 'missing_source': item['research_source_missing'] = True
            if category == 'shared_visible': item['props']['shared'] = True
            dataset['entities'].append(item)
            queries.append(_query(f'safe-{category}-{n:02}', 'safety:' + category + ':' + str(n),
                                  'dev' if n < 3 else 'blind', 'safety', f'核对安全夹具 {category} {n}',
                                  {'answerable': not blocked, 'relevance': {} if blocked else {uri: 2}, 'answer_terms': []},
                                  forbidden_sources=[uri] if blocked else [], expected_allowed=[uri] if not blocked else [],
                                  scenario=category))
    fixture = {'format': FORMAT, 'version': 1, 'dataset_sha256': digest(dataset), 'queries': queries,
               'gold_aliases': aliases, 'legacy50_policy': 'regression_only_never_selection',
               'real_source_manifest': real['manifest'], 'seed': 8128,
               'notes': ['Synthetic questions are authored diagnostics, not reviewed real-user gold.',
                         'Snapshot safety assertions do not replace installed API/write-path security tests.']}
    assert len([q for q in queries if q['track'] == 'facts']) == 300
    return dataset, fixture, {'format': FORMAT, 'dataset_sha256': digest(dataset), 'entries': real['labels']}


def _real_slots(snapshot):
    entities, relations, manifest = [], [], {'authorized': False, 'imported_families': 0}
    groups = {}
    if snapshot is not None:
        if snapshot.get('authorization') != 'local_authorized_snapshot' and snapshot.get('format') != 'kg-retrieval-snapshot-v1':
            raise ValueError('Real import requires explicit local_authorized_snapshot authorization')
        source = snapshot.get('dataset', snapshot)
        if not isinstance(source.get('entities'), list) or not source.get('namespace'):
            raise ValueError('Authorized snapshot must contain a namespace and entities')
        rejected = 0
        for entity in source['entities']:
            props = entity.get('properties', entity.get('props', {}))
            if any(str(key).lower() in {'password', 'token', 'api_key', 'authorization', 'secret'} for key in props):
                raise ValueError('Credential-like fields are not permitted in research snapshots')
            uri = entity.get('uri', props.get('uri'))
            probe_text = ' '.join(str(v) for v in [uri, entity.get('name'), props.get('hermes_name'), props.get('foaf_name'), props.get('hermes_description')] if v)
            if re.search(r'synthetic|fixture|smoke|probe|acceptance|placeholder|\btest\b|测试|合成|占位|连通性', probe_text, re.I):
                rejected += 1
                continue
            family = entity.get('source_family') or props.get('hermes_sourceSession') or 'unattributed-snapshot'
            if not uri:
                raise ValueError('Real entities need stable source URIs')
            groups.setdefault(str(family), []).append(deepcopy(entity))
        selected = sorted(groups, key=lambda family: digest(family))
        for family in selected: entities.extend(groups[family])
        uris = {e.get('uri', e.get('properties', {}).get('uri')) for e in entities}
        relations = [deepcopy(r) for r in source.get('relations', []) if r.get('from') in uris and r.get('to') in uris]
        manifest = {'authorized': True, 'snapshot_sha256': digest(snapshot), 'namespace': source['namespace'],
                    'imported_families': len(selected), 'excluded_test_or_placeholder_entities': rejected,
                    'personal_data': True, 'human_reviewed': False}
    selected = sorted(groups, key=lambda family: digest(family))
    assignment = {'dev': [], 'blind': []}
    property_labels = {'hermes_description': '事实说明', 'description': '事实说明', 'hermes_note': '补充说明', 'note': '补充说明',
                       'hermes_preferenceKey': '偏好维度', 'hermes_preferenceValue': '偏好取值', 'hermes_decisionText': '决定内容',
                       'hermes_decisionContext': '决定理由', 'hermes_semanticScope': '含义范围', 'hermes_eventTime': '发生时间',
                       'hermes_eventEndTime': '结束时间', 'geo_lat': '纬度', 'geo_long': '经度'}
    candidates = {}
    for family in selected:
        bank = []
        for entity in groups[family]:
            props = entity.get('properties', entity.get('props', {}))
            uri = entity.get('uri', props.get('uri'))
            name = entity.get('name') or props.get('hermes_name') or props.get('foaf_name') or uri
            fields = [key for key, value in props.items() if value is not None and value != '' and (key in property_labels or key.startswith('x_'))]
            for key in sorted(fields):
                question = f'关于“{name}”，来源明确记录的{property_labels.get(key, key)}是什么？'
                bank.append((family, entity, question, [uri], 'semantic', key))
            bank.append((family, entity, f'稳定标识 {uri} 对应对象的显示名是什么？', [uri], 'exact', 'identity'))
            outgoing = [r for r in relations if r['from'] == uri]
            for predicate in sorted({r['type'] for r in outgoing}):
                refs = [relation_key(r['from'], r['type'], r['to']) for r in outgoing if r['type'] == predicate]
                question = f'从“{name}”沿有方向的 {predicate} 关系可以指向哪些对象？'
                bank.append((family, entity, question, [uri, *refs], 'relation', predicate))
        candidates[family] = bank
    # Never split one source family between dev and blind. Incomplete slots are
    # honest when the snapshot cannot support the requested independent groups.
    for family in selected:
        capacity = {split: maximum - len(assignment[split]) for split, maximum in (('dev', 40), ('blind', 60))}
        split = 'dev' if capacity['dev'] and family != 'unattributed-snapshot' else 'blind'
        assignment[split].extend(candidates[family][:capacity[split]])
    queries, labels = [], []
    for n in range(100):
        split = 'dev' if n < 40 else 'blind'
        offset = n if split == 'dev' else n - 40
        available = offset < len(assignment[split])
        chosen = assignment[split][offset] if available else None
        family = 'real:' + chosen[0] if chosen else f'real:unassigned-{n + 1:03}'
        refs = chosen[3] if chosen else []
        source_hash = digest(chosen[1]) if chosen else None
        key = f'real-{n + 1:03}'
        queries.append({'id': key, 'source_family': family, 'split': 'dev' if n < 40 else 'blind',
                        'track': 'facts', 'question': '', 'gold': None, 'provenance': 'real_pending_human',
                        'namespace': manifest.get('namespace'),
                        'draft_question': chosen[2] if chosen else '',
                        'draft_task_type': chosen[4] if chosen else 'semantic',
                        'draft_information_need': chosen[5] if chosen else None,
                        'source_refs': refs, 'source_fingerprint': source_hash})
        labels.append({'id': key, 'question': '', 'relevance': {}, 'answerable': None,
                       'source_refs': refs, 'source_family': family,
                       'answer_terms': [], 'sufficient_sets': [], 'status': 'pending', 'review_origin': None,
                       'reviewer': '', 'reviewed_at': '', 'attestation': '', 'source_fingerprint': source_hash})
    return {'entities': entities, 'relations': relations, 'queries': queries, 'labels': labels, 'manifest': manifest,
            'schema': source.get('schema') if snapshot is not None else None}


def validate_fixture(fixture, dataset):
    check_format(fixture)
    if fixture.get('dataset_sha256') != digest(dataset):
        raise ValueError('Fixture is bound to another snapshot')
    ids, families = set(), {}
    for query in fixture['queries']:
        if query['id'] in ids: raise ValueError('Duplicate query ID')
        ids.add(query['id'])
        if query['split'] not in {'dev', 'blind', 'regression'}: raise ValueError('Unknown split')
        previous = families.setdefault(query['source_family'], query['split'])
        if previous != query['split']: raise ValueError('Source family leaks across splits')
        if query.get('legacy50') and query['split'] != 'regression': raise ValueError('Legacy50 is regression only')
        gold = query.get('gold')
        if gold:
            for term in gold.get('answer_terms', []):
                if term and term.casefold() in query['question'].casefold():
                    raise ValueError('Answer text copied into query: ' + query['id'])
    return True


def validate_labels(fixture, dataset, labels):
    validate_fixture(fixture, dataset)
    check_format(labels)
    if labels.get('dataset_sha256') != digest(dataset): raise ValueError('Labels belong to another snapshot')
    real = {q['id']: q for q in fixture['queries'] if q.get('provenance') == 'real_pending_human'}
    entries = labels.get('entries', [])
    by_id = {row['id']: row for row in entries}
    if len(by_id) != len(entries) or set(by_id) != set(real): raise ValueError('Manual label IDs must match all 100 real slots exactly')
    known = {e.get('uri', e.get('properties', {}).get('uri')) for e in dataset['entities']}
    known |= {relation_key(r['from'], r['type'], r['to']) for r in dataset['relations']}
    entity_families = {e.get('uri', e.get('properties', {}).get('uri')):
                       'real:' + str(e.get('source_family') or e.get('properties', e.get('props', {})).get('hermes_sourceSession') or 'unattributed-snapshot')
                       for e in dataset['entities']}
    ref_families = dict(entity_families)
    for relation in dataset['relations']:
        ref_families[relation_key(relation['from'], relation['type'], relation['to'])] = entity_families.get(relation['from'])
    valid_families = {query['source_family'] for query in real.values() if not query['source_family'].startswith('real:unassigned-')}
    family_splits, evidence_splits = {}, {}
    snapshot_hash = digest(dataset)
    reviewed, issues = 0, []
    for key, query in real.items():
        row = by_id[key]
        reasons = []
        refs = row.get('source_refs', query.get('source_refs', []))
        family = row.get('source_family', query['source_family'])
        if (not isinstance(refs, list) or not refs or any(ref not in known for ref in refs)
                or row.get('source_fingerprint') not in {query.get('source_fingerprint'), snapshot_hash}):
            reasons.append('missing_or_changed_source')
        if family not in valid_families or (refs and family not in {ref_families.get(ref) for ref in refs}): reasons.append('unverified_source_family')
        if family in valid_families:
            previous = family_splits.setdefault(family, query['split'])
            if previous != query['split']: reasons.append('source_family_split_leakage')
        if row.get('status') != 'reviewed' or row.get('review_origin') != 'human': reasons.append('not_human_reviewed')
        if not row.get('reviewer', '').strip() or row.get('attestation') != ATTESTATION: reasons.append('missing_attestation')
        try: datetime.fromisoformat(row.get('reviewed_at', '').replace('Z', '+00:00'))
        except (ValueError, TypeError): reasons.append('invalid_review_time')
        if not isinstance(row.get('question'), str) or not row['question'].strip(): reasons.append('missing_question')
        if not isinstance(row.get('answerable'), bool): reasons.append('missing_answerability')
        relevance = row.get('relevance', {})
        if not isinstance(relevance, dict):
            reasons.append('invalid_relevance')
            relevance = {}
        elif any(k not in known or type(v) is not int or v not in {0, 1, 2} for k, v in relevance.items()): reasons.append('invalid_relevance')
        if row.get('answerable') is True and not any(v == 2 for v in relevance.values()): reasons.append('missing_sufficient_evidence')
        sets = row.get('sufficient_sets')
        if row.get('answerable') is True and (not isinstance(sets, list) or not sets or any(not isinstance(s, list) or not s or any(relevance.get(key, 0) < 2 for key in s) for s in sets)):
            reasons.append('invalid_sufficient_sets')
        if not isinstance(row.get('answer_terms'), list) or any(not isinstance(term, str) for term in row.get('answer_terms', [])):
            reasons.append('invalid_answer_terms')
        if row.get('answerable') is False and any(relevance.values()): reasons.append('noanswer_has_relevant_evidence')
        if isinstance(relevance, dict):
            for ref, grade in relevance.items():
                if grade:
                    previous = evidence_splits.setdefault(ref, query['split'])
                    if previous != query['split']: reasons.append('gold_evidence_split_leakage')
        if isinstance(row.get('answer_terms'), list) and any(isinstance(term, str) and term and term.casefold() in row.get('question', '').casefold() for term in row.get('answer_terms', [])): reasons.append('answer_leakage')
        if reasons: issues.append({'id': key, 'reasons': reasons})
        else: reviewed += 1
    return {'ok': not issues and reviewed == 100, 'reviewed': reviewed, 'required': 100, 'issues': issues,
            'dataset_sha256': digest(dataset), 'fixture_sha256': digest(fixture), 'labels_sha256': digest(labels)}


def apply_labels(fixture, dataset, labels=None, lock=None):
    result = deepcopy(fixture)
    if labels is None: return result, {'ok': False, 'reviewed': 0, 'required': 100, 'reason': 'human_gold_not_locked'}
    validation = validate_labels(fixture, dataset, labels)
    locked = bool(lock and lock.get('validation') == validation and lock.get('format') == FORMAT and validation['ok'])
    if not locked: return result, {**validation, 'ok': False, 'reason': 'human_gold_not_locked'}
    by_id = {row['id']: row for row in labels['entries']}
    for query in result['queries']:
        if query['id'] not in by_id: continue
        row = by_id[query['id']]
        query.update(question=row['question'], gold={k: row[k] for k in ('relevance', 'answerable', 'answer_terms', 'sufficient_sets')},
                     provenance='real_human_locked', task_type=row.get('task_type', 'semantic'),
                     source_family=row.get('source_family', query['source_family']),
                     source_refs=row.get('source_refs', query['source_refs']))
    return result, validation


def write_prepared(output, *, real_snapshot=None):
    output = Path(output)
    if output.exists() and any(output.iterdir()): raise ValueError('Preparation output must be empty; preserve existing labels')
    dataset, fixture, labels = prepare_fixture(real_snapshot=real_snapshot)
    validate_fixture(fixture, dataset)
    for name, value in (('dataset.json', dataset), ('fixture.json', fixture), ('labels-template.json', labels)):
        write_json(output / name, value, replace=False)
    return {'ok': True, 'format': FORMAT, 'queries': 480, 'synthetic_facts': 200, 'real_slots': 100,
            'ontology': 60, 'safety': 120, 'human_reviewed': 0, 'promotion': 'blocked',
            'dataset_sha256': digest(dataset), 'fixture_sha256': digest(fixture)}
