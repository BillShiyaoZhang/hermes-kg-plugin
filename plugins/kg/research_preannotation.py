"""Assemble source-backed machine drafts for explicit later human review.

This module never calls a model, reads a profile, changes a frozen fixture or
signs human attestations. The independent per-row judgments are supplied by the
annotator; deterministic assembly checks references, partitions and review gates.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import html as html_escape
import json
from pathlib import Path
import re

from .research.common import digest, read_json, relation_key, write_json
from .research.fixtures import validate_fixture, validate_labels
from .research.review import review_html

FORMAT = 'kg-retrieval-preannotation-v1'
HUMAN_BLOCKERS = {'not_human_reviewed', 'missing_attestation', 'invalid_review_time'}
COMMON_RISKS = {'source_unattributed', 'snapshot_only', 'historical_snapshot'}


def source_catalogue(dataset, namespace):
    result = {}
    for entity in dataset['entities']:
        uri = entity.get('uri', entity.get('properties', {}).get('uri'))
        if isinstance(uri, str) and uri.startswith(namespace):
            result[uri] = entity
    for edge in dataset['relations']:
        if edge['from'] in result and edge['to'] in result:
            key = relation_key(edge['from'], edge['type'], edge['to'])
            # A logical triple can have several real qualifier variants.
            result.setdefault(key, {'variants': []})['variants'].append(edge)
    return result


def assemble(dataset, fixture, template, batches):
    validate_fixture(fixture, dataset)
    snapshot = digest(dataset)
    if template.get('dataset_sha256') != snapshot:
        raise ValueError('Template does not bind this dataset')
    real = {q['id']: q for q in fixture['queries'] if q.get('provenance') == 'real_pending_human'}
    templates = {entry['id']: entry for entry in template['entries']}
    if any(entry.get('status') == 'reviewed' or entry.get('review_origin') == 'human'
           or any(entry.get(name) for name in ('reviewer', 'attestation', 'reviewed_at'))
           for entry in templates.values()):
        raise ValueError('Refusing to replace an existing human review with machine drafts')
    namespace = fixture['real_source_manifest']['namespace']
    catalogue, entries, seen = source_catalogue(dataset, namespace), [], set()
    for batch in batches:
        if (batch.get('format') != 'kg-retrieval-preannotation-batch-v1'
                or batch.get('review_origin') != 'machine' or batch.get('human_reviewed') is not False):
            raise ValueError('Only explicit machine preannotation batches are accepted')
        if batch.get('dataset_sha256') != snapshot:
            raise ValueError('Machine batch belongs to another frozen snapshot')
        for item in batch['entries']:
            key = item.get('id')
            if key not in real or key in seen:
                raise ValueError('Unknown or duplicate real annotation ID')
            seen.add(key)
            if (any(item.get(name) for name in ('reviewer', 'reviewed_at', 'attestation'))
                    or item.get('status') == 'reviewed' or item.get('review_origin') == 'human'
                    or item.get('human_reviewed') is True):
                raise ValueError('A machine batch cannot contain human review claims')
            if item.get('confidence') not in {'high', 'medium', 'low'}:
                raise ValueError('A support-confidence judgment is required')
            codes = item.get('uncertainty_codes')
            if not isinstance(codes, list) or any(not isinstance(code, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}', code) for code in codes):
                raise ValueError('Invalid uncertainty codes')
            if not isinstance(item.get('proposed_answer'), str) or not item['proposed_answer'].strip():
                raise ValueError('Each slot needs an explicit proposed answer or abstention explanation')
            refs = item.get('source_refs')
            if not isinstance(refs, list) or not refs or len(set(refs)) != len(refs) or any(ref not in catalogue for ref in refs):
                raise ValueError('Sources must be distinct references in the authorized real snapshot')
            relevance = item.get('relevance')
            if not isinstance(relevance, dict) or any(ref not in refs or type(grade) is not int or grade not in (0, 1, 2) for ref, grade in relevance.items()):
                raise ValueError('Grades must reference inspected real sources')
            if type(item.get('answerable')) is not bool or not str(item.get('question', '')).strip():
                raise ValueError('Each slot needs a question and answerability judgment')
            if item.get('task_type') not in {'semantic', 'exact', 'relation'}:
                raise ValueError('Preserve the real task type or explicitly replace a semantic slot')
            if not isinstance(item.get('answer_terms'), list) or any(not isinstance(term, str) for term in item['answer_terms']):
                raise ValueError('Answer terms must be a list of strings')
            row = deepcopy(templates[key])
            for name in ('question', 'task_type', 'answerable', 'relevance', 'sufficient_sets', 'answer_terms', 'source_refs', 'source_family'):
                row[name] = deepcopy(item[name])
            # Bind all inspected dependencies, including additional endpoints,
            # to the immutable full snapshot, rather than only the old anchor.
            row.update(source_fingerprint=snapshot, status='pending', review_origin='machine',
                       reviewer='', reviewed_at='', attestation='')
            annotation = {name: deepcopy(item.get(name, '')) for name in (
                'proposed_answer', 'confidence', 'uncertainty_codes', 'uncertainty_note', 'rationale')}
            annotation['confidence_meaning'] = 'support in frozen graph fields; not verified truth, current state or human approval'
            annotation['original_source_refs'] = real[key].get('source_refs', [])
            annotation['original_draft_question'] = real[key].get('draft_question', '')
            # Exact URI tasks can contain part of their display name. They are
            # identity diagnostics, not semantic paraphrase evidence. Do not
            # claim overlapping words are answer-only evaluation terms.
            overlapping = [term for term in row['answer_terms'] if term and term.casefold() in row['question'].casefold()]
            if overlapping:
                if row['task_type'] != 'exact':
                    raise ValueError('A semantic question contains an answer-only term')
                row['answer_terms'] = [term for term in row['answer_terms'] if term not in overlapping]
                annotation['omitted_overlapping_answer_terms'] = overlapping
            row['machine_annotation'] = annotation
            entries.append(row)
    if seen != set(real) or len(entries) != 100 or set(templates) != set(real):
        raise ValueError('Exactly all 100 real slots must have one machine draft')
    labels = {'format': template['format'], 'dataset_sha256': snapshot,
              'preannotation_format': FORMAT, 'review_origin': 'machine',
              'human_reviewed': False, 'entries': sorted(entries, key=lambda entry: entry['id'])}
    validation = validate_labels(fixture, dataset, labels)
    issues = {row['id']: sorted(set(row['reasons']) - HUMAN_BLOCKERS) for row in validation['issues']}
    for row in labels['entries']:
        annotation = row['machine_annotation']
        annotation['structural_issues'] = issues.get(row['id'], [])
        annotation['needs_content_review'] = bool(annotation['confidence'] != 'high'
            or annotation['structural_issues'])
        annotation['needs_risk_review'] = bool(set(annotation['uncertainty_codes']) - COMMON_RISKS)
    # Even a fully filled, coherent machine draft remains ineligible as gold.
    assert validation['reviewed'] == 0 and not validation['ok']
    return labels, catalogue, validation


def render_workbench(dataset, fixture, labels):
    view = deepcopy(fixture)
    indexed = {row['id']: row for row in labels['entries']}
    for query in view['queries']:
        if query['id'] in indexed:
            # Preview added real endpoint evidence and the replacement slot,
            # without changing the frozen fixture on disk.
            query['source_refs'] = indexed[query['id']]['source_refs']
            query['draft_task_type'] = indexed[query['id']]['task_type']
            query['source_family'] = indexed[query['id']]['source_family']
    html = review_html(dataset, view, labels)
    marker = '<script id="data" type="application/json">'
    old_payload = html.split(marker, 1)[1].split('</script>', 1)[0]
    payload = json.loads(old_payload)
    catalogue = source_catalogue(dataset, fixture['real_source_manifest']['namespace'])
    payload['catalogue'] = catalogue
    for card in payload['cards']:
        card['sources'] = {ref: catalogue[ref] for ref in card['query']['source_refs']}
    # Keep actual parallel variants in the human source preview too. The frozen
    # legacy workbench alone keeps only the last edge of a logical triple.
    escaped = json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    html = html.replace(marker + old_payload + '</script>', marker + escaped + '</script>', 1)

    def replace(old, new):
        nonlocal html
        if html.count(old) != 1:
            raise ValueError('Frozen review renderer changed; inspect before composing a new page')
        html = html.replace(old, new)

    replace('这些是候选来源与空白标注，不是已经验证的100道真实题。先剔除历史测试、占位和不适合的问题。摘要只证明文件完整性，不认证复核人身份。',
        '100条已由机器预填，尚无人工gold。答案以本轮冻结快照为准，不代表当前在线状态。请优先核对内容疑点，再确认来源归属；高置信度只说明字段支撑明确。')
    replace('<main id="cards"></main>', '<nav><label>筛选 <select id="filter"><option value="all">全部100条</option><option value="content">内容疑点 / 替补项</option><option value="source">来源归属缺失</option><option value="pending">尚未人工确认</option><option value="reviewed">已人工确认</option></select></label><p id="counts" role="status"></p><p>每条均已给出建议答案、0/1/2证据与完整充分集合；只在实际核对后勾选该条。完成前可导出部分进度，未确认项仍不计作gold。</p></nav><main id="cards"></main>')
    replace("text('small','来源家族 '+q.source_family,section);", """text('small','来源家族 '+e.source_family,section);
section.dataset.id=e.id;const a=e.machine_annotation||{};section.dataset.content=String(!!a.needs_content_review);section.dataset.source=String((a.uncertainty_codes||[]).includes('source_unattributed'));
text('p','建议答案：'+(a.proposed_answer||''),section);text('p','字段支撑置信度：'+(a.confidence||'')+'；机器草稿，待人工确认',section);
if(a.uncertainty_note)text('p','待核对：'+a.uncertainty_note,section).className='warning';
if(a.rationale)text('p','预标注依据：'+a.rationale,section);
if((a.structural_issues||[]).length)text('p','需修正的标注约束：'+a.structural_issues.join(', '),section).className='warning';""")
    replace("text('small','选中的等级2证据作为一个充分集合：必须全部召回，不按任一实体命中算正确。复杂路径/统计题请在JSON补task_type与独立答案校验。',section);", """text('label','充分证据集合（JSON；同一集合必须全部召回，不同集合为替代支持）',section);
const sets=document.createElement('textarea');sets.value=JSON.stringify(e.sufficient_sets||[]);section.appendChild(sets);
const useAll=text('button','用所有2级证据组成一个完整集合',section);useAll.type='button';useAll.onclick=()=>{const refs=Array.from(grades).filter(([uri,s])=>Number(s.value)===2).map(([uri])=>uri);sets.value=JSON.stringify(refs.length?[refs]:[]);reviewed.checked=false;applyFilter();};""")
    replace("controls.set(q.id,{q,question,answerable,grades,terms,reviewed,family});}}", """controls.set(q.id,{q,question,answerable,grades,terms,reviewed,family,sets});
for(const field of [question,answerable,terms,family,sets,...grades.values()])field.addEventListener('input',()=>{reviewed.checked=false;applyFilter();});reviewed.addEventListener('change',applyFilter);
}applyFilter();}
function applyFilter(){const filter=document.getElementById('filter').value;let shown=0,done=0;for(const section of root.children){const c=controls.get(section.dataset.id);if(c.reviewed.checked)done++;const visible=filter==='all'||filter==='content'&&section.dataset.content==='true'||filter==='source'&&section.dataset.source==='true'||filter==='pending'&&!c.reviewed.checked||filter==='reviewed'&&c.reviewed.checked;section.hidden=!visible;if(visible)shown++;}document.getElementById('counts').textContent='显示 '+shown+' / 100；已人工勾选 '+done+' / 100（初始为0）';}
document.getElementById('filter').addEventListener('change',applyFilter);""")
    replace("sufficient_sets:enough.length?[enough]:[],answer_terms:", "sufficient_sets:JSON.parse(c.sets.value||'[]'),answer_terms:")
    # Explicitly keep pending drafts marked machine after a partial export.
    replace("review_origin:c.reviewed.checked?'human':null,", "review_origin:c.reviewed.checked?'human':'machine',")
    replace('reviewer,reviewed_at:new Date().toISOString(),attestation:payload.attestation',
        "reviewer:c.reviewed.checked?reviewer:'',reviewed_at:c.reviewed.checked?new Date().toISOString():'',attestation:c.reviewed.checked?payload.attestation:''")
    replace('grades.set(uri,select);', "grades.set(uri,select);select.addEventListener('input',()=>{const existing=controls.get(q.id);if(existing){existing.reviewed.checked=false;applyFilter();}});")
    replace('addEvidence(extra.value,true);extra.value=\'\';',
        "addEvidence(extra.value,true);reviewed.checked=false;applyFilter();extra.value='';")
    return html


def uncertainty_report(labels, validation):
    entries = labels['entries']
    content = [row for row in entries if row['machine_annotation']['needs_content_review']]
    source = [row for row in entries if 'source_unattributed' in row['machine_annotation']['uncertainty_codes']]
    lines = ['# 100条机器预标注：人工复核清单', '',
        '预标注基于本轮冻结图快照，指纹见summary.json。没有运行候选检索、读取盲测排名或调用MiniMax；以下不是人工gold。', '',
        f'已填100/100；内容疑点或替补建议{len(content)}条；来源归属缺失{len(source)}条；当前人工通过0/100。', '',
        '置信度仅指快照字段是否直接支撑建议答案。来源真实与否、当前是否仍有效，必须由你核对。', '',
        '## 优先核对内容', '', '| ID | 问题 | 建议答案 | 需要确认什么 |', '|---|---|---|---|']
    # Source material remains literal in Markdown previews as well as HTML.
    clean = lambda value: html_escape.escape(str(value), quote=False).replace('|', '\\|').replace('[', '\\[').replace(']', '\\]').replace('\n', '<br>')
    for row in content:
        a = row['machine_annotation']
        note = a['uncertainty_note'] + ('；标注约束：' + ','.join(a['structural_issues']) if a['structural_issues'] else '')
        lines.append('| ' + ' | '.join(map(clean, (row['id'], row['question'], a['proposed_answer'], note))) + ' |')
    risks = {}
    for row in entries:
        for code in set(row['machine_annotation']['uncertainty_codes']) - COMMON_RISKS:
            risks.setdefault(code, []).append(row['id'])
    lines += ['', '## 其他风险提示（不等同于答案字段含糊）', '',
        '高支撑项也可能涉及历史资源、未执行的决定、隐私位置或弱关联语义。以下提示保留全部原标注；可按ID在页面核对。', '']
    for code, ids in sorted(risks.items()):
        lines.append('- ' + code + '：' + ', '.join(ids))
    lines += ['', '## 共性来源问题', '',
        f'{len(source)}条缺少原始会话归属。这是共同的来源风险，与每题答案字段是否明确分别处理；不能把这些条目当作{len(source)}个独立来源家族。', '',
        ', '.join(row['id'] for row in source), '',
        '## 题集覆盖限制', '',
        f'- 当前预标注可回答{sum(row["answerable"] for row in entries)}题，无答案{sum(not row["answerable"] for row in entries)}题。',
        '- 原99条非空草稿均直接问已有字段或实际边，不能把模型未找回误标成真正无答案。现有无答案覆盖不足，正式冻结前应补充人工确认的负例；本轮未擅自替换20条题目。',
        f'- 保留{sum(row["task_type"] == "exact" for row in entries)}条原URI定位题，单列身份定位能力，不把URI里的名称线索算语义改写效果。',
        '- real-040原本无问题和来源；本次只提供同dev来源家族内的替补建议，需你明确确认。', '',
        '## 复核方式', '',
        '打开同目录的human-review-prefilled.html，可筛选内容疑点和来源归属缺失，逐项修改问题、证据等级与充分集合。填写复核人及确认声明，并仅勾选实际核对过的条目，再导出JSON。未勾选项保持pending，不会自动成为gold。', '',
        '原fixture、旧工作台和原模板未覆盖。没有创建labels lock或运行正式盲测。', '']
    return '\n'.join(lines)


def build(fixture_dir, batch_paths, output_dir):
    fixture_dir, output_dir = Path(fixture_dir).resolve(), Path(output_dir).resolve()
    dataset, fixture, template = [read_json(fixture_dir / name) for name in ('dataset.json', 'fixture.json', 'labels-template.json')]
    labels, catalogue, validation = assemble(dataset, fixture, template, [read_json(path) for path in batch_paths])
    for name in ('prelabels.json', 'human-review-prefilled.html', 'uncertain-items.md', 'summary.json'):
        if (output_dir / name).exists():
            raise ValueError('Refusing to overwrite existing annotation work')
    html = render_workbench(dataset, fixture, labels)
    report = uncertainty_report(labels, validation)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / 'prelabels.json', labels, replace=False)
    (output_dir / 'human-review-prefilled.html').write_text(html, encoding='utf-8')
    (output_dir / 'uncertain-items.md').write_text(report, encoding='utf-8')
    annotations = [row['machine_annotation'] for row in labels['entries']]
    summary = {'format': FORMAT, 'created_at': datetime.now(timezone.utc).isoformat(),
        'dataset_sha256': digest(dataset), 'fixture_sha256': digest(fixture), 'machine_drafts': 100,
        'human_reviewed': validation['reviewed'], 'human_gold_eligible': False, 'external_generation_api_calls': 0,
        'confidence': dict(Counter(item['confidence'] for item in annotations)),
        'content_review_ids': [row['id'] for row in labels['entries'] if row['machine_annotation']['needs_content_review']],
        'risk_notice_ids': [row['id'] for row in labels['entries'] if row['machine_annotation']['needs_risk_review']],
        'source_unattributed_ids': [row['id'] for row in labels['entries'] if 'source_unattributed' in row['machine_annotation']['uncertainty_codes']],
        'structural_issues': [row for row in validation['issues'] if set(row['reasons']) - HUMAN_BLOCKERS],
        'answerable': sum(row['answerable'] for row in labels['entries']),
        'not_answerable': sum(not row['answerable'] for row in labels['entries']),
        'input_bytes_sha256': {str(path): hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in batch_paths},
        'assembler_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'artifacts': {name: hashlib.sha256((output_dir / name).read_bytes()).hexdigest() for name in ('prelabels.json', 'human-review-prefilled.html', 'uncertain-items.md')},
        'scope': 'machine preannotation only; no human attestations, lock, blind evaluation or production changes'}
    write_json(output_dir / 'summary.json', summary, replace=False)
    return summary
