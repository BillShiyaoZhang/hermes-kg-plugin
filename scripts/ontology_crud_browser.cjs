/* Full App ontology CRUD acceptance with synthetic, intercepted API traffic.
 * Build/serve console first, then set KG_ONTOLOGY_CRUD_BROWSER_URL if needed.
 * No request is allowed to reach a real memory service or database.
 */
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

const origin = (process.env.KG_ONTOLOGY_CRUD_BROWSER_URL || "http://127.0.0.1:5173").replace(/\/$/, "");
const output = path.resolve(process.env.KG_ONTOLOGY_CRUD_REPORT_DIR || "reports/ontology-crud");
const namespace = "urn:ontology-crud:synthetic:";
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

function crudSchema() {
  const schema = initialSchema();
  Object.assign(schema.editing, {
    can_add_types: true, can_add_properties: true, can_add_relations: true,
    can_manage_lifecycle: true, create_datatypes: ["string", "integer", "double", "boolean", "dateTime"],
    create_value_shapes: ["scalar", "array"],
    custom_key_patterns: { entity_types: "x_[a-z][a-z0-9_]{0,47}", properties: "x_[a-z][a-z0-9_]{0,47}", relations: "X_[A-Z][A-Z0-9_]{0,47}" },
  });
  for (const kind of Object.keys(fields)) for (const item of schema[kind]) {
    item.custom = false; item.active = true;
    item.allowed_actions = item.managed ? [] : ["update", "deactivate"];
  }
  return schema;
}
function mutate(schema, operations = []) {
  const next = structuredClone(schema);
  for (const operation of operations) {
    const { kind, key, action, definition } = operation;
    assert.ok(Object.keys(fields).includes(kind));
    const existing = next[kind].find((item) => item.key === key);
    if (action === "create") {
      assert.equal(existing, undefined);
      const created = { ...definition, key, iri: `urn:synthetic:${kind}:${key}`, custom: true, active: true, runtime_supported: true, count: 0, properties: [], customized_fields: ["label", "description"], allowed_actions: ["update", "deactivate", "delete"] };
      if (kind === "entity_types") created.storage_label = key;
      else if (kind === "properties") Object.assign(created, { managed: false, required_for: definition.required_for || [], aliases: definition.aliases || [], value_shape: definition.value_shape || "scalar" });
      else Object.assign(created, { storage_type: key, declared_domain: definition.domain, declared_range: definition.range, aliases: definition.aliases || [] });
      next[kind].push(created);
    } else {
      assert.ok(existing);
      assert.ok(existing.allowed_actions.includes(action));
      if (action === "delete") { assert.equal(existing.custom, true); next[kind] = next[kind].filter((item) => item.key !== key); }
      else { existing.active = action === "restore"; existing.allowed_actions = ["update", existing.active ? "deactivate" : "restore", ...(existing.custom ? ["delete"] : [])]; }
    }
  }
  for (const item of next.entity_types)
    item.properties = structuredClone(next.properties.filter((property) => property.domain.includes("any") || property.domain.includes(item.key)));
  return next;
}

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, ...(process.env.KG_CHROME_PATH ? { executablePath: process.env.KG_CHROME_PATH } : {}) });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const errors = [], requests = [], results = { synthetic_only: true };
  let schema = crudSchema(), sequence = 0, currentPreview, conflictNext = false, failStatistics = false;
  const references = (kind, key) => ({ kind, key, label: schema[kind].find((item) => item.key === key)?.label || key,
    active_nodes: key === "x_project" ? 2 : 0, archived_nodes: key === "x_project" ? 1 : 0,
    active_relations: 0, archived_relations: 0, pending_reviews: key === "x_project" ? 1 : 0,
    definition_references: key === "x_project" ? 2 : 0, history_revisions: key === "x_project" ? 1 : 0,
    reference_count: key === "x_project" ? 7 : 0 });
  const requestKey = (body) => JSON.stringify([body.expected_namespace, body.expected_version, body.patch, body.operations]);
  await context.route("**/api/**", async (route) => {
    try {
      const request = route.request(), url = new URL(request.url()), body = request.postDataJSON() || {};
      requests.push({ path: url.pathname, method: request.method(), body });
      assert.equal(url.search, "");
      assert.equal(request.headers().authorization, "Bearer ontology-crud-synthetic-token");
      const json = (value, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
      switch (url.pathname) {
        case "/api/v1/status": return json({ connected: true, memory: { searchable_entities: 6, counts: Object.fromEntries(types.map((type) => [type, 1])) }, sync: { pending_count: 0, jobs: [] }, search: { mode: "keyword", state: "ready", model: { ready: false } }, compatibility: { data_mode: "demo" } });
        case "/api/v1/ontology": return json(schema);
        case "/api/v1/ontology/validate": {
          assert.equal(body.expected_namespace, namespace); assert.equal(body.expected_version, schema.version);
          if (failStatistics) { failStatistics = false; return json({ detail: "引用统计不可用，未执行操作。" }, 503); }
          const operation = body.operations?.[0];
          const impact = operation ? references(operation.kind, operation.key) : null;
          const blocked = operation?.action === "delete" && impact.reference_count > 0;
          const high = ["delete", "deactivate"].includes(operation?.action);
          const candidate = blocked ? structuredClone(schema) : mutate(applyPatch(schema, body.patch || {}), body.operations);
          const risk = { level: high ? "high" : ["create", "restore"].includes(operation?.action) ? "medium" : "low", reasons: [high ? "请核对历史和待审引用；不会级联删除事实。" : "仅修改当前空间的定义。"], requires_confirmation: high,
            ...(high ? { confirmation_phrase: `${operation.action === "delete" ? "删除" : "停用"} ${impact.label}`, confirmation_token: `synthetic-preview-${++sequence}` } : {}) };
          currentPreview = { key: requestKey(body), risk, can_publish: !blocked };
          return json({ namespace, version: schema.version, expected_version: schema.version, can_publish: !blocked, structural: !!operation, risk,
            changes: operation ? [{ kind: operation.kind, key: operation.key, fields: [operation.action] }] : Object.entries(body.patch).flatMap(([kind, entries]) => Object.entries(entries).map(([key, value]) => ({ kind, key, fields: Object.keys(value) }))),
            impact: { checked_nodes: 6, checked_relations: 0, affected_nodes: impact?.active_nodes || 0, affected_relations: 0, definitions: impact ? [impact] : [], violations: blocked ? [{ kind: operation.kind, key: operation.key, code: "definition_in_use", count: impact.reference_count }] : [] }, schema: candidate });
        }
        case "/api/v1/ontology/publish": {
          assert.equal(body.expected_namespace, namespace); assert.equal(body.expected_version, schema.version);
          assert.ok(currentPreview?.can_publish); assert.equal(requestKey(body), currentPreview.key);
          assert.ok(body.operation_id?.length >= 8);
          if (currentPreview.risk.requires_confirmation) {
            assert.equal(body.confirmation, currentPreview.risk.confirmation_phrase);
            assert.equal(body.confirmation_token, currentPreview.risk.confirmation_token);
          }
          if (conflictNext) { conflictNext = false; schema.version++; return json({ detail: "合成版本冲突，未应用。", code: "ontology_version_conflict", version: schema.version }, 409); }
          schema = mutate(applyPatch(schema, body.patch || {}), body.operations); schema.version++;
          return json({ ok: true, namespace, version: schema.version, operation_id: body.operation_id, idempotent: false, schema });
        }
        case "/api/v1/graph/overview/query": return json({ namespace, scope, stats_available: true, stats_error: null, total_nodes: 6, total_edges: 0, groups: types.map((type) => ({ type, count: 1 })), relations: [] });
        case "/api/v1/graph/query": return json({ namespace, scope, edge_scope: "returned_nodes", nodes: entities, edges: [], total_nodes: 6, total_edges: 0, truncated_nodes: false, truncated_edges: false, focus_found: null });
        case "/api/v1/ontology/entities/query": case "/api/v1/memories/query": return json({ ...emptyPage(body), items: entities, total: entities.length });
        case "/api/v1/ontology/relations/query": case "/api/v1/memories/revisions": return json(emptyPage(body));
        case "/api/v1/memories/detail": return json(entities.find((item) => item.uri === body.uri) || entities[0]);
        case "/api/v1/reviews/query": return json({ ...emptyPage(body), total_pending: 0 });
        default: throw new Error("Unmocked CRUD API: " + url.pathname);
      }
    } catch (error) { errors.push(error.message); await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "Synthetic CRUD contract mismatch" }) }); }
  });
  const page = await context.newPage(); page.on("pageerror", (error) => errors.push(error.message));
  try {
    await page.goto(origin + "/#token=ontology-crud-synthetic-token"); await page.locator(".graph-node").first().waitFor();
    await page.getByRole("button", { name: "本体", exact: true }).click();
    await page.getByRole("button", { name: "管理本体", exact: true }).click();
    const manager = page.getByRole("region", { name: "本体定义管理", exact: true });
    const category = async (name) => manager.getByRole("group", { name: "定义类别" }).getByRole("button", { name: new RegExp(`^${name}`) }).click();
    const row = (key) => manager.locator(".oc-definition").filter({ has: page.locator("dd").filter({ hasText: new RegExp(`^${key}$`) }) });
    const mutation = () => page.locator(".oc-mutation");
    const submit = () => mutation().locator(".oe-actions > div > button").last();
    const preview = async (region = mutation()) => {
      const response = page.waitForResponse((response) => new URL(response.url()).pathname.endsWith("/ontology/validate"));
      await region.getByRole("button", { name: "预览影响", exact: true }).click(); await response;
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    };
    const confirm = async () => {
      assert.equal(await submit().isDisabled(), true);
      const input = mutation().getByRole("textbox", { name: /^输入“/ });
      await input.fill("错误确认"); assert.equal(await submit().isDisabled(), true);
      await input.fill(currentPreview.risk.confirmation_phrase); assert.equal(await submit().isEnabled(), true);
      assert.equal(await input.evaluate((element) => {
        element.scrollIntoView({ block: "center" });
        const rect = element.getBoundingClientRect(), footer = element.closest(".oe-editor").querySelector(".oe-actions").getBoundingClientRect();
        return rect.bottom <= footer.top + 1 && document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2) === element;
      }), true);
    };
    const publish = async () => { await submit().click(); await manager.waitFor(); };
    const create = async (kindName, key, label) => {
      await category(kindName); await manager.getByRole("button", { name: `新建${kindName}`, exact: true }).click();
      await mutation().getByRole("textbox", { name: "显示名称", exact: true }).fill(label);
      await mutation().getByRole("textbox", { name: /^固定标识/ }).fill(key);
      await mutation().getByRole("textbox", { name: "说明", exact: true }).fill("用于CRUD验收的虚构定义。");
      await preview(); assert.equal(currentPreview.risk.level, "medium"); await publish(); await row(key).waitFor();
    };
    assert.equal(await row("person").getByRole("button", { name: "删除", exact: true }).count(), 0);
    results.builtin_delete_absent = true;
    await create("类型", "x_project", "合成项目");
    const typeDirectory = page.getByRole("group", { name: "实体类型", exact: true });
    assert.equal(await typeDirectory.getByRole("checkbox", { name: /合成项目/ }).isChecked(), true);
    results.all_types_scope_includes_created_type = true;
    await typeDirectory.getByRole("checkbox", { name: /事件/ }).uncheck();
    await create("属性", "x_priority", "项目优先级");
    await create("关系", "X_WORKS_ON", "负责项目");
    results.create_type_property_relation = true;
    await category("类型"); await row("x_project").getByRole("button", { name: "编辑", exact: true }).click();
    const editor = page.getByRole("region", { name: "编辑本体定义", exact: true });
    await editor.getByRole("textbox", { name: "显示名称", exact: true }).fill("验收项目");
    await preview(editor); await editor.getByRole("button", { name: "发布本体", exact: true }).click(); await manager.waitFor();
    assert.match(await row("x_project").innerText(), /验收项目/); results.read_and_update_custom_definition = true;
    await row("x_project").getByRole("button", { name: "删除", exact: true }).click(); await preview();
    assert.equal(await submit().isDisabled(), true); assert.match(await mutation().innerText(), /历史实体/);
    await mutation().getByRole("button", { name: "保留历史，改为停用", exact: true }).click(); await preview();
    await confirm(); await page.screenshot({ path: path.join(output, "high-risk-deactivate.png"), fullPage: true }); await publish();
    assert.equal(schema.entity_types.find((item) => item.key === "x_project").active, false);
    assert.match(await row("x_project").innerText(), /已停用/); results.used_delete_blocked_alternative_deactivation_confirmed = true;
    await manager.getByRole("button", { name: "返回所选内容", exact: true }).click();
    await page.locator('[data-node-id="schema:x_project"].is-disabled').waitFor();
    assert.match(await page.locator('[data-node-id="schema:x_project"]').textContent(), /已停用/); results.disabled_definition_remains_in_graph = true;
    await page.getByRole("button", { name: "管理本体", exact: true }).click();
    await row("x_project").getByRole("button", { name: "恢复", exact: true }).click(); await preview(); await publish();
    assert.equal(schema.entity_types.find((item) => item.key === "x_project").active, true); results.restore_custom_definition = true;
    await manager.getByRole("button", { name: "新建类型", exact: true }).click();
    await mutation().getByRole("textbox", { name: "显示名称", exact: true }).fill("空定义");
    await mutation().getByRole("textbox", { name: /^固定标识/ }).fill("x_spare");
    await preview(); conflictNext = true; await submit().click();
    await mutation().getByRole("button", { name: "读取最新版本并重新核对", exact: true }).click();
    assert.equal(await mutation().getByRole("textbox", { name: "显示名称", exact: true }).inputValue(), "空定义");
    assert.equal(await submit().isDisabled(), true); await preview(); await publish(); results.conflict_preserves_create_draft = true;
    assert.equal(await typeDirectory.getByRole("checkbox", { name: /空定义/ }).isChecked(), false);
    results.explicit_type_subset_does_not_expand = true;
    await manager.getByRole("button", { name: "返回所选内容", exact: true }).click();
    await page.getByRole("button", { name: /^类型：/ }).click();
    await page.getByRole("button", { name: "适合全部可见节点", exact: true }).click();
    await page.locator('[data-node-id="schema:x_spare"]').click();
    await page.waitForFunction((namespace) => {
      const saved = JSON.parse(sessionStorage.getItem(`kg-v3-session:${namespace}`) || "{}");
      return saved.views?.ontology?.primary === "schema:x_spare";
    }, namespace);
    await page.getByRole("button", { name: "管理本体", exact: true }).click();
    await row("x_spare").getByRole("button", { name: "删除", exact: true }).click(); await preview(); await confirm(); await publish();
    assert.equal(await row("x_spare").count(), 0); results.unreferenced_custom_deleted_with_bound_confirmation = true;
    await page.waitForFunction((namespace) => {
      const saved = JSON.parse(sessionStorage.getItem(`kg-v3-session:${namespace}`) || "{}");
      return saved.views?.ontology && saved.views.ontology.primary !== "schema:x_spare" && !saved.views.ontology.selected.includes("schema:x_spare");
    }, namespace);
    results.deleted_definition_selection_cleared = true;
    await row("x_project").getByRole("button", { name: "停用", exact: true }).click();
    failStatistics = true; const writes = requests.filter((item) => item.path.endsWith("/publish")).length;
    await preview(); await mutation().getByRole("alert").waitFor(); assert.equal(await submit().isDisabled(), true);
    assert.equal(requests.filter((item) => item.path.endsWith("/publish")).length, writes); results.statistics_failure_blocks_mutation = true;
    await preview(); await page.evaluate(fs.readFileSync(require.resolve("axe-core/axe.min.js", { paths: [path.resolve("console")] }), "utf8"));
    const axeRun = () => page.evaluate(async () => (await axe.run(".graph-workspace-v3", { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] } })).violations.map((item) => ({ id: item.id, impact: item.impact, targets: item.nodes.map((node) => node.target) })));
    results.axe_desktop = await axeRun(); assert.deepEqual(results.axe_desktop, []);
    await page.setViewportSize({ width: 390, height: 844 }); await mutation().getByRole("heading", { level: 2 }).scrollIntoViewIfNeeded();
    results.mobile_no_overflow = await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1); assert.equal(results.mobile_no_overflow, true);
    await mutation().getByRole("textbox", { name: /^输入“/ }).scrollIntoViewIfNeeded();
    const confirmationInput = mutation().getByRole("textbox", { name: /^输入“/ });
    await confirmationInput.evaluate((element) => element.scrollIntoView({ block: "center" }));
    results.mobile_confirmation_reachable = await confirmationInput.evaluate((element) => {
      const rect = element.getBoundingClientRect(), footer = element.closest(".oe-editor").querySelector(".oe-actions").getBoundingClientRect();
      return rect.bottom <= footer.top + 1 && document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2) === element;
    });
    assert.equal(results.mobile_confirmation_reachable, true);
    results.axe_mobile = await axeRun(); assert.deepEqual(results.axe_mobile, []);
    await page.screenshot({ path: path.join(output, "mobile-crud-confirmation.png"), fullPage: true });
    await mutation().getByRole("textbox", { name: /^操作说明/ }).fill("窄屏下保留的操作草稿。");
    await page.keyboard.press("Escape");
    const discard = page.getByRole("dialog", { name: "放弃未提交的输入？", exact: true });
    await discard.waitFor(); await discard.getByRole("button", { name: "继续编辑", exact: true }).click();
    assert.equal(await mutation().getByRole("textbox", { name: /^操作说明/ }).inputValue(), "窄屏下保留的操作草稿。");
    results.mobile_escape_respects_draft_confirmation = true;
    const oldSession = await page.evaluate((namespace) => JSON.parse(sessionStorage.getItem(`kg-v3-session:${namespace}`)), namespace);
    oldSession.lens = "ontology";
    oldSession.views.ontology.types = ["person", "x_deleted_legacy"];
    const legacy = await context.newPage();
    legacy.on("pageerror", (error) => errors.push(error.message));
    await legacy.addInitScript(({ namespace, saved }) => sessionStorage.setItem(`kg-v3-session:${namespace}`, JSON.stringify(saved)), { namespace, saved: oldSession });
    const beforeLegacyRequests = requests.length;
    await legacy.goto(origin + "/#token=ontology-crud-synthetic-token");
    await legacy.locator('[data-node-id="schema:person"]').waitFor();
    await legacy.waitForFunction((namespace) => {
      const saved = JSON.parse(sessionStorage.getItem(`kg-v3-session:${namespace}`) || "{}");
      return saved.views?.ontology && !saved.views.ontology.types.includes("x_deleted_legacy");
    }, namespace);
    assert.ok(requests.slice(beforeLegacyRequests).filter((request) => request.path.includes("/graph/")).every((request) => !request.body.types?.includes("x_deleted_legacy")));
    results.legacy_session_drops_deleted_type_before_query = true;
    const namedProbe = "legacy-named-view-synthetic-probe";
    const namedState = { ...structuredClone(oldSession.views.explore),
      types: ["person", "x_deleted_named"], relations: ["HERMES_knows", "X_DELETED_NAMED"],
      filterQuery: namedProbe, primary: null, selected: [], edge: null, focusUri: null, spotlight: null,
      expansions: {}, path: null, representation: "graph" };
    await legacy.evaluate(({ namespace, state }) => {
      localStorage.setItem(`hermes:graph:views:v1:${encodeURIComponent(namespace)}`, JSON.stringify({
        version: 1, namespace, views: [{ id: "legacy-deleted-definition-view", name: "旧定义筛选验收",
          createdAt: "2026-10-24T00:00:00.000Z", lens: "explore", state, seeds: [] }],
      }));
    }, { namespace, state: namedState });
    await legacy.getByRole("button", { name: "保存命名视图", exact: true }).click();
    const namedDialog = legacy.getByRole("dialog", { name: "命名视图", exact: true });
    const savedRow = namedDialog.locator(".gw-view-list > li").filter({ hasText: "旧定义筛选验收" });
    const beforeNamedRequests = requests.length;
    const restoredQuery = legacy.waitForResponse((response) => new URL(response.url()).pathname === "/api/v1/graph/query" && response.request().postDataJSON()?.q === namedProbe);
    await savedRow.getByRole("button", { name: "加载", exact: true }).click();
    await restoredQuery;
    await legacy.getByRole("status").filter({ hasText: "已移除失效筛选" }).waitFor();
    const namedQueries = requests.slice(beforeNamedRequests).filter((request) => request.path.includes("/graph/"));
    assert.ok(namedQueries.some((request) => request.path.endsWith("/graph/query") && request.body.q === namedProbe), "Named view must issue a fresh graph query");
    assert.ok(namedQueries.every((request) => !request.body.types?.includes("x_deleted_named") && !request.body.relation_types?.includes("X_DELETED_NAMED")));
    await legacy.waitForFunction(({ namespace, probe }) => {
      const saved = JSON.parse(sessionStorage.getItem(`kg-v3-session:${namespace}`) || "{}");
      return saved.lens === "explore" && saved.views?.explore?.filterQuery === probe &&
        saved.views.explore.types.join(",") === "person" && saved.views.explore.relations.join(",") === "HERMES_knows";
    }, { namespace, probe: namedProbe });
    assert.match(await legacy.locator(".gw-notice").filter({ hasText: "已移除失效筛选" }).innerText(), /永久删除的定义不会被视图恢复/);
    await legacy.screenshot({ path: path.join(output, "legacy-named-view-restored.png"), fullPage: true });
    results.legacy_named_view_restore_drops_deleted_filters_and_explains = true;
    await legacy.close();
    results.errors = errors; assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, "results.json"), JSON.stringify(results, null, 2));
    fs.writeFileSync(path.join(output, "requests.json"), JSON.stringify(requests, null, 2));
    console.log(JSON.stringify(results, null, 2));
  } catch (error) {
    await page.screenshot({ path: path.join(output, "failure.png"), fullPage: true }).catch(() => {});
    fs.writeFileSync(path.join(output, "failure.json"), JSON.stringify({ results, errors, error: error.message, requests }, null, 2));
    console.error(error); process.exitCode = 1;
  } finally { await browser.close(); }
})();
