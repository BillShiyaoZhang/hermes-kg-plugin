import { useState } from "react";
import {
  ArrowLeft,
  Check,
  PencilLine,
  Plus,
  Power,
  RotateCcw,
  Search,
  Trash2,
} from "lucide-react";
import OntologyEditor from "./OntologyEditor";
import OntologyMutationEditor from "./OntologyMutationEditor";
import {
  ontologyEditableFields,
  type OntologyEditTarget,
  type OntologyEditorTransport,
} from "./ontologyEditorHelpers";
import {
  canCreateOntology,
  ONTOLOGY_ACTION_NAMES,
  ONTOLOGY_KIND_NAMES,
  type OntologyMutationTarget,
} from "./ontologyCrud";
import type { OntologyCollection, OntologySchema } from "./ontologyTypes";
import "./ontologyCrud.css";

export default function OntologyManager({
  schema,
  transport,
  writeEnabled,
  onPublished,
  onInteractionLockChange,
  onReconnect,
  onClose,
}: {
  schema: OntologySchema;
  transport: OntologyEditorTransport;
  writeEnabled: boolean;
  onPublished: (schema: OntologySchema) => void;
  onInteractionLockChange: (locked: boolean) => void;
  onReconnect?: () => void;
  onClose: () => void;
}) {
  const [kind, setKind] = useState<OntologyCollection>("entity_types");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [editing, setEditing] = useState<OntologyEditTarget | null>(null);
  const [mutation, setMutation] = useState<OntologyMutationTarget | null>(null);
  const [receipt, setReceipt] = useState("");
  const [sequence, setSequence] = useState(0);
  const begin = (target: OntologyMutationTarget) => {
    if (!writeEnabled) return;
    onInteractionLockChange(true);
    setMutation(target);
    setSequence((value) => value + 1);
  };
  const finish = (published: OntologySchema, message: string) => {
    onPublished(published);
    setReceipt(
      `${message} · 本体版本 ${published.version}。画布位置保持不变。`,
    );
    setEditing(null);
    setMutation(null);
    onInteractionLockChange(false);
  };
  if (editing)
    return (
      <OntologyEditor
        schema={schema}
        target={editing}
        transport={transport}
        writeEnabled={writeEnabled}
        onReconnect={onReconnect}
        onInteractionLockChange={onInteractionLockChange}
        onPublished={(published) => finish(published, "定义已更新")}
        onClose={() => {
          setEditing(null);
          onInteractionLockChange(false);
        }}
      />
    );
  if (mutation)
    return (
      <OntologyMutationEditor
        key={sequence}
        schema={schema}
        target={mutation}
        transport={transport}
        writeEnabled={writeEnabled}
        onReconnect={onReconnect}
        onInteractionLockChange={onInteractionLockChange}
        onPublished={(published) =>
          finish(
            published,
            `${ONTOLOGY_ACTION_NAMES[mutation.action]}${ONTOLOGY_KIND_NAMES[mutation.kind]}已完成`,
          )
        }
        onClose={() => {
          setMutation(null);
          onInteractionLockChange(false);
        }}
        onAlternative={begin}
      />
    );
  const normalized = query.trim().toLocaleLowerCase();
  const items = schema[kind].filter(
    (item) =>
      (status === "all" ||
        (status === "inactive"
          ? item.active === false
          : item.active !== false)) &&
      (!normalized ||
        [
          item.label,
          item.key,
          item.iri,
          item.description,
          ...("aliases" in item ? item.aliases : []),
        ]
          .join(" ")
          .toLocaleLowerCase()
          .includes(normalized)),
  );
  return (
    <section className="oc-manager" aria-label="本体定义管理">
      <button className="oc-back" onClick={onClose}>
        <ArrowLeft size={15} />
        返回所选内容
      </button>
      <header>
        <div>
          <span className="gw-eyebrow">
            当前空间 · 本体版本 {schema.version ?? "未知"}
          </span>
          <h2>管理本体</h2>
        </div>
        <p>维护可以记录的类型、属性和连接规则。</p>
      </header>
      {receipt && (
        <div className="oc-receipt" role="status">
          <Check size={17} />
          {receipt}
        </div>
      )}
      <div className="oc-tabs" role="group" aria-label="定义类别">
        {(["entity_types", "properties", "relations"] as const).map((value) => (
          <button
            key={value}
            aria-pressed={kind === value}
            onClick={() => setKind(value)}
          >
            {ONTOLOGY_KIND_NAMES[value]}
            <span>{schema[value].length}</span>
          </button>
        ))}
      </div>
      <div className="oc-filters">
        <label className="oc-search">
          <Search size={15} />
          <input
            aria-label="搜索本体定义"
            placeholder="名称、说明或标识"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <select
          aria-label="定义状态"
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          <option value="all">全部状态</option>
          <option value="active">已启用</option>
          <option value="inactive">已停用</option>
        </select>
      </div>
      <div className="oc-list-heading">
        <span>
          {items.length} 项{normalized ? "匹配定义" : "定义"}
        </span>
        {canCreateOntology(schema, kind) && (
          <button
            className="oe-button oc-new"
            disabled={!writeEnabled}
            onClick={() => begin({ kind, action: "create", key: "" })}
          >
            <Plus size={15} />
            新建{ONTOLOGY_KIND_NAMES[kind]}
          </button>
        )}
      </div>
      {schema.read_only && (
        <p className="oe-warning">当前服务只支持查看本体。</p>
      )}
      {!items.length && (
        <p className="gw-empty">
          {normalized || status !== "all"
            ? "没有符合条件的定义。可调整关键词或状态。"
            : "暂无此类定义。"}
        </p>
      )}
      <div className="oc-definitions">
        {items.map((item) => {
          const target = { kind, key: item.key };
          const canEdit = ontologyEditableFields(schema, target).length > 0;
          const actions = schema.editing?.can_manage_lifecycle
            ? (item.allowed_actions ?? [])
            : [];
          return (
            <article
              key={item.key}
              className={`oc-definition ${item.active === false ? "is-inactive" : ""}`}
            >
              <div className="oc-definition-title">
                <h3>{item.label}</h3>
                <span
                  className={`oc-status ${item.active === false ? "is-inactive" : ""}`}
                >
                  {item.active === false ? "已停用" : "已启用"}
                </span>
                <small>{item.custom ? "自定义" : "内建"}</small>
              </div>
              <p>{item.description || "暂无说明"}</p>
              <details>
                <summary>定义详情</summary>
                <dl>
                  <div>
                    <dt>固定标识</dt>
                    <dd>{item.key}</dd>
                  </div>
                  <div>
                    <dt>IRI</dt>
                    <dd>{item.iri}</dd>
                  </div>
                  {"domain" in item && (
                    <div>
                      <dt>{kind === "relations" ? "允许起点" : "适用类型"}</dt>
                      <dd>
                        {item.domain
                          .map((key) =>
                            key === "any"
                              ? "任意业务类型"
                              : (schema.entity_types.find(
                                  (type) => type.key === key,
                                )?.label ?? key),
                          )
                          .join("、")}
                      </dd>
                    </div>
                  )}
                  {"range" in item && (
                    <div>
                      <dt>{kind === "relations" ? "允许终点" : "值类型"}</dt>
                      <dd>
                        {item.range
                          .map(
                            (key) =>
                              schema.entity_types.find(
                                (type) => type.key === key,
                              )?.label ??
                              (key === "any" ? "任意业务类型" : key),
                          )
                          .join("、")}
                      </dd>
                    </div>
                  )}
                  {"aliases" in item && (
                    <div>
                      <dt>兼容名称</dt>
                      <dd>{item.aliases.join("、") || "无"}</dd>
                    </div>
                  )}
                  {"required_for" in item && (
                    <div>
                      <dt>必填类型</dt>
                      <dd>
                        {item.required_for
                          .map(
                            (key) =>
                              schema.entity_types.find(
                                (type) => type.key === key,
                              )?.label ?? key,
                          )
                          .join("、") || "无"}
                      </dd>
                    </div>
                  )}
                </dl>
              </details>
              <div className="oc-definition-actions">
                {canEdit && (
                  <button
                    disabled={!writeEnabled}
                    onClick={() => {
                      onInteractionLockChange(true);
                      setEditing(target);
                    }}
                  >
                    <PencilLine size={14} />
                    编辑
                  </button>
                )}
                {actions.includes("deactivate") && (
                  <button
                    disabled={!writeEnabled}
                    onClick={() => begin({ ...target, action: "deactivate" })}
                  >
                    <Power size={14} />
                    停用
                  </button>
                )}
                {actions.includes("restore") && (
                  <button
                    disabled={!writeEnabled}
                    onClick={() => begin({ ...target, action: "restore" })}
                  >
                    <RotateCcw size={14} />
                    恢复
                  </button>
                )}
                {actions.includes("delete") && (
                  <button
                    className="oc-delete"
                    disabled={!writeEnabled}
                    onClick={() => begin({ ...target, action: "delete" })}
                  >
                    <Trash2 size={14} />
                    删除
                  </button>
                )}
              </div>
              {!item.custom && (
                <small className="oc-protected">
                  内建定义保留规范身份
                  {actions.includes("deactivate") ? "；可以停用并恢复" : ""}。
                </small>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}
