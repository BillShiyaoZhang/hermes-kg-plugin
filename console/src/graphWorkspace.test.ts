import { describe, expect, it } from "vitest";
import type { GraphSnapshot } from "./graphData";
import { savedId } from "./graphData";
import type { GraphEdge, GraphNode } from "./graphModel";
import type { OntologyEntityRecord } from "./ontologyTypes";
import {
  aggregateGraph,
  boundGraph,
  emptySnapshot,
  groupGraph,
  mergeSnapshots,
  presentationMode,
  pushHistory,
  stepHistory,
  timeVisible,
} from "./graphWorkspace";
import type { Expansion, GraphOverview, History } from "./graphWorkspace";

const record = (id: string, name = id): OntologyEntityRecord => ({
  uri: `test:${id}`,
  name,
  description: "",
  type: "person",
  properties: {},
});
const snapshot = (
  ids: string[],
  edges: [string, string, string][] = [],
  overrides: Partial<GraphSnapshot> = {},
): GraphSnapshot => ({
  ...emptySnapshot("test:"),
  nodes: ids.map((id) => record(id)),
  edges: edges.map(([id, source, target]) => ({
    id,
    relation_type: "RELATED_TO",
    stored_type: "RELATED_TO",
    properties: {},
    source: record(source),
    target: record(target),
  })),
  total_nodes: ids.length,
  total_edges: edges.length,
  ...overrides,
});
const overview = (
  nodes: number | null,
  edges: number | null,
  overrides: Partial<GraphOverview> = {},
): GraphOverview => ({
  namespace: "test:",
  scope: "current_namespace_active",
  stats_available: true,
  stats_error: null,
  total_nodes: nodes,
  total_edges: edges,
  groups: [],
  relations: [],
  ...overrides,
});
const expansion = (data: GraphSnapshot): Expansion => ({
  query: {
    center_uri: data.nodes[0]?.uri || "test:root",
    direction: "both",
    relation_types: [],
  },
  snapshot: {
    ...data,
    center_uri: data.nodes[0]?.uri || "test:root",
    total_neighbors: data.nodes.length - 1,
    next_cursor: null,
    has_more: false,
    relation_counts: [],
  },
});
const node = (id: string, overrides: Partial<GraphNode> = {}): GraphNode => ({
  id,
  type: "person",
  label: id,
  layer: "saved",
  ...overrides,
});
const edge = (id: string, source: string, target: string): GraphEdge => ({
  id,
  source,
  target,
  label: "相关",
  kind: "saved",
  relationType: "RELATED_TO",
});

describe("workspace completeness", () => {
  it("only declares a complete graph when measured totals match all loaded objects", () => {
    expect(
      presentationMode(
        overview(2, 1),
        snapshot(["a", "b"], [["ab", "a", "b"]]),
        false,
      ),
    ).toBe("complete");
    expect(presentationMode(overview(0, 0), snapshot([]), false)).toBe(
      "complete",
    );
    expect(presentationMode(overview(3, 1), snapshot(["a", "b"]), false)).toBe(
      "partial",
    );
    expect(
      presentationMode(
        overview(2, 1, { namespace: "other:" }),
        snapshot(["a", "b"], [["ab", "a", "b"]]),
        false,
      ),
    ).toBe("partial");
  });
  it("uses aggregation for measured large or truncated graphs and local mode for deliberate exploration", () => {
    expect(presentationMode(overview(201, 0), snapshot(["a"]), false)).toBe(
      "aggregate",
    );
    expect(presentationMode(overview(20, 501), snapshot(["a"]), false)).toBe(
      "aggregate",
    );
    expect(
      presentationMode(
        overview(2, 1),
        snapshot(["a"], [], { truncated_nodes: true }),
        false,
      ),
    ).toBe("aggregate");
    expect(presentationMode(overview(2000, 9000), snapshot(["a"]), true)).toBe(
      "local",
    );
  });
  it("keeps unknown or invalid statistics partial even when the sample is truncated", () => {
    const truncated = snapshot(["a"], [], { truncated_nodes: true });
    expect(presentationMode(null, truncated, false)).toBe("partial");
    expect(presentationMode(overview(null, null), truncated, false)).toBe(
      "partial",
    );
    expect(
      presentationMode(
        overview(1, 0, { stats_available: false }),
        truncated,
        false,
      ),
    ).toBe("partial");
    expect(
      presentationMode(overview(Number.POSITIVE_INFINITY, 0), truncated, false),
    ).toBe("partial");
    expect(presentationMode(overview(1, null), snapshot(["a"]), false)).toBe(
      "partial",
    );
  });
});

describe("actual-data aggregation", () => {
  const data = overview(9, 6, {
    groups: [
      { type: "person", count: 5 },
      { type: "location", count: 3 },
      { type: "concept", count: 1 },
      { type: "event", count: 0 },
    ],
    relations: [
      {
        source_type: "person",
        target_type: "location",
        relation_type: "HERMES_locatedAt",
        count: 2,
      },
      {
        source_type: "location",
        target_type: "person",
        relation_type: "RELATED_TO",
        count: 1,
      },
      {
        source_type: "person",
        target_type: "person",
        relation_type: "HERMES_knows",
        count: 3,
      },
      {
        source_type: "person",
        target_type: "event",
        relation_type: "RELATED_TO",
        count: 1,
      },
    ],
  });
  it("keeps isolated nonempty types and collection identities distinct from ontology", () => {
    const graph = aggregateGraph(data);
    expect(graph.nodes.map((item) => item.id)).toEqual([
      "aggregate:person",
      "aggregate:location",
      "aggregate:concept",
    ]);
    expect(
      graph.nodes.every(
        (item) => item.layer === "aggregate" && item.count! > 0,
      ),
    ).toBe(true);
    expect(graph.nodes.some((item) => item.id.startsWith("schema:"))).toBe(
      false,
    );
  });
  it("preserves direction, self loops and business relation keys without dangling edges", () => {
    const graph = aggregateGraph(data);
    expect(graph.edges).toHaveLength(3);
    expect(graph.edges[0]).toMatchObject({
      source: "aggregate:person",
      target: "aggregate:location",
      relationType: "HERMES_locatedAt",
      kind: "aggregate",
      count: 2,
    });
    expect(graph.edges[1]).toMatchObject({
      source: "aggregate:location",
      target: "aggregate:person",
    });
    expect(graph.edges[2]).toMatchObject({
      source: "aggregate:person",
      target: "aggregate:person",
    });
    expect(aggregateGraph({ ...data, stats_available: false })).toEqual({
      nodes: [],
      edges: [],
    });
  });
});

describe("expansion ownership and collapse", () => {
  const base = snapshot(["root"]);
  const first = expansion(
    snapshot(
      ["root", "a", "shared"],
      [
        ["ra", "root", "a"],
        ["as", "a", "shared"],
      ],
    ),
  );
  const second = expansion(
    snapshot(
      ["root", "b", "shared"],
      [
        ["rb", "root", "b"],
        ["bs", "b", "shared"],
      ],
    ),
  );
  it("retains shared nodes and edges owned by another expansion when one is collapsed", () => {
    const combined = mergeSnapshots(base, { first, second });
    expect(combined.nodes).toHaveLength(4);
    const collapsed = mergeSnapshots(base, { second });
    expect(collapsed.nodes.map((item) => item.uri)).toEqual([
      "test:root",
      "test:b",
      "test:shared",
    ]);
    expect(collapsed.edges.map((item) => item.id)).toEqual(["rb", "bs"]);
    expect(collapsed.nodes.some((item) => item.uri === "test:a")).toBe(false);
  });
  it("retains explicitly pinned objects without resurrecting collapsed scope relationships", () => {
    const combined = mergeSnapshots(base, { first, second });
    const collapsed = mergeSnapshots(base, {}, [savedId("test:a")], combined);
    expect(collapsed.nodes.map((item) => item.uri)).toEqual([
      "test:root",
      "test:a",
    ]);
    expect(collapsed.edges).toEqual([]);
  });
  it("never replaces freshly returned records with stale retained copies", () => {
    const old = snapshot(["root"], [], { nodes: [record("root", "Old name")] });
    const fresh = snapshot(["root"], [], {
      nodes: [record("root", "New name")],
    });
    expect(
      mergeSnapshots(fresh, {}, [savedId("test:root")], old).nodes[0].name,
    ).toBe("New name");
  });
  it("excludes foreign scopes and counts only edges whose endpoints are loaded", () => {
    const foreign = expansion(
      snapshot(["leak"], [], { namespace: "foreign:" }),
    );
    const incomplete = snapshot(["root"], [["dangling", "root", "missing"]]);
    const merged = mergeSnapshots(incomplete, { foreign });
    expect(merged.nodes.map((item) => item.uri)).toEqual(["test:root"]);
    expect(merged.total_edges).toBe(0);
    expect(merged.edges).toEqual([]);
  });
  it("deduplicates path and expansion ownership without discarding isolated path endpoints", () => {
    const path = snapshot(["root", "a", "end"], [["ra", "root", "a"]]);
    const merged = mergeSnapshots(base, { first }, [], null, path);
    expect(merged.nodes.map((item) => item.uri)).toEqual([
      "test:root",
      "test:a",
      "test:shared",
      "test:end",
    ]);
    expect(merged.edges.filter((item) => item.id === "ra")).toHaveLength(1);
  });
});

describe("bounded graph and temporary groups", () => {
  const nodes = [node("a"), node("b"), node("c"), node("d")];
  const edges = [
    edge("ab", "a", "b"),
    edge("bc", "b", "c"),
    edge("cd", "c", "d"),
    edge("loop", "d", "d"),
    edge("outside", "a", "missing"),
  ];
  it("counts links omitted by both node and edge budgets without dangling endpoints", () => {
    const bounded = boundGraph(nodes, edges, [], 2, 1);
    expect(bounded.nodes).toHaveLength(2);
    expect(bounded.edges.map((item) => item.id)).toEqual(["ab"]);
    expect(bounded.omittedNodes).toBe(2);
    expect(bounded.omittedEdges).toBe(3);
    const hidden = boundGraph(nodes, edges, ["b"]);
    expect(hidden.edges.map((item) => item.id)).toEqual(["cd", "loop"]);
    expect(boundGraph(nodes, edges, [], -1, -1).nodes).toEqual([]);
  });
  it("maps external group links as view links and explains internal facts instead of inventing group self loops", () => {
    const graph = groupGraph(nodes, edges, [
      { id: "group:one", label: "Team", memberIds: ["a", "b"] },
    ]);
    expect(graph.nodes.map((item) => item.id)).toEqual(["c", "d", "group:one"]);
    expect(graph.nodes[2].description).toContain("内部包含 1 条关系");
    expect(graph.edges.find((item) => item.id === "bc")).toMatchObject({
      source: "group:one",
      target: "c",
      kind: "group",
      relationType: "RELATED_TO",
      memberIds: ["bc"],
    });
    expect(graph.edges.some((item) => item.id === "ab")).toBe(false);
    expect(graph.edges.find((item) => item.id === "loop")).toMatchObject({
      source: "d",
      target: "d",
      kind: "saved",
    });
    const ids = new Set(graph.nodes.map((item) => item.id));
    expect(
      graph.edges.every((item) => ids.has(item.source) && ids.has(item.target)),
    ).toBe(true);
  });
  it("does not duplicate overlapping members and leaves singleton groups uncollapsed", () => {
    const graph = groupGraph(nodes, edges, [
      { id: "g1", label: "One", memberIds: ["a", "b"] },
      { id: "g2", label: "Two", memberIds: ["b", "c", "d"] },
      { id: "g3", label: "Missing", memberIds: ["not-loaded"] },
    ]);
    expect(graph.nodes).toHaveLength(2);
    expect(graph.nodes[0].memberIds).toEqual(["a", "b"]);
    expect(graph.nodes[1].memberIds).toEqual(["c", "d"]);
  });
});

describe("temporal filtering and history", () => {
  it("filters only actual event objects and treats missing or imprecise dates as unknown", () => {
    const data = [
      node("before", { type: "event", eventTime: "2026-09-30" }),
      node("start", { type: "event", eventTime: "2026-10-01T12:00:00+08:00" }),
      node("end", { type: "event", eventTime: "2026-10-31" }),
      node("after", { type: "event", eventTime: "2026-11-01" }),
      node("unknown", { type: "event" }),
      node("imprecise", { type: "event", eventTime: "2026-10" }),
      node("person"),
      node("schema:event", { type: "event", layer: "schema" }),
      node("aggregate:event", { type: "event", layer: "aggregate", count: 12 }),
    ];
    expect(
      timeVisible(data, "2026-10-01", "2026-10-31", false).map(
        (item) => item.id,
      ),
    ).toEqual(["start", "end", "person", "schema:event", "aggregate:event"]);
    expect(
      timeVisible(data, "2026-10-01", "2026-10-31", true).map(
        (item) => item.id,
      ),
    ).toContain("unknown");
    expect(
      timeVisible(data, "2026-10-01", "2026-10-31", true).map(
        (item) => item.id,
      ),
    ).toContain("imprecise");
  });
  it("restores the entire interaction context on back/forward and clears redo after a new action", () => {
    const first = {
      query: "alpha",
      selection: ["a"],
      canvas: { x: 10, zoom: 1 },
    };
    const second = {
      query: "beta",
      selection: ["b"],
      canvas: { x: 70, zoom: 2 },
    };
    const initial: History<typeof first> = {
      past: [],
      present: first,
      future: [],
    };
    const next = pushHistory(initial, second);
    const back = stepHistory(next, "back");
    expect(back.present).toEqual(first);
    expect(stepHistory(back, "forward").present).toEqual(second);
    expect(pushHistory(back, { ...first, query: "gamma" }).future).toEqual([]);
    expect(initial.present).toEqual(first);
  });
  it("bounds undo history and preserves a stable state at either boundary", () => {
    let history: History<number> = { past: [], present: 0, future: [] };
    for (let value = 1; value <= 60; value++)
      history = pushHistory(history, value);
    expect(history.past).toHaveLength(40);
    expect(
      stepHistory({ past: [], present: 1, future: [] }, "back").present,
    ).toBe(1);
    expect(stepHistory(history, "forward")).toBe(history);
  });
});
