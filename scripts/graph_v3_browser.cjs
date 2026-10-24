/* Full-app interaction acceptance. All API traffic is fulfilled with fictional data.
 * Run Vite first, then PLAYWRIGHT_MODULE=... node scripts/graph_v3_browser.cjs.
 * Optional KG_GRAPH_INTERACTIVE=1 runs visibly and keeps the synthetic prototype
 * open after acceptance. Close its browser window to finish the process.
 * Does not connect to a memory profile or database, even when one is running.
 */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = process.env.KG_V3_BROWSER_URL || 'http://127.0.0.1:5173';
const interactive = process.env.KG_GRAPH_INTERACTIVE === '1';
const output = path.resolve(process.env.KG_V3_REPORT_DIR || 'reports/graph-v3');
const namespace = 'urn:graph-v3:synthetic:';
const types = ['person','event','preference','decision','location','concept'];
const names = ['人物','事件','偏好','决策','地点','概念'];
const keys = ['HERMES_knows','HERMES_participant','HERMES_hasPreference','HERMES_hasDecision','HERMES_locatedAt','LINKED_TO','CAUSED_BY','RELATED_TO','owl_sameAs'];
const relationNames = ['认识','参与','拥有偏好','作出决定','位于','关联','由…引起','相关','同一实体'];
const property = {key:'hermes_name',label:'名称',description:'记录显示名称',iri:'urn:synthetic:property:name',domain:['any'],range:['string'],aliases:['name'],required_for:['concept'],managed:false,runtime_supported:true};
const entity = (i) => ({uri:namespace+types[i%6]+'-'+i,type:types[i%6],name:['林舟','阅读讨论会','无糖茶','选择晨间学习','城市图书馆','知识管理'][i%6]+(i>5?' '+i:''),description:'仅用于交互验收的虚构知识记录 '+i,version:2,properties:{hermes_name:'虚构记录 '+i,hermes_eventTime:i%6===1?'2026-10-20T10:00:00+08:00':null,hermes_updatedAt:'2026-10-23T10:00:00Z'},updated_at:'2026-10-23T10:00:00Z',status:'active',evidence:[{quote:'这是合成测试依据。',message_id:'synthetic-1'}]});
const small = Array.from({length:12},(_,i)=>entity(i));
// Three independent event objects exercise known, unknown and later dates.
small[7] = {...small[7],properties:{...small[7].properties,hermes_eventTime:null}};
small[11] = {...entity(13),properties:{...entity(13).properties,hermes_eventTime:'2026-10-23T14:00:00+08:00'}};
const endpoint = n => ({uri:n.uri,type:n.type,name:n.name});
const edge = (i,source,target,relation_type='RELATED_TO') => ({id:'synthetic-edge-'+i,source:endpoint(source),target:endpoint(target),relation_type,stored_type:relation_type,properties:{hermes_origin:'synthetic'},properties_truncated:true,preview:true});
const edges = [edge(0,small[0],small[1],'HERMES_participant'),edge(1,small[0],small[2],'HERMES_hasPreference'),edge(2,small[0],small[3],'HERMES_hasDecision'),edge(3,small[1],small[4],'HERMES_locatedAt'),edge(4,small[5],small[0]),edge(5,small[5],small[5]),edge(6,small[0],small[1],'LINKED_TO')];
const batch = {id:'synthetic-review',status:'pending',reason:'虚构批次，核对新增关系与原记录差异。',entities:[{...small[0],name:'林舟（候选）',description:'待审的虚构修订'}],relations:[{from_urn:small[0].uri,to_urn:small[5].uri,type:'RELATED_TO'}],base_versions:{[small[0].uri]:2,[small[5].uri]:2}};
const scope='current_namespace_active';
const snapshot=(nodes,relationships,extra={})=>({namespace,scope,edge_scope:'returned_nodes',nodes,edges:relationships,total_nodes:nodes.length,total_edges:relationships.length,truncated_nodes:false,truncated_edges:false,focus_found:null,...extra});
const page=(items,body={})=>({namespace,scope,items:items.slice(body.offset||0,(body.offset||0)+(body.limit||20)),total:items.length,offset:body.offset||0,limit:body.limit||20,has_more:(body.offset||0)+(body.limit||20)<items.length});
const counts = data => types.map(type=>({type,count:data.filter(n=>n.type===type).length}));
function schema(data){return {namespace,scope,read_only:true,stats_available:true,stats_error:null,ontology:{iri:'urn:synthetic:ontology',label:'Synthetic ontology',description:'Fictional fixture',version:'3'},entity_types:types.map((type,i)=>({key:type,label:names[i],description:'合成'+names[i]+'定义',iri:'urn:synthetic:type:'+type,storage_label:type,runtime_supported:true,count:data.filter(n=>n.type===type).length,properties:[property]})),relations:keys.map((key,i)=>({key,label:relationNames[i],description:'合成关系定义',iri:'urn:synthetic:relation:'+key,storage_type:key,aliases:[key.toUpperCase()],domain:i<4?['person']:i===4?['event','person']:['any'],range:i===0?['person']:i===1?['event']:i===2?['preference']:i===3?['decision']:i===4?['location']:['any'],declared_domain:['any'],declared_range:['any'],count:edges.filter(e=>e.relation_type===key).length,properties:[],runtime_supported:true})),properties:[property],stats:{entities:data.length,relationships:edges.length,unmapped_relationships:0},schema_warnings:[]};}

(async()=>{
  fs.mkdirSync(output,{recursive:true});
  const browser=await chromium.launch({headless:!interactive,...(process.env.KG_CHROME_PATH?{executablePath:process.env.KG_CHROME_PATH}:{})});
  const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
  const errors=[],requests=[],results={synthetic_only:true};
  let dataset=small,reviewStatus='pending',slowApproval=false,releaseApproval,approvalConflict=false;
  const install=async(context)=>context.route('**/api/v1/**',async route=>{
    const request=route.request(),url=new URL(request.url()),body=request.postDataJSON()||{};
    requests.push({path:url.pathname,method:request.method(),body});
    if(url.search)throw new Error('Private read inputs entered API URL');
    if(approvalConflict&&url.pathname.endsWith('/approve')){
      approvalConflict=false;
      await route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({detail:'Synthetic version conflict; no mutation applied.'})});
      return;
    }
    let value={};
    const selected=dataset.filter(n=>(!body.types?.length||body.types.includes(n.type))&&(!body.type||body.type===n.type)&&(!body.q||(n.name+' '+n.description+' '+n.uri).toLowerCase().includes(body.q.toLowerCase())));
    const selectedIds=new Set(selected.map(n=>n.uri));
    const selectedEdges=edges.filter(e=>selectedIds.has(e.source.uri)&&selectedIds.has(e.target.uri)&&(!body.relation_types?.length||body.relation_types.includes(e.relation_type)));
    switch(url.pathname){
      case '/api/v1/status':value={connected:true,memory:{searchable_entities:dataset.length,counts:Object.fromEntries(counts(dataset).map(g=>[g.type,g.count]))},sync:{pending_count:reviewStatus==='pending'?1:0,jobs:[]},search:{mode:'keyword',state:'ready',model:{ready:false}},compatibility:{data_mode:'demo'}};break;
      case '/api/v1/ontology':value=schema(dataset);break;
      case '/api/v1/graph/overview/query':value={namespace,scope,stats_available:true,stats_error:null,total_nodes:selected.length,total_edges:selectedEdges.length,groups:counts(selected),relations:selectedEdges.map(e=>({source_type:e.source.type,target_type:e.target.type,relation_type:e.relation_type,count:1}))};break;
      case '/api/v1/graph/query':{
        let nodes=selected;if(body.focus_uri){const center=dataset.find(n=>n.uri===body.focus_uri);const neighbors=new Set(edges.filter(e=>e.source.uri===body.focus_uri||e.target.uri===body.focus_uri).flatMap(e=>[e.source.uri,e.target.uri]));nodes=center?[center,...selected.filter(n=>n.uri!==center.uri&&neighbors.has(n.uri))]:[];}
        const chosen=nodes.slice(0,body.node_limit||200),ids=new Set(chosen.map(n=>n.uri)),connections=selectedEdges.filter(e=>ids.has(e.source.uri)&&ids.has(e.target.uri));value=snapshot(chosen,connections,{total_nodes:nodes.length,total_edges:connections.length,truncated_nodes:nodes.length>chosen.length,focus_found:body.focus_uri?!!chosen.length:null});break;}
      case '/api/v1/graph/neighbors/query':{const center=dataset.find(n=>n.uri===body.center_uri);const extra=entity(60);value=snapshot(center?[center,small[1],extra]:[],center?[edge(60,center,extra)]:[],{center_uri:body.center_uri,total_neighbors:2,next_cursor:null,has_more:false,relation_counts:[{relation_type:'RELATED_TO',direction:'out',count:2}],focus_found:!!center});break;}
      case '/api/v1/graph/paths/query':{const a=dataset.find(n=>n.uri===body.source_uri),b=dataset.find(n=>n.uri===body.target_uri),connection=edge(100,a,b);value=snapshot([a,b],[connection],{paths:[{node_uris:[a.uri,b.uri],edge_ids:[connection.id]}],max_hops:6,truncated:false,timed_out:false});break;}
      case '/api/v1/ontology/entities/query':value=page(selected,body);break;
      case '/api/v1/ontology/relations/query':value=page(selectedEdges,body);break;
      case '/api/v1/memories/query':value=page(selected,body);break;
      case '/api/v1/memories/detail':value=dataset.find(n=>n.uri===body.uri)||(body.uri===entity(60).uri?entity(60):small[0]);break;
      case '/api/v1/memories/revisions':value={items:[]};break;
      case '/api/v1/reviews/query':{const matches=reviewStatus==='pending'&&(!body.q||JSON.stringify(batch).includes(body.q))?[batch]:[];value={...page(matches,body),total_pending:reviewStatus==='pending'?1:0};break;}
      case '/api/v1/reviews/approve':reviewStatus='approved';value={ok:true,receipt_id:'synthetic-receipt',disposition:'applied'};break;
      case '/api/v1/reviews/reject':reviewStatus='rejected';value={ok:true,receipt_id:'synthetic-receipt',disposition:'rejected'};break;
      default:
        if(url.pathname.endsWith('/approve')){if(slowApproval)await new Promise(resolve=>{releaseApproval=resolve;});reviewStatus='approved';value={ok:true,receipt_id:'synthetic-receipt',disposition:'applied'};}
        else if(url.pathname.endsWith('/reject')){reviewStatus='rejected';value={ok:true,receipt_id:'synthetic-receipt',disposition:'rejected'};}
        else value={items:[],total:0};
    }
    await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(value)});
  });
  await install(context);
  const p=await context.newPage();p.on('pageerror',e=>errors.push(e.message));
  try{
    await p.goto(root+'/#token=synthetic-acceptance-token');
    await p.getByText('全量实体图',{exact:false}).waitFor();
    await p.locator('.graph-node').first().waitFor();
    await p.waitForTimeout(900);
    assert.equal(await p.locator('.graph-node').count(),12);
    results.small_complete=true;
    await p.evaluate(fs.readFileSync(require.resolve('axe-core/axe.min.js',{paths:[path.resolve('console')]}),'utf8'));
    results.axe_desktop=await p.evaluate(async()=>{const r=await axe.run('.graph-workspace-v3',{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.map(n=>n.target)}));});
    const readCount=requests.filter(r=>r.path.includes('/graph/')).length;
    const surface=p.locator('.graph-canvas-surface'),box=await surface.boundingBox();
    await p.mouse.move(box.x+box.width/2,box.y+box.height/2);await p.mouse.wheel(0,-120);await p.waitForTimeout(120);
    assert.equal(requests.filter(r=>r.path.includes('/graph/')).length,readCount);
    results.zoom_no_data_change=true;
    await p.getByRole('button',{name:'切换检查器',exact:true}).click();
    await p.waitForTimeout(200);
    const world=()=>p.locator('.graph-world').getAttribute('transform');
    const before=await world();
    await p.locator('.graph-node').first().click();
    await p.waitForTimeout(100);assert.equal(await world(),before);
    results.selection_keeps_camera=true;
    await p.getByRole('button',{name:'以此探索',exact:true}).click();
    await p.getByRole('button',{name:'返回上个探索上下文'}).click();
    await p.waitForTimeout(150);assert.equal(await world(),before);
    results.navigation_restores_camera=true;
    await p.getByRole('button',{name:'展开 30 个邻居',exact:true}).click();
    await p.getByText('已加载 2 / 2',{exact:false}).waitFor();
    results.incremental_expansion=true;
    const expandedId='saved:'+entity(60).uri;
    const expandedNode=p.locator(`[data-node-id="${expandedId}"]`);
    await expandedNode.click();await expandedNode.press('p');
    await p.locator(`[data-node-id="${expandedId}"].is-pinned`).waitFor();
    await p.locator(`[data-node-id="saved:${small[0].uri}"]`).click();
    await p.getByRole('button',{name:'收起此节点的展开',exact:true}).click();
    await p.locator(`[data-node-id="${expandedId}"].is-pinned`).waitFor();
    await p.getByRole('button',{name:'本体',exact:true}).click();await p.getByRole('button',{name:'探索',exact:true}).click();
    await p.locator(`[data-node-id="${expandedId}"].is-pinned`).waitFor();results.pinned_collapse_lens_retention=true;
    await p.getByRole('button',{name:'撤销画布操作',exact:true}).click();
    assert.ok(await p.locator('.graph-node').count()>=12);
    results.collapse_undo=true;
    await p.getByRole('button',{name:'本体',exact:true}).click();
    await p.locator('.graph-node-schema').first().click();
    await p.getByRole('tab',{name:'属性',exact:true}).click();
    results.ontology_inspector=true;
    await p.getByRole('button',{name:'探索',exact:true}).click();
    await p.getByRole('button',{name:'列表',exact:true}).click();
    await p.getByRole('checkbox',{name:/多选 林舟$/}).check();
    await p.getByRole('checkbox',{name:/多选 阅读讨论会$/}).check();
    await p.getByRole('button',{name:'查询连接',exact:true}).click();
    await p.getByRole('heading',{name:'连接查询结果'}).waitFor();results.path_query=true;
    await p.getByRole('button',{name:'收起检查器',exact:true}).click();
    await p.getByRole('button',{name:'本体',exact:true}).click();await p.getByRole('button',{name:'探索',exact:true}).click();
    assert.equal(await p.getByRole('checkbox',{name:/多选 /,checked:true}).count(),2);
    assert.equal(await p.locator('.gw-inspector').count(),0);results.lens_preserves_multiselection=true;
    await p.getByRole('button',{name:'比较所选对象',exact:true}).click();await p.getByRole('dialog',{name:'并列核对'}).waitFor();
    await p.keyboard.press('Escape');results.compare=true;
    await p.getByRole('button',{name:'保存命名视图',exact:true}).click();
    await p.getByRole('textbox',{name:/视图名称/}).fill('合成验收视图');
    await p.getByRole('button',{name:'保存',exact:true}).click();
    await p.getByText('已保存“合成验收视图”。',{exact:true}).waitFor();
    await p.keyboard.press('Escape');results.named_view=true;
    const timelineReads=requests.filter(r=>r.path.includes('/graph/')).length;
    await p.getByRole('button',{name:'图谱显示工具',exact:true}).click();
    const timeline=p.locator('.evt-timeline');await timeline.waitFor();
    assert.match(await timeline.locator('.evt-selection').innerText(),/3 \/ 3/);
    const startBrush=timeline.locator('input[type="range"]').first(),endBrush=timeline.locator('input[type="range"]').nth(1);
    await startBrush.focus();await startBrush.press('End');
    await p.waitForFunction(()=>document.querySelector('.evt-selection')?.textContent.includes('2 / 3'));
    assert.equal(await timeline.getByLabel('起始日期',{exact:true}).inputValue(),'2026-10-23');
    await timeline.getByRole('checkbox',{name:/包含日期未知的事件/}).uncheck();
    await p.waitForFunction(()=>document.querySelector('.evt-selection')?.textContent.includes('1 / 3'));
    assert.equal(await p.getByRole('checkbox',{name:/多选 阅读讨论会$/}).count(),0);
    assert.equal(await p.getByRole('checkbox',{name:/多选 阅读讨论会 7$/}).count(),0);
    assert.equal(await p.getByRole('checkbox',{name:/多选 阅读讨论会 13$/}).count(),1);
    assert.equal(await p.getByRole('checkbox',{name:/多选 林舟$/}).count(),1);
    await p.screenshot({path:path.join(output,'timeline.png'),fullPage:true});
    await startBrush.focus();await startBrush.press('Home');await endBrush.focus();await endBrush.press('Home');
    await p.waitForFunction(()=>document.querySelector('.evt-dates input[type="date"]:last-child'));
    assert.equal(await timeline.getByLabel('结束日期',{exact:true}).inputValue(),'2026-10-20');
    assert.equal(await p.getByRole('checkbox',{name:/多选 阅读讨论会$/}).count(),1);
    assert.equal(await p.getByRole('checkbox',{name:/多选 阅读讨论会 13$/}).count(),0);
    await timeline.getByRole('checkbox',{name:/包含日期未知的事件/}).check();
    await p.waitForFunction(()=>document.querySelector('.evt-selection')?.textContent.includes('2 / 3'));
    await timeline.getByRole('button',{name:'显示全部',exact:true}).click();
    await p.waitForFunction(()=>document.querySelector('.evt-selection')?.textContent.includes('3 / 3'));
    assert.equal(requests.filter(r=>r.path.includes('/graph/')).length,timelineReads);
    results.local_timeline_brush=true;results.unknown_event_toggle=true;results.timeline_no_data_request=true;
    await p.getByRole('group',{name:'图谱工作视角'}).getByRole('button',{name:/审阅/}).click();
    await p.locator('.gw-review-item').first().click();
    await p.getByText('林舟（候选）',{exact:false}).first().waitFor();results.review_context=true;
    await p.getByRole('button',{name:'修改林舟（候选）',exact:true}).click();
    const reviewName=p.locator('.gw-inspector').getByRole('textbox',{name:'名称',exact:true});
    await reviewName.fill('林舟（409更正草稿）');
    await p.getByRole('textbox',{name:'更正说明',exact:true}).fill('409 后保留的人工核对依据');
    approvalConflict=true;await p.getByRole('button',{name:'保存更正并批准',exact:true}).click();
    await p.getByRole('button',{name:'读取最新记录并核对',exact:true}).waitFor();
    await p.getByText('记录或批次已发生变化，本次输入仍保留。',{exact:false}).waitFor();
    assert.equal(await reviewName.inputValue(),'林舟（409更正草稿）');
    assert.equal(await p.getByRole('textbox',{name:'更正说明',exact:true}).inputValue(),'409 后保留的人工核对依据');
    assert.equal(reviewStatus,'pending');
    await p.getByRole('button',{name:'关闭批次审阅',exact:true}).click();
    await p.getByRole('button',{name:'继续核对',exact:true}).click();
    assert.equal(await reviewName.inputValue(),'林舟（409更正草稿）');
    await p.getByRole('button',{name:'关闭批次审阅',exact:true}).click();await p.getByRole('button',{name:'放弃草稿并关闭',exact:true}).click();
    results.review_409_draft_preserved=true;
    await p.locator('.gw-review-item').first().click();
    await p.getByRole('button',{name:'拒绝批次',exact:true}).click();
    await p.getByRole('textbox',{name:'拒绝原因',exact:true}).fill('合成草稿，保留输入验证');
    await p.locator('.gw-lenses button').nth(1).evaluate(el=>el.click());assert.equal(await p.getByRole('textbox',{name:'拒绝原因',exact:true}).inputValue(),'合成草稿，保留输入验证');
    await p.getByRole('button',{name:'关闭批次审阅',exact:true}).click();
    await p.getByRole('heading',{name:'放弃未提交的审阅草稿？'}).waitFor();await p.getByRole('button',{name:'继续核对',exact:true}).click();
    results.review_draft_navigation_guard=true;
    await p.getByRole('button',{name:'返回核对',exact:true}).click();
    // No edited fields remain, but the reason still counts as a draft. Close explicitly.
    await p.getByRole('button',{name:'关闭批次审阅',exact:true}).click();await p.getByRole('button',{name:'放弃草稿并关闭',exact:true}).click();
    await p.locator('.gw-review-item').first().click();slowApproval=true;
    await p.getByRole('button',{name:'批准此批次',exact:true}).click();
    await p.getByRole('button',{name:'正在批准',exact:true}).waitFor();
    await p.locator('.gw-lenses button').nth(1).evaluate(el=>el.click());
    assert.equal(await p.locator('.gw-lenses button').nth(2).getAttribute('aria-pressed'),'true');
    releaseApproval();
    await p.getByText('已应用：服务器已确认这批内容写入',{exact:false}).waitFor();
    assert.equal(await p.locator('.gw-lenses button').nth(2).getAttribute('aria-pressed'),'true');
    await p.getByRole('button',{name:'下一项',exact:true}).waitFor();results.review_write_receipt_preserved=true;
    await p.getByRole('button',{name:'探索',exact:true}).click();await p.getByRole('button',{name:'图',exact:true}).click();
    await p.locator('.gw-stage').focus();
    await p.screenshot({path:path.join(output,'desktop.png'),fullPage:true});
    results.desktop_overflow=await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth);assert.equal(results.desktop_overflow,false);
    // A fresh context represents a whole-space graph beyond the raw budget.
    dataset=Array.from({length:10000},(_,i)=>entity(i));
    const big=await browser.newContext({viewport:{width:1440,height:1000}});await install(big);const q=await big.newPage();q.on('pageerror',e=>errors.push(e.message));
    await q.goto(root+'/#token=synthetic-acceptance-token');await q.getByText('全库聚合',{exact:false}).waitFor();
    assert.equal(await q.locator('.graph-node-aggregate').count(),6);results.ten_thousand_aggregated=true;
    await q.locator('.graph-node-aggregate').first().click();await q.getByText('进入此集合',{exact:true}).waitFor();await q.getByRole('button',{name:'下一页',exact:true}).click();
    results.aggregate_member_paging=true;await q.screenshot({path:path.join(output,'aggregate.png'),fullPage:true});
    const outside=dataset[9999],search=q.getByRole('textbox',{name:'搜索探索',exact:true});
    const graphReadsBeforeLocate=requests.filter(r=>r.path.includes('/graph/')).length;
    await search.fill(outside.name);await q.getByRole('button',{name:'提交定位搜索',exact:true}).click();
    await q.locator('.gw-search-results .gw-search-result').filter({hasText:outside.name}).click();
    await q.getByRole('heading',{name:outside.name,exact:true}).waitFor();
    assert.equal(await search.inputValue(),outside.name);
    assert.equal(await q.locator('.graph-node-aggregate').count(),6);
    assert.equal(requests.filter(r=>r.path.includes('/graph/')).length,graphReadsBeforeLocate);
    await q.getByRole('button',{name:'提交定位搜索',exact:true}).click();
    await q.locator('.gw-search-results .gw-search-result').filter({hasText:outside.name}).waitFor();
    await q.getByRole('button',{name:'作为当前范围筛选',exact:true}).click();
    await q.getByText('全量实体图 · 1 个实体',{exact:false}).waitFor();
    assert.equal(await q.locator('.graph-node').count(),1);assert.equal(await q.locator('.graph-node-aggregate').count(),0);
    assert.equal(await search.inputValue(),outside.name);
    assert.ok(requests.some(r=>r.path==='/api/v1/graph/query'&&r.body.q===outside.name));
    results.out_of_canvas_global_search=true;results.locate_preserves_search_and_range=true;results.explicit_search_filter=true;
    await q.getByRole('navigation',{name:'探索路径',exact:true}).getByRole('button',{name:'知识图谱',exact:true}).click();await q.getByText('全库聚合',{exact:false}).waitFor();
    await q.locator('.graph-node-aggregate').first().click();await q.getByText('进入此集合',{exact:true}).waitFor();
    await q.setViewportSize({width:390,height:844});await q.getByRole('button',{name:'收起检查器',exact:true}).click();await q.waitForTimeout(200);await q.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
    results.mobile_visible_nodes=await q.locator('.graph-node').evaluateAll(nodes=>{const r=document.querySelector('.graph-svg').getBoundingClientRect();return nodes.filter(n=>{const b=n.getBoundingClientRect();return b.right>r.left&&b.left<r.right&&b.bottom>r.top&&b.top<r.bottom;}).length;});
    assert.ok(results.mobile_visible_nodes>0,'Narrow viewport must retain visible graph nodes');
    assert.equal(await q.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);results.mobile_no_overflow=true;
    await q.setViewportSize({width:320,height:900});await q.evaluate(()=>document.documentElement.style.fontSize='200%');await q.screenshot({path:path.join(output,'text-200.png'),fullPage:true});
    results.text_200_no_overflow=await q.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);assert.equal(results.text_200_no_overflow,true);
    await q.evaluate(fs.readFileSync(require.resolve('axe-core/axe.min.js',{paths:[path.resolve('console')]}),'utf8'));
    results.axe_narrow=await q.evaluate(async()=>{const r=await axe.run('.graph-workspace-v3',{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.map(n=>n.target)}));});
    await q.emulateMedia({reducedMotion:'reduce'});results.reduced_motion=true;
    await big.close();
    assert.deepEqual(errors,[]);results.pageerrors=errors;
    results.private_post_inputs=requests.filter(r=>r.body&&Object.keys(r.body).length).every(r=>r.method==='POST');
    fs.writeFileSync(path.join(output,'browser-results.json'),JSON.stringify(results,null,2));process.stdout.write(JSON.stringify(results,null,2));
    if(interactive){
      dataset=small;reviewStatus='pending';slowApproval=false;approvalConflict=false;
      await p.reload();await p.locator('.graph-node').first().waitFor();
      process.stdout.write('\n合成验收完成。此浏览器窗口可继续交互；所有 API 都由虚构夹具响应，不连接真实记忆数据。关闭此浏览器窗口以结束。\n');
      await new Promise(resolve=>browser.once('disconnected',resolve));
    }
  }catch(error){if(releaseApproval)releaseApproval();await p.screenshot({path:path.join(output,'failure.png'),fullPage:true});fs.writeFileSync(path.join(output,'browser-failure.json'),JSON.stringify({error:String(error),results,pageerrors:errors,requests:requests.map(r=>r.path)},null,2));throw error;}
  finally{await browser.close();}
})();
