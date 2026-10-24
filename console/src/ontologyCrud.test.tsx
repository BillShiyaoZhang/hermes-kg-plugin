import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import OntologyManager from "./OntologyManager";
import OntologyMutationEditor from "./OntologyMutationEditor";
import OntologyRiskPanel from "./OntologyRiskPanel";
import { normalizeMemory } from "./api";
import {
  ontologyDraftChanges,
  ontologyDraftFrom,
  ontologyDraftIssues,
} from "./ontologyEditorHelpers";
import {
  canCreateOntology,
  canMutateOntology,
  newOntologyDraft,
  ontologyConfirmationMatches,
  ontologyMutationIssues,
  ontologyMutationPreviewMatches,
  ontologyMutationRequest,
  ontologyRiskReady,
} from "./ontologyCrud";
import type {
  OntologyCollection,
  OntologyRisk,
  OntologySchema,
  OntologyValidationResult,
} from "./ontologyTypes";

function fixture(): OntologySchema {
  const property = {
    key: "x_priority",
    label: "优先级",
    description: "",
    iri: "urn:synthetic:priority",
    domain: ["any"],
    range: ["string"],
    required_for: [],
    aliases: [],
    managed: false,
    runtime_supported: true,
    custom: true,
    active: true,
    allowed_actions: ["update", "deactivate", "delete"],
  };
  return {
    namespace: "urn:crud-test:",
    scope: "current_namespace_active",
    read_only: false,
    version: 8,
    editing: {
      scope: "namespace_overlay",
      can_edit: true,
      can_add_types: true,
      can_add_properties: true,
      can_add_relations: true,
      can_manage_lifecycle: true,
      can_change_storage_ids: false,
      structural_policy: "tighten_only",
      entity_fields: ["label", "description"],
      property_fields: [
        "label",
        "description",
        "aliases",
        "domain",
        "range",
        "required_for",
      ],
      relation_fields: ["label", "description", "aliases", "domain", "range"],
      readonly_properties: [],
      immutable_keys: ["key", "iri"],
      datatypes_by_property: {},
      create_datatypes: [
        "string",
        "integer",
        "double",
        "boolean",
        "date",
        "dateTime",
      ],
      create_value_shapes: ["scalar"],
    },
    ontology: {
      iri: "urn:synthetic:",
      label: "合成本体",
      description: "",
      version: "1",
    },
    entity_types: [
      {
        key: "person",
        label: "人物",
        description: "",
        iri: "urn:synthetic:person",
        storage_label: "Person",
        runtime_supported: true,
        custom: false,
        active: true,
        allowed_actions: ["update", "deactivate"],
        count: 3,
        properties: [property],
      },
      {
        key: "x_project",
        label: "项目",
        description: "",
        iri: "urn:synthetic:project",
        storage_label: "KGCustom_x_project",
        runtime_supported: true,
        custom: true,
        active: true,
        allowed_actions: ["update", "deactivate", "delete"],
        count: 0,
        properties: [property],
      },
    ],
    properties: [property],
    relations: [],
    stats_available: true,
    stats_error: null,
    stats: { entities: 3, relationships: 0, unmapped_relationships: 0 },
    schema_warnings: [],
  };
}
const risk = (): OntologyRisk => ({
  level: "high",
  reasons: ["此操作永久移除定义，不删除事实。"],
  requires_confirmation: true,
  confirmation_phrase: "删除 项目",
  confirmation_token: "scope-version-and-reference-bound-token",
});
const preview = (): OntologyValidationResult => ({
  namespace: "urn:crud-test:",
  version: 8,
  expected_version: 8,
  can_publish: true,
  structural: true,
  changes: [],
  risk: risk(),
  schema: fixture(),
  impact: {
    checked_nodes: 3,
    checked_relations: 0,
    violations: [],
    definitions: [
      {
        kind: "entity_types",
        key: "x_project",
        label: "项目",
        active_nodes: 0,
        archived_nodes: 0,
        active_relations: 0,
        archived_relations: 0,
        pending_reviews: 0,
        history_revisions: 0,
        definition_references: 0,
        reference_count: 0,
      },
    ],
  },
});

describe("ontology CRUD capability and write safety", () => {
  it("requires explicit create and lifecycle capabilities; old edit-only services stay usable", () => {
    const schema = fixture();
    for (const kind of [
      "entity_types",
      "properties",
      "relations",
    ] as OntologyCollection[])
      expect(canCreateOntology(schema, kind)).toBe(true);
    expect(
      canMutateOntology(schema, {
        kind: "entity_types",
        key: "person",
        action: "delete",
      }),
    ).toBe(false);
    schema.read_only = true;
    expect(canCreateOntology(schema, "entity_types")).toBe(false);
    schema.read_only = false;
    delete schema.editing!.can_manage_lifecycle;
    expect(
      canMutateOntology(schema, {
        kind: "entity_types",
        key: "x_project",
        action: "delete",
      }),
    ).toBe(false);
  });
  it("creates separate typed operations without accepting client storage labels or IRIs", () => {
    const schema = fixture();
    const draft = newOntologyDraft("relations");
    Object.assign(draft, {
      key: "X_WORKS_ON",
      label: "  参与项目  ",
      domain: ["person"],
      range: ["x_project"],
      aliases: "works_on,works_on",
    });
    const target = { kind: "relations", key: "", action: "create" } as const;
    expect(ontologyMutationIssues(schema, target, draft)).toEqual([]);
    expect(ontologyMutationRequest(schema, target, draft)).toEqual({
      expected_namespace: schema.namespace,
      expected_version: 8,
      patch: {},
      operations: [
        {
          action: "create",
          kind: "relations",
          key: "X_WORKS_ON",
          definition: {
            label: "参与项目",
            description: "",
            aliases: ["works_on"],
            domain: ["person"],
            range: ["x_project"],
          },
        },
      ],
    });
  });
  it("rejects duplicate inactive keys, unsafe keys, invalid scopes and unsupported value shapes", () => {
    const schema = fixture();
    schema.entity_types[1].active = false;
    const target = { kind: "entity_types", key: "", action: "create" } as const;
    const draft = {
      ...newOntologyDraft("entity_types"),
      key: "x_project",
      label: "项目",
    };
    expect(ontologyMutationIssues(schema, target, draft).join()).toContain(
      "已经存在",
    );
    draft.key = "x_invalid`) DETACH DELETE n";
    expect(ontologyMutationIssues(schema, target, draft).join()).toContain(
      "标识须",
    );
    const property = {
      ...newOntologyDraft("properties"),
      key: "x_cost",
      label: "成本",
      domain: ["x_project"],
      range: ["object"],
      value_shape: "array" as const,
    };
    const issues = ontologyMutationIssues(
      schema,
      { kind: "properties", key: "", action: "create" },
      property,
    ).join();
    expect(issues).toContain("已启用类型");
    expect(issues).toContain("属性值类型");
    expect(issues).toContain("值形式");
  });
  it("allows changes to custom constraints without treating the current value as an immutable builtin floor", () => {
    const schema = fixture();
    const target = { kind: "properties", key: "x_priority" } as const;
    schema.properties[0].domain = ["person"];
    schema.properties[0].required_for = ["person"];
    const draft = ontologyDraftFrom(schema.properties[0]);
    draft.domain = ["any"];
    draft.range = ["integer"];
    draft.required_for = [];
    expect(ontologyDraftIssues(schema, target, draft)).toEqual([]);
    expect(ontologyDraftChanges(schema, target, draft)).toEqual({
      domain: ["any"],
      range: ["integer"],
      required_for: [],
    });
  });
  it("keeps custom types intact while rejecting arbitrary unknown memory type identifiers", () => {
    expect(
      normalizeMemory({
        type: "x_project",
        uri: "urn:synthetic:one",
        name: "项目",
      }).type,
    ).toBe("x_project");
    expect(
      normalizeMemory({ type: "arbitrary_unpublished_garbage" }).type,
    ).toBe("unknown");
  });
  it("fails closed on missing server risk, missing confirmation token or unknown destructive reference counts", () => {
    const request = ontologyMutationRequest(
      fixture(),
      { kind: "entity_types", key: "x_project", action: "delete" },
      newOntologyDraft("entity_types"),
    );
    const result = preview();
    expect(ontologyMutationPreviewMatches(result, request)).toBe(true);
    result.risk = undefined;
    expect(ontologyMutationPreviewMatches(result, request)).toBe(false);
    result.risk = { ...risk(), confirmation_token: undefined };
    expect(ontologyMutationPreviewMatches(result, request)).toBe(false);
    result.risk = risk();
    result.impact.definitions![0].archived_nodes = null;
    expect(ontologyMutationPreviewMatches(result, request)).toBe(false);
    result.can_publish = false;
    expect(ontologyMutationPreviewMatches(result, request)).toBe(true);
    result.namespace = "urn:other-space:";
    expect(ontologyMutationPreviewMatches(result, request)).toBe(false);
  });
  it("requires the exact server-issued phrase and never substitutes confirmation for a valid risk envelope", () => {
    expect(ontologyConfirmationMatches(risk(), "项目")).toBe(false);
    expect(ontologyConfirmationMatches(risk(), "删除 项目")).toBe(true);
    expect(
      ontologyConfirmationMatches(
        { ...risk(), confirmation_token: undefined },
        "删除 项目",
      ),
    ).toBe(false);
    expect(ontologyRiskReady({ ...risk(), requires_confirmation: false })).toBe(
      false,
    );
    expect(
      ontologyConfirmationMatches(
        {
          level: "low",
          reasons: ["更新显示名称"],
          requires_confirmation: false,
        },
        "",
      ),
    ).toBe(true);
  });
});

describe("risk-appropriate ontology management UI", () => {
  const transport = { validate: vi.fn(), publish: vi.fn(), read: vi.fn() };
  it("exposes all three searchable definition categories with creation and lifecycle actions", () => {
    const html = renderToStaticMarkup(
      <OntologyManager
        schema={fixture()}
        transport={transport}
        writeEnabled
        onPublished={vi.fn()}
        onInteractionLockChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(html).toContain("搜索本体定义");
    expect(html).toContain("新建类型");
    expect(html).toContain("定义详情");
    expect((html.match(/>删除<\/button>/g) ?? []).length).toBe(1);
    expect(html).toContain("内建定义保留规范身份");
  });
  it("presents deletion as permanent, never as deleting facts, and requires an explicit preview first", () => {
    const html = renderToStaticMarkup(
      <OntologyMutationEditor
        schema={fixture()}
        target={{ kind: "entity_types", key: "x_project", action: "delete" }}
        transport={transport}
        writeEnabled
        onPublished={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(html).toContain("永久移除此自定义定义");
    expect(html).toContain("<code>x_project</code>");
    expect(html).toContain("不会级联清除记录");
    expect(html).toContain("先检查完整引用范围");
    expect(html).toMatch(/disabled=""[^>]*>.*确认删除/s);
  });
  it("only shows typed confirmation for publishable high-risk operations; blocked operations remain blocked", () => {
    const result = preview();
    const high = renderToStaticMarkup(
      <OntologyRiskPanel
        preview={result}
        confirmation=""
        onConfirmation={vi.fn()}
      />,
    );
    expect(high).toContain("输入“删除 项目”确认此操作");
    expect(high).toContain("历史实体");
    expect(high).toContain("待审候选批次");
    result.can_publish = false;
    expect(
      renderToStaticMarkup(
        <OntologyRiskPanel
          preview={result}
          confirmation=""
          onConfirmation={vi.fn()}
        />,
      ),
    ).not.toContain("autoComplete");
    result.risk = {
      level: "low",
      reasons: ["更新显示信息"],
      requires_confirmation: false,
    };
    result.can_publish = true;
    const low = renderToStaticMarkup(
      <OntologyRiskPanel
        preview={result}
        confirmation=""
        onConfirmation={vi.fn()}
      />,
    );
    expect(low).toContain("低风险");
    expect(low).not.toContain("确认此操作");
  });
});
