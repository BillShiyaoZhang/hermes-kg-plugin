import { describe, expect, it } from "vitest";
import type { GraphEdge, GraphNode } from "./graphModel";
import {
  computeGraphLayout,
  fitGraphView,
  insertGraphNodes,
  radialLayoutTree,
  zoomGraphAt,
} from "./graphLayout";

const node = (id: string): GraphNode => ({
  id,
  label: id,
  type: "concept",
  layer: "saved",
});
const edge = (source: string, target: string): GraphEdge => ({
  id: `${source}-${target}`,
  source,
  target,
  kind: "saved",
  label: "related",
});

describe("continuous graph positioning", () => {
  it("keeps the pointer's world coordinate fixed at both zoom limits", () => {
    const original = { x: -320, y: 145, zoom: 0.55 },
      pointer = { x: 413, y: 219 };
    for (const factor of [0.0001, 0.8, 1.25, 100]) {
      const next = zoomGraphAt(original, factor, pointer);
      expect((pointer.x - next.x) / next.zoom).toBeCloseTo(
        (pointer.x - original.x) / original.zoom,
      );
      expect((pointer.y - next.y) / next.zoom).toBeCloseTo(
        (pointer.y - original.y) / original.zoom,
      );
      expect(next.zoom).toBeGreaterThanOrEqual(0.08);
      expect(next.zoom).toBeLessThanOrEqual(3);
    }
  });
  it("fits a negative-coordinate neighborhood and preserves its center", () => {
    const bounds = { x: -650, y: -420, width: 1100, height: 750 };
    const view = fitGraphView(bounds, 960, 600);
    expect((bounds.x + bounds.width / 2) * view.zoom + view.x).toBeCloseTo(480);
    expect((bounds.y + bounds.height / 2) * view.zoom + view.y).toBeCloseTo(
      285,
    );
    expect(bounds.width * view.zoom).toBeLessThanOrEqual(840);
    expect(bounds.height * view.zoom).toBeLessThanOrEqual(450);
  });
  it("keeps the default narrow-screen fit above the wrapped bottom toolbar", () => {
    const bounds = { x: -300, y: -250, width: 700, height: 900 };
    const view = fitGraphView(bounds, 390, 720);
    expect(bounds.y * view.zoom + view.y).toBeGreaterThanOrEqual(60);
    expect((bounds.y + bounds.height) * view.zoom + view.y).toBeLessThanOrEqual(
      560,
    );
    expect((bounds.x + bounds.width / 2) * view.zoom + view.x).toBeCloseTo(195);
  });
  it("adds two overlapping expansions without moving or mutating existing coordinates", () => {
    const original = { a: { x: 400, y: 300 }, b: { x: 700, y: 300 } };
    const first = insertGraphNodes(
      [node("a"), node("b"), node("c"), node("d")],
      [edge("a", "c"), edge("a", "d")],
      original,
      "a",
    );
    const second = insertGraphNodes(
      [node("a"), node("b"), node("c"), node("d"), node("e")],
      [edge("b", "c"), edge("b", "e")],
      first,
      "b",
    );
    expect(original).toEqual({ a: { x: 400, y: 300 }, b: { x: 700, y: 300 } });
    expect(second.a).toEqual(original.a);
    expect(second.b).toEqual(original.b);
    expect(second.c).toEqual(first.c);
    expect(second.d).toEqual(first.d);
    for (const [a, p] of Object.entries(second))
      for (const [b, q] of Object.entries(second)) {
        if (a !== b)
          expect(Math.abs(p.x - q.x) >= 175 || Math.abs(p.y - q.y) >= 115).toBe(
            true,
          );
      }
  });
  it("uses stable new-node ordering so identical expansion results keep their mental map", () => {
    const nodes = [node("a"), node("b"), node("c")],
      edges = [edge("a", "b"), edge("a", "c")],
      original = { a: { x: 0, y: 0 } };
    expect(insertGraphNodes(nodes, edges, original, "a")).toEqual(
      insertGraphNodes(
        [...nodes].reverse(),
        [...edges].reverse(),
        original,
        "a",
      ),
    );
  });
  it("collapses a whole graph into its member centroid without forgetting positions needed by ungroup", () => {
    const positions = { a: { x: 400, y: 300 }, b: { x: 700, y: 300 } };
    const group: GraphNode = {
      id: "group",
      label: "Local collection",
      type: "concept",
      layer: "group",
      memberIds: ["a", "b"],
      count: 2,
    };
    const grouped = insertGraphNodes([group], [], positions);
    expect(grouped.group).toEqual({ x: 550, y: 300 });
    expect(grouped.a).toEqual(positions.a);
    expect(grouped.b).toEqual(positions.b);
    expect(insertGraphNodes([node("a"), node("b")], [], grouped).a).toEqual(
      positions.a,
    );
  });
});

describe("layout-only radial tree", () => {
  it("keeps cycles, reciprocal facts and self-links out of the positioning tree without changing facts", () => {
    const nodes = [node("a"), node("b"), node("c"), node("isolated")];
    const facts = [
      edge("a", "b"),
      edge("b", "a"),
      edge("b", "c"),
      edge("c", "a"),
      edge("a", "a"),
      edge("a", "outside"),
    ];
    const before = JSON.stringify(facts),
      tree = radialLayoutTree(nodes, facts, "b");
    expect(tree).toHaveLength(2);
    expect(tree.every((item) => item.source === "b")).toBe(true);
    expect(new Set(tree.map((item) => item.target)).size).toBe(2);
    expect(JSON.stringify(facts)).toBe(before);
    expect(
      radialLayoutTree([...nodes].reverse(), [...facts].reverse(), "b"),
    ).toEqual(tree);
  });
  it("retains finite type positions for empty or duplicate inputs", async () => {
    expect(await computeGraphLayout([], [], "type")).toEqual({});
    const result = await computeGraphLayout(
      [node("a"), node("a"), node("b")],
      [],
      "type",
    );
    expect(Object.keys(result)).toEqual(["a", "b"]);
    expect(
      Object.values(result).every(
        (point) => Number.isFinite(point.x) && Number.isFinite(point.y),
      ),
    ).toBe(true);
  });
});
