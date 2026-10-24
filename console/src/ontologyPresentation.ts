import type {
  OntologyEntityDefinition,
  OntologyProperty,
  OntologyRelationDefinition,
} from "./ontologyTypes";

type Copy = { title: string; description: string };
type Resource = {
  key?: string;
  label?: string;
  description?: string;
  iri?: string;
};

const properties: Record<string, Copy> = {
  hermes_name: {
    title: "名称",
    description: "记录的显示名称，便于搜索和识别。",
  },
  foaf_name: { title: "人物姓名", description: "人物的姓名或常用称呼。" },
  hermes_description: {
    title: "内容说明",
    description: "对这条记录主要信息的简要说明。",
  },
  hermes_note: { title: "备注", description: "需要保留的补充细节或说明。" },
  hermes_eventTime: {
    title: "事件时间",
    description: "事件发生或计划发生的日期与时间，按来源能够支持的精度记录。",
  },
  hermes_eventType: {
    title: "事件类型",
    description: "事件所属的类别，帮助区分不同的经历或安排。",
  },
  hermes_timePrecision: {
    title: "时间精度",
    description:
      "时间精确到日期、具体时刻，或仍然未知。只有日期时，不表示事件发生在午夜。",
  },
  hermes_timeYearInferred: {
    title: "年份是否补全",
    description:
      "年份是否根据来源消息补全。未标注这一项，不代表原文明确提供了年份。",
  },
  hermes_preferenceKey: {
    title: "偏好主题",
    description: "这项偏好针对什么，例如饮食、工作方式或沟通习惯。",
  },
  hermes_preferenceValue: {
    title: "偏好内容",
    description: "人物明确表达的喜好、倾向或习惯。",
  },
  hermes_decisionText: {
    title: "决策内容",
    description: "已经作出的选择或决定。",
  },
  hermes_decisionContext: {
    title: "决策背景",
    description: "作出这一选择时的背景、考虑或原因。",
  },
  geo_lat: {
    title: "纬度",
    description: "地点的纬度坐标；没有可靠坐标时可以不填写。",
  },
  geo_long: {
    title: "经度",
    description: "地点的经度坐标；没有可靠坐标时可以不填写。",
  },
  shared: {
    title: "允许跨空间读取",
    description: "是否允许其他空间读取这条记录。实际访问仍受服务权限控制。",
  },
  hermes_updatedAt: {
    title: "更新时间",
    description: "这条记录最近一次更新的时间。",
  },
  hermes_createdAt: {
    title: "创建时间",
    description: "这条记录首次创建的时间。",
  },
  hermes_sourceSession: {
    title: "来源会话",
    description: "这条记录引用的来源会话标识，供追溯原始内容。",
  },
  hermes_sourceSessions: {
    title: "来源会话列表",
    description: "与这条记录有关的来源会话标识列表，供追溯原始内容。",
  },
  hermes_extractionIds: {
    title: "抽取记录标识",
    description: "生成或补充这条记录的抽取批次标识，供追溯处理过程。",
  },
  hermes_relLabel: {
    title: "关系说明",
    description: "为关系提供便于理解的补充名称或说明。",
  },
};

const propertyAliases: Record<string, string> = {
  name: "hermes_name",
  personName: "foaf_name",
  description: "hermes_description",
  note: "hermes_note",
  eventTime: "hermes_eventTime",
  eventType: "hermes_eventType",
  timePrecision: "hermes_timePrecision",
  timeYearInferred: "hermes_timeYearInferred",
  preferenceKey: "hermes_preferenceKey",
  preferenceValue: "hermes_preferenceValue",
  decisionText: "hermes_decisionText",
  decisionContext: "hermes_decisionContext",
  latitude: "geo_lat",
  longitude: "geo_long",
  updatedAt: "hermes_updatedAt",
  createdAt: "hermes_createdAt",
  sourceSession: "hermes_sourceSession",
  sourceSessions: "hermes_sourceSessions",
  extractionIds: "hermes_extractionIds",
  relLabel: "hermes_relLabel",
};

const definitions: Record<string, Copy> = {
  person: {
    title: "人物",
    description: "有姓名或常用称呼的人物，可与事件、偏好、决策和地点建立联系。",
  },
  event: {
    title: "事件",
    description: "发生或计划发生的事情，可记录时间、类别以及参与的人物。",
  },
  preference: {
    title: "偏好",
    description: "人物明确表达的喜好或习惯，包含偏好主题和具体内容。",
  },
  decision: {
    title: "决策",
    description: "人物作出的选择或决定，可同时保留作出选择的背景。",
  },
  location: {
    title: "地点",
    description: "有名称的地方，可按需要补充经纬度坐标。",
  },
  concept: {
    title: "概念",
    description: "项目、主题、术语等领域概念，可与其他记录建立联系。",
  },
  HERMES_knows: {
    title: "认识",
    description: "从一个人物指向其认识的另一个人物。",
  },
  HERMES_participant: {
    title: "参与",
    description: "从人物指向其参与的事件。",
  },
  HERMES_hasPreference: {
    title: "拥有偏好",
    description: "从人物指向属于该人物的偏好记录。",
  },
  HERMES_hasDecision: {
    title: "做出决策",
    description: "从人物指向由该人物作出的决策。",
  },
  HERMES_locatedAt: {
    title: "位于",
    description: "从人物或事件指向相关地点；允许的起点与终点以当前规则为准。",
  },
  LINKED_TO: {
    title: "关联",
    description: "从一条记录指向与它建立关联的另一条记录。",
  },
  CAUSED_BY: {
    title: "由…引起",
    description: "从结果记录指向引起该结果的原因记录。",
  },
  RELATED_TO: {
    title: "相关",
    description: "表示两条记录在内容上有关联，不额外推断因果或身份相同。",
  },
  owl_sameAs: {
    title: "同一实体",
    description: "表示两条记录指向同一个现实对象。",
  },
};

const definitionAliases: Record<string, string> = {
  Person: "person",
  Event: "event",
  Preference: "preference",
  Decision: "decision",
  Location: "location",
  Concept: "concept",
  knows: "HERMES_knows",
  participant: "HERMES_participant",
  hasPreference: "HERMES_hasPreference",
  hasDecision: "HERMES_hasDecision",
  locatedAt: "HERMES_locatedAt",
  linkedTo: "LINKED_TO",
  causedBy: "CAUSED_BY",
  relatedTo: "RELATED_TO",
  sameAs: "owl_sameAs",
};

function copyFor(
  resource: Resource,
  catalog: Record<string, Copy>,
  aliases: Record<string, string>,
): Copy | undefined {
  const find = (key: string | undefined) =>
    key ? (catalog[key] ?? catalog[aliases[key]]) : undefined;
  const direct = find(resource.key);
  if (direct) return direct;
  // A custom definition may reuse a familiar label; preserve its own meaning.
  if (resource.iri?.startsWith("urn:hermes:ontology:"))
    return (
      find(resource.iri.slice("urn:hermes:ontology:".length)) ??
      find(resource.label)
    );
  if (!resource.key && !resource.iri) return find(resource.label);
  return undefined;
}

export function ontologyPropertyTitle(
  property: Pick<
    OntologyProperty,
    "key" | "label" | "description" | "iri" | "customized_fields"
  >,
): string {
  if (property.customized_fields?.includes("label")) return property.label;
  return (
    copyFor(property, properties, propertyAliases)?.title ||
    property.label ||
    property.key
  );
}

export function ontologyPropertyDescription(
  property: Pick<
    OntologyProperty,
    "key" | "label" | "description" | "iri" | "customized_fields"
  >,
): string {
  if (property.customized_fields?.includes("description"))
    return property.description;
  return (
    copyFor(property, properties, propertyAliases)?.description ||
    property.description ||
    ""
  );
}

export function ontologyDefinitionDescription(
  definition: OntologyEntityDefinition | OntologyRelationDefinition,
): string {
  if (definition.customized_fields?.includes("description"))
    return definition.description;
  return (
    copyFor(definition, definitions, definitionAliases)?.description ||
    definition.description ||
    ""
  );
}

const valueTitles: Record<string, string> = {
  hermes_origin: "记录来源",
  origin: "记录来源",
  uri: "记录标识",
  hermes_revision: "记录版本",
  version: "记录版本",
  hermes_userConfirmed: "已经人工确认",
  hermes_aliasConfirmed: "别名已经确认",
  hermes_canonicalUri: "规范记录标识",
  hermes_evidenceJson: "来源证据",
  hermes_confidence: "置信度",
  confidence: "置信度",
};

export function propertyValueTitle(key: string): string {
  return (
    properties[key]?.title ||
    properties[propertyAliases[key]]?.title ||
    valueTitles[key] ||
    key
  );
}
