import { describe, expect, it, vi } from "vitest";
import {
  ApiError,
  consumeFragmentToken,
  createApi,
  normalizeMemory,
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

describe("session token transport", () => {
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
    expect(url).toBe("/api/v1/memories?type=person&status=active");
    expect(String(url)).not.toContain("private-token");
    expect(new Headers(init?.headers).get("Authorization")).toBe(
      "Bearer private-token",
    );
  });
});

describe("API failure and write acknowledgement", () => {
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
