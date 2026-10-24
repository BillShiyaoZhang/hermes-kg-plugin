import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { createPortal } from "react-dom";
import { Check, RefreshCw, Save, Trash2, X } from "lucide-react";
import type { ConsoleApi } from "./api";
import type { GraphNode } from "./graphModel";
import { GRAPH_LAYER_LABELS, GRAPH_TYPE_LABELS } from "./graphModel";
import { errorMessage, formatDate, textValue } from "./lib";
import { propertyValueTitle } from "./ontologyPresentation";
import PendingFeedback from "./PendingFeedback";
import type { Memory, Review } from "./types";
import "./workspaceDialogs.css";

const FOCUSABLE =
  'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href], [tabindex]:not([tabindex="-1"])';

export function WorkspaceDialog({
  title,
  onClose,
  children,
  wide = false,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  const id = useId();
  const panel = useRef<HTMLDivElement>(null);
  const closeHandler = useRef(onClose);
  closeHandler.current = onClose;
  const previousFocus = useRef<HTMLElement | null>(null);
  if (!previousFocus.current && typeof document !== "undefined")
    previousFocus.current = document.activeElement as HTMLElement | null;
  useEffect(() => {
    const current = panel.current;
    if (!current) return;
    const background = document.getElementById("main-content");
    // The portal is outside the app. Never inert an ancestor of this dialog.
    const inertBackground =
      background && !background.contains(current) ? background : null;
    const previousInert = inertBackground?.inert ?? false;
    if (inertBackground) inertBackground.inert = true;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const initialFocus =
      current.querySelector<HTMLElement>("[autofocus]") ??
      (current.contains(document.activeElement)
        ? (document.activeElement as HTMLElement)
        : null) ??
      current.querySelector<HTMLElement>(
        'input:not(:disabled):not([type="hidden"]), textarea:not(:disabled)',
      ) ??
      current;
    initialFocus.focus({ preventScroll: true });
    const topmost = () => {
      const panels = document.querySelectorAll<HTMLElement>(
        "[data-gw-dialog-panel]",
      );
      return panels[panels.length - 1] === current;
    };
    const focusable = () =>
      Array.from(current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (element) =>
          element.tabIndex >= 0 &&
          element.getClientRects().length > 0 &&
          !element.closest("[inert], [hidden]"),
      );
    const keydown = (event: globalThis.KeyboardEvent) => {
      if (!topmost()) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        closeHandler.current();
        return;
      }
      if (event.key !== "Tab") return;
      const elements = focusable();
      const first = elements[0];
      const last = elements[elements.length - 1];
      if (!first || !last) {
        event.preventDefault();
        current.focus();
        return;
      }
      if (
        event.shiftKey &&
        (document.activeElement === first ||
          document.activeElement === current ||
          !current.contains(document.activeElement))
      ) {
        event.preventDefault();
        last.focus();
      } else if (
        !event.shiftKey &&
        (document.activeElement === last ||
          document.activeElement === current ||
          !current.contains(document.activeElement))
      ) {
        event.preventDefault();
        first.focus();
      }
    };
    const focusin = (event: FocusEvent) => {
      if (
        topmost() &&
        event.target instanceof Node &&
        !current.contains(event.target)
      )
        current.focus({ preventScroll: true });
    };
    document.addEventListener("keydown", keydown, true);
    document.addEventListener("focusin", focusin);
    return () => {
      document.removeEventListener("keydown", keydown, true);
      document.removeEventListener("focusin", focusin);
      if (inertBackground) inertBackground.inert = previousInert;
      document.body.style.overflow = previousOverflow;
      const before = previousFocus.current;
      if (before?.isConnected && !before.closest("[inert]"))
        before.focus({ preventScroll: true });
      else if (background?.isConnected && !background.inert)
        background.focus({ preventScroll: true });
    };
  }, []);
  const content = (
    <div
      className="gw-modal"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) closeHandler.current();
      }}
    >
      <div
        className={`gw-dialog${wide ? " gw-dialog-wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        tabIndex={-1}
        ref={panel}
        data-gw-dialog-panel="true"
      >
        <header className="gw-dialog-header">
          <h2 id={`${id}-title`}>{title}</h2>
          <button
            className="gw-dialog-icon"
            aria-label="关闭对话框"
            onClick={onClose}
          >
            <X size={19} aria-hidden="true" />
          </button>
        </header>
        <div className="gw-dialog-body">{children}</div>
      </div>
    </div>
  );
  return typeof document === "undefined"
    ? content
    : createPortal(content, document.body);
}

/** Candidate identity stays attached to its batch; a matching URI is not a fact. */
export function comparisonCandidate(
  node: GraphNode,
  reviews: Review[],
): { review?: Review; memory?: Memory; reference: boolean } {
  const review = reviews.find((item) => item.id === node.reviewId);
  if (!review || node.layer !== "candidate")
    return { review, reference: false };
  const prefix = `candidate:${review.id}:`;
  if (node.id.startsWith(prefix)) {
    const candidateIndex = Number(
      node.id.slice(prefix.length).split(":", 1)[0],
    );
    const indexed =
      Number.isInteger(candidateIndex) && candidateIndex >= 0
        ? review.entities[candidateIndex]
        : undefined;
    if (indexed?.uri === node.uri)
      return { review, memory: indexed, reference: false };
  }
  if (node.id.startsWith("candidate-endpoint:"))
    return { review, reference: true };
  return {
    review,
    memory: review.entities.find((entity) => entity.uri === node.uri),
    reference: false,
  };
}

type ComparisonRead = { loading: boolean; memory?: Memory; error?: unknown };

export function loadComparisonDetails(
  api: Pick<ConsoleApi, "detail">,
  nodes: GraphNode[],
  onResult: (id: string, result: ComparisonRead) => void,
): () => void {
  let active = true;
  for (const node of nodes.filter(
    (item) => item.layer === "saved" && !!item.uri,
  )) {
    Promise.resolve()
      .then(() => api.detail(node.uri!))
      .then((memory) => {
        if (active) onResult(node.id, { loading: false, memory });
      })
      .catch((error: unknown) => {
        if (active) onResult(node.id, { loading: false, error });
      });
  }
  return () => {
    active = false;
  };
}

export function CompareDialog({
  api,
  items,
  reviews,
  onClose,
}: {
  api: ConsoleApi;
  items: GraphNode[];
  reviews: Review[];
  onClose: () => void;
}) {
  const ids = JSON.stringify(
    items.map((item) => [item.id, item.layer, item.uri, item.reviewId]),
  );
  const nodes = useMemo(
    () =>
      [...new Map(items.map((item) => [item.id, item])).values()].slice(0, 6),
    [ids],
  );
  const [reads, setReads] = useState<Record<string, ComparisonRead>>({});
  const [retry, setRetry] = useState(0);
  const generation = useRef(0);
  useEffect(() => {
    const sequence = ++generation.current;
    const saved = nodes.filter((node) => node.layer === "saved" && !!node.uri);
    setReads(
      Object.fromEntries(saved.map((node) => [node.id, { loading: true }])),
    );
    // detail() currently has no AbortSignal argument. Cancel ownership of each
    // result instead; stale or unmounted reads cannot update this comparison.
    return loadComparisonDetails(api, saved, (id, result) => {
      if (generation.current === sequence)
        setReads((current) => ({ ...current, [id]: result }));
    });
  }, [api, nodes, retry]);
  const loading = Object.values(reads).some((read) => read.loading);
  return (
    <WorkspaceDialog title="并列核对" onClose={onClose} wide>
      <div className="gw-comparison-toolbar">
        <p>逐项核对属性与来源。候选、集合及已保存记录分别保留其身份。</p>
        <button
          className="gw-dialog-button"
          onClick={() => setRetry((value) => value + 1)}
          disabled={loading}
        >
          <RefreshCw size={15} aria-hidden="true" />
          刷新记录
        </button>
      </div>
      {items.length > 6 && (
        <p className="gw-dialog-notice">
          最多并列核对 6 项；当前显示所选对象的前 6 项。
        </p>
      )}
      <PendingFeedback
        active={loading}
        message="仍在读取部分记录。已返回的项目可以继续核对，也可以关闭此窗口。"
      />
      {!nodes.length ? (
        <p className="gw-dialog-empty">尚未选择可核对的对象。</p>
      ) : (
        <div
          className="gw-comparison-scroll"
          role="region"
          aria-label="并列核对项目，可横向滚动"
          tabIndex={0}
        >
          <div
            className="gw-comparison-grid"
            style={{
              gridTemplateColumns: `repeat(${nodes.length}, minmax(240px, 1fr))`,
            }}
          >
            {nodes.map((node) => {
              const collection =
                node.layer === "aggregate" || node.layer === "group";
              const candidate = comparisonCandidate(node, reviews);
              const read = reads[node.id];
              const memory =
                node.layer === "saved" ? read?.memory : candidate.memory;
              return (
                <article
                  className="gw-comparison-card"
                  key={node.id}
                  aria-label={`${node.label}的核对信息`}
                >
                  <header>
                    <span
                      className={`gw-comparison-identity gw-comparison-${node.layer}`}
                    >
                      {GRAPH_LAYER_LABELS[node.layer]}
                    </span>
                    <h3>{memory?.name || node.label}</h3>
                    <p>
                      {GRAPH_TYPE_LABELS[node.type] || node.type}
                      {memory?.version !== undefined
                        ? ` · 版本 ${memory.version}`
                        : ""}
                    </p>
                    {node.layer === "candidate" && (
                      <p className="gw-dialog-notice">
                        待审提议，尚未作为已保存事实应用。
                      </p>
                    )}
                  </header>
                  {collection ? (
                    <>
                      <section>
                        <h4>集合范围</h4>
                        <p>
                          {node.layer === "aggregate"
                            ? "当前空间的有效记录统计集合。"
                            : "当前视图中临时组合的已加载对象。"}
                        </p>
                        <dl>
                          <div>
                            <dt>包含对象</dt>
                            <dd>
                              {typeof node.count === "number"
                                ? `${node.count} 项`
                                : "数量未知"}
                            </dd>
                          </div>
                        </dl>
                        {node.description && <p>{node.description}</p>}
                        {!!node.memberIds?.length && (
                          <details>
                            <summary>成员标识</summary>
                            <ul>
                              {node.memberIds.map((id) => (
                                <li key={id}>
                                  <code>{id}</code>
                                </li>
                              ))}
                            </ul>
                          </details>
                        )}
                      </section>
                      <section>
                        <h4>属性与依据</h4>
                        <p>
                          这是集合视图，没有单条记忆的属性或来源；请展开集合后选择具体记录。
                        </p>
                      </section>
                    </>
                  ) : node.layer === "schema" ? (
                    <>
                      <section>
                        <h4>类型与规则</h4>
                        <p>{node.description || "允许的知识结构。"}</p>
                      </section>
                      <section>
                        <h4>依据</h4>
                        <p>这是本体定义，不是已发生的实例事实。</p>
                      </section>
                    </>
                  ) : node.layer === "review" ? (
                    <>
                      <section>
                        <h4>审阅批次</h4>
                        <p>
                          {candidate.review?.reason ||
                            node.description ||
                            "未提供批次说明。"}
                        </p>
                        <p>
                          {candidate.review
                            ? `${candidate.review.entities.length} 个实体提议 · ${candidate.review.relations.length} 条关系提议`
                            : "此批次不在当前已加载的待审列表中。"}
                        </p>
                      </section>
                      <section>
                        <h4>依据</h4>
                        <p>请选择批次中的具体候选核对来源。</p>
                      </section>
                    </>
                  ) : node.layer === "candidate" && !memory ? (
                    <>
                      <section>
                        <h4>属性</h4>
                        <p>
                          {candidate.reference
                            ? "这是候选关系的待核对端点引用；此批次未提供它的完整候选记录。"
                            : "当前已加载批次中没有此候选的完整记录。"}
                        </p>
                      </section>
                      <section>
                        <h4>依据</h4>
                        <p>
                          当前未提供可核对的候选依据。端点 URI
                          相同不表示提案已经成为事实。
                        </p>
                      </section>
                    </>
                  ) : node.layer === "saved" && (!read || read.loading) ? (
                    <p className="gw-dialog-empty" role="status">
                      正在读取完整记录…
                    </p>
                  ) : read?.error ? (
                    <div className="gw-dialog-error" role="alert">
                      <p>{errorMessage(read.error)}</p>
                      <button
                        className="gw-dialog-button"
                        onClick={() => setRetry((value) => value + 1)}
                      >
                        重试读取
                      </button>
                    </div>
                  ) : memory ? (
                    <>
                      {memory.description && (
                        <p className="gw-comparison-description">
                          {memory.description}
                        </p>
                      )}
                      <section>
                        <h4>属性</h4>
                        <dl className="gw-comparison-properties">
                          {Object.entries(memory.properties).map(
                            ([key, value]) => (
                              <div key={key}>
                                <dt>{propertyValueTitle(key)}</dt>
                                <dd>{textValue(value)}</dd>
                              </div>
                            ),
                          )}
                          {!Object.keys(memory.properties).length && (
                            <p>未提供附加属性。</p>
                          )}
                        </dl>
                        <details>
                          <summary>记录标识与状态</summary>
                          <code>{memory.uri}</code>
                          <p>
                            {memory.status === "archived"
                              ? "已停用"
                              : "有效记录"}
                            {memory.readOnly ? " · 只读" : ""}
                            {memory.updatedAt
                              ? ` · 更新于 ${formatDate(memory.updatedAt)}`
                              : ""}
                          </p>
                        </details>
                      </section>
                      <section>
                        <h4>
                          依据 <small>{memory.evidence.length}</small>
                        </h4>
                        {memory.evidence.length ? (
                          <ol className="gw-comparison-evidence">
                            {memory.evidence.map((evidence, index) => (
                              <li
                                key={`${evidence.sessionId || ""}:${evidence.messageId || ""}:${index}`}
                              >
                                {evidence.quote ? (
                                  <blockquote>{evidence.quote}</blockquote>
                                ) : (
                                  <p>此条依据没有引用原文。</p>
                                )}
                                <dl>
                                  {evidence.origin && (
                                    <div>
                                      <dt>来源</dt>
                                      <dd>{evidence.origin}</dd>
                                    </div>
                                  )}
                                  {evidence.sessionId && (
                                    <div>
                                      <dt>来源会话</dt>
                                      <dd>
                                        <code>{evidence.sessionId}</code>
                                      </dd>
                                    </div>
                                  )}
                                  {evidence.messageId !== undefined && (
                                    <div>
                                      <dt>消息标识</dt>
                                      <dd>{evidence.messageId}</dd>
                                    </div>
                                  )}
                                  {evidence.timestamp && (
                                    <div>
                                      <dt>来源时间</dt>
                                      <dd>{formatDate(evidence.timestamp)}</dd>
                                    </div>
                                  )}
                                  {evidence.reason && (
                                    <div>
                                      <dt>说明</dt>
                                      <dd>{evidence.reason}</dd>
                                    </div>
                                  )}
                                </dl>
                              </li>
                            ))}
                          </ol>
                        ) : (
                          <p>当前记录没有保存可核对的来源依据。</p>
                        )}
                      </section>
                    </>
                  ) : (
                    <p>此对象未提供可比较的记忆详情。</p>
                  )}
                </article>
              );
            })}
          </div>
        </div>
      )}
    </WorkspaceDialog>
  );
}

type SavedLens = "ontology" | "explore" | "review";
type JsonValue =
  string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };
export interface NamedWorkspaceView {
  id: string;
  name: string;
  createdAt: string;
  lens: SavedLens;
  state: { [key: string]: JsonValue };
  seeds: { [key: string]: JsonValue }[];
}
type ViewStorage = Pick<Storage, "getItem" | "setItem">;
const SAVED_VIEW_LIMIT = 20;
const LENS_NAMES: Record<SavedLens, string> = {
  ontology: "本体结构",
  explore: "知识探索",
  review: "审阅",
};
const forbiddenViewKey =
  /(?:token|password|secret|credential|authorization|evidence|quote|source.?text|full.?text|properties|revisions|expansions|path(?:result|snapshot)?$)/i;
const isRecord = (value: unknown): value is Record<string, unknown> =>
  !!value &&
  typeof value === "object" &&
  !Array.isArray(value) &&
  Object.getPrototypeOf(value) === Object.prototype;
const isLens = (value: unknown): value is SavedLens =>
  value === "ontology" || value === "explore" || value === "review";
export const workspaceViewStorageKey = (namespace: string) =>
  `hermes:graph:views:v1:${encodeURIComponent(namespace)}`;

function safeWorkspaceState(value: Record<string, unknown>): boolean {
  const strings = (items: unknown) =>
    Array.isArray(items) &&
    items.length <= 500 &&
    items.every((item) => typeof item === "string");
  for (const key of ["types", "relations", "selected"])
    if (key in value && !strings(value[key])) return false;
  for (const key of ["filterQuery", "search", "label", "start", "end"])
    if (key in value && typeof value[key] !== "string") return false;
  for (const key of ["primary", "edge", "focusUri", "spotlight"])
    if (key in value && value[key] !== null && typeof value[key] !== "string")
      return false;
  for (const key of ["sidebar", "inspector", "includeUnknown"])
    if (key in value && typeof value[key] !== "boolean") return false;
  for (const key of ["reviewPage", "pathIndex"])
    if (
      key in value &&
      (typeof value[key] !== "number" ||
        !Number.isInteger(value[key]) ||
        value[key] < 0)
    )
      return false;
  if (
    "width" in value &&
    (typeof value.width !== "number" ||
      !Number.isFinite(value.width) ||
      value.width < 200 ||
      value.width > 1600)
  )
    return false;
  if (
    "layout" in value &&
    !["relation", "direction", "type", "center"].includes(String(value.layout))
  )
    return false;
  if (
    "representation" in value &&
    !["graph", "list", "library"].includes(String(value.representation))
  )
    return false;
  if (
    "groups" in value &&
    (!Array.isArray(value.groups) ||
      value.groups.length > 100 ||
      !value.groups.every(
        (group) =>
          isRecord(group) &&
          typeof group.id === "string" &&
          typeof group.label === "string" &&
          strings(group.memberIds),
      ))
  )
    return false;
  if ("canvases" in value) {
    if (!isRecord(value.canvases)) return false;
    for (const key of ["relation", "direction", "type", "center"]) {
      const canvas = value.canvases[key];
      if (
        !isRecord(canvas) ||
        !isRecord(canvas.view) ||
        ![canvas.view.x, canvas.view.y, canvas.view.zoom].every(
          (item) => typeof item === "number" && Number.isFinite(item),
        ) ||
        (canvas.view.zoom as number) <= 0 ||
        !isRecord(canvas.positions) ||
        !strings(canvas.pinnedIds) ||
        !strings(canvas.hiddenIds)
      )
        return false;
      if (
        !Object.values(canvas.positions).every(
          (point) =>
            isRecord(point) &&
            typeof point.x === "number" &&
            Number.isFinite(point.x) &&
            typeof point.y === "number" &&
            Number.isFinite(point.y),
        )
      )
        return false;
    }
  }
  return true;
}

/** Persist view settings and seed identifiers, never authentication or evidence. */
export function cleanWorkspaceView(
  value: unknown,
  depth = 0,
  seen = new WeakSet<object>(),
  context = "",
): JsonValue | undefined {
  if (depth > 12) return undefined;
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "string") return value.slice(0, 4000);
  if (typeof value === "number")
    return Number.isFinite(value) ? value : undefined;
  if (typeof value !== "object") return undefined;
  if (seen.has(value)) return undefined;
  seen.add(value);
  if (Array.isArray(value)) {
    const cleaned = value
      .slice(0, 500)
      .map((item) => cleanWorkspaceView(item, depth + 1, seen))
      .filter((item): item is JsonValue => item !== undefined);
    seen.delete(value);
    return cleaned;
  }
  if (!isRecord(value)) return undefined;
  const cleaned = Object.fromEntries(
    Object.entries(value)
      .slice(0, 500)
      .filter(
        ([key]) =>
          (context === "positions" ||
            key === "expansions" ||
            key === "path" ||
            !forbiddenViewKey.test(key)) &&
          !["__proto__", "constructor", "prototype"].includes(key),
      )
      .map(([key, item]) => [
        key,
        key === "expansions"
          ? {}
          : key === "path"
            ? null
            : cleanWorkspaceView(item, depth + 1, seen, key),
      ])
      .filter((pair): pair is [string, JsonValue] => pair[1] !== undefined),
  );
  seen.delete(value);
  return cleaned;
}

export function cleanWorkspaceSeeds(
  seeds: unknown[],
  namespace: string,
): { [key: string]: JsonValue }[] {
  return seeds
    .filter(isRecord)
    .filter(
      (seed) =>
        !!namespace &&
        typeof seed.center_uri === "string" &&
        seed.center_uri.startsWith(namespace) &&
        seed.center_uri.length <= 4000 &&
        ["in", "out", "both"].includes(String(seed.direction)) &&
        Array.isArray(seed.relation_types) &&
        seed.relation_types.every((value) => typeof value === "string"),
    )
    .slice(0, 20)
    .map((seed) => ({
      center_uri: seed.center_uri as string,
      direction: seed.direction as string,
      relation_types: [
        ...new Set(
          (seed.relation_types as string[]).filter(
            (value) => value.length <= 200,
          ),
        ),
      ].slice(0, 40),
      limit:
        typeof seed.limit === "number" &&
        Number.isInteger(seed.limit) &&
        seed.limit > 0
          ? Math.min(seed.limit, 100)
          : 30,
      cursor: null,
    }));
}

export function readWorkspaceViews(
  namespace: string,
  storage: ViewStorage,
): NamedWorkspaceView[] {
  const raw = storage.getItem(workspaceViewStorageKey(namespace));
  if (!raw) return [];
  if (raw.length > 2_000_000) throw new Error("保存的视图数据超出读取上限。");
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new Error("保存的视图格式无法读取。");
  }
  if (
    !isRecord(value) ||
    value.version !== 1 ||
    value.namespace !== namespace ||
    !Array.isArray(value.views)
  )
    throw new Error("保存的视图版本或空间不匹配。");
  return value.views
    .slice(0, SAVED_VIEW_LIMIT)
    .filter(isRecord)
    .flatMap((view) => {
      if (
        typeof view.id !== "string" ||
        !view.id ||
        view.id.length > 128 ||
        typeof view.name !== "string" ||
        !view.name.trim() ||
        view.name.length > 80 ||
        typeof view.createdAt !== "string" ||
        !Number.isFinite(Date.parse(view.createdAt)) ||
        !isLens(view.lens) ||
        !isRecord(view.state) ||
        !Array.isArray(view.seeds)
      )
        return [];
      const cleaned = cleanWorkspaceView(view.state);
      if (!isRecord(cleaned) || !safeWorkspaceState(cleaned)) return [];
      return [
        {
          id: view.id,
          name: view.name,
          createdAt: view.createdAt,
          lens: view.lens,
          state: cleaned as { [key: string]: JsonValue },
          seeds: cleanWorkspaceSeeds(view.seeds, namespace),
        },
      ];
    })
    .filter(
      (view, index, views) =>
        views.findIndex((other) => other.id === view.id) === index,
    );
}

export function writeWorkspaceViews(
  namespace: string,
  views: NamedWorkspaceView[],
  storage: ViewStorage,
): void {
  if (views.length > SAVED_VIEW_LIMIT)
    throw new Error("每个空间最多保存 20 个命名视图。");
  const data = JSON.stringify({ version: 1, namespace, views });
  if (data.length > 2_000_000)
    throw new Error("视图设置较大，请减少固定对象或临时分组后再保存。");
  storage.setItem(workspaceViewStorageKey(namespace), data);
}

export function SavedViewsDialog({
  namespace,
  viewState,
  seeds,
  lens,
  onLoad,
  onClose,
}: {
  namespace: string;
  viewState: unknown;
  seeds: unknown[];
  lens: SavedLens;
  onLoad: (state: unknown, lens: string, seeds: unknown[]) => void;
  onClose: () => void;
}) {
  const [views, setViews] = useState<NamedWorkspaceView[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [unreadable, setUnreadable] = useState(false);
  useEffect(() => {
    setError("");
    setMessage("");
    setUnreadable(false);
    try {
      setViews(readWorkspaceViews(namespace, window.localStorage));
    } catch (readError) {
      setViews([]);
      setUnreadable(true);
      setError(errorMessage(readError));
    }
  }, [namespace]);
  const matching = views.find((view) => view.name === name.trim());
  const persist = (next: NamedWorkspaceView[]) => {
    try {
      writeWorkspaceViews(namespace, next, window.localStorage);
      setViews(next);
      setError("");
      return true;
    } catch {
      setError("无法保存到此浏览器。请检查本地存储权限或可用空间。");
      return false;
    }
  };
  const save = (event: FormEvent) => {
    event.preventDefault();
    setMessage("");
    const trimmed = name.trim();
    if (!trimmed || unreadable) return;
    if (!matching && views.length >= SAVED_VIEW_LIMIT) {
      setError("此空间已保存 20 个视图，请先删除一个，或输入已有名称更新它。");
      return;
    }
    const cleaned = cleanWorkspaceView(viewState);
    if (!isRecord(cleaned) || !safeWorkspaceState(cleaned)) {
      setError("当前视图设置格式不完整，暂时无法保存。");
      return;
    }
    const next: NamedWorkspaceView = {
      id:
        matching?.id ??
        globalThis.crypto?.randomUUID?.() ??
        `view-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      name: trimmed.slice(0, 80),
      createdAt: new Date().toISOString(),
      lens,
      state: cleaned as { [key: string]: JsonValue },
      seeds: cleanWorkspaceSeeds(seeds, namespace),
    };
    if (persist([next, ...views.filter((view) => view.id !== next.id)])) {
      setMessage(matching ? `已更新“${trimmed}”。` : `已保存“${trimmed}”。`);
      setName("");
    }
  };
  return (
    <WorkspaceDialog title="命名视图" onClose={onClose}>
      <p className="gw-dialog-intro">
        将当前视角、筛选、布局和起点保存在此浏览器的当前空间，方便下次继续探索。
      </p>
      <p className="gw-dialog-local-note">
        仅保存视图设置与对象标识，不保存令牌或来源全文。展开结果与路径结果在加载时重新读取；删除视图不改变知识。
      </p>
      <form className="gw-view-save" onSubmit={save}>
        <label htmlFor="gw-view-name">视图名称</label>
        <div>
          <input
            id="gw-view-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="例如：近期项目的关联"
            maxLength={80}
            disabled={unreadable}
          />
          <button
            className="gw-dialog-button gw-dialog-primary"
            type="submit"
            disabled={!name.trim() || unreadable}
          >
            <Save size={15} aria-hidden="true" />
            {matching ? "更新" : "保存"}
          </button>
        </div>
        {matching && <p>将更新同名视图的设置。</p>}
      </form>
      {error && (
        <p className="gw-dialog-error" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="gw-dialog-success" role="status">
          <Check size={16} aria-hidden="true" />
          {message}
        </p>
      )}
      <div className="gw-view-list-heading">
        <h3>此空间的视图</h3>
        <span>{views.length} / 20</span>
      </div>
      {!views.length ? (
        <p className="gw-dialog-empty">
          {unreadable
            ? "现有视图暂时无法读取；未覆盖原有数据。"
            : "尚未保存命名视图。"}
        </p>
      ) : (
        <ul className="gw-view-list">
          {views.map((view) => (
            <li key={view.id}>
              <div>
                <strong>{view.name}</strong>
                <small>
                  {LENS_NAMES[view.lens]} · {formatDate(view.createdAt)}
                </small>
              </div>
              <div>
                <button
                  className="gw-dialog-button"
                  onClick={() => {
                    onLoad(view.state, view.lens, view.seeds);
                    onClose();
                  }}
                >
                  加载
                </button>
                <button
                  className="gw-dialog-icon"
                  aria-label={`删除视图“${view.name}”`}
                  onClick={() => {
                    if (persist(views.filter((item) => item.id !== view.id)))
                      setMessage(`已删除“${view.name}”。`);
                  }}
                >
                  <Trash2 size={16} aria-hidden="true" />
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </WorkspaceDialog>
  );
}
