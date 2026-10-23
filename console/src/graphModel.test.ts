import { describe, expect, it } from "vitest";
import {
  directionalNode,
  filterGraph,
  graphNeighborhood,
  graphEdgeGeometry,
  graphNodeId,
  layoutGraph,
  layoutFocusedGraph,
} from "./graphModel";
import type { GraphEdge, GraphNode } from "./graphModel";

const nodes: GraphNode[] = [
  { id: "type-person", label: "人物", type: "person", layer: "schema" },
  {
    id: "saved-alex",
    label: "Alex",
    type: "person",
    layer: "saved",
    uri: "urn:person:alex",
  },
  {
    id: "candidate-alex",
    label: "Alex",
    type: "person",
    layer: "candidate",
    uri: "urn:person:alex",
    reviewId: "review-a",
  },
  {
    id: "saved-project",
    label: "Project Aurora",
    type: "concept",
    layer: "saved",
  },
  { id: "review-a", label: "需要确认的关系", type: "unknown", layer: "review" },
];
const edges: GraphEdge[] = [
  {
    id: "instance",
    source: "saved-alex",
    target: "type-person",
    label: "属于类型",
    kind: "instance",
  },
  {
    id: "saved",
    source: "saved-alex",
    target: "saved-project",
    label: "参与",
    kind: "saved",
  },
  {
    id: "candidate",
    source: "candidate-alex",
    target: "review-a",
    label: "待确认",
    kind: "review",
  },
  {
    id: "outside",
    source: "saved-alex",
    target: "not-loaded",
    label: "缺失端点",
    kind: "saved",
  },
];

describe("unified graph identities and scope", () => {
  it("keeps a saved entity and each review candidate separate even for identical URIs", () => {
    const ids = [
      graphNodeId("saved", "urn:a"),
      graphNodeId("candidate", "urn:a", "r1"),
      graphNodeId("candidate", "urn:a", "r2"),
    ];
    expect(new Set(ids).size).toBe(3);
    expect(graphNodeId("candidate", "a:b", "r")).not.toBe(
      graphNodeId("candidate", "b", "r:a"),
    );
  });
  it("returns only present one-hop neighbors and never crosses to another candidate", () => {
    expect([...graphNeighborhood("saved-alex", nodes, edges)].sort()).toEqual([
      "saved-alex",
      "saved-project",
      "type-person",
    ]);
    expect([...graphNeighborhood("missing", nodes, edges)]).toEqual([]);
  });
  it("filters both edge endpoints, including when every type is hidden", () => {
    expect(filterGraph(nodes, edges, { types: new Set() })).toEqual({
      nodes: [],
      edges: [],
    });
    const result = filterGraph(nodes, edges, { layers: new Set(["saved"]) });
    expect(result.nodes.map((node) => node.id)).toEqual([
      "saved-alex",
      "saved-project",
    ]);
    expect(result.edges.map((edge) => edge.id)).toEqual(["saved"]);
  });
  it("combines search, layer and focus without exposing hidden nodes", () => {
    const result = filterGraph(nodes, edges, {
      layers: new Set(["saved"]),
      focusId: "saved-alex",
      query: "  ALEX ",
    });
    expect(result.nodes.map((node) => node.id)).toEqual(["saved-alex"]);
    expect(result.edges).toEqual([]);
  });
});

describe("stable graph layout", () => {
  it.each([1, 2, 3, 4, 5, 6, 7, 8])(
    "keeps a focused center and %i neighbors readable without overlapping label cells",
    (count) => {
      const input: GraphNode[] = Array.from(
        { length: count + 1 },
        (_, index) => ({
          id: `focus-${index}`,
          label: `邻居节点 ${index}`,
          type: "person",
          layer: index % 2 ? "saved" : "schema",
        }),
      );
      const focused = layoutFocusedGraph(input, "focus-0");
      expect(focused.byId.get("focus-0")).toMatchObject({ x: 0, y: 0 });
      for (const a of focused.nodes)
        for (const b of focused.nodes) {
          if (a.id !== b.id)
            expect(
              Math.abs(a.x - b.x) >= 180 || Math.abs(a.y - b.y) >= 122,
            ).toBe(true);
        }
      const scale = Math.min(
        (560 - 48) / focused.bounds.width,
        (580 - 92) / focused.bounds.height,
        1.4,
      );
      expect(scale * 14).toBeGreaterThanOrEqual(10);
    },
  );
  it("keeps a large focused neighborhood finite, deterministic and in separate label cells", () => {
    const input: GraphNode[] = Array.from({ length: 200 }, (_, index) => ({
      id: `focus-${String(index).padStart(3, "0")}`,
      label: `Node ${index}`,
      type: "concept",
      layer: "saved",
    }));
    const focused = layoutFocusedGraph(input, "focus-000");
    expect(focused.nodes).toEqual(
      layoutFocusedGraph([...input].reverse(), "focus-000").nodes,
    );
    expect(focused.nodes).toHaveLength(200);
    expect(
      focused.nodes.every(
        (node) => Number.isFinite(node.x) && Number.isFinite(node.y),
      ),
    ).toBe(true);
    expect(focused.bounds.width).toBeLessThan(3500);
    expect(focused.bounds.height).toBeLessThan(3000);
    for (const a of focused.nodes)
      for (const b of focused.nodes) {
        if (a.id !== b.id)
          expect(Math.abs(a.x - b.x) >= 180 || Math.abs(a.y - b.y) >= 122).toBe(
            true,
          );
      }
  });
  it("leaves the stable overview unchanged after creating a temporary focused layout", () => {
    const overview = layoutGraph(nodes);
    layoutFocusedGraph(nodes, "saved-alex");
    expect(layoutGraph(nodes)).toEqual(overview);
  });
  it("keeps reciprocal links and multiple self-relations visually distinct", () => {
    const source = { ...nodes[0], id: "a", x: 100, y: 100 };
    const target = { ...nodes[1], id: "b", x: 300, y: 100 };
    const outgoing = graphEdgeGeometry(source, target, -24);
    const incoming = graphEdgeGeometry(target, source, 24);
    expect(outgoing.y).toBeLessThan(100);
    expect(incoming.y).toBeGreaterThan(100);
    const firstLoop = graphEdgeGeometry(source, source, -24, 0);
    const secondLoop = graphEdgeGeometry(source, source, 24, 1);
    expect(firstLoop.d).not.toBe(secondLoop.d);
    expect(secondLoop.y).toBeLessThan(firstLoop.y);
  });
  it("is deterministic under input ordering, including type definitions and review nodes", () => {
    const expanded: GraphNode[] = [
      ...nodes,
      { id: "rule-works", label: "参与", type: "relation", layer: "schema" },
      { id: "any", label: "任意类型", type: "any", layer: "schema" },
    ];
    expect(layoutGraph(expanded).nodes).toEqual(
      layoutGraph([...expanded].reverse()).nodes,
    );
  });
  it("places 200 nodes on finite, non-overlapping cell centres without a force simulation", () => {
    const input: GraphNode[] = Array.from({ length: 200 }, (_, index) => ({
      id: `node-${String(index).padStart(3, "0")}`,
      label: `Entity ${index}`,
      type: [
        "person",
        "event",
        "preference",
        "decision",
        "location",
        "concept",
      ][index % 6],
      layer: index % 3 === 0 ? "candidate" : "saved",
    }));
    const result = layoutGraph(input);
    expect(result.nodes).toHaveLength(200);
    expect(
      new Set(result.nodes.map((node) => `${node.x}:${node.y}`)).size,
    ).toBe(200);
    expect(
      result.nodes.every(
        (node) => Number.isFinite(node.x) && Number.isFinite(node.y),
      ),
    ).toBe(true);
    expect(result.bounds.width).toBeLessThan(4000);
    expect(result.bounds.height).toBeLessThan(3000);
    for (const a of result.nodes)
      for (const b of result.nodes) {
        if (a.id !== b.id)
          expect(Math.hypot(a.x - b.x, a.y - b.y)).toBeGreaterThanOrEqual(100);
      }
  });
  it("handles empty input and duplicate IDs without NaN or duplicate interactive targets", () => {
    expect(layoutGraph([]).bounds).toEqual({
      x: 0,
      y: 0,
      width: 400,
      height: 280,
    });
    expect(layoutGraph([nodes[0], nodes[0]]).nodes).toHaveLength(1);
  });
  it("moves keyboard focus geometrically and returns no target at a boundary", () => {
    const positioned = [
      { ...nodes[0], x: 100, y: 100 },
      { ...nodes[1], x: 250, y: 100 },
      { ...nodes[2], x: 100, y: 220 },
    ];
    expect(directionalNode(positioned[0], positioned, "ArrowRight")?.id).toBe(
      "saved-alex",
    );
    expect(directionalNode(positioned[0], positioned, "ArrowDown")?.id).toBe(
      "candidate-alex",
    );
    expect(
      directionalNode(positioned[0], positioned, "ArrowLeft"),
    ).toBeUndefined();
  });
});
