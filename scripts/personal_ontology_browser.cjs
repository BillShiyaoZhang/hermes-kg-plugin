/* Personal ontology presentation acceptance against the actual compiled profile.
 * Build/preview console first; every API request is fulfilled with synthetic data.
 * KG_PERSONAL_BROWSER_URL, KG_PERSONAL_REPORT_DIR, KG_PYTHON,
 * PLAYWRIGHT_MODULE and KG_CHROME_PATH may select existing local tooling.
 * No memory profile, database or external vocabulary service is contacted.
 */
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const root = path.resolve(__dirname, "..");
const origin = (process.env.KG_PERSONAL_BROWSER_URL || "http://127.0.0.1:5177").replace(/\/$/, "");
const output = path.resolve(process.env.KG_PERSONAL_REPORT_DIR || path.join(root, "reports/personal-ontology"));
const namespace = "urn:personal-browser:synthetic:";
const token = "personal-ontology-synthetic-token";
const python = process.env.KG_PYTHON || path.join(root, ".venv-console", "Scripts", "python.exe");
const schema = JSON.parse(execFileSync(python, ["-c", [
  "from kg.config import KGConfig",
  "from kg.ontology_profile import load_personal_profile",
  "from kg.ontology_presets import _assemble",
  "from kg.ontology_editing import effective_schema",
  "import json",
  `config=KGConfig(urn_prefix=${JSON.stringify(namespace)})`,
  "state,_,_=_assemble(config,{'version':0,'patch':{}},load_personal_profile())",
  "print(json.dumps(effective_schema(config,state),ensure_ascii=True))",
].join("\n")], { cwd: root, env: { ...process.env, PYTHONPATH: path.join(root, "plugins") }, encoding: "utf8" }));
schema.stats_available = true;
schema.stats = { entities: 0, relationships: 0, unmapped_relationships: 0 };
for (const kind of ["entity_types", "relations"]) for (const item of schema[kind]) item.count = 0;
const scope = "current_namespace_active";
const emptyPage = (body = {}) => ({ namespace, scope, items: [], total: 0, offset: body.offset || 0, limit: body.limit || 20, has_more: false });

(async () => {
  fs.mkdirSync(output, { recursive: true });
  fs.writeFileSync(path.join(output, "schema.fixture.json"), JSON.stringify(schema, null, 2));
  const browser = await chromium.launch({ headless: true, ...(process.env.KG_CHROME_PATH ? { executablePath: process.env.KG_CHROME_PATH } : {}) });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const errors = [], requests = [], results = {
    synthetic_only: true, browser: browser.version(), profile_digest: schema.profile_digest,
    personal_preset_digest: schema.presets.find((preset) => preset.id === "personal").digest,
  };
  await context.route("**/*", async (route) => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== origin) {
      errors.push("Unexpected external request: " + url.origin);
      return route.abort();
    }
    if (!url.pathname.startsWith("/api/")) return route.continue();
    try {
      const body = request.postDataJSON() || {};
      requests.push({ path: url.pathname, method: request.method(), body });
      assert.equal(request.headers().authorization, "Bearer " + token);
      assert.equal(url.search, "");
      let value;
      switch (url.pathname) {
        case "/api/v1/status": value = { connected: true, memory: { searchable_entities: 0, counts: {} }, sync: { pending_count: 0, jobs: [] }, search: { mode: "keyword", state: "ready", model: { ready: false } }, compatibility: { data_mode: "demo" } }; break;
        case "/api/v1/ontology": value = schema; break;
        case "/api/v1/graph/overview/query": value = { namespace, scope, stats_available: true, stats_error: null, total_nodes: 0, total_edges: 0, groups: [], relations: [] }; break;
        case "/api/v1/graph/query": value = { namespace, scope, edge_scope: "returned_nodes", nodes: [], edges: [], total_nodes: 0, total_edges: 0, truncated_nodes: false, truncated_edges: false, focus_found: null }; break;
        case "/api/v1/ontology/entities/query": case "/api/v1/ontology/relations/query": case "/api/v1/memories/query": case "/api/v1/memories/revisions": value = emptyPage(body); break;
        case "/api/v1/reviews/query": value = { ...emptyPage(body), total_pending: 0 }; break;
        default: throw new Error("Unmocked personal ontology API: " + url.pathname);
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(value) });
    } catch (error) {
      errors.push(error.message);
      await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "Synthetic acceptance contract mismatch" }) });
    }
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") errors.push("console: " + message.text()); });
  const inspector = page.locator(".ov3-inspector");
  const semantic = () => inspector.locator(":scope > .ov3-panel > .ov3-semantic");
  const noOverflow = () => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1);
  const choose = async (key) => {
    await page.locator(`[data-node-id="schema:${key}"]`).click();
    await inspector.waitFor();
  };
  const openSemantic = async () => {
    const details = semantic();
    if (await details.getAttribute("open") === null) await details.locator("summary").click();
    await details.scrollIntoViewIfNeeded();
    assert.equal(await details.getAttribute("open"), "");
    return details;
  };
  try {
    await page.goto(origin + "/#token=" + token);
    await page.getByRole("button", { name: "本体", exact: true }).click();
    await page.locator('[data-node-id="schema:person"]').waitFor();
    await choose("person");
    const person = await openSemantic();
    assert.match(await person.innerText(), /Schema\.org/);
    assert.match(await person.innerText(), /30\.1/);
    const publicLink = person.getByRole("link", { name: "https://schema.org/Person", exact: true });
    assert.equal(await publicLink.getAttribute("href"), "https://schema.org/Person");
    assert.equal(await publicLink.getAttribute("rel"), "noopener noreferrer");
    assert.equal(await person.locator("input,textarea,select").count(), 0);
    assert.equal(await inspector.locator("code").filter({ hasText: "urn:hermes:ontology:Person" }).count(), 1);
    results.desktop_standard_binding = true;
    results.desktop_no_overflow = await noOverflow(); assert.equal(results.desktop_no_overflow, true);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: path.join(output, "desktop-person-source.png"), fullPage: true });
    await page.evaluate(fs.readFileSync(require.resolve("axe-core/axe.min.js", { paths: [path.join(root, "console")] }), "utf8"));
    const axeRun = () => page.evaluate(async () => (await axe.run(".ov3-inspector", { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] } })).violations.map((item) => ({ id: item.id, impact: item.impact, targets: item.nodes.map((node) => node.target) })));
    results.axe_desktop = await axeRun(); assert.deepEqual(results.axe_desktop, []);

    await page.getByRole("button", { name: "切换检查器", exact: true }).click();
    await choose("x_personal_item");
    const itemSource = await openSemantic();
    assert.match(await itemSource.innerText(), /Dublin Core/);
    assert.match(await itemSource.innerText(), /dcmitype\/PhysicalObject/);
    results.physical_object_source = true;
    await page.getByRole("button", { name: "切换检查器", exact: true }).click();
    await choose("person");

    await inspector.getByRole("tab", { name: "属性", exact: true }).click();
    const propertySource = inspector.locator(".ov3-property .ov3-semantic").first();
    await propertySource.locator("summary").click();
    assert.match(await propertySource.innerText(), /https:\/\/schema\.org\//);
    results.property_binding = true;

    await inspector.getByRole("tab", { name: "允许关系", exact: true }).click();
    await inspector.getByRole("button", { name: "参与", exact: true }).click();
    await inspector.waitFor();
    await inspector.getByRole("tab", { name: "概览", exact: true }).click();
    const relation = await openSemantic();
    assert.match(await relation.innerText(), /schema.org\/attendee/);
    assert.match(await relation.innerText(), /语义投影时反向，保存的关系方向不变/);
    results.reverse_mapping_explained = true;

    await page.setViewportSize({ width: 390, height: 844 });
    await relation.scrollIntoViewIfNeeded();
    results.mobile_no_overflow = await noOverflow(); assert.equal(results.mobile_no_overflow, true);
    const visible = await relation.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.left >= -1 && rect.right <= innerWidth + 1;
    });
    assert.equal(visible, true); results.mobile_binding_reachable = true;
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: path.join(output, "mobile-participant-source.png"), fullPage: true });
    results.axe_mobile = await axeRun();
    assert.deepEqual(results.axe_mobile, []);
    results.web_links_safe = await inspector.locator(".ov3-semantic a").evaluateAll((links) => links.every((link) => /^https?:/.test(link.href) && link.rel.includes("noopener") && link.rel.includes("noreferrer")));
    assert.equal(results.web_links_safe, true);
    results.errors = errors; assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, "results.json"), JSON.stringify(results, null, 2));
    fs.writeFileSync(path.join(output, "requests.json"), JSON.stringify(requests, null, 2));
    for (const name of ["failure.json", "failure.png"]) fs.rmSync(path.join(output, name), { force: true });
    console.log(JSON.stringify(results, null, 2));
  } catch (error) {
    await page.screenshot({ path: path.join(output, "failure.png"), fullPage: true }).catch(() => {});
    fs.writeFileSync(path.join(output, "failure.json"), JSON.stringify({ results, errors, error: error.message, requests }, null, 2));
    console.error(error); process.exitCode = 1;
  } finally { await browser.close(); }
})();
