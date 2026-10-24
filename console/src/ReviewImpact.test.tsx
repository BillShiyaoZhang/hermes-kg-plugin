import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { normalizeMemory } from "./api";
import type { ConsoleApi } from "./api";
import type { Memory, Review } from "./types";
import ReviewImpact, {
  loadReviewOriginals,
  reviewFieldDifferences,
  reviewImpactSummary,
  reviewOriginalUris,
} from "./ReviewImpact";

const entity = (uri: string, extra: Partial<Memory> = {}) => ({
  ...normalizeMemory({
    uri,
    name: "示例偏好",
    type: "preference",
    description: "无糖茶",
  }),
  ...extra,
});
const review = (
  entities = [entity("urn:fixture:kg:preference-a")],
  relations: Review["relations"] = [],
): Review => ({
  id: "fixture-batch",
  status: "pending",
  entities,
  relations,
});
const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

describe("review impact comparisons", () => {
  it("compares only supplied properties and does not turn omitted fields into deletion", () => {
    const original = entity("urn:fixture:kg:preference-a", {
      properties: { hermes_preferenceValue: "茶", retained: "保留", zero: 0 },
      version: 4,
    });
    const candidate = entity(original.uri, {
      description: "改为无糖茶",
      properties: { pref_value: "无糖茶", zero: 0 },
    });
    const fields = reviewFieldDifferences(candidate, original);
    expect(fields.find((field) => field.key === "pref_value")).toMatchObject({
      before: "茶",
      after: "无糖茶",
      comparison: "changed",
      title: "偏好内容",
    });
    expect(fields.find((field) => field.key === "zero")?.comparison).toBe(
      "same",
    );
    expect(fields.some((field) => field.key === "retained")).toBe(false);
    expect(
      fields.find((field) => field.key === "description")?.comparison,
    ).toBe("changed");
    expect(
      reviewFieldDifferences(
        { ...candidate, description: "", properties: {} },
        original,
      ).some((field) => field.key === "description"),
    ).toBe(false);
  });

  it("keeps structural object equality, empty values and unknown originals distinct", () => {
    const original = entity("uri", {
      properties: { object: { a: 1, b: 2 }, explicit: "旧值" },
    });
    const candidate = entity("uri", {
      properties: { object: { b: 2, a: 1 }, explicit: null },
    });
    expect(
      reviewFieldDifferences(candidate, original).find(
        (field) => field.key === "object",
      )?.comparison,
    ).toBe("same");
    expect(
      reviewFieldDifferences(candidate, original).find(
        (field) => field.key === "explicit",
      ),
    ).toMatchObject({ after: null, comparison: "changed" });
    expect(
      reviewFieldDifferences(candidate).every(
        (field) => field.comparison === "unknown",
      ),
    ).toBe(true);
  });

  it("does not duplicate the normalized name and description properties", () => {
    const candidate = entity("uri", {
      properties: {
        hermes_name: "示例偏好",
        hermes_description: "无糖茶",
        uri: "uri",
      },
    });
    expect(reviewFieldDifferences(candidate).map((field) => field.key)).toEqual(
      ["name", "description"],
    );
  });

  it("counts batch proposals, compared changes and unavailable originals separately", () => {
    const updated = entity("existing", { name: "新名称" }),
      added = entity("new"),
      unavailable = entity("unknown");
    const result = reviewImpactSummary(
      review([updated, added, unavailable], [{ type: "RELATED_TO" }]),
      {
        existing: { state: "ready", memory: entity("existing") },
        new: { state: "outside" },
        unknown: { state: "unavailable" },
      },
    );
    expect(result).toEqual({
      entities: 3,
      relations: 1,
      newProposals: 1,
      unknown: 1,
      changedEntities: 1,
      changedFields: 1,
      compared: 1,
    });
  });
});

describe("review impact original reads", () => {
  it("reads only candidate entities already verified in the active graph", async () => {
    const batch = review([
      entity("checked"),
      entity("unchecked"),
      entity("checked"),
    ]);
    const uris = reviewOriginalUris(batch, ["checked", "unrelated"]);
    expect(uris).toEqual(["checked"]);
    const detail = vi.fn(async (uri) => entity(uri, { version: 7 })),
      publish = vi.fn();
    await loadReviewOriginals(
      { detail },
      uris,
      new AbortController().signal,
      publish,
    );
    expect(detail).toHaveBeenCalledTimes(1);
    expect(detail).toHaveBeenCalledWith("checked");
    expect(publish).toHaveBeenCalledWith("checked", {
      state: "ready",
      memory: entity("checked", { version: 7 }),
    });
  });

  it("preserves unknown status for API failure, archived or mismatched responses", async () => {
    const detail = vi.fn(async (uri) => {
        if (uri === "failed") throw new Error("private-connection-secret");
        return entity(uri === "mismatch" ? "foreign" : uri, {
          status: "archived",
        });
      }),
      publish = vi.fn();
    await loadReviewOriginals(
      { detail },
      ["failed", "archived", "mismatch"],
      new AbortController().signal,
      publish,
    );
    expect(publish.mock.calls).toEqual([
      ["failed", { state: "unavailable" }],
      ["archived", { state: "unavailable" }],
      ["mismatch", { state: "unavailable" }],
    ]);
    expect(JSON.stringify(publish.mock.calls)).not.toContain(
      "private-connection-secret",
    );
  });

  it("suppresses late original responses after a batch switch or cancellation", async () => {
    const first = deferred<Memory>(),
      second = deferred<Memory>(),
      controller = new AbortController(),
      publish = vi.fn();
    const stale = loadReviewOriginals(
      { detail: () => first.promise },
      ["old"],
      controller.signal,
      publish,
    );
    controller.abort();
    const latest = loadReviewOriginals(
      { detail: () => second.promise },
      ["new"],
      new AbortController().signal,
      publish,
    );
    second.resolve(entity("new", { version: 2 }));
    await latest;
    first.resolve(entity("old", { version: 1 }));
    await stale;
    expect(publish).toHaveBeenCalledTimes(1);
    expect(publish.mock.calls[0][0]).toBe("new");
  });
});

describe("review impact presentation", () => {
  it("opens a compact summary and labels graph absence without claiming nonexistence", () => {
    const html = renderToStaticMarkup(
      <ReviewImpact
        api={{} as ConsoleApi}
        review={review()}
        existingUris={[]}
      />,
    );
    expect(html).toContain('class="ri-summary" open=""');
    expect(html).toContain("当前有效图未找到原记录");
    expect(html).toContain("不能据此断言记录不存在");
    expect(html).toContain("未提供的属性不视为删除");
    expect(html).toContain("1 个新记录提议");
    expect(html).not.toContain("原记录版本 v0");
    expect(html).toMatch(
      /<details class="ri-technical"><summary>技术标识<\/summary>/,
    );
  });

  it("keeps processed batches as processed context and relation changes as proposals", () => {
    const batch = review(
      [entity("left"), entity("right")],
      [{ from_urn: "left", to_urn: "right", relation_type: "RELATED_TO" }],
    );
    for (const status of ["approved", "rejected"]) {
      const html = renderToStaticMarkup(
        <ReviewImpact
          api={{} as ConsoleApi}
          review={batch}
          existingUris={[]}
          status={status}
        />,
      );
      expect(html).toContain("已处理上下文");
      expect(html).not.toContain("待核对提议");
      expect(html).toContain("尚未与全部已存关系逐条比对");
      expect(html).toContain("2 个实体 · 1 条关系提议");
      expect(html).not.toContain("<button");
    }
  });

  it("renders candidate text literally and keeps full evidence and write actions elsewhere", () => {
    const html = renderToStaticMarkup(
      <ReviewImpact
        api={{} as ConsoleApi}
        review={review([
          entity("uri", {
            name: '<img src=x onerror="alert(1)">',
            description: "<script>private()</script>",
            properties: { custom: "literal <b>value</b>" },
          }),
        ])}
        existingUris={[]}
      />,
    );
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain("literal &lt;b&gt;value&lt;/b&gt;");
    expect(html).toContain(
      "依据、候选修改与整批批准/拒绝请在下方审阅操作中完成",
    );
  });
});
