/* Full App ontology-edit acceptance with synthetic, intercepted API traffic.
 * Build/serve console first, then set KG_ONTOLOGY_BROWSER_URL if needed.
 * No request is allowed to reach a real memory service or database.
 */
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

const origin = (process.env.KG_ONTOLOGY_BROWSER_URL || "http://127.0.0.1:5173").replace(/\/$/, "");
const output = path.resolve(process.env.KG_ONTOLOGY_REPORT_DIR || "reports/ontology-editing");
const namespace = "urn:ontology-edit:synthetic:";
const scope = "current_namespace_active";
const types = ["person", "event", "preference", "decision", "location", "concept"];
const names = ["人物", "事件", "偏好", "决策", "地点", "概念"];
const relationKeys = ["HERMES_knows", "HERMES_participant", "HERMES_hasPreference", "HERMES_hasDecision", "HERMES_locatedAt", "LINKED_TO", "CAUSED_BY", "RELATED_TO", "owl_sameAs"];
const fields = {
  entity_types: ["label", "description"],
  properties: ["label", "description", "aliases", "required_for", "domain", "range"],
  relations: ["label", "description", "aliases", "domain", "range"],
};
const properties = [
  { key: "hermes_name", label: "名称", description: "合成名称属性", iri: "urn:synthetic:property:name", domain: ["concept", "event", "preference", "decision", "location"], range: ["string"], aliases: ["name"], required_for: ["concept", "event", "preference", "decision", "location"], managed: false, runtime_supported: true, value_shape: "scalar" },
  { key: "hermes_description", label: "说明", description: "合成内容说明", iri: "urn:synthetic:property:description", domain: ["any"], range: ["string"], aliases: ["description"], required_for: [], managed: false, runtime_supported: true, value_shape: "scalar" },
  { key: "hermes_revision", label: "版本", description: "系统版本", iri: "urn:synthetic:property:revision", domain: ["any"], range: ["integer"], aliases: [], required_for: [], managed: true, runtime_supported: true, value_shape: "scalar" },
];
const entity = (index) => ({
  uri: namespace + types[index] + "-synthetic",
  type: types[index], name: names[index] + "合成记录", description: index === 5 ? "" : "仅供本体编辑交互验收",
  version: 1, properties: { hermes_name: names[index] + "合成记录", ...(index === 5 ? {} : { hermes_description: "虚构内容" }) },
});
const entities = types.map((_, index) => entity(index));
const emptyPage = (body = {}) => ({ namespace, scope, items: [], total: 0, offset: body.offset || 0, limit: body.limit || 20, has_more: false });
function initialSchema() {
  return {
    namespace, scope, version: 0, read_only: false,
    editing: {
      scope: "namespace_overlay", can_edit: true, can_add_types: false,
      can_change_storage_ids: false, structural_policy: "tighten_only",
      entity_fields: fields.entity_types, property_fields: fields.properties,
      relation_fields: fields.relations,
      builtin_constraints: {
        properties: Object.fromEntries(properties.map((property) => [property.key, { domain: structuredClone(property.domain), range: structuredClone(property.range), required_for: structuredClone(property.required_for), aliases: structuredClone(property.aliases) }])),
        relations: Object.fromEntries(relationKeys.map((key) => [key, { domain: ["any"], range: ["any"], aliases: [] }])),
      },
      datatypes_by_property: Object.fromEntries(properties.map((property) => [property.key, property.range])),
      immutable_keys: ["key", "iri", "storage_label", "storage_type", "runtime_supported", "managed", "value_shape"],
      readonly_properties: ["hermes_revision"],
    },
    stats_available: true, stats_error: null,
    ontology: { iri: "urn:synthetic:ontology", label: "合成本体", description: "虚构验收定义", version: "1.1.0" },
    entity_types: types.map((key, index) => ({ key, label: names[index], description: names[index] + "合成定义", iri: "urn:synthetic:type:" + key, storage_label: key[0].toUpperCase() + key.slice(1), runtime_supported: true, count: 1, properties: structuredClone(properties.filter((property) => property.domain.includes("any") || property.domain.includes(key))) })),
    relations: relationKeys.map((key) => ({ key, label: key, description: "合成关系", iri: "urn:synthetic:relation:" + key, storage_type: key, aliases: [], domain: ["any"], range: ["any"], declared_domain: ["any"], declared_range: ["any"], count: 0, properties: [], runtime_supported: true })),
    properties: structuredClone(properties),
    stats: { entities: entities.length, relationships: 0, unmapped_relationships: 0 }, schema_warnings: [],
  };
}
function applyPatch(schema, patch) {
  const next = structuredClone(schema);
  for (const kind of Object.keys(fields)) {
    for (const [key, change] of Object.entries(patch[kind] || {})) {
      const target = next[kind].find((item) => item.key === key);
      assert.ok(target, "Patch references an unknown synthetic definition");
      assert.ok(Object.keys(change).every((field) => fields[kind].includes(field)));
      Object.assign(target, structuredClone(change));
      target.customized_fields = [...new Set([...(target.customized_fields || []), ...Object.keys(change)])];
    }
  }
  for (const type of next.entity_types)
    type.properties = structuredClone(next.properties.filter((property) => property.domain.includes("any") || property.domain.includes(type.key)));
  return next;
}

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, ...(process.env.KG_CHROME_PATH ? { executablePath: process.env.KG_CHROME_PATH } : {}) });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const errors = [], requests = [], results = { synthetic_only: true };
  let schema = initialSchema(), failPreview = false, failAuthPreview = false, blockedPreview = false, conflictPublish = false, holdPublish = false, releasePublish;
  let acceptedToken = "ontology-synthetic-token";
  await context.route("**/api/**", async (route) => {
    try {
      const request = route.request(), url = new URL(request.url());
      const body = request.postDataJSON() || {};
      assert.equal(url.search, "", "Private read/write inputs must stay out of API URLs");
      requests.push({ path: url.pathname, method: request.method(), body });
      const json = (value, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
      if (request.headers().authorization !== "Bearer " + acceptedToken)
        return json({ detail: "合成令牌已过期" }, 401);
      switch (url.pathname) {
        case "/api/v1/status": return json({ connected: true, memory: { searchable_entities: 6, counts: Object.fromEntries(types.map((type) => [type, 1])) }, sync: { pending_count: 0, jobs: [] }, search: { mode: "keyword", state: "ready", model: { ready: false } }, compatibility: { data_mode: "demo" } });
        case "/api/v1/ontology": return json(schema);
        case "/api/v1/ontology/validate": {
          assert.equal(request.method(), "POST");
          assert.equal(body.expected_namespace, namespace);
          assert.equal(body.expected_version, schema.version);
          assert.ok(body.patch && Object.keys(body.patch).length);
          if (failAuthPreview) {
            failAuthPreview = false;
            acceptedToken = "ontology-synthetic-reconnected-token";
            return json({ detail: "合成令牌已过期" }, 401);
          }
          if (failPreview) { failPreview = false; return json({ detail: "合成预览暂不可用" }, 503); }
          const changes = Object.entries(body.patch).flatMap(([kind, entries]) => Object.entries(entries).map(([key, change]) => ({ kind, key, fields: Object.keys(change) })));
          const candidate = applyPatch(schema, body.patch);
          const structural = [...candidate.properties, ...candidate.relations].some((item) => item.customized_fields?.some((field) => ["domain", "range", "required_for"].includes(field)));
          const affectedNodes = entities.filter((entity) => changes.some((change) =>
            change.kind === "entity_types" ? entity.type === change.key : change.kind === "properties" && entity.properties[change.key] != null,
          )).length;
          return json({ namespace, version: schema.version, expected_version: body.expected_version, can_publish: !blockedPreview, structural, changes, impact: { checked_nodes: structural ? 6 : 0, checked_relations: 0, affected_nodes: affectedNodes, affected_relations: 0, violations: blockedPreview ? [{ kind: "properties", key: "hermes_description", code: "required", count: 1 }] : [] }, schema: candidate });
        }
        case "/api/v1/ontology/publish": {
          assert.equal(request.method(), "POST");
          assert.equal(body.expected_namespace, namespace);
          assert.ok(typeof body.operation_id === "string" && body.operation_id.length >= 8);
          if (holdPublish) await new Promise((resolve) => { releasePublish = resolve; });
          if (conflictPublish) {
            conflictPublish = false;
            schema = { ...schema, version: schema.version + 1 };
            return json({ detail: "本体已由另一窗口修改，请读取最新版本。", code: "ontology_version_conflict", version: schema.version }, 409);
          }
          assert.equal(body.expected_version, schema.version);
          schema = applyPatch(schema, body.patch);
          schema.version++;
          return json({ ok: true, namespace, version: schema.version, operation_id: body.operation_id, idempotent: false, schema });
        }
        case "/api/v1/graph/overview/query": return json({ namespace, scope, stats_available: true, stats_error: null, total_nodes: 6, total_edges: 0, groups: types.map((type) => ({ type, count: 1 })), relations: [] });
        case "/api/v1/graph/query": return json({ namespace, scope, edge_scope: "returned_nodes", nodes: entities, edges: [], total_nodes: 6, total_edges: 0, truncated_nodes: false, truncated_edges: false, focus_found: null });
        case "/api/v1/ontology/entities/query":
        case "/api/v1/memories/query": return json({ ...emptyPage(body), items: entities, total: 6 });
        case "/api/v1/ontology/relations/query":
        case "/api/v1/memories/revisions": return json(emptyPage(body));
        case "/api/v1/memories/detail": return json(entities.find((item) => item.uri === body.uri) || entities[0]);
        case "/api/v1/reviews/query": return json({ ...emptyPage(body), total_pending: 0 });
        default: throw new Error("Unexpected synthetic API route: " + url.pathname);
      }
    } catch (error) {
      errors.push(error.message);
      await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "Synthetic acceptance contract mismatch" }) });
    }
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    await page.goto(origin + "/#token=ontology-synthetic-token");
    await page.locator(".graph-node").first().waitFor();
    await page.getByRole("button", { name: "本体", exact: true }).click();
    await page.locator('[data-node-id="schema:concept"]').click();
    await page.getByRole("button", { name: "编辑定义", exact: true }).waitFor();
    results.editor_entry_available = true;
    const definition = page.locator('[data-node-id="schema:concept"]');
    const world = () => page.locator(".graph-world").getAttribute("transform");
    const coordinates = () => page.locator(".graph-world > .graph-node").evaluateAll((nodes) =>
      Object.fromEntries(nodes.map((node) => [node.getAttribute("data-node-id"), node.getAttribute("transform")])),
    );
    const graphRequests = () => requests.filter((request) => request.path.includes("/graph/")).length;
    const publishes = () => requests.filter((request) => request.path.endsWith("/ontology/publish"));
    const validations = () => requests.filter((request) => request.path.endsWith("/ontology/validate"));
    const editor = page.getByRole("region", { name: "编辑本体定义", exact: true });
    const openEditor = async () => {
      await page.getByRole("button", { name: "编辑定义", exact: true }).click();
      await editor.getByRole("textbox", { name: "显示名称", exact: true }).waitFor();
    };
    const preview = async () => {
      const response = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/v1/ontology/validate");
      await editor.getByRole("button", { name: "预览影响", exact: true }).click();
      await response;
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    };
    const publish = () => editor.getByRole("button", { name: /发布/ });
    const cancel = () => editor.getByRole("button", { name: /^取消(?:编辑)?$/ });
    const area = await page.locator(".graph-canvas-surface").boundingBox();
    await page.mouse.move(area.x + area.width / 2, area.y + area.height / 2);
    await page.mouse.wheel(0, -90);
    await page.waitForTimeout(200);
    const cameraBefore = await world(), positionsBefore = await coordinates(), readsBefore = graphRequests();
    await openEditor();
    assert.equal(await editor.getByRole("textbox", { name: "兼容名称", exact: true }).count(), 0);
    await editor.getByRole("textbox", { name: "显示名称", exact: true }).fill("专题概念");
    await editor.getByRole("textbox", { name: /^说明(?:\s|$)/ }).fill("这是合成编辑后的定义说明。");
    results.capability_fields_only = true;
    const otherLens = await page.getByRole("button", { name: "探索", exact: true }).boundingBox();
    await page.mouse.click(otherLens.x + otherLens.width / 2, otherLens.y + otherLens.height / 2);
    assert.equal(await editor.isVisible(), true);
    assert.equal(await definition.getAttribute("aria-pressed"), "true");
    assert.equal(await editor.getByRole("textbox", { name: "显示名称", exact: true }).inputValue(), "专题概念");
    assert.equal(publishes().length, 0);
    results.draft_blocks_lens_navigation = true;
    await cancel().click();
    const discard = page.getByRole("dialog", { name: "放弃本体草稿？", exact: true });
    await discard.waitFor();
    await discard.getByRole("button", { name: "继续编辑", exact: true }).click();
    assert.equal(await editor.getByRole("textbox", { name: "显示名称", exact: true }).inputValue(), "专题概念");
    results.discard_confirmation_preserves_draft = true;
    await preview();
    assert.equal(await publish().isEnabled(), true);
    assert.match(await editor.locator(".oe-impact").innerText(), /影响当前空间\s*1\s*条实体/s);
    results.metadata_affected_count_visible = true;
    await editor.getByRole("textbox", { name: /^说明(?:\s|$)/ }).fill("这是修改预览之后的最新说明。");
    assert.equal(await publish().isDisabled(), true);
    await preview();
    await publish().waitFor({ state: "visible" });
    await publish().click();
    await editor.waitFor({ state: "hidden" });
    await page.waitForFunction(() => document.querySelector('[data-node-id="schema:concept"] .graph-node-label')?.textContent === "专题概念");
    assert.equal(schema.version, 1);
    assert.equal(await world(), cameraBefore);
    assert.deepEqual(await coordinates(), positionsBefore);
    assert.equal(graphRequests(), readsBefore);
    assert.equal(await definition.getAttribute("aria-pressed"), "true");
    assert.equal(publishes()[0].body.patch.entity_types.concept.description, "这是修改预览之后的最新说明。");
    results.preview_invalidated_on_edit = true;
    results.metadata_publish_keeps_camera_positions_selection = true;
    results.metadata_publish_zero_graph_requests = true;
    await page.screenshot({ path: path.join(output, "metadata-published.png"), fullPage: true });

    await openEditor();
    await editor.getByRole("textbox", { name: "显示名称", exact: true }).fill("领域术语");
    await editor.getByRole("textbox", { name: /^说明(?:\s|$)/ }).fill("409 后仍须保留的草稿说明。");
    await preview();
    conflictPublish = true;
    await publish().click();
    await editor.getByRole("button", { name: "读取最新版本并重新核对", exact: true }).waitFor();
    assert.equal(await editor.getByRole("textbox", { name: "显示名称", exact: true }).inputValue(), "领域术语");
    assert.equal(await editor.getByRole("textbox", { name: /^说明(?:\s|$)/ }).inputValue(), "409 后仍须保留的草稿说明。");
    assert.equal(await publish().isDisabled(), true);
    await page.screenshot({ path: path.join(output, "conflict-draft.png"), fullPage: true });
    const validationsBeforeRebase = validations().length;
    await editor.getByRole("button", { name: "读取最新版本并重新核对", exact: true }).click();
    await page.waitForTimeout(120);
    assert.equal(validations().length, validationsBeforeRebase);
    assert.equal(await editor.getByRole("textbox", { name: "显示名称", exact: true }).inputValue(), "领域术语");
    assert.equal(await editor.getByRole("textbox", { name: /^说明(?:\s|$)/ }).inputValue(), "409 后仍须保留的草稿说明。");
    assert.equal(await publish().isDisabled(), true);
    await preview();
    assert.equal(validations().at(-1).body.expected_version, 2);
    holdPublish = true;
    await publish().click();
    await page.waitForTimeout(100);
    assert.ok(releasePublish, "Controlled pending publication was reached");
    assert.equal(await publish().isDisabled(), true);
    assert.equal(await cancel().isDisabled(), true);
    const pendingPublishes = publishes().length;
    await page.mouse.click(otherLens.x + otherLens.width / 2, otherLens.y + otherLens.height / 2);
    await page.keyboard.press("Escape");
    assert.equal(await editor.isVisible(), true);
    assert.equal(publishes().length, pendingPublishes);
    results.pending_publish_blocks_close_and_navigation = true;
    holdPublish = false;
    releasePublish();
    releasePublish = undefined;
    await editor.waitFor({ state: "hidden" });
    assert.equal(schema.version, 3);
    assert.equal(await world(), cameraBefore);
    assert.equal(graphRequests(), readsBefore);
    results.conflict_preserves_draft_and_requires_repreview = true;

    await openEditor();
    await editor.getByRole("textbox", { name: /^说明(?:\s|$)/ }).fill("预览服务失败时仍然保留。");
    failPreview = true;
    await preview();
    await editor.getByRole("alert").waitFor();
    assert.match(await editor.getByRole("alert").innerText(), /输入已保留|暂不可用/);
    assert.equal(await publish().isDisabled(), true);
    assert.equal(await editor.getByRole("textbox", { name: /^说明(?:\s|$)/ }).inputValue(), "预览服务失败时仍然保留。");
    await preview();
    assert.equal(await publish().isEnabled(), true);
    results.preview_failure_retry_preserves_draft = true;
    await editor.getByRole("textbox", { name: /^说明(?:\s|$)/ }).fill("401 重新认证后仍须保留的输入。");
    failAuthPreview = true;
    await preview();
    await editor.getByRole("button", { name: "重新连接", exact: true }).waitFor();
    assert.equal(await editor.getByRole("textbox", { name: /^说明(?:\s|$)/ }).inputValue(), "401 重新认证后仍须保留的输入。");
    assert.equal(await publish().isDisabled(), true);
    await editor.getByRole("button", { name: "重新连接", exact: true }).click();
    const authentication = page.getByRole("dialog", { name: "连接你的记忆工作台", exact: true });
    await authentication.waitFor();
    await authentication.getByLabel("访问令牌", { exact: true }).fill(acceptedToken);
    await authentication.getByRole("button", { name: "连接记忆服务", exact: true }).click();
    await authentication.waitFor({ state: "hidden" });
    assert.equal(await editor.getByRole("textbox", { name: /^说明(?:\s|$)/ }).inputValue(), "401 重新认证后仍须保留的输入。");
    assert.equal(await publish().isDisabled(), true);
    await preview();
    assert.equal(await publish().isEnabled(), true);
    results.unauthorized_reconnect_preserves_editor_and_requires_preview = true;
    await cancel().click();
    await discard.getByRole("button", { name: "放弃草稿", exact: true }).click();
    await editor.waitFor({ state: "hidden" });
    await page.getByRole("tab", { name: "属性", exact: true }).click();
    const propertyCard = (key) => page.locator(".ov3-property").filter({ has: page.locator("code").filter({ hasText: new RegExp(`^${key}$`) }) });
    assert.equal(await propertyCard("hermes_revision").getByRole("button", { name: /编辑/ }).count(), 0);
    await propertyCard("hermes_description").getByRole("button", { name: /编辑/ }).click();
    await editor.waitFor();
    await editor.getByRole("group", { name: "在哪些类型中必填", exact: true }).getByRole("checkbox", { name: "概念", exact: true }).check();
    blockedPreview = true;
    const writesBeforeBlockedPreview = publishes().length;
    await preview();
    await editor.getByText("现有数据阻止发布", { exact: false }).waitFor();
    assert.equal(await publish().isDisabled(), true);
    assert.ok(validations().at(-1).body.patch.properties.hermes_description.required_for.includes("concept"));
    assert.equal(publishes().length, writesBeforeBlockedPreview);
    results.compatibility_preview_blocks_publish = true;
    results.managed_property_readonly = true;
    await page.screenshot({ path: path.join(output, "constraint-blocked.png"), fullPage: true });
    await page.addScriptTag({ path: require.resolve("axe-core/axe.min.js", { paths: [path.resolve("console")] }) });
    results.axe_desktop = await page.evaluate(async () => {
      const report = await axe.run(".graph-workspace-v3", { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] } });
      return report.violations.map((violation) => ({ id: violation.id, impact: violation.impact, targets: violation.nodes.map((node) => node.target) }));
    });
    assert.deepEqual(results.axe_desktop, []);
    const requiredTypes = () => editor.getByRole("group", { name: "在哪些类型中必填", exact: true });
    blockedPreview = false;
    await requiredTypes().getByRole("checkbox", { name: "概念", exact: true }).uncheck();
    await requiredTypes().getByRole("checkbox", { name: "人物", exact: true }).check();
    await preview();
    assert.equal(await publish().isEnabled(), true);
    await publish().click();
    await editor.waitFor({ state: "hidden" });
    await propertyCard("hermes_description").getByRole("button", { name: /编辑/ }).click();
    assert.equal(await requiredTypes().getByRole("checkbox", { name: "人物", exact: true }).isChecked(), true);
    assert.equal(await requiredTypes().getByRole("checkbox", { name: "人物", exact: true }).isEnabled(), true);
    await requiredTypes().getByRole("checkbox", { name: "人物", exact: true }).uncheck();
    await preview();
    await publish().click();
    await editor.waitFor({ state: "hidden" });
    assert.deepEqual(publishes().at(-1).body.patch.properties.hermes_description.required_for, []);
    assert.deepEqual(schema.properties.find((property) => property.key === "hermes_description").required_for, []);
    results.custom_required_constraint_can_be_withdrawn = true;
    await propertyCard("hermes_description").getByRole("button", { name: /编辑/ }).click();
    await requiredTypes().getByRole("checkbox", { name: "概念", exact: true }).check();
    blockedPreview = true;
    await preview();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(150);
    results.mobile_no_horizontal_overflow = await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1);
    assert.equal(results.mobile_no_horizontal_overflow, true);
    await editor.getByRole("heading", { name: "编辑本体定义", exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, "mobile-editor.png"), fullPage: true });
    await cancel().scrollIntoViewIfNeeded();
    results.mobile_actions_reachable = await cancel().evaluate((button) => {
      const box = button.getBoundingClientRect();
      return document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)?.closest("button") === button;
    });
    assert.equal(results.mobile_actions_reachable, true);
    const aboveActionBar = (element) => {
      element.scrollIntoView({ block: "center" });
      const bounds = element.getBoundingClientRect(),
        footer = element.closest(".oe-editor").querySelector(".oe-actions").getBoundingClientRect();
      return bounds.bottom <= footer.top + 1 && document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2) === element;
    };
    results.mobile_publish_reason_unobscured = await editor.getByRole("textbox", { name: /^发布说明/ }).evaluate(aboveActionBar);
    assert.equal(results.mobile_publish_reason_unobscured, true);
    await editor.locator(".oe-copy > summary").click();
    results.mobile_copy_draft_unobscured = await editor.getByRole("textbox", { name: "可复制的本体草稿", exact: true }).evaluate(aboveActionBar);
    assert.equal(results.mobile_copy_draft_unobscured, true);
    results.axe_mobile = await page.evaluate(async () => {
      const report = await axe.run(".graph-workspace-v3", { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] } });
      return report.violations.map((violation) => ({ id: violation.id, impact: violation.impact, targets: violation.nodes.map((node) => node.target) }));
    });
    assert.deepEqual(results.axe_mobile, []);
    await page.screenshot({ path: path.join(output, "mobile-editor-actions.png"), fullPage: true });
    schema = initialSchema();
    schema.read_only = true;
    delete schema.editing;
    delete schema.version;
    const legacy = await context.newPage();
    legacy.on("pageerror", (error) => errors.push(error.message));
    await legacy.goto(origin + "/#token=" + acceptedToken);
    await legacy.locator(".graph-node").first().waitFor();
    await legacy.getByRole("button", { name: "本体", exact: true }).click();
    await legacy.locator('[data-node-id="schema:concept"]').click();
    await legacy.locator(".ov3-inspector").waitFor();
    assert.equal(await legacy.getByRole("button", { name: "编辑定义", exact: true }).count(), 0);
    assert.ok(await legacy.locator(".graph-node-schema").count());
    results.legacy_viewer_only_service_supported = true;
    await legacy.close();
    schema = applyPatch(initialSchema(), { properties: { hermes_description: { required_for: ["person"] } } });
    schema.version = 1;
    delete schema.editing.builtin_constraints;
    const fallback = await context.newPage();
    fallback.on("pageerror", (error) => errors.push(error.message));
    await fallback.goto(origin + "/#token=" + acceptedToken);
    await fallback.locator(".graph-node").first().waitFor();
    await fallback.getByRole("button", { name: "本体", exact: true }).click();
    await fallback.locator('[data-node-id="schema:concept"]').click();
    await fallback.getByRole("tab", { name: "属性", exact: true }).click();
    await fallback.locator('[data-property-key="hermes_description"]').getByRole("button", { name: /编辑/ }).click();
    const fallbackRequired = fallback.getByRole("region", { name: "编辑本体定义", exact: true }).getByRole("group", { name: "在哪些类型中必填", exact: true }).getByRole("checkbox", { name: /人物/ });
    assert.equal(await fallbackRequired.isChecked(), true);
    assert.equal(await fallbackRequired.isDisabled(), true);
    results.optional_builtin_capability_fallback_preserves_effective_requirement = true;
    await fallback.close();
    results.errors = errors;
    fs.writeFileSync(path.join(output, "results.json"), JSON.stringify(results, null, 2));
    fs.writeFileSync(path.join(output, "requests.json"), JSON.stringify(requests, null, 2));
    console.log(JSON.stringify(results, null, 2));
    assert.deepEqual(errors, []);
  } catch (error) {
    await page.screenshot({ path: path.join(output, "failure.png"), fullPage: true }).catch(() => {});
    fs.writeFileSync(path.join(output, "failure.json"), JSON.stringify({ results, errors, error: error.message, requests }, null, 2));
    console.error(error);
    process.exitCode = 1;
  } finally {
    if (releasePublish) releasePublish();
    await browser.close();
  }
})();
