import {
  memo,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import type { CSSProperties, KeyboardEvent, PointerEvent } from "react";
import {
  BookOpen,
  CalendarDays,
  Check,
  ChevronUp,
  Download,
  EyeOff,
  Fingerprint,
  Focus,
  GitBranch,
  Group,
  HelpCircle,
  Layers3,
  Map as MapIcon,
  MapPin,
  Maximize2,
  Minus,
  Pin,
  Plus,
  Redo2,
  SearchCheck,
  Sparkles,
  Undo2,
  UserRound,
  X,
} from "lucide-react";
import {
  directionalNode,
  GRAPH_LAYER_LABELS,
  GRAPH_TYPE_COLORS,
  GRAPH_TYPE_LABELS,
  graphBounds,
  graphEdgeGeometry,
  layoutGraph,
} from "./graphModel";
import type { GraphEdge, GraphNode, PositionedGraphNode } from "./graphModel";
import {
  clampGraphZoom,
  computeGraphLayout,
  fitGraphView,
  insertGraphNodes,
  MAX_GRAPH_ZOOM,
  MIN_GRAPH_ZOOM,
  zoomGraphAt,
} from "./graphLayout";
import type {
  GraphLayoutMode,
  GraphPoint,
  GraphPositions,
  GraphView,
} from "./graphLayout";
import { createGraphDragSimulation, relationGraphLayout } from "./graphPhysics";
import type { GraphDragSimulation } from "./graphPhysics";
import "./graph.css";

export interface CanvasState {
  view: { x: number; y: number; zoom: number };
  positions: Record<string, { x: number; y: number }>;
  pinnedIds: string[];
  hiddenIds: string[];
  viewport?: { width: number; height: number };
}
export interface GraphCanvasProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  layoutNodes?: GraphNode[];
  selectedId?: string | null;
  selectedEdgeId?: string | null;
  focusId?: string | null;
  onSelectNode: (node: GraphNode) => void;
  onSelectEdge: (edge: GraphEdge) => void;
  onFocusNode: (node: GraphNode) => void;
  emptyMessage?: string;
  workspaceKey?: string;
  layoutMode?: GraphLayoutMode;
  state?: CanvasState;
  onStateChange?: (next: CanvasState, commit?: boolean) => void;
  onBeginInteraction?: () => void;
  selectedIds?: string[];
  onSelectionChange?: (ids: string[], primary?: GraphNode) => void;
  onClearSelection?: () => void;
  onPinNodes?: (ids: string[], pinned?: boolean) => void;
  onHideNodes?: (ids: string[]) => void;
  onGroupNodes?: (ids: string[]) => void;
  onCompareNodes?: (ids: string[]) => void;
  spotlightId?: string | null;
  searchIds?: string[];
  command?: {
    id: number;
    type:
      | "fit"
      | "fit-selection"
      | "export-svg"
      | "export-png"
      | "pin"
      | "hide"
      | "undo"
      | "redo";
    label?: string;
  } | null;
  onUndo?: () => void;
  onRedo?: () => void;
}
const TYPE_ICONS: Record<string, typeof UserRound> = {
  person: UserRound,
  event: CalendarDays,
  preference: Sparkles,
  decision: Fingerprint,
  location: MapPin,
  concept: BookOpen,
  relation: GitBranch,
  any: Layers3,
};
const DEFAULT_STATE: CanvasState = {
  view: { x: 0, y: 0, zoom: 1 },
  positions: {},
  pinnedIds: [],
  hiddenIds: [],
};
const LAYOUT_NAMES = {
  relation: "关系布局",
  direction: "方向布局",
  type: "类型布局",
  center: "中心布局",
};
const label = (value: string, length = 15) =>
  Array.from(value).length > length
    ? `${Array.from(value)
        .slice(0, length - 1)
        .join("")}…`
    : value;
const distance = (a: GraphPoint, b: GraphPoint) =>
  Math.hypot(a.x - b.x, a.y - b.y);
const middle = (a: GraphPoint, b: GraphPoint) => ({
  x: (a.x + b.x) / 2,
  y: (a.y + b.y) / 2,
});
type Gesture =
  | { kind: "pan"; start: GraphPoint; original: GraphView; moved: boolean }
  | {
      kind: "node";
      start: GraphPoint;
      originals: GraphPositions;
      ids: string[];
      node: GraphNode;
      moved: boolean;
      additive: boolean;
    }
  | { kind: "box"; start: GraphPoint; current: GraphPoint; base: string[] };
interface EdgeDrawing {
  edge: GraphEdge;
  path: ReturnType<typeof graphEdgeGeometry>;
  members: GraphEdge[];
  bundleKey: string;
  collapsed: boolean;
}

interface EdgeGlyphProps {
  drawing: EdgeDrawing;
  description: string;
  className: string;
  selected: boolean;
  showLabel: boolean;
  compact: boolean;
  zoom: number;
  markerId: string;
  attachRef: (element: SVGGElement | null) => void;
  onActivate: (drawing: EdgeDrawing) => void;
  onKey: (event: KeyboardEvent<SVGGElement>, drawing: EdgeDrawing) => void;
}
const GraphEdgeGlyph = memo(function GraphEdgeGlyph({
  drawing,
  description,
  className,
  selected,
  showLabel,
  compact,
  zoom,
  markerId,
  attachRef,
  onActivate,
  onKey,
}: EdgeGlyphProps) {
  const { edge, path, collapsed, members } = drawing;
  const count = collapsed
    ? members.reduce((sum, member) => sum + (member.count ?? 1), 0)
    : (edge.count ?? 1);
  return (
    <g
      ref={attachRef}
      className={className}
      role="button"
      tabIndex={-1}
      aria-label={description}
      aria-pressed={selected}
      data-graph-interactive="true"
      onClick={() => onActivate(drawing)}
      onKeyDown={(event) => onKey(event, drawing)}
    >
      <title>{description}</title>
      <path
        className="graph-edge-hit"
        d={path.d}
        style={{ strokeWidth: 18 / zoom }}
      />
      <path
        className="graph-edge-line"
        d={path.d}
        markerEnd={compact ? undefined : `url(#${markerId})`}
      />
      {showLabel && (
        <g>
          {collapsed && (
            <rect
              className="graph-bundle-badge"
              x={path.x - 49}
              y={path.y - 24}
              width={98}
              height={25}
              rx={12}
            />
          )}
          <text
            className="graph-edge-label"
            x={path.x}
            y={path.y - 7}
            textAnchor="middle"
          >
            {collapsed
              ? `${count} 条关系 +`
              : `${label(edge.label)}${edge.disabled ? " · 已停用" : ""}${count > 1 ? ` ×${count}` : ""}`}
          </text>
        </g>
      )}
    </g>
  );
});

interface NodeArtworkProps {
  node: GraphNode;
  selected: boolean;
  focused: boolean;
  pinned: boolean;
  compact: boolean;
  medium: boolean;
  zoom: number;
  showLabel: boolean;
}
const GraphNodeArtwork = memo(function GraphNodeArtwork({
  node,
  selected,
  focused,
  pinned,
  compact,
  medium,
  zoom,
  showLabel,
}: NodeArtworkProps) {
  const schema = node.layer === "schema",
    collection = node.layer === "aggregate" || node.layer === "group";
  const Icon =
    node.layer === "review"
      ? SearchCheck
      : collection
        ? Layers3
        : (TYPE_ICONS[node.type] ?? BookOpen);
  return (
    <>
      {schema || collection ? (
        <rect
          className="graph-node-halo"
          x={schema ? -78 : -61}
          y={schema ? -37 : -39}
          width={schema ? 156 : 122}
          height={schema ? 74 : 78}
          rx={collection ? 22 : 17}
        />
      ) : (
        <circle className="graph-node-halo" r={compact ? 14 / zoom : 38} />
      )}
      {collection && !compact && (
        <>
          <rect
            className="graph-node-stack"
            x={-49}
            y={-36}
            width={104}
            height={68}
            rx={18}
          />
          <rect
            className="graph-node-stack"
            x={-53}
            y={-32}
            width={104}
            height={68}
            rx={18}
          />
        </>
      )}
      {compact ? (
        <circle
          className="graph-node-shape"
          r={collection ? 12 / zoom : 7 / zoom}
        />
      ) : schema ? (
        <rect
          className="graph-node-shape"
          x={-71}
          y={-29}
          width={142}
          height={58}
          rx={12}
        />
      ) : collection ? (
        <rect
          className="graph-node-shape"
          x={-56}
          y={-28}
          width={112}
          height={64}
          rx={18}
        />
      ) : node.layer === "review" ? (
        <path
          className="graph-node-shape"
          d="M 0 -33 L 33 0 L 0 33 L -33 0 Z"
        />
      ) : (
        <circle className="graph-node-shape" r={27} />
      )}
      {!compact && (
        <Icon
          x={schema ? -10 : collection ? -24 : -11}
          y={schema ? -23 : -11}
          size={schema ? 20 : 22}
          strokeWidth={1.8}
          className="graph-node-icon"
          aria-hidden="true"
        />
      )}
      {collection && (
        <text
          className="graph-node-count"
          x={compact ? 0 : 16}
          y={compact ? 4 / zoom : 6}
          textAnchor="middle"
          style={
            compact
              ? {
                  fontSize: "calc(11px / var(--graph-label-zoom, 1))",
                }
              : undefined
          }
        >
          {node.count ?? node.memberIds?.length ?? 0}
        </text>
      )}
      <text
        className="graph-node-label"
        style={{
          display: showLabel ? undefined : "none",
        }}
        y={compact ? 26 / zoom : schema ? 15 : collection ? 58 : 48}
        textAnchor="middle"
      >
        {label(node.label, schema ? (node.disabled ? 6 : 10) : 16)}
        {node.disabled ? " · 已停用" : ""}
      </text>
      <text
        className="graph-node-layer"
        style={{
          display: !compact && !medium ? undefined : "none",
        }}
        y={schema ? 49 : collection ? 75 : 66}
        textAnchor="middle"
      >
        {GRAPH_LAYER_LABELS[node.layer]}
      </text>
      {!compact && pinned && (
        <Pin
          x={schema ? 56 : collection ? 42 : 21}
          y={-32}
          size={14}
          className="graph-node-pin"
          aria-hidden="true"
        />
      )}
      {!compact && focused && (
        <Focus
          x={schema ? -70 : -34}
          y={-33}
          size={16}
          className="graph-node-focus-icon"
          aria-hidden="true"
        />
      )}
      {selected && !compact && (
        <Check
          x={schema ? 58 : collection ? 43 : 22}
          y={19}
          size={14}
          className="graph-node-selection-check"
          aria-hidden="true"
        />
      )}
    </>
  );
});

export default function GraphCanvas(props: GraphCanvasProps) {
  const {
    nodes,
    edges,
    layoutNodes,
    selectedId,
    selectedEdgeId,
    focusId,
    onSelectNode,
    onSelectEdge,
    onFocusNode,
    workspaceKey = "default",
    layoutMode = "type",
    state,
    onStateChange,
    onBeginInteraction,
    selectedIds,
    onSelectionChange,
    onClearSelection,
    onPinNodes,
    onHideNodes,
    onGroupNodes,
    onCompareNodes,
    spotlightId,
    searchIds = [],
    command,
    onUndo,
    onRedo,
    emptyMessage = "当前筛选下没有节点。调整类型或图层，或清除搜索。",
  } = props;
  const uid = useId().replace(/:/g, "");
  const canvasRef = useRef<HTMLElement>(null),
    hostRef = useRef<HTMLDivElement>(null),
    svgRef = useRef<SVGSVGElement>(null);
  const nodeRefs = useRef(new Map<string, SVGGElement>()),
    edgeRefs = useRef(new Map<string, SVGGElement>());
  const nodeRefCache = useRef(
      new Map<string, (element: SVGGElement | null) => void>(),
    ),
    edgeRefCache = useRef(
      new Map<string, (element: SVGGElement | null) => void>(),
    );
  const stableNodeRef = (id: string) => {
    let attach = nodeRefCache.current.get(id);
    if (!attach) {
      attach = (element) => {
        if (element) nodeRefs.current.set(id, element);
        else nodeRefs.current.delete(id);
      };
      nodeRefCache.current.set(id, attach);
    }
    return attach;
  };
  const stableEdgeRef = (id: string) => {
    let attach = edgeRefCache.current.get(id);
    if (!attach) {
      attach = (element) => {
        if (element) edgeRefs.current.set(id, element);
        else edgeRefs.current.delete(id);
      };
      edgeRefCache.current.set(id, attach);
    }
    return attach;
  };
  const [canvas, setCanvas] = useState<CanvasState>(state ?? DEFAULT_STATE);
  const worldRef = useRef<SVGGElement>(null),
    cameraLayerRef = useRef<HTMLDivElement>(null),
    zoomOutputRef = useRef<HTMLOutputElement>(null),
    miniViewportRef = useRef<SVGRectElement>(null);
  const miniGeometry = useRef({ x: 0, y: 0, scale: 1 });
  const sceneBounds = useRef({ x: 0, y: 0, width: 400, height: 280 });
  const autoFit = useRef(!state || Object.keys(state.positions).length === 0);
  const cameraTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const semantic = useRef(
    canvas.view.zoom < 0.28 ? 0 : canvas.view.zoom < 0.65 ? 1 : 2,
  );
  const pendingFull = useRef(false);
  const cameraDirty = useRef(false);
  const live = useRef(canvas),
    pending = useRef<CanvasState | null>(null),
    frame = useRef(0);
  const changeRef = useRef(onStateChange);
  changeRef.current = onStateChange;
  const controlledRef = useRef(state !== undefined);
  controlledRef.current = state !== undefined;
  const awaitingEchoes = useRef<CanvasState[]>([]);
  const gesture = useRef<Gesture | null>(null),
    touches = useRef(new Map<number, GraphPoint>());
  const physics = useRef<GraphDragSimulation | null>(null),
    physicsFrame = useRef(0),
    physicsLastFrame = useRef(0),
    reducedMotion = useRef(false);
  const regrouping = useRef<AbortController | null>(null);
  const pinch = useRef<{
    distance: number;
    midpoint: GraphPoint;
    view: GraphView;
  } | null>(null);
  const savedViewport = state?.viewport;
  const validViewport =
    !!savedViewport &&
    Number.isFinite(savedViewport.width) &&
    Number.isFinite(savedViewport.height) &&
    savedViewport.width > 0 &&
    savedViewport.height > 0;
  const [size, setSize] = useState(
    validViewport ? savedViewport! : { width: 960, height: 600 },
  );
  const firstMeasurement = useRef(true);
  const initialRestoredWithoutViewport = useRef(
    !!state && Object.keys(state.positions).length > 0 && !validViewport,
  );
  const sizeRef = useRef(size);
  sizeRef.current = size;
  const [activeId, setActiveId] = useState<string | null>(selectedId ?? null);
  const [localIds, setLocalIds] = useState<string[]>([]);
  const [hoverId, setHoverId] = useState<string | null>(null),
    [tooltip, setTooltip] = useState<{
      title: string;
      text: string;
      point: GraphPoint;
    } | null>(null);
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const [space, setSpace] = useState(false),
    [dragging, setDragging] = useState(false);
  const [box, setBox] = useState<{
    start: GraphPoint;
    current: GraphPoint;
  } | null>(null);
  const [menu, setMenu] = useState<{
    point: GraphPoint;
    node?: GraphNode;
  } | null>(null);
  const [showHelp, setShowHelp] = useState(false),
    [showMini, setShowMini] = useState(true),
    [showExport, setShowExport] = useState(false);
  const [expandedBundles, setExpandedBundles] = useState(new Set<string>());
  const [announcement, setAnnouncement] = useState(""),
    [layingOut, setLayingOut] = useState(false);
  const layoutTicket = useRef(0),
    scope = `${workspaceKey}:${layoutMode}`;
  const lastNodeClick = useRef<{ id: string; time: number } | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (menu)
      menuRef.current
        ?.querySelector<HTMLButtonElement>('[role="menuitem"]')
        ?.focus();
  }, [menu]);
  const sourceNodes = layoutNodes ?? nodes;
  const artworkNodes = useMemo(
    () => new Map(nodes.map((node) => [node.id, node])),
    [nodes],
  );
  const sourceKey = sourceNodes
    .map((node) => node.id)
    .sort()
    .join("\u0000");
  const edgeKey = edges
    .map((edge) =>
      JSON.stringify([edge.id, edge.source, edge.target, edge.count ?? 1]),
    )
    .sort()
    .join("\u0000");
  const paintCamera = useCallback((view: GraphView) => {
    worldRef.current?.setAttribute(
      "transform",
      `translate(${view.x} ${view.y}) scale(${view.zoom})`,
    );
    if (cameraLayerRef.current)
      cameraLayerRef.current.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.zoom})`;
    svgRef.current
      ?.querySelectorAll<SVGElement>(
        ".graph-node.is-selected .graph-node-label, .graph-node:hover .graph-node-label, .graph-node.is-spotlight .graph-node-label, .graph-node-count",
      )
      .forEach((element) =>
        element.style.setProperty("--graph-label-zoom", String(view.zoom)),
      );
    if (zoomOutputRef.current)
      zoomOutputRef.current.textContent = `${Math.round(view.zoom * 100)}%`;
    const mini = miniViewportRef.current,
      geometry = miniGeometry.current;
    if (mini) {
      mini.setAttribute(
        "x",
        String((-view.x / view.zoom - geometry.x) * geometry.scale + 12),
      );
      mini.setAttribute(
        "y",
        String((-view.y / view.zoom - geometry.y) * geometry.scale + 12),
      );
      mini.setAttribute(
        "width",
        String((sizeRef.current.width / view.zoom) * geometry.scale),
      );
      mini.setAttribute(
        "height",
        String((sizeRef.current.height / view.zoom) * geometry.scale),
      );
    }
  }, []);
  const update = useCallback(
    (next: CanvasState, commit = false) => {
      next = { ...next, viewport: { ...sizeRef.current } };
      if (cameraTimer.current) clearTimeout(cameraTimer.current);
      if (frame.current) cancelAnimationFrame(frame.current);
      frame.current = 0;
      pending.current = null;
      pendingFull.current = false;
      live.current = next;
      cameraDirty.current = false;
      paintCamera(next.view);
      semantic.current =
        next.view.zoom < 0.28 ? 0 : next.view.zoom < 0.65 ? 1 : 2;
      setCanvas(next);
      if (changeRef.current) {
        if (controlledRef.current) {
          awaitingEchoes.current.push(next);
          if (awaitingEchoes.current.length > 32)
            awaitingEchoes.current.shift();
        }
        changeRef.current(next, commit);
      }
    },
    [paintCamera],
  );
  const schedule = useCallback(
    (next: CanvasState) => {
      const previous = live.current;
      const cameraOnly =
        next.positions === previous.positions &&
        next.pinnedIds === previous.pinnedIds &&
        next.hiddenIds === previous.hiddenIds;
      live.current = next;
      cameraDirty.current ||= cameraOnly;
      pending.current = next;
      pendingFull.current ||= !cameraOnly;
      if (cameraTimer.current) clearTimeout(cameraTimer.current);
      if (cameraOnly)
        cameraTimer.current = setTimeout(() => update(live.current), 120);
      if (!frame.current)
        frame.current = requestAnimationFrame(() => {
          frame.current = 0;
          if (pending.current) {
            const value = pending.current;
            pending.current = null;
            const full = pendingFull.current;
            pendingFull.current = false;
            if (full) update(value);
            else {
              // Camera motion never reconciles thousands of SVG descendants.
              // Publish its external snapshot when the gesture settles.
              paintCamera(value.view);
              // Semantic details settle after 120 ms without input so hundreds
              // of icon/label updates never interrupt a continuous gesture.
            }
          }
        });
    },
    [update, paintCamera],
  );
  const flush = useCallback(
    (commit = false) => {
      if (frame.current) cancelAnimationFrame(frame.current);
      frame.current = 0;
      pending.current = null;
      update(live.current, commit);
    },
    [update],
  );
  const stopPhysics = useCallback(
    (commit = false) => {
      if (physicsFrame.current) cancelAnimationFrame(physicsFrame.current);
      physicsFrame.current = 0;
      if (!physics.current) return;
      physics.current.stop();
      physics.current = null;
      if (commit) update(live.current, true);
    },
    [update],
  );
  const historyAction = (redo = false) => {
    regrouping.current?.abort();
    stopPhysics(true);
    gesture.current = null;
    setDragging(false);
    if (redo) onRedo?.();
    else onUndo?.();
  };
  const runPhysics = useCallback(() => {
    if (physicsFrame.current || !physics.current || document.hidden) return;
    const step = (time: number) => {
      physicsFrame.current = 0;
      const engine = physics.current;
      if (!engine || document.hidden) return;
      // Render at most 30 snapshots/sec, independently of pointer event rate.
      if (time - physicsLastFrame.current >= 32) {
        physicsLastFrame.current = time;
        update({
          ...live.current,
          positions: { ...live.current.positions, ...engine.tick(2) },
        });
      }
      if (engine.done) {
        if (gesture.current?.kind !== "node") stopPhysics(true);
      } else physicsFrame.current = requestAnimationFrame(step);
    };
    physicsFrame.current = requestAnimationFrame(step);
  }, [update, stopPhysics]);
  useEffect(() => {
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    reducedMotion.current = preference.matches;
    const finish = () => {
      if (!physics.current) return;
      stopPhysics(true);
      if (gesture.current?.kind === "node") {
        gesture.current = null;
        setDragging(false);
      }
    };
    const change = () => {
      reducedMotion.current = preference.matches;
      if (preference.matches) finish();
    };
    const visibility = () => {
      if (document.hidden) finish();
    };
    preference.addEventListener("change", change);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      preference.removeEventListener("change", change);
      document.removeEventListener("visibilitychange", visibility);
      stopPhysics();
    };
  }, [stopPhysics]);
  useEffect(() => {
    const outside = (event: globalThis.PointerEvent) => {
      if (
        cameraDirty.current &&
        !canvasRef.current?.contains(event.target as Node)
      )
        flush();
    };
    const keyboard = (event: globalThis.KeyboardEvent) => {
      if (
        cameraDirty.current &&
        !["Shift", "Control", "Alt", "Meta"].includes(event.key)
      )
        flush();
    };
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("keydown", keyboard, true);
    return () => {
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("keydown", keyboard, true);
    };
  }, [flush]);
  useEffect(() => {
    if (state) {
      const echo = awaitingEchoes.current.indexOf(state);
      if (echo >= 0) {
        // Parent echoes may arrive after the next pointer event has already
        // queued a newer camera. An echo must never cancel that newer frame.
        awaitingEchoes.current.splice(0, echo + 1);
        return;
      }
    }
    if (state && state !== live.current) {
      // A parent action can batch away one of our snapshots (e.g. settle then
      // pin). Once an external state supersedes it, that old reference may be
      // an intentional undo target rather than a delayed acknowledgement.
      awaitingEchoes.current = [];
      regrouping.current?.abort();
      stopPhysics();
      gesture.current = null;
      setDragging(false);
      if (Object.keys(state.positions).length) autoFit.current = false;
      if (cameraTimer.current) clearTimeout(cameraTimer.current);
      if (frame.current) cancelAnimationFrame(frame.current);
      frame.current = 0;
      pendingFull.current = false;
      pending.current = null;
      live.current = state;
      paintCamera(state.view);
      semantic.current =
        state.view.zoom < 0.28 ? 0 : state.view.zoom < 0.65 ? 1 : 2;
      setCanvas(state);
    }
  }, [state]);
  useEffect(() => {
    regrouping.current?.abort();
    stopPhysics();
    gesture.current = null;
    autoFit.current = !state || Object.keys(state.positions).length === 0;
    touches.current.clear();
    pinch.current = null;
    setMenu(null);
    setHoverId(null);
    setTooltip(null);
    setBox(null);
  }, [scope]);
  useEffect(() => {
    regrouping.current?.abort();
    if (physics.current) {
      stopPhysics(true);
      gesture.current = null;
      setDragging(false);
    }
  }, [sourceKey, edgeKey, stopPhysics]);
  useEffect(
    () => () => {
      if (frame.current) cancelAnimationFrame(frame.current);
      if (cameraTimer.current) clearTimeout(cameraTimer.current);
      if (hoverTimer.current) clearTimeout(hoverTimer.current);
      regrouping.current?.abort();
    },
    [],
  );
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width > 0 && height > 0) {
        const previous = sizeRef.current;
        const skipCompensation =
          firstMeasurement.current && initialRestoredWithoutViewport.current;
        firstMeasurement.current = false;
        sizeRef.current = { width, height };
        setSize({ width, height });
        if (
          Object.keys(live.current.positions).length &&
          (width !== previous.width || height !== previous.height)
        ) {
          update({
            ...live.current,
            view: skipCompensation
              ? live.current.view
              : autoFit.current
                ? fitGraphView(sceneBounds.current, width, height)
                : {
                    ...live.current.view,
                    x: live.current.view.x + (width - previous.width) / 2,
                    y: live.current.view.y + (height - previous.height) / 2,
                  },
          });
        }
      }
    });
    observer.observe(host);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    let stopped = false;
    const controller = new AbortController();
    const ticket = ++layoutTicket.current,
      current = state ?? live.current;
    if (!sourceNodes.some((node) => !current.positions[node.id])) {
      setLayingOut(false);
      return;
    }
    if (Object.keys(current.positions).length > 0) {
      setLayingOut(false);
      update({
        ...current,
        positions: insertGraphNodes(
          sourceNodes,
          edges,
          current.positions,
          focusId ?? selectedId,
        ),
      });
      return;
    }
    if (!sourceNodes.length) return;
    setLayingOut(true);
    const initial =
      layoutMode === "type"
        ? Promise.resolve(
            Object.fromEntries(
              layoutGraph(sourceNodes).nodes.map(({ id, x, y }) => [
                id,
                { x, y },
              ]),
            ),
          )
        : computeGraphLayout(
            sourceNodes,
            edges,
            layoutMode,
            focusId ?? selectedId,
            controller.signal,
          );
    initial
      .catch(() => {
        if (!stopped)
          setAnnouncement(
            "自动布局暂不可用，已使用稳定类型布局。仍可拖动节点调整。",
          );
        return Object.fromEntries(
          layoutGraph(sourceNodes).nodes.map(({ id, x, y }) => [id, { x, y }]),
        );
      })
      .then((positions) => {
        if (stopped || ticket !== layoutTicket.current) return;
        const positioned = nodes
          .filter((node) => positions[node.id])
          .map((node) => ({ ...node, ...positions[node.id] }));
        update({
          ...live.current,
          positions,
          view: fitGraphView(
            graphBounds(positioned),
            sizeRef.current.width,
            sizeRef.current.height,
          ),
        });
      })
      .finally(() => {
        if (!stopped) setLayingOut(false);
      });
    return () => {
      stopped = true;
      controller.abort();
    };
    // External state stores coordinates per layout. New members only get local cells.
  }, [scope, sourceKey, edgeKey]);
  const chosen =
    selectedIds ??
    (localIds.length ? localIds : selectedId ? [selectedId] : []);
  const chosenSet = useMemo(() => new Set(chosen), [chosen.join("\u0000")]);
  const hidden = useMemo(() => new Set(canvas.hiddenIds), [canvas.hiddenIds]);
  const visible = useMemo(
    () =>
      nodes
        .filter((node) => !hidden.has(node.id))
        .map((node) => ({
          ...node,
          ...(canvas.positions[node.id] ?? { x: 0, y: 0 }),
        })),
    [nodes, hidden, canvas.positions],
  );
  const byId = useMemo(
    () => new Map(visible.map((node) => [node.id, node])),
    [visible],
  );
  const validEdges = useMemo(
    () =>
      edges.filter((edge) => byId.has(edge.source) && byId.has(edge.target)),
    [edges, byId],
  );
  const regroup = async () => {
    if (regrouping.current) {
      regrouping.current.abort();
      return;
    }
    stopPhysics(true);
    onBeginInteraction?.();
    const controller = new AbortController();
    regrouping.current = controller;
    setLayingOut(true);
    try {
      const positions = await relationGraphLayout(
        visible,
        validEdges,
        controller.signal,
        {
          positions: live.current.positions,
          pinnedIds: live.current.pinnedIds,
        },
      );
      if (!controller.signal.aborted)
        update(
          {
            ...live.current,
            positions: { ...live.current.positions, ...positions },
          },
          true,
        );
      setAnnouncement(
        controller.signal.aborted
          ? "已取消重新聚集，保留原有位置。"
          : "已按关联重新聚集，固定位置与视角保持。可撤销此次调整。",
      );
    } catch {
      setAnnouncement("重新聚集暂不可用，已保留原有位置。可重试或手动调整。");
    } finally {
      if (regrouping.current === controller) {
        regrouping.current = null;
        setLayingOut(false);
      }
    }
  };
  const nodeBounds = useMemo(() => graphBounds(visible), [visible]);
  const degree = useMemo(() => {
    const result = new Map<string, number>();
    validEdges.forEach((edge) => {
      result.set(edge.source, (result.get(edge.source) ?? 0) + 1);
      result.set(edge.target, (result.get(edge.target) ?? 0) + 1);
    });
    return result;
  }, [validEdges]);
  const priorityIds = useMemo(
    () =>
      new Set(
        [...visible]
          .sort(
            (a, b) =>
              (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0) ||
              a.id.localeCompare(b.id),
          )
          .slice(0, 18)
          .map((node) => node.id),
      ),
    [visible, degree],
  );
  const searchSet = useMemo(() => new Set(searchIds), [searchIds]);
  useEffect(
    () => paintCamera(live.current.view),
    [hoverId, spotlightId, chosen.join("\u0000"), paintCamera],
  );
  const emphasized = useMemo(() => {
    const centers = new Set(
      spotlightId ? [spotlightId] : hoverId ? [hoverId] : chosen,
    );
    if (!centers.size) return null;
    const result = new Set(centers);
    validEdges.forEach((edge) => {
      if (centers.has(edge.source)) result.add(edge.target);
      if (centers.has(edge.target)) result.add(edge.source);
    });
    return result;
  }, [hoverId, spotlightId, chosen.join("\u0000"), validEdges]);
  useEffect(() => {
    if (selectedId && byId.has(selectedId)) setActiveId(selectedId);
  }, [selectedId, byId]);
  useEffect(() => {
    if (!activeId || !byId.has(activeId)) setActiveId(visible[0]?.id ?? null);
  }, [activeId, byId, visible]);
  useEffect(() => {
    if (selectedIds === undefined) setLocalIds(selectedId ? [selectedId] : []);
  }, [selectedId, selectedIds]);
  const select = (ids: string[], primary?: GraphNode) => {
    const unique = [...new Set(ids)];
    setLocalIds(unique);
    onSelectionChange?.(unique, primary);
    if (primary) {
      setActiveId(primary.id);
      onSelectNode(primary);
    } else if (!unique.length) onClearSelection?.();
  };
  const pinNodes = () => {
    if (!chosen.length) return;
    if (regrouping.current) return;
    stopPhysics(true);
    const allPinned = chosen.every((id) => live.current.pinnedIds.includes(id));
    if (onPinNodes) onPinNodes(chosen, !allPinned);
    else {
      onBeginInteraction?.();
      update(
        {
          ...live.current,
          pinnedIds: allPinned
            ? live.current.pinnedIds.filter((id) => !chosenSet.has(id))
            : [...new Set([...live.current.pinnedIds, ...chosen])],
        },
        true,
      );
    }
    setMenu(null);
  };
  const hideNodes = () => {
    if (!chosen.length) return;
    if (regrouping.current) return;
    stopPhysics(true);
    if (onHideNodes) onHideNodes(chosen);
    else {
      onBeginInteraction?.();
      update(
        {
          ...live.current,
          hiddenIds: [...new Set([...live.current.hiddenIds, ...chosen])],
        },
        true,
      );
    }
    select([]);
    setMenu(null);
  };
  const fit = (selection = false) => {
    autoFit.current = !selection;
    const targets = selection
      ? visible.filter((node) => chosenSet.has(node.id))
      : visible;
    if (targets.length)
      update({
        ...live.current,
        view: fitGraphView(
          selection ? graphBounds(targets) : bounds,
          size.width,
          size.height,
        ),
      });
  };
  const zoom = (
    factor: number,
    point = { x: size.width / 2, y: size.height / 2 },
  ) => {
    autoFit.current = false;
    schedule({
      ...live.current,
      view: zoomGraphAt(live.current.view, factor, point),
    });
  };
  const localPoint = (event: {
    clientX: number;
    clientY: number;
  }): GraphPoint => {
    const rect = hostRef.current!.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const wheel = (event: WheelEvent) => {
      if (
        (event.target as Element).closest(".graph-context-menu,.graph-minimap")
      )
        return;
      event.preventDefault();
      autoFit.current = false;
      const rect = host.getBoundingClientRect(),
        amount = event.deltaY * (event.deltaMode === 1 ? 16 : 1);
      if (event.shiftKey && !event.ctrlKey)
        schedule({
          ...live.current,
          view: {
            ...live.current.view,
            x: live.current.view.x - (event.deltaX || amount),
          },
        });
      else
        schedule({
          ...live.current,
          view: zoomGraphAt(
            live.current.view,
            Math.exp(-amount * (event.ctrlKey ? 0.008 : 0.0018)),
            { x: event.clientX - rect.left, y: event.clientY - rect.top },
          ),
        });
    };
    host.addEventListener("wheel", wheel, { passive: false });
    return () => host.removeEventListener("wheel", wheel);
  }, [schedule]);
  const pointerDown = (event: PointerEvent<SVGSVGElement | HTMLDivElement>) => {
    if (regrouping.current) return;
    if (event.button !== 0 && event.button !== 1) return;
    if (
      (event.target as Element).closest(
        ".graph-minimap,.graph-context-menu,.graph-collapse-bundles",
      )
    )
      return;
    // Finish a previous settling drag before beginning a new undo transaction.
    stopPhysics(true);
    if (cameraDirty.current) flush();
    canvasRef.current?.focus({ preventScroll: true });
    if (hoverTimer.current) clearTimeout(hoverTimer.current);
    setMenu(null);
    setTooltip(null);
    setShowExport(false);
    const point = localPoint(event);
    if (event.pointerType === "touch") {
      touches.current.set(event.pointerId, point);
      if (touches.current.size >= 2) {
        const [a, b] = [...touches.current.values()];
        pinch.current = {
          distance: Math.max(1, distance(a, b)),
          midpoint: middle(a, b),
          view: live.current.view,
        };
        gesture.current = null;
        event.currentTarget.setPointerCapture(event.pointerId);
        event.preventDefault();
        return;
      }
    }
    const target = event.target as Element,
      id = target.closest("[data-node-id]")?.getAttribute("data-node-id");
    if (space || event.button === 1)
      gesture.current = {
        kind: "pan",
        start: point,
        original: live.current.view,
        moved: false,
      };
    else if (id && byId.has(id)) {
      onBeginInteraction?.();
      const node = byId.get(id)!,
        ids = (chosenSet.has(id) ? chosen : [id]).filter(
          (key) => !live.current.pinnedIds.includes(key),
        );
      gesture.current = {
        kind: "node",
        start: point,
        originals: Object.fromEntries(
          Object.entries(live.current.positions).map(([key, point]) => [
            key,
            { ...point },
          ]),
        ),
        ids,
        node,
        moved: false,
        additive: event.shiftKey || event.metaKey || event.ctrlKey,
      };
    } else if (target.closest("[data-graph-interactive]")) return;
    else if (event.shiftKey) {
      gesture.current = {
        kind: "box",
        start: point,
        current: point,
        base: event.ctrlKey || event.metaKey ? chosen : [],
      };
      setBox({ start: point, current: point });
    } else
      gesture.current = {
        kind: "pan",
        start: point,
        original: live.current.view,
        moved: false,
      };
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const pointerMove = (event: PointerEvent<SVGSVGElement | HTMLDivElement>) => {
    const point = localPoint(event);
    if (touches.current.has(event.pointerId))
      touches.current.set(event.pointerId, point);
    if (pinch.current && touches.current.size >= 2) {
      autoFit.current = false;
      const [a, b] = [...touches.current.values()],
        mid = middle(a, b),
        start = pinch.current,
        nextZoom = clampGraphZoom(
          (start.view.zoom * distance(a, b)) / start.distance,
        );
      schedule({
        ...live.current,
        view: {
          zoom: nextZoom,
          x:
            mid.x -
            ((start.midpoint.x - start.view.x) / start.view.zoom) * nextZoom,
          y:
            mid.y -
            ((start.midpoint.y - start.view.y) / start.view.zoom) * nextZoom,
        },
      });
      return;
    }
    const action = gesture.current;
    if (!action) return;
    if (action.kind === "box") {
      action.current = point;
      setBox({ start: action.start, current: point });
      return;
    }
    const dx = point.x - action.start.x,
      dy = point.y - action.start.y;
    if (!action.moved && Math.hypot(dx, dy) <= 5) return;
    if (action.kind === "node" && !action.ids.length) return;
    action.moved = true;
    autoFit.current = false;
    setDragging(true);
    if (action.kind === "pan")
      schedule({
        ...live.current,
        view: {
          ...action.original,
          x: action.original.x + dx,
          y: action.original.y + dy,
        },
      });
    else {
      const targets = Object.fromEntries(
        action.ids
          .filter((id) => action.originals[id])
          .map((id) => [
            id,
            {
              x: action.originals[id].x + dx / live.current.view.zoom,
              y: action.originals[id].y + dy / live.current.view.zoom,
            },
          ]),
      );
      if (layoutMode === "relation") {
        physics.current ??= createGraphDragSimulation(
          visible,
          validEdges,
          live.current.positions,
          action.ids,
          live.current.pinnedIds,
        );
        physics.current.move(targets);
        // Reduced motion still adjusts related nodes without a running animation.
        const positions = physics.current.tick(reducedMotion.current ? 12 : 1);
        schedule({
          ...live.current,
          positions: { ...live.current.positions, ...positions },
        });
        if (!reducedMotion.current) runPhysics();
      } else
        schedule({
          ...live.current,
          positions: { ...live.current.positions, ...targets },
        });
    }
  };
  const pointerUp = (event: PointerEvent<SVGSVGElement | HTMLDivElement>) => {
    touches.current.delete(event.pointerId);
    if (pinch.current) {
      pinch.current = null;
      const remaining = [...touches.current.values()][0];
      gesture.current = remaining
        ? {
            kind: "pan",
            start: remaining,
            original: live.current.view,
            moved: true,
          }
        : null;
      flush();
      return;
    }
    const action = gesture.current;
    gesture.current = null;
    setDragging(false);
    if (!action) return;
    if (action.kind === "box") {
      const x = Math.min(action.start.x, action.current.x),
        y = Math.min(action.start.y, action.current.y),
        right = Math.max(action.start.x, action.current.x),
        bottom = Math.max(action.start.y, action.current.y);
      const ids = visible
        .filter((node) => {
          const px = node.x * live.current.view.zoom + live.current.view.x,
            py = node.y * live.current.view.zoom + live.current.view.y;
          return px >= x && px <= right && py >= y && py <= bottom;
        })
        .map((node) => node.id);
      select([...action.base, ...ids]);
      setBox(null);
    } else if (action.kind === "node") {
      if (action.moved) {
        lastNodeClick.current = null;
        if (!chosenSet.has(action.node.id)) select(action.ids);
        if (physics.current && !reducedMotion.current && !document.hidden) {
          flush();
          runPhysics();
          if (physics.current.done) stopPhysics(true);
        } else {
          if (physics.current && reducedMotion.current)
            live.current = {
              ...live.current,
              positions: {
                ...live.current.positions,
                ...physics.current.tick(84),
              },
            };
          stopPhysics();
          flush(true);
        }
      } else if (action.additive) {
        lastNodeClick.current = null;
        select(
          chosenSet.has(action.node.id)
            ? chosen.filter((id) => id !== action.node.id)
            : [...chosen, action.node.id],
        );
      } else {
        select([action.node.id], action.node);
        const previous = lastNodeClick.current,
          now = performance.now();
        if (previous?.id === action.node.id && now - previous.time < 350) {
          lastNodeClick.current = null;
          onFocusNode(action.node);
        } else lastNodeClick.current = { id: action.node.id, time: now };
      }
    } else {
      flush();
      if (!action.moved) select([]);
    }
  };
  const leaveHover = () => {
    if (hoverTimer.current) clearTimeout(hoverTimer.current);
    setHoverId(null);
    setTooltip(null);
  };
  const hoverNode = (event: PointerEvent<SVGGElement>, node: GraphNode) => {
    if (gesture.current) return;
    const point = localPoint(event);
    if (hoverTimer.current) clearTimeout(hoverTimer.current);
    hoverTimer.current = setTimeout(() => {
      setHoverId(node.id);
      setTooltip({
        title: node.label,
        text: `${GRAPH_LAYER_LABELS[node.layer]} · ${GRAPH_TYPE_LABELS[node.type] ?? node.type}${node.count ? ` · ${node.count} 项` : ""}${node.description ? `\n${label(node.description, 120)}` : ""}`,
        point,
      });
    }, 140);
  };
  const reveal = (node: PositionedGraphNode) => {
    const view = live.current.view,
      px = node.x * view.zoom + view.x,
      py = node.y * view.zoom + view.y;
    if (px < 60 || px > size.width - 60 || py < 60 || py > size.height - 90)
      update({
        ...live.current,
        view: {
          ...view,
          x: size.width / 2 - node.x * view.zoom,
          y: size.height / 2 - node.y * view.zoom,
        },
      });
  };
  const keyNode = (
    event: KeyboardEvent<SVGGElement>,
    node: PositionedGraphNode,
  ) => {
    let next: PositionedGraphNode | undefined;
    if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key))
      next = directionalNode(
        node,
        visible,
        event.key as "ArrowLeft" | "ArrowRight" | "ArrowUp" | "ArrowDown",
      );
    else if (event.key === "Home") next = visible[0];
    else if (event.key === "End") next = visible[visible.length - 1];
    else if (event.key === "Enter" || (event.key === " " && !space)) {
      event.preventDefault();
      event.stopPropagation();
      if (event.key === " ") setSpace(true);
      select(
        event.shiftKey ? [...chosen, node.id] : [node.id],
        event.shiftKey ? undefined : node,
      );
      return;
    } else if (event.key.toLowerCase() === "r") {
      const edge = validEdges.find(
        (edge) => edge.source === node.id || edge.target === node.id,
      );
      if (edge) {
        event.preventDefault();
        event.stopPropagation();
        edgeRefs.current.get(edge.id)?.focus();
        onSelectEdge(edge);
      }
      return;
    } else return;
    event.preventDefault();
    event.stopPropagation();
    if (next) {
      setActiveId(next.id);
      reveal(next);
      nodeRefs.current.get(next.id)?.focus();
    }
  };
  const keyCanvas = (event: KeyboardEvent<HTMLElement>) => {
    if (
      (event.target as Element).closest(
        "input,textarea,select,[contenteditable=true]",
      )
    )
      return;
    const key = event.key.toLowerCase();
    if (regrouping.current) {
      if (key === "escape") regrouping.current.abort();
      event.preventDefault();
      return;
    }
    if (key === " " && event.target === event.currentTarget) {
      event.preventDefault();
      setSpace(true);
    } else if (key === "f" && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      fit(!event.shiftKey);
    } else if (key === "e" && !event.ctrlKey && !event.metaKey) {
      const node = byId.get(chosen[0] ?? activeId ?? "");
      if (node) {
        event.preventDefault();
        onFocusNode(node);
      }
    } else if (key === "p" && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      pinNodes();
    } else if (key === "escape") {
      event.preventDefault();
      if (space) {
        setSpace(false);
        if (gesture.current?.kind === "pan") {
          gesture.current = null;
          setDragging(false);
          flush();
        }
      } else if (menu) setMenu(null);
      else if (showHelp) setShowHelp(false);
      else {
        select([]);
        setHoverId(null);
        setTooltip(null);
      }
    } else if (key === "+" || key === "=") {
      event.preventDefault();
      zoom(1.25);
    } else if (key === "-") {
      event.preventDefault();
      zoom(0.8);
    } else if ((event.ctrlKey || event.metaKey) && key === "z") {
      event.preventDefault();
      historyAction(event.shiftKey);
    } else if ((event.ctrlKey || event.metaKey) && key === "a") {
      event.preventDefault();
      select(visible.map((node) => node.id));
    }
  };
  useEffect(() => {
    const release = () => setSpace(false);
    window.addEventListener("keyup", release);
    window.addEventListener("blur", release);
    return () => {
      window.removeEventListener("keyup", release);
      window.removeEventListener("blur", release);
    };
  }, []);
  const drawingCache = useRef(
    new Map<string, { geometry: string; drawing: EdgeDrawing }>(),
  );
  const drawings = useMemo(() => {
    const nextCache = new Map<
      string,
      { geometry: string; drawing: EdgeDrawing }
    >();
    const groups = new Map<string, GraphEdge[]>(),
      geometricSiblings = new Map<string, GraphEdge[]>();
    validEdges.forEach((edge) => {
      // A bundle never merges opposite directions, facts, rules or proposals.
      const key = JSON.stringify([
          edge.source,
          edge.target,
          edge.kind,
          edge.kind === "definition" ? (edge.relationType ?? edge.id) : "",
          !!edge.disabled,
        ]),
        canonical = JSON.stringify([edge.source, edge.target].sort());
      const group = groups.get(key) ?? [];
      group.push(edge);
      groups.set(key, group);
      const siblings = geometricSiblings.get(canonical) ?? [];
      siblings.push(edge);
      geometricSiblings.set(canonical, siblings);
    });
    geometricSiblings.forEach((siblings) =>
      siblings.sort((a, b) => a.id.localeCompare(b.id)),
    );
    const result: EdgeDrawing[] = [];
    groups.forEach((members, bundleKey) => {
      members.sort((a, b) => a.id.localeCompare(b.id));
      const collapsed =
        members.length >= 3 &&
        !expandedBundles.has(bundleKey) &&
        !members.some((edge) => edge.id === selectedEdgeId);
      const siblings = geometricSiblings.get(
        JSON.stringify([members[0].source, members[0].target].sort()),
      )!;
      const indices = new Map(siblings.map((edge, index) => [edge.id, index]));
      (collapsed ? [members[0]] : members).forEach((edge) => {
        const index = indices.get(edge.id)!;
        const slot = collapsed
          ? members.reduce((sum, member) => sum + indices.get(member.id)!, 0) /
            members.length
          : index;
        const bend =
          (slot - (siblings.length - 1) / 2) * 48 +
          (edge.kind === "definition" ? 18 : 0);
        const source = byId.get(edge.source)!,
          target = byId.get(edge.target)!;
        const geometry = JSON.stringify([
          source.id,
          source.layer,
          source.x,
          source.y,
          target.id,
          target.layer,
          target.x,
          target.y,
          bend,
          index,
          collapsed,
        ]);
        const previous = drawingCache.current.get(edge.id);
        if (
          previous?.geometry === geometry &&
          previous.drawing.edge === edge &&
          previous.drawing.members.length === members.length &&
          previous.drawing.members.every(
            (member, offset) => member === members[offset],
          )
        ) {
          nextCache.set(edge.id, previous);
          result.push(previous.drawing);
          return;
        }
        const drawing: EdgeDrawing = {
          edge,
          path: graphEdgeGeometry(source, target, bend, index),
          members,
          bundleKey,
          collapsed,
        };
        nextCache.set(edge.id, { geometry, drawing });
        result.push(drawing);
      });
    });
    drawingCache.current = nextCache;
    return result;
  }, [validEdges, byId, expandedBundles, selectedEdgeId]);
  const edgeActions = useRef({
    activate: (_drawing: EdgeDrawing) => {},
    key: (_event: KeyboardEvent<SVGGElement>, _drawing: EdgeDrawing) => {},
  });
  edgeActions.current = {
    activate: (drawing) => {
      if (drawing.collapsed)
        setExpandedBundles(
          (current) => new Set([...current, drawing.bundleKey]),
        );
      else onSelectEdge(drawing.edge);
    },
    key: (event, drawing) => {
      const edge = drawing.edge;
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        event.stopPropagation();
        edgeActions.current.activate(drawing);
      } else if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        nodeRefs.current.get(edge.source)?.focus();
      } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        event.stopPropagation();
        const related = drawings.filter(
            (item) =>
              item.edge.source === edge.source ||
              item.edge.target === edge.source,
          ),
          index = related.findIndex((item) => item.edge.id === edge.id),
          next =
            related[
              (index + (event.key === "ArrowRight" ? 1 : related.length - 1)) %
                related.length
            ];
        if (next) {
          edgeRefs.current.get(next.edge.id)?.focus();
          if (!next.collapsed) onSelectEdge(next.edge);
        }
      }
    },
  };
  const activateEdge = useCallback(
    (drawing: EdgeDrawing) => edgeActions.current.activate(drawing),
    [],
  );
  const keyEdge = useCallback(
    (event: KeyboardEvent<SVGGElement>, drawing: EdgeDrawing) =>
      edgeActions.current.key(event, drawing),
    [],
  );
  const bounds = useMemo(() => {
    let left = nodeBounds.x,
      top = nodeBounds.y,
      right = left + nodeBounds.width,
      bottom = top + nodeBounds.height;
    for (const drawing of drawings) {
      const coordinates =
        drawing.path.d.match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi)?.map(Number) ??
        [];
      for (let index = 0; index + 1 < coordinates.length; index += 2) {
        left = Math.min(left, coordinates[index] - 18);
        right = Math.max(right, coordinates[index] + 18);
        top = Math.min(top, coordinates[index + 1] - 24);
        bottom = Math.max(bottom, coordinates[index + 1] + 18);
      }
    }
    return { x: left, y: top, width: right - left, height: bottom - top };
  }, [drawings, nodeBounds]);
  sceneBounds.current = bounds;
  const exportGraph = async (format: "svg" | "png") => {
    setShowExport(false);
    if (!svgRef.current || !visible.length) return;
    try {
      const original = svgRef.current,
        copy = original.cloneNode(true) as SVGSVGElement;
      const originalElements = [original, ...original.querySelectorAll("*")],
        copiedElements = [copy, ...copy.querySelectorAll("*")];
      const styleNames = [
        "fill",
        "stroke",
        "stroke-width",
        "stroke-dasharray",
        "font-family",
        "font-size",
        "font-weight",
        "paint-order",
        "stroke-linejoin",
        "opacity",
        "color",
        "vector-effect",
      ];
      originalElements.forEach((element, index) => {
        const computed = getComputedStyle(element),
          target = copiedElements[index] as SVGElement;
        styleNames.forEach((name) =>
          target.style.setProperty(name, computed.getPropertyValue(name)),
        );
      });
      copy
        .querySelectorAll("[data-export-exclude]")
        .forEach((element) => element.remove());
      copy.querySelector(".graph-world")?.removeAttribute("transform");
      (
        copy.querySelector(".graph-world") as SVGElement | null
      )?.style.removeProperty("transform");
      copy
        .querySelectorAll(".graph-node-label,.graph-node-layer")
        .forEach(
          (element) => ((element as SVGElement).style.display = "block"),
        );
      copy.setAttribute("xmlns", "http://www.w3.org/2000/svg");
      copy.setAttribute(
        "viewBox",
        `${bounds.x} ${bounds.y} ${bounds.width} ${bounds.height}`,
      );
      copy.setAttribute("width", String(bounds.width));
      copy.setAttribute("height", String(bounds.height));
      const background = document.createElementNS(
        "http://www.w3.org/2000/svg",
        "rect",
      );
      background.setAttribute("x", String(bounds.x));
      background.setAttribute("y", String(bounds.y));
      background.setAttribute("width", String(bounds.width));
      background.setAttribute("height", String(bounds.height));
      background.setAttribute("fill", "#fcfdfb");
      copy.insertBefore(background, copy.querySelector(".graph-world"));
      const description = document.createElementNS(
        "http://www.w3.org/2000/svg",
        "desc",
      );
      description.textContent = `当前已加载可见子图：${visible.length} 个节点、${validEdges.length} 条关系。包含当前聚合与分组；不包含未加载、已筛除和隐藏的内容。`;
      copy.prepend(description);
      const blob = new Blob([new XMLSerializer().serializeToString(copy)], {
        type: "image/svg+xml;charset=utf-8",
      });
      const download = (data: Blob, suffix: string) => {
        const url = URL.createObjectURL(data),
          anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = `knowledge-visible-graph.${suffix}`;
        anchor.click();
        setTimeout(() => URL.revokeObjectURL(url), 30000);
      };
      if (format === "svg") download(blob, "svg");
      else {
        // The deployed console permits data: images and deliberately does not
        // permit blob: images. Keep that CSP boundary during PNG rasterization.
        const imageData = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () =>
            typeof reader.result === "string"
              ? resolve(reader.result)
              : reject(new Error("svg"));
          reader.onerror = reject;
          reader.readAsDataURL(blob);
        });
        const picture = new Image();
        await new Promise<void>((resolve, reject) => {
          picture.onload = () => resolve();
          picture.onerror = reject;
          picture.src = imageData;
        });
        const raster = document.createElement("canvas"),
          scale = Math.min(2, 4096 / Math.max(bounds.width, bounds.height));
        raster.width = Math.max(1, Math.round(bounds.width * scale));
        raster.height = Math.max(1, Math.round(bounds.height * scale));
        const context = raster.getContext("2d");
        if (!context) throw new Error("canvas");
        context.drawImage(picture, 0, 0, raster.width, raster.height);
        const png = await new Promise<Blob>((resolve, reject) =>
          raster.toBlob(
            (value) => (value ? resolve(value) : reject(new Error("png"))),
            "image/png",
          ),
        );
        download(png, "png");
      }
      setAnnouncement(
        `已导出 ${visible.length} 个可见节点。文件仅包含已加载可见子图。`,
      );
    } catch {
      setAnnouncement("导出未完成，请重试或改用 SVG 格式。");
    }
  };
  const commandRef = useRef<number | null>(null);
  useEffect(() => {
    if (!command || commandRef.current === command.id) return;
    commandRef.current = command.id;
    if (command.type === "fit") fit();
    else if (command.type === "fit-selection") fit(true);
    else if (command.type === "pin") pinNodes();
    else if (command.type === "hide") hideNodes();
    else if (command.type === "undo") historyAction();
    else if (command.type === "redo") historyAction(true);
    else void exportGraph(command.type === "export-png" ? "png" : "svg");
  }, [command]);
  const miniScale = Math.min(
    156 / Math.max(1, bounds.width),
    106 / Math.max(1, bounds.height),
  );
  miniGeometry.current = { x: bounds.x, y: bounds.y, scale: miniScale };
  const miniX = (point: number) => (point - bounds.x) * miniScale + 12,
    miniY = (point: number) => (point - bounds.y) * miniScale + 12;
  const moveMini = (event: PointerEvent<SVGSVGElement>) => {
    autoFit.current = false;
    const rect = event.currentTarget.getBoundingClientRect(),
      x =
        bounds.x +
        (((event.clientX - rect.left) * 180) / rect.width - 12) / miniScale,
      y =
        bounds.y +
        (((event.clientY - rect.top) * 130) / rect.height - 12) / miniScale;
    schedule({
      ...live.current,
      view: {
        ...live.current.view,
        x: size.width / 2 - x * live.current.view.zoom,
        y: size.height / 2 - y * live.current.view.zoom,
      },
    });
  };
  const compact = canvas.view.zoom < 0.28,
    medium = canvas.view.zoom < 0.65;
  return (
    <section
      ref={canvasRef}
      className={`graph-canvas${focusId ? " is-focused" : ""}${space ? " is-pan-tool" : ""}`}
      aria-label="交互知识图谱"
      tabIndex={0}
      onKeyDown={keyCanvas}
      onBlur={(event) => {
        if (
          cameraDirty.current &&
          !event.currentTarget.contains(event.relatedTarget as Node)
        )
          flush();
      }}
    >
      <div className="graph-canvas-toolbar">
        <div className="graph-canvas-context">
          <span>{LAYOUT_NAMES[layoutMode]}</span>
          <small>
            {visible.length} 节点 · {validEdges.length} 关系
            {layingOut ? " · 正在布局" : ""}
          </small>
        </div>
        <div
          className="graph-canvas-controls"
          role="group"
          aria-label="图谱视图控制"
        >
          {onUndo && (
            <button
              onClick={() => historyAction()}
              disabled={!!regrouping.current}
              aria-label="撤销图谱操作"
              title="撤销 Ctrl+Z"
            >
              <Undo2 size={16} />
            </button>
          )}
          {onRedo && (
            <button
              onClick={() => historyAction(true)}
              disabled={!!regrouping.current}
              aria-label="重做图谱操作"
              title="重做 Ctrl+Shift+Z"
            >
              <Redo2 size={16} />
            </button>
          )}
          {layoutMode === "relation" && (
            <button
              onClick={() => void regroup()}
              disabled={!visible.length || (layingOut && !regrouping.current)}
              aria-label={
                regrouping.current ? "取消重新聚集" : "重新聚集关联节点"
              }
              title={
                regrouping.current
                  ? "取消重新聚集 Escape"
                  : "按关联重新聚集，保留固定位置和视角；可撤销"
              }
            >
              {regrouping.current ? <X size={17} /> : <Group size={17} />}
            </button>
          )}
          <button
            onClick={() => zoom(0.8)}
            aria-label="缩小图谱"
            disabled={canvas.view.zoom <= MIN_GRAPH_ZOOM}
          >
            <Minus size={17} />
          </button>
          <output ref={zoomOutputRef} aria-label="图谱缩放比例">
            {Math.round(canvas.view.zoom * 100)}%
          </output>
          <button
            onClick={() => zoom(1.25)}
            aria-label="放大图谱"
            disabled={canvas.view.zoom >= MAX_GRAPH_ZOOM}
          >
            <Plus size={17} />
          </button>
          <button
            onClick={() => fit()}
            aria-label="适合全部可见节点"
            title="适应可见图谱 Shift+F"
          >
            <Maximize2 size={17} />
          </button>
          <button
            onClick={() => fit(true)}
            aria-label="适合所选节点"
            disabled={!chosen.length}
            title="适应所选 F"
          >
            <Focus size={17} />
          </button>
          <button
            onClick={() => setShowMini(!showMini)}
            aria-label="显示导航小地图"
            aria-pressed={showMini}
          >
            <MapIcon size={17} />
          </button>
          <div className="graph-export-control">
            <button
              onClick={() => setShowExport(!showExport)}
              aria-label="导出可见子图"
              aria-expanded={showExport}
            >
              <Download size={17} />
            </button>
            {showExport && (
              <div className="graph-export-menu">
                <p>
                  当前已加载可见子图，含当前聚合与分组。未加载、筛除及隐藏内容不在导出范围。
                </p>
                <button onClick={() => void exportGraph("svg")}>
                  导出 SVG
                </button>
                <button onClick={() => void exportGraph("png")}>
                  导出 PNG
                </button>
              </div>
            )}
          </div>
          <button
            onClick={() => setShowHelp(!showHelp)}
            aria-label="图谱操作帮助"
            aria-expanded={showHelp}
          >
            <HelpCircle size={17} />
          </button>
        </div>
      </div>
      {chosen.length > 1 && (
        <div
          className="graph-multiselect-bar"
          role="toolbar"
          aria-label="多选节点操作"
        >
          <strong>已选 {chosen.length} 项</strong>
          <button onClick={pinNodes}>
            <Pin size={14} />
            固定
          </button>
          <button onClick={hideNodes}>
            <EyeOff size={14} />
            隐藏
          </button>
          {onGroupNodes && (
            <button onClick={() => onGroupNodes(chosen)}>
              <Group size={14} />
              分组
            </button>
          )}
          {onCompareNodes && (
            <button onClick={() => onCompareNodes(chosen)}>
              <Layers3 size={14} />
              比较
            </button>
          )}
          <button onClick={() => select([])} aria-label="清除多选">
            <X size={15} />
          </button>
        </div>
      )}
      <p
        id={`${uid}-instructions`}
        className={showHelp ? "graph-canvas-help" : "sr-only"}
      >
        滚轮在指针处缩放；拖动空白或按住空格平移；触屏双指缩放。关系布局按连接聚集，拖动节点时邻近关联对象随之调整，固定节点保持不动。位置接近不代表新增事实。Shift
        点击或 Shift 拖动空白框选。F 适应所选，Shift+F 适应全图，E 展开所选，P
        固定，Escape 取消。方向键移动焦点，Enter 选择，R
        进入关联边，边上左右切换、Escape
        返回节点。多条平行边可点数量展开。布局与隐藏仅改变当前视图。
      </p>
      <div
        className={`graph-canvas-surface${dragging ? " is-dragging" : ""}`}
        ref={hostRef}
        style={{ "--graph-label-zoom": canvas.view.zoom } as CSSProperties}
        onPointerDown={pointerDown}
        onPointerMove={pointerMove}
        onPointerUp={pointerUp}
        onPointerCancel={(event) => {
          touches.current.delete(event.pointerId);
          pinch.current = null;
          const action = gesture.current;
          stopPhysics();
          if (action?.kind === "node" && action.moved)
            live.current = {
              ...live.current,
              positions: { ...live.current.positions, ...action.originals },
            };
          gesture.current = null;
          setDragging(false);
          setBox(null);
          flush();
        }}
        onContextMenu={(event) => {
          if (
            (event.target as Element).closest(
              ".graph-minimap,.graph-context-menu",
            )
          )
            return;
          event.preventDefault();
          const id = (event.target as Element)
              .closest("[data-node-id]")
              ?.getAttribute("data-node-id"),
            node = id ? byId.get(id) : undefined;
          if (node && !chosenSet.has(node.id)) select([node.id], node);
          const point = localPoint(event);
          setMenu({
            point: {
              x: Math.max(4, Math.min(point.x, size.width - 220)),
              y: Math.max(4, Math.min(point.y, size.height - 290)),
            },
            node,
          });
        }}
      >
        {!visible.length ? (
          <div className="graph-canvas-empty">
            <SearchCheck size={30} aria-hidden="true" />
            <p>{emptyMessage}</p>
          </div>
        ) : (
          <div
            ref={cameraLayerRef}
            className="graph-camera-layer"
            style={{
              transform: `translate(${canvas.view.x}px, ${canvas.view.y}px) scale(${canvas.view.zoom})`,
            }}
          >
            <svg
              ref={svgRef}
              className="graph-svg"
              viewBox={`0 0 ${size.width} ${size.height}`}
              role="group"
              aria-label="知识图谱节点与关联"
              aria-describedby={`${uid}-instructions`}
            >
              <defs>
                <marker
                  id={`${uid}-arrow`}
                  viewBox="0 0 10 10"
                  refX="9"
                  refY="5"
                  markerWidth="5"
                  markerHeight="5"
                  orient="auto-start-reverse"
                >
                  <path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke" />
                </marker>
                <pattern
                  id={`${uid}-dots`}
                  width="24"
                  height="24"
                  patternUnits="userSpaceOnUse"
                >
                  <circle cx="1" cy="1" r="1" fill="#d2dbd4" />
                </pattern>
              </defs>
              <rect
                className="graph-background-grid"
                width={size.width}
                height={size.height}
                fill={`url(#${uid}-dots)`}
                data-export-exclude="true"
              />
              <g
                ref={worldRef}
                className="graph-world"
                style={{ transform: "none" }}
                transform={`translate(${canvas.view.x} ${canvas.view.y}) scale(${canvas.view.zoom})`}
              >
                {drawings.map((drawing) => {
                  const { edge, members, collapsed } = drawing,
                    selected = selectedEdgeId === edge.id,
                    highlighted =
                      selected ||
                      chosenSet.has(edge.source) ||
                      chosenSet.has(edge.target) ||
                      hoverId === edge.source ||
                      hoverId === edge.target,
                    count = collapsed
                      ? members.reduce(
                          (sum, member) => sum + (member.count ?? 1),
                          0,
                        )
                      : (edge.count ?? 1),
                    description = collapsed
                      ? `${members.length} 条平行关系，点击展开`
                      : `${byId.get(edge.source)!.label} → ${edge.label} → ${byId.get(edge.target)!.label}${edge.disabled ? "，定义已停用，历史内容保留" : ""}${count > 1 ? `，${count} 条关系汇总` : ""}`,
                    showLabel =
                      selected ||
                      highlighted ||
                      (!compact && !medium && drawings.length <= 55) ||
                      collapsed ||
                      !!edge.disabled;
                  return (
                    <GraphEdgeGlyph
                      key={edge.id}
                      drawing={drawing}
                      description={description}
                      className={`graph-edge graph-edge-${edge.kind}${highlighted ? " is-highlighted" : ""}${selected ? " is-selected" : ""}${emphasized && (!emphasized.has(edge.source) || !emphasized.has(edge.target)) ? " is-muted" : ""}${collapsed ? " is-bundle" : ""}${edge.disabled ? " is-disabled" : ""}`}
                      selected={selected}
                      showLabel={showLabel}
                      compact={compact}
                      zoom={canvas.view.zoom}
                      markerId={`${uid}-arrow`}
                      attachRef={stableEdgeRef(edge.id)}
                      onActivate={activateEdge}
                      onKey={keyEdge}
                    />
                  );
                })}
                {visible.map((node) => {
                  const selected = chosenSet.has(node.id),
                    focused = focusId === node.id,
                    hovered = hoverId === node.id,
                    pinned = canvas.pinnedIds.includes(node.id);
                  const color =
                    GRAPH_TYPE_COLORS[node.type] ?? GRAPH_TYPE_COLORS.unknown;
                  const collection =
                      node.layer === "aggregate" || node.layer === "group",
                    showLabel =
                      selected ||
                      hovered ||
                      spotlightId === node.id ||
                      node.disabled ||
                      (!compact &&
                        (!medium || priorityIds.has(node.id) || collection));
                  return (
                    <g
                      key={node.id}
                      ref={stableNodeRef(node.id)}
                      transform={`translate(${node.x} ${node.y})`}
                      className={`graph-node graph-node-${node.layer}${selected ? " is-selected" : ""}${focused ? " is-focused" : ""}${pinned ? " is-pinned" : ""}${searchSet.has(node.id) ? " is-search-match" : ""}${spotlightId === node.id ? " is-spotlight" : ""}${emphasized && !emphasized.has(node.id) ? " is-muted" : ""}${node.disabled ? " is-disabled" : ""}`}
                      style={
                        {
                          "--graph-fill": color.fill,
                          "--graph-stroke": color.stroke,
                          "--graph-ink": color.ink,
                        } as CSSProperties
                      }
                      data-node-id={node.id}
                      data-graph-interactive="true"
                      role="button"
                      aria-label={`${node.label}，${GRAPH_TYPE_LABELS[node.type] ?? node.type}，${GRAPH_LAYER_LABELS[node.layer]}${node.disabled ? "，定义已停用，历史内容保留" : ""}${node.count ? `，${node.count} 项` : ""}${pinned ? "，已固定" : ""}`}
                      aria-pressed={selected}
                      tabIndex={activeId === node.id ? 0 : -1}
                      onFocus={() => {
                        setActiveId(node.id);
                        reveal(node);
                      }}
                      onKeyDown={(event) => keyNode(event, node)}
                      onPointerEnter={(event) => hoverNode(event, node)}
                      onPointerLeave={leaveHover}
                    >
                      <GraphNodeArtwork
                        node={artworkNodes.get(node.id) ?? node}
                        selected={selected}
                        focused={focused}
                        pinned={pinned}
                        compact={compact}
                        medium={medium}
                        zoom={canvas.view.zoom}
                        showLabel={showLabel}
                      />
                    </g>
                  );
                })}
              </g>
              {box && (
                <rect
                  className="graph-selection-box"
                  x={Math.min(box.start.x, box.current.x)}
                  y={Math.min(box.start.y, box.current.y)}
                  width={Math.abs(box.start.x - box.current.x)}
                  height={Math.abs(box.start.y - box.current.y)}
                  data-export-exclude="true"
                  transform={`translate(${-canvas.view.x / canvas.view.zoom} ${-canvas.view.y / canvas.view.zoom}) scale(${1 / canvas.view.zoom})`}
                />
              )}
            </svg>
          </div>
        )}
        {tooltip && (
          <div
            className="graph-hover-card"
            role="tooltip"
            style={{
              left: Math.max(
                10,
                Math.min(tooltip.point.x + 15, size.width - 280),
              ),
              top: Math.max(
                10,
                Math.min(tooltip.point.y + 18, size.height - 145),
              ),
            }}
          >
            <strong>{tooltip.title}</strong>
            <p>{tooltip.text}</p>
          </div>
        )}
        {showMini && visible.length > 5 && (
          <div className="graph-minimap">
            <span>导航</span>
            <svg
              viewBox="0 0 180 130"
              role="img"
              aria-label="图谱导航小地图，点击移动视图"
              onPointerDown={(event) => {
                event.preventDefault();
                event.currentTarget.setPointerCapture(event.pointerId);
                moveMini(event);
              }}
              onPointerMove={(event) => {
                if (event.buttons === 1) moveMini(event);
              }}
              onPointerUp={() => flush()}
            >
              {validEdges.map((edge) => (
                <line
                  key={edge.id}
                  x1={miniX(byId.get(edge.source)!.x)}
                  y1={miniY(byId.get(edge.source)!.y)}
                  x2={miniX(byId.get(edge.target)!.x)}
                  y2={miniY(byId.get(edge.target)!.y)}
                />
              ))}
              {visible.map((node) => (
                <circle
                  key={node.id}
                  cx={miniX(node.x)}
                  cy={miniY(node.y)}
                  r={chosenSet.has(node.id) ? 2.8 : 1.7}
                  className={chosenSet.has(node.id) ? "selected" : ""}
                />
              ))}
              <rect
                ref={miniViewportRef}
                x={miniX(-canvas.view.x / canvas.view.zoom)}
                y={miniY(-canvas.view.y / canvas.view.zoom)}
                width={(size.width / canvas.view.zoom) * miniScale}
                height={(size.height / canvas.view.zoom) * miniScale}
              />
            </svg>
          </div>
        )}
        {menu && (
          <div
            ref={menuRef}
            className="graph-context-menu"
            role="menu"
            aria-label="图谱上下文操作"
            style={{ left: menu.point.x, top: menu.point.y }}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.stopPropagation();
                setMenu(null);
                if (menu.node) nodeRefs.current.get(menu.node.id)?.focus();
                else canvasRef.current?.focus();
              } else if (
                ["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)
              ) {
                event.preventDefault();
                event.stopPropagation();
                const items = [
                  ...event.currentTarget.querySelectorAll<HTMLButtonElement>(
                    '[role="menuitem"]',
                  ),
                ];
                const current = items.indexOf(
                  document.activeElement as HTMLButtonElement,
                );
                const index =
                  event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? items.length - 1
                      : (current +
                          (event.key === "ArrowDown" ? 1 : items.length - 1)) %
                        items.length;
                items[index]?.focus();
              }
            }}
          >
            <div className="graph-context-heading">
              <strong>
                {menu.node
                  ? label(menu.node.label, 19)
                  : chosen.length
                    ? `已选 ${chosen.length} 项`
                    : "当前视图"}
              </strong>
              <button onClick={() => setMenu(null)} aria-label="关闭上下文菜单">
                <X size={14} />
              </button>
            </div>
            {(menu.node || chosen.length === 1) && (
              <button
                role="menuitem"
                onClick={() => {
                  const node = menu.node ?? byId.get(chosen[0]);
                  if (node) onFocusNode(node);
                  setMenu(null);
                }}
              >
                <GitBranch size={15} />
                展开一跳关联<kbd>E</kbd>
              </button>
            )}
            {chosen.length > 0 && (
              <>
                <button role="menuitem" onClick={pinNodes}>
                  <Pin size={15} />
                  {chosen.every((id) => canvas.pinnedIds.includes(id))
                    ? "取消固定"
                    : "固定所选"}
                  <kbd>P</kbd>
                </button>
                <button role="menuitem" onClick={hideNodes}>
                  <EyeOff size={15} />
                  从当前视图隐藏
                </button>
                <button
                  role="menuitem"
                  onClick={() => {
                    fit(true);
                    setMenu(null);
                  }}
                >
                  <Focus size={15} />
                  适应所选<kbd>F</kbd>
                </button>
              </>
            )}
            {chosen.length > 1 && onGroupNodes && (
              <button
                role="menuitem"
                onClick={() => {
                  onGroupNodes(chosen);
                  setMenu(null);
                }}
              >
                <Group size={15} />
                建立局部分组
              </button>
            )}
            {chosen.length > 1 && onCompareNodes && (
              <button
                role="menuitem"
                onClick={() => {
                  onCompareNodes(chosen);
                  setMenu(null);
                }}
              >
                <Layers3 size={15} />
                比较所选
              </button>
            )}
            <button
              role="menuitem"
              onClick={() => {
                fit();
                setMenu(null);
              }}
            >
              <Maximize2 size={15} />
              适应可见图谱<kbd>⇧ F</kbd>
            </button>
          </div>
        )}
        {expandedBundles.size > 0 && (
          <button
            className="graph-collapse-bundles"
            onClick={() => setExpandedBundles(new Set())}
          >
            <ChevronUp size={13} />
            收起平行关系
          </button>
        )}
        <div className="graph-canvas-hint" aria-hidden="true">
          <span>滚轮缩放</span>
          <span>Shift 框选</span>
          <span>双击展开</span>
          {compact && <span>概览</span>}
        </div>
      </div>
      <div className="sr-only" role="status" aria-live="polite">
        {announcement ||
          (layingOut ? "正在计算图谱布局" : `${visible.length} 个可见节点`)}
      </div>
    </section>
  );
}
