/* Isolated synthetic GraphCanvas acceptance and 300-node/800-edge frame benchmark.
 * Run against a Vite development server via KG_BROWSER_URL. No API requests occur.
 * PLAYWRIGHT_MODULE and KG_CHROME_PATH may point to an existing local installation.
 * The 4-second RAF samples measure main-thread/compositor behavior in headless Chromium,
 * not real touchscreen hardware or a cross-device performance guarantee.
 */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("node:fs");
const path = require("node:path");
const origin = (process.env.KG_BROWSER_URL || "http://127.0.0.1:5173").replace(
  /\/$/,
  "",
);
const output = path.resolve(
  process.env.KG_BROWSER_REPORT_DIR || "reports/graph-v3-canvas",
);
fs.mkdirSync(output, { recursive: true });
const fixture = `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'"><style>body{margin:0;padding:20px;background:#f4f6f2;font-family:Arial}button{cursor:pointer;border:0}#root{width:1100px}</style></head><body><div id="root"></div><script type="module">
import RefreshRuntime from '/@react-refresh';
RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;
const React=(await import('/node_modules/.vite/deps/react.js')).default;
const {createRoot}=(await import('/node_modules/.vite/deps/react-dom_client.js')).default;
const {default:GraphCanvas}=await import('/src/GraphCanvas.tsx');
const initialNodes=Array.from({length:8},(_,i)=>({id:String.fromCharCode(97+i),label:'Synthetic '+i,type:i%2?'person':'concept',layer:i===7?'aggregate':'saved',count:i===7?12:undefined}));
const initialPositions=Object.fromEntries(initialNodes.map((n,i)=>[n.id,{x:120+(i%4)*220,y:120+Math.floor(i/4)*220}]));
const initialState=()=>({view:{x:40,y:40,zoom:.9},positions:structuredClone(initialPositions),pinnedIds:[],hiddenIds:[]});
const initialEdges=[1,2,3].map(i=>({id:'parallel-'+i,source:'a',target:'b',label:'relation '+i,kind:'saved'})).concat([{id:'candidate-edge',source:'a',target:'b',label:'proposal',kind:'candidate'},{id:'other',source:'b',target:'f',label:'related',kind:'saved'}]);
window.events=[];
function App(){const [state,setState]=React.useState(initialState),[ids,setIds]=React.useState([]),[nodes,setNodes]=React.useState(initialNodes),[layout,setLayout]=React.useState('type'),[links,setLinks]=React.useState(initialEdges);
 window.snapshot=state;window.selected=ids;window.performanceGraph=()=>{const n=Array.from({length:300},(_,i)=>({id:'n'+i,label:'Synthetic node '+i,type:['person','event','preference','decision','location','concept'][i%6],layer:'saved'}));const p=Object.fromEntries(n.map((a,i)=>[a.id,{x:120+(i%20)*160,y:120+Math.floor(i/20)*115}]));const e=Array.from({length:800},(_,i)=>({id:'e'+i,source:'n'+(i%300),target:'n'+((i*13+1+Math.floor(i/300)*17)%300),kind:'saved',label:'Related '+i}));setIds([]);setNodes(n);setLinks(e);setState({view:{x:40,y:40,zoom:.45},positions:p,pinnedIds:[],hiddenIds:[]});};window.resetFixture=()=>{setIds([]);setState(initialState());};window.addFixtureNode=()=>setNodes(n=>[...n,{id:'new',label:'New synthetic',type:'concept',layer:'saved'}]);window.useLayout=m=>{setLayout(m);setState(s=>({...s,positions:{}}));};
 return React.createElement(GraphCanvas,{nodes,edges:links,layoutMode:layout,workspaceKey:layout,state,selectedIds:ids,onStateChange:(s,c)=>{window.snapshot=s;setState(s);window.events.push({kind:'state',commit:!!c});},onBeginInteraction:()=>window.events.push({kind:'begin'}),onSelectionChange:setIds,onClearSelection:()=>setIds([]),onSelectNode:n=>window.events.push({kind:'select',id:n.id}),onSelectEdge:e=>window.events.push({kind:'edge',id:e.id}),onFocusNode:n=>window.events.push({kind:'expand',id:n.id}),onPinNodes:(n,p)=>{setState(s=>({...s,pinnedIds:p?[...new Set([...s.pinnedIds,...n])]:s.pinnedIds.filter(x=>!n.includes(x))}));window.events.push({kind:'pin',ids:n});},onHideNodes:n=>setState(s=>({...s,hiddenIds:[...s.hiddenIds,...n]})),onGroupNodes:n=>window.events.push({kind:'group',ids:n}),onCompareNodes:n=>window.events.push({kind:'compare',ids:n})});}
createRoot(document.getElementById('root')).render(React.createElement(App));
</script></body></html>`;
(async () => {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.KG_CHROME_PATH
      ? { executablePath: process.env.KG_CHROME_PATH }
      : {}),
  });
  const page = await browser.newPage({
    viewport: { width: 1200, height: 1000 },
  });
  const errors = [];
  page.on("request", (r) => {
    if (new URL(r.url()).pathname.startsWith("/api/"))
      errors.push("Unexpected backend access in isolated canvas test");
  });
  page.on("pageerror", (e) => {
    errors.push(e.message);
    console.error("PAGE ERROR", e.message);
  });
  page.on("requestfailed", (r) => {
    if (!r.url().startsWith("ws:"))
      console.error("Failed synthetic test asset:", r.url());
  });
  const result = {};
  try {
    await page.route(origin + "/", (r) =>
      r.fulfill({ contentType: "text/html", body: fixture }),
    );
    await page.goto(origin);
    await page.locator("[data-node-id=a]").waitFor();
    const shape = (id) =>
      page.locator("[data-node-id=" + id + "] .graph-node-shape");
    await shape("a").click();
    await page.getByRole("button", { name: "适合所选节点", exact: true }).click();
    const fitSelected = await page.evaluate(() => window.snapshot.view);
    await page.evaluate(() => window.resetFixture());
    await shape("a").click();
    await page.keyboard.press("f");
    result.fit_selected_key = await page.evaluate(
      (expected) => JSON.stringify(window.snapshot.view) === JSON.stringify(expected),
      fitSelected,
    );
    await page.getByRole("button", { name: "适合全部可见节点", exact: true }).click();
    const fitAll = await page.evaluate(() => window.snapshot.view);
    await page.evaluate(() => window.resetFixture());
    await shape("a").click();
    await page.keyboard.press("Shift+f");
    result.fit_all_shift_key = await page.evaluate(
      (expected) => JSON.stringify(window.snapshot.view) === JSON.stringify(expected),
      fitAll,
    );
    result.fit_shortcut_copy =
      (await page.getByRole("button", { name: "适合所选节点", exact: true }).getAttribute("title")) === "适应所选 F" &&
      (await page.getByRole("button", { name: "适合全部可见节点", exact: true }).getAttribute("title")) === "适应可见图谱 Shift+F";
    if (process.env.KG_GRAPH_ONLY_SHORTCUTS === "1") {
      result.errors = errors;
      fs.writeFileSync(path.join(output, "shortcuts.json"), JSON.stringify(result, null, 2));
      console.log(JSON.stringify(result, null, 2));
      if (Object.values(result).some((value) => value === false) || errors.length)
        process.exitCode = 1;
      return;
    }
    await page.evaluate(() => window.resetFixture());
    await page.waitForTimeout(360);
    await page.evaluate(() => { window.events = []; });
    await shape("a").click();
    await shape("b").click({ modifiers: ["Shift"] });
    result.multiselect = await page.evaluate(
      () => window.selected.join(",") === "a,b",
    );
    await page.evaluate(() => window.resetFixture());
    await page.waitForTimeout(100);
    const before = await page.evaluate(() => ({
      state: structuredClone(window.snapshot),
      selects: window.events.filter((x) => x.kind === "select").length,
      commits: window.events.filter((x) => x.commit).length,
    }));
    const box = await shape("a").boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(
      box.x + box.width / 2 + 63,
      box.y + box.height / 2 + 27,
      { steps: 8 },
    );
    await page.mouse.up();
    const after = await page.evaluate(() => ({
      state: structuredClone(window.snapshot),
      selects: window.events.filter((x) => x.kind === "select").length,
      commits: window.events.filter((x) => x.commit).length,
    }));
    result.drag_no_click = after.selects === before.selects;
    result.drag_single_commit = after.commits === before.commits + 1;
    result.drag_position =
      Math.abs(after.state.positions.a.x - before.state.positions.a.x - 70) <
      0.1;
    await page.evaluate(() => window.resetFixture());
    await page.waitForTimeout(100);
    const surface = await page.locator(".graph-canvas-surface").boundingBox();
    const v1 = await page.evaluate(() => window.snapshot.view);
    const point = { x: surface.x + 450, y: surface.y + 270 };
    await page.mouse.move(point.x, point.y);
    await page.mouse.wheel(0, -200);
    await page.waitForTimeout(200);
    const v2 = await page.evaluate(() => window.snapshot.view);
    result.wheel_anchor =
      Math.abs((450 - v1.x) / v1.zoom - (450 - v2.x) / v2.zoom) < 0.01 &&
      v2.zoom > v1.zoom;
    await page.evaluate(() => window.resetFixture());
    await page.waitForTimeout(80);
    await shape("a").dblclick();
    result.double_expand = await page.evaluate(
      () => window.events.filter((x) => x.kind === "expand").length === 1,
    );
    await page.evaluate(() => window.resetFixture());
    await page.waitForTimeout(80);
    await page.mouse.move(surface.x + 20, surface.y + 20);
    await shape("a").hover();
    await page.waitForTimeout(50);
    result.hover_short_delay =
      (await page.locator(".graph-hover-card").count()) === 0 &&
      (await page.locator(".graph-node.is-muted").count()) === 0;
    await page.locator(".graph-hover-card").waitFor({ timeout: 500 });
    result.hover_preview =
      (await page.locator(".graph-node.is-muted").count()) > 0 &&
      (await page.evaluate(() => window.selected.length)) === 0;
    await shape("b").hover();
    await page.waitForTimeout(40);
    await page.mouse.move(surface.x + 20, surface.y + 20);
    await page.waitForTimeout(160);
    result.hover_cancel =
      (await page.locator(".graph-hover-card").count()) === 0 &&
      (await page.locator(".graph-node.is-muted").count()) === 0;
    await page.keyboard.down("Shift");
    await page.mouse.move(surface.x + 100, surface.y + 100);
    await page.mouse.down();
    await page.mouse.move(surface.x + 370, surface.y + 200, { steps: 8 });
    await page.mouse.up();
    await page.keyboard.up("Shift");
    result.box_select = await page.evaluate(
      () => window.selected.join(",") === "a,b",
    );
    await page.keyboard.press("p");
    result.pin_key = await page.evaluate(
      () => window.snapshot.pinnedIds.length === 2,
    );
    await page.keyboard.down("Space");
    await page.keyboard.press("Escape");
    result.escape_exits_pan_first =
      (await page.locator(".is-pan-tool").count()) === 0 &&
      (await page.evaluate(() => window.selected.length)) === 2;
    await page.keyboard.up("Space");
    await page.keyboard.press("Escape");
    result.escape_clear = await page.evaluate(
      () => window.selected.length === 0,
    );
    result.bundle_preserves_layers =
      (await page.locator(".graph-edge-candidate").count()) === 1 &&
      (await page.locator(".is-bundle").count()) === 1;
    await page.locator(".is-bundle").click();
    result.bundle_expand =
      (await page.locator(".is-bundle").count()) === 0 &&
      (await page.locator(".graph-edge-saved").count()) === 4;
    await page.evaluate(() => window.addFixtureNode());
    await page.waitForTimeout(100);
    result.expansion_camera = await page.evaluate(
      () =>
        window.snapshot.view.zoom === 0.9 &&
        window.snapshot.view.x === 40 &&
        window.snapshot.positions.a.x === 120 &&
        !!window.snapshot.positions.new,
    );
    for (const mode of ["relation", "direction", "center"]) {
      await page.evaluate((m) => window.useLayout(m), mode);
      await page.waitForFunction(
        () => Object.keys(window.snapshot.positions).length === 9,
        { timeout: 15000 },
      );
      result["layout_" + mode] = await page.evaluate(
        () =>
          Object.values(window.snapshot.positions).every(
            (p) => Number.isFinite(p.x) && Number.isFinite(p.y),
          ) && !document.body.innerText.includes("自动布局暂不可用"),
      );
    }
    await page
      .getByRole("button", { name: "导出可见子图", exact: true })
      .click();
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出 SVG", exact: true }).click();
    const download = await downloadPromise;
    const exported = path.join(output, "visible-graph.svg");
    await download.saveAs(exported);
    result.export_scope = fs
      .readFileSync(exported, "utf8")
      .includes("不包含未加载");
    const pngPromise = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "导出可见子图", exact: true })
      .click();
    await page.getByRole("button", { name: "导出 PNG", exact: true }).click();
    const png = await pngPromise;
    await png.saveAs(path.join(output, "visible-graph.png"));
    result.png_export =
      fs.statSync(path.join(output, "visible-graph.png")).size > 100 &&
      fs
        .readFileSync(path.join(output, "visible-graph.png"))
        .subarray(0, 8)
        .equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
    await page.evaluate(() => window.resetFixture());
    await page.waitForTimeout(100);
    const resizeBefore = await page.evaluate(() => ({
      view: window.snapshot.view,
      width: document.querySelector(".graph-canvas-surface").clientWidth,
    }));
    await page.evaluate(
      () => (document.getElementById("root").style.width = "500px"),
    );
    await page.waitForTimeout(100);
    const resizeAfter = await page.evaluate(() => ({
      view: window.snapshot.view,
      width: document.querySelector(".graph-canvas-surface").clientWidth,
    }));
    result.resize_world_center =
      Math.abs(
        (resizeBefore.width / 2 - resizeBefore.view.x) /
          resizeBefore.view.zoom -
          (resizeAfter.width / 2 - resizeAfter.view.x) / resizeAfter.view.zoom,
      ) < 0.1;
    await page.evaluate(
      () => (document.getElementById("root").style.width = "1100px"),
    );
    await page.waitForTimeout(100);
    await shape("b").click();
    result.selected_label_screen_px = await page
      .locator("[data-node-id=b] .graph-node-label")
      .evaluate(
        (e) =>
          parseFloat(getComputedStyle(e).fontSize) *
            window.snapshot.view.zoom >=
          11.99,
      );
    const cdp = await page.context().newCDPSession(page);
    await cdp.send("Emulation.setTouchEmulationEnabled", {
      enabled: true,
      maxTouchPoints: 2,
    });
    const touchArea = await page.locator(".graph-canvas-surface").boundingBox();
    const touchBefore = await page.evaluate(() => window.snapshot.view.zoom);
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchStart",
      touchPoints: [{ x: touchArea.x + 300, y: touchArea.y + 280, id: 1 }],
    });
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchStart",
      touchPoints: [
        { x: touchArea.x + 300, y: touchArea.y + 280, id: 1 },
        { x: touchArea.x + 450, y: touchArea.y + 280, id: 2 },
      ],
    });
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchMove",
      touchPoints: [
        { x: touchArea.x + 270, y: touchArea.y + 280, id: 1 },
        { x: touchArea.x + 480, y: touchArea.y + 280, id: 2 },
      ],
    });
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchEnd",
      touchPoints: [],
    });
    await page.waitForTimeout(150);
    result.simulated_touch_pinch = await page.evaluate(
      (z) => window.snapshot.view.zoom > z,
      touchBefore,
    );
    await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: false });
    await page.evaluate(() => {
      const b = document.createElement("button");
      b.id = "outside-action";
      b.textContent = "Synthetic outside action";
      b.onclick = () => (window.outsideViewAtClick = window.snapshot.view.zoom);
      document.body.prepend(b);
    });
    const outsideBefore = await page.evaluate(() => window.snapshot.view.zoom);
    await page.evaluate(() =>
      document.querySelector(".graph-canvas-surface").dispatchEvent(
        new WheelEvent("wheel", {
          bubbles: true,
          cancelable: true,
          deltaY: -150,
          clientX: 450,
          clientY: 350,
        }),
      ),
    );
    await page.locator("#outside-action").click();
    result.outside_action_flush = await page.evaluate(
      (z) => window.outsideViewAtClick > z,
      outsideBefore,
    );
    await page.evaluate(() => window.performanceGraph());
    await page.waitForFunction(
      () => document.querySelectorAll("[data-node-id]").length === 300,
    );
    await page.waitForTimeout(250);
    result.performance = {
      hardware: {
        cpu: require("node:os").cpus()[0].model,
        logicalCores: require("node:os").cpus().length,
        totalMemoryGB: Math.round(require("node:os").totalmem() / 1024 ** 3),
        browser: browser.version(),
        headless: true,
        viewport: "1200x1000",
      },
      samples: [],
    };
    for (let sample = 0; sample < Number(process.env.KG_GRAPH_PERF_SAMPLES ?? 3); sample++) {
      for (const phase of ["pan", "zoom"]) {
        await page.evaluate(() => window.performanceGraph());
        await page.waitForTimeout(100);
        const area = await page.locator(".graph-canvas-surface").boundingBox();
        if (phase === "pan") {
          await page.mouse.move(area.x + 45, area.y + 400);
          await page.mouse.down();
        }
        const measurement = await page.evaluate(
          async ({ phase }) => {
            const svg = document.querySelector(".graph-svg"),
              world = document.querySelector(".graph-world"),
              rect = document
                .querySelector(".graph-canvas-surface")
                .getBoundingClientRect(),
              frames = [],
              responses = [];
            let start = 0,
              last = 0,
              index = 0,
              pending = null;
            await new Promise((resolve) => {
              const tick = (now) => {
                if (!start) {
                  start = now;
                  last = now;
                } else frames.push(now - last);
                last = now;
                if (
                  pending &&
                  world.getAttribute("transform") !== pending.before
                ) {
                  responses.push(performance.now() - pending.time);
                  pending = null;
                }
                if (now - start >= 4000) {
                  resolve();
                  return;
                }
                if (!pending) {
                  pending = {
                    time: performance.now(),
                    before: world.getAttribute("transform"),
                  };
                  if (phase === "pan")
                    svg.dispatchEvent(
                      new PointerEvent("pointermove", {
                        pointerId: 1,
                        pointerType: "mouse",
                        bubbles: true,
                        buttons: 1,
                        clientX: rect.left + 75 + Math.sin(index / 20) * 65,
                        clientY: rect.top + 400 + Math.cos(index / 20) * 25,
                      }),
                    );
                  else
                    svg.dispatchEvent(
                      new WheelEvent("wheel", {
                        bubbles: true,
                        cancelable: true,
                        deltaY: Math.sin(index / 12) * 8 + 2,
                        clientX: rect.left + 450,
                        clientY: rect.top + 260,
                      }),
                    );
                  index++;
                }
                requestAnimationFrame(tick);
              };
              requestAnimationFrame(tick);
            });
            const percentile = (xs, p) =>
              [...xs].sort((a, b) => a - b)[
                Math.min(xs.length - 1, Math.floor(xs.length * p))
              ];
            return {
              phase,
              frames: frames.length,
              fps: (frames.length * 1000) / frames.reduce((a, b) => a + b, 0),
              frameP95Ms: percentile(frames, 0.95),
              domResponseP95Ms: percentile(responses, 0.95),
              inputs: responses.length,
              nodes: document.querySelectorAll("[data-node-id]").length,
              edges: document.querySelectorAll(".graph-world>.graph-edge")
                .length,
            };
          },
          { phase },
        );
        if (phase === "pan") await page.mouse.up();
        result.performance.samples.push(measurement);
      }
    }
    // Native mouse input is separate from the programmatic RAF camera samples.
    // Observe the selected DOM state and wait one more animation frame.
    await page.evaluate(() => {
      document.addEventListener(
        "pointerdown",
        (event) => {
          const id = event.target
            .closest?.("[data-node-id]")
            ?.getAttribute("data-node-id");
          if (id === window.selectionTarget)
            window.selectionStart = performance.now();
        },
        true,
      );
    });
    const selectionSamples = [];
    for (let index = 0; index < 50; index++) {
      const id = `n${(index * 7) % 300}`;
      await page.evaluate((target) => {
        window.selectionTarget = target;
        window.selectionStart = 0;
        window.selectionPromise = new Promise((resolve, reject) => {
          const watchdog = setTimeout(
            () => reject(new Error(`Selection did not respond: ${target}`)),
            3000,
          );
          const poll = () => {
            const node = document.querySelector(`[data-node-id="${target}"]`);
            if (
              window.selectionStart &&
              node?.classList.contains("is-selected")
            )
              requestAnimationFrame(() => {
                clearTimeout(watchdog);
                resolve(performance.now() - window.selectionStart);
              });
            else requestAnimationFrame(poll);
          };
          requestAnimationFrame(poll);
        });
      }, id);
      const box = await page
        .locator(`[data-node-id="${id}"] .graph-node-shape`)
        .boundingBox();
      if (!box) throw new Error("Synthetic selection target unavailable");
      await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
      selectionSamples.push(await page.evaluate(() => window.selectionPromise));
    }
    const orderedSelection = [...selectionSamples].sort((a, b) => a - b);
    result.performance.selection = {
      method:
        "50 native mouse clicks; browser pointerdown to selected DOM class plus the following animation frame",
      samples: selectionSamples.length,
      p95Ms: orderedSelection[Math.ceil(orderedSelection.length * 0.95) - 1],
      medianMs: orderedSelection[Math.floor(orderedSelection.length / 2)],
      maximumMs: Math.max(...selectionSamples),
      rawMs: selectionSamples,
    };
    result.selection_under_100ms = result.performance.selection.p95Ms <= 100;
    fs.writeFileSync(
      path.join(output, "performance.json"),
      JSON.stringify(result.performance, null, 2),
    );
    await page.screenshot({
      path: path.join(output, "canvas.png"),
      fullPage: true,
    });
    result.errors = errors;
    fs.writeFileSync(
      path.join(output, "results.json"),
      JSON.stringify(result, null, 2),
    );
    console.log(JSON.stringify(result, null, 2));
    if (
      Object.entries(result).some(([k, v]) => k !== "errors" && !v) ||
      errors.length
    )
      process.exitCode = 1;
  } catch (e) {
    console.error(e);
    console.log(JSON.stringify(result));
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
