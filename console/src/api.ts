import type {
  ConsoleStatus,
  EntityType,
  Evidence,
  JsonRecord,
  Memory,
  MemoryPage,
  MutationReceipt,
  Review,
  Revision,
  SyncJob,
  SessionSummary,
  Version,
} from "./types";

export const API_ROOT = "/api/v1";
export const TOKEN_KEY = "hermes-memory-console-token";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export const record = (value: unknown): JsonRecord =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : {};
const array = (value: unknown): unknown[] =>
  Array.isArray(value) ? value : [];
export const stringValue = (...values: unknown[]): string | undefined =>
  values.find((v): v is string => typeof v === "string" && v.length > 0);
const numberValue = (...values: unknown[]): number | null =>
  values.find(
    (v): v is number => typeof v === "number" && Number.isFinite(v),
  ) ?? null;
const boolValue = (...values: unknown[]): boolean | null =>
  values.find((v): v is boolean => typeof v === "boolean") ?? null;
const dateValue = (...values: unknown[]): string | undefined => {
  const value = values.find(
    (v) =>
      (typeof v === "string" && !!v) ||
      (typeof v === "number" && Number.isFinite(v) && v > 0),
  );
  if (typeof value === "number")
    return new Date(value < 1e12 ? value * 1000 : value).toISOString();
  return typeof value === "string" ? value : undefined;
};
const versionValue = (...values: unknown[]): Version | undefined =>
  values.find(
    (v): v is Version => typeof v === "number" || typeof v === "string",
  );

/** Consume a fragment token once. Fragments never enter API URLs or server logs. */
export function consumeFragmentToken(
  url: URL,
  storage: Pick<Storage, "getItem" | "setItem">,
  replace: (url: string) => void,
): string {
  const params = new URLSearchParams(url.hash.slice(1));
  const fragmentToken = params.get("token") || params.get("access_token");
  if (fragmentToken?.trim()) {
    const value = fragmentToken.trim();
    storage.setItem(TOKEN_KEY, value);
    params.delete("token");
    params.delete("access_token");
    url.hash = params.toString();
    replace(url.pathname + url.search + url.hash);
    return value;
  }
  return storage.getItem(TOKEN_KEY) || "";
}

export function initialToken(): string {
  try {
    return consumeFragmentToken(
      new URL(window.location.href),
      sessionStorage,
      (url) => window.history.replaceState(null, "", url),
    );
  } catch {
    return "";
  }
}
export function saveToken(value: string) {
  const token = value.trim();
  if (token) sessionStorage.setItem(TOKEN_KEY, token);
  else sessionStorage.removeItem(TOKEN_KEY);
  return token;
}

export function normalizeEvidence(input: unknown): Evidence[] {
  const values = Array.isArray(input) ? input : input ? [input] : [];
  return values.flatMap((value) => {
    const r = record(value);
    const quote = stringValue(r.quote, r.text);
    const kind = stringValue(r.kind, r.origin);
    return quote || kind === "user_edit"
      ? [
          {
            quote: quote ?? "",
            messageId: versionValue(r.message_id, r.messageId),
            sessionId: stringValue(r.session_id, r.source_session),
            origin: stringValue(r.origin, r.source, r.kind),
            timestamp: dateValue(r.timestamp, r.created_at),
            kind,
            changes: record(r.changes),
            reason: stringValue(r.reason),
          },
        ]
      : [];
  });
}

export function normalizeMemory(input: unknown): Memory {
  const r = record(input);
  const p = record(r.properties ?? r.props);
  const labels = array(r.labels);
  const rawType = String(
    r.type ??
      r.entity_type ??
      labels.find(
        (label) =>
          typeof label === "string" &&
          [
            "person",
            "event",
            "decision",
            "preference",
            "location",
            "concept",
          ].includes(label.toLowerCase()),
      ) ??
      "unknown",
  ).toLowerCase();
  const type = (
    [
      "person",
      "event",
      "decision",
      "preference",
      "location",
      "concept",
    ].includes(rawType)
      ? rawType
      : "unknown"
  ) as EntityType;
  const state = stringValue(r.lifecycle_status, r.status);
  const revisionValues = array(r.revisions);
  const revisions: Revision[] = revisionValues.map((value, index) => {
    const item = record(value);
    return {
      id: String(item.id ?? item.revision_id ?? item.operation_id ?? index),
      action: stringValue(item.action, item.operation, item.kind) ?? "revision",
      reason: stringValue(item.reason, item.note),
      createdAt: dateValue(item.created_at, item.timestamp),
      version: versionValue(item.version, item.new_version),
      changes: record(item.changes),
      state: record(item.state),
    };
  });
  const supporting = normalizeEvidence(r.supporting_evidence);
  const evidence = normalizeEvidence(r.evidence ?? r.evidences);
  return {
    uri: stringValue(r.uri, r.id) ?? "",
    type,
    name:
      stringValue(r.name, p.foaf_name, p.hermes_name, p.name) ?? "未命名记忆",
    description:
      stringValue(
        r.description,
        p.hermes_description,
        p.description,
        r.decision_text,
        p.hermes_decisionText,
        p.decision_text,
      ) ?? "",
    properties: p,
    readOnly: r.read_only === true || r.readOnly === true,
    version: versionValue(
      r.version,
      p.hermes_revision,
      p.hermes_version,
      r.hermes_version,
    ),
    status:
      state === "archived" ||
      r.archived === true ||
      labels.includes("KGQuarantined")
        ? "archived"
        : "active",
    semanticStatus:
      state && !["active", "archived"].includes(state)
        ? state
        : stringValue(r.semantic_status, p.hermes_status),
    updatedAt: dateValue(r.updated_at, r.updatedAt, p.hermes_updatedAt),
    createdAt: dateValue(r.created_at, r.createdAt, p.hermes_createdAt),
    eventTime: stringValue(
      r.time,
      r.event_time,
      p.hermes_eventTime,
      p.event_time,
    ),
    endTime: stringValue(r.end_time, p.hermes_eventEndTime),
    timePrecision: stringValue(r.time_precision, p.hermes_timePrecision),
    participants: array(r.participants ?? r.linked_persons).filter(
      (v): v is string => typeof v === "string",
    ),
    evidence: [...evidence, ...supporting],
    relations: array(r.relations).map(record),
    revisions,
  };
}

export function normalizePage(
  input: unknown,
  offset = 0,
  limit = 30,
): MemoryPage {
  const r = record(input);
  const items = array(
    r.items ??
      r.kg_results ??
      r.results ??
      (Array.isArray(input) ? input : undefined),
  ).map(normalizeMemory);
  return {
    items,
    total: numberValue(r.total) ?? items.length,
    offset: numberValue(r.offset) ?? offset,
    limit: numberValue(r.limit) ?? limit,
    mode: stringValue(r.mode),
  };
}

export function normalizeReview(input: unknown): Review {
  const r = record(input);
  const payload = record(r.payload ?? r.extraction);
  const expected = record(r.expected_versions ?? r.base_versions);
  return {
    id: String(r.id ?? r.snapshot_id ?? ""),
    reason: stringValue(r.reason, r.error),
    status: stringValue(r.status) ?? "pending",
    createdAt: dateValue(r.created_at),
    entities: array(r.entities ?? payload.entities).map(normalizeMemory),
    relations: array(r.relations ?? payload.relations).map(record),
    expectedVersions: Object.fromEntries(
      Object.entries(expected).filter(
        (entry): entry is [string, Version] =>
          typeof entry[1] === "string" || typeof entry[1] === "number",
      ),
    ),
  };
}

export function normalizeJob(input: unknown): SyncJob {
  const r = record(input),
    result = record(r.result);
  return {
    id: String(r.id ?? r.job_id ?? r.snapshot_id ?? ""),
    status: stringValue(r.status) ?? "unknown",
    createdAt: dateValue(r.created_at, r.started_at),
    finishedAt: dateValue(r.finished_at, r.completed_at),
    error: stringValue(
      r.error,
      r.detail,
      result.error,
      r.status === "failed" ? result.message : undefined,
    ),
    summary:
      stringValue(r.summary, r.message) ??
      (r.kind === "prepare"
        ? "准备本地语义模型"
        : r.kind === "index"
          ? "更新搜索索引"
          : r.kind === "sync"
            ? "记忆同步任务"
            : undefined),
  };
}

export function normalizeStatus(input: unknown): ConsoleStatus {
  const r = record(input),
    memory = record(r.memory),
    sync = record(r.sync),
    search = record(r.search),
    model = record(search.model);
  const rawCounts = record(memory.counts);
  const counts = Object.fromEntries(
    Object.entries(rawCounts).filter(
      (entry): entry is [string, number] => typeof entry[1] === "number",
    ),
  );
  const kindCounts = Object.entries(counts).filter(([key]) =>
    [
      "person",
      "event",
      "decision",
      "preference",
      "location",
      "concept",
    ].includes(key.toLowerCase()),
  );
  const lastRun = record(sync.last_run);
  return {
    connected: boolValue(r.connected),
    totalMemories:
      numberValue(
        memory.total,
        memory.active_count,
        counts.total,
        counts.active,
        memory.searchable_entities,
        r.searchable_entities,
      ) ??
      (kindCounts.length
        ? kindCounts.reduce((sum, [, value]) => sum + value, 0)
        : null),
    counts,
    paused: boolValue(sync.paused),
    pendingCount: numberValue(sync.pending_count),
    lastRun: dateValue(
      sync.last_run,
      lastRun.finished_at,
      lastRun.completed_at,
      lastRun.started_at,
      sync.last_success_at,
    ),
    jobs: array(sync.jobs).map(normalizeJob),
    search: {
      mode:
        stringValue(search.mode) ??
        (model.ready === false ? "keyword" : undefined),
      indexed: numberValue(search.indexed, search.indexed_count),
      pending: numberValue(search.pending, search.pending_count),
      modelReady: boolValue(search.model_ready, model.ready),
      ready:
        boolValue(search.ready) ??
        (search.state === "ready"
          ? true
          : ["pending", "unavailable"].includes(String(search.state))
            ? false
            : null),
      indexes: record(search.indexes),
    },
    compatibility: record(r.compatibility),
    raw: r,
  };
}

export function createApi(
  getToken: () => string,
  fetcher: typeof fetch = fetch,
) {
  async function request(
    path: string,
    init: RequestInit = {},
  ): Promise<unknown> {
    const token = getToken();
    const headers = new Headers(init.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    headers.set("Accept", "application/json");
    if (init.body) headers.set("Content-Type", "application/json");
    let response: Response;
    try {
      response = await fetcher(API_ROOT + path, {
        ...init,
        headers,
        credentials: "same-origin",
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError")
        throw error;
      throw new ApiError("无法连接记忆服务。请确认服务正在运行，然后重试。", 0);
    }
    const text = await response.text();
    let data: unknown;
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      throw new ApiError(
        "服务返回了无法读取的响应。请确认 API 地址与服务版本。",
        response.status,
      );
    }
    const r = record(data);
    if (!response.ok || r.ok === false) {
      const detail = r.detail ?? r.error ?? r.message;
      const message =
        typeof detail === "string"
          ? detail
          : response.status === 401
            ? "访问令牌无效或已过期，请重新连接。"
            : response.status === 409
              ? "这条记录已发生变化。请刷新后重新核对，再提交操作。"
              : "请求未能完成，请稍后重试。";
      throw new ApiError(message, response.status, detail);
    }
    return data;
  }
  async function mutate(
    path: string,
    body: JsonRecord = {},
  ): Promise<MutationReceipt> {
    const result = record(
      await request(path, { method: "POST", body: JSON.stringify(body) }),
    );
    if (result.ok !== true && !result.receipt)
      throw new ApiError(
        "服务未返回成功回执。请刷新核对状态，不要重复提交。",
        502,
        result,
      );
    return result;
  }
  return {
    status: async () => normalizeStatus(await request("/status")),
    memories: async (
      query: {
        type?: string;
        status?: string;
        q?: string;
        offset?: number;
        limit?: number;
      } = {},
      signal?: AbortSignal,
    ) => {
      const params = new URLSearchParams();
      Object.entries(query).forEach(([key, value]) => {
        if (value !== undefined && value !== "") params.set(key, String(value));
      });
      return normalizePage(
        await request("/memories?" + params.toString(), { signal }),
        query.offset,
        query.limit,
      );
    },
    search: async (
      question: string,
      filters: JsonRecord = {},
      signal?: AbortSignal,
      interpret = false,
    ) =>
      normalizePage(
        await request("/search", {
          method: "POST",
          body: JSON.stringify({ question, limit: 30, filters, interpret }),
          signal,
        }),
      ),
    detail: async (uri: string) => {
      const value = await request(
        "/memories/detail?" + new URLSearchParams({ uri }),
      );
      const r = record(value);
      return normalizeMemory(r.memory ?? value);
    },
    reviews: async () => {
      const value = await request("/reviews");
      return array(record(value).items ?? value).map(normalizeReview);
    },
    review: (id: string, action: "approve" | "reject", body: JsonRecord) =>
      mutate(`/reviews/${encodeURIComponent(id)}/${action}`, body),
    correct: (memory: Memory, changes: JsonRecord, reason: string) =>
      mutate("/memories/correct", {
        uri: memory.uri,
        changes,
        expected_version: memory.version,
        operation_id: crypto.randomUUID(),
        reason,
      }),
    archive: (memory: Memory, reason: string) =>
      mutate("/memories/archive", {
        uri: memory.uri,
        expected_version: memory.version,
        operation_id: crypto.randomUUID(),
        reason,
      }),
    restore: (memory: Memory, reason: string) =>
      mutate("/memories/restore", {
        uri: memory.uri,
        expected_version: memory.version,
        operation_id: crypto.randomUUID(),
        reason,
      }),
    sync: () => mutate("/sync/run"),
    prepareSearch: () =>
      request("/search/prepare", { method: "POST", body: "{}" }).then(record),
    reindex: () =>
      request("/search/reindex", { method: "POST", body: "{}" }).then(record),
    pause: () => mutate("/sync/pause"),
    resume: () => mutate("/sync/resume"),
    jobs: async () => {
      const value = await request("/jobs");
      return array(record(value).items ?? value).map(normalizeJob);
    },
    sessions: async (): Promise<SessionSummary[]> => {
      const value = await request("/sessions");
      return array(record(value).items).map((item) => {
        const session = record(item);
        return {
          id: String(session.id ?? session.session_id ?? ""),
          source: stringValue(session.source, session.platform) ?? "未提供来源",
          startedAt: dateValue(session.started_at, session.created_at),
          messageCount: numberValue(session.message_count),
        };
      });
    },
    backfill: (sessionIds: string[]) =>
      mutate("/sync/backfill", { session_ids: sessionIds }),
    settings: async () => record(await request("/settings")),
  };
}
export type ConsoleApi = ReturnType<typeof createApi>;
