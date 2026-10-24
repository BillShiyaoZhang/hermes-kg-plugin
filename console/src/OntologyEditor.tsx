import { useEffect, useId, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowRight,
  Check,
  Eye,
  LoaderCircle,
  RefreshCw,
  Save,
} from "lucide-react";
import { ApiError } from "./api";
import { errorMessage } from "./lib";
import PendingFeedback from "./PendingFeedback";
import { WorkspaceDialog } from "./WorkspaceDialogs";
import { displayCount } from "./ontologyHelpers";
import { ontologyPropertyTitle } from "./ontologyPresentation";
import { ontologyDefinitionLabel, ontologyTypeLabel } from "./ontologyV3";
import type {
  OntologyProperty,
  OntologyPublishRequest,
  OntologySchema,
  OntologyValidationResult,
} from "./ontologyTypes";
import {
  ONTOLOGY_FIELD_LABELS,
  ontologyDraftChanges,
  ontologyDraftFrom,
  ontologyDraftIssues,
  ontologyEditableFields,
  ontologyEditConstraints,
  ontologyEditRequest,
  ontologyEditResource,
  ontologyScopeOptions,
  ontologyValidationMatches,
  rebaseOntologyDraft,
  type OntologyEditField,
  type OntologyEditorDraft,
  type OntologyEditorTransport,
  type OntologyEditTarget,
} from "./ontologyEditorHelpers";
import "./ontologyEditor.css";

export interface OntologyEditorProps {
  schema: OntologySchema;
  target: OntologyEditTarget;
  transport: OntologyEditorTransport;
  writeEnabled?: boolean;
  onPublished: (schema: OntologySchema) => void;
  onClose: () => void;
  onInteractionLockChange?: (locked: boolean) => void;
  onReconnect?: () => void;
}
type Busy = "validate" | "publish" | "read" | null;
const EMPTY_DRAFT: OntologyEditorDraft = {
  label: "",
  description: "",
  aliases: "",
  domain: [],
  range: [],
  required_for: [],
};
const fingerprint = (value: unknown) => JSON.stringify(value);
const violationTitle = (code: string) =>
  (
    ({
      required_property_missing: "现有记录缺少必填属性",
      missing_required_property: "现有记录缺少必填属性",
      property_domain_violation: "现有属性超出适用类型",
      property_range_violation: "现有属性值与类型不兼容",
      relation_domain_violation: "现有关系起点超出规则",
      relation_range_violation: "现有关系终点超出规则",
      incompatible_value: "现有属性值与类型不兼容",
    }) as Record<string, string>
  )[code] || "现有数据与草案约束不兼容";

export default function OntologyEditor({
  schema,
  target,
  transport,
  writeEnabled = true,
  onPublished,
  onClose,
  onInteractionLockChange,
  onReconnect,
}: OntologyEditorProps) {
  const id = useId();
  // This snapshot is deliberate: background refreshes never overwrite a draft.
  const [baseline, setBaseline] = useState(() => structuredClone(schema));
  const parentVersionAtBaseline = useRef(schema.version);
  const resource = ontologyEditResource(baseline, target);
  const [draft, setDraft] = useState<OntologyEditorDraft>(() =>
    resource ? ontologyDraftFrom(resource) : EMPTY_DRAFT,
  );
  const [touched, setTouched] = useState<OntologyEditField[]>([]);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<Busy>(null);
  const busyRef = useRef<Busy>(null);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState("");
  const [conflict, setConflict] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [preview, setPreview] = useState<{
    key: string;
    result: OntologyValidationResult;
  } | null>(null);
  const operation = useRef<{
    key: string;
    request: OntologyPublishRequest;
  } | null>(null);
  const readController = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const mounted = useRef(true);
  const reportLock = useRef(onInteractionLockChange);
  reportLock.current = onInteractionLockChange;
  const fields = ontologyEditableFields(baseline, target);
  const changes = ontologyDraftChanges(baseline, target, draft);
  const changedFields = Object.keys(changes) as OntologyEditField[];
  const dirty = changedFields.length > 0 || !!reason.trim();
  const request = ontologyEditRequest(baseline, target, draft);
  const requestKey = fingerprint(request);
  const issues = ontologyDraftIssues(baseline, target, draft);
  const scopeChanged = schema.namespace !== baseline.namespace;
  const versionChanged =
    schema.namespace === baseline.namespace &&
    typeof schema.version === "number" &&
    schema.version !== parentVersionAtBaseline.current &&
    schema.version > (baseline.version ?? -1);
  const needsRebase = conflict || versionChanged;
  const currentPreview = preview?.key === requestKey ? preview.result : null;
  const canValidate =
    writeEnabled &&
    !!fields.length &&
    !scopeChanged &&
    !needsRebase &&
    !busy &&
    !!changedFields.length &&
    !issues.length;
  const canPublish = canValidate && currentPreview?.can_publish === true;
  const resultRegion = useRef<HTMLDivElement>(null);
  const editorRegion = useRef<HTMLElement>(null);

  useEffect(() => {
    mounted.current = true;
    reportLock.current?.(true);
    editorRegion.current
      ?.querySelector<HTMLInputElement>("input")
      ?.focus({ preventScroll: true });
    return () => {
      mounted.current = false;
      readController.current?.abort();
      reportLock.current?.(false);
    };
  }, []);
  useEffect(() => {
    if (!writeEnabled) {
      setPreview(null);
      setNotice("连接恢复后，请重新预览影响再发布。草稿仍保留在此编辑器。");
    }
  }, [writeEnabled]);
  useEffect(() => {
    if (error || preview) resultRegion.current?.focus({ preventScroll: true });
  }, [error, preview]);
  useEffect(() => {
    if (!dirty && busy !== "publish") return;
    const preventLeave = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", preventLeave);
    return () => window.removeEventListener("beforeunload", preventLeave);
  }, [dirty, busy]);

  const setWorking = (next: Busy) => {
    busyRef.current = next;
    setBusy(next);
  };
  const cancelRead = () => {
    readController.current?.abort();
    generation.current++;
    if (busyRef.current !== "publish") setWorking(null);
  };
  const change = <K extends OntologyEditField>(
    field: K,
    value: OntologyEditorDraft[K],
  ) => {
    if (busyRef.current === "publish") return;
    reportLock.current?.(true);
    cancelRead();
    setDraft((current) => ({ ...current, [field]: value }));
    setTouched((current) =>
      current.includes(field) ? current : [...current, field],
    );
    setPreview(null);
    setError(null);
    setNotice("");
    operation.current = null;
  };
  const requestClose = () => {
    if (busyRef.current === "publish") {
      setNotice("正在等待发布回执。请保留编辑器，服务确认后再关闭。");
      return;
    }
    cancelRead();
    if (dirty) setConfirmDiscard(true);
    else onClose();
  };
  const recordFailure = (failure: unknown) => {
    setError(failure);
    if (failure instanceof ApiError && failure.status === 409) {
      setConflict(true);
      setPreview(null);
      setNotice(
        "版本或数据已变化。输入没有丢失，请读取最新版本并重新核对影响。",
      );
    }
  };
  const validate = async () => {
    if (!canValidate || busyRef.current) return;
    reportLock.current?.(true);
    const controller = new AbortController();
    readController.current?.abort();
    readController.current = controller;
    const ticket = ++generation.current;
    setWorking("validate");
    setError(null);
    setNotice("");
    try {
      const result = await transport.validate(request, controller.signal);
      if (
        controller.signal.aborted ||
        ticket !== generation.current ||
        !mounted.current
      )
        return;
      if (!ontologyValidationMatches(result, request))
        throw new Error(
          "服务返回的影响预览不属于当前空间或基线版本。草稿未发布。",
        );
      setPreview({ key: requestKey, result });
    } catch (failure) {
      if (!controller.signal.aborted && mounted.current) recordFailure(failure);
    } finally {
      if (ticket === generation.current && mounted.current) setWorking(null);
    }
  };
  const readLatest = async () => {
    if (!transport.read || !writeEnabled || busyRef.current || scopeChanged)
      return;
    reportLock.current?.(true);
    const controller = new AbortController();
    readController.current = controller;
    const ticket = ++generation.current;
    setWorking("read");
    setError(null);
    setPreview(null);
    try {
      const latest = await transport.read(controller.signal);
      if (
        controller.signal.aborted ||
        ticket !== generation.current ||
        !mounted.current
      )
        return;
      if (latest.namespace !== baseline.namespace)
        throw new Error(
          "当前服务已切换空间。不能将此草稿发布到其他空间，请复制草稿后放弃或返回原空间。",
        );
      const latestResource = ontologyEditResource(latest, target);
      const latestFields = ontologyEditableFields(latest, target);
      if (
        !latestResource ||
        !latestFields.length ||
        changedFields.some((field) => !latestFields.includes(field))
      )
        throw new Error(
          "最新版本已不支持此草稿涉及的编辑字段。输入仍保留，可复制或明确放弃。",
        );
      setDraft(rebaseOntologyDraft(latestResource, draft, touched));
      // A parent may still hold the pre-read version, including one restored backwards.
      parentVersionAtBaseline.current = schema.version;
      setBaseline(structuredClone(latest));
      setConflict(false);
      operation.current = null;
      setNotice(
        `已读取版本 ${latest.version}。你的编辑字段已保留，未编辑字段采用最新定义；请重新预览影响。`,
      );
    } catch (failure) {
      if (!controller.signal.aborted && mounted.current) recordFailure(failure);
    } finally {
      if (ticket === generation.current && mounted.current) setWorking(null);
    }
  };
  const publish = async () => {
    if (!canPublish || busyRef.current) return;
    reportLock.current?.(true);
    const key = fingerprint([request, reason.trim()]);
    if (operation.current?.key !== key)
      operation.current = {
        key,
        request: {
          ...request,
          operation_id: crypto.randomUUID(),
          ...(reason.trim() ? { reason: reason.trim() } : {}),
        },
      };
    const publishRequest = operation.current.request;
    setWorking("publish");
    setError(null);
    setNotice("");
    try {
      const result = await transport.publish(publishRequest);
      if (!mounted.current) return;
      if (
        !result.ok ||
        result.namespace !== baseline.namespace ||
        result.schema.namespace !== baseline.namespace ||
        result.operation_id !== publishRequest.operation_id ||
        result.version <= (baseline.version ?? -1)
      )
        throw new Error(
          "服务未返回与此次发布匹配的完整回执。请保留草稿，使用原操作重试或读取最新版本核对。",
        );
      onPublished(result.schema);
    } catch (failure) {
      if (mounted.current) recordFailure(failure);
    } finally {
      if (mounted.current) setWorking(null);
    }
  };
  const toggleScope = (
    field: "domain" | "range",
    value: string,
    checked: boolean,
  ) => {
    const next = checked
      ? value === "any"
        ? ["any"]
        : [...draft[field].filter((item) => item !== "any"), value]
      : draft[field].filter((item) => item !== value);
    change(field, [...new Set(next)]);
  };
  const resourceTitle = resource
    ? target.kind === "properties"
      ? ontologyPropertyTitle(resource as OntologyProperty)
      : ontologyDefinitionLabel(
          resource as Exclude<typeof resource, OntologyProperty>,
        )
    : target.key;
  const initial = resource ? ontologyDraftFrom(resource) : EMPTY_DRAFT;
  const constraints = ontologyEditConstraints(baseline, target);
  const renderScope = (
    field: "domain" | "range",
    label: string,
    values: string[],
    datatype = false,
  ) => (
    <fieldset className="oe-choice-field">
      <legend>{label}</legend>
      <div>
        {values.map((value) => (
          <label key={value}>
            <input
              type={datatype ? "radio" : "checkbox"}
              name={datatype ? `${id}-${field}` : undefined}
              checked={draft[field].includes(value)}
              disabled={busy === "publish" || !writeEnabled || scopeChanged}
              onChange={(event) =>
                datatype
                  ? change(field, [value])
                  : toggleScope(field, value, event.target.checked)
              }
            />
            <span>{datatype ? value : ontologyTypeLabel(value)}</span>
          </label>
        ))}
      </div>
      <p>
        {datatype
          ? "仅列出当前运行服务支持的值类型。"
          : "可在本体内置范围内调整或撤回自定义限制；选择任意业务类型会清除具体类型。"}
      </p>
    </fieldset>
  );

  return (
    <>
      <section
        ref={editorRegion}
        className="oe-editor"
        role="region"
        aria-label="编辑本体定义"
        data-ontology-target={`${target.kind}:${target.key}`}
      >
        <header className="oe-header">
          <span>
            {target.kind === "entity_types"
              ? "实体类型"
              : target.kind === "relations"
                ? "关系定义"
                : "属性定义"}{" "}
            · 草稿
          </span>
          <h2>编辑本体定义</h2>
        </header>
        <div className="oe-summary">
          <div>
            <strong>{resourceTitle}</strong>
            <span>基线版本 {baseline.version ?? "未知"}</span>
          </div>
          <p>
            草稿不会自动保存。预览影响后再发布到当前空间；既有记录不会被自动改写。
          </p>
        </div>
        <details className="oe-identity">
          <summary>固定标识与作用空间</summary>
          <dl>
            <div>
              <dt>规范标识</dt>
              <dd>
                <code>{target.key}</code>
              </dd>
            </div>
            <div>
              <dt>本体 IRI</dt>
              <dd>
                <code>{resource?.iri}</code>
              </dd>
            </div>
            <div>
              <dt>作用空间</dt>
              <dd>
                <code>{baseline.namespace}</code>
              </dd>
            </div>
          </dl>
          <p>类型、关系和属性标识与保存字段不可修改；此编辑器不创建新类型。</p>
        </details>
        {scopeChanged && (
          <div className="oe-error" role="alert">
            当前空间已变化。此草稿仍属于原空间，发布已阻止；可复制草稿后放弃，或返回原空间继续。
          </div>
        )}
        {!writeEnabled && (
          <div className="oe-warning" role="status">
            <p>服务当前不可写。输入仍保留，恢复连接后须重新预览影响。</p>
            {onReconnect && (
              <button className="oe-button" onClick={onReconnect}>
                重新连接
              </button>
            )}
          </div>
        )}
        {!fields.length ? (
          <p className="oe-warning">当前运行服务没有开放此定义的编辑能力。</p>
        ) : (
          <div className="oe-fields">
            {fields.includes("label") && (
              <label className="oe-field" htmlFor={`${id}-label`}>
                显示名称
                <input
                  id={`${id}-label`}
                  maxLength={120}
                  value={draft.label}
                  disabled={busy === "publish" || scopeChanged}
                  onChange={(event) => change("label", event.target.value)}
                />
              </label>
            )}
            {fields.includes("description") && (
              <label className="oe-field" htmlFor={`${id}-description`}>
                说明
                <textarea
                  id={`${id}-description`}
                  rows={4}
                  maxLength={4000}
                  value={draft.description}
                  disabled={busy === "publish" || scopeChanged}
                  onChange={(event) =>
                    change("description", event.target.value)
                  }
                />
                <small>可清空说明；未编辑的中文展示回退不会被写入本体。</small>
              </label>
            )}
            {fields.includes("aliases") && (
              <label className="oe-field" htmlFor={`${id}-aliases`}>
                兼容名称
                <textarea
                  id={`${id}-aliases`}
                  rows={3}
                  value={draft.aliases}
                  disabled={busy === "publish" || scopeChanged}
                  onChange={(event) => change("aliases", event.target.value)}
                />
                <small>
                  每行一个名称。新增名称以英文字母开头，仅含字母、数字与下划线；发布前会检查冲突并保护内置兼容名称。
                </small>
              </label>
            )}
            {fields.includes("domain") &&
              renderScope(
                "domain",
                target.kind === "relations" ? "允许的起点类型" : "属性适用类型",
                ontologyScopeOptions(
                  baseline,
                  constraints?.domain ?? initial.domain,
                ),
              )}
            {fields.includes("range") &&
              renderScope(
                "range",
                target.kind === "relations" ? "允许的终点类型" : "属性值类型",
                target.kind === "properties"
                  ? (baseline.editing?.datatypes_by_property[target.key] ??
                      initial.range)
                  : ontologyScopeOptions(
                      baseline,
                      constraints?.range ?? initial.range,
                    ),
                target.kind === "properties",
              )}
            {fields.includes("required_for") && (
              <fieldset className="oe-choice-field">
                <legend>在哪些类型中必填</legend>
                <div>
                  {baseline.entity_types.map((type) => (
                    <label key={type.key}>
                      <input
                        type="checkbox"
                        checked={draft.required_for.includes(type.key)}
                        disabled={
                          busy === "publish" ||
                          scopeChanged ||
                          (
                            constraints?.required_for ?? initial.required_for
                          ).includes(type.key) ||
                          (!draft.domain.includes("any") &&
                            !draft.domain.includes(type.key))
                        }
                        onChange={(event) =>
                          change(
                            "required_for",
                            event.target.checked
                              ? [...draft.required_for, type.key]
                              : draft.required_for.filter(
                                  (item) => item !== type.key,
                                ),
                          )
                        }
                      />
                      <span>
                        {ontologyTypeLabel(type.key)}
                        {(
                          constraints?.required_for ?? initial.required_for
                        ).includes(type.key)
                          ? " · 内置要求"
                          : ""}
                      </span>
                    </label>
                  ))}
                </div>
                <p>
                  本体内置必填要求不可撤回；自己新增的要求可调整。新增要求会核对当前空间的既有记录。
                </p>
              </fieldset>
            )}
          </div>
        )}
        {!!issues.length && changedFields.length > 0 && (
          <ul className="oe-issues" aria-label="草稿校验问题">
            {issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        )}
        {needsRebase && !scopeChanged && (
          <div className="oe-warning">
            <strong>需要核对最新版本</strong>
            <p>
              基线或当前数据已有变化，草稿仍保留。读取最新版本后请重新预览，旧预览不能用于发布。
            </p>
            <button
              className="oe-button"
              disabled={!!busy || !writeEnabled || !transport.read}
              onClick={() => void readLatest()}
            >
              <RefreshCw size={15} aria-hidden="true" />
              读取最新版本并重新核对
            </button>
          </div>
        )}
        <div
          className="oe-preview"
          ref={resultRegion}
          tabIndex={-1}
          role="region"
          aria-label="本体编辑影响预览"
          aria-busy={!!busy}
        >
          {!!error && (
            <div className="oe-error" role="alert">
              <AlertCircle size={17} aria-hidden="true" />
              <p>{errorMessage(error)}</p>
            </div>
          )}
          {notice && (
            <p className="oe-notice" role="status">
              {notice}
            </p>
          )}
          <PendingFeedback
            active={!!busy}
            message={
              busy === "publish"
                ? "仍在等待发布回执。请保留此编辑器；未确认时重试会复用原操作标识。"
                : "仍在核对定义和当前空间数据。可以继续保留草稿或取消读取，不编造进度。"
            }
          />
          <h3>
            影响预览 <small>{changedFields.length} 个字段变化</small>
          </h3>
          {!changedFields.length ? (
            <p className="oe-muted">编辑字段后，先预览影响再发布。</p>
          ) : (
            <dl className="oe-change-list">
              {changedFields.map((field) => (
                <div key={field}>
                  <dt>{ONTOLOGY_FIELD_LABELS[field]}</dt>
                  <dd>
                    <span>
                      {Array.isArray(initial[field])
                        ? (initial[field] as string[]).join("、") || "无"
                        : initial[field] || "空"}
                    </span>
                    <ArrowRight size={14} aria-hidden="true" />
                    <strong>
                      {Array.isArray(changes[field])
                        ? (changes[field] as string[]).join("、") || "无"
                        : changes[field] || "空"}
                    </strong>
                  </dd>
                </div>
              ))}
            </dl>
          )}
          {currentPreview && (
            <div
              className={`oe-impact ${currentPreview.can_publish ? "is-valid" : "is-blocked"}`}
            >
              <h4>
                {currentPreview.can_publish ? (
                  <>
                    <Check size={16} aria-hidden="true" />
                    校验通过，可以明确发布
                  </>
                ) : (
                  <>
                    <AlertCircle size={16} aria-hidden="true" />
                    现有数据阻止发布
                  </>
                )}
              </h4>
              <p>
                {(currentPreview.impact.affected_nodes !== undefined ||
                  currentPreview.impact.affected_relations !== undefined) && (
                  <>
                    影响当前空间{" "}
                    {displayCount(currentPreview.impact.affected_nodes)}{" "}
                    条实体、
                    {displayCount(
                      currentPreview.impact.affected_relations,
                    )}{" "}
                    条关系。
                  </>
                )}
                已核对当前空间{" "}
                {displayCount(currentPreview.impact.checked_nodes)} 条实体、
                {displayCount(currentPreview.impact.checked_relations)} 条关系。
                {currentPreview.structural
                  ? "本次包含约束变更。"
                  : "本次为定义展示信息变更。"}
              </p>
              {currentPreview.impact.violations.length > 0 && (
                <ul>
                  {currentPreview.impact.violations.map((violation, index) => (
                    <li key={`${violation.key}:${violation.code}:${index}`}>
                      <strong>{violationTitle(violation.code)}</strong> ·{" "}
                      {displayCount(violation.count)} 条
                      <small>
                        {violation.key} · {violation.code}
                      </small>
                    </li>
                  ))}
                </ul>
              )}
              <p>预览不会批准候选、改写历史记录或迁移数据。</p>
            </div>
          )}
        </div>
        <label className="oe-field oe-reason" htmlFor={`${id}-reason`}>
          发布说明（可选）
          <textarea
            id={`${id}-reason`}
            rows={2}
            maxLength={2000}
            value={reason}
            disabled={busy === "publish" || scopeChanged}
            onChange={(event) => {
              reportLock.current?.(true);
              setReason(event.target.value);
              operation.current = null;
            }}
          />
        </label>
        <details className="oe-copy">
          <summary>查看并复制草稿</summary>
          <p>
            下列内容仅含定义变化、空间与基线标识，可手动复制保存。不会包含令牌或记忆依据。
          </p>
          <textarea
            readOnly
            rows={6}
            aria-label="可复制的本体草稿"
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
            onClick={requestClose}
            disabled={busy === "publish"}
          >
            取消编辑
          </button>
          <div>
            <button
              className="oe-button"
              disabled={!canValidate}
              onClick={() => void validate()}
            >
              {busy === "validate" ? (
                <LoaderCircle size={16} className="spin" aria-hidden="true" />
              ) : (
                <Eye size={16} aria-hidden="true" />
              )}
              {busy === "validate" ? "正在核对影响" : "预览影响"}
            </button>
            <button
              className="oe-button oe-primary"
              disabled={!canPublish}
              onClick={() => void publish()}
            >
              {busy === "publish" ? (
                <LoaderCircle size={16} className="spin" aria-hidden="true" />
              ) : (
                <Save size={16} aria-hidden="true" />
              )}
              {busy === "publish"
                ? "正在发布本体"
                : operation.current
                  ? "使用原操作重试发布"
                  : "发布本体"}
            </button>
          </div>
        </footer>
      </section>
      {confirmDiscard && (
        <WorkspaceDialog
          title="放弃本体草稿？"
          onClose={() => setConfirmDiscard(false)}
        >
          <div className="oe-discard">
            <p>
              此草稿尚未发布。继续编辑会保留所有输入；放弃只清除本地草稿，不更改已发布本体。
            </p>
            <div>
              <button
                className="oe-button"
                onClick={() => setConfirmDiscard(false)}
              >
                继续编辑
              </button>
              <button
                className="oe-button oe-danger"
                onClick={() => {
                  setConfirmDiscard(false);
                  onClose();
                }}
              >
                放弃草稿
              </button>
            </div>
          </div>
        </WorkspaceDialog>
      )}
    </>
  );
}
