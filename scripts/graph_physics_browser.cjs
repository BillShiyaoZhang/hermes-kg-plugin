/* Isolated, synthetic GraphCanvas force-layout acceptance. Never contacts the KG API. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("node:fs");
const path = require("node:path");
const origin = process.env.KG_BROWSER_URL || "http://127.0.0.1:5174";
const output = path.resolve(
  process.env.KG_BROWSER_REPORT_DIR || "reports/graph-physics",
);
fs.mkdirSync(output, { recursive: true });
const fixture = `<!doctype html><html><head><meta charset="utf-8"><style>body{margin:0;background:#f4f6f2;font-family:Arial}#root{width:1180px;padding:10px}button{border:0;cursor:pointer}</style></head><body><div id="root"></div><script type="module">
import RefreshRuntime from '/@react-refresh';RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;
await import('/src/styles.css');await import('/src/design-system.css');await import('/src/graphWorkspace.css');
const React=(await import('/node_modules/.vite/deps/react.js')).default;const {createRoot}=(await import('/node_modules/.vite/deps/react-dom_client.js')).default;const {default:GraphCanvas}=await import('/src/GraphCanvas.tsx');
const node=id=>({id,label:id==='user'?'User · shared hub':id,type:'concept',layer:'saved'});const edge=(a,b)=>({id:a+':'+b,source:a,target:b,label:'related',kind:'saved'});
const ns=['a','b','c','d','e','f','user','isolate'].map(node);const es=[edge('a','b'),edge('a','c'),edge('b','c'),edge('d','e'),edge('d','f'),edge('e','f'),...['a','b','c','d','e','f'].map(id=>edge('user',id))];
const initial=()=>({view:{x:0,y:0,zoom:1},positions:{},pinnedIds:['c'],hiddenIds:[]});window.commits=[];window.updates=0;window.undoHistory=[];window.redoHistory=[];let before;
function App(){const [state,setState]=React.useState(initial),[nodes,setNodes]=React.useState(ns),[edges,setEdges]=React.useState(es),[mounted,setMounted]=React.useState(true),[selected,setSelected]=React.useState([]);window.snapshot=state;window.choose=id=>setSelected([id]);
window.undo=()=>{window.redoHistory.push(window.snapshot);setState(window.undoHistory.pop())};window.redo=()=>{window.undoHistory.push(window.snapshot);setState(window.redoHistory.pop())};window.refresh=()=>setNodes(n=>n.map(x=>({...x})));window.unmount=()=>setMounted(false);
window.benchmark=()=>{const n=Array.from({length:200},(_,i)=>node('n'+i));const e=n.flatMap((_,i)=>[1,2].map(d=>edge('n'+i,'n'+(Math.floor(i/10)*10+(i+d)%10))));for(let i=0;i<19;i++)e.push(edge('n'+(i*10),'n'+((i+1)*10)));setNodes(n);setEdges(e);setState({...initial(),pinnedIds:[]})};
return mounted&&React.createElement(GraphCanvas,{nodes,edges,layoutMode:'relation',state,selectedIds:selected,onSelectionChange:setSelected,workspaceKey:'physics-fixture',onStateChange:(s,commit)=>{window.updates++;if(commit){window.commits.push(structuredClone(s));window.undoHistory.push(before)}window.snapshot=s;setState(s)},onBeginInteraction:()=>{before=window.snapshot},onPinNodes:(ids,pinned)=>{const now=window.snapshot;window.undoHistory.push(now);const next={...now,pinnedIds:pinned?[...new Set([...now.pinnedIds,...ids])]:now.pinnedIds.filter(id=>!ids.includes(id))};window.commits.push(structuredClone(next));window.snapshot=next;setState(next)},onSelectNode:()=>{},onSelectEdge:()=>{},onFocusNode:()=>{},onUndo:window.undo,onRedo:window.redo})};createRoot(document.getElementById('root')).render(React.createElement(App));
</script></body></html>`;

async function main() {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.KG_CHROME_PATH
      ? { executablePath: process.env.KG_CHROME_PATH }
      : {}),
  });
  const page = await browser.newPage({
    viewport: { width: 1220, height: 900 },
  });
  const errors = [],
    result = {};
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/"))
      errors.push("Unexpected KG API request");
  });
  const assert = (name, condition) => {
    result[name] = condition;
    if (!condition) throw new Error(name);
  };
  const screen = async (id) => {
    const box = await page
      .locator(`[data-node-id="${id}"] .graph-node-shape`)
      .boundingBox();
    if (!box) throw new Error(`Missing ${id}`);
    return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  };
  const drag = async (id, dx, dy, release = true) => {
    const start = await screen(id);
    await page.mouse.move(start.x, start.y);
    await page.mouse.down();
    await page.mouse.move(start.x + dx, start.y + dy, { steps: 12 });
    if (release) await page.mouse.up();
    return start;
  };
  try {
    await page.route(origin + "/", (route) =>
      route.fulfill({ contentType: "text/html", body: fixture }),
    );
    await page.goto(origin);
    await page.waitForFunction(
      () => Object.keys(window.snapshot?.positions ?? {}).length === 8,
    );
    const initial = await page.evaluate(() => structuredClone(window.snapshot));
    const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
    assert(
      "dense_circles_separate_despite_shared_hub",
      dist(initial.positions.a, initial.positions.d) >
        1.5 * dist(initial.positions.a, initial.positions.b),
    );
    await drag("a", -110, 60, false);
    await page.waitForTimeout(160);
    const during = await page.evaluate(() => structuredClone(window.snapshot));
    assert(
      "related_neighbor_moves_during_drag",
      dist(during.positions.b, initial.positions.b) > 10,
    );
    assert(
      "pin_stays_fixed_during_drag",
      JSON.stringify(during.positions.c) ===
        JSON.stringify(initial.positions.c),
    );
    assert(
      "pointer_target_exact",
      Math.abs(
        during.positions.a.x - initial.positions.a.x + 110 / initial.view.zoom,
      ) < 0.01 &&
        Math.abs(
          during.positions.a.y - initial.positions.a.y - 60 / initial.view.zoom,
        ) < 0.01,
    );
    assert(
      "no_commit_before_release",
      await page.evaluate(() => window.commits.length === 0),
    );
    await page.mouse.up();
    await page.waitForFunction(() => window.commits.length === 1);
    const settled = await page.evaluate(() => structuredClone(window.snapshot));
    assert(
      "camera_unchanged_during_drag_and_settle",
      JSON.stringify(settled.view) === JSON.stringify(initial.view),
    );
    await page.waitForTimeout(250);
    const idleUpdates = await page.evaluate(() => window.updates);
    await page.waitForTimeout(250);
    assert(
      "simulation_stops_after_settling",
      await page.evaluate((n) => window.updates === n, idleUpdates),
    );
    await page.evaluate(() => window.undo());
    await page.waitForTimeout(60);
    assert(
      "single_undo_restores_all_related_positions",
      await page.evaluate(
        (p) => JSON.stringify(window.snapshot.positions) === JSON.stringify(p),
        initial.positions,
      ),
    );
    await page.evaluate(() => window.redo());
    await page.waitForTimeout(60);
    assert(
      "redo_restores_settled_positions",
      await page.evaluate(
        (p) => JSON.stringify(window.snapshot.positions) === JSON.stringify(p),
        settled.positions,
      ),
    );
    await page.evaluate(() => window.refresh());
    await page.waitForTimeout(120);
    assert(
      "data_refresh_preserves_camera_and_positions",
      await page.evaluate(
        (p) => JSON.stringify(window.snapshot) === JSON.stringify(p),
        settled,
      ),
    );
    await drag("c", 60, 35);
    await page.waitForTimeout(100);
    assert(
      "pinned_node_cannot_be_dragged",
      await page.evaluate(
        (p) =>
          JSON.stringify(window.snapshot.positions.c) === JSON.stringify(p) &&
          window.commits.length === 1,
        initial.positions.c,
      ),
    );
    await drag("a", 65, -25, false);
    await page.waitForTimeout(70);
    await page
      .locator(".graph-canvas-surface")
      .dispatchEvent("pointercancel", { pointerId: 1 });
    await page.mouse.up();
    await page.waitForTimeout(80);
    assert(
      "cancel_restores_entire_drag",
      await page.evaluate(
        (p) =>
          JSON.stringify(window.snapshot.positions) === JSON.stringify(p) &&
          window.commits.length === 1,
        settled.positions,
      ),
    );
    await page.emulateMedia({ reducedMotion: "reduce" });
    await drag("a", 35, 20);
    await page.waitForTimeout(100);
    const reducedUpdates = await page.evaluate(() => window.updates);
    await page.waitForTimeout(180);
    assert(
      "reduced_motion_has_no_running_animation",
      await page.evaluate(
        (n) => window.updates === n && window.commits.length === 2,
        reducedUpdates,
      ),
    );
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await drag("a", 40, -20, false);
    await page.waitForTimeout(50);
    await page.evaluate(() => {
      Object.defineProperty(document, "hidden", {
        configurable: true,
        value: true,
      });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await page.mouse.up();
    await page.waitForTimeout(50);
    const hiddenUpdates = await page.evaluate(() => window.updates);
    await page.waitForTimeout(180);
    assert(
      "hidden_tab_stops_and_commits_drag_once",
      await page.evaluate(
        (n) => window.updates === n && window.commits.length === 3,
        hiddenUpdates,
      ),
    );
    await page.evaluate(() => {
      delete document.hidden;
      document.dispatchEvent(new Event("visibilitychange"));
    });
    const beforeRegroup = await page.evaluate(() =>
      structuredClone(window.snapshot),
    );
    await page
      .getByRole("button", { name: "重新聚集关联节点", exact: true })
      .click();
    await page.waitForFunction(() => window.commits.length === 4);
    assert(
      "explicit_regroup_preserves_pin_and_camera",
      await page.evaluate(
        (before) =>
          JSON.stringify(window.snapshot.view) ===
            JSON.stringify(before.view) &&
          JSON.stringify(window.snapshot.positions.c) ===
            JSON.stringify(before.positions.c),
        beforeRegroup,
      ),
    );
    await page.evaluate(() => window.undo());
    await page.waitForTimeout(60);
    assert(
      "explicit_regroup_is_one_undo",
      await page.evaluate(
        (before) =>
          JSON.stringify(window.snapshot.positions) ===
          JSON.stringify(before.positions),
        beforeRegroup,
      ),
    );
    await page.evaluate(() => {
      const button = document.querySelector('[aria-label="重新聚集关联节点"]');
      button.click();
      button.click();
    });
    await page.waitForTimeout(120);
    assert(
      "cancel_regroup_preserves_state_and_history",
      await page.evaluate(
        (before) =>
          JSON.stringify(window.snapshot.positions) ===
            JSON.stringify(before.positions) && window.commits.length === 4,
        beforeRegroup,
      ),
    );
    await drag("a", 30, 15);
    await page
      .getByRole("button", { name: "撤销图谱操作", exact: true })
      .click();
    await page.waitForTimeout(120);
    assert(
      "undo_while_settling_restores_entire_drag",
      await page.evaluate(
        (before) =>
          JSON.stringify(window.snapshot.positions) ===
            JSON.stringify(before.positions) && window.commits.length === 5,
        beforeRegroup,
      ),
    );
    await drag("a", 35, 20);
    await page.evaluate(() => window.choose("b"));
    await page.locator('[data-node-id="b"].is-selected').waitFor();
    await page.keyboard.press("p");
    await page.waitForTimeout(70);
    const newlyPinned = await page.evaluate(() =>
      structuredClone(window.snapshot.positions.b),
    );
    await page.waitForTimeout(200);
    assert(
      "pin_neighbor_while_settling_finishes_drag_then_fixes_neighbor",
      await page.evaluate(
        (point) =>
          window.commits.length === 7 &&
          window.snapshot.pinnedIds.includes("b") &&
          JSON.stringify(window.snapshot.positions.b) === JSON.stringify(point),
        newlyPinned,
      ),
    );
    const pinnedPositions = await page.evaluate(() =>
      structuredClone(window.snapshot.positions),
    );
    await page
      .getByRole("button", { name: "撤销图谱操作", exact: true })
      .click();
    await page.waitForTimeout(70);
    assert(
      "parent_pin_undo_applies_skipped_canvas_snapshot",
      await page.evaluate(
        (positions) =>
          !window.snapshot.pinnedIds.includes("b") &&
          JSON.stringify(window.snapshot.positions) ===
            JSON.stringify(positions) &&
          !document.querySelector('[data-node-id="b"].is-pinned'),
        pinnedPositions,
      ),
    );
    await page
      .getByRole("button", { name: "撤销图谱操作", exact: true })
      .click();
    await page.waitForTimeout(70);
    assert(
      "drag_and_pin_have_separate_undo_entries",
      await page.evaluate(
        (before) =>
          JSON.stringify(window.snapshot.positions) ===
            JSON.stringify(before.positions) &&
          !window.snapshot.pinnedIds.includes("b"),
        beforeRegroup,
      ),
    );
    await page.screenshot({ path: path.join(output, "clustered-drag.png") });
    await page.evaluate(() => window.benchmark());
    await page.waitForFunction(
      () => Object.keys(window.snapshot.positions).length === 200,
    );
    await page.evaluate(() => {
      window.frameSamples = [];
      let last = performance.now();
      const sample = (now) => {
        window.frameSamples.push(now - last);
        last = now;
        if (window.frameSamples.length < 100) requestAnimationFrame(sample);
      };
      requestAnimationFrame(sample);
    });
    await drag("n0", 90, 40);
    await page.waitForFunction(() => window.frameSamples.length === 100);
    const frames = await page.evaluate(() =>
      window.frameSamples.slice(2).sort((a, b) => a - b),
    );
    result.node_200_frame_p95_ms = frames[Math.floor(frames.length * 0.95)];
    assert("node_200_frame_budget", result.node_200_frame_p95_ms < 55);
    await page.screenshot({ path: path.join(output, "200-nodes.png") });
    await page.evaluate(() => window.unmount());
    await page.waitForTimeout(50);
    const unmountedUpdates = await page.evaluate(() => window.updates);
    await page.waitForTimeout(120);
    assert(
      "unmount_cleans_animation",
      await page.evaluate((n) => window.updates === n, unmountedUpdates),
    );
    assert("no_browser_errors_or_api_access", errors.length === 0);
  } finally {
    fs.writeFileSync(
      path.join(output, "results.json"),
      JSON.stringify({ results: result, errors }, null, 2),
    );
    await browser.close();
  }
  console.log(JSON.stringify(result, null, 2));
}
main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
