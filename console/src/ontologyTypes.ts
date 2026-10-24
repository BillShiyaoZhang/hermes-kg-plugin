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
  customized_fields?: string[];
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
  customized_fields?: string[];
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
  customized_fields?: string[];
}

export interface OntologySchema {
  namespace: string;
  scope: "current_namespace_active";
  read_only: boolean;
  /** Absent on older viewer-only services. Editing requires an explicit version. */
  version?: number;
  editing?: OntologyEditingCapabilities;
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

export interface OntologyEditingCapabilities {
  scope: "namespace_overlay";
  can_edit: boolean;
  can_add_types: boolean;
  can_change_storage_ids: boolean;
  structural_policy: "tighten_only";
  entity_fields: string[];
  property_fields: string[];
  relation_fields: string[];
  datatypes_by_property: Record<string, string[]>;
  immutable_keys: string[];
  readonly_properties: string[];
  builtin_constraints?: {
    properties: Record<string, OntologyStructuralConstraints>;
    relations: Record<string, OntologyStructuralConstraints>;
  };
}

export interface OntologyStructuralConstraints {
  domain: string[];
  range: string[];
  aliases: string[];
  required_for?: string[];
}

export interface OntologyMetadataPatch {
  label?: string;
  description?: string;
}

export interface OntologyPropertyPatch extends OntologyMetadataPatch {
  aliases?: string[];
  required_for?: string[];
  domain?: string[];
  range?: string[];
}

export interface OntologyRelationPatch extends OntologyMetadataPatch {
  aliases?: string[];
  domain?: string[];
  range?: string[];
}

export interface OntologyPatch {
  entity_types?: Record<string, OntologyMetadataPatch>;
  properties?: Record<string, OntologyPropertyPatch>;
  relations?: Record<string, OntologyRelationPatch>;
}

export interface OntologyEditRequest {
  expected_namespace: string;
  expected_version: number;
  patch: OntologyPatch;
}

export interface OntologyPublishRequest extends OntologyEditRequest {
  operation_id: string;
  reason?: string;
}

export interface OntologyValidationResult {
  namespace: string;
  version: number;
  expected_version: number;
  can_publish: boolean;
  structural: boolean;
  changes: { kind: string; key: string; fields: string[] }[];
  impact: {
    checked_nodes: number | null;
    checked_relations: number | null;
    affected_nodes?: number | null;
    affected_relations?: number | null;
    violations: { kind: string; key: string; code: string; count: number }[];
  };
  schema: OntologySchema;
}

export interface OntologyPublishResult {
  ok: true;
  namespace: string;
  version: number;
  operation_id: string;
  idempotent: boolean;
  schema: OntologySchema;
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
