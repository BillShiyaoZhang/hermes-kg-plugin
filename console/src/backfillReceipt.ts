import type { MutationReceipt } from "./types";

/** Only acknowledge sessions the service explicitly confirms. */
export function confirmedBackfillSessions(
  receipt: MutationReceipt,
  requested: readonly string[],
): string[] {
  if (receipt.ok !== true || !Array.isArray(receipt.queued_sessions)) {
    throw new Error(
      "服务未返回可核对的回填回执。请先核对任务状态，再决定是否重试；已保留本次选择。",
    );
  }
  const requestedIds = new Set(requested);
  const queued = receipt.queued_sessions;
  if (
    queued.some((id) => typeof id !== "string" || !requestedIds.has(id)) ||
    new Set(queued).size !== queued.length
  ) {
    throw new Error(
      "回填回执与本次选择不一致。请先核对任务状态；已保留本次选择。",
    );
  }
  return queued as string[];
}
