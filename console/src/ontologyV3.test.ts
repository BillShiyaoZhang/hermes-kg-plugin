import { describe, expect, it, vi } from "vitest";
import type { ConsoleApi } from "./api";
import type {
  OntologyEntityDefinition,
  OntologyProperty,
  OntologyRelationDefinition,
  OntologySchema,
} from "./ontologyTypes";
import {
  filterOntologyProperties,
  loadOntologyInstances,
  ontologyDefinitionLabel,
  ontologyGraph,
  searchOntology,
} from "./ontologyV3";

const property = (
  overrides: Partial<OntologyProperty> = {},
): OntologyProperty => ({
  key: "custom_title",
  label: "标题",
  description: "对外显示的名称",
  iri: "urn:custom:property:title",
  aliases: ["displayTitle"],
  domain: ["person"],
  range: ["string"],
  required_for: [],
  managed: false,
  runtime_supported: true,
  ...overrides,
});
const entity = (
  key: string,
  overrides: Partial<OntologyEntityDefinition> = {},
): OntologyEntityDefinition => ({
  key,
  label: key,
  description: `Definition of ${key}`,
  iri: `urn:custom:type:${key}`,
  storage_label: key,
  runtime_supported: true,
  count: 0,
  properties: [],
  ...overrides,
});
const relation = (
  overrides: Partial<OntologyRelationDefinition> = {},
): OntologyRelationDefinition => ({
  key: "HERMES_locatedAt",
  label: "locatedAt",
  description: "Connect a person or event to a location",
  iri: "urn:custom:relation:locatedAt",
  storage_type: "HERMES_locatedAt",
  aliases: ["locatedAt"],
  domain: ["person", "event"],
  range: ["location"],
  declared_domain: ["any"],
  declared_range: ["any"],
  count: null,
  properties: [],
  runtime_supported: true,
  ...overrides,
});
const schema = (overrides: Partial<OntologySchema> = {}): OntologySchema => ({
  namespace: "synthetic",
  scope: "current_namespace_active",
  read_only: true,
  stats_available: true,
  stats_error: null,
  ontology: {
    iri: "urn:custom",
    label: "Example",
    description: "Synthetic test ontology",
    version: "1",
  },
  entity_types: [
    "person",
    "event",
    "preference",
    "decision",
    "location",
    "concept",
  ].map((key) => entity(key)),
  relations: [relation()],
  properties: [],
  stats: { entities: 0, relationships: 0, unmapped_relationships: 0 },
  schema_warnings: [],
  ...overrides,
});

describe("ontology v3 projection", () => {
  it("projects entity definitions directly with stable selection identities", () => {
    const data = schema();
    const graph = ontologyGraph(data);
    expect(graph.nodes.map((node) => node.id)).toEqual(
      data.entity_types.map((item) => `schema:${item.key}`),
    );
    expect(graph.nodes.every((node) => node.layer === "schema")).toBe(true);
    expect(graph.nodes.some((node) => node.type === "relation")).toBe(false);
    expect(graph.definitions.get("schema:person")).toBe(data.entity_types[0]);
  });

  it("uses effective directed endpoints while preserving every relation definition", () => {
    const data = schema();
    const graph = ontologyGraph(data);
    expect(graph.edges).toMatchObject([
      {
        id: "schema-relation:HERMES_locatedAt:person:location",
        source: "schema:person",
        target: "schema:location",
        kind: "definition",
        label: "位于",
        relationType: "HERMES_locatedAt",
      },
      {
        id: "schema-relation:HERMES_locatedAt:event:location",
        source: "schema:event",
        target: "schema:location",
        kind: "definition",
      },
    ]);
    for (const edge of graph.edges)
      expect(graph.definitions.get(edge.id)).toBe(data.relations[0]);
    expect(data.relations[0].declared_domain).toEqual(["any"]);
  });

  it("represents universal rules as a logical any self-loop rather than 36 facts", () => {
    const graph = ontologyGraph(
      schema({
        relations: [
          relation({ key: "RELATED_TO", domain: ["any"], range: ["any"] }),
        ],
      }),
    );
    expect(graph.nodes).toHaveLength(7);
    expect(graph.edges).toHaveLength(1);
    expect(graph.edges[0]).toMatchObject({
      id: "schema-relation:RELATED_TO:any:any",
      source: "schema:any",
      target: "schema:any",
      kind: "definition",
    });
    expect(
      graph.nodes.find((node) => node.id === "schema:any")?.description,
    ).toContain("不是额外的实体类型");
  });

  it("preserves a constrained end of a partially universal rule and deduplicates scopes", () => {
    const graph = ontologyGraph(
      schema({
        relations: [
          relation({
            domain: ["any", "person"],
            range: ["location", "location"],
          }),
        ],
      }),
    );
    expect(graph.edges).toHaveLength(1);
    expect(graph.edges[0]).toMatchObject({
      source: "schema:any",
      target: "schema:location",
    });
  });

  it("keeps undefined references and missing declarations distinct from any", () => {
    const graph = ontologyGraph(
      schema({ relations: [relation({ domain: ["device"], range: [] })] }),
    );
    expect(graph.edges[0]).toMatchObject({
      source: "schema:device",
      target: "schema:undeclared",
    });
    expect(
      graph.nodes.find((node) => node.id === "schema:undeclared")?.description,
    ).toContain("不能据此推断为任意类型");
    expect(graph.nodes.some((node) => node.id === "schema:any")).toBe(false);
  });

  it("retains parallel relationships as independent selectable rules", () => {
    const graph = ontologyGraph(
      schema({ relations: [relation({ key: "a" }), relation({ key: "b" })] }),
    );
    expect(new Set(graph.edges.map((edge) => edge.id)).size).toBe(4);
    expect(graph.edges.every((edge) => graph.definitions.has(edge.id))).toBe(
      true,
    );
  });
});

describe("ontology definition search", () => {
  const data = schema({
    entity_types: [entity("person", { properties: [property()] })],
    relations: [
      relation({
        properties: [
          property({
            key: "confidence",
            label: "Confidence",
            aliases: ["certainty"],
          }),
        ],
      }),
    ],
  });

  it("finds owner definitions by property names, aliases and IRIs", () => {
    expect(searchOntology(data, "displayTitle")).toEqual(["schema:person"]);
    expect(searchOntology(data, "urn:custom:property:title")).toContain(
      "schema:person",
    );
    expect(searchOntology(data, "certainty")).toEqual([
      "schema-relation:HERMES_locatedAt:person:location",
      "schema-relation:HERMES_locatedAt:event:location",
    ]);
  });

  it("matches readable names, compatibility names, technical IRIs and normalized text", () => {
    expect(searchOntology(data, "位于")).toHaveLength(2);
    expect(searchOntology(data, "LOCATEDAT")).toHaveLength(2);
    expect(searchOntology(data, "urn:custom:type:person")).toEqual([
      "schema:person",
    ]);
    expect(searchOntology(data, "ＤＩＳＰＬＡＹＴＩＴＬＥ")).toEqual([
      "schema:person",
    ]);
  });

  it("exposes logical scope nodes and all identities for an empty search", () => {
    const universal = schema({
      relations: [relation({ domain: ["any"], range: ["any"] })],
    });
    expect(searchOntology(universal, "任意业务类型")).toContain("schema:any");
    const graph = ontologyGraph(universal);
    expect(searchOntology(universal, " ")).toHaveLength(
      graph.nodes.length + graph.edges.length,
    );
  });

  it("preserves custom definition labels", () => {
    expect(ontologyDefinitionLabel(entity("device", { label: "设备" }))).toBe(
      "设备",
    );
  });
});

describe("ontology property filters", () => {
  const definition = entity("person", {
    properties: [
      property({
        key: "required",
        required_for: ["person"],
        required_condition: "when active",
      }),
      property({ key: "optional", required_for: ["event"] }),
      property({ key: "managed", managed: true }),
    ],
  });
  it("filters requirements relative to the selected definition and separates managed values", () => {
    expect(
      filterOntologyProperties(definition, "", "required").map(
        (item) => item.key,
      ),
    ).toEqual(["required"]);
    expect(
      filterOntologyProperties(definition, "", "optional").map(
        (item) => item.key,
      ),
    ).toEqual(["optional"]);
    expect(
      filterOntologyProperties(definition, "", "managed").map(
        (item) => item.key,
      ),
    ).toEqual(["managed"]);
    expect(filterOntologyProperties(definition, "managed", "required")).toEqual(
      [],
    );
  });
});

describe("ontology instance reads", () => {
  it("keeps disabled custom definitions and their labels as searchable history", () => {
    const custom = entity("project", {
      label: "项目",
      custom: true,
      active: false,
    });
    const rule = relation({
      key: "custom_owns",
      label: "负责",
      custom: true,
      active: false,
      domain: ["person"],
      range: ["project"],
    });
    const data = schema({
      entity_types: [entity("person"), custom],
      relations: [rule],
    });
    const graph = ontologyGraph(data);
    expect(
      graph.nodes.find((node) => node.id === "schema:project"),
    ).toMatchObject({
      label: "项目",
      type: "project",
      disabled: true,
      custom: true,
    });
    expect(graph.edges[0]).toMatchObject({
      label: "负责",
      disabled: true,
      target: "schema:project",
    });
    expect(graph.edges[0].description).toContain("项目");
    expect(searchOntology(data, "项目")).toContain("schema:project");
    expect(graph.definitions.get("schema:project")).toBe(custom);
  });

  it("sends a disabled custom definition's original key when reading historical instances", async () => {
    const entities = vi.fn().mockResolvedValue({ items: [] });
    const relations = vi.fn();
    await loadOntologyInstances(
      {
        ontologyEntities: entities,
        ontologyRelations: relations,
      } as unknown as ConsoleApi,
      entity("project", { active: false, custom: true }),
      0,
      "",
      undefined,
    );
    expect(entities).toHaveBeenCalledWith(
      { type: "project", q: "", offset: 0, limit: 20 },
      undefined,
    );
    expect(relations).not.toHaveBeenCalled();
  });

  it("sends type, query and pagination to the server using the abort signal", async () => {
    const entities = vi.fn().mockResolvedValue({ items: [] });
    const relations = vi.fn().mockResolvedValue({ items: [] });
    const api = {
      ontologyEntities: entities,
      ontologyRelations: relations,
    } as unknown as ConsoleApi;
    const signal = new AbortController().signal;
    await loadOntologyInstances(api, entity("person"), 40, "  Ada  ", signal);
    expect(entities).toHaveBeenCalledWith(
      { type: "person", q: "Ada", offset: 40, limit: 20 },
      signal,
    );
    expect(relations).not.toHaveBeenCalled();
    await loadOntologyInstances(api, relation(), 20, "office", signal);
    expect(relations).toHaveBeenCalledWith(
      { relation: "HERMES_locatedAt", q: "office", offset: 20, limit: 20 },
      signal,
    );
  });
});
