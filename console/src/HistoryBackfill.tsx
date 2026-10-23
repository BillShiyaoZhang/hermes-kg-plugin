import { useEffect, useId, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowDownToLine,
  CalendarDays,
  ChevronDown,
  History,
  LoaderCircle,
  RefreshCw,
} from "lucide-react";
import type { ConsoleApi } from "./api";
import type { SessionSummary } from "./types";
import { errorMessage, formatDate } from "./lib";
import { confirmedBackfillSessions } from "./backfillReceipt";

export default function HistoryBackfill({
  api,
  enabled,
  refreshKey,
  refresh,
  notify,
}: {
  api: ConsoleApi;
  enabled: boolean;
  refreshKey: number;
  refresh: () => void;
  notify: (message: string, error?: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [queued, setQueued] = useState<number | null>(null);
  const [slow, setSlow] = useState(false);
  const descriptionId = useId();
  const selectionId = useId();
  const requestGeneration = useRef(0);
  useEffect(() => {
    requestGeneration.current += 1;
    // A temporary outage or expired token must not discard the user's choice.
    // A fresh metadata response rechecks which selected sessions still exist.
    setBusy(false);
    setLoading(false);
    return () => {
      requestGeneration.current += 1;
    };
  }, [api, enabled]);
  useEffect(() => {
    setSlow(false);
    if (!loading && !busy) return;
    const timer = window.setTimeout(() => setSlow(true), 10000);
    return () => window.clearTimeout(timer);
  }, [loading, busy]);
  useEffect(() => {
    if (!enabled || !open) return;
    let active = true;
    setLoading(true);
    setLoadError(null);
    api
      .sessions()
      .then((value) => {
        if (!active) return;
        setSessions(value);
        setSelected((current) =>
          current.filter((id) => value.some((session) => session.id === id)),
        );
      })
      .catch((failure) => {
        if (active) setLoadError(failure);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [api, enabled, open, refreshKey]);
  const enqueue = async () => {
    if (busy || loading || !enabled || !selected.length) return;
    const generation = requestGeneration.current;
    const requested = [...selected];
    setBusy(true);
    setActionError(null);
    setQueued(null);
    try {
      const receipt = await api.backfill(requested);
      if (generation !== requestGeneration.current) return;
      const confirmed = confirmedBackfillSessions(receipt, requested);
      setQueued(confirmed.length);
      setSelected((current) => current.filter((id) => !confirmed.includes(id)));
      if (confirmed.length < requested.length) {
        setActionError(
          new Error(
            "部分会话尚未获得入队确认，已保留未确认的选择。请先核对任务状态。",
          ),
        );
      }
      notify(
        `${confirmed.length} 个会话已确认加入回填队列。抽取和写入尚未完成，请在任务记录查看结果。`,
      );
      refresh();
    } catch (failure) {
      if (generation !== requestGeneration.current) return;
      setActionError(failure);
    } finally {
      if (generation === requestGeneration.current) setBusy(false);
    }
  };
  return (
    <details
      className="content-panel backfill-panel"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        <span>
          <History size={17} aria-hidden="true" />
          选择历史会话回填
        </span>
        <span>
          按需选择，最多 20 个<ChevronDown size={15} />
        </span>
      </summary>
      <div className="backfill-body">
        <p className="backfill-description" id={descriptionId}>
          此列表只展示会话时间、来源和消息数。加入队列后，服务会读取所选会话正文，交给已配置的模型抽取记忆；若配置的是外部服务，相关正文会发送到该服务。原有抽取进度不会被重置。
        </p>
        {!enabled ? (
          <p className="backfill-empty">
            连接或提取能力暂不可用。已保留当前选择；恢复后请刷新核对可回填会话。
          </p>
        ) : loading ? (
          <div className="backfill-loading" role="status">
            <LoaderCircle className="spin" size={17} aria-hidden="true" />
            正在读取会话元信息
          </div>
        ) : !sessions.length && !loadError ? (
          <p className="backfill-empty">
            没有可展示的历史会话。请确认记忆服务已接入会话来源。
          </p>
        ) : (
          <fieldset
            className="backfill-session-group"
            aria-describedby={`${descriptionId} ${selectionId}`}
            disabled={busy || !!loadError}
          >
            <legend>可回填会话 · 当前列出 {sessions.length} 个</legend>
            <div className="backfill-sessions">
              {sessions.map((session) => (
                <label
                  key={session.id}
                  className={selected.includes(session.id) ? "selected" : ""}
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(session.id)}
                    disabled={
                      busy ||
                      !!loadError ||
                      (!selected.includes(session.id) && selected.length >= 20)
                    }
                    onChange={(event) =>
                      setSelected((current) =>
                        event.target.checked
                          ? [...current, session.id]
                          : current.filter((id) => id !== session.id),
                      )
                    }
                  />
                  <CalendarDays size={16} aria-hidden="true" />
                  <span>
                    <strong>{formatDate(session.startedAt, true)}</strong>
                    <code title={session.id}>{session.id}</code>
                  </span>
                  <span className="session-source">{session.source}</span>
                  <span className="session-message-count">
                    {session.messageCount === null
                      ? "消息数未知"
                      : `${session.messageCount} 条消息`}
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
        )}
        {slow && (
          <p className="backfill-feedback" role="status">
            {busy
              ? "仍在等待入队确认。请勿重复提交；可在本页任务记录核对结果。"
              : "仍在读取会话元数据。可以继续浏览，稍后刷新会话重试。"}
          </p>
        )}
        {queued !== null && (
          <p className="backfill-feedback" role="status">
            已确认 {queued}{" "}
            个会话入队。抽取与写入尚未完成；可离开本页，稍后在“同步与任务”的任务记录核对处理结果。
          </p>
        )}
        {!!loadError && (
          <div className="backfill-error" role="alert">
            <AlertCircle size={16} aria-hidden="true" />
            <p>
              会话元数据未能更新。{errorMessage(loadError)}{" "}
              已保留当前选择，请刷新核对后再提交。
            </p>
          </div>
        )}
        {!!actionError && (
          <div className="backfill-error" role="alert">
            <AlertCircle size={16} />
            <p>
              {errorMessage(actionError)}{" "}
              {selected.length > 0 && "已保留当前选择。"}
            </p>
          </div>
        )}
        <div className="backfill-actions">
          <span id={selectionId} role="status">
            已选择 {selected.length} / 20 个会话
            {selected.length >= 20 && "，已达到上限"}
          </span>
          <div>
            <button
              className="button button-quiet"
              disabled={!enabled || loading || busy}
              onClick={refresh}
            >
              <RefreshCw size={14} />
              刷新会话
            </button>
            <button
              className="button button-secondary"
              disabled={
                !enabled || !selected.length || busy || loading || !!loadError
              }
              onClick={() => void enqueue()}
            >
              {busy ? (
                <LoaderCircle size={14} className="spin" />
              ) : (
                <ArrowDownToLine size={14} />
              )}
              {busy
                ? "正在提交入队"
                : `使用配置的模型回填${selected.length ? ` ${selected.length} 个会话` : ""}`}
            </button>
          </div>
        </div>
      </div>
    </details>
  );
}
