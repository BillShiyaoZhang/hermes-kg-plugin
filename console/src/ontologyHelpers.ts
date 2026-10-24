import { typeLabels } from "./lib";
import type {
  OntologyEntityKey,
  OntologyRelationDefinition,
  OntologyTypeReference,
} from "./ontologyTypes";

const relationLabels: Record<string, string> = {
  HERMES_knows: "认识",
  HERMES_participant: "参与",
  HERMES_hasPreference: "拥有偏好",
  HERMES_hasDecision: "做出决策",
  HERMES_locatedAt: "位于",
  LINKED_TO: "关联",
  CAUSED_BY: "由…引起",
  RELATED_TO: "相关",
  owl_sameAs: "同一实体",
};

export function knownCount(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

export function displayCount(value: number | null | undefined): string {
  return knownCount(value) ? value.toLocaleString("zh-CN") : "—";
}

export function relationLabel(
  relation: Pick<
    OntologyRelationDefinition,
    "key" | "label" | "customized_fields"
  >,
): string {
  if (relation.customized_fields?.includes("label")) return relation.label;
  return relationLabels[relation.key] || relation.label || relation.key;
}

export function describeTypes(types: OntologyTypeReference[]): string {
  if (types.includes("any")) return "任意业务类型";
  return (
    types
      .map((key) => typeLabels[key as OntologyEntityKey] || key)
      .join("、") || "未声明"
  );
}

/** Effective runtime endpoints take priority over broader historical RDF declarations. */
export function relationTouchesType(
  relation: Pick<OntologyRelationDefinition, "domain" | "range">,
  type: string,
): boolean {
  return [...relation.domain, ...relation.range].some(
    (key) => key === "any" || key === type,
  );
}

export function pageRange(
  offset: number,
  itemCount: number,
  total: number,
): string {
  if (!total) return "共 0 条";
  if (!itemCount) return `当前页没有记录 · 共 ${displayCount(total)} 条`;
  return `${displayCount(offset + 1)}–${displayCount(offset + itemCount)} / ${displayCount(total)} 条`;
}
