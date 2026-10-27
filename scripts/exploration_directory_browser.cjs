/* Explore's ontology directory acceptance. Every API response uses fictional data.
 * Start console Vite first, or set KG_DIRECTORY_STATIC_DIR to a built console.
 * No real memory service/profile/database is contacted.
 * KG_DIRECTORY_BROWSER_URL, KG_DIRECTORY_REPORT_DIR, KG_PYTHON,
 * PLAYWRIGHT_MODULE and KG_CHROME_PATH select existing local tooling.
 */
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const root = path.resolve(__dirname, "..");
const origin = (
  process.env.KG_DIRECTORY_BROWSER_URL || "http://127.0.0.1:5186"
).replace(/\/$/, "");
const output = path.resolve(
  process.env.KG_DIRECTORY_REPORT_DIR ||
    path.join(root, "reports/exploration-directory"),
);
const staticDirectory =
  process.env.KG_DIRECTORY_STATIC_DIR &&
  path.resolve(process.env.KG_DIRECTORY_STATIC_DIR);
const staticAssets = new Map();
if (staticDirectory) {
  const snapshot = (directory) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const file = path.join(directory, entry.name);
      if (entry.isDirectory()) snapshot(file);
      else staticAssets.set(file, fs.readFileSync(file));
    }
  };
  snapshot(staticDirectory);
  assert.ok(
    staticAssets.has(path.join(staticDirectory, "index.html")),
    "Build the console before running static acceptance",
  );
}
const namespace = "urn:directory-browser:synthetic:",
  token = "directory-synthetic-token";
const python =
  process.env.KG_PYTHON || path.join(root, ".venv-console/Scripts/python.exe");
const schema = JSON.parse(
  execFileSync(
    python,
    [
      "-c",
      [
        "from kg.config import KGConfig",
        "from kg.ontology_profile import load_personal_profile",
        "from kg.ontology_presets import _assemble",
        "from kg.ontology_editing import effective_schema",
        "import json",
        `config=KGConfig(urn_prefix=${JSON.stringify(namespace)})`,
        "state,_,_=_assemble(config,{'version':0,'patch':{}},load_personal_profile())",
        "print(json.dumps(effective_schema(config,state),ensure_ascii=True))",
      ].join("\n"),
    ],
    {
      cwd: root,
      env: { ...process.env, PYTHONPATH: path.join(root, "plugins") },
      encoding: "utf8",
    },
  ),
);
const scope = "current_namespace_active";
// The runtime keeps historical types/relations readable after their definitions
// are disabled; selecting the whole active directory must not broaden to them.
schema.entity_types.push({
  ...schema.entity_types.find((item) => item.custom),
  key: "x_retired_fixture",
  label: "合成已停用概念",
  storage_label: "KGCustom_x_retired_fixture",
  iri: `${namespace}ontology:entity_types:x_retired_fixture`,
  active: false,
});
schema.relations.push({
  ...schema.relations.find((item) => item.custom),
  key: "X_RETIRED_FIXTURE",
  label: "合成已停用关系",
  storage_type: "X_RETIRED_FIXTURE",
  iri: `${namespace}ontology:relations:X_RETIRED_FIXTURE`,
  active: false,
});
const types = schema.entity_types
  .filter((item) => item.runtime_supported && item.active !== false)
  .map((item) => item.key);
const records = [
  ...Array.from({ length: 207 }, (_, index) => ({
    uri: `${namespace}task-${index}`,
    name: `合成任务 ${index}`,
    description: "合成验收记录",
    type: "x_personal_task",
    properties: {},
  })),
  {
    uri: `${namespace}project`,
    name: "合成项目",
    description: "项目包含任务",
    type: "x_personal_project",
    properties: {},
  },
  {
    uri: `${namespace}person-a`,
    name: "合成人物甲",
    description: "合成人物",
    type: "person",
    properties: {},
  },
  {
    uri: `${namespace}person-b`,
    name: "合成人物乙",
    description: "合成人物",
    type: "person",
    properties: {},
  },
  {
    uri: `${namespace}retired`,
    name: "合成历史实例",
    description: "仅用于验证停用定义不会进入当前目录的全选范围。",
    type: "x_retired_fixture",
    properties: {},
  },
];
const counts = (nodes) =>
  types
    .map((type) => ({
      type,
      count: nodes.filter((node) => node.type === type).length,
    }))
    .filter((item) => item.count);
const fictionalRelations = [{
  id: "directory-synthetic-friendship",
  relation_type: "RELATED_TO",
  stored_type: "RELATED_TO",
  source: records.find((node) => node.uri === `${namespace}person-a`),
  target: records.find((node) => node.uri === `${namespace}person-b`),
  properties: {},
}, {
  id: "directory-retired-relationship",
  relation_type: "X_RETIRED_FIXTURE",
  stored_type: "X_RETIRED_FIXTURE",
  source: records.find((node) => node.uri === `${namespace}person-a`),
  target: records.find((node) => node.uri === `${namespace}person-b`),
  properties: {},
}];
schema.stats_available = true;
schema.stats = {
  entities: records.length,
  relationships: fictionalRelations.length,
  unmapped_relationships: 0,
};
for (const item of schema.entity_types)
  item.count =
    item.key === "x_personal_topic"
      ? null
      : records.filter((node) => node.type === item.key).length;
for (const item of schema.relations)
  item.count = fictionalRelations.filter((edge) => edge.relation_type === item.key).length;
const emptyPage = (body = {}) => ({
  namespace,
  scope,
  items: [],
  total: 0,
  offset: body.offset || 0,
  limit: body.limit || 20,
  has_more: false,
});

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.KG_CHROME_PATH
      ? { executablePath: process.env.KG_CHROME_PATH }
      : {}),
  });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const errors = [],
    requests = [],
    results = {
      synthetic_only: true,
      browser: browser.version(),
      concepts: types.length,
    };
  await context.route("**/*", async (route) => {
    const request = route.request(),
      url = new URL(request.url());
    if (url.origin !== origin) {
      errors.push("Unexpected external origin " + url.origin);
      return route.abort();
    }
    if (!url.pathname.startsWith("/api/")) {
      if (!staticDirectory) return route.continue();
      const file = path.resolve(
        staticDirectory,
        url.pathname === "/"
          ? "index.html"
          : "." + decodeURIComponent(url.pathname),
      );
      if (!file.startsWith(staticDirectory + path.sep))
        throw new Error("Static path escaped build directory");
      const mime =
        {
          ".html": "text/html",
          ".js": "text/javascript",
          ".css": "text/css",
          ".svg": "image/svg+xml",
          ".png": "image/png",
          ".woff2": "font/woff2",
        }[path.extname(file)] || "application/octet-stream";
      return route.fulfill({
        status: staticAssets.has(file) ? 200 : 404,
        contentType: mime,
        body: staticAssets.get(file) || "",
      });
    }
    try {
      const body = request.postDataJSON() || {};
      assert.equal(request.headers().authorization, "Bearer " + token);
      assert.equal(url.search, "");
      requests.push({ path: url.pathname, body });
      const selected = records.filter(
        (node) => !body.types?.length || body.types.includes(node.type),
      );
      const selectedIds = new Set(selected.map((node) => node.uri));
      const edges = fictionalRelations.filter((edge) =>
        selectedIds.has(edge.source.uri) && selectedIds.has(edge.target.uri) &&
        (!body.relation_types?.length || body.relation_types.includes(edge.relation_type)),
      );
      let value;
      switch (url.pathname) {
        case "/api/v1/status":
          value = {
            connected: true,
            memory: {
              searchable_entities: records.length,
              counts: Object.fromEntries(
                counts(records).map((item) => [item.type, item.count]),
              ),
            },
            sync: { pending_count: 0, jobs: [] },
            search: {
              mode: "keyword",
              state: "ready",
              model: { ready: false },
            },
            compatibility: { data_mode: "demo" },
          };
          break;
        case "/api/v1/ontology":
          value = schema;
          break;
        case "/api/v1/memories/detail":
          value = { memory: records.find((node) => node.uri === body.uri) };
          break;
        case "/api/v1/graph/overview/query":
          value = {
            namespace,
            scope,
            stats_available: true,
            stats_error: null,
            total_nodes: selected.length,
            total_edges: edges.length,
            groups: counts(selected),
            relations: edges.map((edge) => ({
              source_type: edge.source.type,
              target_type: edge.target.type,
              relation_type: edge.relation_type,
              count: 1,
            })),
          };
          break;
        case "/api/v1/graph/query":
          value = {
            namespace,
            scope,
            edge_scope: "returned_nodes",
            nodes: selected.slice(0, 200),
            edges: edges.filter((edge) => selected.slice(0, 200).some((node) => node.uri === edge.source.uri) && selected.slice(0, 200).some((node) => node.uri === edge.target.uri)),
            total_nodes: selected.length,
            total_edges: edges.length,
            truncated_nodes: selected.length > 200,
            truncated_edges: false,
            focus_found: null,
          };
          break;
        case "/api/v1/ontology/entities/query":
        case "/api/v1/ontology/relations/query":
        case "/api/v1/memories/query":
        case "/api/v1/memories/revisions":
          value = emptyPage(body);
          break;
        case "/api/v1/reviews/query":
          value = { ...emptyPage(body), total_pending: 0 };
          break;
        default:
          throw new Error("Unmocked directory API " + url.pathname);
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(value),
      });
    } catch (error) {
      errors.push(error.message);
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Synthetic contract mismatch" }),
      });
    }
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push("console: " + message.text());
  });
  const directory = () =>
    page.getByRole("complementary", { name: "图谱目录与筛选" });
  const concepts = () => directory().locator("fieldset.gw-concept-directory");
  const graphReads = () =>
    requests.filter((item) => item.path.includes("/graph/")).length;
  const taskOnly = () =>
    requests.some(
      (item) =>
        item.path.endsWith("/graph/query") &&
        JSON.stringify(item.body.types) === '["x_personal_task"]',
    );
  const waitPersisted = async () =>
    page.waitForFunction(
      (key) =>
        JSON.parse(sessionStorage.getItem(key) || "null")?.views?.explore
          ?.directoryVersion === 1,
      `kg-v3-session:${namespace}`,
    );
  try {
    await page.goto(origin + "/#token=" + token);
    await concepts()
      .getByRole("checkbox", { name: "任务", exact: true })
      .waitFor();
    assert.equal(await concepts().getByRole("checkbox").count(), 17);
    assert.equal(
      await concepts().getByRole("checkbox", { checked: true }).count(),
      17,
    );
    await page.locator('[data-node-id="aggregate:x_personal_task"]').waitFor();
    assert.match(
      await page.locator(".gw-footer").innerText(),
      /全库聚合 · 210 个实体/,
    );
    assert.match(await concepts().innerText(), /暂无记录/);
    assert.equal(
      await concepts().getByLabel("数量未知", { exact: true }).count(),
      1,
    );
    results.default_explore_directory = true;
    results.dynamic_concepts_and_counts = true;
    const initialRead = requests.filter((item) => item.path.endsWith("/graph/query")).at(-1);
    assert.equal(initialRead.body.types.length, 17);
    assert.ok(!initialRead.body.types.includes("x_retired_fixture"));
    assert.ok(initialRead.body.relation_types.length > 0);
    assert.ok(!initialRead.body.relation_types.includes("X_RETIRED_FIXTURE"));
    results.active_directory_all_excludes_disabled_runtime_history = true;
    await page.screenshot({
      path: path.join(output, "desktop-directory.png"),
      fullPage: true,
    });

    await concepts().getByRole("button", { name: "清空", exact: true }).click();
    await page.waitForFunction(
      () => document.querySelectorAll(".graph-node").length === 0,
    );
    const afterClear = graphReads();
    await page.waitForTimeout(150);
    assert.equal(graphReads(), afterClear);
    await concepts()
      .getByRole("checkbox", { name: "任务", exact: true })
      .check();
    await page.waitForFunction(() =>
      document.querySelector('[data-node-id="aggregate:x_personal_task"]'),
    );
    assert.equal(taskOnly(), true);
    assert.match(
      await page.locator(".gw-footer").innerText(),
      /全库聚合 · 207 个实体/,
    );
    assert.equal(await page.locator(".graph-node-aggregate").count(), 1);
    results.server_type_filter_includes_unloaded_records = true;
    await concepts().getByRole("button", { name: "清空", exact: true }).click();
    await page.waitForFunction(
      () => document.querySelectorAll(".graph-node").length === 0,
    );
    const beforeAll = graphReads();
    await concepts().getByRole("button", { name: "全选", exact: true }).click();
    await page.locator('[data-node-id="aggregate:person"]').waitFor();
    assert.ok(graphReads() > beforeAll);
    results.empty_then_all_refetches = true;

    const relationKey = schema.relations.find(
      (item) => item.runtime_supported && item.active !== false,
    ).key;
    const relationDetails = directory().locator("details").first();
    await relationDetails.locator("summary").click();
    await relationDetails.getByRole("checkbox").first().uncheck();
    await page.waitForTimeout(250);
    const relationRequest = requests
      .filter(
        (item) =>
          item.path.endsWith("/graph/query") &&
          item.body.relation_types?.length,
      )
      .at(-1);
    assert.ok(
      relationRequest &&
        !relationRequest.body.relation_types.includes(relationKey),
    );
    await relationDetails
      .getByRole("button", { name: "显示全部关系", exact: true })
      .click();
    results.server_relation_filter = true;

    // A persisted empty relationship projection must not poison the all-relations cache.
    await concepts().getByRole("button", { name: "清空", exact: true }).click();
    await concepts().getByRole("checkbox", { name: "人物", exact: true }).check();
    await page.waitForFunction(() => document.querySelectorAll(".graph-edge").length === 1);
    await waitPersisted();
    await page.waitForTimeout(450);
    await page.evaluate((key) => {
      const saved = JSON.parse(sessionStorage.getItem(key));
      saved.views.explore.relations = ["__none__"];
      sessionStorage.setItem(key, JSON.stringify(saved));
    }, `kg-v3-session:${namespace}`);
    await page.reload();
    await page.waitForFunction(() => document.querySelector(".gw-footer")?.textContent.includes("2 个实体 · 0 条关系"));
    assert.equal(await page.locator(".graph-edge").count(), 0);
    const beforeRestoreRelations = graphReads();
    await directory().getByRole("button", { name: "显示全部关系", exact: true }).click();
    await page.waitForFunction(() => document.querySelectorAll(".graph-edge").length === 1);
    await page.waitForFunction(() => document.querySelector(".gw-footer")?.textContent.includes("2 个实体 · 1 条关系"));
    assert.ok(graphReads() > beforeRestoreRelations);
    results.no_relations_reload_then_all_restores_edges_and_counts = true;

    const firstPerson = page.locator(`[data-node-id="saved:${namespace}person-a"]`);
    await firstPerson.click();
    await page.waitForTimeout(100);
    const transforms = () => page.locator(".graph-node").evaluateAll((items) =>
      Object.fromEntries(items.map((item) => [item.getAttribute("data-node-id"), item.getAttribute("transform")])),
    );
    const beforeDrag = await transforms();
    const personBox = await firstPerson.locator(".graph-node-shape").boundingBox();
    await page.mouse.move(personBox.x + personBox.width / 2, personBox.y + personBox.height / 2);
    await page.mouse.down();
    await page.mouse.move(personBox.x + personBox.width / 2 + 70, personBox.y + personBox.height / 2 + 35, { steps: 6 });
    await page.mouse.up();
    // Use the Explorer footer while the canvas is still relaxing its neighbors.
    await page.getByRole("button", { name: "固定所选对象", exact: true }).click();
    await page.waitForFunction(() => document.querySelector(".graph-node.is-pinned"));
    const afterPin = await transforms();
    await page.getByRole("button", { name: "撤销画布操作", exact: true }).click();
    await page.waitForFunction(() => !document.querySelector(".graph-node.is-pinned"));
    assert.deepEqual(await transforms(), afterPin);
    await page.getByRole("button", { name: "撤销画布操作", exact: true }).click();
    await page.waitForFunction((before) =>
      [...document.querySelectorAll(".graph-node")].every((item) =>
        item.getAttribute("transform") === before[item.getAttribute("data-node-id")]), beforeDrag,
    );
    results.footer_pin_during_settling_has_separate_undo = true;

    await concepts().getByRole("button", { name: "清空", exact: true }).click();
    await concepts()
      .getByRole("checkbox", { name: "任务", exact: true })
      .check();
    await directory()
      .getByRole("button", { name: "收起目录", exact: true })
      .click();
    await page.getByRole("button", { name: "本体", exact: true }).click();
    assert.equal(
      await concepts().getByRole("checkbox", { checked: true }).count(),
      17,
    );
    await page.getByRole("button", { name: "探索", exact: true }).click();
    assert.equal(await directory().count(), 0);
    await waitPersisted();
    await page.waitForTimeout(450);
    await page.reload();
    await page.locator('[data-node-id="aggregate:x_personal_task"]').waitFor();
    assert.equal(await directory().count(), 0);
    await page
      .getByRole("button", { name: "切换目录与筛选", exact: true })
      .click();
    assert.equal(
      await concepts().getByRole("checkbox", { checked: true }).count(),
      1,
    );
    assert.equal(
      await concepts()
        .getByRole("checkbox", { name: "任务", exact: true })
        .isChecked(),
      true,
    );
    results.lens_filters_and_explicit_collapse_retained = true;

    await page.waitForTimeout(450);
    await page.evaluate((key) => {
      const saved = JSON.parse(sessionStorage.getItem(key));
      saved.views.explore.sidebar = false;
      delete saved.views.explore.directoryVersion;
      sessionStorage.setItem(key, JSON.stringify(saved));
    }, `kg-v3-session:${namespace}`);
    await page.reload();
    await concepts()
      .getByRole("checkbox", { name: "任务", exact: true })
      .waitFor();
    assert.equal(
      await concepts().getByRole("checkbox", { checked: true }).count(),
      1,
    );
    results.legacy_session_directory_migration = true;

    await page.setViewportSize({ width: 390, height: 844 });
    await concepts()
      .getByRole("checkbox", {
        name: schema.entity_types.find(
          (item) => item.key === "x_personal_topic",
        ).label,
        exact: true,
      })
      .scrollIntoViewIfNeeded();
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
      true,
    );
    assert.equal(
      await directory().evaluate((element) => {
        const rect = element.getBoundingClientRect();
        return rect.left >= -1 && rect.right <= innerWidth + 1;
      }),
      true,
    );
    await directory()
      .getByRole("button", { name: "收起目录", exact: true })
      .click();
    await page
      .getByRole("button", { name: "切换目录与筛选", exact: true })
      .click();
    await page.screenshot({
      path: path.join(output, "mobile-directory.png"),
      fullPage: true,
    });
    results.mobile_directory_reachable_no_overflow = true;
    await page.evaluate(
      fs.readFileSync(
        require.resolve("axe-core/axe.min.js", {
          paths: [path.join(root, "console")],
        }),
        "utf8",
      ),
    );
    results.axe_mobile_directory = await page.evaluate(async () =>
      (
        await axe.run(".gw-directory", {
          runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] },
        })
      ).violations.map((item) => ({
        id: item.id,
        impact: item.impact,
        targets: item.nodes.map((node) => node.target),
      })),
    );
    assert.deepEqual(results.axe_mobile_directory, []);
    results.errors = errors;
    assert.deepEqual(errors, []);
    fs.writeFileSync(
      path.join(output, "results.json"),
      JSON.stringify(results, null, 2),
    );
    fs.writeFileSync(
      path.join(output, "requests.json"),
      JSON.stringify(requests, null, 2),
    );
    for (const name of ["failure.json", "failure.png"])
      fs.rmSync(path.join(output, name), { force: true });
    console.log(JSON.stringify(results, null, 2));
  } catch (error) {
    await page
      .screenshot({ path: path.join(output, "failure.png"), fullPage: true })
      .catch(() => {});
    fs.writeFileSync(
      path.join(output, "failure.json"),
      JSON.stringify(
        { results, errors, error: error.message, requests },
        null,
        2,
      ),
    );
    console.error(error);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
