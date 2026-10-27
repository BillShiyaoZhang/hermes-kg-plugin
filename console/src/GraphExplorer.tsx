import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Check,
  Command,
  Download,
  Expand,
  Eye,
  Filter,
  GitBranch,
  List,
  Maximize2,
  PanelLeftClose,
  PanelRightClose,
  Pin,
  RefreshCw,
  RotateCcw,
  RotateCw,
  Save,
  Search,
  SlidersHorizontal,
  X,
} from "lucide-react";
import type { ConsoleApi } from "./api";
import { normalizeMemory } from "./api";
import type { Memory, Review, ReviewPage } from "./types";
import type { OntologyEntityPage, OntologySchema } from "./ontologyTypes";
import GraphCanvas from "./GraphCanvas";
import type { CanvasState } from "./GraphCanvas";
import type { GraphNode, GraphEdge } from "./graphModel";
import { GRAPH_LAYER_LABELS, GRAPH_TYPE_COLORS } from "./graphModel";
import { workspaceGraph, savedId, reviewId } from "./graphData";
import type { GraphSnapshot } from "./graphData";
import {
  aggregateGraph,
  boundGraph,
  emptySnapshot,
  groupGraph,
  mergeSnapshots,
  presentationMode,
  timeVisible,
  TYPE_KEYS,
  typeTitle,
} from "./graphWorkspace";
import type {
  Expansion,
  GraphLens,
  GraphOverview,
  LayoutMode,
  NeighborQuery,
  PathSnapshot,
  TemporaryGroup,
} from "./graphWorkspace";
import {
  ontologyDefinitionLabel,
  ontologyGraph,
  searchOntology,
} from "./ontologyV3";
import OntologyInspector from "./OntologyInspector";
import OntologyManager from "./OntologyManager";
import OntologyDirectory from "./OntologyDirectory";
import {
  allDirectoryTypesSelected,
  currentDirectoryRelations,
  DIRECTORY_STATE_VERSION,
  directoryDefinitions,
  directoryGraphKey,
  directoryGraphQuery,
  restoreDirectoryState,
} from "./graphDirectory";
import EventTimeline from "./EventTimeline";
import ReviewImpact from "./ReviewImpact";
import {
  WorkspaceDialog,
  CompareDialog,
  SavedViewsDialog,
} from "./WorkspaceDialogs";
import { errorMessage, textValue } from "./lib";
import { relationLabel } from "./ontologyHelpers";
import { propertyValueTitle } from "./ontologyPresentation";
import PendingFeedback from "./PendingFeedback";
import "./graphWorkspace.css";
import "./graphWorkspaceV3.css";

interface ViewState {
  types: string[];
  relations: string[];
  filterQuery: string;
  search: string;
  layout: LayoutMode;
  canvases: Record<LayoutMode, CanvasState>;
  selected: string[];
  primary: string | null;
  edge: string | null;
  focusUri: string | null;
  label: string;
  expansions: Record<string, Expansion>;
  spotlight: string | null;
  groups: TemporaryGroup[];
  path: PathSnapshot | null;
  pathIndex: number;
  representation: "graph" | "list" | "library";
  start: string;
  end: string;
  includeUnknown: boolean;
  sidebar: boolean;
  directoryVersion?: number;
  inspector: boolean;
  width: number;
  reviewPage: number;
}
type Views = Record<GraphLens, ViewState>;
type Entry = {
  snapshot: GraphSnapshot | null;
  overview: GraphOverview | null;
  initialMode: ReturnType<typeof presentationMode>;
  error?: unknown;
  snapshotStale?: boolean;
};
type Navigation = { lens: GraphLens; views: Views };
const LENSES = [
  { key: "ontology", label: "本体", icon: BookOpen },
  { key: "explore", label: "探索", icon: GitBranch },
  { key: "review", label: "审阅", icon: Check },
] as const;
const canvas = (): CanvasState => ({
  view: { x: 0, y: 0, zoom: 1 },
  positions: {},
  pinnedIds: [],
  hiddenIds: [],
});
const initialView = (lens: GraphLens): ViewState => ({
  types: [...TYPE_KEYS],
  relations: [],
  filterQuery: "",
  search: "",
  layout: lens === "ontology" ? "direction" : "relation",
  canvases: {
    relation: canvas(),
    direction: canvas(),
    type: canvas(),
    center: canvas(),
  },
  selected: [],
  primary: null,
  edge: null,
  focusUri: null,
  label: "全图",
  expansions: {},
  spotlight: null,
  groups: [],
  path: null,
  pathIndex: 0,
  representation: "graph",
  start: "",
  end: "",
  includeUnknown: true,
  sidebar: true,
  directoryVersion: DIRECTORY_STATE_VERSION,
  inspector: false,
  width: 370,
  reviewPage: 0,
});
const initialViews = (): Views => ({
  ontology: initialView("ontology"),
  explore: initialView("explore"),
  review: initialView("review"),
});
const expansionKey = (q: NeighborQuery) =>
  JSON.stringify([q.center_uri, q.direction, [...q.relation_types].sort()]);
const cleanView = (v: ViewState) => ({ ...v, expansions: {}, path: null });
function validView(value: unknown): value is ViewState {
  if (!value || typeof value !== "object") return false;
  const v = value as ViewState;
  return (
    Array.isArray(v.types) &&
    v.types.every(
      (t) => typeof t === "string" && t.length > 0 && t.length <= 64,
    ) &&
    Array.isArray(v.relations) &&
    v.relations.every((t) => typeof t === "string") &&
    typeof v.filterQuery === "string" &&
    typeof v.search === "string" &&
    typeof v.label === "string" &&
    ["relation", "direction", "type", "center"].includes(v.layout) &&
    ["graph", "list", "library"].includes(v.representation) &&
    !!v.canvases &&
    ["relation", "direction", "type", "center"].every((k) => {
      const c = v.canvases[k as LayoutMode];
      return (
        !!c &&
        !!c.view &&
        [c.view.x, c.view.y, c.view.zoom].every(Number.isFinite) &&
        c.view.zoom > 0 &&
        (!c.viewport ||
          [c.viewport.width, c.viewport.height].every(
            (value) =>
              typeof value === "number" && Number.isFinite(value) && value > 0,
          )) &&
        !!c.positions &&
        Array.isArray(c.pinnedIds) &&
        Array.isArray(c.hiddenIds)
      );
    }) &&
    Array.isArray(v.selected) &&
    Array.isArray(v.groups) &&
    typeof v.sidebar === "boolean" &&
    (v.directoryVersion === undefined ||
      v.directoryVersion === DIRECTORY_STATE_VERSION) &&
    typeof v.width === "number"
  );
}

function currentDefinitionView(
  view: ViewState,
  schema: OntologySchema | null,
): ViewState {
  if (!schema) return view;
  const types = view.types.filter((key) =>
    schema.entity_types.some(
      (item) =>
        item.key === key && item.runtime_supported && item.active !== false,
    ),
  );
  const relations = currentDirectoryRelations(view.relations, schema);
  return types.length === view.types.length &&
    relations.length === view.relations.length &&
    relations.every((key, index) => key === view.relations[index])
    ? view
    : { ...view, types, relations };
}

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
  interactionLocked: externalInteractionLocked = false,
  interactionMessage = "请先完成当前操作，或关闭当前编辑并处理未提交草稿。",
  writeEnabled = true,
  onInteractionLockChange,
  onReconnect,
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
  interactionLocked?: boolean;
  interactionMessage?: string;
  writeEnabled?: boolean;
  onInteractionLockChange?: (locked: boolean) => void;
  onReconnect?: () => void;
}) {
  const [ontologyEditing, setOntologyEditing] = useState(false);
  const [ontologyManagerOpen, setOntologyManagerOpen] = useState(false);
  const ontologyEditLock = useRef(false);
  const interactionLocked = externalInteractionLocked || ontologyEditing;
  const changeOntologyInteractionLock = useCallback(
    (locked: boolean) => {
      ontologyEditLock.current = locked;
      setOntologyEditing(locked);
      onInteractionLockChange?.(locked);
    },
    [onInteractionLockChange],
  );
  useEffect(
    () => () => {
      if (ontologyEditLock.current) onInteractionLockChange?.(false);
    },
    [onInteractionLockChange],
  );
  const [lens, setLens] = useState<GraphLens>("explore"),
    [views, setViews] = useState<Views>(initialViews);
  const v = views[lens],
    c = v.canvases[v.layout];
  const live = useRef({ lens, views });
  live.current = { lens, views };
  const [schema, setSchema] = useState<OntologySchema | null>(null),
    [schemaError, setSchemaError] = useState<unknown>(null),
    [schemaLoading, setSchemaLoading] = useState(false),
    [schemaScopeChanged, setSchemaScopeChanged] = useState(false);
  const schemaContext = useRef(schema);
  schemaContext.current = schema;
  const schemaReadGeneration = useRef(0);
  const typeKeys = useMemo(
    () =>
      schema
        ? directoryDefinitions(schema).types.map((item) => item.key)
        : [...TYPE_KEYS],
    [schema],
  );
  const previousTypeKeys = useRef<string[]>([...TYPE_KEYS]);
  const allTypesVisible = allDirectoryTypesSelected(v.types, typeKeys);
  useEffect(() => {
    if (!schema) return;
    const definitions = ontologyGraph(schema);
    const nodeIds = new Set(definitions.nodes.map((item) => item.id));
    const edgeIds = new Set(definitions.edges.map((item) => item.id));
    const before = previousTypeKeys.current;
    previousTypeKeys.current = typeKeys;
    setViews((current) => {
      let changed = false;
      const next = { ...current };
      for (const key of ["ontology", "explore", "review"] as const) {
        const selected = current[key].types;
        const all =
          selected.length === before.length &&
          before.every((type) => selected.includes(type));
        const types = all
          ? [...typeKeys]
          : selected.filter((type) => typeKeys.includes(type));
        const relations = currentDirectoryRelations(
          current[key].relations,
          schema,
        );
        if (
          types.join("\u0000") !== selected.join("\u0000") ||
          relations.join("\u0000") !== current[key].relations.join("\u0000")
        ) {
          next[key] = { ...current[key], types, relations };
          changed = true;
        }
      }
      const ontologyView = next.ontology;
      const selected = ontologyView.selected.filter((id) => nodeIds.has(id));
      const primary =
        ontologyView.primary && nodeIds.has(ontologyView.primary)
          ? ontologyView.primary
          : null;
      const edge =
        ontologyView.edge && edgeIds.has(ontologyView.edge)
          ? ontologyView.edge
          : ontologyView.edge?.startsWith("schema-relation:")
            ? (definitions.edges.find(
                (item) =>
                  item.relationType === ontologyView.edge!.split(":")[1],
              )?.id ?? null)
            : null;
      if (
        selected.length !== ontologyView.selected.length ||
        primary !== ontologyView.primary ||
        edge !== ontologyView.edge
      ) {
        next.ontology = { ...ontologyView, selected, primary, edge };
        changed = true;
      }
      return changed ? next : current;
    });
  }, [schema, typeKeys]);
  useEffect(() => {
    if (lens !== "ontology") setOntologyManagerOpen(false);
  }, [lens]);
  const applyPublishedOntology = (published: OntologySchema) => {
    schemaReadGeneration.current++;
    setSchemaLoading(false);
    setSchema((current) =>
      current &&
      (current.namespace !== published.namespace ||
        (current.version ?? 0) > (published.version ?? 0))
        ? current
        : published,
    );
    setNotice(`本体版本 ${published.version ?? "—"} 已发布。画布位置已保留。`);
  };
  const typeName = (key: string) => {
    const definition = schema?.entity_types.find((item) => item.key === key);
    return definition ? ontologyDefinitionLabel(definition) : typeTitle(key);
  };
  const relationName = (item: Parameters<typeof relationLabel>[0]) =>
    relationLabel(
      schema?.relations.find((definition) => definition.key === item.key) ??
        item,
    );
  const [data, setData] = useState<Record<string, Entry>>({}),
    [loading, setLoading] = useState(false);
  const [reviewPage, setReviewPage] = useState<ReviewPage | null>(null),
    [reviewsError, setReviewsError] = useState<unknown>(null),
    [reviewsLoading, setReviewsLoading] = useState(false),
    [reviewTick, setReviewTick] = useState(0);
  const pending = reviewPage?.items ?? [];
  const [context, setContext] = useState<GraphSnapshot | null>(null);
  const [busy, setBusy] = useState(false),
    [operationError, setOperationError] = useState<unknown>(null),
    [notice, setNotice] = useState("");
  useEffect(() => {
    if (ontologyEditing) setNotice("");
    if (!interactionLocked)
      setNotice((current) => (current === interactionMessage ? "" : current));
  }, [interactionLocked, interactionMessage, ontologyEditing]);
  const [searchOpen, setSearchOpen] = useState(false),
    [searchTerm, setSearchTerm] = useState(""),
    [searchOffset, setSearchOffset] = useState(0),
    [searchResults, setSearchResults] = useState<OntologyEntityPage | null>(
      null,
    ),
    [searchReviews, setSearchReviews] = useState<ReviewPage | null>(null),
    [searchBusy, setSearchBusy] = useState(false),
    [searchError, setSearchError] = useState<unknown>(null);
  const [members, setMembers] = useState<OntologyEntityPage | null>(null),
    [memberPage, setMemberPage] = useState(0),
    [memberBusy, setMemberBusy] = useState(false),
    [memberError, setMemberError] = useState<unknown>(null);
  const [direction, setDirection] = useState<"in" | "out" | "both">("both"),
    [expandRelation, setExpandRelation] = useState("");
  const [pathMode, setPathMode] = useState<"shortest" | "common_neighbors">(
      "shortest",
    ),
    [pathDirection, setPathDirection] = useState<"out" | "both">("both");
  const [commandsOpen, setCommandsOpen] = useState(false),
    [commandSearch, setCommandSearch] = useState("");
  const [seedRevision, setSeedRevision] = useState(0);
  const [compareIds, setCompareIds] = useState<string[] | null>(null),
    [savedOpen, setSavedOpen] = useState(false),
    [timeOpen, setTimeOpen] = useState(false),
    [groupOpen, setGroupOpen] = useState(false),
    [groupName, setGroupName] = useState("分组");
  const [command, setCommand] = useState<{
    id: number;
    type:
      | "fit"
      | "fit-selection"
      | "export-svg"
      | "export-png"
      | "undo"
      | "redo"
      | "pin"
      | "hide";
    label?: string;
  }>();
  const [, forceHistory] = useState(0);
  const host = useRef<HTMLDivElement>(null),
    stage = useRef<HTMLElement>(null),
    inspector = useRef<HTMLElement>(null),
    searchInput = useRef<HTMLInputElement>(null);
  const navigation = useRef<{ past: Navigation[]; future: Navigation[] }>({
    past: [],
    future: [],
  });
  const undo = useRef<
    Record<GraphLens, { past: ViewState[]; future: ViewState[] }>
  >({
    ontology: { past: [], future: [] },
    explore: { past: [], future: [] },
    review: { past: [], future: [] },
  });
  const interaction = useRef<ViewState | null>(null),
    operation = useRef<AbortController | null>(null),
    generation = useRef(0),
    namespace = useRef("");
  const seeds = useRef<Partial<Record<GraphLens, NeighborQuery[]>>>({});
  const restoringReview = useRef<string | null>(null);
  const query = useMemo(
    () => directoryGraphQuery(v, typeKeys, schema ?? undefined),
    [v.filterQuery, v.types, v.relations, v.focusUri, typeKeys, schema],
  );
  const key = directoryGraphKey(schema?.namespace ?? "", query, v.relations);
  const previousEntries = useRef<Partial<Record<GraphLens, Entry>>>({});
  const requestedEntry = data[key],
    base = requestedEntry ?? previousEntries.current[lens];
  const stale =
    !!base &&
    (!requestedEntry || !!requestedEntry.snapshotStale) &&
    lens !== "ontology";
  useEffect(() => {
    if (requestedEntry) previousEntries.current[lens] = requestedEntry;
  }, [requestedEntry, lens]);
  const batchCache = useRef(new Map<string, Review>());
  pending.forEach((batch) => batchCache.current.set(batch.id, batch));
  const selectedBatch =
    pending.find(
      (r) =>
        r.id === selectedReviewId ||
        v.primary === reviewId(r.id) ||
        v.primary?.startsWith(`candidate:${r.id}:`) ||
        v.primary?.startsWith(`candidate-endpoint:${r.id}:`),
    ) ??
    (selectedReviewId ? batchCache.current.get(selectedReviewId) : undefined);
  const completed =
    selectedReviewStatus === "approved" || selectedReviewStatus === "rejected";
  const update = useCallback(
    (
      patch: Partial<ViewState> | ((view: ViewState) => ViewState),
      target?: GraphLens,
    ) => {
      const targetLens = target ?? live.current.lens;
      setViews((all) => ({
        ...all,
        [targetLens]:
          typeof patch === "function"
            ? patch(all[targetLens])
            : { ...all[targetLens], ...patch },
      }));
    },
    [],
  );
  const commit = useCallback(
    (patch: Partial<ViewState> | ((view: ViewState) => ViewState)) => {
      const now = live.current;
      undo.current[now.lens].past = [
        ...undo.current[now.lens].past,
        now.views[now.lens],
      ].slice(-40);
      undo.current[now.lens].future = [];
      update(patch);
      forceHistory((t) => t + 1);
    },
    [update],
  );
  const restoreSelection = (next: ViewState) => {
    restoringReview.current = null;
    if (next.primary?.startsWith("saved:"))
      onSelectMemory(normalizeMemory({ uri: next.primary.slice(6) }));
    else {
      const batch = [...pending, ...batchCache.current.values()].find(
        (r) =>
          next.primary === reviewId(r.id) ||
          next.primary?.includes(`:${r.id}:`),
      );
      if (batch) {
        restoringReview.current = null;
        onSelectReview(batch);
      } else {
        restoringReview.current =
          next.primary?.startsWith("review:") ||
          next.primary?.startsWith("candidate:") ||
          next.primary?.startsWith("candidate-endpoint:")
            ? next.primary
            : null;
        onClearSelection();
      }
    }
  };
  const navigate = (target: GraphLens, patch: Partial<ViewState> = {}) => {
    if (interactionLocked) {
      setNotice(interactionMessage);
      return;
    }
    const now = live.current;
    navigation.current.past = [
      ...navigation.current.past,
      { lens: now.lens, views: now.views },
    ].slice(-40);
    navigation.current.future = [];
    operation.current?.abort();
    generation.current++;
    setBusy(false);
    setSearchOpen(false);
    setOperationError(null);
    const proposed = { ...now.views[target], ...patch };
    const next = currentDefinitionView(proposed, schema);
    if (next !== proposed)
      setNotice(
        "本体定义已经变化，已移除失效筛选；未恢复已删除定义或业务数据。",
      );
    setLens(target);
    setViews({ ...now.views, [target]: next });
    restoreSelection(next);
    forceHistory((t) => t + 1);
  };
  const go = (way: "back" | "forward") => {
    if (interactionLocked) {
      setNotice(interactionMessage);
      return;
    }
    const h = navigation.current,
      next = way === "back" ? h.past.pop() : h.future.shift();
    if (!next) return;
    const now = { lens: live.current.lens, views: live.current.views };
    if (way === "back") h.future.unshift(now);
    else h.past.push(now);
    operation.current?.abort();
    generation.current++;
    setBusy(false);
    const restored = Object.fromEntries(
      Object.entries(next.views).map(([key, view]) => [
        key,
        currentDefinitionView(view, schema),
      ]),
    ) as Views;
    setLens(next.lens);
    setViews(restored);
    restoreSelection(restored[next.lens]);
    if (
      Object.keys(restored).some(
        (key) => restored[key as GraphLens] !== next.views[key as GraphLens],
      )
    )
      setNotice("本体定义已经变化，已移除失效筛选；返回只恢复浏览状态。");
    setSearchOpen(false);
    forceHistory((t) => t + 1);
  };
  const undoView = (redo = false) => {
    if (interactionLocked) {
      setNotice(interactionMessage);
      return;
    }
    const now = live.current,
      h = undo.current[now.lens],
      next = redo ? h.future.shift() : h.past.pop();
    if (!next) return;
    if (redo) h.past.push(now.views[now.lens]);
    else h.future.unshift(now.views[now.lens]);
    const restored = currentDefinitionView(next, schema);
    update(restored);
    restoreSelection(restored);
    forceHistory((t) => t + 1);
  };
  const clear = () => {
    if (interactionLocked) {
      setNotice(interactionMessage);
      return;
    }
    update({ selected: [], primary: null, edge: null, spotlight: null });
    onClearSelection();
    stage.current?.focus({ preventScroll: true });
  };

  useEffect(() => {
    if (!enabled) {
      if (!ontologyEditLock.current) {
        setSchema(null);
        setData({});
      }
      setReviewPage(null);
      operation.current?.abort();
      return;
    }
    const controller = new AbortController();
    const requestGeneration = ++schemaReadGeneration.current;
    setSchemaLoading(true);
    setSchemaError(null);
    api
      .ontologySchema(controller.signal)
      .then((s) => {
        if (
          controller.signal.aborted ||
          requestGeneration !== schemaReadGeneration.current
        )
          return;
        if (
          ontologyEditLock.current &&
          schemaContext.current &&
          schemaContext.current.namespace !== s.namespace
        ) {
          setSchemaScopeChanged(true);
          setSchemaError(
            new Error(
              "服务当前空间已经改变。本体草稿仍保留，请复制需要的内容或明确放弃后再切换空间。",
            ),
          );
          return;
        }
        setSchemaScopeChanged(false);
        setSchema(s);
      })
      .catch((e) => {
        if (
          !controller.signal.aborted &&
          requestGeneration === schemaReadGeneration.current
        )
          setSchemaError(e);
      })
      .finally(() => {
        if (
          !controller.signal.aborted &&
          requestGeneration === schemaReadGeneration.current
        )
          setSchemaLoading(false);
      });
    return () => controller.abort();
  }, [api, enabled, refreshKey, ontologyEditing]);
  useEffect(() => {
    if (!enabled || lens === "ontology") return;
    const controller = new AbortController();
    setLoading(true);
    if (!query) {
      setData((all) => ({
        ...all,
        [key]: {
          snapshot: emptySnapshot(schema?.namespace),
          overview: null,
          initialMode: "partial",
        },
      }));
      setLoading(false);
      return;
    }
    Promise.allSettled([
      api.graph(query, controller.signal),
      api.graphOverview(query, controller.signal),
    ])
      .then(([s, o]) => {
        if (controller.signal.aborted) return;
        let snapshot = s.status === "fulfilled" ? s.value : null,
          overview = o.status === "fulfilled" ? o.value : null;
        if (v.relations.includes("__none__")) {
          if (snapshot)
            snapshot = {
              ...snapshot,
              edges: [],
              total_edges: 0,
              truncated_edges: false,
            };
          if (overview)
            overview = { ...overview, total_edges: 0, relations: [] };
        }
        setData((all) => ({
          ...all,
          [key]: {
            snapshot:
              snapshot ??
              all[key]?.snapshot ??
              previousEntries.current[lens]?.snapshot ??
              null,
            snapshotStale: !snapshot,
            overview,
            initialMode:
              all[key]?.initialMode ??
              presentationMode(overview, snapshot, !!v.focusUri),
            error:
              s.status === "rejected"
                ? s.reason
                : o.status === "rejected"
                  ? o.reason
                  : undefined,
          },
        }));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [api, enabled, lens, key, refreshKey]);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    setReviewsLoading(true);
    setReviewsError(null);
    api
      .reviewPage(
        {
          q: lens === "review" ? v.filterQuery : "",
          offset: v.reviewPage * 20,
          limit: 20,
        },
        controller.signal,
      )
      .then((page) => {
        if (!controller.signal.aborted) {
          if (page.total && page.offset >= page.total)
            update({ reviewPage: Math.floor((page.total - 1) / 20) });
          else setReviewPage(page);
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted) setReviewsError(e);
      })
      .finally(() => {
        if (!controller.signal.aborted) setReviewsLoading(false);
      });
    return () => controller.abort();
  }, [api, enabled, lens, v.filterQuery, v.reviewPage, reviewTick, refreshKey]);
  useEffect(() => {
    if (!enabled || reviewsLoading) return;
    const timer = setTimeout(() => setReviewTick((t) => t + 1), 30000);
    return () => clearTimeout(timer);
  }, [enabled, reviewsLoading, reviewTick]);
  useEffect(() => {
    if (
      lens === "review" &&
      restoringReview.current === v.primary &&
      selectedBatch &&
      !selectedReviewId
    ) {
      restoringReview.current = null;
      onSelectReview(selectedBatch);
    }
  }, [lens, v.primary, selectedBatch, selectedReviewId, onSelectReview]);
  useEffect(() => {
    setContext(null);
    if (!enabled || lens !== "review" || !selectedBatch) return;
    const controller = new AbortController();
    const uris = [
      ...new Set([
        ...selectedBatch.entities.map((e) => e.uri),
        ...selectedBatch.relations
          .flatMap((r) => [
            r.from_urn ?? r.from ?? r.source,
            r.to_urn ?? r.to ?? r.target,
          ])
          .filter((s): s is string => typeof s === "string"),
      ]),
    ].slice(0, 30);
    Promise.all(
      uris.map((uri) =>
        api
          .graph(
            { focus_uri: uri, node_limit: 1, edge_limit: 1 },
            controller.signal,
          )
          .catch(() => emptySnapshot()),
      ),
    ).then((results) => {
      if (!controller.signal.aborted)
        setContext(
          mergeSnapshots(
            null,
            Object.fromEntries(
              results.map((s, i) => [
                String(i),
                {
                  query: {
                    center_uri: uris[i],
                    direction: "both",
                    relation_types: [],
                  },
                  snapshot: {
                    ...s,
                    center_uri: uris[i],
                    total_neighbors: 0,
                    next_cursor: null,
                    has_more: false,
                    relation_counts: [],
                  },
                },
              ]),
            ),
          ),
        );
    });
    return () => controller.abort();
  }, [api, enabled, lens, selectedBatch?.id, refreshKey]);
  useEffect(() => {
    operation.current?.abort();
    generation.current++;
    setBusy(false);
    return () => operation.current?.abort();
  }, [key, lens, enabled]);
  useEffect(() => {
    if (!schema?.namespace || namespace.current === schema.namespace) return;
    if (namespace.current) {
      setViews(initialViews());
      setData({});
      previousEntries.current = {};
      batchCache.current.clear();
      seeds.current = {};
      navigation.current = { past: [], future: [] };
      undo.current = {
        ontology: { past: [], future: [] },
        explore: { past: [], future: [] },
        review: { past: [], future: [] },
      };
      onClearSelection();
    }
    namespace.current = schema.namespace;
    try {
      const saved = JSON.parse(
        sessionStorage.getItem(`kg-v3-session:${schema.namespace}`) ?? "null",
      );
      if (
        saved &&
        ["ontology", "explore", "review"].includes(saved.lens) &&
        saved.views &&
        Object.keys(saved.views).length === 3 &&
        Object.values(saved.views).every(validView)
      ) {
        setLens(saved.lens);
        setViews(
          Object.fromEntries(
            Object.entries(saved.views as Views).map(([key, view]) => [
              key,
              currentDefinitionView(
                restoreDirectoryState(view, key as GraphLens),
                schema,
              ),
            ]),
          ) as Views,
        );
        seeds.current = saved.seeds ?? {};
        setSeedRevision((t) => t + 1);
        restoreSelection(saved.views[saved.lens]);
      }
    } catch {
      setNotice("本地会话状态无法读取，已使用默认工作区。");
    }
  }, [schema?.namespace]);
  useEffect(() => {
    if (!schema?.namespace || namespace.current !== schema.namespace) return;
    const timer = setTimeout(() => {
      try {
        sessionStorage.setItem(
          `kg-v3-session:${schema.namespace}`,
          JSON.stringify({
            lens,
            views: Object.fromEntries(
              Object.entries(views).map(([l, view]) => [l, cleanView(view)]),
            ),
            seeds: Object.fromEntries(
              Object.entries(views).map(([l, view]) => [
                l,
                [
                  ...new Map(
                    [
                      ...(seeds.current[l as GraphLens] ?? []),
                      ...Object.values(view.expansions).map((e) => e.query),
                    ].map((query) => [
                      expansionKey(query),
                      { ...query, cursor: null },
                    ]),
                  ).values(),
                ],
              ]),
            ),
          }),
        );
      } catch {
        /* Disabled storage does not prevent live exploration. */
      }
    }, 400);
    return () => clearTimeout(timer);
  }, [schema?.namespace, views, lens]);
  useEffect(() => {
    const pendingSeeds = seeds.current[lens];
    if (!enabled || !base?.snapshot || !pendingSeeds?.length) return;
    const controller = new AbortController();
    Promise.all(
      pendingSeeds
        .slice(0, 20)
        .map((q) =>
          api
            .graphNeighbors({ ...q, cursor: null }, controller.signal)
            .then(
              (snapshot) =>
                [
                  expansionKey(q),
                  { query: { ...q, cursor: null }, snapshot },
                ] as const,
            ),
        ),
    )
      .then((items) => {
        if (!controller.signal.aborted) {
          if (seeds.current[lens] === pendingSeeds) delete seeds.current[lens];
          update({ expansions: Object.fromEntries(items) });
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted) {
          seeds.current[lens] = pendingSeeds;
          setOperationError(e);
        }
      });
    return () => controller.abort();
  }, [api, enabled, lens, !!base?.snapshot, seedRevision, refreshKey]);

  const ontology = useMemo(
    () =>
      schema
        ? ontologyGraph(schema)
        : { nodes: [], edges: [], definitions: new Map() },
    [schema],
  );
  const retained = useRef<Partial<Record<GraphLens, GraphSnapshot>>>({});
  const retainedIds = useMemo(
    () => [
      ...new Set([
        ...Object.values(v.canvases).flatMap((state) => state.pinnedIds),
        ...v.groups.flatMap((group) => group.memberIds),
      ]),
    ],
    [v.canvases, v.groups],
  );
  const merged = useMemo(
    () =>
      mergeSnapshots(
        base?.snapshot ?? null,
        v.expansions,
        retainedIds,
        retained.current[lens],
        v.path,
      ),
    [base?.snapshot, v.expansions, retainedIds, v.path, lens],
  );
  useEffect(() => {
    retained.current[lens] = merged;
  }, [merged, lens]);
  const local =
    !!v.focusUri || !!v.path || Object.keys(v.expansions).length > 0;
  const inferred = presentationMode(
    base?.overview ?? null,
    base?.snapshot ?? null,
    local,
  );
  const displayMode = stale
    ? "partial"
    : !local && base?.initialMode === "complete" && inferred === "aggregate"
      ? "partial"
      : inferred;
  const raw = useMemo(() => {
    if (lens === "ontology") {
      const matches =
        v.filterQuery && schema
          ? new Set(searchOntology(schema, v.filterQuery))
          : null;
      const matchingEdgeIds = new Set(
        ontology.edges
          .filter((e) => matches?.has(e.id))
          .flatMap((e) => [e.source, e.target]),
      );
      return {
        ...ontology,
        nodes: ontology.nodes.filter(
          (n) =>
            (!typeKeys.includes(n.type) || v.types.includes(n.type)) &&
            (!matches || matches.has(n.id) || matchingEdgeIds.has(n.id)),
        ),
        edges: ontology.edges.filter(
          (e) =>
            !matches ||
            matches.has(e.id) ||
            matches.has(e.source) ||
            matches.has(e.target),
        ),
        omittedCandidates: 0,
      };
    }
    if (lens === "explore" && !v.types.length)
      return {
        nodes: [],
        edges: [],
        definitions: new Map(),
        omittedCandidates: 0,
      };
    if (
      lens === "explore" &&
      displayMode === "aggregate" &&
      base?.overview?.stats_available
    ) {
      const graph = aggregateGraph(base.overview);
      return {
        ...graph,
        nodes: graph.nodes.map((item) => ({
          ...item,
          label: `${typeName(item.type)} · ${(item.count ?? 0).toLocaleString("zh-CN")} 条`,
        })),
        edges: graph.edges.map((item) => ({
          ...item,
          label: `${relationName({ key: item.relationType ?? "", label: item.relationType ?? "关系" })} · ${item.count ?? 0}`,
        })),
        definitions: new Map(),
        omittedCandidates: 0,
      };
    }
    const projection = workspaceGraph(
      null,
      lens === "review" ? context : merged,
      lens === "review" ? (selectedBatch ? [selectedBatch] : pending) : [],
    );
    const status = completed
      ? selectedReviewStatus === "approved"
        ? "已批准"
        : "已拒绝"
      : null;
    return {
      ...projection,
      nodes: projection.nodes
        .filter(
          (n) =>
            n.layer !== "schema" &&
            (lens !== "review" || !!selectedBatch || n.layer !== "saved"),
        )
        .filter(
          (n) =>
            ["review", "unknown"].includes(n.type) || v.types.includes(n.type),
        )
        .map((n) =>
          status && n.reviewId === selectedReviewId
            ? {
                ...n,
                label: `${n.label} · ${status}`,
                description: `此批次${status}；这里保留处理时上下文，请以当前事实详情核对最终记录。`,
              }
            : n,
        ),
      edges: projection.edges
        .filter((e) => e.kind !== "instance")
        .map((e) => {
          const label = e.relationType
            ? relationName({ key: e.relationType, label: e.label })
            : e.label;
          return {
            ...e,
            label:
              status && e.reviewId === selectedReviewId
                ? `${label} · ${status}`
                : label,
          };
        }),
    };
  }, [
    lens,
    ontology,
    schema,
    v.filterQuery,
    merged,
    context,
    displayMode,
    base?.overview,
    pending,
    selectedBatch,
    v.types,
    completed,
    selectedReviewStatus,
    selectedReviewId,
  ]);
  const timed = useMemo(
    () => timeVisible(raw.nodes, v.start, v.end, v.includeUnknown),
    [raw.nodes, v.start, v.end, v.includeUnknown],
  );
  const grouped = useMemo(
    () => groupGraph(timed, raw.edges, v.groups),
    [timed, raw.edges, v.groups],
  );
  const visible = useMemo(
    () =>
      boundGraph(
        grouped.nodes,
        grouped.edges.filter(
          (e) =>
            !v.relations.length ||
            e.kind === "review" ||
            v.relations.includes(e.relationType ?? e.description ?? ""),
        ),
        c.hiddenIds,
      ),
    [grouped, v.relations, c.hiddenIds],
  );
  const node: GraphNode | undefined =
    raw.nodes.find((n) => n.id === v.primary) ??
    visible.nodes.find((n) => n.id === v.primary) ??
    (v.primary?.startsWith("saved:")
      ? {
          id: v.primary,
          uri: v.primary.slice(6),
          label:
            searchResults?.items.find(
              (item) => item.uri === v.primary?.slice(6),
            )?.name ?? "所选记忆",
          type: "unknown",
          layer: "saved",
        }
      : undefined);
  const edge: GraphEdge | undefined = raw.edges.find((e) => e.id === v.edge);
  const definition = ontology.definitions.get(v.primary ?? v.edge ?? "");
  const selectedSaved = v.selected
    .map((id) => raw.nodes.find((n) => n.id === id))
    .filter((n): n is GraphNode => !!n && n.layer === "saved" && !!n.uri);
  const savedEdge = merged.edges.find((e) => `saved-edge:${e.id}` === v.edge);
  const showDetail =
    !!detail &&
    ((!!selectedUri && v.primary === savedId(selectedUri)) ||
      (!!selectedReviewId &&
        (v.primary === reviewId(selectedReviewId) ||
          v.primary?.includes(`:${selectedReviewId}:`) ||
          node?.reviewId === selectedReviewId)));
  const searchIds = useMemo(
    () =>
      lens === "ontology" && schema
        ? searchOntology(schema, searchTerm)
        : (searchResults?.items.map((item) => savedId(item.uri)) ?? []),
    [lens, schema, searchTerm, searchResults],
  );
  const pathIds = v.path?.paths[v.pathIndex]?.node_uris.map(savedId) ?? [];
  const description = stale
    ? "上次读取的图 · 当前范围尚未读取成功"
    : lens === "ontology"
      ? "本体规则 · 不代表已发生事实"
      : lens === "review"
        ? `审阅上下文 · ${reviewPage?.total_pending ?? "—"} 个待审批次`
        : displayMode === "aggregate"
          ? `全库聚合 · ${base?.overview?.total_nodes ?? "—"} 个实体 · ${base?.overview?.total_edges ?? "—"} 条实际关系`
          : displayMode === "complete"
            ? c.hiddenIds.length ||
              v.groups.length ||
              v.start ||
              v.end ||
              !v.includeUnknown
              ? `当前可见 ${visible.nodes.length} 节点 / ${visible.edges.length} 连线 · 已加载 ${base?.snapshot?.nodes.length ?? 0} 个实体`
              : `全量实体图 · ${base?.snapshot?.nodes.length ?? 0} 个实体 · ${base?.snapshot?.edges.length ?? 0} 条关系`
            : `已加载${local ? "局部" : "部分"}图 · ${merged.nodes.length} 个实体 · ${merged.edges.length} 条关系`;

  const selectNode = (item: GraphNode) => {
    if (interactionLocked) {
      setNotice(interactionMessage);
      return;
    }
    update({
      primary: item.id,
      edge: null,
      selected: [item.id],
      inspector: true,
    });
    setMemberPage(0);
    if (item.layer === "saved" && item.uri)
      onSelectMemory(
        normalizeMemory(
          merged.nodes.find((n) => n.uri === item.uri) ??
            context?.nodes.find((n) => n.uri === item.uri) ?? { uri: item.uri },
        ),
      );
    else if (item.reviewId) {
      const batch =
        pending.find((r) => r.id === item.reviewId) ??
        batchCache.current.get(item.reviewId);
      if (batch) onSelectReview(batch);
      else onClearSelection();
    } else onClearSelection();
  };
  const selectEdge = (item: GraphEdge) => {
    if (interactionLocked) {
      setNotice(interactionMessage);
      return;
    }
    update({ primary: null, edge: item.id, selected: [], inspector: true });
    onClearSelection();
  };
  const selectUri = (uri: string) => {
    if (interactionLocked) {
      setNotice(interactionMessage);
      return;
    }
    update({
      primary: savedId(uri),
      edge: null,
      selected: [savedId(uri)],
      inspector: true,
    });
    onSelectMemory(
      normalizeMemory(merged.nodes.find((n) => n.uri === uri) ?? { uri }),
    );
  };
  const previousExternal = useRef({
    uri: selectedUri,
    review: selectedReviewId,
  });
  useEffect(() => {
    const previous = previousExternal.current;
    if (
      selectedUri &&
      previous.uri !== selectedUri &&
      v.primary !== savedId(selectedUri)
    )
      update({
        primary: savedId(selectedUri),
        selected: [savedId(selectedUri)],
        edge: null,
        inspector: true,
      });
    else if (
      !selectedUri &&
      previous.uri &&
      v.primary === savedId(previous.uri)
    )
      update({ primary: null, selected: [] });
    if (
      selectedReviewId &&
      previous.review !== selectedReviewId &&
      !v.primary?.includes(selectedReviewId)
    )
      update({
        primary: reviewId(selectedReviewId),
        selected: [reviewId(selectedReviewId)],
        edge: null,
        inspector: true,
      });
    else if (
      !selectedReviewId &&
      previous.review &&
      v.primary?.includes(previous.review)
    )
      update({ primary: null, selected: [] });
    previousExternal.current = { uri: selectedUri, review: selectedReviewId };
  }, [selectedUri, selectedReviewId]);

  const explore = (item: GraphNode) => {
    if (item.layer === "group") {
      commit({ groups: v.groups.filter((g) => g.id !== item.id) });
      return;
    }
    if (item.layer === "aggregate") {
      navigate("explore", {
        types: [item.type],
        label: `${typeName(item.type)}集合`,
        primary: item.id,
        selected: [item.id],
        inspector: true,
        focusUri: null,
        expansions: {},
        path: null,
      });
      setMemberPage(0);
      return;
    }
    if (item.layer === "schema") {
      if (typeKeys.includes(item.type))
        navigate("explore", {
          types: [item.type],
          label: `${typeName(item.type)}实例`,
          primary: null,
          selected: [],
          focusUri: null,
          expansions: {},
          path: null,
          inspector: false,
        });
      return;
    }
    if (item.layer === "saved" && item.uri) {
      navigate("explore", {
        focusUri: item.uri,
        label: item.label,
        primary: item.id,
        selected: [item.id],
        layout: "center",
        canvases: { ...live.current.views.explore.canvases, center: canvas() },
        expansions: {},
        path: null,
        representation: "graph",
        inspector: true,
      });
      onSelectMemory(
        normalizeMemory(
          merged.nodes.find((n) => n.uri === item.uri) ?? { uri: item.uri },
        ),
      );
      return;
    }
    selectNode(item);
  };
  const expand = async (
    item: GraphNode,
    next = false,
    options?: NeighborQuery,
  ) => {
    if (item.layer !== "saved" || !item.uri) {
      explore(item);
      return;
    }
    if (!enabled || busy) return;
    const q: NeighborQuery = options
      ? { ...options }
      : {
          center_uri: item.uri,
          direction,
          relation_types: expandRelation ? [expandRelation] : [],
          limit: 30,
        };
    const id = expansionKey(q),
      old = v.expansions[id];
    q.cursor = next ? old?.snapshot.next_cursor : null;
    const controller = new AbortController();
    operation.current?.abort();
    operation.current = controller;
    const ticket = ++generation.current;
    setBusy(true);
    setOperationError(null);
    try {
      const result = await api.graphNeighbors(q, controller.signal);
      if (controller.signal.aborted || ticket !== generation.current) return;
      const snapshot =
        next && old
          ? {
              ...result,
              nodes: [
                ...new Map(
                  [...old.snapshot.nodes, ...result.nodes].map((n) => [
                    n.uri,
                    n,
                  ]),
                ).values(),
              ],
              edges: [
                ...new Map(
                  [...old.snapshot.edges, ...result.edges].map((e) => [
                    e.id,
                    e,
                  ]),
                ).values(),
              ],
            }
          : result;
      commit((view) => ({
        ...view,
        expansions: {
          ...view.expansions,
          [id]: { query: { ...q, cursor: null }, snapshot },
        },
        representation: "graph",
      }));
      setNotice(
        `已加载 ${snapshot.nodes.filter((n) => n.uri !== item.uri).length} / ${result.total_neighbors} 个匹配邻居${result.has_more ? "，可继续加载" : ""}。合并图仅表示已加载关系。`,
      );
    } catch (e) {
      if (!controller.signal.aborted) setOperationError(e);
    } finally {
      if (ticket === generation.current) setBusy(false);
    }
  };
  const collapse = (item: GraphNode) => {
    commit((view) => ({
      ...view,
      expansions: Object.fromEntries(
        Object.entries(view.expansions).filter(
          ([, e]) => e.query.center_uri !== item.uri,
        ),
      ),
    }));
    setNotice("已收起该次展开；其他展开引用或固定的节点仍保留。");
  };
  const pin = (ids: string[], pinned?: boolean) =>
    commit((view) => {
      const current = view.canvases[view.layout],
        shouldPin =
          pinned ?? !ids.every((id) => current.pinnedIds.includes(id));
      return {
        ...view,
        canvases: {
          ...view.canvases,
          [view.layout]: {
            ...current,
            pinnedIds: shouldPin
              ? [...new Set([...current.pinnedIds, ...ids])]
              : current.pinnedIds.filter((id) => !ids.includes(id)),
          },
        },
      };
    });
  const hide = (ids: string[]) => {
    commit((view) => ({
      ...view,
      canvases: {
        ...view.canvases,
        [view.layout]: {
          ...view.canvases[view.layout],
          hiddenIds: [
            ...new Set([...view.canvases[view.layout].hiddenIds, ...ids]),
          ],
        },
      },
    }));
    setNotice("对象已从当前画布隐藏。可撤销或显示隐藏对象，知识不变。");
  };
  const issue = (type: NonNullable<typeof command>["type"]) => {
    if (v.representation !== "graph") update({ representation: "graph" });
    setCommand({ id: Date.now() + Math.random(), type, label: description });
  };
  const runViewAction = (type: "undo" | "redo" | "pin" | "hide") => {
    // The canvas finalizes a settling drag before starting another transaction.
    // Its callbacks below still call the raw actions to avoid command loops.
    if (v.representation === "graph") issue(type);
    else if (type === "undo" || type === "redo") undoView(type === "redo");
    else if (type === "pin") pin(v.selected);
    else hide(v.selected);
  };
  const connect = async () => {
    if (selectedSaved.length !== 2 || busy || !enabled) return;
    const controller = new AbortController();
    operation.current?.abort();
    operation.current = controller;
    const ticket = ++generation.current;
    setBusy(true);
    setOperationError(null);
    try {
      const result = await api.graphPaths(
        {
          source_uri: selectedSaved[0].uri!,
          target_uri: selectedSaved[1].uri!,
          mode: pathMode,
          direction: pathDirection,
          relation_types: v.relations.filter((r) => r !== "__none__"),
          max_hops: 6,
          limit: 5,
        },
        controller.signal,
      );
      if (controller.signal.aborted || ticket !== generation.current) return;
      commit({ path: result, pathIndex: 0 });
      setNotice(
        result.timed_out
          ? "查询预算耗尽，返回结果可能不完整。"
          : result.paths.length
            ? `找到 ${result.paths.length} 条连接${result.truncated ? "，结果已截断" : ""}。`
            : `在当前查询预算及 ${result.max_hops} 跳范围内未找到连接${result.truncated ? "；不能据此排除其他连接" : ""}。`,
      );
    } catch (e) {
      if (!controller.signal.aborted) setOperationError(e);
    } finally {
      if (ticket === generation.current) setBusy(false);
    }
  };
  const submitSearch = (offset = 0) => {
    if (!offset) {
      navigation.current.past = [
        ...navigation.current.past,
        { lens, views },
      ].slice(-40);
      navigation.current.future = [];
    }
    setSearchTerm(v.search.trim());
    setSearchOffset(offset);
    setSearchOpen(true);
    forceHistory((t) => t + 1);
  };
  useEffect(() => {
    if (!searchOpen || !enabled || lens === "ontology") return;
    const controller = new AbortController();
    setSearchBusy(true);
    setSearchError(null);
    setSearchResults(null);
    setSearchReviews(null);
    const work =
      lens === "review"
        ? api
            .reviewPage(
              { q: searchTerm, offset: searchOffset, limit: 20 },
              controller.signal,
            )
            .then((page) => {
              if (!controller.signal.aborted) setSearchReviews(page);
            })
        : api
            .ontologyEntities(
              { q: searchTerm, offset: searchOffset, limit: 20 },
              controller.signal,
            )
            .then((page) => {
              if (!controller.signal.aborted) setSearchResults(page);
            });
    work
      .catch((e) => {
        if (!controller.signal.aborted) setSearchError(e);
      })
      .finally(() => {
        if (!controller.signal.aborted) setSearchBusy(false);
      });
    return () => controller.abort();
  }, [api, enabled, lens, searchOpen, searchTerm, searchOffset]);
  useEffect(() => {
    setMembers(null);
    if (!enabled || node?.layer !== "aggregate") return;
    const controller = new AbortController();
    setMemberBusy(true);
    setMemberError(null);
    api
      .ontologyEntities(
        {
          type: node.type,
          q: v.filterQuery,
          offset: memberPage * 20,
          limit: 20,
        },
        controller.signal,
      )
      .then((page) => {
        if (!controller.signal.aborted) setMembers(page);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setMemberError(e);
      })
      .finally(() => {
        if (!controller.signal.aborted) setMemberBusy(false);
      });
    return () => controller.abort();
  }, [api, enabled, node?.id, memberPage, v.filterQuery]);
  const commands = [
    { label: "适应全部可见节点", run: () => issue("fit") },
    { label: "适应当前选择", run: () => issue("fit-selection") },
    { label: "切换目录与筛选", run: () => update({ sidebar: !v.sidebar }) },
    { label: "切换检查器", run: () => update({ inspector: !v.inspector }) },
    {
      label: "切换图 / 列表",
      run: () =>
        update({
          representation: v.representation === "list" ? "graph" : "list",
        }),
    },
    { label: "撤销画布操作", run: () => runViewAction("undo") },
    { label: "重做画布操作", run: () => runViewAction("redo") },
    { label: "固定当前选择", run: () => runViewAction("pin") },
    { label: "隐藏当前选择", run: () => runViewAction("hide") },
    {
      label: "展开当前对象",
      run: () => {
        if (node) void expand(node);
      },
    },
    { label: "打开或保存命名视图", run: () => setSavedOpen(true) },
    { label: "导出 SVG", run: () => issue("export-svg") },
    { label: "导出 PNG", run: () => issue("export-png") },
    { label: "本体视角", run: () => navigate("ontology") },
    { label: "探索视角", run: () => navigate("explore") },
    { label: "审阅视角", run: () => navigate("review") },
  ];
  useEffect(() => {
    const keydown = (e: globalThis.KeyboardEvent) => {
      if (
        e.defaultPrevented ||
        document.querySelector('[role="dialog"][aria-modal="true"]')
      )
        return;
      if (interactionLocked) return;
      const typing = (e.target as HTMLElement)?.closest(
        "input,textarea,select,[contenteditable='true']",
      );
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k" && !typing) {
        e.preventDefault();
        setCommandsOpen((open) => !open);
        return;
      }
      if (
        e.key === "Escape" &&
        !typing &&
        !commandsOpen &&
        !savedOpen &&
        !compareIds &&
        !groupOpen
      ) {
        if (searchOpen) {
          setSearchOpen(false);
          searchInput.current?.focus();
        } else if (host.current?.contains(e.target as Node)) {
          if (v.spotlight) update({ spotlight: null });
          else if (v.path) commit({ path: null });
          else clear();
        }
        return;
      }
      if (typing) return;
      if (e.key === "/") {
        e.preventDefault();
        searchInput.current?.focus();
      }
      if (
        (e.ctrlKey || e.metaKey) &&
        e.key.toLowerCase() === "z" &&
        stage.current?.contains(e.target as Node)
      ) {
        e.preventDefault();
        runViewAction(e.shiftKey ? "redo" : "undo");
      }
    };
    document.addEventListener("keydown", keydown);
    return () => document.removeEventListener("keydown", keydown);
  });
  useEffect(() => {
    if (!v.inspector || !matchMedia("(max-width:767px)").matches) return;
    const element = inspector.current;
    if (!element) return;
    const previous = document.activeElement as HTMLElement | null;
    element.focus({ preventScroll: true });
    const keydown = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") {
        if (ontologyEditLock.current) return;
        e.preventDefault();
        e.stopPropagation();
        update({ inspector: false });
      }
      if (e.key === "Tab") {
        const controls = Array.from(
          element.querySelectorAll<HTMLElement>(
            "button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),summary,[tabindex='0']",
          ),
        ).filter((el) => el.getClientRects().length);
        const first = controls[0],
          last = controls[controls.length - 1];
        if (
          e.shiftKey &&
          (document.activeElement === first ||
            document.activeElement === element)
        ) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    element.addEventListener("keydown", keydown);
    return () => {
      element.removeEventListener("keydown", keydown);
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    };
  }, [v.inspector, lens]);

  return (
    <div
      ref={host}
      className={`graph-workspace-v3${v.sidebar ? " has-directory" : ""}${v.inspector ? " has-inspector" : ""}`}
      style={{ "--inspector-width": `${v.width}px` } as CSSProperties}
      onClickCapture={(event) => {
        if (
          interactionLocked &&
          !(event.target as Element).closest('.gw-inspector,[role="dialog"]')
        ) {
          event.preventDefault();
          event.stopPropagation();
          setNotice(interactionMessage);
        }
      }}
      onPointerDownCapture={(event) => {
        if (
          interactionLocked &&
          !(event.target as Element).closest('.gw-inspector,[role="dialog"]')
        ) {
          event.preventDefault();
          event.stopPropagation();
        }
      }}
    >
      <header className="gw-topbar" inert={interactionLocked || undefined}>
        <h1 className="sr-only">知识图谱工作区</h1>
        <div className="gw-lenses" role="group" aria-label="图谱工作视角">
          {LENSES.map(({ key: which, label, icon: Icon }) => (
            <button
              key={which}
              aria-pressed={lens === which}
              onClick={() => navigate(which)}
            >
              <Icon size={16} />
              {label}
              {which === "review" && (
                <span className="gw-count">
                  {reviewPage?.total_pending ?? "—"}
                </span>
              )}
            </button>
          ))}
        </div>
        <form
          className="gw-search"
          onSubmit={(e) => {
            e.preventDefault();
            submitSearch();
          }}
        >
          <Search size={16} />
          <input
            ref={searchInput}
            aria-label={`搜索${LENSES.find((l) => l.key === lens)?.label}`}
            value={v.search}
            maxLength={200}
            placeholder={
              lens === "ontology"
                ? "类型、关系、属性或 IRI…"
                : "搜索并定位知识…"
            }
            onChange={(e) => update({ search: e.target.value })}
            disabled={!enabled}
          />
          <button disabled={!enabled} aria-label="提交定位搜索" type="submit">
            ↵
          </button>
          <button
            type="button"
            aria-label="打开命令菜单"
            title="命令菜单 Ctrl / ⌘ K"
            onClick={() => setCommandsOpen(true)}
          >
            <Command size={15} />
          </button>
        </form>
        <div className="gw-top-actions">
          <button
            className="gw-directory-toggle"
            aria-label="切换目录与筛选"
            aria-expanded={v.sidebar}
            aria-controls="gw-ontology-directory"
            title="按本体概念筛选"
            onClick={() => update({ sidebar: !v.sidebar })}
          >
            <Filter size={17} />
            <span>目录与筛选</span>
          </button>
          <select
            aria-label="图谱布局"
            value={v.layout}
            onChange={(e) => commit({ layout: e.target.value as LayoutMode })}
          >
            <option value="relation">关系布局</option>
            <option value="direction">方向布局</option>
            <option value="type">按类型布局</option>
            <option value="center">中心布局</option>
          </select>
          <button
            aria-label="全屏工作区"
            onClick={() => {
              if (document.fullscreenElement) void document.exitFullscreen();
              else
                void host.current
                  ?.requestFullscreen()
                  .catch(() =>
                    setNotice("浏览器未允许全屏，可收起侧栏扩大画布。"),
                  );
            }}
          >
            <Maximize2 size={17} />
          </button>
          <button
            aria-label="刷新图谱"
            disabled={!enabled || loading}
            onClick={refresh}
          >
            <RefreshCw size={17} className={loading ? "spin" : ""} />
          </button>
          <button
            aria-label="切换检查器"
            aria-expanded={v.inspector}
            onClick={() => update({ inspector: !v.inspector })}
          >
            <PanelRightClose size={17} />
          </button>
        </div>
      </header>
      <div className="gw-scopebar" inert={interactionLocked || undefined}>
        <nav aria-label="探索路径">
          <button
            aria-label="返回上个探索上下文"
            disabled={!navigation.current.past.length}
            onClick={() => go("back")}
          >
            <ArrowLeft size={15} />
          </button>
          <button
            aria-label="前进到下个探索上下文"
            disabled={!navigation.current.future.length}
            onClick={() => go("forward")}
          >
            <ArrowRight size={15} />
          </button>
          <button
            onClick={() =>
              navigate("explore", {
                ...initialView("explore"),
                canvases: v.canvases,
              })
            }
          >
            知识图谱
          </button>
          <span>/</span>
          <strong>
            {lens === "ontology"
              ? "本体结构"
              : lens === "review"
                ? "候选核对"
                : v.label}
          </strong>
        </nav>
        <div className="gw-representations" role="group" aria-label="呈现方式">
          {lens === "ontology" && schema && (
            <button
              aria-label="管理本体"
              aria-pressed={ontologyManagerOpen}
              onClick={() => {
                setOntologyManagerOpen(true);
                update({ inspector: true });
              }}
            >
              <SlidersHorizontal size={14} />
              管理本体
            </button>
          )}
          <button
            aria-pressed={v.representation === "graph"}
            onClick={() => update({ representation: "graph" })}
          >
            <GitBranch size={14} />图
          </button>
          <button
            aria-pressed={v.representation === "list"}
            onClick={() => update({ representation: "list" })}
          >
            <List size={14} />
            列表
          </button>
          <button
            aria-pressed={v.representation === "library"}
            onClick={() =>
              update({ representation: "library", inspector: !!detail })
            }
          >
            <Search size={14} />
            语义与停用
          </button>
        </div>
      </div>
      {(v.filterQuery ||
        !allTypesVisible ||
        v.relations.length > 0 ||
        v.start ||
        v.end) && (
        <div className="gw-chips" aria-label="当前筛选">
          {v.filterQuery && (
            <button onClick={() => navigate(lens, { filterQuery: "" })}>
              包含：{v.filterQuery}
              <X size={12} />
            </button>
          )}
          {!allTypesVisible && (
            <button onClick={() => navigate(lens, { types: [...typeKeys] })}>
              类型：{v.types.map(typeName).join("、") || "全部隐藏"}
              <X size={12} />
            </button>
          )}
          {v.relations.map((relation) => (
            <button
              key={relation}
              onClick={() =>
                navigate(lens, {
                  relations: v.relations.filter((r) => r !== relation),
                })
              }
            >
              {relation === "__none__"
                ? "全部关系隐藏"
                : relationName({ key: relation, label: relation })}
              <X size={12} />
            </button>
          ))}
          {(v.start || v.end) && (
            <button onClick={() => commit({ start: "", end: "" })}>
              已加载事件：{v.start || "不限"} → {v.end || "不限"}
              <X size={12} />
            </button>
          )}
        </div>
      )}
      <PendingFeedback
        active={loading || schemaLoading || reviewsLoading || busy}
        message="正在读取，当前镜头、选择和输入保持不变。"
      />
      {!!(schemaError || base?.error || reviewsError || operationError) && (
        <div className="gw-alert" role="alert">
          {errorMessage(
            operationError || base?.error || schemaError || reviewsError,
          )}
          <button
            onClick={() => {
              setOperationError(null);
              refresh();
            }}
          >
            重试读取
          </button>
        </div>
      )}
      {notice && (
        <div className="gw-notice" role="status">
          {notice}
          <button aria-label="关闭提示" onClick={() => setNotice("")}>
            <X size={14} />
          </button>
        </div>
      )}
      {interactionLocked && (
        <div className="gw-notice" role="status">
          当前编辑或提交进行中，探索上下文已锁定。{interactionMessage}
        </div>
      )}
      <div className="gw-body">
        {v.sidebar && (
          <aside
            id="gw-ontology-directory"
            className="gw-directory"
            aria-label="图谱目录与筛选"
            inert={interactionLocked || undefined}
          >
            <div className="gw-panel-heading">
              <strong>{lens === "review" ? "审阅队列" : "目录与筛选"}</strong>
              <button
                aria-label="收起目录"
                onClick={() => update({ sidebar: false })}
              >
                <PanelLeftClose size={16} />
              </button>
            </div>
            {lens === "review" && (
              <>
                <p className="gw-muted">
                  整批核对与处理 · {reviewPage?.total_pending ?? "—"} 批待审
                </p>
                {reviewsLoading && !pending.length && (
                  <p role="status">正在读取批次…</p>
                )}
                {pending.map((batch) => (
                  <button
                    key={batch.id}
                    className={`gw-review-item${selectedReviewId === batch.id ? " selected" : ""}`}
                    onClick={() => {
                      update({
                        primary: reviewId(batch.id),
                        selected: [reviewId(batch.id)],
                        edge: null,
                        inspector: true,
                      });
                      onSelectReview(batch);
                    }}
                  >
                    <strong>{batch.entities[0]?.name || "关系提案"}</strong>
                    <small>
                      {batch.entities.length} 个实体 · {batch.relations.length}{" "}
                      条关系
                    </small>
                    <span>{batch.reason || "查看候选与已有记录差异"}</span>
                  </button>
                ))}
                {!pending.length && !reviewsLoading && (
                  <p className="gw-empty">当前范围没有待审批次。</p>
                )}
                <div className="gw-pagination">
                  <button
                    disabled={!v.reviewPage || reviewsLoading}
                    onClick={() => update({ reviewPage: v.reviewPage - 1 })}
                  >
                    上一页
                  </button>
                  <span>
                    {v.reviewPage + 1} /{" "}
                    {Math.max(1, Math.ceil((reviewPage?.total ?? 0) / 20))}
                  </span>
                  <button
                    disabled={!reviewPage?.has_more || reviewsLoading}
                    onClick={() => update({ reviewPage: v.reviewPage + 1 })}
                  >
                    下一页
                  </button>
                </div>
              </>
            )}
            <OntologyDirectory
              schema={schema}
              types={v.types}
              relations={v.relations}
              onTypesChange={(types) => navigate(lens, { types })}
              onRelationsChange={(relations) => navigate(lens, { relations })}
            />
            <details>
              <summary>显示与工具</summary>
              <button onClick={() => setTimeOpen(!timeOpen)}>
                已加载事件时间筛选
              </button>
              <button
                onClick={() =>
                  commit((view) => ({
                    ...view,
                    canvases: {
                      ...view.canvases,
                      [view.layout]: {
                        ...view.canvases[view.layout],
                        hiddenIds: [],
                      },
                    },
                  }))
                }
              >
                显示隐藏对象（{c.hiddenIds.length}）
              </button>
              <button onClick={() => setSavedOpen(true)}>
                打开 / 保存命名视图
              </button>
              <button onClick={() => issue("export-svg")}>
                导出当前图 SVG
              </button>
              <button onClick={() => issue("export-png")}>
                导出当前图 PNG
              </button>
            </details>
            {timeOpen && (
              <EventTimeline
                nodes={raw.nodes}
                start={v.start}
                end={v.end}
                includeUnknown={v.includeUnknown}
                onChange={(patch) => commit(patch)}
              />
            )}
            <div className="gw-legend">
              <span>▭ 类型定义</span>
              <span>● 已保存实体</span>
              <span>◌ 待审候选</span>
              <span>▣ 实际记录集合</span>
              <p>位置与线长不表示权重或置信度。规则连线不表示事实。</p>
            </div>
          </aside>
        )}
        <section
          ref={stage}
          className="gw-stage"
          tabIndex={-1}
          aria-busy={loading || stale}
          inert={stale || interactionLocked || undefined}
          aria-label="知识图谱内容"
        >
          {v.representation === "library" ? (
            library
          ) : v.representation === "list" ? (
            <div className="gw-list">
              <h2>
                节点与集合 <small>{visible.nodes.length}</small>
              </h2>
              {visible.nodes.map((item) => (
                <div className="gw-list-row" key={item.id}>
                  <input
                    type="checkbox"
                    aria-label={`多选 ${item.label}`}
                    checked={v.selected.includes(item.id)}
                    onChange={() =>
                      update({
                        selected: v.selected.includes(item.id)
                          ? v.selected.filter((id) => id !== item.id)
                          : [...v.selected, item.id],
                        inspector: true,
                      })
                    }
                  />
                  <button onClick={() => selectNode(item)}>
                    <i
                      style={{
                        background:
                          GRAPH_TYPE_COLORS[item.type]?.stroke ?? "#52616a",
                      }}
                    />
                    <strong>{item.label}</strong>
                    <small>
                      {typeName(item.type)} · {GRAPH_LAYER_LABELS[item.layer]}
                    </small>
                  </button>
                  <button
                    onClick={() => explore(item)}
                    aria-label={`探索 ${item.label}`}
                  >
                    <Expand size={15} />
                  </button>
                </div>
              ))}
              <h2>
                关系 <small>{visible.edges.length}</small>
              </h2>
              {visible.edges.map((item) => (
                <button
                  className="gw-list-edge"
                  key={item.id}
                  onClick={() => selectEdge(item)}
                >
                  <strong>
                    {
                      (
                        visible.nodes.find((n) => n.id === item.source) ??
                        raw.nodes.find((n) => n.id === item.source)
                      )?.label
                    }{" "}
                    →{" "}
                    {
                      (
                        visible.nodes.find((n) => n.id === item.target) ??
                        raw.nodes.find((n) => n.id === item.target)
                      )?.label
                    }
                  </strong>
                  <span>
                    {item.label} ·{" "}
                    {item.kind === "definition"
                      ? "允许连接规则"
                      : item.kind === "candidate"
                        ? "待审提议"
                        : item.kind === "aggregate"
                          ? "实际关系聚合"
                          : "已加载关系"}
                  </span>
                </button>
              ))}
              {!visible.nodes.length && (
                <p className="gw-empty">
                  没有可见对象。检查筛选、隐藏对象或服务连接。
                </p>
              )}
            </div>
          ) : (
            <GraphCanvas
              key={`${lens}:${v.layout}:${v.focusUri ?? "overview"}`}
              nodes={visible.nodes}
              edges={visible.edges}
              layoutNodes={visible.nodes}
              selectedId={v.primary}
              selectedEdgeId={v.edge}
              focusId={v.focusUri ? savedId(v.focusUri) : null}
              workspaceKey={`${lens}:${v.layout}:${v.focusUri ?? "overview"}`}
              layoutMode={v.layout}
              state={c}
              onStateChange={(next, shouldCommit) => {
                if (shouldCommit) {
                  undo.current[lens].past = [
                    ...undo.current[lens].past,
                    interaction.current ?? v,
                  ].slice(-40);
                  undo.current[lens].future = [];
                  interaction.current = null;
                }
                const all = live.current.views;
                const changed = {
                  ...all,
                  [lens]: {
                    ...all[lens],
                    canvases: { ...all[lens].canvases, [v.layout]: next },
                  },
                };
                live.current = { ...live.current, views: changed };
                setViews(changed);
              }}
              onBeginInteraction={() => {
                interaction.current = live.current.views[live.current.lens];
              }}
              selectedIds={v.selected}
              onSelectionChange={(ids, primary) => {
                update({
                  selected: ids,
                  primary: ids.length === 1 ? ids[0] : v.primary,
                  edge: null,
                  inspector: ids.length > 1 || v.inspector,
                });
                if (ids.length === 1 && !primary) {
                  const target = raw.nodes.find((n) => n.id === ids[0]);
                  if (target) selectNode(target);
                }
              }}
              onSelectNode={selectNode}
              onSelectEdge={selectEdge}
              onFocusNode={(item) => void expand(item)}
              onClearSelection={clear}
              onPinNodes={pin}
              onHideNodes={hide}
              onGroupNodes={(ids) => {
                update({ selected: ids });
                setGroupOpen(true);
              }}
              onCompareNodes={(ids) => setCompareIds(ids)}
              spotlightId={v.spotlight}
              searchIds={[...searchIds, ...pathIds]}
              command={command}
              onUndo={() => undoView()}
              onRedo={() => undoView(true)}
              emptyMessage={
                !enabled
                  ? "连接服务后开始探索。"
                  : loading || schemaLoading
                    ? "正在读取图谱…"
                    : !v.types.length
                      ? "所有实体类型已隐藏，可在目录中恢复。"
                      : "当前范围没有可见对象。调整筛选或显示隐藏对象。"
              }
            />
          )}
          {(loading || schemaLoading || busy) && (
            <div className="gw-loading" role="status">
              <RefreshCw size={14} className="spin" />
              {busy
                ? "正在查询关系…"
                : stale
                  ? "正在切换范围，暂时保留上次画面…"
                  : "正在更新，保留当前镜头…"}
            </div>
          )}
          {v.selected.length > 1 && v.representation !== "graph" && (
            <div className="gw-selection-bar">
              <strong>已选 {v.selected.length} 项</strong>
              <button onClick={() => setCompareIds(v.selected)}>比较</button>
              <button onClick={() => pin(v.selected)}>固定</button>
              <button onClick={() => hide(v.selected)}>隐藏</button>
              <button onClick={() => setGroupOpen(true)}>分组</button>
            </div>
          )}
        </section>
        {v.inspector && (
          <>
            <div
              className="gw-resizer"
              role="separator"
              aria-label="调整检查器宽度"
              aria-orientation="vertical"
              aria-valuemin={300}
              aria-valuemax={560}
              aria-valuenow={v.width}
              tabIndex={0}
              onKeyDown={(e) => {
                if (["ArrowLeft", "ArrowRight"].includes(e.key)) {
                  e.preventDefault();
                  update({
                    width: Math.min(
                      560,
                      Math.max(
                        300,
                        v.width + (e.key === "ArrowLeft" ? 20 : -20),
                      ),
                    ),
                  });
                }
              }}
              onPointerDown={(e) => {
                const start = e.clientX,
                  width = v.width;
                e.currentTarget.setPointerCapture(e.pointerId);
                const move = (event: PointerEvent) =>
                  update({
                    width: Math.max(
                      300,
                      Math.min(560, width + start - event.clientX),
                    ),
                  });
                const end = () => {
                  window.removeEventListener("pointermove", move);
                  window.removeEventListener("pointerup", end);
                  window.removeEventListener("pointercancel", end);
                };
                window.addEventListener("pointermove", move);
                window.addEventListener("pointerup", end, { once: true });
                window.addEventListener("pointercancel", end, { once: true });
              }}
            />
            <aside
              ref={inspector}
              className="gw-inspector"
              tabIndex={-1}
              aria-label="所选内容检查器"
            >
              <div className="gw-panel-heading">
                <strong>
                  {node
                    ? GRAPH_LAYER_LABELS[node.layer]
                    : edge
                      ? "关系详情"
                      : "检查器"}
                </strong>
                <button
                  aria-label="收起检查器"
                  disabled={interactionLocked}
                  onClick={() => {
                    update({ inspector: false });
                    stage.current?.focus({ preventScroll: true });
                  }}
                >
                  <X size={17} />
                </button>
              </div>
              {lens === "review" && selectedBatch && (
                <ReviewImpact
                  api={api}
                  review={selectedBatch}
                  existingUris={context?.nodes.map((item) => item.uri) ?? []}
                  status={selectedReviewStatus}
                />
              )}
              {lens === "ontology" && ontologyManagerOpen && schema ? (
                <OntologyManager
                  schema={schema}
                  transport={{
                    validate: api.ontologyValidate,
                    publish: api.ontologyPublish,
                    read: api.ontologySchema,
                  }}
                  writeEnabled={enabled && writeEnabled && !schemaScopeChanged}
                  onPublished={applyPublishedOntology}
                  onInteractionLockChange={changeOntologyInteractionLock}
                  onReconnect={onReconnect}
                  onClose={() => {
                    setOntologyManagerOpen(false);
                    stage.current?.focus({ preventScroll: true });
                  }}
                />
              ) : showDetail ? (
                detail
              ) : definition && schema ? (
                <OntologyInspector
                  schema={schema}
                  definition={definition}
                  api={api}
                  writeEnabled={enabled && writeEnabled && !schemaScopeChanged}
                  editorTransport={{
                    validate: api.ontologyValidate,
                    publish: api.ontologyPublish,
                    read: api.ontologySchema,
                  }}
                  onInteractionLockChange={changeOntologyInteractionLock}
                  onReconnect={onReconnect}
                  onPublished={applyPublishedOntology}
                  onInstances={(type) =>
                    navigate("explore", {
                      types: [type],
                      filterQuery: "",
                      focusUri: null,
                      label: `${typeName(type)}实例`,
                      expansions: {},
                      primary: null,
                      selected: [],
                      inspector: false,
                    })
                  }
                  onRelationInstances={(relation) =>
                    navigate("explore", {
                      relations: [relation],
                      focusUri: null,
                      label: relationName({ key: relation, label: relation }),
                      expansions: {},
                      primary: null,
                      selected: [],
                      inspector: false,
                    })
                  }
                  onSelectInstance={selectUri}
                  onSelectRelation={(relation) => {
                    const match = ontology.edges.find((item) =>
                      item.id.includes(`:${relation}:`),
                    );
                    if (match) selectEdge(match);
                  }}
                />
              ) : (
                <section className="gw-detail">
                  {node || edge ? (
                    <>
                      <span className="gw-eyebrow">
                        {edge
                          ? edge.kind === "definition"
                            ? "允许连接规则"
                            : "关系"
                          : typeName(node!.type)}
                      </span>
                      <h2>{node?.label ?? edge?.label}</h2>
                      <p>{node?.description ?? edge?.description}</p>
                      {node?.layer === "aggregate" && (
                        <>
                          <p className="gw-note">
                            这是 {node.count}{" "}
                            条实际记录的集合，不是类型定义。选择成员可核对来源和关系。
                          </p>
                          <button
                            className="gw-primary"
                            onClick={() => explore(node)}
                          >
                            进入此集合
                          </button>
                          {memberBusy && <p role="status">正在读取成员…</p>}
                          {!!memberError && (
                            <p role="alert">{errorMessage(memberError)}</p>
                          )}
                          {members?.items.map((item) => (
                            <div className="gw-member" key={item.uri}>
                              <button onClick={() => selectUri(item.uri)}>
                                {item.name || "未命名实体"}
                              </button>
                              <button
                                aria-label={`探索 ${item.name}`}
                                onClick={() =>
                                  explore({
                                    id: savedId(item.uri),
                                    uri: item.uri,
                                    type: item.type,
                                    label: item.name,
                                    layer: "saved",
                                  })
                                }
                              >
                                <Expand size={14} />
                              </button>
                            </div>
                          ))}
                          {members && (
                            <div className="gw-pagination">
                              <button
                                disabled={!memberPage || memberBusy}
                                onClick={() => setMemberPage((p) => p - 1)}
                              >
                                上一页
                              </button>
                              <span>
                                {members.total ? members.offset + 1 : 0}–
                                {members.offset + members.items.length} /{" "}
                                {members.total}
                              </span>
                              <button
                                disabled={!members.has_more || memberBusy}
                                onClick={() => setMemberPage((p) => p + 1)}
                              >
                                下一页
                              </button>
                            </div>
                          )}
                        </>
                      )}
                      {node?.layer === "group" && (
                        <>
                          <p>
                            当前分组包含 {node.memberIds?.length}{" "}
                            项，不改变知识。
                          </p>
                          <button
                            onClick={() =>
                              commit({
                                groups: v.groups.filter(
                                  (g) => g.id !== node.id,
                                ),
                              })
                            }
                          >
                            解散分组
                          </button>
                        </>
                      )}
                      {edge && (
                        <>
                          <dl>
                            <div>
                              <dt>起点</dt>
                              <dd>
                                <button
                                  onClick={() => {
                                    const item = raw.nodes.find(
                                      (n) => n.id === edge.source,
                                    );
                                    if (item) selectNode(item);
                                  }}
                                >
                                  {raw.nodes.find((n) => n.id === edge.source)
                                    ?.label ?? "当前范围不可见"}
                                </button>
                              </dd>
                            </div>
                            <div>
                              <dt>终点</dt>
                              <dd>
                                <button
                                  onClick={() => {
                                    const item = raw.nodes.find(
                                      (n) => n.id === edge.target,
                                    );
                                    if (item) selectNode(item);
                                  }}
                                >
                                  {raw.nodes.find((n) => n.id === edge.target)
                                    ?.label ?? "当前范围不可见"}
                                </button>
                              </dd>
                            </div>
                          </dl>
                          {edge.kind === "aggregate" && (
                            <p className="gw-note">
                              当前完整统计范围中存在 {edge.count}{" "}
                              条此类实际关系。
                            </p>
                          )}
                          {savedEdge && (
                            <>
                              <h3>属性与来源预览</h3>
                              <p className="gw-muted">
                                完整来源可在起点或终点的关联关系中核对。
                              </p>
                              <dl>
                                {Object.entries(savedEdge.properties).map(
                                  ([prop, value]) => (
                                    <div key={prop}>
                                      <dt>{propertyValueTitle(prop)}</dt>
                                      <dd>{textValue(value)}</dd>
                                    </div>
                                  ),
                                )}
                              </dl>
                            </>
                          )}
                          {edge.reviewId && (
                            <button
                              onClick={() => {
                                const batch = pending.find(
                                  (r) => r.id === edge.reviewId,
                                );
                                if (batch) {
                                  update({
                                    primary: reviewId(batch.id),
                                    selected: [reviewId(batch.id)],
                                    edge: null,
                                  });
                                  onSelectReview(batch);
                                }
                              }}
                            >
                              核对所属批次
                            </button>
                          )}
                        </>
                      )}
                    </>
                  ) : (
                    <div className="gw-inspector-empty">
                      <GitBranch size={32} />
                      <h2>选择一个对象</h2>
                      <p>
                        单击核对详情，双击展开。图与列表共享选择，缩放不会改变数据范围。
                      </p>
                    </div>
                  )}
                </section>
              )}
              {node?.layer === "saved" && (
                <section className="gw-context-actions">
                  <h3>沿关系探索</h3>
                  <div className="gw-inline">
                    <label>
                      方向
                      <select
                        aria-label="展开方向"
                        value={direction}
                        onChange={(e) =>
                          setDirection(e.target.value as typeof direction)
                        }
                      >
                        <option value="both">出入关系</option>
                        <option value="out">出关系</option>
                        <option value="in">入关系</option>
                      </select>
                    </label>
                    <label>
                      关系
                      <select
                        aria-label="展开关系类型"
                        value={expandRelation}
                        onChange={(e) => setExpandRelation(e.target.value)}
                      >
                        <option value="">全部关系</option>
                        {schema?.relations.map((r) => (
                          <option key={r.key} value={r.key}>
                            {relationName(r)}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <div className="gw-inline">
                    <button
                      disabled={!enabled || busy}
                      onClick={() => void expand(node)}
                    >
                      <Expand size={14} />
                      展开 30 个邻居
                    </button>
                    <button onClick={() => explore(node)}>以此探索</button>
                    <button
                      onClick={() =>
                        update({
                          spotlight: v.spotlight === node.id ? null : node.id,
                        })
                      }
                    >
                      <Eye size={14} />
                      {v.spotlight ? "退出聚光" : "聚光"}
                    </button>
                  </div>
                  {Object.entries(v.expansions)
                    .filter(([, e]) => e.query.center_uri === node.uri)
                    .map(([id, e]) => (
                      <div className="gw-expansion" key={id}>
                        <p>
                          {e.snapshot.nodes.length - 1} /{" "}
                          {e.snapshot.total_neighbors} 个邻居 ·{" "}
                          {e.query.direction === "both"
                            ? "出入"
                            : e.query.direction === "out"
                              ? "出"
                              : "入"}
                          关系
                        </p>
                        {e.snapshot.relation_counts.length > 0 && (
                          <details>
                            <summary>实际关系分布</summary>
                            {e.snapshot.relation_counts.map((count, i) => (
                              <p key={i}>
                                {count.direction === "out" ? "出" : "入"} ·{" "}
                                {relationName({
                                  key: count.relation_type,
                                  label: count.relation_type,
                                })}{" "}
                                · {count.count}
                              </p>
                            ))}
                          </details>
                        )}
                        {e.snapshot.has_more && (
                          <button
                            disabled={busy || !enabled}
                            onClick={() => void expand(node, true, e.query)}
                          >
                            继续加载
                          </button>
                        )}
                        {e.snapshot.truncated_edges && (
                          <p>该页关系超过上限，已加载关系不完整。</p>
                        )}
                      </div>
                    ))}
                  {Object.values(v.expansions).some(
                    (e) => e.query.center_uri === node.uri,
                  ) && (
                    <button onClick={() => collapse(node)}>
                      收起此节点的展开
                    </button>
                  )}
                </section>
              )}
              {selectedSaved.length === 2 && (
                <section className="gw-context-actions">
                  <h3>两个实体的连接</h3>
                  <p>
                    {selectedSaved[0].label} → {selectedSaved[1].label}
                  </p>
                  <div className="gw-inline">
                    <select
                      aria-label="连接查询方式"
                      value={pathMode}
                      onChange={(e) =>
                        setPathMode(e.target.value as typeof pathMode)
                      }
                    >
                      <option value="shortest">最短路径</option>
                      <option value="common_neighbors">共同邻居</option>
                    </select>
                    <select
                      aria-label="路径方向"
                      value={pathDirection}
                      onChange={(e) =>
                        setPathDirection(e.target.value as typeof pathDirection)
                      }
                    >
                      <option value="both">忽略方向查找</option>
                      <option value="out">沿箭头方向</option>
                    </select>
                    <button
                      disabled={!enabled || busy}
                      onClick={() => void connect()}
                    >
                      查询连接
                    </button>
                  </div>
                  <p className="gw-muted">
                    当前空间有效事实 · 最多 6 跳 / 5 条 / 5
                    秒数据库预算。共同出邻居表示双方均指向该邻居。
                  </p>
                </section>
              )}
              {v.path && (
                <section className="gw-context-actions">
                  <h3>连接查询结果</h3>
                  {v.path.paths.map((path, i) => (
                    <button
                      key={i}
                      aria-pressed={v.pathIndex === i}
                      onClick={() => update({ pathIndex: i })}
                    >
                      路径 {i + 1} · {path.edge_ids.length} 条关系
                    </button>
                  ))}
                  <p>
                    {v.path.timed_out
                      ? "预算耗尽，结果可能不完整。"
                      : v.path.paths.length
                        ? `${v.path.paths.length} 条返回结果${v.path.truncated ? "（已截断）" : ""}`
                        : `在查询预算及 ${v.path.max_hops} 跳范围内未找到连接。`}
                  </p>
                  <button onClick={() => commit({ path: null })}>
                    退出路径，恢复探索
                  </button>
                </section>
              )}
              {completed && (
                <section className="gw-context-actions">
                  <p className="gw-note">
                    此批次已
                    {selectedReviewStatus === "approved" ? "批准" : "拒绝"}
                    ，结果仍保留。撤销画布不会撤销处理。
                  </p>
                  <button
                    onClick={() => {
                      const next = pending.find(
                        (r) => r.id !== selectedReviewId,
                      );
                      if (next) {
                        update({
                          primary: reviewId(next.id),
                          selected: [reviewId(next.id)],
                          edge: null,
                        });
                        onSelectReview(next);
                      } else
                        setNotice("当前页没有下一项，可刷新或切换批次页。");
                    }}
                  >
                    下一项
                  </button>
                </section>
              )}
              {v.primary &&
                !visible.nodes.some((item) => item.id === v.primary) &&
                !completed && (
                  <p className="gw-note">
                    所选内容不在当前画布，详情保留。可调整筛选、显示隐藏对象或以此探索。
                  </p>
                )}
            </aside>
          </>
        )}
      </div>
      <footer className="gw-footer" inert={interactionLocked || undefined}>
        <div role="status">
          <span className="gw-status-dot" />
          {!enabled ? "尚未连接" : loading ? "正在更新图谱…" : description}
          {(visible.omittedNodes > 0 || visible.omittedEdges > 0) && (
            <span>
              {" "}
              · 画布预算省略 {visible.omittedNodes} 节点 /{" "}
              {visible.omittedEdges} 边
            </span>
          )}
          {raw.omittedCandidates > 0 && (
            <span> · 候选预览超预算，完整内容见批次详情</span>
          )}
          {v.start || v.end ? <span> · 已启用局部事件筛选</span> : null}
          {!!base?.overview && !base.overview.stats_available && (
            <span> · 全库统计未知</span>
          )}
          {c.hiddenIds.length > 0 && (
            <span> · {c.hiddenIds.length} 项已隐藏</span>
          )}
        </div>
        <div>
          <button
            disabled={
              !undo.current[lens].past.length &&
              (!interaction.current ||
                interaction.current.canvases[v.layout] === c)
            }
            aria-label="撤销画布操作"
            onClick={() => runViewAction("undo")}
          >
            <RotateCcw size={15} />
          </button>
          <button
            disabled={!undo.current[lens].future.length}
            aria-label="重做画布操作"
            onClick={() => runViewAction("redo")}
          >
            <RotateCw size={15} />
          </button>
          <button
            aria-label="比较所选对象"
            disabled={v.selected.length < 2}
            onClick={() => setCompareIds(v.selected)}
          >
            比较 {v.selected.length > 1 ? v.selected.length : ""}
          </button>
          <button
            title="本布局内固定"
            disabled={!v.selected.length}
            aria-label="固定所选对象"
            onClick={() => runViewAction("pin")}
          >
            <Pin size={14} />
          </button>
          <button aria-label="保存命名视图" onClick={() => setSavedOpen(true)}>
            <Save size={15} />
          </button>
          <button aria-label="导出图谱" onClick={() => issue("export-svg")}>
            <Download size={15} />
          </button>
          <button
            aria-label="图谱显示工具"
            onClick={() => {
              update({ sidebar: true });
              setTimeOpen(true);
            }}
          >
            <SlidersHorizontal size={15} />
          </button>
        </div>
      </footer>
      {searchOpen && (
        <div
          className="gw-search-results"
          role="region"
          aria-label="定位搜索结果"
        >
          <div className="gw-panel-heading">
            <strong>定位“{searchTerm || "全部"}”</strong>
            <button
              aria-label="关闭搜索结果"
              onClick={() => setSearchOpen(false)}
            >
              <X size={16} />
            </button>
          </div>
          <p className="gw-muted">选择只定位与核对；作为筛选才改变画布范围。</p>
          {searchBusy && <p role="status">正在全范围搜索…</p>}
          {!!searchError && <p role="alert">{errorMessage(searchError)}</p>}
          {lens === "ontology"
            ? searchIds.map((id) => {
                const item = ontology.nodes.find((n) => n.id === id),
                  relation = ontology.edges.find((e) => e.id === id);
                return (
                  <button
                    className="gw-search-result"
                    key={id}
                    onClick={() => {
                      if (item) selectNode(item);
                      else if (relation) selectEdge(relation);
                      setSearchOpen(false);
                    }}
                  >
                    <BookOpen size={15} />
                    {item?.label ?? relation?.label ?? id}
                  </button>
                );
              })
            : lens === "review"
              ? searchReviews?.items.map((batch) => (
                  <button
                    className="gw-search-result"
                    key={batch.id}
                    onClick={() => {
                      batchCache.current.set(batch.id, batch);
                      navigate("review", {
                        primary: reviewId(batch.id),
                        selected: [reviewId(batch.id)],
                        edge: null,
                        inspector: true,
                      });
                      onSelectReview(batch);
                      setSearchOpen(false);
                    }}
                  >
                    <Check size={15} />
                    <span>
                      <strong>{batch.entities[0]?.name || "关系提案"}</strong>
                      <small>
                        {batch.entities.length} 个实体 ·{" "}
                        {batch.relations.length} 条关系
                      </small>
                    </span>
                  </button>
                ))
              : searchResults?.items.map((item) => (
                  <button
                    key={item.uri}
                    className="gw-search-result"
                    onClick={() => {
                      selectUri(item.uri);
                      setSearchOpen(false);
                    }}
                  >
                    <span
                      className="gw-type-marker"
                      style={{
                        background: GRAPH_TYPE_COLORS[item.type]?.stroke,
                      }}
                    />
                    <span>
                      <strong>{item.name}</strong>
                      <small>{typeName(item.type)}</small>
                    </span>
                  </button>
                ))}
          {!searchBusy &&
            (lens === "ontology"
              ? !searchIds.length
              : lens === "review"
                ? searchReviews?.total === 0
                : searchResults?.total === 0) && (
              <p className="gw-empty">当前搜索范围没有命中。</p>
            )}
          {lens !== "ontology" && (
            <div className="gw-pagination">
              <button
                disabled={!searchOffset || searchBusy}
                onClick={() => submitSearch(searchOffset - 20)}
              >
                上一页
              </button>
              <span>
                {(lens === "review" ? searchReviews : searchResults)?.total ??
                  "—"}{" "}
                个匹配{lens === "review" ? "批次" : "实体"}
              </span>
              <button
                disabled={
                  !(lens === "review" ? searchReviews : searchResults)
                    ?.has_more || searchBusy
                }
                onClick={() => submitSearch(searchOffset + 20)}
              >
                下一页
              </button>
            </div>
          )}
          <button
            className="gw-primary"
            onClick={() => {
              navigate(lens, { filterQuery: searchTerm, reviewPage: 0 });
              setSearchOpen(false);
            }}
          >
            作为当前范围筛选
          </button>
        </div>
      )}
      {commandsOpen && (
        <WorkspaceDialog
          title="命令菜单"
          onClose={() => setCommandsOpen(false)}
        >
          <input
            autoFocus
            aria-label="搜索命令"
            placeholder="输入操作名称…"
            value={commandSearch}
            onChange={(e) => setCommandSearch(e.target.value)}
          />
          <div className="gw-command-list">
            {commands
              .filter((action) => action.label.includes(commandSearch))
              .map((action) => (
                <button
                  key={action.label}
                  onClick={() => {
                    setCommandsOpen(false);
                    action.run();
                  }}
                >
                  {action.label}
                  <ArrowRight size={14} />
                </button>
              ))}
          </div>
        </WorkspaceDialog>
      )}
      {groupOpen && (
        <WorkspaceDialog
          title="创建当前视图分组"
          onClose={() => setGroupOpen(false)}
        >
          <p>仅分组当前已加载的 {v.selected.length} 个对象，不改变知识。</p>
          <label>
            分组名称
            <input
              autoFocus
              maxLength={60}
              value={groupName}
              onChange={(e) => setGroupName(e.target.value)}
            />
          </label>
          <button
            className="gw-primary"
            disabled={v.selected.length < 2}
            onClick={() => {
              const id = `group:${crypto.randomUUID()}`;
              commit((view) => ({
                ...view,
                groups: [
                  ...view.groups,
                  {
                    id,
                    label: groupName.trim() || "分组",
                    memberIds: [...view.selected],
                  },
                ],
                selected: [id],
                primary: id,
              }));
              onClearSelection();
              setGroupOpen(false);
            }}
          >
            创建分组
          </button>
        </WorkspaceDialog>
      )}
      {compareIds && (
        <CompareDialog
          api={api}
          items={compareIds
            .map(
              (id) =>
                visible.nodes.find((item) => item.id === id) ??
                raw.nodes.find((item) => item.id === id),
            )
            .filter((item): item is GraphNode => !!item)}
          reviews={pending}
          onClose={() => setCompareIds(null)}
        />
      )}
      {savedOpen && schema && (
        <SavedViewsDialog
          namespace={schema.namespace}
          viewState={cleanView(v)}
          seeds={Object.values(v.expansions).map((e) => ({
            ...e.query,
            cursor: null,
          }))}
          lens={lens}
          onClose={() => setSavedOpen(false)}
          onLoad={(value, target, queries) => {
            if (
              !validView(value) ||
              !["ontology", "explore", "review"].includes(target)
            ) {
              setNotice("保存的视图格式不兼容。");
              return;
            }
            seeds.current[target as GraphLens] = queries as NeighborQuery[];
            setSeedRevision((t) => t + 1);
            const migrated = restoreDirectoryState(value, target as GraphLens);
            const restored = currentDefinitionView(migrated, schema);
            navigate(target as GraphLens, restored);
            setSavedOpen(false);
            setNotice(
              restored !== migrated
                ? "已恢复视图；本体定义已变化，已移除失效筛选。永久删除的定义不会被视图恢复。"
                : "已恢复视图，记录按当前权限重新读取。",
            );
          }}
        />
      )}
    </div>
  );
}
