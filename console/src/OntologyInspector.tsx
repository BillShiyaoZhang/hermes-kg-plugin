import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import {
  ArrowRight,
  ChevronLeft,
  ChevronRight,
  PencilLine,
  Search,
} from "lucide-react";
import type { ConsoleApi } from "./api";
import OntologyEditor from "./OntologyEditor";
import {
  ontologyEditableFields,
  type OntologyEditTarget,
  type OntologyEditorTransport,
} from "./ontologyEditorHelpers";
import PendingFeedback from "./PendingFeedback";
import { errorMessage, formatDate, textValue } from "./lib";
import {
  describeTypes,
  displayCount,
  knownCount,
  pageRange,
  relationLabel,
} from "./ontologyHelpers";
import {
  ontologyDefinitionDescription,
  ontologyPropertyDescription,
  ontologyPropertyTitle,
} from "./ontologyPresentation";
import type {
  OntologyEntityPage,
  OntologyRelationPage,
  OntologySchema,
} from "./ontologyTypes";
import {
  filterOntologyProperties,
  isOntologyRelation,
  loadOntologyInstances,
  ontologyDefinitionLabel,
  ontologyTypeLabel,
  type OntologyDefinition,
  type OntologyPropertyFilter,
} from "./ontologyV3";
import "./ontologyV3.css";

type Tab = "overview" | "properties" | "relations" | "instances";
type InstancePage = OntologyEntityPage | OntologyRelationPage;

export interface OntologyInspectorProps {
  schema: OntologySchema;
  definition: OntologyDefinition;
  api: ConsoleApi;
  onInstances: (type: string) => void;
  onRelationInstances?: (key: string) => void;
  onSelectRelation?: (key: string) => void;
  onSelectInstance?: (uri: string) => void;
  editorTransport?: OntologyEditorTransport;
  onPublished?: (schema: OntologySchema) => void;
  onInteractionLockChange?: (locked: boolean) => void;
  writeEnabled?: boolean;
  onReconnect?: () => void;
}

export default function OntologyInspector({
  schema,
  definition,
  api,
  onInstances,
  onRelationInstances,
  onSelectRelation,
  onSelectInstance,
  editorTransport,
  onPublished,
  onInteractionLockChange,
  writeEnabled = true,
  onReconnect,
}: OntologyInspectorProps) {
  const id = useId().replace(/:/g, "");
  const relation = isOntologyRelation(definition);
  const identity = `${relation ? "relation" : "entity"}:${definition.key}`;
  const [tab, setTab] = useState<Tab>("overview");
  const [editingTarget, setEditingTarget] = useState<OntologyEditTarget | null>(
    null,
  );
  const [propertyQuery, setPropertyQuery] = useState("");
  const [propertyFilter, setPropertyFilter] =
    useState<OntologyPropertyFilter>("all");
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [retry, setRetry] = useState(0);
  const [request, setRequest] = useState<{
    key: string;
    page: InstancePage | null;
    loading: boolean;
    error: unknown;
  }>({ key: "", page: null, loading: false, error: null });
  const results = useRef<HTMLDivElement>(null);
  const focusResults = useRef(false);
  const requestKey = JSON.stringify([identity, query, offset]);

  useEffect(() => {
    setTab("overview");
    setPropertyQuery("");
    setPropertyFilter("all");
    setDraft("");
    setQuery("");
    setOffset(0);
    focusResults.current = false;
  }, [identity]);

  useEffect(() => {
    if (tab !== "instances" || !definition.runtime_supported) return;
    const controller = new AbortController();
    setRequest({ key: requestKey, page: null, loading: true, error: null });
    loadOntologyInstances(api, definition, offset, query, controller.signal)
      .then((page) => {
        if (!controller.signal.aborted)
          setRequest({ key: requestKey, page, loading: false, error: null });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted)
          setRequest({ key: requestKey, page: null, loading: false, error });
      });
    return () => controller.abort();
  }, [api, definition, tab, requestKey, offset, query, retry]);

  const page = request.key === requestKey ? request.page : null;
  const loading =
    tab === "instances" &&
    definition.runtime_supported &&
    (request.key !== requestKey || request.loading);
  const error = request.key === requestKey ? request.error : null;
  useEffect(() => {
    if (focusResults.current && !loading && request.key === requestKey) {
      focusResults.current = false;
      results.current?.focus({ preventScroll: true });
    }
  }, [loading, request.key, requestKey]);

  const title = ontologyDefinitionLabel(definition);
  const definitionTarget: OntologyEditTarget = {
    kind: relation ? "relations" : "entity_types",
    key: definition.key,
  };
  const canEditDefinition =
    !!editorTransport &&
    ontologyEditableFields(schema, definitionTarget).length > 0;
  const startEditing = (target: OntologyEditTarget) => {
    if (
      !writeEnabled ||
      !editorTransport ||
      !ontologyEditableFields(schema, target).length
    )
      return;
    onInteractionLockChange?.(true);
    setEditingTarget(target);
  };
  const restoreEditEntry = (target: OntologyEditTarget) => {
    requestAnimationFrame(() => {
      const identity = `${target.kind}:${target.key}`;
      [
        ...document.querySelectorAll<HTMLButtonElement>(
          "[data-ontology-edit-target]",
        ),
      ]
        .find((button) => button.dataset.ontologyEditTarget === identity)
        ?.focus({ preventScroll: true });
    });
  };
  const filteredProperties = filterOntologyProperties(
    definition,
    propertyQuery,
    propertyFilter,
  );
  const tabs: { key: Tab; label: string }[] = [
    { key: "overview", label: "概览" },
    { key: "properties", label: "属性" },
    { key: "relations", label: relation ? "端点规则" : "允许关系" },
    { key: "instances", label: relation ? "实际关系" : "实例" },
  ];
  const browseGraph = () => {
    if (relation) onRelationInstances?.(definition.key);
    else onInstances(definition.key);
  };
  const canBrowseGraph =
    definition.runtime_supported && (!relation || !!onRelationInstances);
  const changePage = (next: number) => {
    focusResults.current = true;
    setOffset(next);
  };
  const searchInstances = (event: FormEvent) => {
    event.preventDefault();
    setQuery(draft.trim());
    setOffset(0);
    setRetry((value) => value + 1);
  };
  const tabKeyboard = (
    event: KeyboardEvent<HTMLButtonElement>,
    current: Tab,
  ) => {
    const index = tabs.findIndex((item) => item.key === current);
    const next =
      event.key === "ArrowRight"
        ? (index + 1) % tabs.length
        : event.key === "ArrowLeft"
          ? (index + tabs.length - 1) % tabs.length
          : event.key === "Home"
            ? 0
            : event.key === "End"
              ? tabs.length - 1
              : -1;
    if (next >= 0) {
      event.preventDefault();
      setTab(tabs[next].key);
      document.getElementById(`${id}-${tabs[next].key}-tab`)?.focus();
    }
  };
  const endpoint = (uri: string, name: string) =>
    onSelectInstance ? (
      <button className="ov3-text-button" onClick={() => onSelectInstance(uri)}>
        {name || "未命名实体"}
      </button>
    ) : (
      <strong>{name || "未命名实体"}</strong>
    );

  if (editingTarget && editorTransport)
    return (
      <OntologyEditor
        schema={schema}
        target={editingTarget}
        transport={editorTransport}
        writeEnabled={writeEnabled}
        onReconnect={onReconnect}
        onInteractionLockChange={onInteractionLockChange}
        onClose={() => {
          restoreEditEntry(editingTarget);
          setEditingTarget(null);
        }}
        onPublished={(next) => {
          restoreEditEntry(editingTarget);
          setEditingTarget(null);
          onPublished?.(next);
        }}
      />
    );

  return (
    <section className="ov3-inspector" aria-label={`${title}的本体定义`}>
      <header className="ov3-heading">
        <span className="ov3-eyebrow">
          {relation ? "关系定义" : "实体类型"} ·{" "}
          {canEditDefinition ? "可编辑" : "只读"}
        </span>
        <h2>{title}</h2>
        <p>
          {ontologyDefinitionDescription(definition) || "此定义没有补充说明。"}
        </p>
        {canEditDefinition && (
          <button
            className="ov3-edit-entry"
            disabled={!writeEnabled}
            data-ontology-edit-target={`${definitionTarget.kind}:${definitionTarget.key}`}
            onClick={() => startEditing(definitionTarget)}
          >
            <PencilLine size={14} aria-hidden="true" />
            编辑定义
          </button>
        )}
      </header>
      <div className="ov3-tabs" role="tablist" aria-label="定义详情">
        {tabs.map((item) => (
          <button
            key={item.key}
            id={`${id}-${item.key}-tab`}
            role="tab"
            aria-selected={tab === item.key}
            aria-controls={`${id}-${item.key}-panel`}
            tabIndex={tab === item.key ? 0 : -1}
            onClick={() => setTab(item.key)}
            onKeyDown={(event) => tabKeyboard(event, item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>
      {tabs
        .filter((item) => item.key !== tab)
        .map((item) => (
          <div
            key={item.key}
            hidden
            id={`${id}-${item.key}-panel`}
            role="tabpanel"
            aria-labelledby={`${id}-${item.key}-tab`}
          />
        ))}
      <div
        className="ov3-panel"
        id={`${id}-${tab}-panel`}
        role="tabpanel"
        aria-labelledby={`${id}-${tab}-tab`}
      >
        {tab === "overview" && (
          <>
            <p className="ov3-rule-note">
              规则说明允许记录什么、怎样连接；不表示某条事实已经发生。
            </p>
            <dl className="ov3-metrics">
              <div>
                <dt>当前空间 · 有效{relation ? "关系" : "实例"}</dt>
                <dd>
                  {displayCount(definition.count)}
                  <small>
                    {knownCount(definition.count)
                      ? "条 · 全空间统计"
                      : "数量暂时未知"}
                  </small>
                </dd>
              </div>
              <div>
                <dt>已声明属性</dt>
                <dd>
                  {definition.properties.length}
                  <small>个属性</small>
                </dd>
              </div>
            </dl>
            {relation && (
              <div className="ov3-endpoint-flow">
                <span>
                  <small>允许的起点</small>
                  {describeTypes(definition.domain)}
                </span>
                <ArrowRight size={18} aria-hidden="true" />
                <span>
                  <small>允许的终点</small>
                  {describeTypes(definition.range)}
                </span>
              </div>
            )}
            {!definition.runtime_supported && (
              <p className="ov3-warning">
                本体已声明；当前运行接口尚未支持此定义。
              </p>
            )}
            {canBrowseGraph && (
              <button className="ov3-primary" onClick={browseGraph}>
                在图中查看{relation ? "实际关系" : "此类实例"}
                <ArrowRight size={16} aria-hidden="true" />
              </button>
            )}
            <details className="ov3-technical">
              <summary>技术标识与运行约束</summary>
              <dl>
                <div>
                  <dt>规范名称</dt>
                  <dd>
                    <code>{definition.key}</code>
                  </dd>
                </div>
                <div>
                  <dt>本体标识</dt>
                  <dd>
                    <code>{definition.iri}</code>
                  </dd>
                </div>
                <div>
                  <dt>保存{relation ? "关系名" : "标签"}</dt>
                  <dd>
                    <code>
                      {relation
                        ? definition.storage_type
                        : definition.storage_label}
                    </code>
                  </dd>
                </div>
                {relation && (
                  <>
                    <div>
                      <dt>兼容名称</dt>
                      <dd>{definition.aliases.join("、") || "无"}</dd>
                    </div>
                    <div>
                      <dt>原始声明</dt>
                      <dd>
                        {describeTypes(definition.declared_domain)} →{" "}
                        {describeTypes(definition.declared_range)}
                      </dd>
                    </div>
                    <div>
                      <dt>实际生效</dt>
                      <dd>
                        {describeTypes(definition.domain)} →{" "}
                        {describeTypes(definition.range)}
                      </dd>
                    </div>
                  </>
                )}
                <div>
                  <dt>原始说明</dt>
                  <dd>{definition.description || "未提供"}</dd>
                </div>
              </dl>
            </details>
            {!!schema.schema_warnings.length && (
              <details className="ov3-technical ov3-warning">
                <summary>
                  {schema.schema_warnings.length} 项本体兼容提示
                </summary>
                <p>操作能力以当前运行约束为准。</p>
                <ul>
                  {schema.schema_warnings.map((warning, index) => (
                    <li key={`${warning.code}:${index}`}>
                      <strong>{warning.code}</strong>
                      <p>
                        {warning.message ||
                          textValue(
                            Object.fromEntries(
                              Object.entries(warning).filter(
                                ([key]) => key !== "code",
                              ),
                            ),
                          ) ||
                          "存在声明与运行契约的差异。"}
                      </p>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </>
        )}
        {tab === "properties" && (
          <>
            <label className="ov3-search">
              <Search size={16} aria-hidden="true" />
              <input
                type="search"
                value={propertyQuery}
                onChange={(event) => setPropertyQuery(event.target.value)}
                placeholder="查找属性、别名或 IRI"
                aria-label="搜索定义属性"
              />
            </label>
            <div
              className="ov3-property-filters"
              role="group"
              aria-label="属性要求"
            >
              {(
                [
                  ["all", "全部"],
                  ["required", "必填"],
                  ["optional", "可选"],
                  ["managed", "系统维护"],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  aria-pressed={propertyFilter === value}
                  onClick={() => setPropertyFilter(value)}
                >
                  {label}
                </button>
              ))}
            </div>
            <p className="ov3-result-note" role="status">
              {filteredProperties.length} / {definition.properties.length}{" "}
              个声明属性
            </p>
            {!filteredProperties.length && (
              <div className="ov3-empty">
                {definition.properties.length ? (
                  <>
                    <p>没有符合条件的属性。</p>
                    <button
                      onClick={() => {
                        setPropertyFilter("all");
                        setPropertyQuery("");
                      }}
                    >
                      清除属性筛选
                    </button>
                  </>
                ) : (
                  <p>
                    此定义未单独声明属性。已保存记录可能仍有附加属性，可在实例详情核对。
                  </p>
                )}
              </div>
            )}
            <div className="ov3-properties">
              {filteredProperties.map((property) => (
                <article
                  className="ov3-property"
                  key={property.key}
                  data-property-key={property.key}
                >
                  <div className="ov3-property-title">
                    <h3>{ontologyPropertyTitle(property)}</h3>
                    <div>
                      {property.required_for.includes(definition.key) && (
                        <span>
                          {property.required_condition ? "有条件必填" : "必填"}
                        </span>
                      )}
                      {property.managed && <span>系统维护</span>}
                      {!property.runtime_supported && <span>未接入</span>}
                    </div>
                  </div>
                  <p>
                    {ontologyPropertyDescription(property) || "未提供说明。"}
                  </p>
                  <div className="ov3-value-type">
                    {property.range.join(" / ") || "未声明值类型"}
                    {property.value_shape === "array" ? " · 多个值" : ""}
                  </div>
                  {editorTransport &&
                    ontologyEditableFields(schema, {
                      kind: "properties",
                      key: property.key,
                    }).length > 0 && (
                      <button
                        className="ov3-edit-entry ov3-edit-property"
                        disabled={!writeEnabled}
                        aria-label={`编辑属性 ${ontologyPropertyTitle(property)}`}
                        data-ontology-edit-target={`properties:${property.key}`}
                        onClick={() =>
                          startEditing({
                            kind: "properties",
                            key: property.key,
                          })
                        }
                      >
                        <PencilLine size={13} aria-hidden="true" />
                        编辑属性
                      </button>
                    )}
                  {property.required_condition && (
                    <p className="ov3-condition">
                      条件：{property.required_condition}
                    </p>
                  )}
                  {!!property.allowed_values?.length && (
                    <div className="ov3-values">
                      {property.allowed_values.map((value) => (
                        <code key={value}>{value}</code>
                      ))}
                    </div>
                  )}
                  <details className="ov3-technical">
                    <summary>标识与兼容信息</summary>
                    <dl>
                      <div>
                        <dt>属性名</dt>
                        <dd>
                          <code>{property.key}</code>
                        </dd>
                      </div>
                      <div>
                        <dt>适用类型</dt>
                        <dd>{describeTypes(property.domain)}</dd>
                      </div>
                      <div>
                        <dt>兼容名称</dt>
                        <dd>{property.aliases.join("、") || "无"}</dd>
                      </div>
                      <div>
                        <dt>本体标识</dt>
                        <dd>
                          <code>{property.iri}</code>
                        </dd>
                      </div>
                    </dl>
                  </details>
                </article>
              ))}
            </div>
          </>
        )}
        {tab === "relations" && (
          <>
            {relation ? (
              <>
                <p className="ov3-rule-note">
                  {title}：{describeTypes(definition.domain)} →{" "}
                  {describeTypes(definition.range)}。箭头表示允许的方向。
                </p>
                <h3>当前运行约束</h3>
                <dl className="ov3-details">
                  <div>
                    <dt>起点类型</dt>
                    <dd>{describeTypes(definition.domain)}</dd>
                  </div>
                  <div>
                    <dt>终点类型</dt>
                    <dd>{describeTypes(definition.range)}</dd>
                  </div>
                </dl>
                {(definition.domain.includes("any") ||
                  definition.range.includes("any")) && (
                  <p>
                    “任意业务类型”表示逻辑范围，不是额外的实体类型，也不是所有类型之间已经存在关系。
                  </p>
                )}
                <details className="ov3-technical">
                  <summary>对照原始声明</summary>
                  <p>
                    {describeTypes(definition.declared_domain)} →{" "}
                    {describeTypes(definition.declared_range)}
                  </p>
                  <p>声明与运行范围不同时，当前支持能力采用上方运行约束。</p>
                </details>
              </>
            ) : (
              <>
                {(
                  [
                    ["out", "出关系"],
                    ["in", "入关系"],
                    ["universal", "通用规则"],
                  ] as const
                ).map(([direction, label]) => {
                  const relations = schema.relations.filter((item) =>
                    direction === "universal"
                      ? item.domain.includes("any") ||
                        item.range.includes("any")
                      : !(
                          item.domain.includes("any") ||
                          item.range.includes("any")
                        ) &&
                        (direction === "out"
                          ? item.domain
                          : item.range
                        ).includes(definition.key),
                  );
                  return (
                    <section className="ov3-relation-group" key={direction}>
                      <h3>
                        {label}
                        <small>{relations.length}</small>
                      </h3>
                      {!relations.length && (
                        <p className="ov3-result-note">未声明{label}。</p>
                      )}
                      {relations.map((item) => (
                        <div className="ov3-relation" key={item.key}>
                          {onSelectRelation ? (
                            <button
                              className="ov3-relation-select"
                              onClick={() => onSelectRelation(item.key)}
                            >
                              <strong>{relationLabel(item)}</strong>
                              <ChevronRight size={15} aria-hidden="true" />
                            </button>
                          ) : (
                            <strong>{relationLabel(item)}</strong>
                          )}
                          <p>
                            {describeTypes(item.domain)} →{" "}
                            {describeTypes(item.range)}
                          </p>
                          <small>
                            {displayCount(item.count)}{" "}
                            {knownCount(item.count)
                              ? "条有效关系 · 全空间"
                              : "数量未知"}
                          </small>
                        </div>
                      ))}
                    </section>
                  );
                })}
              </>
            )}
          </>
        )}
        {tab === "instances" && (
          <>
            <p className="ov3-rule-note">
              当前空间的有效{relation ? "关系" : "实例"}
              ，由服务端筛选并分页；不包含停用记录或其他空间。
            </p>
            {!definition.runtime_supported ? (
              <p className="ov3-warning">
                当前运行接口尚不支持读取此定义的实际数据。
              </p>
            ) : (
              <>
                <form
                  className="ov3-instance-search"
                  onSubmit={searchInstances}
                >
                  <label className="ov3-search">
                    <Search size={16} aria-hidden="true" />
                    <input
                      aria-label={relation ? "搜索实际关系" : "搜索此类实例"}
                      value={draft}
                      maxLength={200}
                      onChange={(event) => setDraft(event.target.value)}
                      placeholder={
                        relation
                          ? "起点、终点或关系标签"
                          : "名称、描述或完整 URI"
                      }
                    />
                  </label>
                  <button type="submit" disabled={loading}>
                    搜索
                  </button>
                </form>
                {canBrowseGraph && (
                  <button
                    className="ov3-text-button ov3-browse"
                    onClick={browseGraph}
                  >
                    在图中查看{relation ? "这些关系" : "此类实例"}
                    <ArrowRight size={14} aria-hidden="true" />
                  </button>
                )}
                <div
                  className="ov3-instance-results"
                  ref={results}
                  tabIndex={-1}
                  aria-busy={loading}
                  role="region"
                  aria-label={relation ? "实际关系列表" : "实例列表"}
                >
                  <PendingFeedback
                    active={loading}
                    message="仍在读取此定义的实际数据。可以切换标签或返回图谱；当前读取不会修改知识。"
                  />
                  {loading ? (
                    <p role="status">
                      正在读取{relation ? "实际关系" : "实例"}…
                    </p>
                  ) : error ? (
                    <div className="ov3-warning" role="alert">
                      <p>{errorMessage(error)}</p>
                      <button onClick={() => setRetry((value) => value + 1)}>
                        重试
                      </button>
                    </div>
                  ) : page && !page.items.length ? (
                    <div className="ov3-empty">
                      <p>
                        {query
                          ? "没有符合条件的记录。"
                          : offset
                            ? "当前页没有记录，数据可能已变化。"
                            : `当前空间暂无此类有效${relation ? "关系" : "实例"}。`}
                      </p>
                      {(query || offset > 0) && (
                        <button
                          onClick={() => {
                            setDraft("");
                            setQuery("");
                            setOffset(0);
                          }}
                        >
                          返回全部记录
                        </button>
                      )}
                    </div>
                  ) : (
                    page && (
                      <div className="ov3-instance-list">
                        {relation
                          ? (page as OntologyRelationPage).items.map(
                              (item, index) => (
                                <article
                                  className="ov3-instance"
                                  key={`${item.source.uri}:${item.relation_type}:${item.target.uri}:${index}`}
                                >
                                  <div className="ov3-record-flow">
                                    <div>
                                      <small>
                                        起点 ·{" "}
                                        {ontologyTypeLabel(item.source.type)}
                                      </small>
                                      {endpoint(
                                        item.source.uri,
                                        item.source.name,
                                      )}
                                    </div>
                                    <ArrowRight size={17} aria-hidden="true" />
                                    <div>
                                      <small>
                                        终点 ·{" "}
                                        {ontologyTypeLabel(item.target.type)}
                                      </small>
                                      {endpoint(
                                        item.target.uri,
                                        item.target.name,
                                      )}
                                    </div>
                                  </div>
                                  <details className="ov3-technical">
                                    <summary>已存属性与端点标识</summary>
                                    <code>
                                      {item.source.uri} → {item.target.uri}
                                    </code>
                                    <dl>
                                      {Object.entries(item.properties).map(
                                        ([key, value]) => (
                                          <div key={key}>
                                            <dt>{key}</dt>
                                            <dd>{textValue(value)}</dd>
                                          </div>
                                        ),
                                      )}
                                    </dl>
                                    {!Object.keys(item.properties).length && (
                                      <p>无附加属性。</p>
                                    )}
                                  </details>
                                </article>
                              ),
                            )
                          : (page as OntologyEntityPage).items.map((item) => (
                              <article className="ov3-instance" key={item.uri}>
                                {endpoint(item.uri, item.name)}
                                {item.description && <p>{item.description}</p>}
                                {item.updated_at && (
                                  <small>
                                    更新于 {formatDate(item.updated_at)}
                                  </small>
                                )}
                                <details className="ov3-technical">
                                  <summary>记录标识</summary>
                                  <code>{item.uri}</code>
                                </details>
                              </article>
                            ))}
                      </div>
                    )
                  )}
                </div>
                {page && (
                  <footer className="ov3-pagination">
                    <span role="status">
                      {pageRange(page.offset, page.items.length, page.total)}
                    </span>
                    <div>
                      <button
                        aria-label="上一页实例"
                        disabled={loading || offset === 0}
                        onClick={() =>
                          changePage(Math.max(0, offset - page.limit))
                        }
                      >
                        <ChevronLeft size={17} aria-hidden="true" />
                      </button>
                      <button
                        aria-label="下一页实例"
                        disabled={loading || !page.has_more}
                        onClick={() => changePage(offset + page.limit)}
                      >
                        <ChevronRight size={17} aria-hidden="true" />
                      </button>
                    </div>
                  </footer>
                )}
              </>
            )}
          </>
        )}
      </div>
    </section>
  );
}
