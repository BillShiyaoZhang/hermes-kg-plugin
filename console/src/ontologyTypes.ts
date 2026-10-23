import type { EntityType, JsonRecord, Version } from "./types";

export type OntologyEntityKey = Exclude<EntityType, "unknown">;
/** "any" is a logical scope; other strings can describe future ontology types. */
export type OntologyTypeReference = string;

export interface OntologyProperty {
  key: string;
  label: string;
  description: string;
  iri: string;
  domain: OntologyTypeReference[];
  range: string[];
  aliases: string[];
  required_for: string[];
  required_condition?: string;
  allowed_values?: string[];
  value_shape?: "scalar" | "array";
  managed: boolean;
  runtime_supported: boolean;
}

export interface OntologyEntityDefinition {
  key: string;
  label: string;
  description: string;
  iri: string;
  storage_label: string;
  runtime_supported: boolean;
  count: number | null;
  properties: OntologyProperty[];
}

export interface OntologyRelationDefinition {
  key: string;
  label: string;
  description: string;
  iri: string;
  storage_type: string;
  aliases: string[];
  domain: OntologyTypeReference[];
  range: OntologyTypeReference[];
  declared_domain: OntologyTypeReference[];
  declared_range: OntologyTypeReference[];
  count: number | null;
  properties: OntologyProperty[];
  runtime_supported: boolean;
}

export interface OntologySchema {
  namespace: string;
  scope: "current_namespace_active";
  read_only: true;
  stats_available: boolean;
  stats_error: { error_type: string } | null;
  ontology: {
    iri: string;
    label: string;
    description: string;
    version: string;
  };
  entity_types: OntologyEntityDefinition[];
  relations: OntologyRelationDefinition[];
  properties: OntologyProperty[];
  stats: {
    entities: number | null;
    relationships: number | null;
    unmapped_relationships: number | null;
  };
  schema_warnings: { code: string; message?: string; [key: string]: unknown }[];
}

export interface OntologyEntityRecord {
  uri: string;
  type: OntologyEntityKey;
  name: string;
  description: string;
  version?: Version;
  properties: JsonRecord;
  updated_at?: string;
}

export interface OntologyEndpoint {
  uri: string;
  type: OntologyEntityKey;
  name: string;
}

export interface OntologyRelationRecord {
  relation_type: string;
  stored_type: string;
  source: OntologyEndpoint;
  target: OntologyEndpoint;
  properties: JsonRecord;
}

export interface OntologyPage<T> {
  namespace: string;
  scope: "current_namespace_active";
  items: T[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
}

export type OntologyEntityPage = OntologyPage<OntologyEntityRecord>;
export type OntologyRelationPage = OntologyPage<OntologyRelationRecord>;
