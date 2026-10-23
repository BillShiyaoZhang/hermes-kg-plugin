import { describe, expect, it, vi } from "vitest";
import {
  ApiError,
  consumeFragmentToken,
  createApi,
  normalizeMemory,
  normalizePage,
  normalizeReview,
  normalizeStatus,
  TOKEN_KEY,
} from "./api";

function response(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("read-only ontology transport", () => {
  it("preserves complete pending totals while paging matched reviews through private POST inputs", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        response({
          items: [],
          total: 1,
          total_pending: 105,
          offset: 0,
          limit: 20,
          has_more: false,
        }),
      );
    const result = await createApi(() => "synthetic-token", fetcher).reviewPage(
      { q: "candidate105 private", offset: 0, limit: 20 },
    );
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/reviews/query");
    expect(fetcher.mock.calls[0][1]?.method).toBe("POST");
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({
      q: "candidate105 private",
      offset: 0,
      limit: 20,
    });
    expect(result.total).toBe(1);
    expect(result.total_pending).toBe(105);
    expect(result.has_more).toBe(false);
  });
  it("keeps graph search and focus identities in an authenticated cancellable POST", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(response({ nodes: [], edges: [], total_nodes: 0 }));
    const controller = new AbortController();
    await createApi(() => "synthetic-token", fetcher).graph(
      {
        q: "private search",
        focus_uri: "urn:private:concept-example",
        types: ["concept"],
        node_limit: 120,
        edge_limit: 400,
      },
      controller.signal,
    );
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe("/api/v1/graph/query");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      q: "private search",
      focus_uri: "urn:private:concept-example",
      types: ["concept"],
      node_limit: 120,
      edge_limit: 400,
    });
    expect(new Headers(init?.headers).get("Authorization")).toBe(
      "Bearer synthetic-token",
    );
    expect(init?.signal).toBeInstanceOf(AbortSignal);
  });
  it("preserves unknown statistics instead of presenting them as empty data", async () => {
    const schema = {
      stats_available: false,
      entity_types: [
        { key: "person", count: null },
        { key: "event", count: 0 },
      ],
    };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(schema));
    const value = await createApi(() => "test-token", fetcher).ontologySchema();
    expect(value.stats_available).toBe(false);
    expect(value.entity_types.map((type) => type.count)).toEqual([null, 0]);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/ontology");
  });

  it("keeps bounded browse inputs in POST bodies instead of access-log URLs", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () =>
      response({
        items: [],
        total: 0,
        offset: 20,
        limit: 20,
        has_more: false,
      }),
    );
    const controller = new AbortController();
    const api = createApi(() => "test-token", fetcher);
    await api.ontologyEntities(
      { type: "event", q: "A&B 中文", offset: 20, limit: 20 },
      controller.signal,
    );
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe("/api/v1/ontology/entities/query");
    expect(JSON.parse(String(init?.body))).toEqual({
      q: "A&B 中文",
      type: "event",
      offset: 20,
      limit: 20,
    });
    expect(init?.signal).toBeInstanceOf(AbortSignal);
    expect(init?.signal?.aborted).toBe(false);
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("Authorization")).toBe(
      "Bearer test-token",
    );
    await api.ontologyRelations({ relation: "HERMES_knows", limit: 20 });
    expect(String(fetcher.mock.calls[1][0])).toBe(
      "/api/v1/ontology/relations/query",
    );
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({
      relation: "HERMES_knows",
      limit: 20,
    });
  });
});

describe("session token transport", () => {
  it("keeps private record identifiers and library queries out of request URLs", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => response({ items: [] }));
    const api = createApi(() => "t", fetcher);
    await api.memories({ q: "private memory text" });
    await api.detail("urn:private:kg:concept-owner");
    expect(fetcher.mock.calls.map(([url]) => String(url))).toEqual([
      "/api/v1/memories/query",
      "/api/v1/memories/detail",
    ]);
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({
      uri: "urn:private:kg:concept-owner",
    });
  });
  it("consumes a fragment token without putting it in a request URL, and preserves other fragments", () => {
    const values = new Map<string, string>();
    const replace = vi.fn();
    const token = consumeFragmentToken(
      new URL(
        "http://localhost:8765/?view=memory#token=secret%2Bvalue&section=reviews",
      ),
      {
        getItem: (key) => values.get(key) ?? null,
        setItem: (key, value) => {
          values.set(key, value);
        },
      },
      replace,
    );
    expect(token).toBe("secret+value");
    expect(values.get(TOKEN_KEY)).toBe(token);
    expect(replace).toHaveBeenCalledWith("/?view=memory#section=reviews");
  });

  it("sends bearer auth only through the header", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(response({ items: [], total: 0 }));
    await createApi(() => "private-token", fetcher).memories({
      type: "person",
      status: "active",
    });
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe("/api/v1/memories/query");
    expect(JSON.parse(String(init?.body))).toEqual({
      type: "person",
      status: "active",
    });
    expect(String(url)).not.toContain("private-token");
    expect(new Headers(init?.headers).get("Authorization")).toBe(
      "Bearer private-token",
    );
  });
});

describe("API failure and write acknowledgement", () => {
  it("bounds a stuck transport without retrying or losing the mutation identifier", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(() => new Promise(() => {}));
    const api = createApi(() => "t", fetcher, undefined, 10);
    const memory = normalizeMemory({ uri: "urn:test:person-a", version: 2 });
    await expect(
      api.correct(memory, { name: "B" }, "更正", "stable-operation-id"),
    ).rejects.toMatchObject({ status: 408 });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(true);
    expect(
      JSON.parse(String(fetcher.mock.calls[0][1]?.body)).operation_id,
    ).toBe("stable-operation-id");
  });

  it("forwards cancellation and preserves AbortError for stale query suppression", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(() => new Promise(() => {}));
    const controller = new AbortController();
    const pending = createApi(() => "t", fetcher).memories(
      {},
      controller.signal,
    );
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(true);
  });

  it("reuses an explicit operation identifier on uncertain write retry", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError("connection lost after commit"))
      .mockResolvedValueOnce(
        response({
          ok: true,
          receipt_id: "operation-stable",
          disposition: "applied",
          idempotent: true,
        }),
      );
    const api = createApi(() => "t", fetcher);
    const memory = normalizeMemory({ uri: "urn:test:person-a", version: 2 });
    await expect(
      api.archive(memory, "过时", "stable-operation-id"),
    ).rejects.toMatchObject({ status: 0 });
    expect(
      await api.archive(memory, "过时", "stable-operation-id"),
    ).toMatchObject({ idempotent: true });
    expect(
      fetcher.mock.calls.map(
        ([, init]) => JSON.parse(String(init?.body)).operation_id,
      ),
    ).toEqual(["stable-operation-id", "stable-operation-id"]);
  });

  it("requires an actual memory receipt, while keeping a deferred write distinct", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(response({ ok: true, status: "queued" }, 202))
      .mockResolvedValueOnce(
        response({
          ok: true,
          receipt_id: "operation-1",
          disposition: "deferred",
        }),
      );
    const api = createApi(() => "t", fetcher);
    await expect(
      api.review("r", "approve", { operation_id: "stable-operation-id" }),
    ).rejects.toMatchObject({ status: 502 });
    expect(
      await api.review("r", "approve", { operation_id: "stable-operation-id" }),
    ).toMatchObject({ disposition: "deferred" });
  });

  it("clears only the currently rejected authentication and never echoes its error details", async () => {
    const onUnauthorized = vi.fn();
    let token = "old-token";
    const old = createApi(
      () => token,
      async () => {
        token = "new-token";
        return response({ detail: "private-header-value" }, 401);
      },
      onUnauthorized,
    );
    await expect(old.status()).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).not.toHaveBeenCalled();
    const current = createApi(
      () => token,
      async () => response({ detail: "private-header-value" }, 401),
      onUnauthorized,
    );
    await expect(current.status()).rejects.toMatchObject({
      status: 401,
      detail: undefined,
    });
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("does not present unexpected upstream diagnostic text as a user error", async () => {
    const api = createApi(
      () => "t",
      async () => response({ detail: "password=private-value" }, 503),
    );
    await expect(api.status()).rejects.toMatchObject({
      status: 503,
      detail: undefined,
    });
    await expect(api.status()).rejects.not.toMatchObject({
      message: expect.stringContaining("private-value"),
    });
  });
  it("preserves 409 so the interface can ask for a refreshed version", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(response({ detail: "Memory version changed" }, 409));
    const memory = normalizeMemory({
      uri: "urn:test:person-a",
      type: "person",
      name: "A",
      version: 2,
    });
    const api = createApi(() => "t", fetcher);
    await expect(
      api.correct(memory, { name: "B" }, "用户更正"),
    ).rejects.toMatchObject({ status: 409, message: "Memory version changed" });
    const payload = JSON.parse(String(fetcher.mock.calls[0][1]?.body));
    expect(payload.expected_version).toBe(2);
    expect(payload.uri).toBe(memory.uri);
    expect(payload.operation_id).toMatch(/^[a-f0-9-]{36}$/);
  });

  it("never treats a 2xx body without a success receipt as a saved memory", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(response({ status: "unknown" }));
    await expect(
      createApi(() => "t", fetcher).archive(
        normalizeMemory({ uri: "urn:test:person-a", version: 0 }),
        "错误记忆",
      ),
    ).rejects.toBeInstanceOf(ApiError);
  });

  it("reports inaccessible service rather than providing sample memories", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockRejectedValue(new TypeError("offline"));
    await expect(
      createApi(() => "t", fetcher).memories(),
    ).rejects.toMatchObject({ status: 0 });
  });

  it("does not mistake a queued model preparation request for model readiness", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(response({ job_id: "job-1" }, 202));
    expect(await createApi(() => "t", fetcher).prepareSearch()).toEqual({
      job_id: "job-1",
    });
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/search/prepare");
  });
});

describe("optional remote interpretation", () => {
  it("keeps model interpretation off unless explicitly requested", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async () =>
        response({ kg_results: [], mode: "keyword" }),
      );
    const api = createApi(() => "t", fetcher);
    await api.search("最近见过谁", { entity_type: "event" });
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body)).interpret).toBe(
      false,
    );
    await api.search("最近见过谁", {}, undefined, true);
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body)).interpret).toBe(
      true,
    );
  });
});

describe("backend contract adaptation", () => {
  it("keeps lexical fallback warnings, bounded totals and separate sync queue states", () => {
    expect(
      normalizePage({
        items: [],
        total: 10000,
        truncated: true,
        mode: "keyword",
        warnings: ["vector_pending", false],
      }),
    ).toMatchObject({
      truncated: true,
      mode: "keyword",
      warnings: ["vector_pending"],
    });
    expect(
      normalizeStatus({
        sync: {
          queued_sessions: 4,
          pending_count: 1,
          failed_count: 1,
          paused: false,
          scheduler_enabled: false,
          source_available: true,
          available: true,
        },
      }),
    ).toMatchObject({
      queuedCount: 4,
      pendingCount: 1,
      failedCount: 1,
      paused: false,
      schedulerEnabled: false,
      sourceAvailable: true,
      syncAvailable: true,
    });
    expect(
      normalizeStatus({
        search: { state: "unavailable", model: { ready: false } },
      }).search,
    ).toMatchObject({ mode: "unavailable", ready: false });
  });
  it("preserves the server read-only boundary for shared memory details", () => {
    expect(
      normalizeMemory({ uri: "urn:external:person-a", read_only: true })
        .readOnly,
    ).toBe(true);
    expect(normalizeMemory({ uri: "urn:own:person-a" }).readOnly).toBe(false);
  });
  it("keeps absent memory versions absent, while retaining valid version zero", () => {
    expect(normalizeMemory({ name: "A" }).version).toBeUndefined();
    expect(
      normalizeMemory({ properties: { hermes_revision: 0 } }).version,
    ).toBe(0);
  });

  it("reads nested graph health, numeric run times and local model status", () => {
    const status = normalizeStatus({
      connected: true,
      memory: { searchable_entities: 8 },
      sync: { paused: false, last_run: 1700000000, pending_count: 2 },
      search: {
        state: "pending",
        model: { ready: false },
        pending_count: 3,
        indexes: { vector: { state: "POPULATING", population_percent: 20 } },
      },
    });
    expect(status.totalMemories).toBe(8);
    expect(status.lastRun).toBe("2023-11-14T22:13:20.000Z");
    expect(status.search).toMatchObject({
      mode: "keyword",
      ready: false,
      modelReady: false,
      pending: 3,
    });
    expect(status.search.indexes.vector).toEqual({
      state: "POPULATING",
      population_percent: 20,
    });
  });

  it("preserves every endpoint version in an approval batch", () => {
    const review = normalizeReview({
      id: "r-1",
      base_versions: { "urn:test:person-a": 2, "urn:test:event-a": 0 },
      payload: {
        entities: [{ uri: "urn:test:event-a", type: "Event", name: "见面" }],
      },
    });
    expect(review.expectedVersions).toEqual({
      "urn:test:person-a": 2,
      "urn:test:event-a": 0,
    });
    expect(review.entities[0].type).toBe("event");
  });

  it("keeps user edits distinct from quoted source background", () => {
    const memory = normalizeMemory({
      evidence: [
        { kind: "source_background", quote: "我可能会去参加" },
        {
          kind: "user_edit",
          changes: { name: "已确认参加" },
          reason: "本人确认",
        },
      ],
    });
    expect(memory.evidence).toHaveLength(2);
    expect(memory.evidence[0].kind).toBe("source_background");
    expect(memory.evidence[1]).toMatchObject({
      kind: "user_edit",
      quote: "",
      reason: "本人确认",
    });
  });
});
