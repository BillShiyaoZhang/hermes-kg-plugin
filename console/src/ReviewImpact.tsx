import { useEffect, useId, useMemo, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import type { ConsoleApi } from "./api";
import type { JsonRecord, Memory, Review } from "./types";
import { relationLabel } from "./ontologyHelpers";
import { propertyValueTitle } from "./ontologyPresentation";
import { typeLabels } from "./lib";
import "./reviewImpact.css";

export interface ReviewImpactProps {
  api: ConsoleApi;
  review: Review;
  /** Only URIs already verified against the active graph in this workspace. */
  existingUris: string[];
  status?: string;
}
export type ReviewOriginal =
  | { state: "loading" | "outside" | "unavailable" }
  | { state: "ready"; memory: Memory };
export type ReviewOriginals = Record<string, ReviewOriginal>;
export interface ReviewFieldDifference {
  key: string;
  title: string;
  before: unknown;
  after: unknown;
  comparison: "changed" | "same" | "unknown";
}

// Candidate properties may use the write API's aliases; compare their stored
// counterparts rather than claiming an existing field is newly introduced.
const PROPERTY_ALIASES: Record<string, string> = {
  name: "hermes_name",
  description: "hermes_description",
  note: "hermes_note",
  event_time: "hermes_eventTime",
  eventTime: "hermes_eventTime",
  event_type: "hermes_eventType",
  eventType: "hermes_eventType",
  end_time: "hermes_eventEndTime",
  event_end_time: "hermes_eventEndTime",
  time_precision: "hermes_timePrecision",
  event_status: "hermes_eventStatus",
  alias_names: "hermes_aliasNames",
  semantic_scope: "hermes_semanticScope",
  status: "hermes_status",
  status_note: "hermes_statusNote",
  pref_key: "hermes_preferenceKey",
  key: "hermes_preferenceKey",
  preference_key: "hermes_preferenceKey",
  pref_value: "hermes_preferenceValue",
  value: "hermes_preferenceValue",
  preference_value: "hermes_preferenceValue",
  decision: "hermes_decisionText",
  decision_text: "hermes_decisionText",
  decision_context: "hermes_decisionContext",
  latitude: "geo_lat",
  hermes_lat: "geo_lat",
  longitude: "geo_long",
  hermes_lng: "geo_long",
};
const canonicalProperty = (key: string) => PROPERTY_ALIASES[key] ?? key;
const own = (value: JsonRecord, key: string) =>
  Object.prototype.hasOwnProperty.call(value, key);
const orderedValue = (value: unknown): unknown =>
  Array.isArray(value)
    ? value.map(orderedValue)
    : value && typeof value === "object"
      ? Object.fromEntries(
          Object.entries(value)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([key, item]) => [key, orderedValue(item)]),
        )
      : value;
const equivalent = (left: unknown, right: unknown) =>
  JSON.stringify(orderedValue(left)) === JSON.stringify(orderedValue(right));

/** Only supplied fields participate; omitted original properties are retained. */
export function reviewFieldDifferences(
  candidate: Memory,
  original?: Memory,
): ReviewFieldDifference[] {
  // Review normalization fills an omitted description with an empty string.
  // Without a supplied property we cannot interpret that default as deletion.
  const descriptionProvided =
    candidate.description !== "" ||
    ["description", "hermes_description"].some(
      (key) =>
        own(candidate.properties, key) &&
        candidate.properties[key] === candidate.description,
    );
  const fields = [
    {
      key: "name",
      title: "名称",
      before: original?.name,
      after: candidate.name,
    },
    ...(descriptionProvided
      ? [
          {
            key: "description",
            title: "内容说明",
            before: original?.description,
            after: candidate.description,
          },
        ]
      : []),
    ...Object.entries(candidate.properties).flatMap(([key, after]) => {
      const canonical = canonicalProperty(key);
      if (
        (["hermes_name", "foaf_name", "foaf:name"].includes(canonical) &&
          after === candidate.name) ||
        (canonical === "hermes_description" &&
          after === candidate.description) ||
        (key === "uri" && after === candidate.uri)
      )
        return [];
      const properties = original?.properties ?? {};
      return [
        {
          key,
          title: propertyValueTitle(canonical),
          before: own(properties, key)
            ? properties[key]
            : properties[canonical],
          after,
        },
      ];
    }),
  ];
  return fields.map((field) => ({
    ...field,
    comparison: !original
      ? "unknown"
      : equivalent(field.before, field.after)
        ? "same"
        : "changed",
  }));
}

export function reviewOriginalUris(
  review: Review,
  existingUris: string[],
): string[] {
  const verified = new Set(existingUris);
  return [
    ...new Set(
      review.entities
        .map((entity) => entity.uri)
        .filter((uri) => !!uri && verified.has(uri)),
    ),
  ].sort();
}

/** detail has no AbortSignal argument; abort suppresses all late publications. */
export async function loadReviewOriginals(
  api: Pick<ConsoleApi, "detail">,
  uris: string[],
  signal: AbortSignal,
  publish: (uri: string, result: ReviewOriginal) => void,
): Promise<void> {
  await Promise.all(
    uris.map(async (uri) => {
      if (signal.aborted) return;
      let result: ReviewOriginal;
      try {
        const memory = await api.detail(uri);
        result =
          memory.uri === uri && memory.status === "active" && !memory.readOnly
            ? { state: "ready", memory }
            : { state: "unavailable" };
      } catch {
        result = { state: "unavailable" };
      }
      if (!signal.aborted) publish(uri, result);
    }),
  );
}

export function reviewImpactSummary(
  review: Review,
  originals: ReviewOriginals,
) {
  let newProposals = 0,
    unknown = 0,
    changedEntities = 0,
    changedFields = 0,
    compared = 0;
  for (const entity of review.entities) {
    const original = originals[entity.uri];
    if (original?.state === "outside") newProposals++;
    else if (original?.state !== "ready") unknown++;
    else {
      compared++;
      const changed = reviewFieldDifferences(entity, original.memory).filter(
        (field) => field.comparison === "changed",
      ).length;
      changedFields += changed;
      if (changed) changedEntities++;
    }
  }
  return {
    entities: review.entities.length,
    relations: review.relations.length,
    newProposals,
    unknown,
    changedEntities,
    changedFields,
    compared,
  };
}

const valueText = (value: unknown): string =>
  value === undefined
    ? "原记录未记录此字段"
    : value === null
      ? "空值"
      : value === ""
        ? "空文本"
        : typeof value === "boolean"
          ? value
            ? "是"
            : "否"
          : typeof value === "object"
            ? JSON.stringify(value)
            : String(value);
const endpoint = (value: unknown): string =>
  typeof value === "string"
    ? value
    : value &&
        typeof value === "object" &&
        "uri" in value &&
        typeof value.uri === "string"
      ? value.uri
      : "";
function Value({
  value,
  unknown = false,
}: {
  value: unknown;
  unknown?: boolean;
}) {
  if (unknown) return <span className="ri-unknown">尚未核对</span>;
  const text = valueText(value);
  return text.length > 180 ? (
    <details className="ri-long-value">
      <summary>
        {text.slice(0, 180)}… <span>展开完整值</span>
      </summary>
      <pre>{text}</pre>
    </details>
  ) : (
    <span className="ri-value">{text}</span>
  );
}
function Fields({ fields }: { fields: ReviewFieldDifference[] }) {
  return (
    <dl className="ri-fields">
      {fields.map((field) => (
        <div
          className={`ri-field ri-field-${field.comparison}`}
          key={field.key}
        >
          <dt>
            {field.title}
            <span className="ri-field-status">
              {field.comparison === "changed"
                ? "变更提议"
                : field.comparison === "same"
                  ? "未变化"
                  : "候选值"}
            </span>
          </dt>
          <dd>
            <span className="ri-column-label">原记录</span>
            <Value
              value={field.before}
              unknown={field.comparison === "unknown"}
            />
          </dd>
          <dd>
            <span className="ri-column-label">候选值</span>
            <Value value={field.after} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

export default function ReviewImpact({
  api,
  review,
  existingUris,
  status,
}: ReviewImpactProps) {
  const headingId = useId();
  const resolved = ["approved", "rejected"].includes(status ?? review.status);
  const payloadKey = JSON.stringify([
    review.id,
    review.entities,
    review.relations,
  ]);
  const verifiedUris = reviewOriginalUris(review, existingUris);
  const baseline = useRef<{ payloadKey: string; uris: string[] } | null>(null);
  // After a receipt, a newly saved candidate must not become its own original.
  const uris =
    resolved && baseline.current?.payloadKey === payloadKey
      ? baseline.current.uris
      : verifiedUris;
  const scopeKey = JSON.stringify([payloadKey, uris]);
  const [result, setResult] = useState<{
    key: string;
    values: ReviewOriginals;
  }>({ key: "", values: {} });
  const [retry, setRetry] = useState(0);
  const initial = useMemo(
    () =>
      Object.fromEntries(
        review.entities.map((entity) => [
          entity.uri,
          {
            state: uris.includes(entity.uri) ? "loading" : "outside",
          } as ReviewOriginal,
        ]),
      ),
    [scopeKey],
  );
  const originals = result.key === scopeKey ? result.values : initial;
  const summary = reviewImpactSummary(review, originals);
  useEffect(() => {
    const controller = new AbortController();
    baseline.current = { payloadKey, uris };
    setResult({ key: scopeKey, values: initial });
    void loadReviewOriginals(api, uris, controller.signal, (uri, value) => {
      setResult((current) =>
        current.key === scopeKey
          ? { ...current, values: { ...current.values, [uri]: value } }
          : current,
      );
    });
    return () => controller.abort();
  }, [api, scopeKey, retry]);
  const names = new Map(
    review.entities.map((entity) => [entity.uri, entity.name]),
  );
  for (const [uri, original] of Object.entries(originals))
    if (original.state === "ready" && !names.has(uri))
      names.set(uri, original.memory.name);
  const effectiveStatus = status ?? review.status;
  return (
    <section className="ri-impact" aria-labelledby={headingId}>
      <details className="ri-summary" open>
        <summary>
          <span>
            <strong id={headingId}>批次差异概览</strong>
            <small>
              {effectiveStatus === "approved"
                ? "已批准 · 已处理上下文"
                : effectiveStatus === "rejected"
                  ? "已拒绝 · 已处理上下文"
                  : "待核对提议"}
            </small>
          </span>
          <span className="ri-count">
            {summary.entities} 个实体 · {summary.relations} 条关系提议
          </span>
          <ChevronDown className="ri-disclosure" size={16} aria-hidden="true" />
        </summary>
        <div className="ri-body">
          <p className="ri-note">
            仅对照候选提供的字段；未提供的属性不视为删除。原记录按需读取，此摘要不代替审批前的历史快照。
          </p>
          <p className="ri-totals" role="status">
            {summary.newProposals > 0 && (
              <span>{summary.newProposals} 个新记录提议</span>
            )}
            {summary.compared > 0 && (
              <span>
                已核对 {summary.compared} 个原记录 · {summary.changedEntities}{" "}
                个含变更 · {summary.changedFields} 项字段变更
              </span>
            )}
            {summary.unknown > 0 && (
              <span>{summary.unknown} 个原记录尚待核对</span>
            )}
          </p>
          <div className="ri-entities">
            {review.entities.map((candidate, index) => {
              const original = originals[candidate.uri] ?? {
                state: "outside" as const,
              };
              const fields = reviewFieldDifferences(
                candidate,
                original.state === "ready" ? original.memory : undefined,
              );
              const changed = fields.filter(
                  (field) => field.comparison !== "same",
                ),
                same = fields.filter((field) => field.comparison === "same");
              return (
                <article
                  className="ri-entity"
                  key={`${candidate.uri}:${index}`}
                >
                  <header>
                    <strong>{candidate.name}</strong>
                    <span className="ri-badge">
                      {original.state === "outside"
                        ? "新记录提议"
                        : original.state === "ready"
                          ? "已有记录对照"
                          : "原记录待核对"}
                    </span>
                  </header>
                  <p className="ri-meta">
                    {typeLabels[candidate.type] || candidate.type}
                    {original.state === "ready" && (
                      <>
                        {" "}
                        · 原记录版本{" "}
                        {original.memory.version === undefined
                          ? "未知"
                          : `v${original.memory.version}`}
                      </>
                    )}
                  </p>
                  {original.state === "outside" && (
                    <p className="ri-note">
                      当前有效图未找到原记录，不能据此断言记录不存在。
                    </p>
                  )}
                  {original.state === "loading" && (
                    <p className="ri-note">正在读取原记录；候选内容已保留。</p>
                  )}
                  {original.state === "unavailable" && (
                    <p className="ri-warning">
                      原记录暂时无法核对；其状态与版本未知，候选内容已保留。
                    </p>
                  )}
                  {changed.length > 0 ? (
                    <Fields fields={changed} />
                  ) : (
                    <p className="ri-note">候选提供的字段与已读取版本一致。</p>
                  )}
                  {same.length > 0 && (
                    <details className="ri-unchanged">
                      <summary>查看 {same.length} 个未变化字段</summary>
                      <Fields fields={same} />
                    </details>
                  )}
                  <details className="ri-technical">
                    <summary>技术标识</summary>
                    <code>{candidate.uri || "未提供标识"}</code>
                  </details>
                </article>
              );
            })}
          </div>
          {Object.values(originals).some(
            (original) => original.state === "unavailable",
          ) && (
            <button
              type="button"
              className="button button-secondary ri-retry"
              onClick={() => setRetry((value) => value + 1)}
            >
              重新核对原记录
            </button>
          )}
          {review.relations.length > 0 && (
            <div className="ri-relations">
              <h4>关系新增提议</h4>
              <p className="ri-note">
                以下是本批次的关系提议，尚未与全部已存关系逐条比对。
              </p>
              <ul>
                {review.relations.map((relation, index) => {
                  const source = endpoint(
                      relation.from_urn ?? relation.from ?? relation.source,
                    ),
                    target = endpoint(
                      relation.to_urn ?? relation.to ?? relation.target,
                    );
                  const kind = String(
                    relation.relation_type ??
                      relation.type ??
                      relation.relation ??
                      "关联",
                  );
                  return (
                    <li key={index}>
                      <span>{names.get(source) ?? "批次外引用"}</span>
                      <span className="ri-relation-label">
                        {" "}
                        → {relationLabel({ key: kind, label: kind })} →{" "}
                      </span>
                      <span>{names.get(target) ?? "批次外引用"}</span>
                      <details className="ri-technical">
                        <summary>关系技术标识</summary>
                        <code>
                          {source || "未提供起点"} → {kind} →{" "}
                          {target || "未提供终点"}
                        </code>
                      </details>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
          <p className="ri-note ri-footer">
            依据、候选修改与整批批准/拒绝请在下方审阅操作中完成。
          </p>
        </div>
      </details>
    </section>
  );
}
