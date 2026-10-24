import type {
  OntologyCollection,
  OntologyCreateDefinition,
  OntologyEditRequest,
  OntologyLifecycleAction,
  OntologyRisk,
  OntologySchema,
  OntologyValidationResult,
} from "./ontologyTypes";
import {
  ontologyAliasList,
  ontologyEditResource,
  ontologyValidationMatches,
} from "./ontologyEditorHelpers";

export const ONTOLOGY_KIND_NAMES: Record<OntologyCollection, string> = {
  entity_types: "类型",
  properties: "属性",
  relations: "关系",
};
export const ONTOLOGY_ACTION_NAMES: Record<OntologyLifecycleAction, string> = {
  create: "新建",
  deactivate: "停用",
  restore: "恢复",
  delete: "删除",
};
export interface OntologyMutationTarget {
  kind: OntologyCollection;
  action: OntologyLifecycleAction;
  key: string;
}
export interface OntologyCreateDraft {
  key: string;
  label: string;
  description: string;
  aliases: string;
  domain: string[];
  range: string[];
  required_for: string[];
  value_shape: "scalar" | "array";
}
export const newOntologyDraft = (
  kind: OntologyCollection,
): OntologyCreateDraft => ({
  key: kind === "relations" ? "X_" : "x_",
  label: "",
  description: "",
  aliases: "",
  domain: ["any"],
  range: kind === "properties" ? ["string"] : ["any"],
  required_for: [],
  value_shape: "scalar",
});
export function canCreateOntology(
  schema: OntologySchema,
  kind: OntologyCollection,
) {
  const caps = schema.editing;
  return (
    !schema.read_only &&
    caps?.can_edit &&
    Number.isInteger(schema.version) &&
    (kind === "entity_types"
      ? caps.can_add_types
      : kind === "properties"
        ? caps.can_add_properties
        : caps.can_add_relations) === true
  );
}
export function canMutateOntology(
  schema: OntologySchema,
  target: OntologyMutationTarget,
) {
  if (target.action === "create") return canCreateOntology(schema, target.kind);
  const resource = ontologyEditResource(schema, target);
  return (
    !schema.read_only &&
    schema.editing?.can_manage_lifecycle === true &&
    !!resource?.allowed_actions?.includes(target.action)
  );
}
export function ontologyMutationRequest(
  schema: OntologySchema,
  target: OntologyMutationTarget,
  draft: OntologyCreateDraft,
): OntologyEditRequest {
  let definition: OntologyCreateDefinition | undefined;
  if (target.action === "create") {
    definition = { label: draft.label.trim(), description: draft.description };
    if (target.kind !== "entity_types")
      Object.assign(definition, {
        aliases: ontologyAliasList(draft.aliases),
        domain: [...new Set(draft.domain)],
        range: [...new Set(draft.range)],
        ...(target.kind === "properties"
          ? {
              required_for: [...new Set(draft.required_for)],
              value_shape: draft.value_shape,
            }
          : {}),
      });
  }
  return {
    expected_namespace: schema.namespace,
    expected_version: schema.version ?? -1,
    patch: {},
    operations: [
      {
        action: target.action,
        kind: target.kind,
        key: target.action === "create" ? draft.key.trim() : target.key,
        ...(definition ? { definition } : {}),
      },
    ],
  };
}
export function ontologyMutationIssues(
  schema: OntologySchema,
  target: OntologyMutationTarget,
  draft: OntologyCreateDraft,
): string[] {
  if (!canMutateOntology(schema, target))
    return ["当前服务未开放此操作，或定义状态已变化。"];
  if (target.action !== "create") return [];
  const issues: string[] = [];
  const pattern =
    schema.editing?.custom_key_patterns?.[target.kind] ??
    (target.kind === "relations"
      ? "X_[A-Z][A-Z0-9_]{0,47}"
      : "x_[a-z][a-z0-9_]{0,47}");
  if (!new RegExp(`^(?:${pattern})$`).test(draft.key.trim()))
    issues.push(
      target.kind === "relations"
        ? "关系标识须以 X_ 开头，后接大写英文字母、数字或下划线，主体最多 48 位。"
        : "标识须以 x_ 开头，后接小写英文字母、数字或下划线，主体最多 48 位。",
    );
  if (schema[target.kind].some((item) => item.key === draft.key.trim()))
    issues.push("此标识已经存在，停用定义也占用原标识。");
  if (!draft.label.trim() || draft.label.trim().length > 120)
    issues.push("显示名称必填，最多 120 个字符。");
  if (draft.description.length > 4000) issues.push("说明最多 4000 个字符。");
  if (target.kind === "entity_types") return issues;
  const aliases = ontologyAliasList(draft.aliases);
  if (
    aliases.length > 24 ||
    aliases.some((alias) => !/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(alias))
  )
    issues.push(
      "兼容名称最多 24 项，每项为英文字母开头的字母、数字或下划线，最多 64 位。",
    );
  const activeTypes = schema.entity_types
    .filter((item) => item.active !== false)
    .map((item) => item.key);
  for (const field of target.kind === "relations"
    ? (["domain", "range"] as const)
    : (["domain"] as const)) {
    const values = draft[field];
    if (
      !values.length ||
      values.some((value) => value !== "any" && !activeTypes.includes(value)) ||
      (values.includes("any") && values.length > 1)
    )
      issues.push(
        "起点、终点或适用类型须选择已启用类型；任意类型不能与具体类型混选。",
      );
  }
  if (target.kind === "properties") {
    if (
      draft.range.length !== 1 ||
      !schema.editing?.create_datatypes?.includes(draft.range[0])
    )
      issues.push("请选择服务支持的属性值类型。");
    if (
      draft.required_for.some(
        (type) =>
          !activeTypes.includes(type) ||
          (!draft.domain.includes("any") && !draft.domain.includes(type)),
      )
    )
      issues.push("必填类型必须属于属性的适用范围。");
    if (
      !(schema.editing?.create_value_shapes ?? ["scalar"]).includes(
        draft.value_shape,
      )
    )
      issues.push("当前服务不支持这种值形式。");
  }
  return issues;
}

export function ontologyRiskReady(risk?: OntologyRisk): boolean {
  if (
    !risk ||
    !["low", "medium", "high"].includes(risk.level) ||
    typeof risk.requires_confirmation !== "boolean" ||
    !Array.isArray(risk.reasons) ||
    !risk.reasons.every((reason) => typeof reason === "string")
  )
    return false;
  return (
    !(risk.level === "high" || risk.requires_confirmation) ||
    (risk.requires_confirmation === true &&
      typeof risk.confirmation_phrase === "string" &&
      !!risk.confirmation_phrase.trim() &&
      typeof risk.confirmation_token === "string" &&
      !!risk.confirmation_token.trim())
  );
}
export function ontologyConfirmationMatches(
  risk: OntologyRisk | undefined,
  confirmation: string,
): boolean {
  return (
    ontologyRiskReady(risk) &&
    (!risk?.requires_confirmation ||
      confirmation.trim() === risk.confirmation_phrase)
  );
}
export function ontologyMutationPreviewMatches(
  result: OntologyValidationResult,
  request: OntologyEditRequest,
): boolean {
  const destructive = request.operations?.some(
    (item) => item.action === "delete" || item.action === "deactivate",
  );
  const referenceFields = [
    "active_nodes",
    "archived_nodes",
    "active_relations",
    "archived_relations",
    "pending_reviews",
    "history_revisions",
    "definition_references",
    "reference_count",
  ] as const;
  const referencesKnown =
    !!result.impact.definitions?.length &&
    result.impact.definitions.every((item) =>
      referenceFields.every(
        (field) =>
          typeof item[field] === "number" &&
          Number.isFinite(item[field]) &&
          (item[field] ?? -1) >= 0,
      ),
    );
  return (
    ontologyValidationMatches(result, request) &&
    ontologyRiskReady(result.risk) &&
    (!destructive ||
      (result.risk?.level === "high" &&
        (!result.can_publish || referencesKnown)))
  );
}
