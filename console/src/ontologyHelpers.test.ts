import { describe, expect, it } from "vitest";
import {
  describeTypes,
  displayCount,
  pageRange,
  relationTouchesType,
} from "./ontologyHelpers";

describe("ontology scope and statistics", () => {
  it("keeps an unknown count distinct from a measured empty type", () => {
    expect(displayCount(null)).toBe("—");
    expect(displayCount(undefined)).toBe("—");
    expect(displayCount(Number.NaN)).toBe("—");
    expect(displayCount(0)).toBe("0");
  });

  it("uses effective endpoint constraints instead of an ontology's broader declared domain", () => {
    const locatedAt = {
      domain: ["person", "event"] as const,
      range: ["location"] as const,
    };
    const effective = {
      domain: [...locatedAt.domain],
      range: [...locatedAt.range],
    };
    expect(relationTouchesType(effective, "event")).toBe(true);
    expect(relationTouchesType(effective, "location")).toBe(true);
    expect(relationTouchesType(effective, "preference")).toBe(false);
  });

  it("describes a universal endpoint without inventing an additional entity type", () => {
    expect(describeTypes(["any"])).toBe("任意业务类型");
    expect(
      relationTouchesType({ domain: ["any"], range: ["any"] }, "concept"),
    ).toBe(true);
    expect(describeTypes(["person", "event"])).toBe("人物、事件");
    expect(describeTypes(["device"])).toBe("device");
  });

  it("does not present an out-of-range page as zero total records", () => {
    expect(pageRange(20, 0, 12)).toBe("当前页没有记录 · 共 12 条");
    expect(pageRange(0, 0, 0)).toBe("共 0 条");
    expect(pageRange(20, 4, 24)).toBe("21–24 / 24 条");
  });
});
