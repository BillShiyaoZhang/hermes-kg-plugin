#!/usr/bin/env node
/* Isolated UI QA. All sources and annotations below are synthetic, never gold. */
'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { spawnSync } = require('node:child_process');

const root = path.resolve(__dirname, '..');
const outputRoot = path.join(root, 'reports', 'retrieval-research', 'ui-test');
const runDir = path.join(outputRoot, new Date().toISOString().replace(/[:.]/g, '-'));
const python = process.env.RESEARCH_PYTHON || path.join(root, '.cache', 'retrieval-research', 'venv', 'Scripts', 'python.exe');
const playwrightModule = process.env.PLAYWRIGHT_MODULE || 'playwright';
const executablePath = process.env.KG_CHROME_PATH;
const { chromium } = require(playwrightModule);

function write(file, data) { fs.writeFileSync(file, JSON.stringify(data, null, 2) + '\n'); }
function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
  return JSON.stringify(value);
}
function cli(args, expected = 0) {
  const result = spawnSync(python, [path.join(root, 'scripts', 'research_retrieval.py'), ...args], { cwd: root, encoding: 'utf8', timeout: 60000 });
  assert.ifError(result.error);
  assert.equal(result.status, expected, result.stderr + result.stdout);
  return JSON.parse(result.stdout.trim());
}

async function main() {
  fs.mkdirSync(runDir, { recursive: true });
  const checks = [], pageErrors = [], consoleErrors = [], externalRequests = [];
  const result = { synthetic_only: true, purpose: 'synthetic UI test; not manual gold', personal_data_sent: 0, run_directory: runDir, checks };
  let browser;
  try {
    const namespace = 'urn:review-ui:kg:';
    const injection = '</script><script>window.__injected = 1</script><img src="https://invalid.example/source-canary" onerror="window.__injected = 2"><b>literal HTML</b>';
    const entities = [], relations = [];
    for (let i = 0; i < 60; i++) {
      const a = namespace + 'item-' + i + '-a', b = namespace + 'item-' + i + '-b';
      for (const [uri, suffix] of [[a, 'a'], [b, 'b']]) {
        entities.push({ uri, type: 'concept', name: `Canary item ${i} ${suffix}`, source_family: `ui-family-${i}`,
          props: { hermes_description: `Fabricated canary record ${i} ${suffix}. ` + injection, hermes_note: `Fabricated alternate detail ${i} ${suffix}.` } });
      }
      relations.push({ from: a, to: b, type: 'RELATED_TO', props: { note: 'Fabricated directed connection' } });
      relations.push({ from: b, to: a, type: 'RELATED_TO', props: { note: 'Fabricated reverse connection' } });
    }
    const snapshot = path.join(runDir, 'synthetic-snapshot.json');
    write(snapshot, { authorization: 'local_authorized_snapshot', synthetic_only: true, purpose: result.purpose, dataset: { namespace, entities, relations } });
    const fixtureDir = path.join(runDir, 'fixture');
    cli(['prepare-fixture', '--output', fixtureDir, '--real-snapshot', snapshot]);
    const fixture = JSON.parse(fs.readFileSync(path.join(fixtureDir, 'fixture.json'), 'utf8'));
    const q = fixture.queries.find(row => row.provenance === 'real_pending_human');
    assert.equal(q.id, 'real-001');
    const anchor = q.source_refs[0];
    const relation = relations.find(row => row.from === anchor);
    assert.ok(relation);
    const relationRef = `relation:${relation.from}|${relation.type}|${relation.to}`;
    checks.push('synthetic-only fixture prepared; no actual human review data loaded');

    browser = await chromium.launch({ ...(executablePath ? { executablePath } : {}), headless: true, args: ['--disable-background-networking', '--no-first-run'] });
    result.browser = { version: browser.version(), executable: executablePath, playwright_module: playwrightModule };
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
    const page = await context.newPage();
    page.on('pageerror', error => pageErrors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()); });
    page.on('request', request => { if (/^https?:/i.test(request.url())) externalRequests.push(request.url()); });
    await page.route(/^https?:/i, route => route.abort('blockedbyclient'));
    await page.goto(pathToFileURL(path.join(fixtureDir, 'human-review.html')).href, { waitUntil: 'load' });
    await page.locator('#cards > section').last().waitFor();
    assert.equal(await page.locator('#cards > section').count(), 100);
    assert.equal(await page.evaluate(() => window.__injected), undefined);
    assert.equal(await page.locator('img, b').count(), 0);
    const renderedSource = JSON.parse(await page.locator('#cards pre').first().textContent());
    assert.ok(renderedSource.props.hermes_description.includes(injection));
    const csp = await page.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute('content');
    assert.match(csp, /default-src 'none'/);
    assert.match(csp, /connect-src 'none'/);
    assert.match(csp, /img-src 'none'/);
    result.csp = csp;
    checks.push('100 cards rendered without executing HTML/script source text; CSP forbids network/images');

    // Deliberately omit reviewer/attestation first; exporting must be gated.
    await page.locator('#export').click();
    assert.match(await page.locator('#status').textContent(), /需要复核人和明确人工确认/);
    checks.push('export requires explicit reviewer and attestation');
    const card = page.locator('#cards > section').first();
    const question = '这一条授权来源明确记录了怎样的事实说明？（纯合成 UI 演练）';
    await card.locator('textarea').fill(question);
    await card.locator(':scope > select').selectOption('true');
    await card.locator('label').filter({ hasText: '证据 ' + anchor }).locator('select').selectOption('2');
    await card.locator('input[list="source-catalogue"]').fill(relationRef);
    await card.getByRole('button', { name: '增加证据 / 查看来源' }).click();
    const extraGrade = card.locator('label').filter({ hasText: '证据 ' + relationRef }).locator('select');
    await extraGrade.selectOption('1');
    assert.ok((await card.locator('pre').last().textContent()).includes('"from": "' + relation.from + '"'));
    assert.ok((await card.locator('pre').last().textContent()).includes('"to": "' + relation.to + '"'));
    await card.locator('input[type="checkbox"]').check();
    await page.locator('#reviewer').fill('SYNTHETIC UI AUTOMATION — not a human gold reviewer');
    await page.locator('#attest').check();
    assert.equal(await page.locator('#cards input[type="checkbox"]:checked').count(), 1);
    checks.push('edited one question, selected original evidence and an explicitly directed relation, marked only one synthetic row');

    const [download] = await Promise.all([page.waitForEvent('download'), page.locator('#export').click()]);
    const exportedPath = path.join(runDir, 'synthetic-reviewed-one.json');
    await download.saveAs(exportedPath);
    const exported = JSON.parse(fs.readFileSync(exportedPath, 'utf8'));
    assert.equal(exported.entries.length, 100);
    assert.equal(exported.entries.filter(entry => entry.status === 'reviewed').length, 1);
    assert.equal(exported.entries[0].question, question);
    assert.equal(exported.entries[0].relevance[anchor], 2);
    assert.equal(exported.entries[0].relevance[relationRef], 1);
    assert.deepEqual(exported.entries[0].sufficient_sets, [[anchor]]);
    const { submission_sha256, ...unsigned } = exported;
    assert.equal(submission_sha256, crypto.createHash('sha256').update(canonical(unsigned)).digest('hex'));
    const digestCheck = spawnSync(python, ['-c', "import sys;sys.path.insert(0,'plugins');from kg.research.common import read_json,digest;v=read_json(sys.argv[1]);v.pop('submission_sha256');print(digest(v))", exportedPath], { cwd: root, encoding: 'utf8', timeout: 30000 });
    assert.ifError(digestCheck.error);
    assert.equal(digestCheck.status, 0, digestCheck.stderr);
    assert.equal(digestCheck.stdout.trim(), submission_sha256);
    result.submission_sha256 = submission_sha256;
    checks.push('downloaded 100-row JSON; browser digest matches independent Node and Python canonical SHA-256');

    const validation = cli(['validate-labels', '--fixture-dir', fixtureDir, '--labels', exportedPath], 2);
    assert.equal(validation.ok, false);
    assert.equal(validation.reviewed, 1);
    const lockPath = path.join(runDir, 'must-not-exist.lock.json');
    const lock = cli(['validate-labels', '--fixture-dir', fixtureDir, '--labels', exportedPath, '--lock', lockPath], 1);
    assert.equal(lock.ok, false);
    assert.equal(fs.existsSync(lockPath), false);
    result.validation = { exit_code: 2, ok: validation.ok, reviewed: validation.reviewed, required: validation.required, issues: validation.issues.length };
    result.lock = { exit_code: 1, created: false, error: lock.error };
    checks.push('Python validates exactly 1/100; incomplete submission cannot create a gold lock');

    await page.reload({ waitUntil: 'load' });
    await page.locator('#resume').setInputFiles(exportedPath);
    await page.waitForFunction(value => document.querySelector('#cards textarea').value === value, question);
    const restored = page.locator('#cards > section').first();
    assert.equal(await restored.locator(':scope > select').inputValue(), 'true');
    assert.equal(await restored.locator('label').filter({ hasText: '证据 ' + anchor }).locator('select').inputValue(), '2');
    assert.equal(await restored.locator('label').filter({ hasText: '证据 ' + relationRef }).locator('select').inputValue(), '1');
    assert.equal(await page.locator('#cards input[type="checkbox"]:checked').count(), 1);
    assert.equal(await page.locator('#attest').isChecked(), false);
    assert.equal(await page.evaluate(() => window.__injected), undefined);
    await page.evaluate(() => { const label = document.createElement('p'); label.textContent = 'SYNTHETIC UI TEST — fabricated sources, 1/100 row only, not human gold'; label.style.cssText = 'background:#fff1b8;border:2px solid #725500;padding:8px'; document.querySelector('header').prepend(label); });
    await page.setViewportSize({ width: 1440, height: 1800 });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: path.join(runDir, 'synthetic-review-desktop.png') });
    checks.push('local file resume restores question, selected source, grades, and only-one-row status; global attestation must be renewed');

    assert.deepEqual(pageErrors, []);
    assert.deepEqual(consoleErrors, []);
    assert.deepEqual(externalRequests, []);
    result.page_errors = pageErrors;
    result.console_errors = consoleErrors;
    result.external_requests = externalRequests;
    result.ok = true;
    checks.push('zero JavaScript errors, console errors, or HTTP(S) requests');
    await context.close();
  } catch (error) {
    result.ok = false;
    result.error = error.stack || String(error);
    result.page_errors = pageErrors;
    result.console_errors = consoleErrors;
    result.external_requests = externalRequests;
    throw error;
  } finally {
    if (browser) await browser.close();
    write(path.join(runDir, 'results.json'), result);
    write(path.join(outputRoot, 'latest.json'), result);
    console.log(JSON.stringify({ ok: result.ok, synthetic_only: true, checks: checks.length, report: path.join(runDir, 'results.json') }));
  }
}

main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
