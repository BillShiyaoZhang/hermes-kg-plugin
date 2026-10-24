import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent, ReactNode } from "react";
import {
  AlertCircle,
  ArrowDown,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Database,
  Fingerprint,
  Info,
  Layers3,
  LoaderCircle,
  MapPin,
  Network,
  RefreshCw,
  Search,
  Sparkles,
  UserRound,
  X,
} from "lucide-react";
import type { ConsoleApi } from "./api";
import { errorMessage, formatDate, textValue, typeLabels } from "./lib";
import {
  describeTypes,
  displayCount,
  knownCount,
  pageRange,
  relationLabel,
  relationTouchesType,
} from "./ontologyHelpers";
import type {
  OntologyEntityDefinition,
  OntologyEntityKey,
  OntologyEntityPage,
  OntologyEntityRecord,
  OntologyPage,
  OntologyProperty,
  OntologyRelationDefinition,
  OntologyRelationPage,
  OntologyRelationRecord,
  OntologySchema,
} from "./ontologyTypes";
import "./ontology.css";
import PendingFeedback from "./PendingFeedback";

type View = "structure" | "data";
type DataKind = "entities" | "relations";
type DataPage = OntologyEntityPage | OntologyRelationPage;
type SchemaState = {
  value: OntologySchema | null;
  loading: boolean;
  error: unknown;
  loadedAt: string | null;
};
type DataState = {
  key: string;
  page: DataPage | null;
  loading: boolean;
  error: unknown;
};
const PAGE_SIZE = 20;
const TYPE_ICONS: Record<string, typeof BookOpen> = {
  person: UserRound,
  event: CalendarDays,
  preference: Sparkles,
  decision: Fingerprint,
  location: MapPin,
  concept: BookOpen,
};
const TYPE_ORDER: OntologyEntityKey[] = [
  "person",
  "event",
  "preference",
  "decision",
  "location",
  "concept",
];
const TYPE_DESCRIPTIONS: Record<OntologyEntityKey, string> = {
  person: "人物与已确认的身份",
  event: "发生或计划发生的事情",
  preference: "明确表达的喜好与习惯",
  decision: "作出的选择及其背景",
  location: "与记忆有关的地点",
  concept: "项目、主题与领域概念",
};
const entityLabel = (key: string) =>
  typeLabels[key as keyof typeof typeLabels] || key;
const isRuntimeType = (key: string): key is OntologyEntityKey =>
  TYPE_ORDER.includes(key as OntologyEntityKey);

function Message({
  title,
  children,
  error = false,
  action,
}: {
  title: string;
  children?: ReactNode;
  error?: boolean;
  action?: ReactNode;
}) {
  const Icon = error ? AlertCircle : Info;
  return (
    <div
      className={`ontology-message${error ? " is-error" : ""}`}
      role={error ? "alert" : "status"}
    >
      <Icon size={19} aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        {children && <p>{children}</p>}
      </div>
      {action}
    </div>
  );
}

function Loading({ children }: { children: ReactNode }) {
  return (
    <div className="ontology-loading" role="status">
      <LoaderCircle size={22} className="ontology-spin" aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}

function PropertyList({
  properties,
  type,
}: {
  properties: OntologyProperty[];
  type?: string;
}) {
  if (!properties.length)
    return (
      <p className="ontology-empty-note">
        本体未单独声明关系属性。在实际数据中，可展开记录查看已保存的属性。
      </p>
    );
  return (
    <div className="ontology-properties">
      {properties.map((property) => (
        <details key={property.key} className="ontology-property">
          <summary>
            <span>
              <strong>{property.label || property.key}</strong>
              <code>{property.key}</code>
            </span>
            <span className="ontology-property-flags">
              {type && property.required_for.includes(type) && (
                <span className="ontology-tag">
                  {property.required_condition ? "有条件必需" : "必需"}
                </span>
              )}
              {property.managed && (
                <span className="ontology-tag is-neutral">系统维护</span>
              )}
              <ChevronDown size={15} aria-hidden="true" />
            </span>
          </summary>
          <div className="ontology-property-body">
            <p>{property.description || "未提供属性说明。"}</p>
            {property.required_condition && (
              <p className="ontology-property-condition">
                {property.required_condition}
              </p>
            )}
            <dl className="ontology-definition-list">
              <div>
                <dt>值的类型</dt>
                <dd>
                  {property.range.join("、") || "未声明"}
                  {property.value_shape === "array" ? " · 数组" : ""}
                </dd>
              </div>
              {!!property.allowed_values?.length && (
                <div>
                  <dt>可选值</dt>
                  <dd>
                    {property.allowed_values.map((value) => (
                      <code key={value}>{value}</code>
                    ))}
                  </dd>
                </div>
              )}
              <div>
                <dt>适用类型</dt>
                <dd>{describeTypes(property.domain)}</dd>
              </div>
              {property.aliases.length > 0 && (
                <div>
                  <dt>兼容名称</dt>
                  <dd>
                    {property.aliases.map((alias) => (
                      <code key={alias}>{alias}</code>
                    ))}
                  </dd>
                </div>
              )}
              {!property.runtime_supported && (
                <div>
                  <dt>可写状态</dt>
                  <dd>本体已声明，当前写入接口尚未支持</dd>
                </div>
              )}
              <div>
                <dt>本体标识</dt>
                <dd>
                  <code>{property.iri}</code>
                </dd>
              </div>
            </dl>
          </div>
        </details>
      ))}
    </div>
  );
}

const POSITIONS: Record<OntologyEntityKey, { x: number; y: number }> = {
  person: { x: 125, y: 260 },
  event: { x: 445, y: 112 },
  preference: { x: 445, y: 260 },
  decision: { x: 445, y: 408 },
  location: { x: 775, y: 112 },
  concept: { x: 775, y: 408 },
};

function edgeGeometry(
  source: OntologyEntityKey,
  target: OntologyEntityKey,
  relation: string,
) {
  const a = POSITIONS[source],
    b = POSITIONS[target];
  if (source === target)
    return {
      path: `M ${a.x - 35} ${a.y - 34} C ${a.x - 105} ${a.y - 162}, ${a.x + 105} ${a.y - 162}, ${a.x + 35} ${a.y - 34}`,
      x: a.x,
      y: a.y - 125,
    };
  if (relation === "HERMES_locatedAt" && source === "person")
    return {
      path: `M ${a.x + 78} ${a.y - 18} C 250 5, 650 5, ${b.x - 78} ${b.y - 10}`,
      x: 452,
      y: 40,
    };
  const direction = b.x >= a.x ? 1 : -1;
  return {
    path: `M ${a.x + 78 * direction} ${a.y} C ${a.x + 188 * direction} ${a.y}, ${b.x - 188 * direction} ${b.y}, ${b.x - 78 * direction} ${b.y}`,
    x: (a.x + b.x) / 2,
    y: (a.y + b.y) / 2 - 16,
  };
}

function RelationDiagram({
  entities,
  relations,
  selectedType,
  selectedRelation,
}: {
  entities: OntologyEntityDefinition[];
  relations: OntologyRelationDefinition[];
  selectedType: string;
  selectedRelation: OntologyRelationDefinition | null;
}) {
  const id = useId().replace(/:/g, "");
  const concrete = (selectedRelation ? [selectedRelation] : relations).filter(
    (relation) =>
      !relation.domain.includes("any") && !relation.range.includes("any"),
  );
  const edges = concrete.flatMap((relation) =>
    relation.domain.flatMap((source) =>
      relation.range.flatMap((target) =>
        !isRuntimeType(source) || !isRuntimeType(target)
          ? []
          : [
              {
                relation,
                source,
                target,
                ...edgeGeometry(source, target, relation.key),
              },
            ],
      ),
    ),
  );
  const universal =
    selectedRelation &&
    (selectedRelation.domain.includes("any") ||
      selectedRelation.range.includes("any"));
  const involved = new Set<string>([
    selectedType,
    ...edges.flatMap((edge) => [edge.source, edge.target]),
  ]);
  return (
    <figure className="ontology-diagram">
      <div className="ontology-diagram-title">
        <span>
          <Network size={17} aria-hidden="true" />
          关系示意
        </span>
        <span>
          {selectedRelation
            ? relationLabel(selectedRelation)
            : `${entityLabel(selectedType)}相关关系`}
        </span>
      </div>
      <div
        className="ontology-diagram-scroll"
        tabIndex={0}
        role="region"
        aria-label="实体类型关系示意，可横向滚动"
      >
        <svg
          viewBox="0 0 920 490"
          role="img"
          aria-labelledby={`${id}-title ${id}-description`}
        >
          <title id={`${id}-title`}>
            {selectedRelation
              ? `${relationLabel(selectedRelation)}的起点和终点`
              : `${entityLabel(selectedType)}相关的实体类型关系`}
          </title>
          <desc id={`${id}-description`}>
            方框代表六种实体类型，箭头从关系起点指向终点。类型与关系可通过上方卡片和下方关系表选择；这不是实际实例图。
          </desc>
          <defs>
            <marker
              id={`${id}-arrow`}
              viewBox="0 0 10 10"
              refX="8.5"
              refY="5"
              markerWidth="6"
              markerHeight="6"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" />
            </marker>
          </defs>
          {edges.map((edge) => (
            <g
              key={`${edge.relation.key}-${edge.source}-${edge.target}`}
              className="ontology-graph-edge"
            >
              <path d={edge.path} markerEnd={`url(#${id}-arrow)`} />
              <rect
                x={edge.x - 43}
                y={edge.y - 13}
                width="86"
                height="25"
                rx="12"
              />
              <text x={edge.x} y={edge.y + 4} textAnchor="middle">
                {relationLabel(edge.relation)}
              </text>
            </g>
          ))}
          {universal && (
            <g className="ontology-graph-universal">
              <text x="300" y="42" textAnchor="middle">
                {describeTypes(selectedRelation.domain)}
              </text>
              <path d="M 388 38 L 602 38" markerEnd={`url(#${id}-arrow)`} />
              <text x="495" y="22" textAnchor="middle">
                {relationLabel(selectedRelation)}
              </text>
              <text x="698" y="42" textAnchor="middle">
                {describeTypes(selectedRelation.range)}
              </text>
            </g>
          )}
          {entities.map((entity) => {
            if (!isRuntimeType(entity.key)) return null;
            const position = POSITIONS[entity.key];
            return (
              <g
                key={entity.key}
                transform={`translate(${position.x - 78},${position.y - 34})`}
                className={`ontology-graph-node${selectedType === entity.key ? " is-selected" : ""}${!universal && !involved.has(entity.key) ? " is-dimmed" : ""}`}
              >
                <rect width="156" height="68" rx="14" />
                <text
                  x="78"
                  y="29"
                  textAnchor="middle"
                  className="ontology-graph-node-name"
                >
                  {entityLabel(entity.key)}
                </text>
                <text
                  x="78"
                  y="50"
                  textAnchor="middle"
                  className="ontology-graph-node-count"
                >
                  {knownCount(entity.count)
                    ? `${displayCount(entity.count)} 条可见记忆`
                    : "数量未知"}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      <figcaption>
        箭头表示起点 →
        终点。通用关系适用于任意业务类型；不会增加额外的实体类型。可在下方关系表查看完整定义。
      </figcaption>
    </figure>
  );
}

function RelationDetails({
  relation,
  browse,
}: {
  relation: OntologyRelationDefinition | null;
  browse: () => void;
}) {
  if (!relation)
    return (
      <aside className="ontology-relation-detail is-empty">
        <Network size={28} aria-hidden="true" />
        <h3>选择一种关系</h3>
        <p>查看它连接哪些类型、使用什么名称，以及保存了多少条实际记录。</p>
      </aside>
    );
  const constrained =
    describeTypes(relation.domain) !==
      describeTypes(relation.declared_domain) ||
    describeTypes(relation.range) !== describeTypes(relation.declared_range);
  return (
    <aside
      className="ontology-relation-detail"
      aria-label={`${relationLabel(relation)}的定义`}
    >
      <div className="ontology-detail-heading">
        <span className="ontology-eyebrow">关系定义</span>
        <h3>{relationLabel(relation)}</h3>
        <code>{relation.key}</code>
      </div>
      <p className="ontology-detail-description">
        {relation.description || "未提供关系说明。"}
      </p>
      <div className="ontology-endpoint-flow">
        <div>
          <span>起点 · Domain</span>
          <strong>{describeTypes(relation.domain)}</strong>
        </div>
        <ArrowDown size={18} aria-hidden="true" />
        <div>
          <span>终点 · Range</span>
          <strong>{describeTypes(relation.range)}</strong>
        </div>
      </div>
      <dl className="ontology-definition-list">
        <div>
          <dt>保存名称</dt>
          <dd>
            <code>{relation.storage_type}</code>
          </dd>
        </div>
        <div>
          <dt>可见记录</dt>
          <dd>
            {knownCount(relation.count)
              ? `${displayCount(relation.count)} 条`
              : "数量未知"}
          </dd>
        </div>
        <div>
          <dt>兼容名称</dt>
          <dd>
            {relation.aliases.length
              ? relation.aliases.map((alias) => (
                  <code key={alias}>{alias}</code>
                ))
              : "无其他名称"}
          </dd>
        </div>
      </dl>
      {constrained && (
        <details className="ontology-technical">
          <summary>
            原始本体声明
            <ChevronDown size={14} aria-hidden="true" />
          </summary>
          <p>当前可用范围采用上方约束。本体中的较宽声明保留在这里供核对。</p>
          <dl className="ontology-definition-list">
            <div>
              <dt>Domain</dt>
              <dd>{describeTypes(relation.declared_domain)}</dd>
            </div>
            <div>
              <dt>Range</dt>
              <dd>{describeTypes(relation.declared_range)}</dd>
            </div>
          </dl>
        </details>
      )}
      <div className="ontology-detail-properties">
        <h4>关系属性</h4>
        <PropertyList properties={relation.properties} />
      </div>
      <button
        type="button"
        className="ontology-button is-primary"
        onClick={browse}
        disabled={!relation.runtime_supported}
      >
        查看实际关系
        <ArrowRight size={16} aria-hidden="true" />
      </button>
      <details className="ontology-technical">
        <summary>
          本体标识
          <ChevronDown size={14} aria-hidden="true" />
        </summary>
        <code>{relation.iri}</code>
      </details>
    </aside>
  );
}

function StoredProperties({
  properties,
}: {
  properties: Record<string, unknown>;
}) {
  const entries = Object.entries(properties);
  return entries.length ? (
    <details className="ontology-stored-properties">
      <summary>
        {entries.length} 个属性
        <ChevronDown size={14} aria-hidden="true" />
      </summary>
      <dl>
        {entries.map(([key, value]) => (
          <div key={key}>
            <dt>
              <code>{key}</code>
            </dt>
            <dd>{textValue(value)}</dd>
          </div>
        ))}
      </dl>
    </details>
  ) : (
    <span className="ontology-muted">无附加属性</span>
  );
}

function EntityRows({
  items,
  onSelect,
}: {
  items: OntologyEntityRecord[];
  onSelect: (uri: string) => void;
}) {
  return (
    <table className="ontology-data-table">
      <caption className="ontology-sr-only">当前范围内的实体记录</caption>
      <thead>
        <tr>
          <th scope="col">类型</th>
          <th scope="col">记忆</th>
          <th scope="col" className="ontology-optional-column">
            更新时间
          </th>
          <th scope="col">
            <span className="ontology-sr-only">查看</span>
          </th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => {
          const Icon = TYPE_ICONS[item.type] || Layers3;
          return (
            <tr key={item.uri}>
              <td>
                <span className="ontology-type-tag">
                  <Icon size={16} aria-hidden="true" />
                  {typeLabels[item.type] || item.type}
                </span>
              </td>
              <td>
                <button
                  className="ontology-record-link"
                  type="button"
                  onClick={() => onSelect(item.uri)}
                >
                  <strong>{item.name || "未命名记忆"}</strong>
                </button>
                {item.description && (
                  <p className="ontology-record-description">
                    {item.description}
                  </p>
                )}
                <code className="ontology-record-uri" title={item.uri}>
                  {item.uri}
                </code>
              </td>
              <td className="ontology-optional-column">
                {item.updated_at ? formatDate(item.updated_at) : "未记录"}
              </td>
              <td>
                <button
                  className="ontology-icon-button"
                  type="button"
                  onClick={() => onSelect(item.uri)}
                  aria-label={`查看${item.name || "这条记忆"}的详情`}
                >
                  <ArrowUpRight size={18} aria-hidden="true" />
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function RelationRows({
  items,
  offset,
  onSelect,
  definitions,
}: {
  items: OntologyRelationRecord[];
  offset: number;
  onSelect: (uri: string) => void;
  definitions: OntologyRelationDefinition[];
}) {
  return (
    <table className="ontology-data-table ontology-relation-records">
      <caption className="ontology-sr-only">当前范围内的实际关系</caption>
      <thead>
        <tr>
          <th scope="col">起点</th>
          <th scope="col">关系</th>
          <th scope="col">终点</th>
          <th scope="col">已存属性</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item, index) => {
          const definition = definitions.find(
            (relation) => relation.key === item.relation_type,
          );
          return (
            <tr key={`${offset + index}-${item.source.uri}-${item.target.uri}`}>
              <td>
                <button
                  type="button"
                  className="ontology-record-link"
                  onClick={() => onSelect(item.source.uri)}
                >
                  <strong>{item.source.name || "未命名记忆"}</strong>
                </button>
                <span className="ontology-endpoint-type">
                  {typeLabels[item.source.type]}
                </span>
                <code className="ontology-record-uri" title={item.source.uri}>
                  {item.source.uri}
                </code>
              </td>
              <td>
                <span className="ontology-row-relation">
                  {definition ? relationLabel(definition) : item.relation_type}
                  <ArrowRight size={15} aria-hidden="true" />
                </span>
                <code>{item.stored_type}</code>
                {item.stored_type !== item.relation_type && (
                  <span className="ontology-endpoint-type">已识别兼容名称</span>
                )}
              </td>
              <td>
                <button
                  type="button"
                  className="ontology-record-link"
                  onClick={() => onSelect(item.target.uri)}
                >
                  <strong>{item.target.name || "未命名记忆"}</strong>
                </button>
                <span className="ontology-endpoint-type">
                  {typeLabels[item.target.type]}
                </span>
                <code className="ontology-record-uri" title={item.target.uri}>
                  {item.target.uri}
                </code>
              </td>
              <td>
                <StoredProperties properties={item.properties} />
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export default function OntologyExplorer({
  api,
  enabled,
  refreshKey,
  onSelect,
}: {
  api: ConsoleApi;
  enabled: boolean;
  refreshKey: number;
  onSelect: (uri: string) => void;
}) {
  const instanceId = useId().replace(/:/g, "");
  const dataTabRef = useRef<HTMLButtonElement>(null);
  const dataResultsRef = useRef<HTMLDivElement>(null);
  const pendingPageFocus = useRef<string | null>(null);
  const [view, setView] = useState<View>("structure");
  const [kind, setKind] = useState<DataKind>("entities");
  const [selectedType, setSelectedType] = useState<string>("person");
  const [selectedRelation, setSelectedRelation] = useState<string | null>(null);
  const [showAllRelations, setShowAllRelations] = useState(false);
  const [entityFilter, setEntityFilter] = useState("");
  const [relationFilter, setRelationFilter] = useState("");
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [schemaState, setSchemaState] = useState<SchemaState>({
    value: null,
    loading: false,
    error: null,
    loadedAt: null,
  });
  const [dataState, setDataState] = useState<DataState>({
    key: "",
    page: null,
    loading: false,
    error: null,
  });
  const dataKey = JSON.stringify([
    kind,
    entityFilter,
    relationFilter,
    query,
    offset,
  ]);

  useEffect(() => {
    if (!enabled) {
      setSchemaState({
        value: null,
        loading: false,
        error: null,
        loadedAt: null,
      });
      return;
    }
    const controller = new AbortController();
    setSchemaState((current) => ({ ...current, loading: true, error: null }));
    api
      .ontologySchema(controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        setSchemaState({
          value,
          loading: false,
          error: null,
          loadedAt: new Date().toISOString(),
        });
        setSelectedType((current) =>
          value.entity_types.some((entity) => entity.key === current)
            ? current
            : value.entity_types[0]?.key || "person",
        );
        setSelectedRelation((current) =>
          value.relations.some((relation) => relation.key === current)
            ? current
            : null,
        );
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted)
          setSchemaState((current) => ({ ...current, loading: false, error }));
      });
    return () => controller.abort();
  }, [api, enabled, refreshKey, revision]);

  useEffect(() => {
    if (!enabled) {
      setDataState({ key: "", page: null, loading: false, error: null });
      return;
    }
    if (view !== "data") return;
    const controller = new AbortController();
    setDataState((current) => ({
      key: dataKey,
      page: current.key === dataKey ? current.page : null,
      loading: true,
      error: null,
    }));
    const parameters = { q: query, offset, limit: PAGE_SIZE };
    const request: Promise<DataPage> =
      kind === "entities"
        ? api.ontologyEntities(
            { ...parameters, type: entityFilter },
            controller.signal,
          )
        : api.ontologyRelations(
            { ...parameters, relation: relationFilter },
            controller.signal,
          );
    request
      .then((page) => {
        if (!controller.signal.aborted)
          setDataState({ key: dataKey, page, loading: false, error: null });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted)
          setDataState({ key: dataKey, page: null, loading: false, error });
      });
    return () => controller.abort();
  }, [
    api,
    enabled,
    view,
    kind,
    entityFilter,
    relationFilter,
    query,
    offset,
    refreshKey,
    revision,
    dataKey,
  ]);

  const schema = schemaState.value;
  const entities = useMemo(
    () =>
      [...(schema?.entity_types || [])].sort(
        (a, b) =>
          (isRuntimeType(a.key) ? TYPE_ORDER.indexOf(a.key) : 99) -
          (isRuntimeType(b.key) ? TYPE_ORDER.indexOf(b.key) : 99),
      ),
    [schema],
  );
  const entity = entities.find((definition) => definition.key === selectedType);
  const relation =
    schema?.relations.find(
      (definition) => definition.key === selectedRelation,
    ) || null;
  const related = (schema?.relations || []).filter((definition) =>
    relationTouchesType(definition, selectedType),
  );
  const tableRelations = showAllRelations ? schema?.relations || [] : related;
  const page = dataState.key === dataKey ? dataState.page : null;
  const dataLoading =
    view === "data" && (dataState.loading || dataState.key !== dataKey);
  const busy = schemaState.loading || dataLoading;
  const hasDataFilter =
    !!query || (kind === "entities" ? !!entityFilter : !!relationFilter);
  useEffect(() => {
    const pending = pendingPageFocus.current;
    if (!pending) return;
    if (!enabled || view !== "data" || pending !== dataKey) {
      pendingPageFocus.current = null;
      return;
    }
    if (dataLoading || dataState.key !== pending) return;
    pendingPageFocus.current = null;
    dataResultsRef.current?.focus({ preventScroll: true });
    dataResultsRef.current?.scrollIntoView({
      block: "start",
      behavior: "auto",
    });
  }, [enabled, view, dataKey, dataLoading, dataState.key]);
  const navigatePage = (nextOffset: number) => {
    pendingPageFocus.current = JSON.stringify([
      kind,
      entityFilter,
      relationFilter,
      query,
      nextOffset,
    ]);
    setOffset(nextOffset);
  };
  const refresh = () => setRevision((current) => current + 1);
  const browseType = () => {
    setView("data");
    setKind("entities");
    setEntityFilter(selectedType);
    setDraft("");
    setQuery("");
    setOffset(0);
    dataTabRef.current?.focus();
  };
  const browseRelation = () => {
    setView("data");
    setKind("relations");
    setRelationFilter(relation?.key || "");
    setDraft("");
    setQuery("");
    setOffset(0);
    dataTabRef.current?.focus();
  };
  const changeKind = (value: DataKind) => {
    setKind(value);
    setOffset(0);
  };
  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    setQuery(draft.trim());
    setOffset(0);
  };
  const tabKey = (event: KeyboardEvent<HTMLButtonElement>, current: View) => {
    const next: View | null = ["ArrowLeft", "ArrowRight"].includes(event.key)
      ? current === "structure"
        ? "data"
        : "structure"
      : event.key === "Home"
        ? "structure"
        : event.key === "End"
          ? "data"
          : null;
    if (next) {
      event.preventDefault();
      setView(next);
      document.getElementById(`${instanceId}-tab-${next}`)?.focus();
    }
  };

  return (
    <div className="ontology-page">
      <header className="ontology-hero">
        <div>
          <span className="ontology-eyebrow">ONTOLOGY EXPLORER</span>
          <h1>知识结构</h1>
          <p>了解记忆如何分类、如何连接，再查看它们对应的实际记录。</p>
        </div>
        <button
          type="button"
          className="ontology-button"
          onClick={refresh}
          disabled={!enabled || busy}
        >
          <RefreshCw
            size={16}
            className={busy ? "ontology-spin" : ""}
            aria-hidden="true"
          />
          {busy ? "刷新中" : "刷新"}
        </button>
      </header>
      {!enabled ? (
        <div className="ontology-panel ontology-disconnected">
          <Database size={30} aria-hidden="true" />
          <h2>连接后，查看你的知识结构</h2>
          <p>
            连接记忆服务后，这里会展示实体类型、关系定义和当前范围内的实际数据。
          </p>
        </div>
      ) : (
        <>
          <section className="ontology-scope" aria-label="数据范围">
            <div>
              <span className="ontology-scope-icon">
                <Layers3 size={19} aria-hidden="true" />
              </span>
              <div>
                <strong>当前记忆空间 · 有效记录</strong>
                <p>
                  仅展示此命名空间内的有效实体，以及起点、终点均在此范围内的关系。
                </p>
                {schema && <code>{schema.namespace}</code>}
              </div>
            </div>
            <div className="ontology-statistics">
              <span>
                <strong>{displayCount(schema?.stats.entities)}</strong>实体
              </span>
              <span>
                <strong>{displayCount(schema?.stats.relationships)}</strong>关系
              </span>
            </div>
          </section>
          <div
            className="ontology-tabs"
            role="tablist"
            aria-label="知识结构视图"
          >
            {(["structure", "data"] as View[]).map((value) => (
              <button
                key={value}
                type="button"
                role="tab"
                ref={value === "data" ? dataTabRef : undefined}
                id={`${instanceId}-tab-${value}`}
                aria-controls={`${instanceId}-panel-${value}`}
                aria-selected={view === value}
                tabIndex={view === value ? 0 : -1}
                onClick={() => setView(value)}
                onKeyDown={(event) => tabKey(event, value)}
              >
                {value === "structure" ? (
                  <Network size={17} aria-hidden="true" />
                ) : (
                  <Database size={17} aria-hidden="true" />
                )}
                {value === "structure" ? "结构" : "实际数据"}
              </button>
            ))}
          </div>
          <PendingFeedback
            active={schemaState.loading || dataLoading}
            message="仍在读取知识结构。你可以继续浏览，或稍后重试；不会自动调用外部模型。"
          />
          {!!schemaState.error && (
            <Message
              title={
                schema ? "未能刷新结构，暂时保留上次结果" : "未能加载知识结构"
              }
              error
              action={
                <button
                  className="ontology-button"
                  type="button"
                  onClick={refresh}
                >
                  重试
                </button>
              }
            >
              {errorMessage(schemaState.error)}
            </Message>
          )}
          {schema && !schema.stats_available && (
            <Message title="结构已加载，实际数量暂不可用">
              未知数量显示为“—”，不代表没有数据。请刷新重试，或查看服务连接状态。
            </Message>
          )}
          {view === "structure" ? (
            <section
              role="tabpanel"
              id={`${instanceId}-panel-structure`}
              aria-labelledby={`${instanceId}-tab-structure`}
              className="ontology-structure"
              aria-busy={schemaState.loading}
            >
              {!schema && schemaState.loading ? (
                <Loading>正在读取类型与关系定义</Loading>
              ) : (
                schema && (
                  <>
                    <div className="ontology-section-heading">
                      <div>
                        <h2>实体类型</h2>
                        <p>选择一种类型，了解它的属性与关系。</p>
                      </div>
                      <span className="ontology-section-meta">
                        {
                          entities.filter(
                            (definition) => definition.runtime_supported,
                          ).length
                        }{" "}
                        种可用类型
                      </span>
                    </div>
                    <div className="ontology-type-cards">
                      {entities.map((definition) => {
                        const Icon = isRuntimeType(definition.key)
                          ? TYPE_ICONS[definition.key] || Layers3
                          : Layers3;
                        return (
                          <button
                            key={definition.key}
                            type="button"
                            className={`ontology-type-card${selectedType === definition.key ? " is-selected" : ""}`}
                            aria-pressed={selectedType === definition.key}
                            onClick={() => {
                              setSelectedType(definition.key);
                              setSelectedRelation(null);
                            }}
                          >
                            <span className="ontology-type-card-top">
                              <span className="ontology-type-card-icon">
                                <Icon
                                  size={21}
                                  strokeWidth={1.7}
                                  aria-hidden="true"
                                />
                              </span>
                              {selectedType === definition.key && (
                                <Check size={16} aria-hidden="true" />
                              )}
                            </span>
                            <strong>{entityLabel(definition.key)}</strong>
                            {!definition.runtime_supported && (
                              <span className="ontology-tag is-neutral">
                                未接入
                              </span>
                            )}
                            <span className="ontology-type-card-label">
                              {definition.storage_label}
                            </span>
                            <p>
                              {isRuntimeType(definition.key)
                                ? TYPE_DESCRIPTIONS[definition.key]
                                : definition.description}
                            </p>
                            <span className="ontology-type-card-count">
                              <b>{displayCount(definition.count)}</b>
                              {knownCount(definition.count)
                                ? "条可见记忆"
                                : "数量未知"}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                    <RelationDiagram
                      entities={entities}
                      relations={related}
                      selectedType={selectedType}
                      selectedRelation={relation}
                    />
                    <div className="ontology-relations-layout">
                      <section className="ontology-panel ontology-relation-list">
                        <div className="ontology-section-heading">
                          <div>
                            <h2>
                              {showAllRelations
                                ? "全部关系定义"
                                : `与${entityLabel(selectedType)}有关的关系`}
                            </h2>
                            <p>选择一行，查看方向、约束和兼容名称。</p>
                          </div>
                          <label className="ontology-toggle">
                            <input
                              type="checkbox"
                              checked={showAllRelations}
                              onChange={(event) =>
                                setShowAllRelations(event.target.checked)
                              }
                            />
                            显示全部
                          </label>
                        </div>
                        <div className="ontology-table-scroll">
                          <table className="ontology-definition-table">
                            <caption className="ontology-sr-only">
                              可通过键盘选择的关系定义，与上方示意图对应
                            </caption>
                            <thead>
                              <tr>
                                <th scope="col">关系</th>
                                <th scope="col">起点 → 终点</th>
                                <th scope="col">记录</th>
                              </tr>
                            </thead>
                            <tbody>
                              {tableRelations.map((definition) => (
                                <tr
                                  key={definition.key}
                                  className={
                                    relation?.key === definition.key
                                      ? "is-selected"
                                      : ""
                                  }
                                >
                                  <td>
                                    <button
                                      type="button"
                                      className="ontology-relation-select"
                                      aria-pressed={
                                        relation?.key === definition.key
                                      }
                                      onClick={() =>
                                        setSelectedRelation(definition.key)
                                      }
                                    >
                                      <strong>
                                        {relationLabel(definition)}
                                      </strong>
                                      <code>{definition.key}</code>
                                    </button>
                                  </td>
                                  <td>
                                    <span>
                                      {describeTypes(definition.domain)}
                                    </span>
                                    <ArrowRight size={14} aria-hidden="true" />
                                    <span>
                                      {describeTypes(definition.range)}
                                    </span>
                                  </td>
                                  <td>
                                    <span>
                                      {displayCount(definition.count)}
                                      <span className="ontology-sr-only">
                                        {knownCount(definition.count)
                                          ? " 条关系"
                                          : "关系数量未知"}
                                      </span>
                                    </span>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </section>
                      <RelationDetails
                        relation={relation}
                        browse={browseRelation}
                      />
                    </div>
                    {entity && (
                      <section className="ontology-panel ontology-type-details">
                        <div className="ontology-section-heading">
                          <div>
                            <span className="ontology-eyebrow">实体定义</span>
                            <h2>{entityLabel(entity.key)}的属性</h2>
                            <p>{entity.description}</p>
                          </div>
                          <button
                            className="ontology-button"
                            type="button"
                            onClick={browseType}
                            disabled={
                              !entity.runtime_supported ||
                              !isRuntimeType(entity.key)
                            }
                          >
                            查看此类记忆
                            <ArrowRight size={16} aria-hidden="true" />
                          </button>
                        </div>
                        <div className="ontology-type-metadata">
                          <span>
                            保存标签 <code>{entity.storage_label}</code>
                          </span>
                          <span>{entity.properties.length} 个声明属性</span>
                        </div>
                        <PropertyList
                          properties={entity.properties}
                          type={entity.key}
                        />
                        <details className="ontology-technical">
                          <summary>
                            本体标识
                            <ChevronDown size={14} aria-hidden="true" />
                          </summary>
                          <code>{entity.iri}</code>
                        </details>
                      </section>
                    )}
                    <details className="ontology-panel ontology-source">
                      <summary>
                        <span>
                          <BookOpen size={17} aria-hidden="true" />
                          本体来源与兼容说明
                        </span>
                        <ChevronDown size={16} aria-hidden="true" />
                      </summary>
                      <div>
                        <h3>{schema.ontology.label}</h3>
                        <p>{schema.ontology.description}</p>
                        <dl className="ontology-definition-list">
                          <div>
                            <dt>版本</dt>
                            <dd>{schema.ontology.version || "未声明"}</dd>
                          </div>
                          <div>
                            <dt>标识</dt>
                            <dd>
                              <code>{schema.ontology.iri}</code>
                            </dd>
                          </div>
                          <div>
                            <dt>读取时间</dt>
                            <dd>
                              {schemaState.loadedAt
                                ? formatDate(schemaState.loadedAt, true)
                                : "未记录"}
                            </dd>
                          </div>
                        </dl>
                        {schema.schema_warnings.length > 0 && (
                          <p className="ontology-source-note">
                            当前本体与运行接口存在{" "}
                            {schema.schema_warnings.length}{" "}
                            项兼容差异。上方显示当前实际生效的类型约束；未声明的运行属性可在记忆详情中查看。
                          </p>
                        )}
                        {knownCount(schema.stats.unmapped_relationships) &&
                          schema.stats.unmapped_relationships > 0 && (
                            <p className="ontology-source-note">
                              还有{" "}
                              {displayCount(
                                schema.stats.unmapped_relationships,
                              )}{" "}
                              条关系尚未映射到已知定义，可在“实际数据”的全部关系中查看。
                            </p>
                          )}
                      </div>
                    </details>
                  </>
                )
              )}
              <span className="ontology-sr-only" role="status">
                {relation
                  ? `已选择${relationLabel(relation)}关系定义`
                  : `已选择${entityLabel(selectedType)}类型`}
              </span>
            </section>
          ) : (
            <section
              role="tabpanel"
              id={`${instanceId}-panel-data`}
              aria-labelledby={`${instanceId}-tab-data`}
              className="ontology-panel ontology-browser"
              aria-busy={dataLoading}
            >
              <div className="ontology-section-heading">
                <div>
                  <h2>实际数据</h2>
                  <p>
                    这些是已保存的记录。点击记忆名称可查看证据、属性和修订历史。
                  </p>
                </div>
                <div
                  className="ontology-data-kind"
                  role="group"
                  aria-label="数据种类"
                >
                  <button
                    type="button"
                    aria-pressed={kind === "entities"}
                    onClick={() => changeKind("entities")}
                  >
                    实体
                  </button>
                  <button
                    type="button"
                    aria-pressed={kind === "relations"}
                    onClick={() => changeKind("relations")}
                  >
                    关系
                  </button>
                </div>
              </div>
              <form className="ontology-data-filters" onSubmit={submitSearch}>
                <label className="ontology-filter-select">
                  <span>{kind === "entities" ? "实体类型" : "关系类型"}</span>
                  {kind === "entities" ? (
                    <select
                      value={entityFilter}
                      onChange={(event) => {
                        setEntityFilter(event.target.value);
                        setOffset(0);
                      }}
                    >
                      <option value="">全部实体类型</option>
                      {entities
                        .filter(
                          (definition) =>
                            definition.runtime_supported &&
                            isRuntimeType(definition.key),
                        )
                        .map((definition) => (
                          <option value={definition.key} key={definition.key}>
                            {entityLabel(definition.key)}
                          </option>
                        ))}
                    </select>
                  ) : (
                    <select
                      value={relationFilter}
                      onChange={(event) => {
                        setRelationFilter(event.target.value);
                        setOffset(0);
                      }}
                    >
                      <option value="">全部关系类型</option>
                      {schema?.relations
                        .filter((definition) => definition.runtime_supported)
                        .map((definition) => (
                          <option value={definition.key} key={definition.key}>
                            {relationLabel(definition)}
                          </option>
                        ))}
                    </select>
                  )}
                </label>
                <label className="ontology-search-field">
                  <span>搜索{kind === "entities" ? "记忆" : "关系端点"}</span>
                  <div>
                    <Search size={17} aria-hidden="true" />
                    <input
                      type="search"
                      value={draft}
                      maxLength={200}
                      onChange={(event) => setDraft(event.target.value)}
                      placeholder={
                        kind === "entities"
                          ? "名称、描述或完整 URI"
                          : "起点、终点或关系标签"
                      }
                    />
                    {(draft || query) && (
                      <button
                        className="ontology-icon-button"
                        type="button"
                        aria-label="清除搜索"
                        onClick={() => {
                          setDraft("");
                          setQuery("");
                          setOffset(0);
                        }}
                      >
                        <X size={16} aria-hidden="true" />
                      </button>
                    )}
                  </div>
                </label>
                <button
                  className="ontology-button is-primary"
                  type="submit"
                  disabled={dataLoading}
                >
                  搜索
                </button>
              </form>
              <div
                ref={dataResultsRef}
                className="ontology-results"
                tabIndex={-1}
                role="region"
                aria-label="实际数据结果"
              >
                {dataState.key === dataKey && !!dataState.error ? (
                  <Message
                    title="未能加载实际数据"
                    error
                    action={
                      <button
                        className="ontology-button"
                        type="button"
                        onClick={refresh}
                      >
                        重试
                      </button>
                    }
                  >
                    {errorMessage(dataState.error)}
                  </Message>
                ) : dataLoading && !page ? (
                  <Loading>
                    正在读取{kind === "entities" ? "实体" : "关系"}记录
                  </Loading>
                ) : page && !page.items.length ? (
                  <div className="ontology-data-empty">
                    <Search size={28} aria-hidden="true" />
                    <h3>
                      {hasDataFilter
                        ? "没有符合条件的记录"
                        : "当前范围内还没有记录"}
                    </h3>
                    <p>
                      {offset > 0
                        ? "记录可能已经变化，请返回第一页查看。"
                        : query
                          ? "试试更短的关键词，或清除筛选后重新查看。"
                          : "这里仅展示当前记忆空间内的有效数据。新记忆和关系保存后会出现在这里。"}
                    </p>
                    {offset > 0 ? (
                      <button
                        className="ontology-button"
                        type="button"
                        onClick={() => navigatePage(0)}
                      >
                        返回第一页
                      </button>
                    ) : (
                      hasDataFilter && (
                        <button
                          className="ontology-button"
                          type="button"
                          onClick={() => {
                            setDraft("");
                            setQuery("");
                            setEntityFilter("");
                            setRelationFilter("");
                            setOffset(0);
                          }}
                        >
                          清除筛选
                        </button>
                      )
                    )}
                  </div>
                ) : (
                  page && (
                    <div
                      className={`ontology-table-scroll${dataLoading ? " is-refreshing" : ""}`}
                      tabIndex={0}
                      role="region"
                      aria-label={`${kind === "entities" ? "实体" : "关系"}记录列表`}
                    >
                      {kind === "entities" ? (
                        <EntityRows
                          items={
                            (page as OntologyPage<OntologyEntityRecord>).items
                          }
                          onSelect={onSelect}
                        />
                      ) : (
                        <RelationRows
                          items={
                            (page as OntologyPage<OntologyRelationRecord>).items
                          }
                          offset={offset}
                          onSelect={onSelect}
                          definitions={schema?.relations || []}
                        />
                      )}
                    </div>
                  )
                )}
              </div>
              {page && (
                <footer className="ontology-pagination">
                  <span role="status">
                    {dataLoading
                      ? "正在更新记录…"
                      : pageRange(page.offset, page.items.length, page.total)}
                  </span>
                  <div>
                    <button
                      className="ontology-button"
                      type="button"
                      disabled={dataLoading || offset === 0}
                      onClick={() =>
                        navigatePage(Math.max(0, offset - PAGE_SIZE))
                      }
                    >
                      <ChevronLeft size={16} aria-hidden="true" />
                      上一页
                    </button>
                    <button
                      className="ontology-button"
                      type="button"
                      disabled={dataLoading || !page.has_more}
                      onClick={() => navigatePage(offset + PAGE_SIZE)}
                    >
                      下一页
                      <ChevronRight size={16} aria-hidden="true" />
                    </button>
                  </div>
                </footer>
              )}
            </section>
          )}
        </>
      )}
    </div>
  );
}
