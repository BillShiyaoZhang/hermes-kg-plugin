import type { GraphQuery, GraphSnapshot } from "./graphData";
import { savedId } from "./graphData";
import type { GraphEdge, GraphNode } from "./graphModel";
import { relationLabel } from "./ontologyHelpers";

export type GraphLens = "ontology" | "explore" | "review";
export type LayoutMode = "relation" | "direction" | "type" | "center";
export const TYPE_KEYS = [
  "person",
  "event",
  "preference",
  "decision",
  "location",
  "concept",
];
export interface GraphOverview {
  namespace: string;
  scope: "current_namespace_active";
  stats_available: boolean;
  stats_error: { error_type: string } | null;
  total_nodes: number | null;
  total_edges: number | null;
  groups: { type: string; count: number }[];
  relations: {
    source_type: string;
    target_type: string;
    relation_type: string;
    count: number;
  }[];
}
export interface NeighborQuery {
  center_uri: string;
  direction: "in" | "out" | "both";
  relation_types: string[];
  cursor?: string | null;
  limit?: number;
}
export interface NeighborSnapshot extends GraphSnapshot {
  center_uri: string;
  total_neighbors: number;
  next_cursor: string | null;
  has_more: boolean;
  relation_counts: {
    relation_type: string;
    direction: "in" | "out";
    count: number;
  }[];
}
export interface PathQuery {
  source_uri: string;
  target_uri: string;
  mode: "shortest" | "common_neighbors";
  direction: "out" | "both";
  relation_types: string[];
  max_hops?: number;
  limit?: number;
}
export interface PathSnapshot extends GraphSnapshot {
  paths: { node_uris: string[]; edge_ids: string[] }[];
  max_hops: number;
  truncated: boolean;
  timed_out: boolean;
}
export interface Expansion {
  query: NeighborQuery;
  snapshot: NeighborSnapshot;
}
export const emptySnapshot = (namespace = ""): GraphSnapshot => ({
  namespace,
  scope: "current_namespace_active",
  edge_scope: "returned_nodes",
  nodes: [],
  edges: [],
  total_nodes: 0,
  total_edges: 0,
  truncated_nodes: false,
  truncated_edges: false,
  focus_found: null,
});

/** Completeness is a server fact, never inferred from the number of visible glyphs. */
export function presentationMode(
  overview: GraphOverview | null,
  snapshot: GraphSnapshot | null,
  local: boolean,
) {
  if (local) return "local" as const;
  const known =
    overview?.stats_available &&
    [overview.total_nodes, overview.total_edges].every(
      (count) =>
        typeof count === "number" &&
        Number.isFinite(count) &&
        Number.isInteger(count) &&
        count >= 0,
    );
  if (known && (overview.total_nodes! > 200 || overview.total_edges! > 500))
    return "aggregate" as const;
  if (snapshot?.truncated_nodes || snapshot?.truncated_edges)
    return known ? ("aggregate" as const) : ("partial" as const);
  return known &&
    snapshot &&
    overview.namespace === snapshot.namespace &&
    overview.total_nodes === snapshot.nodes.length &&
    overview.total_edges === snapshot.edges.length
    ? ("complete" as const)
    : ("partial" as const);
}
export function aggregateGraph(overview: GraphOverview) {
  const positiveCount = (count: number) =>
    Number.isFinite(count) && Number.isInteger(count) && count > 0;
  const nodes: GraphNode[] = (overview.stats_available ? overview.groups : [])
    .filter((g) => positiveCount(g.count))
    .map((g) => ({
      id: `aggregate:${g.type}`,
      type: g.type,
      label: `${typeTitle(g.type)} · ${g.count.toLocaleString("zh-CN")} 条`,
      layer: "aggregate",
      count: g.count,
      description: "当前空间中实际有效记录的集合；不是本体定义。",
    }));
  const ids = new Set(nodes.map((n) => n.id));
  const edges: GraphEdge[] = overview.relations
    .filter(
      (r) =>
        positiveCount(r.count) &&
        ids.has(`aggregate:${r.source_type}`) &&
        ids.has(`aggregate:${r.target_type}`),
    )
    .map((r) => ({
      id: `aggregate-edge:${r.source_type}:${r.relation_type}:${r.target_type}`,
      source: `aggregate:${r.source_type}`,
      target: `aggregate:${r.target_type}`,
      kind: "aggregate",
      count: r.count,
      relationType: r.relation_type,
      label: `${relationLabel({ key: r.relation_type, label: r.relation_type })} · ${r.count}`,
      description: "完整统计中的实际关系数量，不代表允许连接规则。",
    }));
  return { nodes, edges };
}
export function typeTitle(type: string) {
  return (
    (
      {
        person: "人物",
        event: "事件",
        preference: "偏好",
        decision: "决策",
        location: "地点",
        concept: "概念",
        any: "任意业务类型",
        review: "审阅批次",
        unknown: "待核对",
        group: "临时分组",
      } as Record<string, string>
    )[type] ?? type
  );
}

/** Every scope owns its data. Removing an expansion cannot delete another's nodes. */
export function mergeSnapshots(
  base: GraphSnapshot | null,
  expansions: Record<string, Expansion>,
  keepIds: string[] = [],
  retained?: GraphSnapshot | null,
  path?: GraphSnapshot | null,
): GraphSnapshot {
  const inputs = [
    base,
    ...Object.values(expansions).map((e) => e.snapshot),
    path,
  ].filter((s): s is GraphSnapshot => !!s);
  const namespace =
    base?.namespace ||
    inputs.find((source) => !!source.namespace)?.namespace ||
    "";
  const sources = inputs.filter((source) => source.namespace === namespace);
  const nodes = new Map<string, GraphSnapshot["nodes"][number]>();
  const edges = new Map<string, GraphSnapshot["edges"][number]>();
  for (const source of sources) {
    for (const node of source.nodes) nodes.set(node.uri, node);
    for (const edge of source.edges) edges.set(edge.id, edge);
  }
  const keep = new Set(keepIds);
  for (const node of retained?.namespace === namespace ? retained.nodes : [])
    if (keep.has(savedId(node.uri)) && !nodes.has(node.uri))
      nodes.set(node.uri, node);
  const values = [...nodes.values()];
  const connectedEdges = [...edges.values()].filter(
    (e) => nodes.has(e.source.uri) && nodes.has(e.target.uri),
  );
  return {
    ...(base ?? emptySnapshot(namespace)),
    namespace,
    nodes: values,
    edges: connectedEdges,
    // These are loaded objects, not a claim of a complete merged induced graph.
    total_nodes: values.length,
    total_edges: connectedEdges.length,
  };
}
export function boundGraph(
  nodes: GraphNode[],
  edges: GraphEdge[],
  hiddenIds: string[],
  maxNodes = 300,
  maxEdges = 800,
) {
  const hidden = new Set(hiddenIds);
  const eligible = nodes.filter((n) => !hidden.has(n.id));
  const eligibleIds = new Set(eligible.map((n) => n.id));
  const eligibleEdges = edges.filter(
    (e) => eligibleIds.has(e.source) && eligibleIds.has(e.target),
  );
  const visible = eligible.slice(0, Math.max(0, Math.floor(maxNodes)));
  const ids = new Set(visible.map((n) => n.id));
  const valid = eligibleEdges
    .filter((e) => ids.has(e.source) && ids.has(e.target))
    .slice(0, Math.max(0, Math.floor(maxEdges)));
  return {
    nodes: visible,
    edges: valid,
    omittedNodes: eligible.length - visible.length,
    omittedEdges: eligibleEdges.length - valid.length,
  };
}
export function graphQueryKey(query: GraphQuery) {
  return JSON.stringify(query);
}
export function timeVisible(
  nodes: GraphNode[],
  start: string,
  end: string,
  includeUnknown: boolean,
) {
  return nodes.filter((n) => {
    if (
      n.type !== "event" ||
      ["schema", "aggregate", "group"].includes(n.layer)
    )
      return true;
    const day = n.eventTime?.match(/^(\d{4}-\d{2}-\d{2})(?:T|\s|$)/)?.[1];
    const timestamp = day ? Date.parse(`${day}T00:00:00Z`) : NaN;
    if (
      !day ||
      !Number.isFinite(timestamp) ||
      new Date(timestamp).toISOString().slice(0, 10) !== day
    )
      return includeUnknown;
    return (!start || day >= start) && (!end || day <= end);
  });
}
export interface TemporaryGroup {
  id: string;
  label: string;
  memberIds: string[];
}
export function groupGraph(
  nodes: GraphNode[],
  edges: GraphEdge[],
  groups: TemporaryGroup[],
) {
  const owner = new Map<string, string>();
  const visibleGroups: GraphNode[] = [];
  for (const group of groups) {
    const members = nodes.filter(
      (n) => group.memberIds.includes(n.id) && !owner.has(n.id),
    );
    if (members.length < 2) continue;
    members.forEach((n) => owner.set(n.id, group.id));
    visibleGroups.push({
      id: group.id,
      type: "group",
      layer: "group",
      label: `${group.label} · ${members.length}`,
      memberIds: members.map((n) => n.id),
      count: members.length,
      description: "仅分组当前已加载对象；不表示全库集合或业务关系。",
    });
  }
  const ids = new Set(nodes.map((node) => node.id));
  const internalCounts = new Map<string, number>();
  const mappedEdges: GraphEdge[] = [];
  for (const edge of edges) {
    if (!ids.has(edge.source) || !ids.has(edge.target)) continue;
    const source = owner.get(edge.source) ?? edge.source;
    const target = owner.get(edge.target) ?? edge.target;
    if (source === target && owner.has(edge.source)) {
      internalCounts.set(source, (internalCounts.get(source) ?? 0) + 1);
      continue;
    }
    const mapped = source !== edge.source || target !== edge.target;
    mappedEdges.push({
      ...edge,
      source,
      target,
      ...(mapped
        ? {
            kind: "group" as const,
            description: `当前视图的分组映射连线；原对象 ${edge.source} → ${edge.target}。不表示集合本身的业务关系。`,
            memberIds: [edge.id],
          }
        : {}),
    });
  }
  return {
    nodes: [
      ...nodes.filter((n) => !owner.has(n.id)),
      ...visibleGroups.map((group) => ({
        ...group,
        description: `${group.description}${internalCounts.get(group.id) ? ` 内部包含 ${internalCounts.get(group.id)} 条关系，解散分组后可逐条查看。` : ""}`,
      })),
    ],
    edges: mappedEdges,
  };
}
export interface History<T> {
  past: T[];
  present: T;
  future: T[];
}
export function pushHistory<T>(history: History<T>, next: T): History<T> {
  return {
    past: [...history.past, history.present].slice(-40),
    present: next,
    future: [],
  };
}
export function stepHistory<T>(
  history: History<T>,
  direction: "back" | "forward",
): History<T> {
  if (direction === "back" && history.past.length)
    return {
      past: history.past.slice(0, -1),
      present: history.past[history.past.length - 1],
      future: [history.present, ...history.future],
    };
  if (direction === "forward" && history.future.length)
    return {
      past: [...history.past, history.present],
      present: history.future[0],
      future: history.future.slice(1),
    };
  return history;
}
