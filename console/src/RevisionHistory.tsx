import { ArrowDown, ChevronDown } from "lucide-react";
import type { JsonRecord, Revision } from "./types";
import { formatDate, textValue } from "./lib";
import { record } from "./api";

export function revisionFields(state: JsonRecord): Record<string, string> {
  const p = record(state.properties);
  const fields: Record<string, string> = {};
  const add = (label: string, ...keys: string[]) => {
    const key = keys.find(
      (candidate) => p[candidate] !== undefined && p[candidate] !== null,
    );
    if (key) fields[label] = textValue(p[key]);
  };
  add("名称", "foaf_name", "hermes_name", "name");
  add("描述", "hermes_description", "description");
  add("决策内容", "hermes_decisionText", "decision_text");
  add("决策背景", "hermes_decisionContext", "decision_context");
  add("偏好项目", "hermes_preferenceKey", "preference_key");
  add("偏好内容", "hermes_preferenceValue", "preference_value");
  add("备注", "hermes_note", "note");
  add("适用范围", "hermes_semanticScope");
  add("语义状态", "hermes_status");
  add("状态说明", "hermes_statusNote");
  add("时间精度", "hermes_timePrecision");
  for (const [label, key] of [
    ["事件开始", "hermes_eventTime"],
    ["结束边界", "hermes_eventEndTime"],
  ]) {
    if (typeof p[key] === "string")
      fields[label] = formatDate(p[key] as string, true);
  }
  if (Array.isArray(state.labels))
    fields["使用状态"] = state.labels.includes("KGQuarantined")
      ? "已停用"
      : "使用中";
  if (Array.isArray(state.relations))
    fields["关联数量"] = `${state.relations.length} 条`;
  return fields;
}

export function revisionDiff(current: JsonRecord, previous?: JsonRecord) {
  const now = revisionFields(current),
    before = previous ? revisionFields(previous) : {};
  const keys = new Set([...Object.keys(now), ...Object.keys(before)]);
  return [...keys]
    .filter((key) => !previous || now[key] !== before[key])
    .map((label) => ({ label, before: before[label], after: now[label] }));
}
const ACTION_LABELS: Record<string, string> = {
  correct: "人工更正",
  corrected: "人工更正",
  archive: "停用记忆",
  archived: "停用记忆",
  restore: "恢复记忆",
  restored: "恢复记忆",
  create: "建立记忆",
  created: "建立记忆",
  write: "写入记忆",
  update: "更新记忆",
};

export default function RevisionHistory({
  revisions,
}: {
  revisions: Revision[];
}) {
  return (
    <div className="revision-list">
      {revisions.map((revision, index) => {
        const previous = revisions[index + 1];
        const hasPrevious =
          !!previous && Object.keys(previous.state).length > 0;
        const changes = revisionDiff(
          revision.state,
          hasPrevious ? previous.state : undefined,
        );
        return (
          <div key={revision.id}>
            <span className="revision-dot" />
            <div>
              <strong>
                {ACTION_LABELS[revision.action] ?? revision.action}
              </strong>
              <time>
                {formatDate(revision.createdAt, true)}
                {revision.version !== undefined && ` · v${revision.version}`}
              </time>
              {revision.reason && <p>{revision.reason}</p>}
              <details className="revision-details">
                <summary>
                  {hasPrevious ? "查看本次更改" : "查看本版内容"}
                  <ChevronDown size={13} />
                </summary>
                {changes.length ? (
                  <div className="revision-diff">
                    {changes.map((change) => (
                      <div key={change.label}>
                        <span>{change.label}</span>
                        {hasPrevious && (
                          <>
                            <del>{change.before ?? "未记录"}</del>
                            <ArrowDown size={11} />
                          </>
                        )}
                        <ins>{change.after ?? "已移除"}</ins>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="revision-no-change">
                    {Object.keys(revision.state).length
                      ? "可读内容未变化；本次修订保留了状态或来源的更新。"
                      : "此版本没有可展示的内容快照。"}
                  </p>
                )}
                {!hasPrevious && (
                  <p className="revision-baseline">
                    未提供更早版本的内容快照，以上展示本版记录。
                  </p>
                )}
              </details>
            </div>
          </div>
        );
      })}
    </div>
  );
}
