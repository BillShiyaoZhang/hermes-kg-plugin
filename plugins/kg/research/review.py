"""Offline human review page: safe text nodes, no network or embedded secrets."""
from __future__ import annotations

import json

from .fixtures import ATTESTATION


def review_html(dataset, fixture, labels):
    sources = {e.get('uri', e.get('properties', {}).get('uri')): e for e in dataset['entities']}
    from .common import relation_key
    for relation in dataset.get('relations', []):
        key = relation_key(relation['from'], relation['type'], relation['to'])
        sources[key] = relation
    real_namespace = fixture.get('real_source_manifest', {}).get('namespace')
    catalogue = {key: value for key, value in sources.items() if real_namespace and real_namespace in key}
    source_families = {}
    for query in fixture['queries']:
        for ref in query.get('source_refs', []): source_families[ref] = query['source_family']
    cards = []
    for query in fixture['queries']:
        if query.get('provenance') != 'real_pending_human': continue
        cards.append({'query': query, 'sources': {ref: sources[ref] for ref in query['source_refs'] if ref in sources}})
    # Escape the script boundary even when a real source contains HTML markup.
    payload = json.dumps({'labels': labels, 'cards': cards, 'catalogue': catalogue, 'source_families': source_families, 'attestation': ATTESTATION}, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>检索研究：真实题人工复核</title>
<style>body{font:16px/1.6 system-ui;max-width:1000px;margin:24px auto;padding:0 20px;color:#24352e}header{position:sticky;top:0;background:white;padding:12px 0;border-bottom:1px solid #bbb}section{border:1px solid #bbb;border-radius:12px;margin:20px 0;padding:20px}textarea{width:100%;min-height:75px;box-sizing:border-box}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f5f3;padding:12px}button,input,select{font:inherit}label{display:block;margin:8px 0}.warning{color:#8a3500}small{color:#48584e}</style>
<header><h1>真实题人工复核（本地）</h1><p class="warning">这些是候选来源与空白标注，不是已经验证的100道真实题。先剔除历史测试、占位和不适合的问题。摘要只证明文件完整性，不认证复核人身份。</p>
<label>复核人 <input id="reviewer" autocomplete="off"></label><label><input id="attest" type="checkbox">我逐项核对了授权来源、问题及证据等级</label><label>恢复本地草稿 <input id="resume" type="file" accept="application/json"></label><button id="export">导出带摘要的标注 JSON</button><p id="status" role="status"></p></header><main id="cards"></main>
<script id="data" type="application/json">''' + payload + '''</script><script>
const payload=JSON.parse(document.getElementById('data').textContent);let labels=payload.labels;
const controls=new Map();const root=document.getElementById('cards');const catalogue=document.createElement('datalist');catalogue.id='source-catalogue';document.body.appendChild(catalogue);for(const key of Object.keys(payload.catalogue)){const o=document.createElement('option');o.value=key;catalogue.appendChild(o);}
function text(tag,value,parent){const el=document.createElement(tag);el.textContent=value;parent.appendChild(el);return el;}
function draw(){root.replaceChildren();controls.clear();const indexed=new Map(labels.entries.map(e=>[e.id,e]));for(const card of payload.cards){const q=card.query,e=indexed.get(q.id);const section=document.createElement('section');root.appendChild(section);text('h2',q.id+' · '+q.split,section);text('small','来源家族 '+q.source_family,section);
if(!q.source_refs.length)text('p','缺少可用独立来源；此槽位不能完成复核。',section);
for(const [uri,source]of Object.entries(card.sources)){text('strong',uri,section);text('pre',JSON.stringify(source,null,2),section);}
text('label','问题：以下为确定性候选，必须核对并独立改写，不复制答案内容',section);const question=document.createElement('textarea');question.value=e.question||q.draft_question||'';section.appendChild(question);
text('label','真实来源家族（不能跨开发与盲测划分，也不能虚构独立来源）',section);const family=document.createElement('input');family.value=e.source_family||q.source_family;family.style.width='100%';section.appendChild(family);
const answerable=document.createElement('select');for(const [v,t]of [['','尚未核对'],['true','来源中有充分答案'],['false','真正无答案']]){const o=document.createElement('option');o.value=v;o.textContent=t;answerable.appendChild(o);}answerable.value=e.answerable===null?'':String(e.answerable);section.appendChild(answerable);
const grades=new Map();function addEvidence(uri,preview=false){if(grades.has(uri))return;if(preview&&payload.catalogue[uri])text('pre',JSON.stringify(payload.catalogue[uri],null,2),section);const label=text('label','证据 '+uri+' ',section),select=document.createElement('select');for(const [v,t]of [[0,'无关'],[1,'仅背景'],[2,'充分答案所需证据']]){const o=document.createElement('option');o.value=v;o.textContent=t;select.appendChild(o);}select.value=String(e.relevance[uri]||0);label.appendChild(select);grades.set(uri,select);}for(const uri of new Set([...q.source_refs,...Object.keys(e.relevance)]))addEvidence(uri);
const extra=document.createElement('input');extra.setAttribute('list','source-catalogue');extra.placeholder='选择同一授权快照中的实体URI或有方向关系';section.appendChild(extra);const add=text('button','增加证据 / 查看来源',section);add.type='button';add.onclick=()=>{if(payload.catalogue[extra.value]){if(family.value.startsWith('real:unassigned-')&&payload.source_families[extra.value])family.value=payload.source_families[extra.value];addEvidence(extra.value,true);extra.value='';}else alert('请从授权快照的候选目录选择');};
text('small','选中的等级2证据作为一个充分集合：必须全部召回，不按任一实体命中算正确。复杂路径/统计题请在JSON补task_type与独立答案校验。',section);
text('label','答案专属词（逗号分隔，禁止出现在题目中）',section);const terms=document.createElement('input');terms.value=(e.answer_terms||[]).join(',');section.appendChild(terms);
const reviewed=document.createElement('input');reviewed.type='checkbox';reviewed.checked=e.status==='reviewed';const lab=text('label','此题已由我逐项复核 ',section);lab.prepend(reviewed);controls.set(q.id,{q,question,answerable,grades,terms,reviewed,family});}}
function canonical(v){if(Array.isArray(v))return'['+v.map(canonical).join(',')+']';if(v&&typeof v==='object')return'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}';return JSON.stringify(v);}
document.getElementById('resume').onchange=async(event)=>{try{const value=JSON.parse(await event.target.files[0].text());if(value.dataset_sha256!==labels.dataset_sha256||value.entries.length!==100)throw Error('来源清单不匹配');labels=value;draw();}catch(e){document.getElementById('status').textContent='无法恢复：'+e.message;}};
document.getElementById('export').onclick=async()=>{
try{
 const reviewer=document.getElementById('reviewer').value.trim(),attested=document.getElementById('attest').checked;
 if(!reviewer||!attested)throw Error('需要复核人和明确人工确认，不能自动生成gold');
 const entries=labels.entries.map(old=>{
  const c=controls.get(old.id),relevance={};
  for(const [uri,s]of c.grades)if(Number(s.value))relevance[uri]=Number(s.value);
  const enough=Object.keys(relevance).filter(k=>relevance[k]===2);
  return{...old,question:c.question.value.trim(),task_type:c.q.draft_task_type||'semantic',
   source_refs:Array.from(c.grades.keys()),source_family:c.family.value.trim(),source_fingerprint:labels.dataset_sha256,
   answerable:c.answerable.value===''?null:c.answerable.value==='true',relevance,
   sufficient_sets:enough.length?[enough]:[],answer_terms:c.terms.value.split(',').map(x=>x.trim()).filter(Boolean),
   status:c.reviewed.checked?'reviewed':'pending',review_origin:c.reviewed.checked?'human':null,
   reviewer,reviewed_at:new Date().toISOString(),attestation:payload.attestation};
 });
 const out={format:labels.format,dataset_sha256:labels.dataset_sha256,entries};
 const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(out)));
 out.submission_sha256=Array.from(new Uint8Array(hash),x=>x.toString(16).padStart(2,'0')).join('');
 const url=URL.createObjectURL(new Blob([JSON.stringify(out,null,2)],{type:'application/json'}));
 const a=document.createElement('a');a.href=url;a.download='reviewed-labels.json';a.click();
 setTimeout(()=>URL.revokeObjectURL(url),1000);
 document.getElementById('status').textContent='已导出；请运行 validate-labels 校验并锁定，未完成项仍会阻断正式结论。';
}catch(e){document.getElementById('status').textContent=e.message;}
};draw();
</script></html>'''
