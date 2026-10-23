import { describe, expect, it } from "vitest";
import {
  CANDIDATE_NODE_LIMIT,
  REVIEW_PAGE_SIZE,
  reviewId,
  savedId,
  visibleWorkspace,
  workspaceGraph,
} from "./graphData";
import type { GraphSnapshot } from "./graphData";
import type { GraphEdge, GraphNode } from "./graphModel";
import type {
  OntologyEntityKey,
  OntologyEntityRecord,
  OntologyRelationDefinition,
  OntologySchema,
} from "./ontologyTypes";
import type { Memory, Review } from "./types";

const TYPES: OntologyEntityKey[] = [
  "person",
  "event",
  "preference",
  "decision",
  "location",
  "concept",
];
const namespace = "urn:graph-projection-fixture:kg:";
const uri = (name: string) => namespace + name;

function record(
  name: string,
  type: OntologyEntityKey = "concept",
): OntologyEntityRecord {
  return {
    uri: uri(name),
    type,
    name,
    description: "Saved fact",
    properties: {},
  };
}

function memory(name: string, type: OntologyEntityKey = "concept"): Memory {
  return {
    ...record(name, type),
    description: "Unapplied proposal",
    status: "active",
    readOnly: false,
    participants: [],
    evidence: [],
    relations: [],
    revisions: [],
  };
}

function review(id: string, entities: Memory[] = [memory(id)]): Review {
  return { id, status: "pending", entities, relations: [] };
}

function snapshot(
  nodes: OntologyEntityRecord[] = [],
  edges: GraphSnapshot["edges"] = [],
): GraphSnapshot {
  return {
    namespace,
    scope: "current_namespace_active",
    edge_scope: "returned_nodes",
    nodes,
    edges,
    total_nodes: nodes.length,
    total_edges: edges.length,
    truncated_nodes: false,
    truncated_edges: false,
    focus_found: null,
  };
}

function relation(
  key = "RELATED_TO",
  domain = ["any"],
  range = ["any"],
): OntologyRelationDefinition {
  return {
    key,
    label: key,
    description: "An allowed relationship, not a stored fact",
    iri: `urn:fixture-schema:${key}`,
    storage_type: key,
    aliases: [],
    domain,
    range,
    declared_domain: domain,
    declared_range: range,
    count: 0,
    properties: [],
    runtime_supported: true,
  };
}

function schema(
  relations: OntologyRelationDefinition[] = [relation()],
): OntologySchema {
  return {
    namespace,
    scope: "current_namespace_active",
    read_only: true,
    stats_available: true,
    stats_error: null,
    ontology: {
      iri: "urn:fixture-schema:",
      label: "Fixture ontology",
      description: "Synthetic data only",
      version: "fixture",
    },
    entity_types: TYPES.map((key) => ({
      key,
      label: key,
      description: `Definition of ${key}`,
      iri: `urn:fixture-schema:${key}`,
      storage_label: key,
      runtime_supported: true,
      count: 0,
      properties: [],
    })),
    relations,
    properties: [],
    stats: { entities: 0, relationships: 0, unmapped_relationships: 0 },
    schema_warnings: [],
  };
}

function assertConnectedEndpoints(nodes: GraphNode[], edges: GraphEdge[]) {
  const ids = new Set(nodes.map((node) => node.id));
  for (const edge of edges) {
    expect(ids.has(edge.source), `${edge.id} source`).toBe(true);
    expect(ids.has(edge.target), `${edge.id} target`).toBe(true);
  }
}

function visible(
  graph: { nodes: GraphNode[]; edges: GraphEdge[] },
  options: Partial<Parameters<typeof visibleWorkspace>[2]> = {},
) {
  return visibleWorkspace(graph.nodes, graph.edges, {
    types: new Set(TYPES),
    layers: new Set(["schema", "saved", "candidate"]),
    query: "",
    showMembership: false,
    ...options,
  });
}

describe("saved knowledge and candidate projection", () => {
  it("preserves distinct contents and identities for saved and per-review versions of one URI", () => {
    const original = record("same-uri", "person");
    const first = {
      ...memory("same-uri", "person"),
      name: "First proposed name",
    };
    const second = { ...first, name: "Second proposed name" };
    const graph = workspaceGraph(schema(), snapshot([original]), [
      review("r1", [first]),
      review("r2", [second]),
    ]);
    const versions = graph.nodes.filter((node) => node.uri === original.uri);
    expect(versions.map((node) => node.label)).toEqual([
      "same-uri",
      "First proposed name",
      "Second proposed name",
    ]);
    expect(new Set(versions.map((node) => node.id)).size).toBe(3);
    expect(versions.filter((node) => node.layer === "saved")).toEqual([
      expect.objectContaining({
        id: savedId(original.uri),
        description: "Saved fact",
      }),
    ]);
    const proposals = graph.edges.filter((edge) => edge.label === "拟更新");
    expect(proposals).toHaveLength(2);
    expect(proposals.map((edge) => edge.reviewId)).toEqual(["r1", "r2"]);
    expect(proposals.every((edge) => edge.kind === "candidate")).toBe(true);
    expect(graph.edges.some((edge) => edge.kind === "saved")).toBe(false);
  });

  it("keeps a relation-only proposal traceable even when both endpoints are already saved", () => {
    const left = record("left"),
      right = record("right");
    const batch = review("relations-only", []);
    batch.relations = [
      { from_urn: left.uri, to_urn: right.uri, type: "RELATED_TO" },
      { from: right.uri, to: left.uri, relation_type: "CAUSED_BY" },
    ];
    const graph = workspaceGraph(schema(), snapshot([left, right]), [batch]);
    const proposals = graph.edges.filter((edge) => edge.kind === "candidate");
    expect(proposals).toHaveLength(2);
    expect(proposals.map((edge) => [edge.source, edge.target])).toEqual([
      [savedId(left.uri), savedId(right.uri)],
      [savedId(right.uri), savedId(left.uri)],
    ]);
    expect(proposals.every((edge) => edge.reviewId === batch.id)).toBe(true);
    expect(new Set(proposals.map((edge) => edge.id)).size).toBe(2);
    expect(
      graph.nodes.find((node) => node.id === reviewId(batch.id)),
    ).toBeDefined();
    expect(graph.edges.some((edge) => edge.kind === "saved")).toBe(false);
  });

  it("never infers saved edges from a schema, a proposal, or type membership", () => {
    const candidate = review("not-a-fact", [memory("one"), memory("two")]);
    candidate.relations = [
      { source: uri("one"), target: uri("two"), type: "RELATED_TO" },
    ];
    const graph = workspaceGraph(schema(), snapshot(), [candidate]);
    expect(graph.edges.some((edge) => edge.kind === "definition")).toBe(true);
    expect(graph.edges.some((edge) => edge.kind === "candidate")).toBe(true);
    expect(graph.edges.some((edge) => edge.kind === "saved")).toBe(false);
    const shown = visible(graph);
    expect(shown.edges.some((edge) => edge.kind === "instance")).toBe(false);
    assertConnectedEndpoints(shown.nodes, shown.edges);
  });

  it("represents unloaded relation endpoints as review-scoped proposals, never invented saved entities", () => {
    const batch = review("unloaded-endpoints", []);
    batch.relations = [
      {
        from_urn: uri("unloaded-left"),
        to_urn: uri("unloaded-right"),
        type: "RELATED_TO",
      },
      {
        from_urn: uri("unloaded-right"),
        to_urn: uri("unloaded-left"),
        type: "CAUSED_BY",
      },
    ];
    const graph = workspaceGraph(schema(), snapshot(), [batch]);
    const endpoints = graph.nodes.filter((node) => node.layer === "candidate");
    expect(endpoints).toHaveLength(2);
    expect(new Set(endpoints.map((node) => node.uri)).size).toBe(2);
    expect(
      endpoints.every(
        (node) => node.type === "unknown" && node.reviewId === batch.id,
      ),
    ).toBe(true);
    expect(graph.nodes.some((node) => node.layer === "saved")).toBe(false);
    expect(
      graph.edges.filter((edge) => edge.kind === "candidate"),
    ).toHaveLength(2);
    const shown = visible(graph);
    expect(
      shown.nodes.filter((node) => node.layer === "candidate"),
    ).toHaveLength(2);
    assertConnectedEndpoints(shown.nodes, shown.edges);
    expect(
      visible(graph, { layers: new Set(["schema", "saved"]) }).nodes.some(
        (node) => node.reviewId === batch.id,
      ),
    ).toBe(false);
  });

  it("creates saved edges only from returned facts and preserves parallel identities", () => {
    const left = record("left"),
      right = record("right");
    const fact = {
      id: "fact-1",
      relation_type: "RELATED_TO",
      stored_type: "RELATED_TO",
      source: left,
      target: right,
      properties: { evidence: "Synthetic source" },
    };
    const graph = workspaceGraph(
      schema(),
      snapshot([left, right], [fact, { ...fact, id: "fact-2" }]),
      [],
    );
    expect(
      graph.edges
        .filter((edge) => edge.kind === "saved")
        .map((edge) => edge.id),
    ).toEqual(["saved-edge:fact-1", "saved-edge:fact-2"]);
  });

  it("does not turn accepted, rejected, deferred or unknown-status batches into pending candidates", () => {
    const pending = review("pending");
    const final = [
      "approved",
      "rejected",
      "deferred",
      "applied",
      "unrecognized",
    ].map((status) => ({ ...review(status), status }));
    const graph = workspaceGraph(null, null, [pending, ...final]);
    expect(graph.pendingCount).toBe(1);
    expect(graph.batches.map((batch) => batch.id)).toEqual(["pending"]);
    expect(graph.nodes.every((node) => node.reviewId === "pending")).toBe(true);
  });
});

describe("candidate search and drawing bounds", () => {
  it("finds a match beyond the initial twenty batches before applying pagination", () => {
    const batches = Array.from({ length: 21 }, (_, index) =>
      review(`batch-${index}`),
    );
    batches[20].entities[0].description = "Outside the first page: NEEDLE";
    const ignored = {
      ...review("rejected-needle"),
      status: "rejected",
      reason: "needle",
    };
    const initial = workspaceGraph(null, null, batches);
    expect(initial.batches).toHaveLength(REVIEW_PAGE_SIZE);
    expect(initial.batches.map((batch) => batch.id)).not.toContain("batch-20");
    const found = workspaceGraph(
      null,
      null,
      [...batches, ignored],
      0,
      "  Needle  ",
    );
    expect(found.pendingCount).toBe(21);
    expect(found.matchingReviewCount).toBe(1);
    expect(found.batches.map((batch) => batch.id)).toEqual(["batch-20"]);
    expect(
      visible(found, { query: "needle" }).nodes.map((node) => node.reviewId),
    ).toEqual(["batch-20", "batch-20"]);
  });

  it("retains the whole matching batch as review context when only a relation matches", () => {
    const batch = review("relation-match", [memory("alpha"), memory("beta")]);
    batch.relations = [
      { from_urn: uri("alpha"), to_urn: uri("beta"), type: "UNIQUE_NEEDLE" },
    ];
    const graph = workspaceGraph(schema(), null, [batch], 0, "unique_needle");
    const shown = visible(graph, { query: "unique_needle" });
    expect(graph.matchingReviewCount).toBe(1);
    expect(
      shown.nodes
        .filter((node) => node.layer === "candidate")
        .map((node) => node.label),
    ).toEqual(["alpha", "beta"]);
    expect(
      shown.edges.filter((edge) => edge.kind === "candidate"),
    ).toHaveLength(1);
    assertConnectedEndpoints(shown.nodes, shown.edges);
  });

  it("bounds the graph to twenty review batches and sixty candidate entities, with truthful omissions", () => {
    const batches = Array.from({ length: 21 }, (_, index) =>
      review(
        `r${index}`,
        Array.from({ length: 4 }, (_, entity) =>
          memory(`r${index}-e${entity}`),
        ),
      ),
    );
    const first = workspaceGraph(null, null, batches);
    expect(first.batches).toHaveLength(REVIEW_PAGE_SIZE);
    expect(first.pendingCount).toBe(21);
    expect(first.matchingReviewCount).toBe(21);
    expect(
      first.nodes.filter((node) => node.layer === "candidate"),
    ).toHaveLength(CANDIDATE_NODE_LIMIT);
    expect(first.nodes.filter((node) => node.layer === "review")).toHaveLength(
      REVIEW_PAGE_SIZE,
    );
    expect(first.omittedCandidates).toBe(20);
    const next = workspaceGraph(null, null, batches, 1);
    expect(next.batches.map((batch) => batch.id)).toEqual(["r20"]);
    expect(
      next.nodes.filter((node) => node.layer === "candidate"),
    ).toHaveLength(4);
    expect(next.omittedCandidates).toBe(0);
  });

  it("does not discard a saved alias-only match returned by the global service", () => {
    const match = record("Canonical name");
    match.properties = { hermes_aliasNames: ["Needle alias"] };
    const graph = workspaceGraph(
      null,
      snapshot([match]),
      [],
      0,
      "Needle alias",
    );
    expect(
      visible(graph, { query: "Needle alias" }).nodes.map((node) => node.id),
    ).toEqual([savedId(match.uri)]);
  });
});

describe("unloaded endpoint bounds", () => {
  it("includes endpoint proposals in the candidate budget and omits over-budget edges safely", () => {
    const batch = review(
      "nearly-full",
      Array.from({ length: CANDIDATE_NODE_LIMIT - 1 }, (_, index) =>
        memory(`known-${index}`),
      ),
    );
    batch.relations = [
      {
        from_urn: uri("extra-left"),
        to_urn: uri("extra-right"),
        type: "RELATED_TO",
      },
    ];
    const graph = workspaceGraph(schema(), snapshot(), [batch]);
    expect(
      graph.nodes.filter((node) => node.layer === "candidate"),
    ).toHaveLength(CANDIDATE_NODE_LIMIT);
    expect(graph.omittedCandidates).toBeGreaterThan(0);
    expect(
      graph.edges.filter((edge) => edge.kind === "candidate"),
    ).toHaveLength(0);
    const shown = visible(graph, { showMembership: true });
    assertConnectedEndpoints(shown.nodes, shown.edges);
  });
});

describe("definition rules and visible graph integrity", () => {
  it("represents any-to-any once through the definition instead of creating thirty-six type pairs", () => {
    const graph = workspaceGraph(schema(), null, []);
    expect(graph.nodes.filter((node) => node.type === "any")).toHaveLength(1);
    expect(graph.nodes.filter((node) => node.type === "relation")).toHaveLength(
      1,
    );
    expect(graph.edges).toEqual([
      expect.objectContaining({
        source: "schema:any",
        target: "schema:relation:RELATED_TO",
        kind: "definition",
      }),
      expect.objectContaining({
        source: "schema:relation:RELATED_TO",
        target: "schema:any",
        kind: "definition",
      }),
    ]);
    expect(graph.definitions.size).toBe(7);
    expect(
      graph.nodes.filter((node) =>
        TYPES.includes(node.type as OntologyEntityKey),
      ),
    ).toHaveLength(6);
  });

  it("prunes both endpoints for each hidden business type and each hidden layer", () => {
    const records = TYPES.map((type) => record(type, type));
    const proposals = review(
      "all-types",
      TYPES.map((type) => memory(type, type)),
    );
    const facts = records.map((source, index) => ({
      id: `ring-${index}`,
      relation_type: "RELATED_TO",
      stored_type: "RELATED_TO",
      source,
      target: records[(index + 1) % records.length],
      properties: {},
    }));
    const graph = workspaceGraph(schema(), snapshot(records, facts), [
      proposals,
    ]);
    for (const hidden of TYPES) {
      const shown = visible(graph, {
        types: new Set(TYPES.filter((type) => type !== hidden)),
        showMembership: true,
      });
      expect(shown.nodes.some((node) => node.type === hidden)).toBe(false);
      assertConnectedEndpoints(shown.nodes, shown.edges);
    }
    for (const hidden of ["schema", "saved", "candidate"]) {
      const shown = visible(graph, {
        layers: new Set(
          ["schema", "saved", "candidate"].filter((layer) => layer !== hidden),
        ),
        showMembership: true,
      });
      expect(
        shown.nodes.some(
          (node) =>
            (node.layer === "review" ? "candidate" : node.layer) === hidden,
        ),
      ).toBe(false);
      assertConnectedEndpoints(shown.nodes, shown.edges);
    }
    expect(visible(graph, { layers: new Set() })).toEqual({
      nodes: [],
      edges: [],
      focusId: null,
    });
  });

  it("focuses only one undirected hop and clears a focus whose center is hidden", () => {
    const nodes: GraphNode[] = ["a", "b", "c", "unrelated"].map((id) => ({
      id,
      label: id,
      type: id === "a" ? "person" : "concept",
      layer: "saved",
    }));
    const edges: GraphEdge[] = [
      {
        id: "incoming",
        source: "b",
        target: "a",
        kind: "saved",
        label: "related",
      },
      {
        id: "second-hop",
        source: "b",
        target: "c",
        kind: "saved",
        label: "related",
      },
      { id: "loop", source: "a", target: "a", kind: "saved", label: "self" },
      {
        id: "not-loaded",
        source: "a",
        target: "missing",
        kind: "saved",
        label: "hidden",
      },
    ];
    const focused = visible({ nodes, edges }, { focusId: "a" });
    expect(focused.nodes.map((node) => node.id)).toEqual(["a", "b"]);
    expect(focused.edges.map((edge) => edge.id)).toEqual(["incoming", "loop"]);
    const hidden = visible(
      { nodes, edges },
      { focusId: "a", types: new Set(["concept"]) },
    );
    expect(hidden.focusId).toBeNull();
    expect(hidden.nodes.map((node) => node.id)).toEqual([
      "b",
      "c",
      "unrelated",
    ]);
    assertConnectedEndpoints(hidden.nodes, hidden.edges);
  });

  it("does not expose absent endpoints even for malformed input from a stale snapshot", () => {
    const left = record("left"),
      hidden = record("not-returned");
    const graph = workspaceGraph(
      schema(),
      snapshot(
        [left],
        [
          {
            id: "outside",
            relation_type: "RELATED_TO",
            stored_type: "RELATED_TO",
            source: left,
            target: hidden,
            properties: {},
          },
        ],
      ),
      [],
    );
    const shown = visible(graph, { showMembership: true });
    expect(shown.edges.some((edge) => edge.id === "saved-edge:outside")).toBe(
      false,
    );
    assertConnectedEndpoints(shown.nodes, shown.edges);
  });
});
