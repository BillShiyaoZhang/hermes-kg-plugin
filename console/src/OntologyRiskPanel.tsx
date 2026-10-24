import { AlertTriangle, ShieldCheck } from "lucide-react";
import { useId } from "react";
import type { OntologyValidationResult } from "./ontologyTypes";
import { displayCount } from "./ontologyHelpers";

export default function OntologyRiskPanel({
  preview,
  confirmation,
  onConfirmation,
  disabled = false,
}: {
  preview: OntologyValidationResult;
  confirmation: string;
  onConfirmation: (value: string) => void;
  disabled?: boolean;
}) {
  const id = useId();
  const risk = preview.risk;
  if (!risk) return null;
  const high = risk.level === "high";
  return (
    <section
      className={`oc-risk is-${risk.level}`}
      aria-label="操作风险与引用影响"
    >
      <h3>
        {high ? (
          <AlertTriangle size={17} aria-hidden="true" />
        ) : (
          <ShieldCheck size={17} aria-hidden="true" />
        )}
        {high
          ? "高风险 · 明确确认"
          : risk.level === "medium"
            ? "结构变更 · 先核对影响"
            : "低风险 · 展示信息变更"}
      </h3>
      <ul>
        {risk.reasons.map((reason, index) => (
          <li key={index}>{reason}</li>
        ))}
      </ul>
      {risk.level === "medium" && (
        <p className="oc-followup">
          候选批准和归档恢复会按发布后的规则重新校验；本次不改写历史内容。
        </p>
      )}
      {preview.impact.definitions?.map((item) => (
        <div className="oc-references" key={`${item.kind}:${item.key}`}>
          <strong>{item.label}</strong>
          <span className="oc-definition-id">
            <code>{item.key}</code>
          </span>
          <dl>
            <div>
              <dt>当前实体</dt>
              <dd>{displayCount(item.active_nodes)}</dd>
            </div>
            <div>
              <dt>当前关系</dt>
              <dd>{displayCount(item.active_relations)}</dd>
            </div>
            <div>
              <dt>历史实体</dt>
              <dd>{displayCount(item.archived_nodes)}</dd>
            </div>
            <div>
              <dt>历史关系</dt>
              <dd>{displayCount(item.archived_relations)}</dd>
            </div>
            <div>
              <dt>待审候选批次</dt>
              <dd>{displayCount(item.pending_reviews)}</dd>
            </div>
            <div>
              <dt>历史修订</dt>
              <dd>{displayCount(item.history_revisions)}</dd>
            </div>
            <div>
              <dt>定义引用</dt>
              <dd>{displayCount(item.definition_references)}</dd>
            </div>
          </dl>
        </div>
      ))}
      {risk.requires_confirmation && preview.can_publish && (
        <label className="oe-field oc-confirmation" htmlFor={id}>
          输入“{risk.confirmation_phrase}”确认此操作
          <input
            id={id}
            value={confirmation}
            disabled={disabled}
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => onConfirmation(event.target.value)}
          />
          <small>
            只有这次已核对的变更会被提交；版本或引用变化后需要重新预览。
          </small>
        </label>
      )}
    </section>
  );
}
