import type {
  OntologyEditRequest,
  OntologyEntityDefinition,
  OntologyProperty,
  OntologyPublishRequest,
  OntologyPublishResult,
  OntologyRelationDefinition,
  OntologySchema,
  OntologyStructuralConstraints,
  OntologyValidationResult,
} from "./ontologyTypes";

export type OntologyEditKind = "entity_types" | "relations" | "properties";
export interface OntologyEditTarget {
  kind: OntologyEditKind;
  key: string;
}
export type OntologyEditResource =
  OntologyEntityDefinition | OntologyRelationDefinition | OntologyProperty;
export type OntologyEditField =
  "label" | "description" | "aliases" | "domain" | "range" | "required_for";
export interface OntologyEditorDraft {
  label: string;
  description: string;
  aliases: string;
  domain: string[];
  range: string[];
  required_for: string[];
}
export interface OntologyEditorTransport {
  validate: (
    request: OntologyEditRequest,
    signal?: AbortSignal,
  ) => Promise<OntologyValidationResult>;
  publish: (request: OntologyPublishRequest) => Promise<OntologyPublishResult>;
  read?: (signal?: AbortSignal) => Promise<OntologySchema>;
}

export const ONTOLOGY_EDIT_FIELDS: OntologyEditField[] = [
  "label",
  "description",
  "aliases",
  "domain",
  "range",
  "required_for",
];
export const ONTOLOGY_FIELD_LABELS: Record<OntologyEditField, string> = {
  label: "显示名称",
  description: "说明",
  aliases: "兼容名称",
  domain: "适用起点 / 类型",
  range: "终点 / 值类型",
  required_for: "必填类型",
};
export function ontologyEditResource(
  schema: OntologySchema,
  target: OntologyEditTarget,
): OntologyEditResource | undefined {
  if (target.kind === "entity_types")
    return schema.entity_types.find((item) => item.key === target.key);
  if (target.kind === "relations")
    return schema.relations.find((item) => item.key === target.key);
  return (
    schema.properties.find((item) => item.key === target.key) ??
    [...schema.entity_types, ...schema.relations]
      .flatMap((item) => item.properties)
      .find((item) => item.key === target.key)
  );
}

export function ontologyEditableFields(
  schema: OntologySchema,
  target: OntologyEditTarget,
): OntologyEditField[] {
  const caps = schema.editing;
  const resource = ontologyEditResource(schema, target);
  if (
    !caps?.can_edit ||
    caps.scope !== "namespace_overlay" ||
    schema.read_only ||
    !Number.isInteger(schema.version) ||
    (schema.version ?? -1) < 0 ||
    !resource?.runtime_supported ||
    (resource.allowed_actions !== undefined &&
      !resource.allowed_actions.includes("update"))
  )
    return [];
  if (
    target.kind === "properties" &&
    ((resource as OntologyProperty).managed ||
      caps.readonly_properties?.includes(target.key))
  )
    return [];
  const fields =
    target.kind === "entity_types"
      ? caps.entity_fields
      : target.kind === "relations"
        ? caps.relation_fields
        : caps.property_fields;
  return ONTOLOGY_EDIT_FIELDS.filter((field) => fields?.includes(field));
}

export function ontologyDraftFrom(
  resource: OntologyEditResource,
): OntologyEditorDraft {
  const scoped = resource as Partial<OntologyProperty>;
  return {
    label: resource.label,
    description: resource.description,
    aliases: scoped.aliases?.join("\n") ?? "",
    domain: [...(scoped.domain ?? [])],
    range: [...(scoped.range ?? [])],
    required_for: [...(scoped.required_for ?? [])],
  };
}

/** The built-in floor permits reverting custom restrictions without weakening runtime rules. */
export function ontologyEditConstraints(
  schema: OntologySchema,
  target: OntologyEditTarget,
): OntologyStructuralConstraints | undefined {
  if (target.kind === "entity_types") return undefined;
  if (ontologyEditResource(schema, target)?.custom)
    return {
      domain: ["any"],
      range: target.kind === "relations" ? ["any"] : [],
      aliases: [],
      required_for: [],
    };
  const builtin =
    schema.editing?.builtin_constraints?.[target.kind]?.[target.key];
  if (builtin) return builtin;
  const resource = ontologyEditResource(schema, target) as
    OntologyProperty | OntologyRelationDefinition | undefined;
  return resource
    ? {
        domain: resource.domain,
        range: resource.range,
        aliases: resource.aliases,
        required_for: "required_for" in resource ? resource.required_for : [],
      }
    : undefined;
}
export function ontologyAliasList(value: string): string[] {
  return [
    ...new Set(
      value
        .split(/[\n,，;；]+/)
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  ];
}
const arrayEqual = (first: string[], second: string[]) =>
  [...new Set(first)].sort().join("\u0000") ===
  [...new Set(second)].sort().join("\u0000");
export function ontologyDraftChanges(
  schema: OntologySchema,
  target: OntologyEditTarget,
  draft: OntologyEditorDraft,
): Partial<Record<OntologyEditField, string | string[]>> {
  const resource = ontologyEditResource(schema, target);
  if (!resource) return {};
  const previous = ontologyDraftFrom(resource);
  const changes: Partial<Record<OntologyEditField, string | string[]>> = {};
  for (const field of ontologyEditableFields(schema, target)) {
    if (field === "label" || field === "description") {
      const next =
        field === "label"
          ? draft[field].trim()
          : draft[field].replace(/\r\n/g, "\n");
      if (next !== previous[field]) changes[field] = next;
    } else {
      const next =
        field === "aliases"
          ? ontologyAliasList(draft.aliases)
          : [...new Set(draft[field])];
      const before =
        field === "aliases"
          ? ontologyAliasList(previous.aliases)
          : previous[field];
      if (!arrayEqual(next, before)) changes[field] = next;
    }
  }
  return changes;
}

export function ontologyEditRequest(
  schema: OntologySchema,
  target: OntologyEditTarget,
  draft: OntologyEditorDraft,
): OntologyEditRequest {
  return {
    expected_namespace: schema.namespace,
    expected_version: schema.version ?? -1,
    patch: {
      [target.kind]: {
        [target.key]: ontologyDraftChanges(schema, target, draft),
      },
    },
  };
}

export function ontologyDraftIssues(
  schema: OntologySchema,
  target: OntologyEditTarget,
  draft: OntologyEditorDraft,
): string[] {
  const issues: string[] = [];
  const resource = ontologyEditResource(schema, target);
  const fields = ontologyEditableFields(schema, target);
  const constraints = ontologyEditConstraints(schema, target);
  if (!resource || !fields.length)
    return ["当前服务没有提供此定义的可编辑能力。"];
  if (fields.includes("label") && !draft.label.trim())
    issues.push("显示名称不能为空。");
  if (fields.includes("label") && draft.label.trim().length > 120)
    issues.push("显示名称最多 120 个字符。");
  if (fields.includes("description") && draft.description.length > 4000)
    issues.push("说明最多 4000 个字符。");
  if (fields.includes("aliases")) {
    const aliases = ontologyAliasList(draft.aliases);
    const initialAliases = (resource as OntologyProperty).aliases;
    if (constraints?.aliases.some((alias) => !aliases.includes(alias)))
      issues.push("本体内置兼容名称不能移除；自己添加的名称可以撤回。");
    if (aliases.length > 24) issues.push("兼容名称最多 24 项。");
    if (
      aliases.some(
        (alias) =>
          !initialAliases.includes(alias) &&
          !/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(alias),
      )
    )
      issues.push(
        "新增兼容名称须以英文字母开头，仅含字母、数字与下划线，最多 64 个字符。",
      );
  }
  for (const field of ["domain", "range"] as const)
    if (fields.includes(field)) {
      if (!draft[field].length)
        issues.push(
          `${field === "domain" ? "适用类型 / 起点" : "值类型 / 终点"}至少选择一项。`,
        );
      if (draft[field].includes("any") && draft[field].length > 1)
        issues.push("任意业务类型不能与具体类型混选。");
      if ((target.kind === "relations" || field === "domain") && constraints) {
        const floor = constraints[field];
        if (
          !floor.includes("any") &&
          draft[field].some((type) => !floor.includes(type))
        )
          issues.push("类型范围不能超出本体内置允许范围。");
      }
    }
  if (target.kind === "properties" && fields.includes("required_for")) {
    const allowed = draft.domain.includes("any")
      ? schema.entity_types.map((item) => item.key)
      : draft.domain;
    if (draft.required_for.some((type) => !allowed.includes(type)))
      issues.push("必填类型必须同时在属性适用范围内。");
    const previous =
      constraints?.required_for ?? (resource as OntologyProperty).required_for;
    if (previous.some((type) => !draft.required_for.includes(type)))
      issues.push("本体内置必填要求不能在此放宽。");
  }
  if (target.kind === "properties" && fields.includes("range")) {
    if (draft.range.length !== 1) issues.push("属性值类型须且只能选择一项。");
    const supported =
      schema.editing?.datatypes_by_property[target.key] ??
      (resource.custom ? schema.editing?.create_datatypes : undefined) ??
      (resource as OntologyProperty).range;
    if (draft.range.some((type) => !supported.includes(type)))
      issues.push("选择的值类型未被当前运行服务支持。");
  }
  return issues;
}

export function ontologyScopeOptions(
  schema: OntologySchema,
  values: string[],
): string[] {
  return values.includes("any")
    ? ["any", ...schema.entity_types.map((item) => item.key)]
    : [...new Set(values)];
}

/** Rebase only explicitly edited fields; concurrent edits to untouched fields survive. */
export function rebaseOntologyDraft(
  latest: OntologyEditResource,
  draft: OntologyEditorDraft,
  touched: OntologyEditField[],
): OntologyEditorDraft {
  const result = ontologyDraftFrom(latest);
  for (const field of touched) {
    if (field === "label" || field === "description" || field === "aliases")
      result[field] = draft[field];
    else result[field] = [...draft[field]];
  }
  return result;
}

export function ontologyValidationMatches(
  result: OntologyValidationResult,
  request: OntologyEditRequest,
): boolean {
  return (
    result.namespace === request.expected_namespace &&
    result.version === request.expected_version &&
    result.expected_version === request.expected_version &&
    result.schema.namespace === request.expected_namespace &&
    result.schema.version === request.expected_version
  );
}
