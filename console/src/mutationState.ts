import { ApiError } from "./api";
import type { JsonRecord, Memory, Review } from "./types";

export type MutationIntent = { fingerprint: string; id: string };

export type MemoryEditDraft = {
  name: string;
  content: string;
  eventTime: string;
  preferenceKey: string;
  preferenceValue: string;
  decisionContext: string;
};

function editableValues(memory: Memory): MemoryEditDraft {
  return {
    name: memory.name,
    content: memory.description,
    eventTime: memory.eventTime ?? "",
    preferenceKey: String(
      memory.properties.hermes_preferenceKey ??
        memory.properties.preference_key ??
        "",
    ),
    preferenceValue: String(
      memory.properties.hermes_preferenceValue ??
        memory.properties.preference_value ??
        "",
    ),
    decisionContext: String(
      memory.properties.hermes_decisionContext ??
        memory.properties.decision_context ??
        "",
    ),
  };
}

export function rebaseMemoryDraft(
  draft: MemoryEditDraft,
  previous: Memory,
  latest: Memory,
): MemoryEditDraft {
  const before = editableValues(previous),
    incoming = editableValues(latest);
  const next = { ...draft };
  for (const key of Object.keys(before) as (keyof MemoryEditDraft)[]) {
    // Preserve deliberate edits, including clearing an optional value. Fields
    // the user did not change must follow the newly acknowledged baseline.
    if (draft[key] === before[key]) next[key] = incoming[key];
  }
  return next;
}

// A retry of the same intent must carry the same key after a lost response.
// Changing a draft or rebasing its version creates a genuinely new intent.
export function operationForIntent(
  previous: MutationIntent | null,
  intent: unknown,
  newId: () => string = () => crypto.randomUUID(),
): MutationIntent {
  const fingerprint = JSON.stringify(intent);
  return previous?.fingerprint === fingerprint
    ? previous
    : { fingerprint, id: newId() };
}

export function writeFailure(failure: unknown): unknown {
  if (failure instanceof ApiError && failure.status === 409)
    return new ApiError(
      "记录或批次已发生变化，本次输入仍保留。请先核对最新内容，再决定是否重新提交。",
      409,
    );
  return failure;
}

export function isPendingReview(status: string): boolean {
  return [
    "pending",
    "review",
    "needs_review",
    "pending_review",
    "awaiting_review",
  ].includes(status);
}

export function reviewChanges(
  review: Review,
  drafts: Record<string, { name: string; content: string }>,
): JsonRecord {
  const changes: JsonRecord = {};
  review.entities.forEach((memory) => {
    const draft = drafts[memory.uri];
    if (!draft) return;
    const change: JsonRecord = {};
    if (draft.name.trim() !== memory.name) change.name = draft.name.trim();
    if (draft.content.trim() !== memory.description)
      change.props = {
        [memory.type === "decision" ? "decision_text" : "description"]:
          draft.content.trim(),
      };
    if (Object.keys(change).length) changes[memory.uri] = change;
  });
  return changes;
}
