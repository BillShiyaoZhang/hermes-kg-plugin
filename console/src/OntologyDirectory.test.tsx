import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { ConsoleApi } from "./api";
import GraphExplorer from "./GraphExplorer";
import OntologyDirectory from "./OntologyDirectory";
import {
  allDirectoryTypesSelected,
  currentDirectoryRelations,
  DIRECTORY_STATE_VERSION,
  directoryDefinitions,
  directoryGraphKey,
  directoryGraphQuery,
  restoreDirectoryState,
  toggleDirectoryRelation,
} from "./graphDirectory";
import type { OntologySchema } from "./ontologyTypes";

const conceptNames = [
  "人物",
  "事件",
  "偏好",
  "决策",
  "地点",
  "概念",
  "组织",
  "项目",
  "任务",
  "目标",
  "资料",
  "技能",
  "实物",
  "习惯",
  "健康记录",
  "财务记录",
  "知识主题",
];
const conceptKeys = [
  "person",
  "event",
  "preference",
  "decision",
  "location",
  "concept",
  "x_personal_organization",
  "x_personal_project",
  "x_personal_task",
  "x_personal_goal",
  "x_personal_resource",
  "x_personal_skill",
  "x_personal_item",
  "x_personal_habit",
  "x_personal_health_record",
  "x_personal_finance_record",
  "x_personal_topic",
];
function fixture(): OntologySchema {
  return {
    namespace: "urn:directory:synthetic:",
    scope: "current_namespace_active",
    read_only: true,
    stats_available: true,
    stats_error: null,
    ontology: {
      iri: "urn:directory:ontology",
      label: "个人本体",
      description: "",
      version: "1",
    },
    entity_types: conceptKeys.map((key, index) => ({
      key,
      label: conceptNames[index],
      description: `${conceptNames[index]}的中文定义`,
      iri: `urn:directory:type:${key}`,
      storage_label: `Synthetic${index}`,
      runtime_supported: true,
      active: true,
      custom: index >= 6,
      count: index === 16 ? null : index,
      properties: [],
    })),
    relations: ["x_contains", "x_about"].map((key) => ({
      key,
      label: key === "x_contains" ? "包含任务" : "关于主题",
      description:
        key === "x_contains" ? "项目包含任务。" : "资料涉及知识主题。",
      iri: `urn:directory:relation:${key}`,
      storage_type: key,
      aliases: [],
      domain: ["any"],
      range: ["any"],
      declared_domain: ["any"],
      declared_range: ["any"],
      count: 2,
      properties: [],
      runtime_supported: true,
      active: true,
      custom: true,
    })),
    properties: [],
    stats: { entities: 1200, relationships: 500, unmapped_relationships: 0 },
    schema_warnings: [],
  };
}
const view = () => ({
  filterQuery: "",
  types: [...conceptKeys],
  relations: [] as string[],
  focusUri: null,
});

describe("shared ontology directory", () => {
  it("shows the directory by default in Explore with a discoverable text control", () => {
    const html = renderToStaticMarkup(
      <GraphExplorer
        api={{} as ConsoleApi}
        enabled={false}
        refreshKey={0}
        refresh={vi.fn()}
        onSelectMemory={vi.fn()}
        onSelectReview={vi.fn()}
        onClearSelection={vi.fn()}
        detail={null}
        library={null}
      />,
    );
    expect(html).toContain('aria-label="图谱目录与筛选"');
    expect(html).toContain('aria-controls="gw-ontology-directory"');
    expect(html).toContain('title="按本体概念筛选"');
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain("has-directory");
  });

  it("renders all 17 effective concepts with Chinese definitions and independent unknown/empty counts", () => {
    const html = renderToStaticMarkup(
      <OntologyDirectory
        schema={fixture()}
        types={conceptKeys}
        relations={[]}
        onTypesChange={vi.fn()}
        onRelationsChange={vi.fn()}
      />,
    );
    for (const name of conceptNames)
      expect(html).toContain(`aria-label="${name}"`);
    expect(html).toContain("知识主题的中文定义");
    expect(html).toContain("项目包含任务。");
    expect(html).toContain("本体概念 · 17");
    expect(html).toContain("已选 17 / 17");
    expect(html).toContain("暂无记录");
    expect(html).toContain('aria-label="当前空间 0 条记录"');
    expect(html).toContain('aria-label="数量未知"');
    expect(html).toContain(">—</span>");
    expect(html).toContain("尚未加载到画布");
    expect(html).toContain(">全选</button>");
    expect(html).toContain(">清空</button>");
  });

  it("only offers active concepts and relations supported by the current runtime", () => {
    const schema = fixture();
    schema.entity_types[0].active = false;
    schema.entity_types[1].runtime_supported = false;
    schema.relations[0].active = false;
    schema.relations[1].runtime_supported = false;
    const definitions = directoryDefinitions(schema);
    expect(definitions.types).toHaveLength(15);
    expect(definitions.relations).toEqual([]);
    const html = renderToStaticMarkup(
      <OntologyDirectory
        schema={schema}
        types={conceptKeys}
        relations={[]}
        onTypesChange={vi.fn()}
        onRelationsChange={vi.fn()}
      />,
    );
    expect(html).not.toContain('aria-label="人物"');
    expect(html).not.toContain('aria-label="事件"');
    expect(html).not.toContain('aria-label="包含任务"');
    expect(html).toContain("当前没有可用于筛选的有效关系");
  });
});

describe("directory filtering request semantics", () => {
  it("sends selected ontology keys to both graph queries even if their records are not loaded", () => {
    const selected = {
      ...view(),
      filterQuery: "待办",
      types: ["x_personal_task"],
      relations: ["x_contains"],
    };
    expect(directoryGraphQuery(selected, conceptKeys)).toEqual({
      q: "待办",
      types: ["x_personal_task"],
      relation_types: ["x_contains"],
      focus_uri: null,
      node_limit: 200,
      edge_limit: 500,
    });
    expect(allDirectoryTypesSelected(selected.types, conceptKeys)).toBe(false);
  });

  it("keeps empty selection separate from API all-types and its cache key", () => {
    const all = directoryGraphQuery(view(), conceptKeys);
    const empty = directoryGraphQuery({ ...view(), types: [] }, conceptKeys);
    expect(all?.types).toEqual([]);
    expect(empty).toBeNull();
    expect(JSON.stringify(["urn:test:", empty])).not.toBe(
      JSON.stringify(["urn:test:", all]),
    );
    expect(allDirectoryTypesSelected(conceptKeys, conceptKeys)).toBe(true);
  });

  it("preserves all, subset and no-relations selections without submitting the local sentinel", () => {
    const available = ["x_contains", "x_about"];
    const subset = toggleDirectoryRelation([], "x_about", available);
    expect(subset).toEqual(["x_contains"]);
    const empty = toggleDirectoryRelation(subset, "x_contains", available);
    expect(empty).toEqual(["__none__"]);
    expect(
      directoryGraphQuery({ ...view(), relations: empty }, conceptKeys)
        ?.relation_types,
    ).toEqual([]);
    expect(toggleDirectoryRelation(empty, "x_about", available)).toEqual([
      "x_about",
    ]);
    expect(
      directoryGraphQuery({ ...view(), relations: [] }, conceptKeys)
        ?.relation_types,
    ).toEqual([]);
  });

  it("does not reuse a no-relations projection when restoring all relationships", () => {
    const all = directoryGraphQuery(view(), conceptKeys);
    const none = directoryGraphQuery(
      { ...view(), relations: ["__none__"] },
      conceptKeys,
    );
    expect(none).toEqual(all);
    expect(directoryGraphKey("urn:test:", none, ["__none__"])).not.toBe(
      directoryGraphKey("urn:test:", all, []),
    );
    expect(directoryGraphKey("urn:test:", all, [])).toBe(
      directoryGraphKey("urn:test:", directoryGraphQuery(view(), conceptKeys), []),
    );
  });

  it("removes unavailable definitions without broadening an explicit relation selection", () => {
    const schema = fixture();
    schema.relations[0].active = false;
    expect(currentDirectoryRelations([], schema)).toEqual([]);
    expect(currentDirectoryRelations(["x_contains"], schema)).toEqual([
      "__none__",
    ]);
    expect(
      currentDirectoryRelations(["x_contains", "x_about"], schema),
    ).toEqual(["x_about"]);
    expect(currentDirectoryRelations(["__none__"], schema)).toEqual([
      "__none__",
    ]);
    schema.relations[1].active = false;
    expect(currentDirectoryRelations([], schema)).toEqual(["__none__"]);
  });

  it("keeps directory all inside active definitions when runtime also retains disabled history", () => {
    const schema = fixture();
    schema.entity_types[0].active = false;
    schema.relations[0].active = false;
    const available = directoryDefinitions(schema);
    const keys = available.types.map((item) => item.key);
    const query = directoryGraphQuery({ ...view(), types: keys }, keys, schema);
    expect(query?.types).toEqual(keys);
    expect(query?.types).not.toContain("person");
    expect(query?.relation_types).toEqual(["x_about"]);
    expect(
      directoryGraphQuery(
        { ...view(), types: keys, relations: ["__none__"] },
        keys,
        schema,
      )?.relation_types,
    ).toEqual([]);
  });
});

describe("directory session migration", () => {
  it("opens the former Explore default once and preserves filters, camera and identity", () => {
    const legacy = {
      ...view(),
      sidebar: false,
      canvases: { relation: { positions: { a: { x: 10, y: 20 } } } },
    };
    const migrated = restoreDirectoryState(legacy, "explore");
    expect(migrated.sidebar).toBe(true);
    expect(migrated.directoryVersion).toBe(DIRECTORY_STATE_VERSION);
    expect(migrated.types).toBe(legacy.types);
    expect(migrated.canvases).toBe(legacy.canvases);
    expect(legacy.sidebar).toBe(false);
  });

  it("keeps an explicit collapse when reloading or opening a saved view", () => {
    const saved = {
      ...view(),
      sidebar: false,
      directoryVersion: DIRECTORY_STATE_VERSION,
    };
    expect(restoreDirectoryState(saved, "explore")).toBe(saved);
    expect(restoreDirectoryState({ sidebar: false }, "ontology").sidebar).toBe(
      false,
    );
    expect(restoreDirectoryState({ sidebar: false }, "review").sidebar).toBe(
      false,
    );
  });
});
