import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent, PointerEvent } from "react";
import {
  BookOpen,
  CalendarDays,
  Fingerprint,
  Focus,
  GitBranch,
  HelpCircle,
  Layers3,
  MapPin,
  Maximize2,
  Minus,
  Plus,
  RotateCcw,
  SearchCheck,
  Sparkles,
  UserRound,
} from "lucide-react";
import {
  directionalNode,
  GRAPH_LAYER_LABELS,
  GRAPH_TYPE_COLORS,
  GRAPH_TYPE_LABELS,
  graphBounds,
  graphEdgeGeometry,
  layoutFocusedGraph,
  layoutGraph,
} from "./graphModel";
import type {
  GraphBounds,
  GraphEdge,
  GraphNode,
  PositionedGraphNode,
} from "./graphModel";
import "./graph.css";

export interface GraphCanvasProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** Supply unfiltered nodes so visibility changes preserve the layout. */
  layoutNodes?: GraphNode[];
  selectedId?: string | null;
  selectedEdgeId?: string | null;
  focusId?: string | null;
  onSelectNode: (node: GraphNode) => void;
  onSelectEdge: (edge: GraphEdge) => void;
  onFocusNode: (node: GraphNode) => void;
  emptyMessage?: string;
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
const MIN_ZOOM = 0.08;
const MAX_ZOOM = 2.4;
const clampZoom = (zoom: number) =>
  Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom));

function shortLabel(label: string, limit = 14): string {
  const chars = Array.from(label);
  return chars.length > limit
    ? `${chars.slice(0, limit - 1).join("")}…`
    : label;
}

function fitView(
  bounds: GraphBounds,
  width: number,
  height: number,
  maximum = 1.2,
  padding = 72,
) {
  const zoom = clampZoom(
    Math.min(
      (width - padding) / Math.max(1, bounds.width),
      (height - 92) / Math.max(1, bounds.height),
      maximum,
    ),
  );
  return {
    zoom,
    x: width / 2 - (bounds.x + bounds.width / 2) * zoom,
    y: height / 2 - (bounds.y + bounds.height / 2) * zoom,
  };
}

export default function GraphCanvas({
  nodes,
  edges,
  layoutNodes,
  selectedId,
  selectedEdgeId,
  focusId,
  onSelectNode,
  onSelectEdge,
  onFocusNode,
  emptyMessage = "当前筛选下没有节点。调整类型或图层，或清除搜索。",
}: GraphCanvasProps) {
  const uid = useId().replace(/:/g, "");
  const instructionsId = `${uid}-instructions`;
  const svgRef = useRef<SVGSVGElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const nodeRefs = useRef(new Map<string, SVGGElement>());
  const edgeRefs = useRef(new Map<string, SVGGElement>());
  const drag = useRef<{
    x: number;
    y: number;
    startX: number;
    startY: number;
    moved: boolean;
  } | null>(null);
  const [size, setSize] = useState({ width: 960, height: 580 });
  const [view, setView] = useState({ x: 0, y: 0, zoom: 1 });
  const [activeId, setActiveId] = useState<string | null>(selectedId || null);
  const [dragging, setDragging] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const previousSize = useRef(size);
  const overviewLayout = useMemo(
    () => layoutGraph(layoutNodes || nodes),
    [layoutNodes, nodes],
  );
  const layout = useMemo(
    () =>
      focusId && nodes.some((node) => node.id === focusId)
        ? layoutFocusedGraph(nodes, focusId)
        : overviewLayout,
    [focusId, nodes, overviewLayout],
  );
  const visible = useMemo(
    () =>
      nodes
        .map((node) => layout.byId.get(node.id))
        .filter((node): node is PositionedGraphNode => !!node),
    [nodes, layout],
  );
  const visibleIds = useMemo(
    () => new Set(visible.map((node) => node.id)),
    [visible],
  );
  const validEdges = useMemo(
    () =>
      edges.filter(
        (edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target),
      ),
    [edges, visibleIds],
  );
  const layoutKey = `${focusId || "overview"}\u0000${layout.nodes.map((node) => node.id).join("\u0000")}`;
  const bounds = useMemo(() => graphBounds(visible), [visible]);
  const previousLayout = useRef("");

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width > 0 && height > 0) setSize({ width, height });
    });
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (previousLayout.current !== layoutKey) {
      previousLayout.current = layoutKey;
      setView(fitView(bounds, size.width, size.height));
    }
  }, [layoutKey, bounds, size.width, size.height]);

  useEffect(() => {
    // Preserve the centre of the view when a side panel or viewport changes size.
    setView((current) => ({
      ...current,
      x: current.x + (size.width - previousSize.current.width) / 2,
      y: current.y + (size.height - previousSize.current.height) / 2,
    }));
    previousSize.current = size;
  }, [size]);

  useEffect(() => {
    if (selectedId && visibleIds.has(selectedId)) setActiveId(selectedId);
  }, [selectedId, visibleIds]);

  useEffect(() => {
    if (!activeId || !visibleIds.has(activeId))
      setActiveId(visible[0]?.id || null);
  }, [visible, visibleIds, activeId]);

  useEffect(() => {
    if (!focusId || !visibleIds.has(focusId)) return;
    setView(fitView(bounds, size.width, size.height, 1.4, 48));
  }, [focusId, visibleIds, bounds, size]);

  const zoomAt = (factor: number, x = size.width / 2, y = size.height / 2) => {
    setView((current) => {
      const zoom = clampZoom(current.zoom * factor);
      const ratio = zoom / current.zoom;
      return {
        zoom,
        x: x - (x - current.x) * ratio,
        y: y - (y - current.y) * ratio,
      };
    });
  };
  const pointerDown = (event: PointerEvent<SVGSVGElement>) => {
    if (
      event.button !== 0 ||
      (event.target as Element).closest("[data-graph-interactive]")
    )
      return;
    drag.current = {
      x: event.clientX,
      y: event.clientY,
      startX: view.x,
      startY: view.y,
      moved: false,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const pointerMove = (event: PointerEvent<SVGSVGElement>) => {
    if (!drag.current) return;
    const dx = event.clientX - drag.current.x;
    const dy = event.clientY - drag.current.y;
    if (Math.hypot(dx, dy) > 3) {
      drag.current.moved = true;
      setDragging(true);
    }
    setView((current) => ({
      ...current,
      x: drag.current!.startX + dx,
      y: drag.current!.startY + dy,
    }));
  };
  const pointerUp = () => {
    drag.current = null;
    setDragging(false);
  };
  const revealNode = (node: PositionedGraphNode) => {
    const screenX = node.x * view.zoom + view.x;
    const screenY = node.y * view.zoom + view.y;
    if (
      screenX < 70 ||
      screenX > size.width - 70 ||
      screenY < 60 ||
      screenY > size.height - 80
    ) {
      setView((current) => ({
        ...current,
        x: size.width / 2 - node.x * current.zoom,
        y: size.height / 2 - node.y * current.zoom,
      }));
    }
  };
  const keyNode = (
    event: KeyboardEvent<SVGGElement>,
    node: PositionedGraphNode,
  ) => {
    const key = event.key;
    let next: PositionedGraphNode | undefined;
    if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(key))
      next = directionalNode(
        node,
        visible,
        key as "ArrowLeft" | "ArrowRight" | "ArrowUp" | "ArrowDown",
      );
    else if (key === "Home") next = visible[0];
    else if (key === "End") next = visible[visible.length - 1];
    else if (key === "Enter" || key === " ") {
      event.preventDefault();
      onSelectNode(node);
      return;
    } else if (key.toLowerCase() === "f") {
      event.preventDefault();
      onFocusNode(node);
      return;
    } else if (key.toLowerCase() === "r") {
      const first = validEdges.find(
        (edge) => edge.source === node.id || edge.target === node.id,
      );
      if (first) {
        event.preventDefault();
        edgeRefs.current.get(first.id)?.focus();
        onSelectEdge(first);
      }
      return;
    } else return;
    event.preventDefault();
    if (next) {
      setActiveId(next.id);
      revealNode(next);
      nodeRefs.current.get(next.id)?.focus();
    }
  };
  const keyEdge = (event: KeyboardEvent<SVGGElement>, edge: GraphEdge) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelectEdge(edge);
    } else if (event.key === "Escape") {
      event.preventDefault();
      nodeRefs.current.get(edge.source)?.focus();
    } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      const related = validEdges.filter(
        (item) => item.source === edge.source || item.target === edge.source,
      );
      const index = related.findIndex((item) => item.id === edge.id);
      const next =
        related[
          (index + (event.key === "ArrowRight" ? 1 : related.length - 1)) %
            related.length
        ];
      if (next) {
        edgeRefs.current.get(next.id)?.focus();
        onSelectEdge(next);
      }
    }
  };
  const parallel = new Map<string, GraphEdge[]>();
  validEdges.forEach((edge) => {
    const key = [edge.source, edge.target].sort().join("\u0000");
    parallel.set(key, [...(parallel.get(key) || []), edge]);
  });
  parallel.forEach((items) =>
    items.sort((a, b) => a.id.localeCompare(b.id, "en")),
  );

  return (
    <section
      className={`graph-canvas${focusId ? " is-focused" : ""}`}
      aria-label="交互知识图谱"
    >
      <div className="graph-canvas-toolbar">
        <div
          className="graph-canvas-controls"
          role="group"
          aria-label="图谱视图控制"
        >
          <button
            type="button"
            onClick={() => zoomAt(1 / 1.25)}
            aria-label="缩小图谱"
            disabled={view.zoom <= MIN_ZOOM}
          >
            <Minus size={18} aria-hidden="true" />
          </button>
          <output aria-label="图谱缩放比例">
            {Math.round(view.zoom * 100)}%
          </output>
          <button
            type="button"
            onClick={() => zoomAt(1.25)}
            aria-label="放大图谱"
            disabled={view.zoom >= MAX_ZOOM}
          >
            <Plus size={18} aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={() => setView(fitView(bounds, size.width, size.height))}
            aria-label="适合全部可见节点"
          >
            <Maximize2 size={18} aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={() =>
              setView(fitView(layout.bounds, size.width, size.height))
            }
            aria-label="重置图谱视图"
          >
            <RotateCcw size={18} aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={() => setShowHelp(!showHelp)}
            aria-label="图谱操作帮助"
            aria-expanded={showHelp}
          >
            <HelpCircle size={18} aria-hidden="true" />
          </button>
        </div>
      </div>
      <p
        id={instructionsId}
        className={showHelp ? "graph-canvas-help" : "sr-only"}
      >
        拖动空白处移动视图，使用加减按钮缩放。点击节点或连线查看详情；双击节点聚焦一跳关联。键盘进入节点后，可用方向键移动、Home
        或 End 跳至首尾、Enter 选择、F 聚焦、R
        查看关联连线；在线上用左右键切换，Escape 返回节点。也可使用列表视图。
      </p>
      <div
        ref={hostRef}
        className={`graph-canvas-surface${dragging ? " is-dragging" : ""}`}
      >
        {!visible.length ? (
          <div className="graph-canvas-empty">
            <SearchCheck size={30} aria-hidden="true" />
            <p>{emptyMessage}</p>
          </div>
        ) : (
          <svg
            ref={svgRef}
            className="graph-svg"
            viewBox={`0 0 ${size.width} ${size.height}`}
            aria-label="知识图谱节点与关联"
            role="group"
            aria-describedby={instructionsId}
            onPointerDown={pointerDown}
            onPointerMove={pointerMove}
            onPointerUp={pointerUp}
            onPointerCancel={pointerUp}
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
                <circle cx="1" cy="1" r="1" fill="#c6d2c9" />
              </pattern>
            </defs>
            <rect
              width={size.width}
              height={size.height}
              fill={`url(#${uid}-dots)`}
            />
            <g transform={`translate(${view.x} ${view.y}) scale(${view.zoom})`}>
              {validEdges.map((edge) => {
                const source = layout.byId.get(edge.source)!;
                const target = layout.byId.get(edge.target)!;
                const siblings = parallel.get(
                  [edge.source, edge.target].sort().join("\u0000"),
                ) || [edge];
                const index = siblings.findIndex((item) => item.id === edge.id);
                const bend =
                  (index - (siblings.length - 1) / 2) * 48 +
                  (edge.kind === "definition" ? 18 : 0);
                const path = graphEdgeGeometry(source, target, bend, index);
                const selected = selectedEdgeId === edge.id;
                const highlighted =
                  selected ||
                  selectedId === edge.source ||
                  selectedId === edge.target;
                const description = `${source.label} → ${edge.label} → ${target.label}${edge.kind === "definition" ? "；类型规则，不表示已发生" : edge.kind === "candidate" ? "；待审候选，尚未保存" : ""}`;
                return (
                  <g
                    key={edge.id}
                    ref={(element) => {
                      if (element) edgeRefs.current.set(edge.id, element);
                      else edgeRefs.current.delete(edge.id);
                    }}
                    className={`graph-edge graph-edge-${edge.kind}${highlighted ? " is-highlighted" : ""}${selected ? " is-selected" : ""}`}
                    role="button"
                    tabIndex={-1}
                    aria-label={description}
                    aria-pressed={selected}
                    data-graph-interactive="true"
                    onClick={() => onSelectEdge(edge)}
                    onKeyDown={(event) => keyEdge(event, edge)}
                  >
                    <title>{description}</title>
                    <path className="graph-edge-hit" d={path.d} />
                    <path
                      className="graph-edge-line"
                      d={path.d}
                      markerEnd={`url(#${uid}-arrow)`}
                    />
                    {(selected ||
                      (edge.kind !== "instance" &&
                        (highlighted ||
                          (view.zoom >= 0.65 &&
                            validEdges.length <= 45)))) && (
                      <text
                        className="graph-edge-label"
                        x={path.x}
                        y={path.y - 7}
                        textAnchor="middle"
                      >
                        {shortLabel(edge.label)}
                      </text>
                    )}
                  </g>
                );
              })}
              {visible.map((node) => {
                const selected = selectedId === node.id;
                const focused = focusId === node.id;
                const color =
                  GRAPH_TYPE_COLORS[node.type] || GRAPH_TYPE_COLORS.unknown;
                const Icon =
                  node.layer === "review"
                    ? SearchCheck
                    : TYPE_ICONS[node.type] || BookOpen;
                const schema = node.layer === "schema";
                const review = node.layer === "review";
                return (
                  <g
                    key={node.id}
                    ref={(element) => {
                      if (element) nodeRefs.current.set(node.id, element);
                      else nodeRefs.current.delete(node.id);
                    }}
                    transform={`translate(${node.x} ${node.y})`}
                    className={`graph-node graph-node-${node.layer}${selected ? " is-selected" : ""}${focused ? " is-focused" : ""}`}
                    style={
                      {
                        "--graph-fill": color.fill,
                        "--graph-stroke": color.stroke,
                        "--graph-ink": color.ink,
                      } as CSSProperties
                    }
                    role="button"
                    aria-label={`${node.label}，${GRAPH_TYPE_LABELS[node.type] || node.type}，${GRAPH_LAYER_LABELS[node.layer]}${focused ? "，当前聚焦" : ""}`}
                    aria-pressed={selected}
                    tabIndex={activeId === node.id ? 0 : -1}
                    data-graph-interactive="true"
                    onFocus={() => {
                      setActiveId(node.id);
                      revealNode(node);
                    }}
                    onClick={() => {
                      setActiveId(node.id);
                      onSelectNode(node);
                    }}
                    onDoubleClick={() => onFocusNode(node)}
                    onKeyDown={(event) => keyNode(event, node)}
                  >
                    <title>
                      {node.label} · {GRAPH_LAYER_LABELS[node.layer]}
                      {node.description ? `\n${node.description}` : ""}
                    </title>
                    {schema ? (
                      <rect
                        className="graph-node-halo"
                        x="-78"
                        y="-36"
                        width="156"
                        height="72"
                        rx="17"
                      />
                    ) : (
                      <circle className="graph-node-halo" r="37" />
                    )}
                    {schema ? (
                      <rect
                        className="graph-node-shape"
                        x="-71"
                        y="-29"
                        width="142"
                        height="58"
                        rx="12"
                      />
                    ) : review ? (
                      <path
                        className="graph-node-shape"
                        d="M 0 -33 L 33 0 L 0 33 L -33 0 Z"
                      />
                    ) : (
                      <circle className="graph-node-shape" r="27" />
                    )}
                    <Icon
                      x={schema ? -10 : -11}
                      y={schema ? -23 : -11}
                      size={schema ? 20 : 22}
                      strokeWidth={1.8}
                      className="graph-node-icon"
                      aria-hidden="true"
                    />
                    <text
                      className="graph-node-label"
                      y={schema ? 15 : 48}
                      textAnchor="middle"
                    >
                      {shortLabel(node.label, schema ? 10 : focusId ? 12 : 14)}
                    </text>
                    <text
                      className="graph-node-layer"
                      y={schema ? 49 : 66}
                      textAnchor="middle"
                    >
                      {GRAPH_LAYER_LABELS[node.layer]}
                    </text>
                    {focused && (
                      <Focus
                        x={schema ? 52 : 18}
                        y={schema ? -30 : -31}
                        size={17}
                        className="graph-node-focus-icon"
                        aria-hidden="true"
                      />
                    )}
                  </g>
                );
              })}
            </g>
          </svg>
        )}
      </div>
    </section>
  );
}
