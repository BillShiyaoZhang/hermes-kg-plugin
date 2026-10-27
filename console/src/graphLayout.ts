import type { GraphBounds, GraphEdge, GraphNode } from "./graphModel";
import { layoutGraph } from "./graphModel";
import { relationGraphLayout } from "./graphPhysics";

export type GraphLayoutMode = "relation" | "direction" | "type" | "center";
export interface GraphPoint {
  x: number;
  y: number;
}
export interface GraphView extends GraphPoint {
  zoom: number;
}
export type GraphPositions = Record<string, GraphPoint>;
export const MIN_GRAPH_ZOOM = 0.08;
export const MAX_GRAPH_ZOOM = 3;
export const clampGraphZoom = (value: number) =>
  Math.max(MIN_GRAPH_ZOOM, Math.min(MAX_GRAPH_ZOOM, value));

/** Keep the world point below the cursor fixed during zoom. */
export function zoomGraphAt(
  view: GraphView,
  factor: number,
  point: GraphPoint,
): GraphView {
  const zoom = clampGraphZoom(view.zoom * factor);
  const ratio = zoom / view.zoom;
  return {
    zoom,
    x: point.x - (point.x - view.x) * ratio,
    y: point.y - (point.y - view.y) * ratio,
  };
}

export function fitGraphView(
  bounds: GraphBounds,
  width: number,
  height: number,
): GraphView {
  const top = 60,
    bottom = width < 480 ? 160 : 90;
  const availableHeight = Math.max(1, height - top - bottom);
  const zoom = clampGraphZoom(
    Math.min(
      Math.max(1, width - 120) / Math.max(1, bounds.width),
      availableHeight / Math.max(1, bounds.height),
      1.2,
    ),
  );
  return {
    zoom,
    x: width / 2 - (bounds.x + bounds.width / 2) * zoom,
    y: top + availableHeight / 2 - (bounds.y + bounds.height / 2) * zoom,
  };
}

/** A spanning tree is a positioning aid, never a replacement for graph facts. */
export function radialLayoutTree(
  nodes: GraphNode[],
  edges: GraphEdge[],
  center?: string | null,
): GraphEdge[] {
  const ids = new Set(nodes.map((node) => node.id));
  const adjacency = new Map([...ids].map((id) => [id, new Set<string>()]));
  edges.forEach((edge) => {
    if (
      edge.source !== edge.target &&
      ids.has(edge.source) &&
      ids.has(edge.target)
    ) {
      adjacency.get(edge.source)!.add(edge.target);
      adjacency.get(edge.target)!.add(edge.source);
    }
  });
  const roots = [...ids].sort(
    (a, b) =>
      Number(b === center) - Number(a === center) || a.localeCompare(b, "en"),
  );
  const seen = new Set<string>(),
    tree: GraphEdge[] = [];
  for (const root of roots) {
    if (seen.has(root)) continue;
    const queue = [root];
    seen.add(root);
    for (let index = 0; index < queue.length; index++) {
      const source = queue[index];
      for (const target of [...adjacency.get(source)!].sort()) {
        if (seen.has(target)) continue;
        seen.add(target);
        queue.push(target);
        tree.push({
          id: `layout-tree:${source}:${target}`,
          source,
          target,
          label: "",
          kind: "saved",
        });
      }
    }
  }
  return tree;
}

/** Only allocate new cells. Existing coordinates are an invariant of expansion. */
export function insertGraphNodes(
  nodes: GraphNode[],
  edges: GraphEdge[],
  positions: GraphPositions,
  center?: string | null,
): GraphPositions {
  const next = { ...positions };
  const currentIds = new Set(nodes.map((node) => node.id));
  const available = nodes.filter((node) => next[node.id]);
  const fallback =
    center && next[center]
      ? next[center]
      : available.length
        ? next[available[0].id]
        : { x: 0, y: 0 };
  for (const node of [...nodes].sort((a, b) =>
    a.id.localeCompare(b.id, "en"),
  )) {
    if (next[node.id]) continue;
    const related = edges
      .filter((edge) => edge.source === node.id || edge.target === node.id)
      .map((edge) => (edge.source === node.id ? edge.target : edge.source))
      .filter((id) => next[id])
      .sort();
    const memberPositions = (node.memberIds ?? [])
      .map((id) => next[id])
      .filter((point): point is GraphPoint => !!point);
    const collectionCenter = memberPositions.length
      ? {
          x:
            memberPositions.reduce((sum, point) => sum + point.x, 0) /
            memberPositions.length,
          y:
            memberPositions.reduce((sum, point) => sum + point.y, 0) /
            memberPositions.length,
        }
      : null;
    const anchor =
      collectionCenter ??
      (center && related.includes(center)
        ? next[center]
        : related.length
          ? next[related[0]]
          : fallback);
    let candidate = { ...anchor };
    for (let slot = 0; slot < 5000; slot++) {
      const offset = collectionCenter ? slot - 1 : slot;
      const ring = Math.floor(Math.max(0, offset) / 12) + 1;
      const angle = ((Math.max(0, offset) % 12) * Math.PI) / 6;
      candidate =
        collectionCenter && slot === 0
          ? { ...anchor }
          : {
              x: anchor.x + Math.cos(angle) * ring * 200,
              y: anchor.y + Math.sin(angle) * ring * 150,
            };
      if (
        !Object.entries(next).some(
          ([id, point]) =>
            currentIds.has(id) &&
            Math.abs(point.x - candidate.x) < 175 &&
            Math.abs(point.y - candidate.y) < 115,
        )
      )
        break;
    }
    next[node.id] = candidate;
  }
  return next;
}

let engine: Promise<import("elkjs/lib/elk-api.js").ELK> | undefined;
async function layoutEngine() {
  if (!engine)
    engine = import("elkjs/lib/elk-api.js").then(
      ({ default: ELK }) =>
        new ELK({
          algorithms: ["stress", "layered", "radial"],
          // Vite emits this dependency as a same-origin worker asset, never a CDN URL.
          workerUrl: new URL(
            "../node_modules/elkjs/lib/elk-worker.min.js",
            import.meta.url,
          ).href,
        }),
    );
  return engine;
}

export async function computeGraphLayout(
  nodes: GraphNode[],
  edges: GraphEdge[],
  mode: GraphLayoutMode,
  center?: string | null,
  signal?: AbortSignal,
): Promise<GraphPositions> {
  const sorted = [
    ...new Map(nodes.map((node) => [node.id, node])).values(),
  ].sort((a, b) => a.id.localeCompare(b.id, "en"));
  if (mode === "type" || !sorted.length)
    return Object.fromEntries(
      layoutGraph(sorted).nodes.map(({ id, x, y }) => [id, { x, y }]),
    );
  if (mode === "relation") return relationGraphLayout(sorted, edges, signal);
  const ids = new Set(sorted.map((node) => node.id));
  const layoutEdges = (
    mode === "center" ? radialLayoutTree(sorted, edges, center) : edges
  )
    .filter((edge) => ids.has(edge.source) && ids.has(edge.target))
    .sort((a, b) => a.id.localeCompare(b.id, "en"));
  const elk = await layoutEngine();
  const result = await elk.layout({
    id: "graph",
    layoutOptions: {
      "elk.algorithm":
        mode === "direction"
          ? "layered"
          : mode === "center"
            ? "radial"
            : "stress",
      "elk.direction": "RIGHT",
      "elk.spacing.nodeNode": "60",
      "elk.spacing.componentComponent": "100",
      "elk.layered.spacing.nodeNodeBetweenLayers": "100",
      "elk.randomSeed": "42",
      "elk.stress.iterationLimit": "250",
      "elk.radial.radius": "200",
    },
    children: sorted.map((node) => ({
      id: node.id,
      width: node.layer === "schema" ? 170 : 150,
      height: 104,
    })),
    edges: layoutEdges.map((edge) => ({
      id: edge.id,
      sources: [edge.source],
      targets: [edge.target],
    })),
  });
  const positions: GraphPositions = {};
  for (const node of result.children ?? [])
    positions[node.id] = {
      x: (node.x ?? 0) + (node.width ?? 150) / 2,
      y: (node.y ?? 0) + (node.height ?? 104) / 2,
    };
  // Stress can leave overlapping label boxes; resolve only these collisions.
  for (let index = 0; index < sorted.length; index++) {
    const point = positions[sorted[index].id];
    for (let attempt = 0; attempt < sorted.length * 2; attempt++) {
      const collision = sorted
        .slice(0, index)
        .some(
          (node) =>
            Math.abs(positions[node.id].x - point.x) < 170 &&
            Math.abs(positions[node.id].y - point.y) < 112,
        );
      if (!collision) break;
      point.y += 116;
    }
  }
  return positions;
}
