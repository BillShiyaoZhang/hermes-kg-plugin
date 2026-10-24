import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { normalizeMemory } from "./api";
import type { ConsoleApi } from "./api";
import type { GraphNode } from "./graphModel";
import type { Review } from "./types";
import {
  cleanWorkspaceSeeds,
  cleanWorkspaceView,
  CompareDialog,
  comparisonCandidate,
  loadComparisonDetails,
  readWorkspaceViews,
  WorkspaceDialog,
  workspaceViewStorageKey,
  writeWorkspaceViews,
  type NamedWorkspaceView,
} from "./WorkspaceDialogs";

const memory = normalizeMemory({
  uri: "test:person:ada",
  type: "person",
  name: "Ada",
  properties: { role: "Engineer" },
  evidence: [
    { quote: "A synthetic source quote", session_id: "synthetic-session" },
  ],
});
const candidate: GraphNode = {
  id: "candidate:batch:0:test:person:ada",
  uri: memory.uri,
  reviewId: "batch",
  type: "person",
  layer: "candidate",
  label: "Ada proposal",
};
const saved: GraphNode = {
  id: "saved:test:person:ada",
  uri: memory.uri,
  type: "person",
  layer: "saved",
  label: "Ada fact",
};
const reviews: Review[] = [
  {
    id: "batch",
    status: "pending",
    reason: "Synthetic proposal",
    entities: [memory],
    relations: [],
  },
];
const storage = () => {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
  };
};
const view = (
  overrides: Partial<NamedWorkspaceView> = {},
): NamedWorkspaceView => ({
  id: "view1",
  name: "Projects",
  createdAt: "2026-10-23T12:00:00Z",
  lens: "explore",
  state: { layout: "relation", canvas: { positions: { a: { x: 20, y: 40 } } } },
  seeds: [
    {
      center_uri: "test:person:ada",
      direction: "both",
      relation_types: ["RELATED_TO"],
      cursor: null,
      limit: 30,
    },
  ],
  ...overrides,
});

describe("workspace dialogs", () => {
  it("has a labelled modal and an explicit close control", () => {
    const html = renderToStaticMarkup(
      <WorkspaceDialog title="核对对象" onClose={() => {}}>
        <p>Content</p>
      </WorkspaceDialog>,
    );
    expect(html).toContain('role="dialog"');
    expect(html).toContain('aria-modal="true"');
    expect(html).toContain('aria-label="关闭对话框"');
    expect(html).toContain("核对对象");
  });

  it("retains candidate batch identity when a fact has the same URI", () => {
    expect(comparisonCandidate(candidate, reviews).memory).toBe(memory);
    expect(comparisonCandidate(saved, reviews).memory).toBeUndefined();
    const reference = comparisonCandidate(
      { ...candidate, id: "candidate-endpoint:batch:test:person:ada" },
      reviews,
    );
    expect(reference.reference).toBe(true);
    expect(reference.memory).toBeUndefined();
  });

  it("treats aggregate and group objects as collections, never memory details", () => {
    const detail = vi.fn();
    const html = renderToStaticMarkup(
      <CompareDialog
        api={{ detail } as unknown as ConsoleApi}
        items={[
          {
            id: "aggregate:person",
            layer: "aggregate",
            type: "person",
            label: "People",
            count: 30,
          },
          {
            id: "group:project",
            layer: "group",
            type: "group",
            label: "Project",
            count: 2,
            memberIds: ["a", "b"],
          },
        ]}
        reviews={reviews}
        onClose={() => {}}
      />,
    );
    expect(html).toContain("集合视图");
    expect(html).toContain("临时组合的已加载对象");
    expect(html).toContain("30 项");
    expect(detail).not.toHaveBeenCalled();
  });

  it("separates candidate attributes and evidence and limits comparison to six objects", () => {
    const html = renderToStaticMarkup(
      <CompareDialog
        api={{} as ConsoleApi}
        items={Array.from({ length: 8 }, (_, index) => ({
          ...candidate,
          id: index ? `candidate:batch:${index}:${memory.uri}` : candidate.id,
        }))}
        reviews={reviews}
        onClose={() => {}}
      />,
    );
    expect(html.match(/class="gw-comparison-card"/g)).toHaveLength(6);
    expect(html).toContain("待审提议，尚未作为已保存事实应用");
    expect(html).toContain("A synthetic source quote");
    expect(html).toContain("Engineer");
    expect(html).toContain("最多并列核对 6 项");
  });
});

describe("comparison asynchronous ownership", () => {
  it("ignores a late result after the comparison changes or closes", async () => {
    let resolve!: (value: typeof memory) => void;
    const pending = new Promise<typeof memory>((done) => {
      resolve = done;
    });
    const detail = vi.fn().mockReturnValue(pending);
    const result = vi.fn();
    const cancel = loadComparisonDetails(
      { detail },
      [saved, candidate],
      result,
    );
    await Promise.resolve();
    cancel();
    resolve(memory);
    await pending;
    await Promise.resolve();
    await Promise.resolve();
    expect(detail).toHaveBeenCalledTimes(1);
    expect(result).not.toHaveBeenCalled();
  });

  it("keeps successful reads available if a sibling read fails", async () => {
    const detail = vi
      .fn()
      .mockImplementation((uri: string) =>
        uri === memory.uri
          ? Promise.resolve(memory)
          : Promise.reject(new Error("unavailable")),
      );
    const result = vi.fn();
    loadComparisonDetails(
      { detail },
      [saved, { ...saved, id: "saved:test:bad", uri: "test:bad" }],
      result,
    );
    await vi.waitFor(() => expect(result).toHaveBeenCalledTimes(2));
    expect(result).toHaveBeenCalledWith(saved.id, { loading: false, memory });
    expect(result).toHaveBeenCalledWith(
      "saved:test:bad",
      expect.objectContaining({ loading: false, error: expect.any(Error) }),
    );
  });
});

describe("namespace-local named views", () => {
  it("preserves useful coordinates, filters and identifiers while removing credentials and evidence", () => {
    expect(
      cleanWorkspaceView({
        canvas: { positions: { a: { x: 5, y: -7 } }, pinned: ["a"] },
        search: "project",
        pathIndex: 1,
        token: "secret",
        auth: { accessToken: "secret" },
        evidence: [{ quote: "private" }],
        properties: { evidenceJson: "private" },
        expansions: { snapshot: "private" },
        path: { nodes: [] },
      }),
    ).toEqual({
      canvas: { positions: { a: { x: 5, y: -7 } }, pinned: ["a"] },
      search: "project",
      pathIndex: 1,
      auth: {},
      expansions: {},
      path: null,
    });
    const shared = { x: 1, y: 2 };
    expect(cleanWorkspaceView({ a: shared, b: shared })).toEqual({
      a: shared,
      b: shared,
    });
    const circular: Record<string, unknown> = { x: 3 };
    circular.self = circular;
    expect(cleanWorkspaceView(circular)).toEqual({ x: 3 });
    expect(
      cleanWorkspaceView({
        positions: { "saved:test:concept:evidence": { x: 12, y: 16 } },
      }),
    ).toEqual({
      positions: { "saved:test:concept:evidence": { x: 12, y: 16 } },
    });
  });

  it("stores only bounded namespace-valid expansion queries and resets pagination", () => {
    const valid = {
      center_uri: "test:person:ada",
      direction: "both",
      relation_types: ["RELATED_TO", "RELATED_TO"],
      cursor: "old-page",
      snapshot: { evidence: "private" },
    };
    const cleaned = cleanWorkspaceSeeds(
      [
        valid,
        { ...valid, center_uri: "foreign:person:b" },
        { ...valid, direction: "sideways" },
        ...Array.from({ length: 25 }, () => valid),
      ],
      "test:",
    );
    expect(cleaned).toHaveLength(20);
    expect(cleaned[0]).toEqual({
      center_uri: valid.center_uri,
      direction: "both",
      relation_types: ["RELATED_TO"],
      limit: 30,
      cursor: null,
    });
    expect(cleanWorkspaceSeeds([valid], "")).toEqual([]);
  });

  it("isolates storage by namespace and safely loads a named view", () => {
    const local = storage();
    writeWorkspaceViews("test:", [view()], local);
    expect(readWorkspaceViews("test:", local)).toEqual([view()]);
    expect(readWorkspaceViews("other:", local)).toEqual([]);
    expect(workspaceViewStorageKey("test:")).not.toBe(
      workspaceViewStorageKey("other:"),
    );
  });

  it("rejects malformed storage, incompatible versions and over-limit writes", () => {
    const local = storage();
    local.setItem(workspaceViewStorageKey("test:"), "{broken");
    expect(() => readWorkspaceViews("test:", local)).toThrow("无法读取");
    local.setItem(
      workspaceViewStorageKey("test:"),
      JSON.stringify({ version: 2, namespace: "test:", views: [] }),
    );
    expect(() => readWorkspaceViews("test:", local)).toThrow("不匹配");
    expect(() =>
      writeWorkspaceViews(
        "test:",
        Array.from({ length: 21 }, () => view()),
        local,
      ),
    ).toThrow("20");
  });

  it("ignores invalid entries and re-cleans imported state and seeds", () => {
    const local = storage();
    local.setItem(
      workspaceViewStorageKey("test:"),
      JSON.stringify({
        version: 1,
        namespace: "test:",
        views: [
          {
            ...view(),
            state: { layout: "type", token: "bad", evidence: "private" },
            seeds: [
              {
                center_uri: "foreign:ada",
                direction: "both",
                relation_types: [],
              },
            ],
          },
          { ...view({ id: "invalid", lens: "unknown" as "explore" }) },
          { ...view({ id: "broken", createdAt: "not-a-date" }) },
          {
            ...view({ id: "broken-group" }),
            state: {
              groups: [{ id: "bad", label: "Broken", memberIds: null }],
            },
          },
        ],
      }),
    );
    expect(readWorkspaceViews("test:", local)).toEqual([
      { ...view(), state: { layout: "type" }, seeds: [] },
    ]);
  });
});
