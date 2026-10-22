/* Run against the explicitly synthetic Docker demo, never a personal profile.
 * PLAYWRIGHT_MODULE may point to an existing Playwright installation.
 */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('node:fs');
const path=require('node:path');

(async()=>{
  const root=process.env.KG_BROWSER_URL||'http://127.0.0.1:8765';
  const token=process.env.KG_BROWSER_TOKEN||'local-development-only';
  const output=path.resolve(process.env.KG_BROWSER_REPORT_DIR||'reports/browser');
  fs.mkdirSync(output,{recursive:true});
  const browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const results={};
  const auth={Authorization:'Bearer '+token};
  try{
    const settings=await page.request.get(root+'/api/v1/status',{headers:auth});
    if((await settings.json()).compatibility?.data_mode!=='demo')throw Error('Browser mutation test requires the synthetic demo environment');
    results.unauthenticated=(await page.request.get(root+'/api/v1/memories')).status();
    await page.goto(root+'/#token='+encodeURIComponent(token));
    await page.getByText('开发演示环境',{exact:false}).first().waitFor();
    await page.locator('.memory-row').filter({has:page.getByRole('heading',{name:'可控制的个人记忆',exact:true})}).click();
    await page.getByRole('button',{name:'纠正内容',exact:true}).click();
    await page.getByRole('textbox',{name:'记忆描述',exact:true}).fill('浏览器回归验证 '+Date.now()+'：可以查看来源、纠正、停用并恢复这条演示记忆。');
    await page.getByRole('textbox',{name:'更正原因',exact:true}).fill('开发环境端到端回归，全部为虚构内容。');
    await page.getByRole('button',{name:'保存更正',exact:true}).click();
    await page.getByText('更正已保存，修订记录已保留。',{exact:true}).waitFor();
    results.correction=true;
    const close=page.getByRole('button',{name:'关闭记忆详情',exact:true});
    if(await close.isVisible())await close.click();
    await page.locator('.memory-row').filter({has:page.getByRole('heading',{name:'可控制的个人记忆',exact:true})}).click();
    await page.getByRole('button',{name:'停用记忆',exact:true}).click();
    await page.getByRole('textbox',{name:'停用原因',exact:true}).fill('验证停用后在所有正常检索中排除。');
    await page.getByRole('button',{name:'确认停用',exact:true}).click();
    await page.getByText('记忆已停用，正常检索将不再使用这条内容。',{exact:true}).waitFor();
    const active=await page.request.post(root+'/api/v1/search',{headers:auth,data:{question:'可控制的个人记忆'}});
    results.archived_excluded=!(await active.json()).kg_results.some(r=>r.uri==='urn:hermes-demo:kg:concept-memory-control');
    if(await close.isVisible())await close.click();
    await page.getByRole('combobox',{name:'记忆状态'}).selectOption('archived');
    await page.locator('.memory-row').filter({has:page.getByRole('heading',{name:'可控制的个人记忆',exact:true})}).click();
    await page.getByRole('button',{name:'恢复使用',exact:true}).click();
    await page.getByText('记忆已恢复，可以继续检索。',{exact:true}).waitFor();
    results.restore=true;
    if(await close.isVisible())await close.click();
    await page.getByRole('combobox',{name:'记忆状态'}).selectOption('active');
    await page.locator('.memory-row').filter({has:page.getByRole('heading',{name:'林舟',exact:true})}).click();
    await page.getByRole('button',{name:/第一次产品方向讨论/}).waitFor();
    await page.screenshot({path:path.join(output,'person-context.png'),fullPage:true});
    results.person_context=true;
    if(await close.isVisible())await close.click();
    await page.screenshot({path:path.join(output,'desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
    results.mobile_no_horizontal_overflow=await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth);
    const current=await page.request.get(root+'/api/v1/memories/detail?uri=urn%3Ahermes-demo%3Akg%3Aconcept-memory-control',{headers:auth});
    const card=await current.json();
    results.version_conflict=(await page.request.post(root+'/api/v1/memories/correct',{headers:auth,data:{uri:card.uri,expected_version:0,operation_id:'browser-stale-'+Date.now(),changes:{name:'must not be written'}}})).status();
    results.page_errors=errors;
    if(results.unauthenticated!==401||!results.archived_excluded||results.version_conflict!==409||errors.length||!results.mobile_no_horizontal_overflow)throw Error('Browser acceptance failed');
    fs.writeFileSync(path.join(output,'results.json'),JSON.stringify(results,null,2));
    console.log(JSON.stringify(results));
  }catch(error){await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});console.error(error);process.exitCode=1;}
  finally{await browser.close();}
})();
