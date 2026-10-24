import { describe, expect, it } from "vitest";
import { relationLabel } from "./ontologyHelpers";
import {
  ontologyDefinitionDescription,
  ontologyPropertyDescription,
  ontologyPropertyTitle,
} from "./ontologyPresentation";
import { ontologyDefinitionLabel } from "./ontologyV3";
import type { OntologyEntityDefinition } from "./ontologyTypes";

const definition: OntologyEntityDefinition = {
  key: "person",
  label: "Person",
  description: "Original RDF text",
  iri: "urn:hermes:ontology:Person",
  storage_label: "Person",
  runtime_supported: true,
  count: 0,
  properties: [],
};
const property = {
  key: "hermes_name",
  label: "name",
  description: "Original RDF text",
  iri: "urn:hermes:ontology:name",
};

describe("explicit ontology presentation overrides", () => {
  it("keeps readable built-in copy until a field is explicitly customized", () => {
    expect(ontologyDefinitionLabel(definition)).toBe("人物");
    expect(ontologyPropertyTitle(property)).toBe("名称");
    expect(ontologyPropertyDescription(property)).not.toBe("Original RDF text");
  });
  it("shows published definition and relation labels without changing canonical keys", () => {
    expect(
      ontologyDefinitionLabel({
        ...definition,
        label: "联系人",
        customized_fields: ["label"],
      }),
    ).toBe("联系人");
    expect(
      relationLabel({
        key: "HERMES_knows",
        label: "熟识",
        customized_fields: ["label"],
      }),
    ).toBe("熟识");
    expect(definition.key).toBe("person");
  });
  it("honours an explicitly cleared description instead of resurrecting fallback text", () => {
    expect(
      ontologyDefinitionDescription({
        ...definition,
        description: "",
        customized_fields: ["description"],
      }),
    ).toBe("");
    expect(
      ontologyPropertyDescription({
        ...property,
        description: "",
        customized_fields: ["description"],
      }),
    ).toBe("");
  });
  it("customizes fields independently", () => {
    const custom = {
      ...property,
      label: "记录标题",
      customized_fields: ["label"],
    };
    expect(ontologyPropertyTitle(custom)).toBe("记录标题");
    expect(ontologyPropertyDescription(custom)).toBe(
      ontologyPropertyDescription(property),
    );
  });
});
