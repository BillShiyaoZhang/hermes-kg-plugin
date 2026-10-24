import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import OntologyEditor from "./OntologyEditor";
import {
  ontologyDraftChanges,
  ontologyDraftFrom,
  ontologyDraftIssues,
  ontologyEditableFields,
  ontologyEditConstraints,
  ontologyEditRequest,
  ontologyScopeOptions,
  ontologyValidationMatches,
  rebaseOntologyDraft,
  type OntologyEditTarget,
} from "./ontologyEditorHelpers";
import type {
  OntologyProperty,
  OntologySchema,
  OntologyValidationResult,
} from "./ontologyTypes";

const property: OntologyProperty = {
  key: "hermes_description",
  label: "description",
  description: "Original definition",
  iri: "urn:test:description",
  domain: ["any"],
  range: ["string"],
  aliases: ["description"],
  required_for: [],
  managed: false,
  runtime_supported: true,
};
const schema = (): OntologySchema => ({
  namespace: "urn:test:workspace:",
  scope: "current_namespace_active",
  read_only: false,
  version: 4,
  editing: {
    scope: "namespace_overlay",
    can_edit: true,
    can_add_types: false,
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
    datatypes_by_property: {
      hermes_description: ["string"],
      geo_lat: ["double", "integer"],
    },
    immutable_keys: ["key", "iri", "storage_label", "storage_type"],
    readonly_properties: [],
  },
  stats_available: false,
  stats_error: null,
  ontology: { iri: "urn:test:", label: "Test", description: "", version: "1" },
  entity_types: ["person", "event"].map((key) => ({
    key,
    label: key,
    description: "",
    iri: `urn:test:${key}`,
    storage_label: key,
    runtime_supported: true,
    count: null,
    properties: [structuredClone(property)],
  })),
  relations: [
    {
      key: "HERMES_knows",
      label: "knows",
      description: "",
      iri: "urn:test:knows",
      storage_type: "HERMES_knows",
      aliases: ["knows"],
      domain: ["person"],
      range: ["person"],
      declared_domain: ["person"],
      declared_range: ["person"],
      count: null,
      properties: [],
      runtime_supported: true,
    },
  ],
  properties: [structuredClone(property)],
  stats: { entities: null, relationships: null, unmapped_relationships: null },
  schema_warnings: [],
});
const target: OntologyEditTarget = { kind: "properties", key: property.key };
const validation = (data: OntologySchema): OntologyValidationResult => ({
  namespace: data.namespace,
  version: data.version!,
  expected_version: data.version!,
  can_publish: true,
  structural: false,
  changes: [],
  impact: {
    checked_nodes: 0,
    checked_relations: 0,
    affected_nodes: 25,
    affected_relations: 0,
    violations: [],
  },
  schema: data,
});

describe("ontology editor drafts and server ownership", () => {
  it("starts from raw schema fields and sends only explicitly changed fields", () => {
    const data = schema();
    const draft = ontologyDraftFrom(data.properties[0]);
    expect(draft.label).toBe("description");
    expect(ontologyDraftChanges(data, target, draft)).toEqual({});
    draft.description = "";
    expect(ontologyEditRequest(data, target, draft)).toEqual({
      expected_namespace: data.namespace,
      expected_version: 4,
      patch: { properties: { hermes_description: { description: "" } } },
    });
  });

  it("never sends identifiers or unsupported structural fields on a type edit", () => {
    const data = schema();
    const typeTarget: OntologyEditTarget = {
      kind: "entity_types",
      key: "person",
    };
    const draft = ontologyDraftFrom(data.entity_types[0]);
    draft.label = "  联系人  ";
    draft.domain = ["event"];
    draft.aliases = "renamed_storage_id";
    expect(ontologyEditRequest(data, typeTarget, draft).patch).toEqual({
      entity_types: { person: { label: "联系人" } },
    });
  });

  it("normalizes alias input without publishing reorder-only changes", () => {
    const data = schema();
    const draft = ontologyDraftFrom(data.properties[0]);
    draft.aliases = "description，description\n";
    expect(ontologyDraftChanges(data, target, draft)).toEqual({});
    draft.aliases += "\nshort_note;short_note";
    expect(ontologyDraftChanges(data, target, draft)).toEqual({
      aliases: ["description", "short_note"],
    });
    expect(ontologyDraftIssues(data, target, draft)).toEqual([]);
    draft.aliases += "\n中文别名";
    expect(ontologyDraftIssues(data, target, draft).join()).toContain(
      "英文字母",
    );
  });

  it("requires explicit capabilities, a version, and a supported non-managed definition", () => {
    const data = schema();
    expect(ontologyEditableFields(data, target)).toContain("required_for");
    for (const patch of [
      { read_only: true },
      { version: undefined },
      { editing: undefined },
    ]) {
      expect(ontologyEditableFields({ ...data, ...patch }, target)).toEqual([]);
    }
    data.properties[0].managed = true;
    expect(ontologyEditableFields(data, target)).toEqual([]);
    data.properties[0].managed = false;
    data.editing!.readonly_properties = [target.key];
    expect(ontologyEditableFields(data, target)).toEqual([]);
    data.editing!.readonly_properties = [];
    data.properties[0].runtime_supported = false;
    expect(ontologyEditableFields(data, target)).toEqual([]);
  });

  it("treats immutable_keys as field names rather than property identifiers", () => {
    const data = schema();
    data.editing!.immutable_keys.push("hermes_description");
    expect(ontologyEditableFields(data, target)).toContain("label");
  });

  it("limits scope choices to the effective baseline and rejects incoherent required rules", () => {
    const data = schema();
    expect(ontologyScopeOptions(data, ["person"])).toEqual(["person"]);
    expect(ontologyScopeOptions(data, ["any"])).toEqual([
      "any",
      "person",
      "event",
    ]);
    const draft = ontologyDraftFrom(data.properties[0]);
    draft.domain = ["person"];
    draft.required_for = ["event"];
    expect(ontologyDraftIssues(data, target, draft).join()).toContain(
      "适用范围",
    );
    data.properties[0].required_for = ["person"];
    draft.required_for = [];
    expect(ontologyDraftIssues(data, target, draft).join()).toContain(
      "不能在此放宽",
    );
  });

  it("rejects unsupported and multiple value types before requesting a preview", () => {
    const data = schema();
    const draft = ontologyDraftFrom(data.properties[0]);
    draft.range = ["string", "integer"];
    const issues = ontologyDraftIssues(data, target, draft).join();
    expect(issues).toContain("只能选择一项");
    expect(issues).toContain("未被当前运行服务支持");
  });

  it("allows reverting custom required rules while protecting the built-in floor", () => {
    const data = schema();
    data.properties[0].required_for = ["person", "event"];
    data.editing!.builtin_constraints = {
      properties: {
        hermes_description: {
          domain: ["any"],
          range: ["string"],
          aliases: ["description"],
          required_for: ["event"],
        },
      },
      relations: {},
    };
    const draft = ontologyDraftFrom(data.properties[0]);
    draft.required_for = ["event"];
    expect(ontologyDraftIssues(data, target, draft)).toEqual([]);
    expect(ontologyDraftChanges(data, target, draft)).toEqual({
      required_for: ["event"],
    });
    draft.required_for = [];
    expect(ontologyDraftIssues(data, target, draft).join()).toContain(
      "内置必填要求",
    );
  });

  it("can restore a custom narrowed scope to the built-in range without adding types", () => {
    const data = schema();
    data.properties[0].domain = ["person"];
    data.editing!.builtin_constraints = {
      properties: {
        hermes_description: {
          domain: ["any"],
          range: ["string"],
          aliases: ["description"],
          required_for: [],
        },
      },
      relations: {
        HERMES_knows: {
          domain: ["person"],
          range: ["person"],
          aliases: ["knows"],
        },
      },
    };
    const floor = ontologyEditConstraints(data, target)!;
    expect(ontologyScopeOptions(data, floor.domain)).toEqual([
      "any",
      "person",
      "event",
    ]);
    const draft = ontologyDraftFrom(data.properties[0]);
    draft.domain = ["any"];
    expect(ontologyDraftIssues(data, target, draft)).toEqual([]);
    const relationTarget: OntologyEditTarget = {
      kind: "relations",
      key: "HERMES_knows",
    };
    const relationDraft = ontologyDraftFrom(data.relations[0]);
    relationDraft.range = ["event"];
    expect(
      ontologyDraftIssues(data, relationTarget, relationDraft).join(),
    ).toContain("内置允许范围");
  });

  it("permits removing custom aliases while retaining built-in compatibility names", () => {
    const data = schema();
    data.properties[0].aliases = ["description", "custom_note"];
    data.editing!.builtin_constraints = {
      properties: {
        hermes_description: {
          domain: ["any"],
          range: ["string"],
          aliases: ["description"],
          required_for: [],
        },
      },
      relations: {},
    };
    const draft = ontologyDraftFrom(data.properties[0]);
    draft.aliases = "description";
    expect(ontologyDraftIssues(data, target, draft)).toEqual([]);
    draft.aliases = "custom_note";
    expect(ontologyDraftIssues(data, target, draft).join()).toContain(
      "内置兼容名称",
    );
  });

  it("rebases touched fields while preserving concurrent changes to untouched fields", () => {
    const draft = ontologyDraftFrom(property);
    draft.label = "我的草稿";
    draft.description = "";
    const latest = {
      ...property,
      label: "其他人的名称",
      description: "其他人的说明",
      aliases: ["description", "new_alias"],
    };
    const rebased = rebaseOntologyDraft(latest, draft, [
      "label",
      "description",
    ]);
    expect(rebased.label).toBe("我的草稿");
    expect(rebased.description).toBe("");
    expect(rebased.aliases).toBe("description\nnew_alias");
    expect(latest.label).toBe("其他人的名称");
  });

  it("cannot adopt a preview from another namespace or baseline version", () => {
    const data = schema();
    const request = ontologyEditRequest(data, target, {
      ...ontologyDraftFrom(property),
      label: "内容",
    });
    const result = validation(data);
    expect(ontologyValidationMatches(result, request)).toBe(true);
    expect(
      ontologyValidationMatches({ ...result, namespace: "other:" }, request),
    ).toBe(false);
    expect(
      ontologyValidationMatches({ ...result, expected_version: 3 }, request),
    ).toBe(false);
    expect(
      ontologyValidationMatches(
        { ...result, schema: { ...data, version: 5 } },
        request,
      ),
    ).toBe(false);
    expect(
      ontologyValidationMatches(
        { ...result, schema: { ...data, namespace: "other:" } },
        request,
      ),
    ).toBe(false);
  });
});

describe("ontology editor form semantics", () => {
  const render = (data: OntologySchema, currentTarget = target) =>
    renderToStaticMarkup(
      <OntologyEditor
        schema={data}
        target={currentTarget}
        transport={{ validate: vi.fn(), publish: vi.fn() }}
        onPublished={() => {}}
        onClose={() => {}}
      />,
    );

  it("keeps the main editor inline and requires an explicit impact preview", () => {
    const html = render(schema());
    expect(html).toContain('role="region" aria-label="编辑本体定义"');
    expect(html).not.toContain('role="dialog"');
    expect(html).toContain("取消编辑");
    expect(html).toContain("预览影响");
    expect(html).toContain("草稿不会自动保存");
    expect(html).toContain('disabled="">');
  });

  it("presents numeric datatype alternatives as a single choice", () => {
    const data = schema();
    data.properties[0] = { ...property, key: "geo_lat", range: ["double"] };
    const html = render(data, { kind: "properties", key: "geo_lat" });
    expect(html.match(/type="radio"/g)).toHaveLength(2);
    expect(html).toContain("double");
    expect(html).toContain("integer");
  });
});
