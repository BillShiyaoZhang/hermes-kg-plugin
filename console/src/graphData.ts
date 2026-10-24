import type { GraphNode, GraphEdge } from "./graphModel";
import type {
  OntologySchema,
  OntologyEntityRecord,
  OntologyRelationRecord,
} from "./ontologyTypes";
import type { Review } from "./types";
import { isPendingReview } from "./mutationState";
import { relationLabel } from "./ontologyHelpers";
import { ontologyDefinitionDescription } from "./ontologyPresentation";
import { ontologyDefinitionLabel, ontologyTypeLabel } from "./ontologyV3";

export interface GraphSnapshot {
  namespace: string;
  scope: "current_namespace_active";
  nodes: OntologyEntityRecord[];
  edges: (OntologyRelationRecord & {
    id: string;
    preview?: boolean;
    properties_truncated?: boolean;
  })[];
  total_nodes: number;
  total_edges: number;
  edge_scope: "returned_nodes";
  truncated_nodes: boolean;
  truncated_edges: boolean;
  focus_found: boolean | null;
}
export interface GraphQuery {
  q?: string;
  types?: string[];
  focus_uri?: string | null;
  node_limit?: number;
  edge_limit?: number;
  relation_types?: string[];
}
export const REVIEW_PAGE_SIZE = 20;
export const CANDIDATE_NODE_LIMIT = 60;
export const savedId = (uri: string) => `saved:${uri}`;
export const reviewId = (id: string) => `review:${id}`;
const text = (value: unknown) => (typeof value === "string" ? value : "");

/** Candidate identities never overwrite a saved entity, even with the same URI. */
export function workspaceGraph(
  schema: OntologySchema | null,
  snapshot: GraphSnapshot | null,
  reviews: Review[],
  reviewPage = 0,
  query = "",
) {
  const nodes: GraphNode[] = [],
    edges: GraphEdge[] = [];
  const definitions = new Map<string, unknown>();
  for (const entity of schema?.entity_types ?? []) {
    const id = `schema:${entity.key}`;
    nodes.push({
      id,
      label: ontologyDefinitionLabel(entity),
      type: entity.key,
      layer: "schema",
      description: ontologyDefinitionDescription(entity),
      disabled: entity.active === false,
      custom: entity.custom,
    });
    definitions.set(id, entity);
  }
  let anyNeeded = false;
  for (const relation of schema?.relations ?? []) {
    const id = `schema:relation:${relation.key}`;
    nodes.push({
      id,
      label: relationLabel(relation),
      type: "relation",
      layer: "schema",
      description: ontologyDefinitionDescription(relation),
      disabled: relation.active === false,
      custom: relation.custom,
    });
    definitions.set(id, relation);
    for (const [direction, types] of [
      ["domain", relation.domain],
      ["range", relation.range],
    ] as const) {
      for (const type of types) {
        if (type === "any") anyNeeded = true;
        const endpoint = `schema:${type}`;
        edges.push({
          id: `${id}:${direction}:${type}`,
          source: direction === "domain" ? endpoint : id,
          target: direction === "domain" ? id : endpoint,
          kind: "definition",
          label: direction === "domain" ? "起点类型" : "终点类型",
          disabled: relation.active === false,
          custom: relation.custom,
          description: `${relationLabel(relation)} · ${direction === "domain" ? "允许的起点" : "允许的终点"}：${ontologyTypeLabel(type, schema ?? undefined)}`,
        });
      }
    }
  }
  if (anyNeeded)
    nodes.push({
      id: "schema:any",
      label: "任意业务类型",
      type: "any",
      layer: "schema",
    });
  for (const record of snapshot?.nodes ?? []) {
    nodes.push({
      id: savedId(record.uri),
      label: record.name || "未命名实体",
      type: record.type,
      layer: "saved",
      uri: record.uri,
      description: record.description,
      eventTime:
        typeof record.properties?.hermes_eventTime === "string"
          ? record.properties.hermes_eventTime
          : undefined,
      updatedAt: record.updated_at,
    });
    edges.push({
      id: `instance:${record.uri}`,
      source: `schema:${record.type}`,
      target: savedId(record.uri),
      kind: "instance",
      label: "属于类型",
    });
  }
  for (const edge of snapshot?.edges ?? []) {
    edges.push({
      id: `saved-edge:${edge.id}`,
      source: savedId(edge.source.uri),
      target: savedId(edge.target.uri),
      kind: "saved",
      label: relationLabel(
        schema?.relations.find((item) => item.key === edge.relation_type) ?? {
          key: edge.relation_type,
          label: edge.relation_type,
        },
      ),
      description: edge.relation_type,
      relationType: edge.relation_type,
    });
  }
  const pending = reviews.filter((r) => isPendingReview(r.status));
  const q = query.trim().toLocaleLowerCase();
  const matching = pending.filter(
    (review) =>
      !q ||
      JSON.stringify([
        review.reason,
        review.entities.map((e) => [e.uri, e.name, e.description]),
        review.relations,
      ])
        .toLocaleLowerCase()
        .includes(q),
  );
  const batches = matching.slice(
    reviewPage * REVIEW_PAGE_SIZE,
    (reviewPage + 1) * REVIEW_PAGE_SIZE,
  );
  let candidateCount = 0,
    omittedCandidates = 0;
  for (const review of batches) {
    const batch = reviewId(review.id);
    nodes.push({
      id: batch,
      label: review.entities[0]?.name
        ? `审阅：${review.entities[0].name}`
        : "待审批次",
      type: "review",
      layer: "review",
      reviewId: review.id,
      description: review.reason,
    });
    const endpoints = new Map<string, string>();
    for (const [index, entity] of review.entities.entries()) {
      if (candidateCount >= CANDIDATE_NODE_LIMIT) {
        omittedCandidates++;
        continue;
      }
      candidateCount++;
      const id = `candidate:${review.id}:${index}:${entity.uri}`;
      if (!endpoints.has(entity.uri)) endpoints.set(entity.uri, id);
      nodes.push({
        id,
        label: entity.name,
        type: entity.type,
        layer: "candidate",
        uri: entity.uri,
        reviewId: review.id,
        description: entity.description,
      });
      edges.push({
        id: `${batch}:${index}`,
        source: batch,
        target: id,
        kind: "review",
        label: "待审内容",
        reviewId: review.id,
      });
      edges.push({
        id: `candidate-type:${id}`,
        source: `schema:${entity.type}`,
        target: id,
        kind: "instance",
        label: "候选类型",
      });
      if (snapshot?.nodes.some((n) => n.uri === entity.uri))
        edges.push({
          id: `proposal:${id}`,
          source: id,
          target: savedId(entity.uri),
          kind: "candidate",
          label: "拟更新",
          description: "候选与当前记录分别保留，批准整个批次后才可能生效。",
          reviewId: review.id,
        });
    }
    const endpoint = (uri: string) => {
      if (!uri) return undefined;
      if (endpoints.has(uri)) return endpoints.get(uri);
      if (snapshot?.nodes.some((n) => n.uri === uri)) return savedId(uri);
      if (candidateCount >= CANDIDATE_NODE_LIMIT) {
        omittedCandidates++;
        return undefined;
      }
      candidateCount++;
      const id = `candidate-endpoint:${review.id}:${uri}`;
      endpoints.set(uri, id);
      const tail = uri.slice(uri.lastIndexOf(":") + 1);
      nodes.push({
        id,
        uri,
        reviewId: review.id,
        type: "unknown",
        layer: "candidate",
        label: `待核对端点 · ${tail || "未命名"}`,
        description:
          "此关系引用的端点不在当前已加载记录中；它不代表一条已保存事实。请在所属批次中核对。",
      });
      edges.push({
        id: `endpoint-batch:${id}`,
        source: batch,
        target: id,
        kind: "review",
        label: "引用待核对",
        reviewId: review.id,
      });
      return id;
    };
    review.relations.forEach((relation, index) => {
      const from = text(relation.from_urn ?? relation.from ?? relation.source),
        to = text(relation.to_urn ?? relation.to ?? relation.target);
      const source = endpoint(from),
        target = endpoint(to);
      if (!source || !target) return;
      const key = text(relation.type ?? relation.relation_type);
      edges.push({
        id: `candidate-edge:${review.id}:${index}`,
        source,
        target,
        kind: "candidate",
        label: `待审 · ${relationLabel(schema?.relations.find((item) => item.key === key) ?? { key, label: key })}`,
        description: "待审提议，批准整个批次后才可能生效。",
        reviewId: review.id,
      });
    });
  }
  return {
    nodes,
    edges,
    definitions,
    pendingCount: pending.length,
    matchingReviewCount: matching.length,
    omittedCandidates,
    batches,
  };
}

/** Filters always prune dangling edges; hiding the center must clear the focus. */
export function visibleWorkspace(
  nodes: GraphNode[],
  edges: GraphEdge[],
  options: {
    types: Set<string>;
    layers: Set<string>;
    query: string;
    showMembership: boolean;
    focusId?: string | null;
  },
) {
  const q = options.query.trim().toLocaleLowerCase();
  let visible = nodes.filter((node) => {
    if (!options.layers.has(node.layer === "review" ? "candidate" : node.layer))
      return false;
    if (
      !["relation", "any", "review", "unknown"].includes(node.type) &&
      !options.types.has(node.type)
    )
      return false;
    // Saved records have already been searched globally by the service.
    return (
      node.layer === "saved" ||
      !!node.reviewId ||
      !q ||
      `${node.label} ${node.description ?? ""} ${node.uri ?? ""}`
        .toLocaleLowerCase()
        .includes(q)
    );
  });
  let ids = new Set(visible.map((n) => n.id));
  let visibleEdges = edges.filter(
    (e) =>
      ids.has(e.source) &&
      ids.has(e.target) &&
      (options.showMembership || e.kind !== "instance"),
  );
  const focus =
    options.focusId && ids.has(options.focusId) ? options.focusId : null;
  if (focus) {
    const neighbors = new Set([focus]);
    visibleEdges.forEach((e) => {
      if (e.source === focus) neighbors.add(e.target);
      if (e.target === focus) neighbors.add(e.source);
    });
    visible = visible.filter((n) => neighbors.has(n.id));
    ids = neighbors;
    visibleEdges = visibleEdges.filter(
      (e) => ids.has(e.source) && ids.has(e.target),
    );
  }
  return { nodes: visible, edges: visibleEdges, focusId: focus };
}
