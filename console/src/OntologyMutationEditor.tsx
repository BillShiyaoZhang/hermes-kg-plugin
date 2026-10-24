import { useEffect, useId, useRef, useState } from "react";
import {
  ArrowLeft,
  Eye,
  LoaderCircle,
  RefreshCw,
  Save,
  ShieldAlert,
} from "lucide-react";
import { ApiError } from "./api";
import { errorMessage } from "./lib";
import { WorkspaceDialog } from "./WorkspaceDialogs";
import PendingFeedback from "./PendingFeedback";
import OntologyRiskPanel from "./OntologyRiskPanel";
import {
  ontologyEditResource,
  type OntologyEditorTransport,
} from "./ontologyEditorHelpers";
import {
  ONTOLOGY_ACTION_NAMES,
  ONTOLOGY_KIND_NAMES,
  newOntologyDraft,
  ontologyConfirmationMatches,
  ontologyMutationIssues,
  ontologyMutationPreviewMatches,
  ontologyMutationRequest,
  type OntologyCreateDraft,
  type OntologyMutationTarget,
} from "./ontologyCrud";
import type {
  OntologyPublishRequest,
  OntologySchema,
  OntologyValidationResult,
} from "./ontologyTypes";
import "./ontologyEditor.css";
import "./ontologyCrud.css";

export default function OntologyMutationEditor({
  schema,
  target,
  transport,
  writeEnabled,
  onPublished,
  onClose,
  onInteractionLockChange,
  onReconnect,
  onAlternative,
}: {
  schema: OntologySchema;
  target: OntologyMutationTarget;
  transport: OntologyEditorTransport;
  writeEnabled: boolean;
  onPublished: (schema: OntologySchema) => void;
  onClose: () => void;
  onInteractionLockChange?: (locked: boolean) => void;
  onReconnect?: () => void;
  onAlternative?: (target: OntologyMutationTarget) => void;
}) {
  const id = useId();
  const [baseline, setBaseline] = useState(() => structuredClone(schema));
  const initialParentVersion = useRef(schema.version);
  const initialDraft = useRef(newOntologyDraft(target.kind));
  const [draft, setDraft] = useState(initialDraft.current);
  const [reason, setReason] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState<"validate" | "publish" | "read" | null>(
    null,
  );
  const busyRef = useRef<typeof busy>(null);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState("");
  const [conflict, setConflict] = useState(false);
  const [discard, setDiscard] = useState(false);
  const [preview, setPreview] = useState<{
    key: string;
    result: OntologyValidationResult;
  } | null>(null);
  const readController = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const mounted = useRef(true);
  const reportLock = useRef(onInteractionLockChange);
  reportLock.current = onInteractionLockChange;
  const region = useRef<HTMLElement>(null);
  const resultRegion = useRef<HTMLDivElement>(null);
  const operation = useRef<{
    key: string;
    request: OntologyPublishRequest;
  } | null>(null);
  const resource = ontologyEditResource(baseline, target);
  const create = target.action === "create";
  const actionName = ONTOLOGY_ACTION_NAMES[target.action];
  const kindName = ONTOLOGY_KIND_NAMES[target.kind];
  const request = ontologyMutationRequest(baseline, target, draft);
  const requestKey = JSON.stringify(request);
  const issues = ontologyMutationIssues(baseline, target, draft);
  const scopeChanged = baseline.namespace !== schema.namespace;
  const versionChanged =
    schema.namespace === baseline.namespace &&
    schema.version !== initialParentVersion.current &&
    (schema.version ?? -1) > (baseline.version ?? -1);
  const needsRebase = conflict || versionChanged;
  const currentPreview = preview?.key === requestKey ? preview.result : null;
  const dirty =
    JSON.stringify(draft) !== JSON.stringify(initialDraft.current) ||
    !!reason.trim();
  const canValidate =
    writeEnabled && !scopeChanged && !needsRebase && !busy && !issues.length;
  const canPublish =
    canValidate &&
    !!currentPreview?.can_publish &&
    ontologyConfirmationMatches(currentPreview.risk, confirmation);
  const title = create
    ? `${actionName}${kindName}`
    : `${actionName}“${resource?.label ?? target.key}”`;
  const locked = busy === "publish" || scopeChanged || !writeEnabled;

  useEffect(() => {
    mounted.current = true;
    reportLock.current?.(true);
    region.current?.focus({ preventScroll: true });
    return () => {
      mounted.current = false;
      readController.current?.abort();
      reportLock.current?.(false);
    };
  }, []);
  useEffect(() => {
    if (!writeEnabled) {
      setPreview(null);
      setConfirmation("");
    }
  }, [writeEnabled]);
  useEffect(() => {
    if (preview || error) resultRegion.current?.focus({ preventScroll: true });
  }, [preview, error]);
  useEffect(() => {
    if (!dirty && busy !== "publish") return;
    const prevent = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [dirty, busy]);
  const working = (value: typeof busy) => {
    busyRef.current = value;
    setBusy(value);
  };
  const invalidate = () => {
    readController.current?.abort();
    generation.current++;
    setPreview(null);
    setConfirmation("");
    setError(null);
    setNotice("");
    operation.current = null;
    if (busyRef.current !== "publish") working(null);
  };
  const change = <K extends keyof OntologyCreateDraft>(
    field: K,
    value: OntologyCreateDraft[K],
  ) => {
    if (busyRef.current === "publish") return;
    invalidate();
    setDraft((current) => ({ ...current, [field]: value }));
  };
  const failure = (value: unknown) => {
    setError(value);
    if (value instanceof ApiError && value.status === 409) {
      setConflict(true);
      setPreview(null);
      setConfirmation("");
      setNotice(
        "版本、引用或数据已变化。输入已保留；读取最新版本后重新预览。旧确认不能继续提交。",
      );
    }
  };
  const close = () => {
    if (busyRef.current === "publish") return;
    readController.current?.abort();
    generation.current++;
    working(null);
    if (dirty) setDiscard(true);
    else onClose();
  };
  const validate = async () => {
    if (!canValidate || busyRef.current) return;
    const controller = new AbortController();
    readController.current?.abort();
    readController.current = controller;
    const ticket = ++generation.current;
    working("validate");
    setError(null);
    setPreview(null);
    setConfirmation("");
    try {
      const result = await transport.validate(request, controller.signal);
      if (
        controller.signal.aborted ||
        !mounted.current ||
        ticket !== generation.current
      )
        return;
      if (!ontologyMutationPreviewMatches(result, request))
        throw new Error(
          "服务返回的范围、版本或风险确认不完整。未提交操作，请重新预览。",
        );
      setPreview({ key: requestKey, result });
      operation.current = null;
    } catch (value) {
      if (
        !controller.signal.aborted &&
        mounted.current &&
        ticket === generation.current
      )
        failure(value);
    } finally {
      if (mounted.current && ticket === generation.current) working(null);
    }
  };
  const reload = async () => {
    if (!transport.read || busyRef.current || !writeEnabled || scopeChanged)
      return;
    const controller = new AbortController();
    readController.current?.abort();
    readController.current = controller;
    const ticket = ++generation.current;
    working("read");
    setError(null);
    setPreview(null);
    setConfirmation("");
    try {
      const latest = await transport.read(controller.signal);
      if (
        controller.signal.aborted ||
        !mounted.current ||
        ticket !== generation.current
      )
        return;
      if (
        latest.namespace !== baseline.namespace ||
        !Number.isInteger(latest.version)
      )
        throw new Error("最新定义不属于草稿所在空间。请保留输入并核对连接。");
      setBaseline(structuredClone(latest));
      setConflict(false);
      operation.current = null;
      setNotice(
        `已读取版本 ${latest.version}，你的输入仍保留。请重新核对影响。`,
      );
    } catch (value) {
      if (
        !controller.signal.aborted &&
        mounted.current &&
        ticket === generation.current
      )
        failure(value);
    } finally {
      if (mounted.current && ticket === generation.current) working(null);
    }
  };
  const publish = async () => {
    if (!canPublish || busyRef.current || !currentPreview) return;
    const risk = currentPreview.risk!;
    const key = JSON.stringify([
      request,
      reason.trim(),
      confirmation.trim(),
      risk.confirmation_token,
    ]);
    if (operation.current?.key !== key)
      operation.current = {
        key,
        request: {
          ...request,
          operation_id: crypto.randomUUID(),
          ...(reason.trim() ? { reason: reason.trim() } : {}),
          ...(risk.requires_confirmation
            ? {
                confirmation: confirmation.trim(),
                confirmation_token: risk.confirmation_token,
              }
            : {}),
        },
      };
    const sent = operation.current.request;
    working("publish");
    setError(null);
    setNotice("");
    try {
      const result = await transport.publish(sent);
      if (!mounted.current) return;
      if (
        !result.ok ||
        result.namespace !== baseline.namespace ||
        result.schema.namespace !== baseline.namespace ||
        result.operation_id !== sent.operation_id ||
        result.version <= (baseline.version ?? -1) ||
        result.schema.version !== result.version
      )
        throw new Error(
          "尚未收到与此操作匹配的完整回执。输入已保留；使用原操作重试以免重复提交。",
        );
      onPublished(result.schema);
    } catch (value) {
      if (mounted.current) failure(value);
    } finally {
      if (mounted.current) working(null);
    }
  };
  const toggle = (
    field: "domain" | "range" | "required_for",
    value: string,
  ) => {
    const next = draft[field].includes(value)
      ? draft[field].filter((item) => item !== value)
      : value === "any"
        ? ["any"]
        : [...draft[field].filter((item) => item !== "any"), value];
    change(field, next);
  };
  const types = baseline.entity_types.filter((item) => item.active !== false);
  const scopes = (
    field: "domain" | "range" | "required_for",
    label: string,
  ) => (
    <fieldset className="oe-choice-field">
      <legend>{label}</legend>
      <div>
        {field !== "required_for" && (
          <label>
            <input
              type="checkbox"
              checked={draft[field].includes("any")}
              disabled={locked}
              onChange={() => toggle(field, "any")}
            />
            <span>任意业务类型</span>
          </label>
        )}
        {types
          .filter(
            (item) =>
              field !== "required_for" ||
              draft.domain.includes("any") ||
              draft.domain.includes(item.key),
          )
          .map((item) => (
            <label key={item.key}>
              <input
                type="checkbox"
                checked={draft[field].includes(item.key)}
                disabled={locked}
                onChange={() => toggle(field, item.key)}
              />
              <span>{item.label}</span>
            </label>
          ))}
      </div>
    </fieldset>
  );

  return (
    <>
      <section
        ref={region}
        tabIndex={-1}
        className="oe-editor oc-mutation"
        aria-label={title}
        onKeyDown={(event) => {
          if (event.key === "Escape" && !event.defaultPrevented) {
            event.preventDefault();
            event.stopPropagation();
            close();
          }
        }}
      >
        <button
          className="oc-back"
          disabled={busy === "publish"}
          onClick={close}
        >
          <ArrowLeft size={15} />
          返回定义目录
        </button>
        <header className="oe-header">
          <span>
            {kindName}定义 · {create ? "新建草稿" : "生命周期管理"}
          </span>
          <h2>{title}</h2>
        </header>
        <div
          className={`oe-summary ${target.action === "delete" ? "oc-destructive-summary" : ""}`}
        >
          <strong>
            {create
              ? "定义发布后才能用于记录知识"
              : target.action === "deactivate"
                ? "停止新增使用，历史仍可查看"
                : target.action === "restore"
                  ? "重新允许此定义用于新增记录"
                  : "永久移除此自定义定义"}
          </strong>
          {!create && (
            <span className="oc-definition-id">
              固定标识：<code>{target.key}</code>
            </span>
          )}
          <p>
            {create
              ? "先填写定义并核对影响。标识发布后固定；可通过显示名称表达业务含义。"
              : target.action === "delete"
                ? "只删除无引用的自定义定义。存在事实、历史、候选或定义引用时会阻止删除；不会级联清除记录。"
                : target.action === "deactivate"
                  ? "停用不会删除事实或候选；新建记录与新增使用会受限制。需要时可以恢复。"
                  : "恢复前重新校验端点、引用与当前约束。历史记录保持原样。"}
          </p>
          <small>当前空间 · 基线版本 {baseline.version ?? "未知"}</small>
        </div>
        {scopeChanged && (
          <p className="oe-error" role="alert">
            当前空间已改变，草稿仍属于原空间。已阻止提交，请核对连接。
          </p>
        )}
        {!writeEnabled && (
          <div className="oe-warning" role="status">
            <p>服务当前不可写，输入已保留。恢复连接后需要重新预览。</p>
            {onReconnect && (
              <button className="oe-button" onClick={onReconnect}>
                重新连接
              </button>
            )}
          </div>
        )}
        {create && (
          <div className="oe-fields">
            <label className="oe-field" htmlFor={`${id}-label`}>
              显示名称
              <input
                id={`${id}-label`}
                value={draft.label}
                maxLength={120}
                disabled={locked}
                onChange={(event) => change("label", event.target.value)}
              />
            </label>
            <label className="oe-field" htmlFor={`${id}-key`}>
              固定标识
              <input
                id={`${id}-key`}
                value={draft.key}
                autoComplete="off"
                spellCheck={false}
                disabled={locked}
                onChange={(event) => change("key", event.target.value)}
              />
              <small>
                {target.kind === "relations"
                  ? "例如 X_WORKS_ON"
                  : target.kind === "properties"
                    ? "例如 x_priority"
                    : "例如 x_project"}
                。只在当前空间生效，发布后不可改名。
              </small>
            </label>
            <label className="oe-field" htmlFor={`${id}-description`}>
              说明
              <textarea
                id={`${id}-description`}
                value={draft.description}
                rows={3}
                maxLength={4000}
                disabled={locked}
                onChange={(event) => change("description", event.target.value)}
              />
            </label>
            {target.kind !== "entity_types" && (
              <>
                {scopes(
                  "domain",
                  target.kind === "relations" ? "允许起点" : "适用类型",
                )}
                {target.kind === "relations" ? (
                  scopes("range", "允许终点")
                ) : (
                  <>
                    <label className="oe-field" htmlFor={`${id}-datatype`}>
                      属性值类型
                      <select
                        id={`${id}-datatype`}
                        value={draft.range[0]}
                        disabled={locked}
                        onChange={(event) =>
                          change("range", [event.target.value])
                        }
                      >
                        {baseline.editing?.create_datatypes?.map((value) => (
                          <option key={value} value={value}>
                            {value}
                          </option>
                        ))}
                      </select>
                    </label>
                    {(baseline.editing?.create_value_shapes?.length ?? 0) >
                      1 && (
                      <label className="oe-field" htmlFor={`${id}-shape`}>
                        值的形式
                        <select
                          id={`${id}-shape`}
                          value={draft.value_shape}
                          disabled={locked}
                          onChange={(event) =>
                            change(
                              "value_shape",
                              event.target.value as "scalar" | "array",
                            )
                          }
                        >
                          {baseline.editing?.create_value_shapes?.map(
                            (shape) => (
                              <option key={shape} value={shape}>
                                {shape === "scalar"
                                  ? "单个值"
                                  : "多个值（数组）"}
                              </option>
                            ),
                          )}
                        </select>
                      </label>
                    )}
                    {scopes("required_for", "必须填写此属性的类型（可选）")}
                  </>
                )}
                <label className="oe-field" htmlFor={`${id}-aliases`}>
                  兼容名称（可选）
                  <textarea
                    id={`${id}-aliases`}
                    rows={2}
                    value={draft.aliases}
                    disabled={locked}
                    onChange={(event) => change("aliases", event.target.value)}
                  />
                  <small>
                    每行一个英文字段名，用于兼容输入；显示名称可使用中文。
                  </small>
                </label>
              </>
            )}
          </div>
        )}
        {!!issues.length && (
          <ul className="oe-issues" aria-label="定义校验问题">
            {issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        )}
        {needsRebase && (
          <div className="oe-warning" role="status">
            <p>定义或引用已经变化，旧预览与确认不能继续使用。输入仍保留。</p>
            <button
              className="oe-button"
              disabled={
                !!busy || !writeEnabled || scopeChanged || !transport.read
              }
              onClick={() => void reload()}
            >
              <RefreshCw size={15} />
              读取最新版本并重新核对
            </button>
          </div>
        )}
        <div
          ref={resultRegion}
          tabIndex={-1}
          className="oe-preview"
          aria-label="操作影响预览"
        >
          <h3>操作影响预览</h3>
          <PendingFeedback
            active={!!busy}
            message={
              busy === "publish"
                ? "正在等待操作回执，请保留输入。未知结果重试会复用原操作标识。"
                : "正在核对当前空间的事实、历史、候选和定义引用。"
            }
          />
          {error != null && (
            <p className="oe-error" role="alert">
              {errorMessage(error)}
            </p>
          )}
          {notice && (
            <p className="oe-notice" role="status">
              {notice}
            </p>
          )}
          {currentPreview ? (
            <>
              <div
                className={`oc-verdict ${currentPreview.can_publish ? "is-valid" : "is-blocked"}`}
                role="status"
              >
                <ShieldAlert size={17} aria-hidden="true" />
                <strong>
                  {currentPreview.can_publish
                    ? "核对完成，请检查影响后提交"
                    : "当前引用或规则阻止此操作"}
                </strong>
                {!currentPreview.can_publish && (
                  <ul>
                    {currentPreview.impact.violations.map((item, index) => (
                      <li key={index}>
                        {item.code === "definition_in_use"
                          ? "此定义仍被使用，不能永久删除。"
                          : item.code === "inactive_reference"
                            ? "依赖的定义尚未启用，请先恢复引用。"
                            : "此操作与当前数据或定义规则不兼容。"}{" "}
                        · {item.count} 项
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <OntologyRiskPanel
                preview={currentPreview}
                confirmation={confirmation}
                disabled={locked || !!busy || needsRebase}
                onConfirmation={(value) => {
                  setConfirmation(value);
                  operation.current = null;
                }}
              />
              {target.action === "delete" &&
                !currentPreview.can_publish &&
                resource?.active !== false &&
                resource?.allowed_actions?.includes("deactivate") &&
                onAlternative && (
                  <button
                    className="oe-button"
                    onClick={() =>
                      onAlternative({ ...target, action: "deactivate" })
                    }
                  >
                    保留历史，改为停用
                  </button>
                )}
            </>
          ) : (
            <p className="oe-muted">
              {target.action === "delete" || target.action === "deactivate"
                ? "先检查完整引用范围，再决定是否继续。统计不可用时不会执行。"
                : "预览只核对定义和数据，不会写入。"}
            </p>
          )}
        </div>
        <label className="oe-field oe-reason" htmlFor={`${id}-reason`}>
          操作说明（可选）
          <textarea
            id={`${id}-reason`}
            rows={2}
            value={reason}
            maxLength={2000}
            disabled={locked}
            onChange={(event) => {
              setReason(event.target.value);
              operation.current = null;
            }}
          />
        </label>
        <details className="oe-copy">
          <summary>查看并复制草稿</summary>
          <textarea
            readOnly
            rows={6}
            aria-label="可复制的本体操作草稿"
            value={JSON.stringify(
              {
                ...request,
                ...(reason.trim() ? { reason: reason.trim() } : {}),
              },
              null,
              2,
            )}
            onFocus={(event) => event.target.select()}
          />
        </details>
        <footer className="oe-actions">
          <button
            className="oe-button"
            disabled={busy === "publish"}
            onClick={close}
          >
            取消操作
          </button>
          <div>
            <button
              className="oe-button"
              disabled={!canValidate}
              onClick={() => void validate()}
            >
              {busy === "validate" ? (
                <LoaderCircle size={16} className="spin" />
              ) : (
                <Eye size={16} />
              )}
              预览影响
            </button>
            <button
              className={`oe-button ${target.action === "delete" || target.action === "deactivate" ? "oe-danger" : "oe-primary"}`}
              disabled={!canPublish}
              onClick={() => void publish()}
            >
              {busy === "publish" ? (
                <LoaderCircle size={16} className="spin" />
              ) : (
                <Save size={16} />
              )}
              {busy === "publish"
                ? "正在提交"
                : operation.current
                  ? "使用原操作重试"
                  : `确认${actionName}`}
            </button>
          </div>
        </footer>
      </section>
      {discard && (
        <WorkspaceDialog
          title="放弃未提交的输入？"
          onClose={() => setDiscard(false)}
        >
          <div className="oe-discard">
            <p>尚未提交定义变更。继续编辑会保留输入；放弃只清除本地草稿。</p>
            <div>
              <button className="oe-button" onClick={() => setDiscard(false)}>
                继续编辑
              </button>
              <button className="oe-button oe-danger" onClick={onClose}>
                放弃草稿
              </button>
            </div>
          </div>
        </WorkspaceDialog>
      )}
    </>
  );
}
