export type GraphLayer =
  "schema" | "saved" | "candidate" | "review" | "aggregate" | "group";
export type GraphEdgeKind =
  | "definition"
  | "instance"
  | "saved"
  | "candidate"
  | "review"
  | "aggregate"
  | "group";

export interface GraphNode {
  id: string;
  label: string;
  type: string;
  layer: GraphLayer;
  uri?: string;
  reviewId?: string;
  description?: string;
  count?: number;
  memberIds?: string[];
  eventTime?: string;
  updatedAt?: string;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  kind: GraphEdgeKind;
  reviewId?: string;
  description?: string;
  count?: number;
  relationType?: string;
  memberIds?: string[];
}

export interface PositionedGraphNode extends GraphNode {
  x: number;
  y: number;
}

export interface GraphBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface GraphLayout {
  nodes: PositionedGraphNode[];
  byId: Map<string, PositionedGraphNode>;
  bounds: GraphBounds;
}

export const GRAPH_LAYER_LABELS: Record<GraphLayer, string> = {
  schema: "类型与规则",
  saved: "已保存",
  candidate: "待审候选",
  review: "审阅事项",
  aggregate: "实际记录集合",
  group: "当前视图分组",
};

export const GRAPH_TYPE_LABELS: Record<string, string> = {
  person: "人物",
  event: "事件",
  preference: "偏好",
  decision: "决策",
  location: "地点",
  concept: "概念",
  relation: "关系定义",
  any: "任意业务类型",
  review: "审阅批次",
  unknown: "未分类",
};

export const GRAPH_TYPE_COLORS: Record<
  string,
  { fill: string; stroke: string; ink: string }
> = {
  person: { fill: "#e9f1fb", stroke: "#315e96", ink: "#234b7a" },
  event: { fill: "#e8f3ed", stroke: "#32694b", ink: "#28543d" },
  preference: { fill: "#f7ebf2", stroke: "#8b466d", ink: "#713654" },
  decision: { fill: "#f1edfa", stroke: "#72539e", ink: "#5d4187" },
  location: { fill: "#e6f2f1", stroke: "#286c68", ink: "#215650" },
  concept: { fill: "#faf0dc", stroke: "#846019", ink: "#6b4b15" },
  relation: { fill: "#eef0f4", stroke: "#56657b", ink: "#405167" },
  any: { fill: "#f3f2ed", stroke: "#736c55", ink: "#5c5542" },
  unknown: { fill: "#eef1ee", stroke: "#596d60", ink: "#415448" },
};

const TYPE_ORDER = [
  "person",
  "event",
  "preference",
  "decision",
  "location",
  "concept",
];
const LAYER_ORDER: Record<GraphLayer, number> = {
  schema: 0,
  saved: 1,
  candidate: 2,
  review: 3,
  aggregate: 4,
  group: 5,
};

const compareIds = (a: GraphNode, b: GraphNode) =>
  LAYER_ORDER[a.layer] - LAYER_ORDER[b.layer] || a.id.localeCompare(b.id, "en");

/** Candidate identities are scoped to their review, never merged with facts. */
export function graphNodeId(
  layer: GraphLayer,
  uri: string,
  reviewId = "",
): string {
  return [layer, reviewId, uri].map(encodeURIComponent).join(":");
}

export function graphBounds(
  nodes: Pick<PositionedGraphNode, "x" | "y">[],
): GraphBounds {
  if (!nodes.length) return { x: 0, y: 0, width: 400, height: 280 };
  const left = Math.min(...nodes.map((node) => node.x)) - 90;
  const top = Math.min(...nodes.map((node) => node.y)) - 48;
  const right = Math.max(...nodes.map((node) => node.x)) + 90;
  const bottom = Math.max(...nodes.map((node) => node.y)) + 74;
  return { x: left, y: top, width: right - left, height: bottom - top };
}

/** Reverse edges use canonical bends so reciprocal facts remain distinct. */
export function graphEdgeGeometry(
  source: PositionedGraphNode,
  target: PositionedGraphNode,
  bend: number,
  loopIndex = 0,
) {
  if (source.id === target.id) {
    const radius = 104 + Math.max(0, loopIndex) * 28;
    const collection = source.layer === "aggregate" || source.layer === "group";
    const endpointX = source.layer === "schema" || collection ? 42 : 28;
    const endpointY = collection ? -37 : source.layer === "schema" ? -34 : -8;
    return {
      d: `M ${source.x + endpointX} ${source.y + endpointY} C ${source.x + radius} ${source.y - radius}, ${source.x - radius} ${source.y - radius}, ${source.x - endpointX} ${source.y + endpointY}`,
      x: source.x,
      y: source.y - radius * 0.75,
    };
  }
  const canonicalBend =
    bend * (source.id.localeCompare(target.id, "en") <= 0 ? 1 : -1);
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  const length = Math.max(1, Math.hypot(dx, dy));
  const ux = dx / length,
    uy = dy / length;
  const clearance = (node: GraphNode) =>
    node.layer === "schema"
      ? Math.min(71 / Math.abs(ux), 29 / Math.abs(uy)) + 5
      : node.layer === "aggregate" || node.layer === "group"
        ? Math.min(56 / Math.abs(ux), 32 / Math.abs(uy)) + 5
        : node.layer === "review"
          ? 33 / (Math.abs(ux) + Math.abs(uy)) + 5
          : 32;
  const sourceClearance = clearance(source),
    targetClearance = clearance(target);
  const sx = source.x + ux * sourceClearance;
  const sy = source.y + uy * sourceClearance;
  const tx = target.x - ux * targetClearance;
  const ty = target.y - uy * targetClearance;
  const mx = (sx + tx) / 2 - (dy / length) * canonicalBend;
  const my = (sy + ty) / 2 + (dx / length) * canonicalBend;
  return {
    d: `M ${sx} ${sy} Q ${mx} ${my} ${tx} ${ty}`,
    x: (sx + tx) / 2 - ((dy / length) * canonicalBend) / 2,
    y: (sy + ty) / 2 + ((dx / length) * canonicalBend) / 2,
  };
}

/** A deterministic, bounded layout; no simulation or background animation. */
export function layoutGraph(input: GraphNode[]): GraphLayout {
  const unique = [...new Map(input.map((node) => [node.id, node])).values()];
  const definitions = unique
    .filter((node) => node.type === "relation" || node.type === "any")
    .sort(compareIds);
  const otherTypes = [...new Set(unique.map((node) => node.type))]
    .filter(
      (type) =>
        !TYPE_ORDER.includes(type) && !["relation", "any"].includes(type),
    )
    .sort();
  const buckets = [...TYPE_ORDER, ...otherTypes]
    .map((type) => ({
      type,
      nodes: unique.filter((node) => node.type === type).sort(compareIds),
    }))
    .filter((bucket) => bucket.nodes.length > 0)
    .map((bucket) => {
      const schema = bucket.nodes.filter((node) => node.layer === "schema");
      const instances = bucket.nodes.filter((node) => node.layer !== "schema");
      const columns = Math.max(
        2,
        Math.min(5, Math.ceil(Math.sqrt(instances.length))),
      );
      return {
        ...bucket,
        schema,
        instances,
        columns,
        width: Math.max(columns * 168, schema.length * 168),
        height:
          (schema.length ? 108 : 0) +
          Math.max(1, Math.ceil(instances.length / columns)) * 116,
      };
    });
  const rows: (typeof buckets)[] = [];
  for (let index = 0; index < buckets.length; index += 3)
    rows.push(buckets.slice(index, index + 3));
  const rowWidths = rows.map(
    (row) =>
      row.reduce((sum, bucket) => sum + bucket.width, 0) +
      Math.max(0, row.length - 1) * 88,
  );
  const definitionColumns = Math.min(6, Math.max(1, definitions.length));
  const definitionWidth = definitionColumns * 174;
  const width = Math.max(400, definitionWidth, ...rowWidths);
  const nodes: PositionedGraphNode[] = [];
  definitions.forEach((node, index) => {
    const row = Math.floor(index / definitionColumns);
    const count = Math.min(
      definitionColumns,
      definitions.length - row * definitionColumns,
    );
    nodes.push({
      ...node,
      x: (width - count * 174) / 2 + (index % definitionColumns) * 174 + 87,
      y: 56 + row * 100,
    });
  });
  let rowY = definitions.length
    ? Math.ceil(definitions.length / definitionColumns) * 100 + 114
    : 64;
  rows.forEach((row, rowIndex) => {
    let x = (width - rowWidths[rowIndex]) / 2;
    row.forEach((bucket) => {
      bucket.schema.forEach((node, index) => {
        nodes.push({
          ...node,
          x:
            x +
            bucket.width / 2 +
            (index - (bucket.schema.length - 1) / 2) * 168,
          y: rowY,
        });
      });
      const startY = rowY + (bucket.schema.length ? 108 : 0);
      bucket.instances.forEach((node, index) => {
        const instanceRow = Math.floor(index / bucket.columns);
        const count = Math.min(
          bucket.columns,
          bucket.instances.length - instanceRow * bucket.columns,
        );
        nodes.push({
          ...node,
          x:
            x +
            (bucket.width - count * 168) / 2 +
            (index % bucket.columns) * 168 +
            84,
          y: startY + instanceRow * 116,
        });
      });
      x += bucket.width + 88;
    });
    rowY += Math.max(...row.map((bucket) => bucket.height)) + 100;
  });
  nodes.sort((a, b) => a.y - b.y || a.x - b.x || compareIds(a, b));
  return {
    nodes,
    byId: new Map(nodes.map((node) => [node.id, node])),
    bounds: graphBounds(nodes),
  };
}

/** A temporary compact neighborhood; the overview layout remains untouched. */
export function layoutFocusedGraph(
  input: GraphNode[],
  focusId: string,
): GraphLayout {
  const unique = [...new Map(input.map((node) => [node.id, node])).values()];
  const center = unique.find((node) => node.id === focusId);
  if (!center) return layoutGraph(input);
  const neighbors = unique
    .filter((node) => node.id !== focusId)
    .sort(compareIds);
  const nodes: PositionedGraphNode[] = [{ ...center, x: 0, y: 0 }];
  if (neighbors.length <= 8) {
    const radius = [0, 180, 180, 180, 180, 190, 225, 235, 255][
      neighbors.length
    ];
    neighbors.forEach((node, index) => {
      const angle =
        neighbors.length === 1
          ? 0
          : (neighbors.length === 3 ? 0 : -Math.PI / 2) +
            (index * Math.PI * 2) / neighbors.length;
      nodes.push({
        ...node,
        x: Math.cos(angle) * radius,
        y: Math.sin(angle) * radius,
      });
    });
  } else {
    // Square grid rings reserve a full label cell around every node. This scales
    // without the overlaps that increasingly dense circular rings would create.
    let index = 0;
    for (let ring = 1; index < neighbors.length; ring++) {
      const cells: { x: number; y: number }[] = [];
      for (let x = -ring; x <= ring; x++) cells.push({ x, y: -ring });
      for (let y = -ring + 1; y <= ring; y++) cells.push({ x: ring, y });
      for (let x = ring - 1; x >= -ring; x--) cells.push({ x, y: ring });
      for (let y = ring - 1; y > -ring; y--) cells.push({ x: -ring, y });
      for (const cell of cells) {
        if (index >= neighbors.length) break;
        nodes.push({ ...neighbors[index++], x: cell.x * 190, y: cell.y * 140 });
      }
    }
  }
  return {
    nodes,
    byId: new Map(nodes.map((node) => [node.id, node])),
    bounds: graphBounds(nodes),
  };
}

export function graphNeighborhood(
  nodeId: string,
  nodes: GraphNode[],
  edges: GraphEdge[],
): Set<string> {
  const available = new Set(nodes.map((node) => node.id));
  if (!available.has(nodeId)) return new Set();
  const result = new Set([nodeId]);
  edges.forEach((edge) => {
    if (edge.source === nodeId && available.has(edge.target))
      result.add(edge.target);
    if (edge.target === nodeId && available.has(edge.source))
      result.add(edge.source);
  });
  return result;
}

export function filterGraph(
  nodes: GraphNode[],
  edges: GraphEdge[],
  options: {
    types?: Set<string>;
    layers?: Set<GraphLayer>;
    query?: string;
    focusId?: string;
  } = {},
): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const query = (options.query || "").trim().toLocaleLowerCase();
  let filtered = nodes.filter(
    (node) =>
      (!options.types || options.types.has(node.type)) &&
      (!options.layers || options.layers.has(node.layer)) &&
      (!query ||
        `${node.label} ${node.description || ""} ${node.type}`
          .toLocaleLowerCase()
          .includes(query)),
  );
  if (options.focusId) {
    const neighborhood = graphNeighborhood(options.focusId, filtered, edges);
    filtered = filtered.filter((node) => neighborhood.has(node.id));
  }
  const ids = new Set(filtered.map((node) => node.id));
  return {
    nodes: filtered,
    edges: edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target)),
  };
}

export function directionalNode(
  current: PositionedGraphNode,
  nodes: PositionedGraphNode[],
  direction: "ArrowLeft" | "ArrowRight" | "ArrowUp" | "ArrowDown",
): PositionedGraphNode | undefined {
  const horizontal = direction === "ArrowLeft" || direction === "ArrowRight";
  const sign = direction === "ArrowLeft" || direction === "ArrowUp" ? -1 : 1;
  return nodes
    .filter((node) => node.id !== current.id)
    .map((node) => {
      const along =
        (horizontal ? node.x - current.x : node.y - current.y) * sign;
      const across = Math.abs(
        horizontal ? node.y - current.y : node.x - current.x,
      );
      return { node, along, score: along + across * 2.5 };
    })
    .filter((value) => value.along > 1)
    .sort(
      (a, b) => a.score - b.score || a.node.id.localeCompare(b.node.id, "en"),
    )[0]?.node;
}
