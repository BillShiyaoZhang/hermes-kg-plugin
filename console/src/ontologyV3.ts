import type { ConsoleApi } from "./api";
import type { GraphEdge, GraphNode } from "./graphModel";
import { typeLabels } from "./lib";
import { relationLabel } from "./ontologyHelpers";
import {
  ontologyDefinitionDescription,
  ontologyPropertyDescription,
  ontologyPropertyTitle,
} from "./ontologyPresentation";
import type {
  OntologyEntityDefinition,
  OntologyEntityPage,
  OntologyProperty,
  OntologyRelationDefinition,
  OntologyRelationPage,
  OntologySchema,
} from "./ontologyTypes";

export type OntologyDefinition =
  OntologyEntityDefinition | OntologyRelationDefinition;
export type OntologyPropertyFilter =
  "all" | "required" | "optional" | "managed";
export const ONTOLOGY_INSTANCE_PAGE_SIZE = 20;

export const isOntologyRelation = (
  definition: OntologyDefinition,
): definition is OntologyRelationDefinition => "domain" in definition;

export function ontologyTypeLabel(
  type: string,
  schema?: Pick<OntologySchema, "entity_types">,
): string {
  if (type === "any") return "任意业务类型";
  if (type === "undeclared") return "未声明类型范围";
  const definition = schema?.entity_types.find((item) => item.key === type);
  return definition
    ? ontologyDefinitionLabel(definition)
    : typeLabels[type] || type;
}

export function ontologyDefinitionLabel(
  definition: OntologyDefinition,
): string {
  if (definition.custom || definition.customized_fields?.includes("label"))
    return definition.label;
  return isOntologyRelation(definition)
    ? relationLabel(definition)
    : typeLabels[definition.key as keyof typeof typeLabels] ||
        definition.label ||
        definition.key;
}

/** Each edge expresses one allowed endpoint pair, never a saved fact. */
export function ontologyGraph(schema: OntologySchema): {
  nodes: GraphNode[];
  edges: GraphEdge[];
  definitions: Map<string, OntologyDefinition>;
} {
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];
  const definitions = new Map<string, OntologyDefinition>();
  const nodeIds = new Set<string>();
  for (const definition of schema.entity_types) {
    const id = `schema:${definition.key}`;
    nodeIds.add(id);
    nodes.push({
      id,
      label: ontologyDefinitionLabel(definition),
      type: definition.key,
      layer: "schema",
      description: ontologyDefinitionDescription(definition),
      disabled: definition.active === false,
      custom: definition.custom,
    });
    definitions.set(id, definition);
  }
  const endpoint = (type: string) => {
    const id = `schema:${type}`;
    if (!nodeIds.has(id)) {
      nodeIds.add(id);
      nodes.push({
        id,
        label: ontologyTypeLabel(type, schema),
        type,
        layer: "schema",
        description:
          type === "any"
            ? "逻辑范围：适用于任意业务类型，不是额外的实体类型。"
            : type === "undeclared"
              ? "此关系没有声明这一端的类型范围，不能据此推断为任意类型。"
              : "关系引用了这一类型；当前本体未提供它的完整定义。",
      });
    }
    return id;
  };
  // A universal endpoint subsumes individual entries without materializing
  // every business-type pair. Empty endpoints remain explicitly unknown.
  const scopes = (types: string[]) =>
    types.includes("any")
      ? ["any"]
      : types.length
        ? [...new Set(types)]
        : ["undeclared"];
  for (const definition of schema.relations) {
    for (const source of scopes(definition.domain)) {
      for (const target of scopes(definition.range)) {
        const id = `schema-relation:${definition.key}:${source}:${target}`;
        edges.push({
          id,
          source: endpoint(source),
          target: endpoint(target),
          label: relationLabel(definition),
          kind: "definition",
          relationType: definition.key,
          disabled: definition.active === false,
          custom: definition.custom,
          description: `${ontologyTypeLabel(source, schema)} → ${ontologyTypeLabel(target, schema)} · ${definition.active === false ? "已停用的类型规则，保留用于核对历史记录。" : "允许的类型规则，不代表已保存事实。"}${ontologyDefinitionDescription(definition)}`,
        });
        definitions.set(id, definition);
      }
    }
  }
  return { nodes, edges, definitions };
}

const includesQuery = (values: unknown[], query: string) =>
  values.join(" ").normalize("NFKC").toLocaleLowerCase().includes(query);

export function ontologyPropertyMatches(
  property: OntologyProperty,
  query: string,
): boolean {
  const normalized = query.trim().normalize("NFKC").toLocaleLowerCase();
  return (
    !normalized ||
    includesQuery(
      [
        property.key,
        property.label,
        property.description,
        property.iri,
        ...Object.values(property.semantic || {}),
        ...property.aliases,
        ...property.domain,
        ...property.range,
        ...(property.allowed_values || []),
        ontologyPropertyTitle(property),
        ontologyPropertyDescription(property),
      ],
      normalized,
    )
  );
}

export function filterOntologyProperties(
  definition: OntologyDefinition,
  query: string,
  filter: OntologyPropertyFilter,
): OntologyProperty[] {
  return definition.properties.filter((property) => {
    const required = property.required_for.includes(definition.key);
    const matchesFilter =
      filter === "all" ||
      (filter === "required" && required) ||
      (filter === "optional" && !required && !property.managed) ||
      (filter === "managed" && property.managed);
    return matchesFilter && ontologyPropertyMatches(property, query);
  });
}

/** Search definitions and their properties, including aliases and RDF IRIs. */
export function searchOntology(
  schema: OntologySchema,
  query: string,
): string[] {
  const normalized = query.trim().normalize("NFKC").toLocaleLowerCase();
  const graph = ontologyGraph(schema);
  if (!normalized)
    return [...graph.nodes, ...graph.edges].map((item) => item.id);
  const results: string[] = [];
  for (const [id, definition] of graph.definitions) {
    const details = isOntologyRelation(definition)
      ? [
          definition.storage_type,
          ...definition.aliases,
          ...definition.domain,
          ...definition.range,
          ...definition.declared_domain,
          ...definition.declared_range,
        ]
      : [definition.storage_label];
    if (
      includesQuery(
        [
          definition.key,
          definition.label,
          definition.description,
          definition.iri,
          ...Object.values(definition.semantic || {}),
          ontologyDefinitionLabel(definition),
          ontologyDefinitionDescription(definition),
          ...details,
        ],
        normalized,
      ) ||
      definition.properties.some((property) =>
        ontologyPropertyMatches(property, normalized),
      )
    )
      results.push(id);
  }
  // Logical scope nodes also remain searchable by their displayed explanation.
  for (const node of graph.nodes) {
    if (
      !graph.definitions.has(node.id) &&
      includesQuery([node.label, node.description, node.type], normalized)
    )
      results.push(node.id);
  }
  return results;
}

export function loadOntologyInstances(
  api: Pick<ConsoleApi, "ontologyEntities" | "ontologyRelations">,
  definition: OntologyDefinition,
  offset: number,
  query: string,
  signal?: AbortSignal,
): Promise<OntologyEntityPage | OntologyRelationPage> {
  const parameters = {
    q: query.trim(),
    offset,
    limit: ONTOLOGY_INSTANCE_PAGE_SIZE,
  };
  return isOntologyRelation(definition)
    ? api.ontologyRelations({ ...parameters, relation: definition.key }, signal)
    : api.ontologyEntities({ ...parameters, type: definition.key }, signal);
}
