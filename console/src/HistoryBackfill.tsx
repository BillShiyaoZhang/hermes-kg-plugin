import { useEffect, useState } from "react";
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
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    if (!enabled || !open) return;
    let active = true;
    setLoading(true);
    setError(null);
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
        if (active) setError(failure);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [api, enabled, open, refreshKey]);
  const enqueue = async () => {
    setBusy(true);
    setError(null);
    try {
      const receipt = await api.backfill(selected);
      const count =
        typeof receipt.queued_sessions === "number"
          ? receipt.queued_sessions
          : Array.isArray(receipt.queued_sessions)
            ? receipt.queued_sessions.length
            : selected.length;
      notify(`${count} 个会话已加入回填队列。可点击“立即同步”查看处理结果。`);
      setSelected([]);
      refresh();
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  };
  return (
    <details
      className="content-panel backfill-panel"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        <span>
          <History size={17} />
          选择历史会话回填
        </span>
        <span>
          按需选择，最多 20 个<ChevronDown size={15} />
        </span>
      </summary>
      <div className="backfill-body">
        <p className="backfill-description">
          只展示时间、来源与消息数，不读取聊天正文。选定的会话将进入提取流程，使用
          Hermes 配置的模型；已处理会话的原有进度不会被重置。
        </p>
        {!enabled ? (
          <p className="backfill-empty">连接记忆服务后，可选择历史会话。</p>
        ) : loading ? (
          <div className="backfill-loading">
            <LoaderCircle className="spin" size={17} />
            正在读取会话元信息
          </div>
        ) : !sessions.length && !error ? (
          <p className="backfill-empty">
            没有可展示的历史会话。请确认 Hermes 主机上的会话数据库已接入。
          </p>
        ) : (
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
                <CalendarDays size={16} />
                <span>
                  <strong>{formatDate(session.startedAt, true)}</strong>
                  <code title={session.id}>{session.id}</code>
                </span>
                <span className="session-source">{session.source}</span>
                <span className="session-message-count">
                  {session.messageCount ?? "—"} 条消息
                </span>
              </label>
            ))}
          </div>
        )}
        {!!error && (
          <div className="backfill-error" role="alert">
            <AlertCircle size={16} />
            <p>{errorMessage(error)}</p>
          </div>
        )}
        <div className="backfill-actions">
          <span>已选择 {selected.length} / 20 个会话</span>
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
              disabled={!enabled || !selected.length || busy || loading}
              onClick={() => void enqueue()}
            >
              {busy ? (
                <LoaderCircle size={14} className="spin" />
              ) : (
                <ArrowDownToLine size={14} />
              )}
              加入回填队列
            </button>
          </div>
        </div>
      </div>
    </details>
  );
}
