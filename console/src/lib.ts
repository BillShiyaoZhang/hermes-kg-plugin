import type { EntityType } from "./types";

export const typeLabels: Record<EntityType, string> = {
  person: "人物",
  event: "事件",
  decision: "决策",
  preference: "偏好",
  location: "地点",
  concept: "概念",
  unknown: "记忆",
};
export function formatDate(value?: string, includeTime = false): string {
  if (!value) return "暂无记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    year: "numeric",
    ...(includeTime
      ? { hour: "2-digit", minute: "2-digit", hour12: false }
      : {}),
  }).format(date);
}
export function relativeTime(value?: string): string {
  if (!value) return "尚未同步";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const minutes = Math.floor((Date.now() - date.getTime()) / 60000);
  if (minutes < 0) return formatDate(value, true);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时前`;
  if (minutes < 10080) return `${Math.floor(minutes / 1440)} 天前`;
  return formatDate(value);
}
export function textValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return value.map(textValue).join("、");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "操作未完成，请重试。";
}
export const jobLabels: Record<string, string> = {
  pending: "等待处理",
  queued: "已排队",
  running: "处理中",
  processing: "处理中",
  extracting: "提取中",
  review: "待审阅",
  needs_review: "待审阅",
  pending_review: "待审阅",
  awaiting_review: "待审阅",
  approved: "已批准",
  rejected: "已拒绝",
  committed: "已写入",
  completed: "已完成",
  succeeded: "已完成",
  success: "已完成",
  failed: "失败",
  error: "失败",
  source_changed: "来源已变化",
  interrupted: "运行中断",
  unknown: "状态未知",
};
export function jobTone(status: string): string {
  return ["failed", "error", "source_changed", "interrupted"].includes(status)
    ? "danger"
    : [
          "running",
          "processing",
          "extracting",
          "pending",
          "queued",
          "review",
          "needs_review",
          "pending_review",
          "awaiting_review",
        ].includes(status)
      ? "amber"
      : ["completed", "committed", "approved", "success", "succeeded"].includes(
            status,
          )
        ? "green"
        : "neutral";
}
