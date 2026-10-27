import { describe, expect, it } from "vitest";
import { createGraphDragSimulation, relationGraphLayout } from "./graphPhysics";
import type { GraphEdge, GraphNode } from "./graphModel";

const node = (id: string): GraphNode => ({
  id,
  label: id,
  type: "concept",
  layer: "saved",
});
const edge = (source: string, target: string): GraphEdge => ({
  id: `${source}:${target}`,
  source,
  target,
  label: "related",
  kind: "saved",
});
const distance = (a: { x: number; y: number }, b: { x: number; y: number }) =>
  Math.hypot(a.x - b.x, a.y - b.y);

describe("topology-driven relation layout", () => {
  const nodes = ["user", "a", "b", "c", "d", "e", "f", "isolated"].map(node);
  const edges = [
    edge("a", "b"),
    edge("a", "c"),
    edge("b", "c"),
    edge("d", "e"),
    edge("d", "f"),
    edge("e", "f"),
    ...["a", "b", "c", "d", "e", "f"].map((id) => edge("user", id)),
  ];
  it("keeps dense circles closer than unrelated circles despite a shared user hub", async () => {
    const before = JSON.stringify({ nodes, edges });
    const positions = await relationGraphLayout(nodes, edges);
    const within =
      (distance(positions.a, positions.b) +
        distance(positions.a, positions.c) +
        distance(positions.d, positions.e) +
        distance(positions.d, positions.f)) /
      4;
    const between =
      (distance(positions.a, positions.d) +
        distance(positions.b, positions.e) +
        distance(positions.c, positions.f)) /
      3;
    expect(between).toBeGreaterThan(within * 1.5);
    expect(JSON.stringify({ nodes, edges })).toBe(before);
    for (const point of Object.values(positions))
      expect(Number.isFinite(point.x) && Number.isFinite(point.y)).toBe(true);
    for (const [id, point] of Object.entries(positions))
      for (const [other, next] of Object.entries(positions))
        if (id !== other) expect(distance(point, next)).toBeGreaterThan(175);
  });
  it("has reproducible positions independent of input ordering, ignores invalid links and self-links", async () => {
    const first = await relationGraphLayout(nodes, edges);
    expect(
      await relationGraphLayout(
        [...nodes].reverse(),
        [...edges, edge("a", "a"), edge("a", "outside")].reverse(),
      ),
    ).toEqual(first);
    expect(await relationGraphLayout([], [])).toEqual({});
  });
  it("adjusts the related neighborhood, exactly follows the pointer, preserves pins and distant nodes, then stops", () => {
    const positions = {
      a: { x: 0, y: 0 },
      b: { x: 220, y: 0 },
      c: { x: 440, y: 0 },
      pin: { x: 220, y: 230 },
      distant: { x: 1100, y: 0 },
    };
    const simulation = createGraphDragSimulation(
      Object.keys(positions).map(node),
      [edge("a", "b"), edge("b", "c"), edge("b", "pin")],
      positions,
      ["a"],
      ["pin"],
    );
    simulation.move({ a: { x: -220, y: -150 } });
    let moved = simulation.tick(12);
    expect(moved.a).toEqual({ x: -220, y: -150 });
    expect(distance(moved.b, positions.b)).toBeGreaterThan(10);
    expect(moved.pin).toEqual(positions.pin);
    expect(moved.distant).toEqual(positions.distant);
    while (!simulation.done) moved = simulation.tick();
    expect(simulation.done).toBe(true);
    expect(moved.a).toEqual({ x: -220, y: -150 });
    expect(positions.a).toEqual({ x: 0, y: 0 });
    simulation.stop();
  });
  it("does not propagate motion through a ubiquitous user hub", () => {
    const ids = [
      "selected",
      "hub",
      ...Array.from({ length: 30 }, (_, i) => `leaf${i}`),
    ];
    const positions = Object.fromEntries(
      ids.map((id, i) => [id, { x: i * 210, y: (i % 2) * 220 }]),
    );
    const simulation = createGraphDragSimulation(
      ids.map(node),
      ids.filter((id) => id !== "hub").map((id) => edge("hub", id)),
      positions,
      ["selected"],
      [],
    );
    simulation.move({ selected: { x: -200, y: -100 } });
    const moved = simulation.tick(40);
    expect(moved.leaf29).toEqual(positions.leaf29);
    simulation.stop();
  });
  it("reclusters an existing view while preserving exact pinned coordinates and leaves caller data untouched", async () => {
    const positions = Object.fromEntries(
      nodes.map((node, index) => [node.id, { x: index * 200, y: 400 }]),
    );
    const before = JSON.stringify(positions);
    const result = await relationGraphLayout(nodes, edges, undefined, {
      positions,
      pinnedIds: ["a", "d"],
    });
    expect(result.a).toEqual(positions.a);
    expect(result.d).toEqual(positions.d);
    expect(result.b).not.toEqual(positions.b);
    expect(JSON.stringify(positions)).toEqual(before);
  });
});
