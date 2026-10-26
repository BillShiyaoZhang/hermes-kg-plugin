#!/usr/bin/env python3
"""Frozen, synthetic typed-graph DEVELOPMENT diagnostics, outside formal gold.

Prepare first, then evaluate cached real models; no model downloads or database
access. Entity hits, visible predicate evidence and unexecuted structural tasks
are separate. This script never measures generated-answer correctness or grants
promotion. Queries/gold must not be edited after seeing model results.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins'))

FORMAT = 'kg-graph-semantics-dev-v1'
FIXTURE_REVISION = 'typed-graph-diagnostic-54-v2'
NS = 'urn:kg-semantic-dev:kg:'
FOREIGN = 'urn:kg-semantic-foreign:kg:'
REPRESENTATIONS = ('values', 'entity', 'entity_relations')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n')


def uri(slug):
    return NS + slug


def build_fixture():
    """All 54 questions/gold are authored here, before any embedding is loaded.

    v2 adds six unanchored attribute-lookup controls before the first model run.
    The prepared, never-evaluated 48-question v1 artifact is retained separately.
    """
    types = [
        {'key': 'person', 'label': '人物', 'storage_label': 'Person'},
        {'key': 'event', 'label': '事件', 'storage_label': 'Event'},
        {'key': 'x_project', 'label': '项目', 'storage_label': 'KGCustom_x_project'},
    ]
    definitions = [
        ('foaf_name', '姓名', ['string'], ['person'], []),
        ('hermes_name', '名称', ['string'], ['event', 'x_project'], []),
        ('hermes_aliasNames', '别名', ['string'], ['any'], ['alias_names']),
        ('hermes_eventTime', '开始时间', ['dateTime'], ['event'], ['event_time']),
        ('hermes_timePrecision', '时间精度', ['string'], ['event'], ['time_precision']),
        ('hermes_eventStatus', '事件状态', ['string'], ['event'], ['event_status']),
        ('x_score', '验收得分', ['integer'], ['x_project'], ['rating']),
        ('x_priority', '优先级', ['integer'], ['x_project'], ['priority']),
        ('x_budget', '预算额度', ['integer'], ['x_project'], ['budget']),
        ('x_round', '评审轮次', ['integer'], ['event'], ['round_number']),
        ('x_phase', '责任阶段', ['string'], ['any'], ['phase']),
    ]
    schema = {'namespace': NS, 'version': 1, 'entity_types': types, 'properties': [], 'relations': []}
    for key, label, ranges, domain, aliases in definitions:
        schema['properties'].append({'key': key, 'label': label, 'range': ranges, 'domain': domain,
            'aliases': aliases, 'required_for': [], 'managed': False, 'active': True,
            'iri': 'urn:graph-diagnostic:ontology:' + key,
            'description': label + '按原文记录；数值没有额外声明单位。'})
    for key, label, domain, ranges, aliases in (
        ('X_LEADS', '负责', ['person'], ['x_project'], ['leads_project']),
        ('X_DEPENDS_ON', '依赖', ['x_project'], ['x_project'], ['depends_on']),
        ('HERMES_participant', '参与', ['person'], ['event'], ['PARTICIPATED_IN']),
        ('RELATED_TO', '有关', ['any'], ['any'], []),
    ):
        schema['relations'].append({'key': key, 'label': label, 'domain': domain, 'range': ranges,
            'aliases': aliases, 'storage_type': key, 'active': True,
            'iri': 'urn:graph-diagnostic:ontology:' + key,
            'description': '有向规则；允许这种关系不代表已存在任何关系事实。'})
    for item in types:
        item.update(active=True, iri='urn:graph-diagnostic:ontology:' + item['key'])
    entities = []
    def entity(slug, kind, name, family, **props):
        entities.append({'uri': uri(slug), 'type': kind, 'name': name,
                         'source_family': family, 'props': props})
    entity('person-lin', 'person', '林舟', 'directory', alias_names=['River', 'Lin Zhou'])
    entity('person-chen', 'person', '陈岚', 'directory', alias_names=['Lan', 'Chen Lan'])
    entity('person-wu', 'person', '吴越', 'directory', alias_names=['Wade'])
    entity('person-other-lin', 'person', '林舟', 'other-directory', alias_names=['Lin Fang'])
    entity('x_project-orion', 'x_project', '曙光', 'review-signals', alias_names=['Orion', 'Morning Star'],
           x_score=4, x_priority=1, x_budget=4)
    entity('x_project-atlas', 'x_project', '星桥', 'review-signals', alias_names=['Atlas'],
           x_score=1, x_priority=4, x_budget=8)
    entity('x_project-iris', 'x_project', '鸢尾', 'review-signals', alias_names=['Iris'],
           x_score=4, x_priority=4, x_budget=1)
    entity('x_project-stone', 'x_project', '岩石', 'project-register', alias_names=['Stone'],
           x_score=0, x_priority=0, x_budget=0)
    entity('event-review', 'event', '曙光评审', 'calendar', event_time='2026-10-20T09:00:00+08:00',
           time_precision='datetime', event_status='occurred', x_round=2)
    entity('event-launch', 'event', '星桥发布', 'calendar', event_time='2026-11-02T14:00:00+08:00',
           time_precision='datetime', event_status='planned')
    entity('event-interview', 'event', '早期访谈', 'oral-history', time_precision='unknown', event_status='occurred')
    entity('event-atlas-review', 'event', '星桥评审', 'calendar', event_time='2026-10-22T10:00:00+08:00',
           time_precision='datetime', event_status='occurred', x_round=4)
    entity('x_project-archived', 'x_project', '归档旧项目', 'scope-fixtures', x_score=4)
    entities[-1]['archived'] = True
    entity('x_project-disabled', 'x_project', '停用记录项目', 'scope-fixtures', x_priority=4)
    entities[-1]['disabled'] = True
    for visibility in ('private', 'shared'):
        entities.append({'uri': FOREIGN + 'x_project-' + visibility, 'type': 'x_project',
                         'name': '外部' + visibility + '项目', 'source_family': 'foreign-fixtures',
                         'props': {'x_score': 4, 'shared': visibility == 'shared'}})
    relations = []
    def relation(source, target, kind, family, **props):
        relations.append({'from': uri(source), 'to': uri(target), 'type': kind,
                          'props': {'hermes_sourceSession': family, **props}})
    relation('person-lin', 'x_project-orion', 'X_LEADS', 'assignment-design', x_phase='设计')
    relation('person-lin', 'x_project-orion', 'X_LEADS', 'assignment-release', x_phase='发布')
    relation('person-lin', 'x_project-orion', 'X_LEADS', 'assignment-design', x_phase='设计')
    relation('person-chen', 'x_project-atlas', 'X_LEADS', 'assignments')
    relation('x_project-orion', 'x_project-atlas', 'X_DEPENDS_ON', 'dependency-register')
    relation('x_project-iris', 'x_project-orion', 'X_DEPENDS_ON', 'dependency-register')
    relation('person-lin', 'person-lin', 'RELATED_TO', 'self-assertion')
    relation('person-lin', 'event-review', 'HERMES_participant', 'attendance')
    relation('person-chen', 'event-atlas-review', 'HERMES_participant', 'attendance')
    relations.append({'from': uri('person-lin'), 'to': FOREIGN + 'x_project-private', 'type': 'X_LEADS'})
    dataset = {'namespace': NS, 'schema': schema, 'entities': entities, 'relations': relations}
    questions = []
    def q(family, text, task, targets=(), properties=(), edges=(), rules=(), structured_gold=None,
          forbidden=(), iri_anchor=None):
        questions.append({'id': f'{family}-{sum(row["family"] == family for row in questions) + 1:02}',
            'family': family, 'source_family': family, 'question': text, 'task': task,
            'track': 'schema' if rules else 'facts', 'entity_targets': [uri(value) for value in targets],
            'property_atoms': list(properties), 'relation_atoms': list(edges), 'rule_targets': list(rules),
            'structured_gold': structured_gold, 'forbidden_sources': list(forbidden),
            'iri_anchor': iri_anchor, 'answer_correctness_executed': False})
    def prop(slug, key, value):
        return {'uri': uri(slug), 'key': key, 'value': value}
    def edge(source, target, kind, **qualifiers):
        return {'from': uri(source), 'to': uri(target), 'type': kind, 'properties': qualifiers}
    q('identity', 'River 是哪位同事的别名？', 'entity', ['person-lin'])
    q('identity', 'Lan 的正式姓名是什么？', 'entity', ['person-chen'])
    q('identity', 'Morning Star 对应哪个项目？', 'entity', ['x_project-orion'])
    q('identity', '这个标识对应哪项记录：' + uri('x_project-atlas'), 'entity', ['x_project-atlas'], iri_anchor=uri('x_project-atlas'))
    q('identity', 'Wade 指的是谁？', 'entity', ['person-wu'])
    q('identity', '名叫林舟的两项记录能否直接当成同一个人？', 'structured',
      ['person-lin', 'person-other-lin'], structured_gold={'distinct_records': 2, 'automatic_merge': False})
    for text, slug, fields in (
        ('曙光项目的验收得分是多少？', 'x_project-orion', [('x_score', 4)]),
        ('Orion 项目的优先级是多少？', 'x_project-orion', [('x_priority', 1)]),
        ('曙光记录的预算额度是多少？不要给它添加未声明单位。', 'x_project-orion', [('x_budget', 4)]),
        ('Atlas 的验收得分是多少？', 'x_project-atlas', [('x_score', 1)]),
        ('星桥项目的优先级是多少？', 'x_project-atlas', [('x_priority', 4)]),
        ('Iris 的得分和优先级分别是多少？', 'x_project-iris', [('x_score', 4), ('x_priority', 4)]),
    ):
        q('numeric', text, 'property', [slug], properties=[prop(slug, key, value) for key, value in fields])
    # No entity names/aliases occur in these questions. Conjunctions are
    # evidence-retrieval probes, not a claim that vector search executes filters.
    for text, slug, fields in (
        ('验收得分为4且优先级为1的是哪个项目？', 'x_project-orion', [('x_score', 4), ('x_priority', 1)]),
        ('优先级为4但验收得分为1的是哪项记录？', 'x_project-atlas', [('x_priority', 4), ('x_score', 1)]),
        ('哪个项目的验收得分和优先级都为4？', 'x_project-iris', [('x_score', 4), ('x_priority', 4)]),
        ('登记预算额度为8且验收得分为1的是哪个项目？', 'x_project-atlas', [('x_budget', 8), ('x_score', 1)]),
        ('验收得分、优先级和预算额度都为0的是哪个项目？', 'x_project-stone', [('x_score', 0), ('x_priority', 0), ('x_budget', 0)]),
        ('哪条事件记录中的数字4表示评审轮次，而不是项目得分？', 'event-atlas-review', [('x_round', 4)]),
    ):
        q('attribute_lookup', text, 'property_lookup', [slug],
          properties=[prop(slug, key, value) for key, value in fields])
    q('temporal', '曙光评审的准确开始时间是什么？', 'property', ['event-review'],
      properties=[prop('event-review', 'hermes_eventTime', '2026-10-20T09:00:00+08:00')])
    q('temporal', '早期访谈的时间是否能精确到某一天？', 'property', ['event-interview'],
      properties=[prop('event-interview', 'hermes_timePrecision', 'unknown')])
    q('temporal', '星桥发布已经发生了，还是仍处于计划状态？', 'property', ['event-launch'],
      properties=[prop('event-launch', 'hermes_eventStatus', 'planned')])
    q('temporal', '星桥评审进行到了第几轮？', 'property', ['event-atlas-review'],
      properties=[prop('event-atlas-review', 'x_round', 4)])
    q('temporal', '在十月二十日当天发生的是哪次评审？', 'structured', ['event-review'],
      structured_gold={'known_time_range': ['2026-10-20', '2026-10-21'], 'uris': [uri('event-review')]})
    q('temporal', '十月二十日至二十三日之间有几次已发生的评审？', 'structured',
      ['event-review', 'event-atlas-review'], structured_gold={'count': 2})
    q('direction', '谁负责曙光项目？', 'relation', ['person-lin', 'x_project-orion'],
      edges=[edge('person-lin', 'x_project-orion', 'X_LEADS')])
    q('direction', '陈岚负责的是哪个项目？', 'relation', ['person-chen', 'x_project-atlas'],
      edges=[edge('person-chen', 'x_project-atlas', 'X_LEADS')])
    q('direction', 'Orion 依赖哪一个项目？', 'relation', ['x_project-orion', 'x_project-atlas'],
      edges=[edge('x_project-orion', 'x_project-atlas', 'X_DEPENDS_ON')])
    q('direction', '哪个项目依赖曙光？', 'relation', ['x_project-iris', 'x_project-orion'],
      edges=[edge('x_project-iris', 'x_project-orion', 'X_DEPENDS_ON')])
    q('direction', '星桥依赖曙光，这个方向的关系存在吗？', 'structured', ['x_project-atlas', 'x_project-orion'],
      structured_gold={'relation_exists': False, 'reverse_exists': True})
    q('direction', '吴越负责曙光吗？允许人物负责项目是否足以证明这件事？', 'structured',
      ['person-wu', 'x_project-orion'], structured_gold={'relation_exists': False, 'rule_is_fact': False})
    q('qualifiers', '林舟负责曙光的哪两个阶段？', 'relation', ['person-lin', 'x_project-orion'],
      edges=[edge('person-lin', 'x_project-orion', 'X_LEADS', x_phase='设计'),
             edge('person-lin', 'x_project-orion', 'X_LEADS', x_phase='发布')])
    q('qualifiers', '曙光的设计阶段由谁负责？', 'relation', ['person-lin', 'x_project-orion'],
      edges=[edge('person-lin', 'x_project-orion', 'X_LEADS', x_phase='设计')])
    q('qualifiers', '曙光的发布阶段由谁负责？', 'relation', ['person-lin', 'x_project-orion'],
      edges=[edge('person-lin', 'x_project-orion', 'X_LEADS', x_phase='发布')])
    q('qualifiers', '林舟是否存在指向自己的有关关系？', 'relation', ['person-lin'],
      edges=[edge('person-lin', 'person-lin', 'RELATED_TO')])
    q('qualifiers', '林舟负责曙光的原始物理边记录有几条，去重后阶段又有几种？', 'structured',
      ['person-lin', 'x_project-orion'], structured_gold={'physical_edges': 3, 'distinct_phases': 2})
    q('qualifiers', '多条负责阶段记录是否说明曙光有两个不同负责人？', 'structured',
      ['person-lin', 'x_project-orion'], structured_gold={'distinct_owners': 1})
    for text, collection, key in (
        ('验收得分字段是什么值类型？', 'properties', 'x_score'),
        ('rating 是哪个规范属性的别名？', 'properties', 'x_score'),
        ('urn:graph-diagnostic:ontology:X_LEADS 这个定义的方向是什么？', 'relations', 'X_LEADS'),
        ('人物可以负责项目，这条规则叫什么？', 'relations', 'X_LEADS'),
        ('哪个自定义实体类型表示项目？', 'entity_types', 'x_project'),
        ('负责关系允许人物指向项目，能否据此推断吴越负责鸢尾？', 'relations', 'X_LEADS'),
    ):
        q('schema', text, 'rule', rules=[{'collection': collection, 'key': key}],
          iri_anchor='urn:graph-diagnostic:ontology:X_LEADS' if 'urn:' in text else None)
    q('scope', '外部私有项目的验收记录是什么？', 'negative',
      forbidden=[FOREIGN + 'x_project-private'], structured_gold={'authorized_fact_answer': False})
    q('scope', '检索外部空间 private 项目的负责人。', 'negative',
      forbidden=[FOREIGN + 'x_project-private'], structured_gold={'authorized_fact_answer': False})
    q('scope', '外部共享项目是否应该计入当前 namespace 的项目数？', 'structured',
      forbidden=[FOREIGN + 'x_project-shared'], structured_gold={'in_current_namespace': False})
    q('scope', '归档旧项目还应该作为当前事实召回吗？', 'negative',
      forbidden=[uri('x_project-archived')], structured_gold={'current_fact': False})
    q('scope', '停用记录项目的当前优先级是什么？', 'negative',
      forbidden=[uri('x_project-disabled')], structured_gold={'current_fact': False})
    q('scope', '岩石项目没有负责边，能从允许负责的规则中找出负责人吗？', 'structured',
      ['x_project-stone'], structured_gold={'owner': None, 'rule_is_fact': False})
    q('structure', '当前 namespace 中有多少个有效项目？', 'structured', structured_gold={'count': 4})
    q('structure', '林舟经由负责和依赖关系能到达星桥吗？', 'structured',
      ['person-lin', 'x_project-orion', 'x_project-atlas'], structured_gold={'directed_path': [uri('person-lin'), uri('x_project-orion'), uri('x_project-atlas')]})
    q('structure', '有多少项目直接依赖曙光？', 'structured', ['x_project-orion', 'x_project-iris'], structured_gold={'count': 1})
    q('structure', '只沿依赖方向，能从曙光到达鸢尾吗？', 'structured',
      ['x_project-orion', 'x_project-iris'], structured_gold={'directed_path_exists': False})
    q('structure', '未知日期的访谈是否应算入十月二十日的事件数量？', 'structured',
      ['event-interview'], structured_gold={'included_in_known_range': False})
    q('structure', '三条同端点负责记录与唯一负责人数量应当怎样区分？', 'structured',
      ['person-lin', 'x_project-orion'], structured_gold={'physical_edges': 3, 'distinct_owners': 1})
    if len(questions) != 54 or any(sum(q['family'] == family for q in questions) != 6
                                  for family in {q['family'] for q in questions}):
        raise AssertionError('The diagnostic contract is exactly nine families of six questions')
    return dataset, questions


def tool_hash():
    paths = [Path(__file__), ROOT / 'plugins/kg/retrieval_documents.py', ROOT / 'plugins/kg/schema.py',
             ROOT / 'plugins/kg/embedding.py', ROOT / 'plugins/kg/embedding_profiles.py',
             *sorted((ROOT / 'plugins/kg/research').glob('*.py')), ROOT / 'plugins/kg/research/protocol-v1.json']
    return digest({path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})


def prepare(output):
    output = Path(output)
    dataset, questions = build_fixture()
    from kg.embedding_profiles import available_profiles
    pins = [profile for profile in available_profiles() if profile['key'] in ('e5-small', 'qwen3-0.6b')]
    manifest = {'format': FORMAT, 'fixture_revision': FIXTURE_REVISION, 'scope': 'dev_diagnostic_only',
                'promotion': 'blocked', 'queries': 54, 'families': 9,
                'dataset_sha256': digest(dataset), 'queries_sha256': digest(questions), 'tool_sha256': tool_hash(),
                'frozen_before_model_calls': True, 'retrieval_scope': 'current_namespace_active_only',
                'ranking': 'exact_cosine_no_lexical_or_graph_expansion', 'top_k': 5, 'max_tokens': 512,
                'representations': list(REPRESENTATIONS), 'allowed_models': ['e5-small', 'qwen3-0.6b'],
                'model_profile_pins': pins,
                'answer_generation': False, 'structured_execution': False,
                'freeze_proof': 'Local creation order and content hashes only; not independent preregistration or a third-party timestamp.',
                'interpretation': 'Descriptive development diagnostics; families overlap in underlying entities and are not independent statistical samples.'}
    manifest['manifest_sha256'] = digest(manifest)
    names = ('dataset.json', 'questions.json', 'diagnostic-manifest.json')
    if output.exists() and any(output.iterdir()):
        if not all((output / name).is_file() for name in names):
            raise ValueError('Existing diagnostic output is incomplete; no fixture or gold was overwritten')
        if read(output / names[0]) != dataset or read(output / names[1]) != questions or read(output / names[2]) != manifest:
            raise ValueError('Frozen diagnostic inputs/tool changed; choose a new output, never edit gold after results')
    else:
        for name, value in zip(names, (dataset, questions, manifest)):
            write_new(output / name, value)
    return {'dataset': dataset, 'questions': questions, 'manifest': manifest}


def documents_for(dataset, representation, schema_only=False):
    from kg.retrieval_documents import dataset_to_documents
    documents = dataset_to_documents(dataset, representation, include_ontology=schema_only)
    return [doc for doc in documents if (doc.kind == 'ontology') == schema_only
            and (schema_only or all(ref.startswith(dataset['namespace']) for ref in doc.source_refs))]


def _literal(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _visible_property(doc, key, value):
    # This measures a deliberately strict renderer-exposure control: a readable
    # canonical predicate plus its literal must be visible together. It is NOT
    # a semantic entailment score, an answer generator, or proof that the user
    # could not hydrate a value-only result through another API.
    return any(f'[{key}; ' in line and line.split(']: ', 1)[-1] == _literal(value)
               for line in doc.text.splitlines())


def score_question(question, ranked):
    top = ranked[:5]
    refs = {ref for doc in top for ref in doc.source_refs if doc.kind != 'ontology'}
    targets = set(question['entity_targets'])
    properties = question['property_atoms']
    relations = question['relation_atoms']
    rules = question['rule_targets']
    enough_properties = all(any(doc.kind == 'entity' and atom['uri'] in doc.source_refs
                               and _visible_property(doc, atom['key'], atom['value']) for doc in top)
                            for atom in properties)
    def relation_matches(doc, atom):
        metadata = doc.metadata
        return (doc.kind == 'relation' and metadata.get('from_uri') == atom['from']
                and metadata.get('to_uri') == atom['to'] and metadata.get('relation_type') == atom['type']
                and all(_visible_property(doc, key, value) for key, value in atom['properties'].items()))
    enough_relations = all(any(relation_matches(doc, atom) for doc in top) for atom in relations)
    enough_rules = all(any(doc.kind == 'ontology' and doc.metadata.get('is_rule') is True
                          and doc.metadata.get('definition_kind') == atom['collection']
                          and doc.metadata.get('definition_key') == atom['key'] for doc in top) for atom in rules)
    return {'entity_hit@5': float(bool(targets & refs)) if targets else None,
            'entity_anchor_recall@5': len(targets & refs) / len(targets) if targets else None,
            'visible_property_evidence@5': float(enough_properties) if properties else None,
            'directed_relation_evidence@5': float(enough_relations) if relations else None,
            'schema_definition_hit@5': float(enough_rules) if rules else None,
            'rule_as_fact': float(any(doc.kind == 'ontology' for doc in top)) if question['track'] == 'facts' else None,
            'forbidden_source_returned': float(bool(set(question['forbidden_sources']) & refs)),
            'negative_nonempty_candidates@5': float(bool(top)) if question['task'] == 'negative' else None,
            'iri_literal_exposed@5': float(any(question['iri_anchor'] in doc.text for doc in top)) if question['iri_anchor'] else None,
            'answer_correctness': None, 'structured_answer_correctness': None}


def summarize(rows):
    keys = sorted({key for row in rows for key in row['metrics']})
    metrics, denominators = {}, {}
    for key in keys:
        values = [row['metrics'][key] for row in rows if row['metrics'][key] is not None]
        metrics[key], denominators[key] = (statistics.fmean(values) if values else None), len(values)
    return {'queries': len(rows), 'metrics': metrics, 'denominators': denominators,
            'structured_or_absence_tasks_unexecuted': sum(row['task'] in ('structured', 'negative') for row in rows)}


def evaluate(bundle, embedder, *, cache_path=None, require_real=True):
    from kg.research.runner import VectorCache, normalize
    from kg.research.metrics import percentile
    dataset, questions, frozen = bundle['dataset'], bundle['questions'], bundle['manifest']
    if (frozen['dataset_sha256'] != digest(dataset) or frozen['queries_sha256'] != digest(questions)
            or frozen['tool_sha256'] != tool_hash()
            or frozen.get('manifest_sha256') != digest({key: value for key, value in frozen.items() if key != 'manifest_sha256'})
            or frozen.get('scope') != 'dev_diagnostic_only' or frozen.get('promotion') != 'blocked'):
        raise ValueError('Diagnostic inputs/tool differ from the frozen manifest')
    embedder.prepare(allow_download=False)
    model = deepcopy(embedder.model_manifest)
    fingerprint = embedder.fingerprint
    real = model.get('ready') is True and bool(model.get('files_sha256')) and model.get('real_vectors') is not False
    pins = {profile['key']: profile for profile in frozen['model_profile_pins']}
    profile = pins.get(model.get('key'), {})
    if require_real and (not real or model.get('max_tokens') != 512 or model.get('device') != 'cpu'
                         or model.get('precision') != 'float32'
                         or model.get('model_revision') != profile.get('model_revision') or not profile):
        raise ValueError('Diagnostics require a prepared pinned real CPU embedding profile with max_tokens=512')
    cache = VectorCache(cache_path) if cache_path else None
    reports, docs_hashes = {}, {}
    try:
        for representation in REPRESENTATIONS:
            corpora = {'facts': documents_for(dataset, representation),
                       'schema': documents_for(dataset, representation, True)}
            docs_hashes[representation] = digest({key: [doc.to_dict() for doc in docs] for key, docs in corpora.items()})
            vectors = {}
            started = time.perf_counter()
            for track, documents in corpora.items():
                rows, missing = [], []
                for doc in documents:
                    chunks = embedder.chunk(doc.text)
                    for chunk in chunks:
                        text = chunk.text if hasattr(chunk, 'text') else chunk['text']
                        vector = cache.get(fingerprint, 'passage', text) if cache else None
                        row = [doc, text, vector]
                        rows.append(row)
                        if vector is None:
                            missing.append(row)
                for start in range(0, len(missing), 8):
                    batch = missing[start:start + 8]
                    encoded = embedder.embed_passages([row[1] for row in batch])
                    if len(encoded) != len(batch):
                        raise ValueError('Embedding batch cardinality mismatch')
                    for row, vector in zip(batch, encoded):
                        row[2] = cache.put(fingerprint, 'passage', row[1], vector) if cache else normalize(vector)
                vectors[track] = rows
            preparation_ms = (time.perf_counter() - started) * 1000
            measured = []
            for question in questions:
                tick = time.perf_counter()
                query = normalize(embedder.embed_query(question['question']))
                scores, documents = {}, {}
                for doc, _, vector in vectors[question['track']]:
                    if len(query) != len(vector):
                        raise ValueError('Mixed embedding dimensions')
                    score = sum(left * right for left, right in zip(query, vector))
                    scores[doc.id] = max(scores.get(doc.id, -math.inf), score)
                    documents[doc.id] = doc
                ranking = [documents[key] for key in sorted(scores, key=lambda key: (-scores[key], key))]
                measured.append({'id': question['id'], 'family': question['family'], 'track': question['track'],
                                 'task': question['task'], 'ranked': [doc.id for doc in ranking[:5]],
                                 'metrics': score_question(question, ranking),
                                 'latency_ms': (time.perf_counter() - tick) * 1000})
            reports[representation] = {'all': summarize(measured),
                'facts': summarize([row for row in measured if row['track'] == 'facts']),
                'schema': summarize([row for row in measured if row['track'] == 'schema']),
                'families': {family: summarize([row for row in measured if row['family'] == family])
                             for family in sorted({row['family'] for row in measured})},
                'latency_ms': {'p50': percentile([row['latency_ms'] for row in measured], .5),
                               'p95': percentile([row['latency_ms'] for row in measured], .95)},
                'preparation_ms': preparation_ms, 'documents': {key: len(value) for key, value in corpora.items()},
                'rows': measured}
        manifest = {'diagnostic_sha256': frozen['manifest_sha256'], 'tool_sha256': tool_hash(),
                    'model': model, 'model_fingerprint': fingerprint, 'documents_sha256': docs_hashes,
                    'results_sha256': digest(reports)}
        return {'format': FORMAT, 'scope': 'dev_diagnostic_only', 'promotion': 'blocked',
                'execution_success': True, 'real_vectors': real, 'synthetic_only': True,
                'external_model_calls': 0, 'database_accessed': False, 'manifest': manifest,
                'run_id': digest(manifest), 'representations': reports,
                'interpretation': [
                    'Entity hits are anchor recall, never generated-answer correctness.',
                    'Visible predicate/literal evidence is a renderer-exposure control, not semantic entailment or hydration/API accuracy.',
                    'Negative, count, path and time-filter tasks are unexecuted; their answer accuracy is N/A.',
                    'Conjunctive numeric lookup questions measure evidence retrieval, not execution or completeness of structured filters.',
                    'Schema candidates are ranked in a separate corpus and cannot satisfy fact evidence.',
                    'Zero rule_as_fact is a static corpus-isolation check; it does not test agent reasoning about rules.',
                    'The explicit current-namespace prefilter is a static snapshot assertion, not a live authorization or race test.',
                    'No abstention threshold is selected; nonempty negative rankings are candidates, not accepted answers.',
                    'Small overlapping authored families support descriptive diagnostics only; no significance or promotion claim.',
                ]}
    finally:
        if cache:
            cache.close()


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('action', choices=('prepare', 'evaluate'))
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--model', choices=('e5-small', 'qwen3-0.6b'), default='e5-small')
    cli.add_argument('--model-cache', type=Path, default=ROOT / '.model-cache')
    cli.add_argument('--vector-cache', type=Path, default=ROOT / '.cache/retrieval-research/graph-semantic-vectors.sqlite3')
    cli.add_argument('--threads', type=int, default=8)
    args = cli.parse_args(argv)
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / 'reports').resolve()) or output == (ROOT / 'reports').resolve():
        cli.error('--output must be a directory below reports/')
    try:
        bundle = prepare(output)
        if args.action == 'prepare':
            result = {'format': FORMAT, 'scope': 'dev_diagnostic_only', 'promotion': 'blocked',
                      'execution_success': True, 'models_executed': 0, **bundle['manifest']}
        else:
            target = output / 'runs' / (args.model + '.json')
            if target.exists():
                raise ValueError('Diagnostic result already exists; preserve it and do not overwrite after seeing scores')
            from kg.embedding_profiles import create_embedder
            embedder = create_embedder(args.model, args.model_cache, device='cpu', precision='float32',
                                       threads=args.threads, batch_size=8, max_tokens=512)
            result = evaluate(bundle, embedder, cache_path=args.vector_cache)
            write_new(target, result)
            result = {'scope': result['scope'], 'promotion': result['promotion'], 'execution_success': True,
                      'real_vectors': result['real_vectors'], 'run_id': result['run_id'],
                      'representations': {key: {'facts': value['facts'], 'schema': value['schema']}
                                          for key, value in result['representations'].items()}}
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({'execution_success': False, 'scope': 'dev_diagnostic_only', 'promotion': 'blocked',
                          'error_type': type(exc).__name__, 'error': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
