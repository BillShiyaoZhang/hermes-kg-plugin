import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ConsoleApi } from "./api";
import OntologyInspector from "./OntologyInspector";
import type { OntologyEntityDefinition, OntologySchema } from "./ontologyTypes";

const definition: OntologyEntityDefinition = {
  key: "person",
  label: "Person",
  description: "A person",
  iri: "urn:test:Person",
  storage_label: "Person",
  runtime_supported: true,
  count: null,
  properties: [],
};
const schema: OntologySchema = {
  namespace: "synthetic",
  scope: "current_namespace_active",
  read_only: true,
  stats_available: false,
  stats_error: { error_type: "unavailable" },
  ontology: {
    iri: "urn:test",
    label: "Test",
    description: "Synthetic schema",
    version: "1",
  },
  entity_types: [definition],
  relations: [],
  properties: [],
  stats: { entities: null, relationships: null, unmapped_relationships: null },
  schema_warnings: [
    { code: "runtime_endpoints_differ", message: "采用当前运行端点范围。" },
  ],
};
const render = (value = definition) =>
  renderToStaticMarkup(
    <OntologyInspector
      schema={schema}
      definition={value}
      api={{} as ConsoleApi}
      onInstances={() => {}}
    />,
  );

describe("ontology inspector structure", () => {
  it("distinguishes a disabled definition from an archived entity without blocking historical browsing", () => {
    const html = render({
      ...definition,
      key: "project",
      label: "项目",
      custom: true,
      active: false,
    });
    expect(html).toContain("项目的本体定义");
    expect(html).toContain("自定义定义");
    expect(html).toContain("已停用");
    expect(html).toContain("既有记录和规则仍保留供核对");
    expect(html).toContain("在图中查看此类实例");
    expect(html).not.toContain("当前运行接口尚未支持此定义");
  });
  it("keeps all four tabs linked to real panels for assistive technology", () => {
    const html = render();
    expect(html.match(/role="tab"/g)).toHaveLength(4);
    expect(html.match(/role="tabpanel"/g)).toHaveLength(4);
    const controlledIds = [...html.matchAll(/aria-controls="([^"]+)"/g)].map(
      (match) => match[1],
    );
    for (const id of controlledIds) expect(html).toContain(`id="${id}"`);
  });

  it("explains definition semantics, unknown statistics and readable schema warnings", () => {
    const html = render();
    expect(html).toContain("不表示某条事实已经发生");
    expect(html).toContain("数量暂时未知");
    expect(html).toContain("runtime_endpoints_differ");
    expect(html).toContain("采用当前运行端点范围。");
    expect(html).toContain("在图中查看此类实例");
    const measuredEmpty = render({ ...definition, count: 0 });
    expect(measuredEmpty).toContain("0<small>条 · 全空间统计");
    expect(measuredEmpty).not.toContain("数量暂时未知");
  });

  it("withholds unsupported instance graph actions while preserving the definition", () => {
    const html = render({ ...definition, runtime_supported: false });
    expect(html).toContain("当前运行接口尚未支持此定义");
    expect(html).not.toContain("在图中查看此类实例");
    expect(html).toContain("urn:test:Person");
  });
});
