import { describe, expect, it, vi } from "vitest";
import { createApi } from "./api";

const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json" },
  });
const neighborQuery = {
  center_uri: "test:person:private-center",
  direction: "in" as const,
  relation_types: ["RELATED_TO"],
  cursor: "private-cursor",
  limit: 30,
};
const pathQuery = {
  source_uri: "test:person:private-start",
  target_uri: "test:event:private-end",
  mode: "shortest" as const,
  direction: "out" as const,
  relation_types: ["HERMES_participant"],
  max_hops: 3,
  limit: 5,
};

describe("v3 graph read transport", () => {
  it("keeps every graph identifier, cursor and search string in authenticated POST bodies", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => response({ nodes: [], edges: [] }));
    const api = createApi(() => "synthetic-token", fetcher);
    const controller = new AbortController();
    await api.graphOverview(
      {
        q: "private terms 中文 & name",
        types: ["person"],
        relation_types: ["RELATED_TO"],
        focus_uri: "not-an-overview-input",
      },
      controller.signal,
    );
    await api.graphNeighbors(neighborQuery, controller.signal);
    await api.graphPaths(pathQuery, controller.signal);
    expect(fetcher.mock.calls.map(([url]) => String(url))).toEqual([
      "/api/v1/graph/overview/query",
      "/api/v1/graph/neighbors/query",
      "/api/v1/graph/paths/query",
    ]);
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({
      q: "private terms 中文 & name",
      types: ["person"],
      relation_types: ["RELATED_TO"],
    });
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual(
      neighborQuery,
    );
    expect(JSON.parse(String(fetcher.mock.calls[2][1]?.body))).toEqual(
      pathQuery,
    );
    for (const [url, init] of fetcher.mock.calls) {
      expect(String(url)).not.toMatch(/private|synthetic-token|\?/);
      expect(init?.method).toBe("POST");
      expect(init?.credentials).toBe("same-origin");
      expect(new Headers(init?.headers).get("Authorization")).toBe(
        "Bearer synthetic-token",
      );
      expect(new Headers(init?.headers).get("Content-Type")).toBe(
        "application/json",
      );
      expect(init?.signal).toBeInstanceOf(AbortSignal);
    }
  });

  it("sends an explicit empty overview query and preserves unknown counts and read boundaries", async () => {
    const data = {
      stats_available: false,
      total_nodes: null,
      total_edges: null,
      groups: [],
      relations: [],
      stats_error: { error_type: "unavailable" },
    };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(data));
    const result = await createApi(() => "", fetcher).graphOverview();
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({
      q: "",
      types: [],
      relation_types: [],
    });
    expect(
      new Headers(fetcher.mock.calls[0][1]?.headers).has("Authorization"),
    ).toBe(false);
    expect(result).toEqual(data);
  });

  it("forwards cancellation for all three readers without automatic retries", async () => {
    for (const reader of ["overview", "neighbors", "paths"] as const) {
      const fetcher = vi
        .fn<typeof fetch>()
        .mockImplementation(() => new Promise(() => {}));
      const api = createApi(() => "t", fetcher);
      const controller = new AbortController();
      const promise =
        reader === "overview"
          ? api.graphOverview({}, controller.signal)
          : reader === "neighbors"
            ? api.graphNeighbors(neighborQuery, controller.signal)
            : api.graphPaths(pathQuery, controller.signal);
      controller.abort();
      await expect(promise).rejects.toMatchObject({ name: "AbortError" });
      expect(fetcher).toHaveBeenCalledTimes(1);
      expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(true);
    }
  });

  it("preserves path timeout/limit feedback and neighbour continuation metadata", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        response({
          nodes: [],
          edges: [],
          paths: [],
          timed_out: true,
          truncated: true,
          max_hops: 3,
        }),
      )
      .mockResolvedValueOnce(
        response({
          nodes: [],
          edges: [],
          total_neighbors: 100,
          has_more: true,
          next_cursor: "next-private",
          relation_counts: [],
        }),
      );
    const api = createApi(() => "t", fetcher);
    expect(await api.graphPaths(pathQuery)).toMatchObject({
      timed_out: true,
      truncated: true,
      max_hops: 3,
    });
    expect(await api.graphNeighbors(neighborQuery)).toMatchObject({
      total_neighbors: 100,
      has_more: true,
      next_cursor: "next-private",
    });
  });

  it("invalidates expired authentication without displaying server diagnostics", async () => {
    const unauthorized = vi.fn();
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        response({ detail: "secret database diagnostics" }, 401),
      );
    const promise = createApi(
      () => "expired-token",
      fetcher,
      unauthorized,
    ).graphPaths(pathQuery);
    await expect(promise).rejects.toMatchObject({
      status: 401,
      message: "访问令牌无效或已过期，请重新连接。",
    });
    expect(unauthorized).toHaveBeenCalledTimes(1);
  });
});
