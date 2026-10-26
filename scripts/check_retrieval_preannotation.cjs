#!/usr/bin/env node
/* Fabricated file:// UI QA only. Never loads the private human-review inputs. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {spawnSync} = require('node:child_process');
const {pathToFileURL} = require('node:url');

const root = path.resolve(__dirname, '..');
const outputRoot = path.join(root, 'reports/retrieval-research/preannotation-ui-test');
const runDir = path.join(outputRoot, new Date().toISOString().replace(/[:.]/g, '-'));
const temp = path.join(root, '.cache/preannotation-ui-temp');
const python = process.env.RESEARCH_PYTHON || path.join(root, '.venv-console/Scripts/python.exe');
const playwrightModule = process.env.PLAYWRIGHT_MODULE || 'playwright';
const executablePath = process.env.KG_CHROME_PATH;
const {chromium} = require(playwrightModule);
function write(file,value) { fs.writeFileSync(file,JSON.stringify(value,null,2)+'\n'); }
function runPython(code,args=[]) {
  const child=spawnSync(python,['-X','utf8','-c',code,...args],{cwd:root,encoding:'utf8',timeout:60000,
    env:{...process.env,TEMP:temp,TMP:temp,PYTHONUTF8:'1'}});
  assert.ifError(child.error);
  assert.equal(child.status,0,child.stderr+child.stdout);
  return JSON.parse(child.stdout.trim());
}

async function main() {
  fs.mkdirSync(runDir,{recursive:true}); fs.mkdirSync(temp,{recursive:true});
  const checks=[], pageErrors=[],consoleErrors=[],externalRequests=[];
  const result={synthetic_only:true,purpose:'Fabricated machine preannotation UI, not human gold',
    private_source_files_loaded:0,external_generation_api_calls:0,checks};
  let browser;
  try {
    const info=runPython("import sys,json;sys.path.insert(0,'plugins');sys.path.insert(0,'plugins/kg/tests');from test_research_preannotation import write_browser_fixture;print(json.dumps(write_browser_fixture(sys.argv[1])))",[runDir]);
    assert.equal(info.synthetic_only,true);
    assert.equal(info.summary.machine_drafts,100);
    assert.equal(info.summary.human_reviewed,0);
    assert.equal(info.summary.human_gold_eligible,false);
    result.assembler_sha256=info.summary.assembler_sha256;
    checks.push('100 machine drafts assembled from 60 fabricated families; pending, zero human gold');
    browser=await chromium.launch({...(executablePath ? {executablePath} : {}),headless:true,
      args:['--disable-background-networking','--no-first-run'],env:{...process.env,TEMP:temp,TMP:temp}});
    result.browser={version:browser.version(),executable:executablePath};
    const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
    const page=await context.newPage();
    page.on('pageerror',error=>pageErrors.push(error.message));
    page.on('console',message=>{if(message.type()==='error')consoleErrors.push(message.text());});
    page.on('request',request=>{if(/^https?:/i.test(request.url()))externalRequests.push(request.url());});
    await page.route(/^https?:/i,route=>route.abort('blockedbyclient'));
    await page.goto(pathToFileURL(info.html).href,{waitUntil:'load'});
    await page.locator('#cards > section').last().waitFor();
    assert.equal(await page.locator('#cards > section').count(),100);
    assert.equal(await page.locator('#cards input[type="checkbox"]:checked').count(),0);
    assert.equal(await page.locator('#attest').isChecked(),false);
    assert.match(await page.locator('#counts').textContent(),/已人工勾选 0 \/ 100/);
    checks.push('all 100 row checkboxes and global attestation initially unchecked');
    assert.equal(await page.evaluate(()=>window.__preannotationInjected),undefined);
    assert.equal(await page.locator('img,b').count(),0);
    assert.ok((await page.locator('#cards pre').first().textContent()).includes('</script><script>'));
    const csp=await page.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute('content');
    assert.match(csp,/default-src 'none'/); assert.match(csp,/connect-src 'none'/); assert.match(csp,/img-src 'none'/);
    checks.push('source and machine-suggestion XSS rendered literally; scripts/images not created; network denied by CSP');
    const card=page.locator('#cards > section').first();
    assert.deepEqual(JSON.parse(await card.locator('textarea').nth(1).inputValue()),info.initial_sets);
    const edited='Fabricated independently edited canary question — UI exercise only';
    await card.locator('textarea').first().fill(edited);
    await page.locator('#filter').selectOption('content');
    assert.equal(await page.locator('#cards > section:not([hidden])').count(),2);
    assert.equal(await card.locator('textarea').first().inputValue(),edited);
    await page.locator('#filter').selectOption('source');
    assert.equal(await page.locator('#cards > section:not([hidden])').count(),10);
    assert.equal(await card.locator('textarea').first().inputValue(),edited);
    assert.deepEqual(JSON.parse(await card.locator('textarea').nth(1).inputValue()),info.initial_sets);
    await page.locator('#filter').selectOption('all');
    assert.equal(await page.locator('#cards > section:not([hidden])').count(),100);
    checks.push('content/source filters preserve question edits and two separate sufficient alternatives');

    // Evidence changes must revoke the row's old confirmation, including a
    // newly created grade control rather than only initial evidence widgets.
    await card.locator('input[type="checkbox"]').check();
    await card.locator('input[list="source-catalogue"]').fill(info.relation_ref);
    await card.getByRole('button',{name:'增加证据 / 查看来源'}).click();
    assert.equal(await card.locator('input[type="checkbox"]').isChecked(),false);
    const variantPreview=await card.locator('pre').last().textContent();
    assert.ok(variantPreview.includes('CANARY_VARIANT_FIRST'));
    assert.ok(variantPreview.includes('CANARY_VARIANT_SECOND'));
    await card.locator('input[type="checkbox"]').check();
    const extraGrade=card.locator('label').filter({hasText:'证据 '+info.relation_ref}).locator('select');
    await extraGrade.selectOption('1');
    assert.equal(await card.locator('input[type="checkbox"]').isChecked(),false);
    checks.push('parallel qualifier variants both visible; adding/editing dynamic evidence revokes old confirmation');

    await page.locator('#export').click();
    assert.match(await page.locator('#status').textContent(),/需要复核人和明确人工确认/);
    await page.locator('#reviewer').fill('SYNTHETIC AUTOMATION — NOT A HUMAN GOLD REVIEWER');
    await page.locator('#attest').check();
    await card.locator('input[type="checkbox"]').check();
    const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#export').click()]);
    const exportedPath=path.join(runDir,'synthetic-partial-only.json');
    await download.saveAs(exportedPath);
    const exported=JSON.parse(fs.readFileSync(exportedPath,'utf8'));
    assert.equal(exported.entries.length,100);
    assert.equal(exported.entries.filter(row=>row.status==='reviewed').length,1);
    const first=exported.entries.find(row=>row.id==='real-001');
    assert.equal(first.question,edited);
    assert.deepEqual(first.sufficient_sets,info.initial_sets);
    assert.equal(first.relevance[info.relation_ref],1);
    const pending=exported.entries.filter(row=>row.status==='pending');
    assert.equal(pending.length,99);
    assert.ok(pending.every(row=>row.review_origin==='machine'&&!row.reviewer&&!row.reviewed_at&&!row.attestation));
    checks.push('partial export retains distinct sufficient alternatives; 99 unconfirmed drafts stay machine/pending without human signatures');
    const validation=runPython("import sys,json;sys.path.insert(0,'plugins');from pathlib import Path;from kg.research.common import read_json,digest;from kg.research.fixtures import validate_labels;p=Path(sys.argv[1]);v=read_json(sys.argv[2]);h=v.pop('submission_sha256');assert digest(v)==h;r=validate_labels(read_json(p/'fixture.json'),read_json(p/'dataset.json'),v);print(json.dumps({'ok':r['ok'],'reviewed':r['reviewed'],'required':r['required']}))",[info.fixture_dir,exportedPath]);
    assert.equal(validation.ok,false); assert.equal(validation.reviewed,1); assert.equal(validation.required,100);
    result.partial_validation=validation;
    checks.push('independent Python validates partial synthetic submission as 1/100 and ineligible, digest matches');

    await page.reload({waitUntil:'load'});
    await page.locator('#resume').setInputFiles(exportedPath);
    await page.waitForFunction(value=>document.querySelector('#cards textarea').value===value,edited);
    assert.equal(await page.locator('#cards input[type="checkbox"]:checked').count(),1);
    assert.deepEqual(JSON.parse(await page.locator('#cards textarea').nth(1).inputValue()),info.initial_sets);
    assert.equal(await page.locator('#attest').isChecked(),false);
    await page.locator('#filter').selectOption('pending');
    assert.equal(await page.locator('#cards > section:not([hidden])').count(),99);
    await page.locator('#filter').selectOption('reviewed');
    assert.equal(await page.locator('#cards > section:not([hidden])').count(),1);
    await page.locator('#cards textarea').first().fill(edited+' changed after confirmation');
    assert.equal(await page.locator('#cards input[type="checkbox"]:checked').count(),0);
    await page.locator('#filter').selectOption('all');
    checks.push('resume keeps partial edits and sufficient sets; renewed global attestation required; later edits revoke row confirmation');

    await page.evaluate(()=>{const banner=document.createElement('p');banner.textContent='SYNTHETIC CANARY QA — FABRICATED DATA, NOT HUMAN GOLD';banner.style.cssText='background:#fff1b8;border:2px solid #725500;padding:8px';document.querySelector('header').prepend(banner);window.scrollTo(0,0);});
    await page.setViewportSize({width:1440,height:1800});
    await page.screenshot({path:path.join(runDir,'synthetic-preannotation-desktop.png')});
    assert.equal(await page.evaluate(()=>window.__preannotationInjected),undefined);
    assert.deepEqual(pageErrors,[]); assert.deepEqual(consoleErrors,[]); assert.deepEqual(externalRequests,[]);
    result.page_errors=pageErrors; result.console_errors=consoleErrors; result.external_requests=externalRequests;
    result.ok=true;
    checks.push('zero JavaScript/console errors and zero HTTP(S) requests across render, filters, export and resume');
    await context.close();
  } catch(error) {
    result.ok=false; result.error=error.stack||String(error);
    result.page_errors=pageErrors;result.console_errors=consoleErrors;result.external_requests=externalRequests;
    throw error;
  } finally {
    if(browser)await browser.close();
    result.qa_script_sha256=crypto.createHash('sha256').update(fs.readFileSync(__filename)).digest('hex');
    write(path.join(runDir,'results.json'),result);write(path.join(outputRoot,'latest.json'),result);
    console.log(JSON.stringify({ok:result.ok,synthetic_only:true,checks:checks.length,report:path.join(runDir,'results.json')}));
  }
}
main().catch(error=>{console.error(error.stack||error);process.exitCode=1;});
