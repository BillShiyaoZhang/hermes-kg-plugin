import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  Search,
  X,
  RefreshCw,
  GitBranch,
  List,
  Focus,
  ArrowLeft,
  SlidersHorizontal,
} from "lucide-react";
import type { ConsoleApi } from "./api";
import { normalizeMemory } from "./api";
import type { Memory, Review, ReviewPage } from "./types";
import type {
  OntologySchema,
  OntologyEntityDefinition,
  OntologyRelationDefinition,
} from "./ontologyTypes";
import GraphCanvas from "./GraphCanvas";
import type { GraphNode, GraphEdge } from "./graphModel";
import { GRAPH_TYPE_COLORS } from "./graphModel";
import {
  workspaceGraph,
  visibleWorkspace,
  savedId,
  reviewId,
  REVIEW_PAGE_SIZE,
} from "./graphData";
import type { GraphSnapshot } from "./graphData";
import { describeTypes, relationLabel } from "./ontologyHelpers";
import { errorMessage, typeLabels, textValue } from "./lib";
import PendingFeedback from "./PendingFeedback";
import {
  ontologyPropertyTitle,
  ontologyPropertyDescription,
  propertyValueTitle,
} from "./ontologyPresentation";
import "./graphWorkspace.css";

const TYPE_KEYS = [
  "person",
  "event",
  "preference",
  "decision",
  "location",
  "concept",
];
const typeName = (type: string) =>
  typeLabels[type as keyof typeof typeLabels] ||
  (
    { relation: "关系定义", any: "任意类型", review: "审阅批次" } as Record<
      string,
      string
    >
  )[type] ||
  type;
const layerName = (layer: string) =>
  (
    ({
      schema: "类型定义",
      saved: "已保存",
      candidate: "待审候选",
      review: "待审批次",
    }) as Record<string, string>
  )[layer] || layer;

export default function GraphExplorer({
  api,
  enabled,
  refreshKey,
  refresh,
  onSelectMemory,
  onSelectReview,
  onClearSelection,
  selectedUri,
  selectedReviewId,
  selectedReviewStatus,
  detail,
  library,
}: {
  api: ConsoleApi;
  enabled: boolean;
  refreshKey: number;
  refresh: () => void;
  onSelectMemory: (memory: Memory) => void;
  onSelectReview: (review: Review) => void;
  onClearSelection: () => void;
  selectedUri?: string;
  selectedReviewId?: string;
  selectedReviewStatus?: string;
  detail: ReactNode;
  library: ReactNode;
}) {
  const [schema, setSchema] = useState<OntologySchema | null>(null),
    [snapshot, setSnapshot] = useState<GraphSnapshot | null>(null);
  const [schemaError, setSchemaError] = useState<unknown>(null),
    [graphError, setGraphError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false),
    [schemaLoading, setSchemaLoading] = useState(false);
  const [mode, setMode] = useState<"graph" | "list" | "library">("graph");
  const [input, setInput] = useState(""),
    [query, setQuery] = useState(""),
    [searchKey, setSearchKey] = useState(0);
  const [types, setTypes] = useState(TYPE_KEYS),
    [layers, setLayers] = useState(["schema", "saved", "candidate"]);
  const [membership, setMembership] = useState(false),
    [edgeKind, setEdgeKind] = useState("all");
  const [focus, setFocus] = useState<string | null>(null),
    [focusUri, setFocusUri] = useState<string | null>(null);
  const [localSelection, setLocalSelection] = useState<string | null>(null),
    [selectedEdge, setSelectedEdge] = useState<GraphEdge | null>(null);
  const [reviewPage, setReviewPage] = useState(0),
    [limit, setLimit] = useState(120),
    [loadedAt, setLoadedAt] = useState("");
  const [reviewResult, setReviewResult] = useState<ReviewPage | null>(null);
  const [reviewsLoading, setReviewsLoading] = useState(false),
    [reviewsError, setReviewsError] = useState<unknown>(null);
  const [reviewTick, setReviewTick] = useState(0);
  const reviews = reviewResult?.items ?? [];
  const reviewTotal = reviewResult?.total ?? 0;
  const reviewScope = JSON.stringify([query, reviewPage]);
  const lastReviewScope = useRef("");
  const beforeFocus = useRef<{
    types: string[];
    layers: string[];
    membership: boolean;
  } | null>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const inspector = useRef<HTMLElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const graphScope = JSON.stringify([query, types, focusUri, limit]);
  const lastScope = useRef("");
  useEffect(() => {
    if (!enabled) {
      setReviewResult(null);
      setReviewsLoading(false);
      setReviewsError(null);
      return;
    }
    const controller = new AbortController();
    if (lastReviewScope.current !== reviewScope) setReviewResult(null);
    setReviewsLoading(true);
    setReviewsError(null);
    api
      .reviewPage(
        {
          q: query,
          offset: reviewPage * REVIEW_PAGE_SIZE,
          limit: REVIEW_PAGE_SIZE,
        },
        controller.signal,
      )
      .then((value) => {
        if (controller.signal.aborted) return;
        if (value.total > 0 && value.offset >= value.total) {
          setReviewPage(Math.floor((value.total - 1) / REVIEW_PAGE_SIZE));
          return;
        }
        lastReviewScope.current = reviewScope;
        setReviewResult(value);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setReviewsError(error);
      })
      .finally(() => {
        if (!controller.signal.aborted) setReviewsLoading(false);
      });
    return () => controller.abort();
  }, [api, enabled, reviewScope, refreshKey, reviewTick, searchKey]);
  useEffect(() => {
    if (!enabled || reviewsLoading) return;
    const timer = window.setTimeout(() => setReviewTick((t) => t + 1), 30000);
    return () => window.clearTimeout(timer);
  }, [enabled, reviewsLoading, reviewTick]);
  useEffect(() => {
    if (!enabled) {
      setSchema(null);
      setSchemaError(null);
      setSchemaLoading(false);
      return;
    }
    const controller = new AbortController();
    setSchemaLoading(true);
    api
      .ontologySchema(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) {
          setSchema(value);
          setSchemaError(null);
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) setSchemaError(error);
      })
      .finally(() => {
        if (!controller.signal.aborted) setSchemaLoading(false);
      });
    return () => controller.abort();
  }, [api, enabled, refreshKey]);
  useEffect(() => {
    if (!enabled) {
      setSnapshot(null);
      setGraphError(null);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    if (lastScope.current !== graphScope) setSnapshot(null);
    setLoading(true);
    setGraphError(null);
    api
      .graph(
        {
          q: query,
          types,
          focus_uri: focusUri,
          node_limit: limit,
          edge_limit: 400,
        },
        controller.signal,
      )
      .then((value) => {
        if (!controller.signal.aborted) {
          lastScope.current = graphScope;
          setSnapshot(value);
          setLoadedAt(new Date().toLocaleTimeString());
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) setGraphError(error);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [api, enabled, graphScope, refreshKey, searchKey]); // graphScope includes every private read parameter.
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (
        (event.ctrlKey || event.metaKey) &&
        event.key.toLowerCase() === "k" &&
        mode !== "library" &&
        !event.isComposing &&
        !document.querySelector('[aria-modal="true"]')
      ) {
        event.preventDefault();
        searchInput.current?.focus();
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [mode]);
  const graph = useMemo(
    () => workspaceGraph(schema, snapshot, reviews),
    [schema, snapshot, reviews],
  );
  const visible = useMemo(
    () =>
      visibleWorkspace(graph.nodes, graph.edges, {
        types: new Set(types),
        layers: new Set(layers),
        query,
        showMembership: membership,
        focusId: focus,
      }),
    [graph, types, layers, query, membership, focus, focusUri],
  );
  const edges = visible.edges.filter(
    (e) => edgeKind === "all" || e.kind === edgeKind,
  );
  const selectedId = selectedUri
    ? savedId(selectedUri)
    : selectedReviewId
      ? graph.nodes.find((n) => n.id === localSelection)?.reviewId ===
        selectedReviewId
        ? localSelection
        : reviewId(selectedReviewId)
      : localSelection;
  const selectedNode = graph.nodes.find((node) => node.id === selectedId);
  const definition = localSelection
    ? (graph.definitions.get(localSelection) as
        OntologyEntityDefinition | OntologyRelationDefinition | undefined)
    : undefined;
  const savedEdge = snapshot?.edges.find(
    (e) => `saved-edge:${e.id}` === selectedEdge?.id,
  );
  const reviewForEdge = selectedEdge?.reviewId
    ? reviews.find((r) => r.id === selectedEdge.reviewId)
    : undefined;
  const selectionHidden =
    !!selectedId && !visible.nodes.some((n) => n.id === selectedId);
  const completedReview =
    selectedReviewStatus === "approved" || selectedReviewStatus === "rejected";
  const revealDetail = () => {
    inspector.current?.scrollIntoView({ block: "start" });
    inspector.current?.focus({ preventScroll: true });
  };
  useEffect(() => {
    if (
      (selectedId || selectedEdge) &&
      focus !== selectedId &&
      window.matchMedia("(max-width:1000px)").matches
    )
      revealDetail();
  }, [selectedId, selectedEdge?.id]);
  useEffect(() => {
    // A saved center temporarily leaves the snapshot while its neighborhood
    // loads. Only review resolution can invalidate a local candidate focus.
    if (
      focus &&
      !reviewsLoading &&
      reviewResult &&
      lastReviewScope.current === reviewScope &&
      !focusUri &&
      (focus.startsWith("candidate:") ||
        focus.startsWith("candidate-endpoint:") ||
        focus.startsWith("review:")) &&
      !graph.nodes.some((n) => n.id === focus)
    )
      setFocus(null);
  }, [focus, focusUri, graph.nodes, reviewsLoading, reviewResult, reviewScope]);
  useEffect(() => {
    const lastPage = Math.max(0, Math.ceil(reviewTotal / REVIEW_PAGE_SIZE) - 1);
    if (reviewResult && reviewPage > lastPage) setReviewPage(lastPage);
  }, [reviewTotal, reviewPage, reviewResult]);
  const selectNode = (node: GraphNode) => {
    setLocalSelection(node.id);
    setSelectedEdge(null);
    if (node.layer === "saved" && node.uri)
      onSelectMemory(
        normalizeMemory(
          snapshot?.nodes.find((n) => n.uri === node.uri) ?? { uri: node.uri },
        ),
      );
    else if (node.reviewId) {
      const review = reviews.find((r) => r.id === node.reviewId);
      if (review) onSelectReview(review);
    } else onClearSelection();
  };
  const selectEdge = (edge: GraphEdge) => {
    onClearSelection();
    setLocalSelection(null);
    setSelectedEdge(edge);
  };
  const focusNode = (node: GraphNode) => {
    if (!focus) beforeFocus.current = { types, layers, membership };
    if (!node.reviewId) {
      setQuery("");
      setInput("");
      setReviewPage(0);
    }
    setFocus(node.id);
    setFocusUri(node.layer === "saved" ? (node.uri ?? null) : null);
    // A type focus fetches its instances from the whole namespace. A record
    // focus restores all types so its neighbors are not silently excluded.
    setTypes(
      node.layer === "schema" && TYPE_KEYS.includes(node.type)
        ? [node.type]
        : TYPE_KEYS,
    );
    setLayers((current) =>
      current.includes(node.layer === "review" ? "candidate" : node.layer)
        ? current
        : [...current, node.layer === "review" ? "candidate" : node.layer],
    );
    if (node.layer === "schema") setMembership(true);
    setEdgeKind("all");
    selectNode(node);
    setMode("graph");
  };
  const reset = () => {
    beforeFocus.current = null;
    setInput("");
    setQuery("");
    setFocus(null);
    setFocusUri(null);
    setTypes(TYPE_KEYS);
    setLayers(["schema", "saved", "candidate"]);
    setEdgeKind("all");
    setMembership(false);
    setReviewPage(0);
    setLimit(120);
    setLocalSelection(null);
    setSelectedEdge(null);
    onClearSelection();
  };
  const focusLabel =
    graph.nodes.find((node) => node.id === focus)?.label ||
    (focusUri ? "所选记录" : "");
  const layerSelection = (layer: string) => {
    setFocus(null);
    setFocusUri(null);
    setLayers((current) =>
      current.includes(layer)
        ? current.filter((value) => value !== layer)
        : [...current, layer],
    );
  };
  return (
    <div className="graph-workspace">
      <header className="graph-page-header">
        <div>
          <div className="section-eyebrow">KNOWLEDGE WORKSPACE</div>
          <h1>知识图谱</h1>
          <p>从结构到记忆，再到审阅，在一张图里理解它们的联系。</p>
        </div>
        <button
          className="button button-secondary"
          onClick={refresh}
          disabled={!enabled || loading || schemaLoading}
        >
          <RefreshCw size={15} className={loading ? "spin" : ""} />
          刷新
        </button>
      </header>
      <div className="graph-mode-bar">
        <div className="graph-segments" role="group" aria-label="工作区视图">
          {(
            [
              ["graph", "节点图", GitBranch],
              ["list", "节点列表", List],
              ["library", "语义与停用", Search],
            ] as const
          ).map(([value, label, Icon]) => (
            <button
              key={value}
              aria-pressed={mode === value}
              onClick={() => setMode(value)}
            >
              <Icon size={16} />
              {label}
            </button>
          ))}
        </div>
        <span className="graph-mode-note">
          {mode === "library"
            ? "语义找回、停用与恢复"
            : "规则、事实与待审提议分层展示"}
        </span>
      </div>
      {mode !== "library" && (
        <section className="graph-tools" aria-label="图谱筛选">
          <form
            className="graph-search"
            onSubmit={(event) => {
              event.preventDefault();
              setQuery(input.trim());
              setSearchKey((k) => k + 1);
              setReviewPage(0);
              setFocus(null);
              setFocusUri(null);
            }}
          >
            <Search size={17} />
            <input
              ref={searchInput}
              aria-label="搜索图谱节点"
              value={input}
              maxLength={200}
              onChange={(e) => setInput(e.target.value)}
              placeholder="搜索节点名称、描述或标识…"
              disabled={!enabled}
            />
            {input && (
              <button
                type="button"
                aria-label="清除图谱搜索"
                onClick={() => {
                  setInput("");
                  setQuery("");
                }}
              >
                <X size={16} />
              </button>
            )}
            <button
              className="button button-primary"
              disabled={!enabled || loading}
            >
              搜索
            </button>
          </form>
          <div className="graph-filter-row">
            <fieldset className="graph-layer-controls">
              <legend>内容层</legend>
              {["schema", "saved", "candidate"].map((layer) => (
                <label key={layer}>
                  <input
                    type="checkbox"
                    checked={layers.includes(layer)}
                    onChange={() => layerSelection(layer)}
                  />
                  {layerName(layer)}
                  {layer === "candidate" && (
                    <span>
                      {reviewsError || (reviewsLoading && !reviews.length)
                        ? "—"
                        : (reviewResult?.total_pending ?? "—")}{" "}
                      批
                    </span>
                  )}
                </label>
              ))}
            </fieldset>
            <button className="button button-quiet" onClick={reset}>
              重置筛选
            </button>
          </div>
          <fieldset className="graph-type-controls">
            <legend>实体类型</legend>
            {TYPE_KEYS.map((type) => (
              <label key={type}>
                <input
                  type="checkbox"
                  checked={types.includes(type)}
                  onChange={() => {
                    setFocus(null);
                    setFocusUri(null);
                    setTypes((current) =>
                      current.includes(type)
                        ? current.filter((t) => t !== type)
                        : [...current, type],
                    );
                  }}
                />
                <i
                  style={{
                    background: GRAPH_TYPE_COLORS[type]?.stroke ?? "#52616a",
                  }}
                />
                {typeName(type)}
              </label>
            ))}
          </fieldset>
          <details className="graph-options">
            <summary>
              <SlidersHorizontal size={14} />
              关系与图例
            </summary>
            <div>
              <label>
                关系显示
                <select
                  aria-label="关系显示"
                  value={edgeKind}
                  onChange={(e) => setEdgeKind(e.target.value)}
                >
                  <option value="all">全部关系</option>
                  <option value="saved">已保存的关系</option>
                  <option value="candidate">待审关系</option>
                  <option value="definition">类型规则</option>
                  <option value="review">批次归属</option>
                </select>
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={membership}
                  onChange={(e) => setMembership(e.target.checked)}
                />
                显示实体到类型的归属线
              </label>
              <p>
                圆角方形：定义　圆形：已保存　虚线圆形：候选　菱形：审阅批次。箭头表示关系方向。定义连线仅说明允许范围。
              </p>
              {(schema?.schema_warnings.length ?? 0) > 0 && (
                <p>
                  当前定义包含 {schema!.schema_warnings.length}{" "}
                  项兼容提示；关系详情会同时说明历史声明与实际运行约束，以实际约束为准。
                </p>
              )}
            </div>
          </details>
        </section>
      )}
      {mode !== "library" && (
        <>
          <div className="graph-read-status" role="status">
            {!enabled
              ? "连接服务后查看图谱。"
              : loading
                ? "正在读取图谱…"
                : `${visible.nodes.length} 个可见节点 · ${edges.length} 条可见连线`}
            {snapshot && !loading && (
              <span>
                已加载 {snapshot.nodes.length} / {snapshot.total_nodes}{" "}
                个匹配实体{loadedAt ? ` · ${loadedAt} 更新` : ""}
              </span>
            )}
          </div>
          <PendingFeedback
            active={loading || schemaLoading || reviewsLoading}
            message="图谱读取需要一些时间。筛选和选择已保留，你可以稍后刷新。"
          />
          {(!!graphError || !!schemaError || !!reviewsError) && (
            <div role="alert" className="graph-warning">
              {graphError
                ? `数据读取失败：${errorMessage(graphError)}`
                : schemaError
                  ? `类型定义读取失败：${errorMessage(schemaError)}`
                  : `待审内容读取失败：${errorMessage(reviewsError)}`}
              <button className="button button-secondary" onClick={refresh}>
                重试读取
              </button>
              {snapshot && <span>当前保留的是上次读取结果。</span>}
            </div>
          )}
          {snapshot?.truncated_nodes && (
            <div className="graph-warning">
              当前加载 {snapshot.nodes.length} / {snapshot.total_nodes}{" "}
              个匹配实体；连线仅包含这些实体之间的关系。请搜索或聚焦以查看其他节点。
              {limit < 200 && (
                <button
                  className="button button-secondary"
                  onClick={() => setLimit(200)}
                >
                  扩展到 200 个实体
                </button>
              )}
            </div>
          )}
          {snapshot?.truncated_edges && (
            <p className="graph-warning">
              当前实体之间有 {snapshot.total_edges} 条关系，已加载前{" "}
              {snapshot.edges.length} 条；可聚焦节点缩小范围。
            </p>
          )}
          {graph.omittedCandidates > 0 && (
            <p className="graph-warning">
              当前批次的候选较多，图中仅绘制前 60
              个；选择批次可在详情中查看全部内容。
            </p>
          )}
          {focus && (
            <div className="graph-focus-banner">
              <Focus size={16} />
              <span>
                聚焦：{focusLabel} · 一跳关联
                {focusUri ? "（当前空间）" : "（已加载内容）"}
              </span>
              <button
                className="button button-secondary"
                onClick={() => {
                  setFocus(null);
                  setFocusUri(null);
                  if (beforeFocus.current) {
                    setTypes(beforeFocus.current.types);
                    setLayers(beforeFocus.current.layers);
                    setMembership(beforeFocus.current.membership);
                    beforeFocus.current = null;
                  }
                }}
              >
                返回全图
              </button>
            </div>
          )}
          {focusUri && snapshot?.focus_found === false && (
            <p className="graph-warning">
              中心记录已停用或不可见，请返回全图后重新选择。
            </p>
          )}
          {reviewTotal > REVIEW_PAGE_SIZE && layers.includes("candidate") && (
            <div
              className="graph-review-paging"
              role="group"
              aria-label="待审批次分页"
            >
              <span>
                待审批次 {reviewPage * REVIEW_PAGE_SIZE + 1}–
                {Math.min((reviewPage + 1) * REVIEW_PAGE_SIZE, reviewTotal)} /{" "}
                {reviewTotal}
              </span>
              <button
                disabled={reviewPage === 0}
                onClick={() => setReviewPage((p) => p - 1)}
              >
                上一批
              </button>
              <button
                disabled={(reviewPage + 1) * REVIEW_PAGE_SIZE >= reviewTotal}
                onClick={() => setReviewPage((p) => p + 1)}
              >
                下一批
              </button>
            </div>
          )}
        </>
      )}
      <div className="graph-content-grid">
        <div className="graph-stage" ref={stage} tabIndex={-1}>
          {mode === "library" ? (
            library
          ) : mode === "graph" ? (
            <GraphCanvas
              nodes={visible.nodes}
              edges={edges}
              layoutNodes={graph.nodes}
              selectedId={selectedId ?? undefined}
              selectedEdgeId={selectedEdge?.id}
              focusId={focus ?? undefined}
              onSelectNode={selectNode}
              onSelectEdge={selectEdge}
              onFocusNode={focusNode}
              emptyMessage={
                enabled
                  ? "没有可见节点。调整类型、内容层或搜索条件。"
                  : "连接服务后开始探索。"
              }
            />
          ) : (
            <div className="graph-node-list" role="region" aria-label="图谱节点列表">
              {visible.nodes.length ? (
                visible.nodes.map((node) => (
                  <button
                    key={node.id}
                    className={node.id === selectedId ? "selected" : ""}
                    onClick={() => selectNode(node)}
                  >
                    <span
                      className="graph-list-dot"
                      style={{
                        background:
                          GRAPH_TYPE_COLORS[node.type]?.stroke ?? "#52616a",
                      }}
                    />
                    <span>
                      <strong>{node.label}</strong>
                      <small>
                        {typeName(node.type)} · {layerName(node.layer)}
                      </small>
                    </span>
                    <span aria-hidden="true">→</span>
                  </button>
                ))
              ) : (
                <p className="graph-empty">没有符合当前条件的节点。</p>
              )}
              <details>
                <summary>关系列表（{edges.length}）</summary>
                {edges.map((edge) => (
                  <button key={edge.id} onClick={() => selectEdge(edge)}>
                    <span>
                      <strong>
                        {graph.nodes.find((n) => n.id === edge.source)?.label} →{" "}
                        {graph.nodes.find((n) => n.id === edge.target)?.label}
                      </strong>
                      <small>
                        {edge.label} ·{" "}
                        {edge.kind === "saved"
                          ? "已保存"
                          : edge.kind === "candidate"
                            ? "待审"
                            : edge.kind === "definition"
                              ? "类型规则"
                              : "归属"}
                      </small>
                    </span>
                  </button>
                ))}
              </details>
            </div>
          )}
        </div>
        <aside
          className="graph-inspector"
          ref={inspector}
          aria-label="所选内容详情"
          tabIndex={-1}
        >
          {(selectedId || selectedEdge) && (
            <div className="graph-inspector-return">
              <button
                className="button button-quiet"
                onClick={() => {
                  stage.current?.scrollIntoView({ block: "start" });
                  stage.current?.focus({ preventScroll: true });
                }}
              >
                <ArrowLeft size={15} />
                返回图谱内容
              </button>
            </div>
          )}
          {detail || (
            <section className="graph-selection-detail">
              {selectedNode || selectedEdge ? (
                <>
                  <div className="graph-inspector-heading">
                    <span>
                      {selectedEdge
                        ? "关系详情"
                        : layerName(selectedNode!.layer)}
                    </span>
                    <button
                      className="icon-button"
                      aria-label="清除所选内容"
                      onClick={() => {
                        setLocalSelection(null);
                        setSelectedEdge(null);
                        onClearSelection();
                      }}
                    >
                      <X size={18} />
                    </button>
                  </div>
                  <h2>
                    {selectedEdge ? selectedEdge.label : selectedNode!.label}
                  </h2>
                  <p>
                    {selectedEdge?.description ||
                      selectedNode?.description ||
                      "查看此节点的规则与关联。"}
                  </p>
                  {definition && (
                    <>
                      {"domain" in definition && (
                        <dl>
                          <div>
                            <dt>允许的起点</dt>
                            <dd>{describeTypes(definition.domain)}</dd>
                          </div>
                          <div>
                            <dt>允许的终点</dt>
                            <dd>{describeTypes(definition.range)}</dd>
                          </div>
                        </dl>
                      )}
                      <p className="graph-definition-note">
                        类型定义说明可以记录什么；它本身不代表某条事实已经发生。
                      </p>
                      {!definition.runtime_supported && (
                        <p className="graph-warning">
                          此定义尚未被当前运行服务支持。
                        </p>
                      )}
                      {"declared_domain" in definition &&
                        (JSON.stringify(definition.domain) !==
                          JSON.stringify(definition.declared_domain) ||
                          JSON.stringify(definition.range) !==
                            JSON.stringify(definition.declared_range)) && (
                          <p>
                            图中展示实际运行的端点约束；历史声明可能更宽。原声明：
                            {describeTypes(definition.declared_domain)} →{" "}
                            {describeTypes(definition.declared_range)}。
                          </p>
                        )}
                      <h3>属性与约束</h3>
                      {definition.properties.length === 0 && (
                        <p>
                          此定义未单独声明属性；实际记录的属性可在对应记忆或关系详情中查看。
                        </p>
                      )}
                      <dl className="graph-property-list">
                        {definition.properties.map((property) => (
                          <div key={property.key}>
                            <dt>
                              {ontologyPropertyTitle(property)}
                              {property.required_for.includes(definition.key)
                                ? property.required_condition
                                  ? " · 有条件必填"
                                  : " · 必填"
                                : ""}
                            </dt>
                            <dd>
                              {ontologyPropertyDescription(property)}
                              {property.required_condition
                                ? ` · ${property.required_condition.includes("time_precision=unknown") ? "已发生但日期未知的事件可不填发生日期；其余事件需提供。" : property.required_condition}`
                                : ""}
                              {property.allowed_values?.length
                                ? ` · 可选：${property.allowed_values.join("、")}`
                                : ""}
                              <details>
                                <summary>值类型与兼容信息</summary>
                                <p>
                                  值类型：
                                  {property.range.join("、") || "未声明"}
                                  {property.value_shape === "array"
                                    ? " · 多个值"
                                    : ""}
                                  {property.managed ? " · 系统维护" : ""}
                                  。适用：{describeTypes(property.domain)}。
                                </p>
                                {!property.runtime_supported && (
                                  <p>此属性已声明，当前写入接口尚未支持。</p>
                                )}
                                {property.aliases.length > 0 && (
                                  <p>兼容名称：{property.aliases.join("、")}</p>
                                )}
                                <code>{property.iri}</code>
                                <p>原定义：{property.description}</p>
                              </details>
                            </dd>
                          </div>
                        ))}
                      </dl>
                      <details>
                        <summary>定义标识</summary>
                        <code>{definition.iri}</code>
                        <p>原定义说明：{definition.description}</p>
                        {"aliases" in definition &&
                          definition.aliases.length > 0 && (
                            <p>兼容名称：{definition.aliases.join("、")}</p>
                          )}
                      </details>
                    </>
                  )}
                  {selectedEdge && (
                    <>
                      <p className="graph-definition-note">
                        {selectedEdge.kind === "saved"
                          ? "这是一条已保存关系。"
                          : selectedEdge.kind === "candidate"
                            ? "这是待审提议，尚未确认生效。"
                            : selectedEdge.kind === "definition"
                              ? "这是允许的类型范围，不是实例事实。"
                              : "这是归属连线。"}
                      </p>
                      <div className="graph-endpoints">
                        {[selectedEdge.source, selectedEdge.target].map(
                          (id, index) => {
                            const node = graph.nodes.find((n) => n.id === id);
                            return node ? (
                              <button
                                className="button button-secondary"
                                key={`${index}:${id}`}
                                onClick={() => selectNode(node)}
                              >
                                {index === 0 ? "起点" : "终点"}：{node.label}
                              </button>
                            ) : null;
                          },
                        )}
                      </div>
                      {selectedEdge.kind === "saved" &&
                        (() => {
                          const record = snapshot?.edges.find(
                            (e) => `saved-edge:${e.id}` === selectedEdge.id,
                          );
                          const def = schema?.relations.find(
                            (r) => r.key === record?.relation_type,
                          );
                          return def ? (
                            <button
                              className="button button-secondary"
                              onClick={() =>
                                selectNode(
                                  graph.nodes.find(
                                    (n) =>
                                      n.id === `schema:relation:${def.key}`,
                                  )!,
                                )
                              }
                            >
                              查看“{relationLabel(def)}”的类型定义
                            </button>
                          ) : (
                            <p>此关系未映射到当前类型定义。</p>
                          );
                        })()}
                    </>
                  )}
                  {selectedNode && (
                    <>
                      <button
                        className="button button-primary"
                        onClick={() => focusNode(selectedNode)}
                      >
                        <Focus size={15} />
                        聚焦此节点
                      </button>
                      <h3>关联内容</h3>
                      <div className="graph-neighbor-list">
                        {graph.edges
                          .filter(
                            (e) =>
                              e.source === selectedNode.id ||
                              e.target === selectedNode.id,
                          )
                          .slice(0, 60)
                          .map((edge) => (
                            <button
                              key={edge.id}
                              onClick={() => selectEdge(edge)}
                            >
                              {edge.label} ·{" "}
                              {
                                graph.nodes.find(
                                  (n) =>
                                    n.id ===
                                    (edge.source === selectedNode.id
                                      ? edge.target
                                      : edge.source),
                                )?.label
                              }
                            </button>
                          ))}
                      </div>
                    </>
                  )}
                </>
              ) : (
                <div className="graph-inspector-empty">
                  <GitBranch size={32} />
                  <h2>从一个节点开始</h2>
                  <p>
                    选择节点查看来源、属性和审阅操作；选择连线查看关系。双击节点或使用“聚焦”探索它的一跳关联。
                  </p>
                  <p>
                    可拖动空白处移动画布，也可使用缩放与适应画布按钮。使用 Tab
                    进入节点，方向键移动，Enter 选择，F 聚焦。
                  </p>
                  {mode !== "graph" && (
                    <button
                      className="button button-secondary"
                      onClick={() => setMode("graph")}
                    >
                      <ArrowLeft size={15} />
                      返回节点图
                    </button>
                  )}
                </div>
              )}
            </section>
          )}
          {selectedEdge && (
            <div className="graph-selection-detail">
              {reviewForEdge && (
                <button
                  className="button button-primary"
                  onClick={() => {
                    setSelectedEdge(null);
                    setLocalSelection(reviewId(reviewForEdge.id));
                    onSelectReview(reviewForEdge);
                  }}
                >
                  审阅所属批次
                </button>
              )}
              {savedEdge && (
                <>
                  <h3>关系属性与来源</h3>
                  {savedEdge.properties_truncated && (
                    <p>
                      这里展示关系来源预览。选择起点或终点，在其“关联关系”中展开可查看完整属性与来源。
                    </p>
                  )}
                  <dl>
                    {Object.entries(savedEdge.properties).map(
                      ([key, value]) => (
                        <div key={key}>
                          <dt>{propertyValueTitle(key)}</dt>
                          <dd>{textValue(value)}</dd>
                        </div>
                      ),
                    )}
                  </dl>
                </>
              )}
            </div>
          )}
          {completedReview ? (
            <p className="graph-warning">
              此批次已移出待审图层，处理结果仍保留在此处。
            </p>
          ) : (
            ((mode !== "library" && selectionHidden && !loading) ||
              (selectedEdge &&
                !edges.some((e) => e.id === selectedEdge.id))) && (
              <p className="graph-warning">
                所选内容未出现在当前筛选中，详情仍保留。可重置筛选或在图中聚焦。
              </p>
            )
          )}
          {detail &&
            !completedReview &&
            (selectedNode || selectedUri || selectedReviewId) && (
              <div className="graph-inspector-focus">
                <button
                  className="button button-secondary"
                  onClick={() =>
                    focusNode(
                      selectedNode ??
                        (selectedUri
                          ? {
                              id: savedId(selectedUri),
                              uri: selectedUri,
                              label: "所选记忆",
                              type: "unknown",
                              layer: "saved",
                            }
                          : {
                              id: reviewId(selectedReviewId!),
                              reviewId: selectedReviewId,
                              label: "所选批次",
                              type: "review",
                              layer: "review",
                            }),
                    )
                  }
                >
                  <Focus size={15} />
                  在图中聚焦
                </button>
              </div>
            )}
        </aside>
      </div>
    </div>
  );
}
