import RevisionHistory from "./RevisionHistory";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  Activity,
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  BookOpen,
  CalendarDays,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Clock3,
  Database,
  FileCheck2,
  FileText,
  Fingerprint,
  History,
  Inbox,
  KeyRound,
  Layers3,
  LoaderCircle,
  LockKeyhole,
  LogOut,
  MessageSquareQuote,
  Pause,
  PencilLine,
  Play,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Terminal,
  Unplug,
  UserRound,
  UsersRound,
  X,
} from "lucide-react";
import {
  ApiError,
  createApi,
  initialToken,
  normalizeMemory,
  saveToken,
  stringValue,
} from "./api";
import type { ConsoleApi } from "./api";
import type {
  ConsoleStatus,
  EntityType,
  JsonRecord,
  Memory,
  MemoryPage,
  Review,
  SyncJob,
} from "./types";
import HistoryBackfill from "./HistoryBackfill";
import {
  errorMessage,
  formatDate,
  jobLabels,
  jobTone,
  relativeTime,
  textValue,
  typeLabels,
} from "./lib";

type Page = "memories" | "reviews" | "sync" | "settings";
type Notification = { message: string; error?: boolean };
const PAGE_LABELS: Record<Page, string> = {
  memories: "记忆库",
  reviews: "审阅收件箱",
  sync: "同步与任务",
  settings: "设置与诊断",
};
const PAGE_ICONS = {
  memories: Layers3,
  reviews: Inbox,
  sync: Activity,
  settings: Settings2,
};
const MAIN_TYPES: { value: string; label: string }[] = [
  { value: "", label: "全部记忆" },
  { value: "person", label: "人物" },
  { value: "event", label: "事件" },
  { value: "decision", label: "决策" },
];

function Brand({ small = false }: { small?: boolean }) {
  return (
    <div className={`brand ${small ? "brand-small" : ""}`}>
      <div className="brand-mark" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
      <div>
        <strong>
          Hermes<span> Memory</span>
        </strong>
        <small>个人记忆工作台</small>
      </div>
    </div>
  );
}
function Spinner({ label = "正在加载" }: { label?: string }) {
  return (
    <div className="loading-state">
      <LoaderCircle className="spin" size={22} />
      <span>{label}</span>
    </div>
  );
}
function Badge({
  children,
  tone = "neutral",
  dot = false,
}: {
  children: ReactNode;
  tone?: string;
  dot?: boolean;
}) {
  return (
    <span className={`badge badge-${tone}`}>
      {dot && <i />}
      {children}
    </span>
  );
}
function TypeIcon({ type, size = 19 }: { type: EntityType; size?: number }) {
  const Icon =
    type === "person"
      ? UserRound
      : type === "event"
        ? CalendarDays
        : type === "decision"
          ? Fingerprint
          : type === "preference"
            ? Sparkles
            : type === "concept"
              ? BookOpen
              : Layers3;
  return (
    <span className={`type-icon type-${type}`}>
      <Icon size={size} strokeWidth={1.65} />
    </span>
  );
}
function ErrorBox({
  error,
  retry,
  compact = false,
}: {
  error: unknown;
  retry?: () => void;
  compact?: boolean;
}) {
  return (
    <div className={`error-box ${compact ? "compact" : ""}`} role="alert">
      <AlertCircle size={19} />
      <div>
        <strong>未能完成请求</strong>
        <p>{errorMessage(error)}</p>
        {error instanceof ApiError && error.status === 409 && (
          <p>为避免覆盖已变化的记录，本次操作没有被确认。</p>
        )}
      </div>
      {retry && (
        <button className="button button-quiet" onClick={retry}>
          <RefreshCw size={14} />
          重试
        </button>
      )}
    </div>
  );
}
function EmptyState({
  icon = <Layers3 size={28} strokeWidth={1.3} />,
  title,
  children,
  action,
}: {
  icon?: ReactNode;
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-illustration">
        {icon}
        <span />
        <span />
      </div>
      <h3>{title}</h3>
      <p>{children}</p>
      {action}
    </div>
  );
}
function Overlay({
  children,
  onClose,
  className = "",
  labelledBy,
}: {
  children: ReactNode;
  onClose: () => void;
  className?: string;
  labelledBy: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    ref.current?.focus();
    const handler = (event: KeyboardEvent) => {
      const panels = document.querySelectorAll(".overlay-panel");
      if (panels[panels.length - 1] !== ref.current) return;
      if (event.key === "Escape") onClose();
      if (event.key !== "Tab" || !ref.current) return;
      const elements = Array.from(
        ref.current.querySelectorAll<HTMLElement>(
          'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href], [tabindex="0"]',
        ),
      );
      const first = elements[0],
        last = elements[elements.length - 1];
      if (
        event.shiftKey &&
        (document.activeElement === first ||
          document.activeElement === ref.current)
      ) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener("keydown", handler);
    return () => {
      document.body.style.overflow = overflow;
      document.removeEventListener("keydown", handler);
      before?.focus();
    };
  }, [onClose]);
  return (
    <div
      className={`overlay ${className}`}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="overlay-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        ref={ref}
        tabIndex={-1}
      >
        {children}
      </div>
    </div>
  );
}

export default function App() {
  const [token, setToken] = useState(initialToken);
  const [page, setPage] = useState<Page>("memories");
  const [status, setStatus] = useState<ConsoleStatus | null>(null);
  const [statusError, setStatusError] = useState<unknown>(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [reviews, setReviews] = useState<Review[]>([]);
  const [reviewsError, setReviewsError] = useState<unknown>(null);
  const [reviewsLoading, setReviewsLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [selected, setSelected] = useState<Memory | null>(null);
  const [selectedReview, setSelectedReview] = useState<Review | null>(null);
  const [authOpen, setAuthOpen] = useState(!token);
  const [notice, setNotice] = useState<Notification | null>(null);
  const api = useMemo(() => createApi(() => token), [token]);
  const currentToken = useRef(token);
  currentToken.current = token;
  const notify = useCallback(
    (message: string, error = false) => setNotice({ message, error }),
    [],
  );
  const refresh = useCallback(() => setRefreshKey((value) => value + 1), []);
  const closeMemory = useCallback(() => setSelected(null), []);
  const closeReview = useCallback(() => setSelectedReview(null), []);
  const closeAuth = useCallback(() => setAuthOpen(false), []);

  const fetchStatus = useCallback(async () => {
    if (!token) return;
    setStatusLoading(true);
    try {
      const value = await api.status();
      if (currentToken.current === token) {
        setStatus(value);
        setStatusError(null);
      }
    } catch (error) {
      if (currentToken.current === token) {
        setStatusError(error);
        setStatus(null);
      }
    } finally {
      if (currentToken.current === token) setStatusLoading(false);
    }
  }, [api, token]);
  const fetchReviews = useCallback(async () => {
    if (!token) return;
    setReviewsLoading(true);
    try {
      const value = await api.reviews();
      if (currentToken.current === token) {
        setReviews(value);
        setReviewsError(null);
      }
    } catch (error) {
      if (currentToken.current === token) {
        setReviewsError(error);
        setReviews([]);
      }
    } finally {
      if (currentToken.current === token) setReviewsLoading(false);
    }
  }, [api, token]);
  useEffect(() => {
    void fetchStatus();
    void fetchReviews();
  }, [fetchStatus, fetchReviews, refreshKey]);
  useEffect(() => {
    if (!token) return;
    const timer = window.setInterval(() => {
      void fetchStatus();
      void fetchReviews();
    }, 30000);
    return () => window.clearInterval(timer);
  }, [token, fetchStatus, fetchReviews]);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(null), 6000);
    return () => clearTimeout(timer);
  }, [notice]);
  const disconnect = () => {
    saveToken("");
    setToken("");
    setStatus(null);
    setStatusError(null);
    setReviewsError(null);
    setReviews([]);
    setSelected(null);
    setSelectedReview(null);
    setAuthOpen(true);
  };
  const pendingReviews = reviews.filter(
    (review) => !["approved", "rejected", "committed"].includes(review.status),
  );
  const offline = !token || statusError || status?.connected === false;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Brand />
        <div className="workspace-label">
          <span className="workspace-avatar">H</span>
          <div>
            <strong>个人工作空间</strong>
            <span>HERMES MEMORY CONSOLE</span>
          </div>
          <LockKeyhole size={13} />
        </div>
        <div className="nav-label">工作台</div>
        <nav aria-label="主导航">
          {(Object.keys(PAGE_LABELS) as Page[]).map((key) => {
            const Icon = PAGE_ICONS[key];
            return (
              <button
                key={key}
                className={`nav-item ${page === key ? "active" : ""}`}
                onClick={() => setPage(key)}
                aria-current={page === key ? "page" : undefined}
              >
                <Icon size={18} strokeWidth={1.7} />
                <span>{PAGE_LABELS[key]}</span>
                {key === "reviews" && pendingReviews.length > 0 && (
                  <span className="nav-count">{pendingReviews.length}</span>
                )}
              </button>
            );
          })}
        </nav>
        <div className="sidebar-note">
          <ShieldCheck size={20} strokeWidth={1.5} />
          <strong>记忆始终由你掌控</strong>
          <p>核对来源，纠正理解，决定哪些内容继续被使用。</p>
          <span>
            PERSONAL ALPHA <i />
          </span>
        </div>
        <div className="sidebar-bottom">
          <span className={`connection-dot ${offline ? "off" : "on"}`} />
          <span>
            {!token
              ? "尚未连接服务"
              : statusLoading && !status
                ? "正在检查服务"
                : offline
                  ? "服务需要检查"
                  : "已连接记忆服务"}
          </span>
          <button
            aria-label="连接设置"
            title="连接设置"
            onClick={() => setPage("settings")}
          >
            <Settings2 size={15} />
          </button>
        </div>
      </aside>

      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumbs">
            <span>工作空间</span>
            <ChevronRight size={13} />
            <strong>{PAGE_LABELS[page]}</strong>
          </div>
          <div className="topbar-right">
            <span className="session-label">
              <LockKeyhole size={13} />
              会话令牌连接
            </span>
            <span className="alpha-pill">ALPHA</span>
            <button
              className="avatar-button"
              onClick={() => setPage("settings")}
              aria-label="设置与诊断"
            >
              H
            </button>
          </div>
        </header>
        <main className="main-content">
          {!token && (
            <div className="connection-banner">
              <KeyRound size={18} />
              <div>
                <strong>连接你的个人记忆服务</strong>
                <span>
                  使用服务启动时提供的访问令牌，开始查看记忆与待审内容。
                </span>
              </div>
              <button
                className="button button-secondary"
                onClick={() => setAuthOpen(true)}
              >
                连接服务
                <ArrowRight size={14} />
              </button>
            </div>
          )}
          {token &&
            statusError instanceof ApiError &&
            statusError.status === 401 && (
              <div className="connection-banner error-banner">
                <Unplug size={18} />
                <div>
                  <strong>连接需要重新验证</strong>
                  <span>当前访问令牌无效或已过期。</span>
                </div>
                <button
                  className="button button-secondary"
                  onClick={() => setAuthOpen(true)}
                >
                  重新连接
                </button>
              </div>
            )}
          {status?.compatibility.data_mode === "demo" && (
            <div className="demo-banner" role="status">
              <Sparkles size={18} />
              <div>
                <strong>开发演示环境 · 虚构数据</strong>
                <span>当前内容用于验证产品体验，不代表真实个人记忆。</span>
              </div>
              <span className="demo-tag">DEMO</span>
            </div>
          )}
          {page === "memories" && (
            <MemoryLibrary
              api={api}
              enabled={!!token}
              status={status}
              statusError={statusError}
              statusLoading={statusLoading}
              reviewCount={
                reviewsError || !token || (reviewsLoading && !reviews.length)
                  ? null
                  : pendingReviews.length
              }
              refreshKey={refreshKey}
              refresh={refresh}
              onSelect={setSelected}
              navigate={setPage}
            />
          )}
          {page === "reviews" && (
            <ReviewInbox
              enabled={!!token}
              reviews={reviews}
              loading={reviewsLoading}
              error={reviewsError}
              refresh={refresh}
              onSelect={setSelectedReview}
            />
          )}
          {page === "sync" && (
            <SyncPage
              api={api}
              enabled={!!token}
              status={status}
              statusError={statusError}
              statusLoading={statusLoading}
              refreshKey={refreshKey}
              refresh={refresh}
              notify={notify}
            />
          )}
          {page === "settings" && (
            <SettingsPage
              api={api}
              enabled={!!token}
              status={status}
              statusError={statusError}
              refreshKey={refreshKey}
              refresh={refresh}
              notify={notify}
              connect={() => setAuthOpen(true)}
              disconnect={disconnect}
            />
          )}
        </main>
        <footer className="page-footer">
          <span>Hermes Memory Console</span>
          <span>有来源的记忆，有分寸的理解。</span>
          <span>
            <ShieldCheck size={12} />
            个人工作空间
          </span>
        </footer>
      </div>
      {selected && (
        <MemoryDrawer
          api={api}
          selected={selected}
          onClose={closeMemory}
          onNavigate={setSelected}
          onChanged={refresh}
          notify={notify}
        />
      )}
      {selectedReview && (
        <ReviewDrawer
          api={api}
          review={selectedReview}
          onClose={closeReview}
          onChanged={refresh}
          notify={notify}
        />
      )}
      {authOpen && (
        <AuthDialog
          initialValue=""
          canClose={!!token}
          onClose={closeAuth}
          onConnected={(value) => {
            setToken(saveToken(value));
            setAuthOpen(false);
            setStatusError(null);
            refresh();
          }}
        />
      )}
      {notice && (
        <div
          className={`toast ${notice.error ? "toast-error" : ""}`}
          role="status"
        >
          {notice.error ? <AlertCircle size={18} /> : <CheckCheck size={18} />}
          <span>{notice.message}</span>
          <button onClick={() => setNotice(null)} aria-label="关闭通知">
            <X size={15} />
          </button>
        </div>
      )}
    </div>
  );
}

function PageHeader({
  eyebrow,
  title,
  subtitle,
  children,
}: {
  eyebrow: string;
  title: string;
  subtitle: string;
  children?: ReactNode;
}) {
  return (
    <div className="page-header">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        <p>{subtitle}</p>
      </div>
      <div className="page-actions">{children}</div>
    </div>
  );
}
function RefreshButton({
  refresh,
  loading = false,
}: {
  refresh: () => void;
  loading?: boolean;
}) {
  return (
    <button
      className="button button-secondary"
      onClick={refresh}
      disabled={loading}
    >
      <RefreshCw size={15} className={loading ? "spin" : ""} />
      刷新
    </button>
  );
}

function MemoryLibrary({
  api,
  enabled,
  status,
  statusError,
  statusLoading,
  reviewCount,
  refreshKey,
  refresh,
  onSelect,
  navigate,
}: {
  api: ConsoleApi;
  enabled: boolean;
  status: ConsoleStatus | null;
  statusError: unknown;
  statusLoading: boolean;
  reviewCount: number | null;
  refreshKey: number;
  refresh: () => void;
  onSelect: (memory: Memory) => void;
  navigate: (page: Page) => void;
}) {
  const [type, setType] = useState("");
  const [lifecycle, setLifecycle] = useState("active");
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const [interpret, setInterpret] = useState(false);
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<MemoryPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const isSearch = !!query && lifecycle === "active";
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "k") {
        event.preventDefault();
        searchInput.current?.focus();
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, []);
  useEffect(() => {
    if (!enabled) {
      setData(null);
      setError(null);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    const request = isSearch
      ? api.search(
          query,
          type ? { entity_type: type } : {},
          controller.signal,
          interpret,
        )
      : api.memories(
          { type, status: lifecycle, q: query, offset, limit: 30 },
          controller.signal,
        );
    request
      .then(setData)
      .catch((failure) => {
        if (!controller.signal.aborted) {
          setError(failure);
          setData(null);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [
    api,
    enabled,
    type,
    lifecycle,
    query,
    offset,
    refreshKey,
    isSearch,
    interpret,
  ]);
  const resetSearch = () => {
    setInput("");
    setQuery("");
    setOffset(0);
  };
  return (
    <>
      <PageHeader
        eyebrow="YOUR PERSONAL MEMORY"
        title="记忆库"
        subtitle="重新找到那些人物、经历，以及做出选择时的自己。"
      >
        <RefreshButton refresh={refresh} loading={loading || statusLoading} />
      </PageHeader>
      <div className="metric-grid">
        <div className="metric-card">
          <div className="metric-top">
            <span>可用记忆</span>
            <Layers3 size={17} />
          </div>
          <div className="metric-value">
            {status?.totalMemories?.toLocaleString() ?? "—"}
            <small>条</small>
          </div>
          <div className="metric-caption">
            <span className={`tiny-dot ${status?.connected ? "green" : ""}`} />
            {status?.connected
              ? "来自当前记忆服务"
              : enabled
                ? "等待服务状态"
                : "连接后查看"}
          </div>
        </div>
        <button
          className="metric-card metric-clickable"
          onClick={() => navigate("reviews")}
        >
          <div className="metric-top">
            <span>等待你的审阅</span>
            <Inbox size={17} />
          </div>
          <div className="metric-value">
            {enabled && !statusError ? (reviewCount ?? "—") : "—"}
            <small>批</small>
            <ArrowRight size={20} />
          </div>
          <div className="metric-caption">先核对，再让新记忆生效</div>
        </button>
        <button
          className="metric-card metric-clickable"
          onClick={() => navigate("sync")}
        >
          <div className="metric-top">
            <span>最近同步</span>
            <Clock3 size={17} />
          </div>
          <div className="metric-value metric-text">
            {status ? relativeTime(status.lastRun) : "—"}
            <ArrowRight size={20} />
          </div>
          <div className="metric-caption">
            {status?.paused === true ? (
              <>
                <span className="tiny-dot amber" />
                自动提取已暂停
              </>
            ) : status?.paused === false ? (
              <>
                <span className="tiny-dot green" />
                自动提取已启用
              </>
            ) : (
              "同步状态尚未确认"
            )}
          </div>
        </button>
      </div>
      {statusError &&
        enabled &&
        !(statusError instanceof ApiError && statusError.status === 401) && (
          <ErrorBox error={statusError} retry={refresh} compact />
        )}
      {status &&
        (status.search.modelReady === false ||
          status.search.ready === false ||
          (status.search.pending ?? 0) > 0) && (
          <div className="info-banner">
            <Database size={17} />
            <span>
              {status.search.modelReady === false
                ? "语义模型尚未就绪。搜索能力以服务当前返回的模式为准。"
                : status.search.ready === false
                  ? "搜索索引尚未就绪，查询可能暂时无法完成。"
                  : `有 ${status.search.pending} 条记忆等待建立索引，搜索结果可能暂不完整。`}
            </span>
            <button onClick={() => navigate("settings")}>
              查看诊断
              <ArrowRight size={13} />
            </button>
          </div>
        )}
      <section className="library-panel">
        <div className="library-toolbar">
          <div className="type-tabs" role="tablist" aria-label="记忆类型">
            {MAIN_TYPES.map((tab) => (
              <button
                role="tab"
                aria-selected={type === tab.value}
                className={type === tab.value ? "active" : ""}
                key={tab.value}
                onClick={() => {
                  setType(tab.value);
                  setOffset(0);
                }}
              >
                {tab.value === "person" ? (
                  <UsersRound size={14} />
                ) : tab.value === "event" ? (
                  <CalendarDays size={14} />
                ) : tab.value === "decision" ? (
                  <Fingerprint size={14} />
                ) : null}
                {tab.label}
              </button>
            ))}
          </div>
          <div className="lifecycle-select">
            <SlidersHorizontal size={14} />
            <select
              aria-label="记忆状态"
              value={lifecycle}
              onChange={(event) => {
                setLifecycle(event.target.value);
                setOffset(0);
              }}
            >
              <option value="active">使用中</option>
              <option value="archived">已停用</option>
              <option value="all">全部状态</option>
            </select>
            <ChevronDown size={13} />
          </div>
        </div>
        <form
          className="search-form"
          onSubmit={(event) => {
            event.preventDefault();
            setQuery(input.trim());
            setOffset(0);
          }}
        >
          <Search size={18} />
          <input
            ref={searchInput}
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder={
              lifecycle === "active"
                ? "搜索姓名、事件，或问一个关于记忆的问题…"
                : "搜索已记录的关键词…"
            }
            aria-label="搜索记忆"
            disabled={!enabled}
          />
          {input && (
            <button
              type="button"
              className="icon-button"
              onClick={resetSearch}
              aria-label="清除搜索"
            >
              <X size={15} />
            </button>
          )}
          <kbd>⌘ K</kbd>
          <button
            className="search-submit"
            type="submit"
            disabled={!enabled || loading}
          >
            搜索
            <ArrowRight size={14} />
          </button>
        </form>
        <div className="search-options">
          <label>
            <input
              type="checkbox"
              checked={interpret}
              onChange={(event) => setInterpret(event.target.checked)}
              disabled={!enabled || lifecycle !== "active"}
            />
            <Sparkles size={13} />
            <span>
              智能理解 <small>使用 Hermes 模型</small>
            </span>
          </label>
          <span>
            {interpret && lifecycle === "active"
              ? "搜索问题会发送到你配置的模型"
              : "默认检索不调用远程模型"}
          </span>
        </div>
        <div className="list-caption">
          <span>
            {query ? (
              <>
                “{query}” 的搜索结果{data && <em>{data.total} 条</em>}
              </>
            ) : (
              <>
                {lifecycle === "archived" ? "已停用的记忆" : "你的记忆"}
                {data && <em>{data.total} 条</em>}
              </>
            )}
          </span>
          <span>
            {data?.mode ? (
              <Badge>
                {data.mode === "hybrid"
                  ? "混合检索"
                  : data.mode === "keyword"
                    ? "关键词检索"
                    : data.mode === "semantic"
                      ? "语义检索"
                      : data.mode}
              </Badge>
            ) : (
              <>
                <History size={12} />
                最近更新
              </>
            )}
          </span>
        </div>
        {loading ? (
          <Spinner label={query ? "正在寻找相关记忆" : "正在读取记忆"} />
        ) : error ? (
          <div className="panel-error">
            <ErrorBox error={error} retry={refresh} />
          </div>
        ) : !data?.items.length ? (
          <EmptyState
            title={
              !enabled
                ? "你的记忆，从一次连接开始"
                : query
                  ? "暂时没有找到相关记忆"
                  : lifecycle === "archived"
                    ? "没有已停用的记忆"
                    : "留给值得记住的事"
            }
          >
            {!enabled
              ? "连接 Hermes 记忆服务后，已保存的人物、事件与决策会出现在这里。"
              : query
                ? "试试更具体的姓名或关键词。没有结果并不代表这件事从未被记录。"
                : lifecycle === "archived"
                  ? "被停用的条目会保留在这里，方便核对与恢复。"
                  : "自动提取与审阅完成后，新的记忆会出现在这里。每一条都可以回到它的来源。"}
            {enabled && !query && lifecycle === "active" && (
              <span className="empty-next">
                先检查同步状态，或查看等待审阅的批次。
              </span>
            )}
          </EmptyState>
        ) : (
          <div className="memory-list">
            {data.items.map((memory, index) => (
              <button
                className="memory-row"
                key={memory.uri || index}
                onClick={() => onSelect(memory)}
              >
                <TypeIcon type={memory.type} />
                <div className="memory-row-body">
                  <div className="memory-row-title">
                    <h3>{memory.name}</h3>
                    {memory.status === "archived" && <Badge>已停用</Badge>}
                  </div>
                  <p>
                    {memory.description ||
                      `${typeLabels[memory.type]}记忆 · 打开查看内容与来源`}
                  </p>
                  <div className="memory-row-meta">
                    <span>{typeLabels[memory.type]}</span>
                    {memory.eventTime && (
                      <>
                        <i />
                        <span>
                          <CalendarDays size={11} />
                          {formatDate(memory.eventTime)}
                        </span>
                      </>
                    )}
                    {memory.evidence.length > 0 && (
                      <>
                        <i />
                        <span>
                          <MessageSquareQuote size={11} />
                          {memory.evidence.length} 条来源
                        </span>
                      </>
                    )}
                    {memory.participants.length > 0 && (
                      <>
                        <i />
                        <span>{memory.participants.join("、")}</span>
                      </>
                    )}
                  </div>
                </div>
                <div className="memory-row-end">
                  <span>
                    {memory.updatedAt ? formatDate(memory.updatedAt) : "—"}
                  </span>
                  <ChevronRight size={16} />
                </div>
              </button>
            ))}
          </div>
        )}
        {data && data.total > data.limit && !isSearch && (
          <div className="pagination">
            <span>
              第 {data.offset + 1}–
              {Math.min(data.offset + data.items.length, data.total)} 条，共{" "}
              {data.total} 条
            </span>
            <div>
              <button
                className="button button-quiet"
                disabled={offset === 0 || loading}
                onClick={() => setOffset(Math.max(0, offset - 30))}
              >
                <ArrowLeft size={14} />
                上一页
              </button>
              <button
                className="button button-quiet"
                disabled={offset + data.limit >= data.total || loading}
                onClick={() => setOffset(offset + 30)}
              >
                下一页
                <ArrowRight size={14} />
              </button>
            </div>
          </div>
        )}
      </section>
      <div className="subtle-note">
        <MessageSquareQuote size={15} />
        <span>记忆保留理解的上下文。原话证明来源，事实仍值得核对。</span>
      </div>
    </>
  );
}

function reviewReason(reason?: string): string {
  if (!reason) return "查看提取出的事实、关系和原话，再决定是否写入。";
  const labels: Record<string, string> = {
    identity_change: "人物身份需要核对",
    potential_duplicate: "可能与已有记忆重复",
    duplicate_candidate: "可能与已有记忆重复",
    user_confirmed: "将修改用户确认过的记忆",
    user_confirmed_conflict: "与用户确认过的内容存在冲突",
    protected_memory: "将修改人工维护的内容",
    archived_target: "涉及已停用记忆",
    quarantined_target: "涉及已停用记忆",
    semantic_conflict: "内容存在语义冲突",
  };
  return reason
    .split(",")
    .map((code) => labels[code.trim()] ?? code.trim())
    .join("；");
}

function ReviewInbox({
  enabled,
  reviews,
  loading,
  error,
  refresh,
  onSelect,
}: {
  enabled: boolean;
  reviews: Review[];
  loading: boolean;
  error: unknown;
  refresh: () => void;
  onSelect: (review: Review) => void;
}) {
  return (
    <>
      <PageHeader
        eyebrow="A MOMENT TO REVIEW"
        title="审阅收件箱"
        subtitle="看看新提取的内容，决定哪些值得成为长期记忆。"
      >
        <RefreshButton refresh={refresh} loading={loading} />
      </PageHeader>
      <div className="review-intro">
        <span className="intro-icon">
          <FileCheck2 size={24} strokeWidth={1.5} />
        </span>
        <div>
          <strong>让记忆的边界更清楚</strong>
          <p>
            模型可能混淆计划、转述和已发生的事。批准前，请核对事实与原话是否表达了同一件事。
          </p>
        </div>
        <Badge tone="amber">人工审阅</Badge>
      </div>
      <section className="content-panel">
        <div className="section-heading">
          <h2>
            提取批次 <span>{enabled ? reviews.length : "—"}</span>
          </h2>
          <span>内容在批准后写入记忆</span>
        </div>
        {loading ? (
          <Spinner label="正在读取待审内容" />
        ) : error ? (
          <div className="panel-error">
            <ErrorBox error={error} retry={refresh} />
          </div>
        ) : !reviews.length ? (
          <EmptyState
            icon={<Inbox size={29} strokeWidth={1.3} />}
            title={enabled ? "收件箱很清静" : "连接后查看待审内容"}
          >
            {enabled
              ? "目前没有等待审阅的批次。新的提取需要复核时，会出现在这里。"
              : "连接你的服务后，可以在此核对每批新提取的事实与证据。"}
          </EmptyState>
        ) : (
          <div className="review-list">
            {reviews.map((review) => (
              <button
                className="review-card"
                key={review.id}
                onClick={() => onSelect(review)}
              >
                <div className="review-card-icon">
                  <FileText size={22} strokeWidth={1.5} />
                </div>
                <div className="review-card-content">
                  <div className="review-card-top">
                    <h3>
                      {review.entities.length
                        ? `${review.entities.length} 条记忆等待核对`
                        : "一批提取内容等待核对"}
                    </h3>
                    <Badge tone={jobTone(review.status)}>
                      {jobLabels[review.status] ?? review.status}
                    </Badge>
                  </div>
                  <p>{reviewReason(review.reason)}</p>
                  <div className="review-preview">
                    {review.entities.slice(0, 3).map((memory, index) => (
                      <span key={index}>
                        {typeLabels[memory.type]}
                        <i />
                        {memory.name}
                      </span>
                    ))}
                    {review.entities.length > 3 && (
                      <span>+{review.entities.length - 3}</span>
                    )}
                  </div>
                  <div className="review-card-meta">
                    <span>{formatDate(review.createdAt, true)}</span>
                    <span>{review.relations.length} 条关系</span>
                    <span className="mono">{review.id.slice(0, 14)}</span>
                  </div>
                </div>
                <span className="review-open">
                  查看并审阅
                  <ArrowRight size={15} />
                </span>
              </button>
            ))}
          </div>
        )}
      </section>
      <div className="subtle-note">
        <ShieldCheck size={15} />
        <span>拒绝的批次不会写入图谱。批次状态以服务返回的提交回执为准。</span>
      </div>
    </>
  );
}

function SyncPage({
  api,
  enabled,
  status,
  statusError,
  statusLoading,
  refreshKey,
  refresh,
  notify,
}: {
  api: ConsoleApi;
  enabled: boolean;
  status: ConsoleStatus | null;
  statusError: unknown;
  statusLoading: boolean;
  refreshKey: number;
  refresh: () => void;
  notify: (message: string, error?: boolean) => void;
}) {
  const [jobs, setJobs] = useState<SyncJob[]>([]),
    [jobsError, setJobsError] = useState<unknown>(null),
    [jobsLoading, setJobsLoading] = useState(false);
  const [busy, setBusy] = useState("");
  useEffect(() => {
    if (!enabled) {
      setJobs([]);
      setJobsError(null);
      return;
    }
    let current = true;
    setJobsLoading(true);
    api
      .jobs()
      .then((value) => {
        if (current) {
          setJobs(value);
          setJobsError(null);
        }
      })
      .catch((error) => {
        if (current) setJobsError(error);
      })
      .finally(() => {
        if (current) setJobsLoading(false);
      });
    return () => {
      current = false;
    };
  }, [api, enabled, refreshKey]);
  const action = async (kind: "run" | "pause" | "resume") => {
    setBusy(kind);
    try {
      if (kind === "run") {
        await api.sync();
        notify("同步请求已被服务接收。请查看任务状态确认处理结果。");
      } else {
        await api[kind]();
        notify(
          kind === "pause"
            ? "自动提取已暂停。已有记忆仍可查询。"
            : "自动提取已恢复。",
        );
      }
      refresh();
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy("");
    }
  };
  return (
    <>
      <PageHeader
        eyebrow="QUIETLY, IN THE BACKGROUND"
        title="同步与任务"
        subtitle="了解记忆如何更新，也随时可以让它慢下来。"
      >
        <RefreshButton refresh={refresh} loading={statusLoading} />
        <button
          className="button button-primary"
          disabled={!enabled || !!busy}
          onClick={() => void action("run")}
        >
          {busy === "run" ? (
            <LoaderCircle size={15} className="spin" />
          ) : (
            <RefreshCw size={15} />
          )}
          立即同步
        </button>
      </PageHeader>
      {!!statusError && <ErrorBox error={statusError} retry={refresh} />}
      <div className="sync-overview">
        <div className="sync-main">
          <div
            className={`sync-orb ${status?.paused === true ? "paused" : ""}`}
          >
            {status?.paused === true ? (
              <Pause size={25} strokeWidth={1.5} />
            ) : (
              <RefreshCw size={25} strokeWidth={1.5} />
            )}
          </div>
          <div>
            <div className="section-eyebrow">自动记忆提取</div>
            <h2>
              {status?.paused === true
                ? "已暂停，按你的节奏来"
                : status?.paused === false
                  ? "在后台，整理值得留下的事"
                  : "等待同步状态"}
            </h2>
            <p>
              {status?.paused === true
                ? "暂停期间不会自动启动新的提取，已有记忆仍然可以检索。"
                : "会话先被提取和校验；需要核对的内容会进入审阅收件箱。"}
            </p>
          </div>
          <button
            className="button button-secondary"
            disabled={!enabled || status?.paused === null || !status || !!busy}
            onClick={() => void action(status?.paused ? "resume" : "pause")}
          >
            {busy && busy !== "run" ? (
              <LoaderCircle size={15} className="spin" />
            ) : status?.paused ? (
              <Play size={15} />
            ) : (
              <Pause size={15} />
            )}
            {status?.paused ? "恢复自动提取" : "暂停自动提取"}
          </button>
        </div>
        <div className="sync-stats">
          <div>
            <span>待处理会话 / 快照</span>
            <strong>
              {status?.pendingCount ?? "—"}
              <small>项</small>
            </strong>
          </div>
          <div>
            <span>最近一次运行</span>
            <strong className="sync-time">
              {status?.lastRun ? formatDate(status.lastRun, true) : "暂无记录"}
            </strong>
          </div>
          <div>
            <span>搜索索引</span>
            <strong>
              {status?.search.indexed ?? "—"}
              <small>已索引</small>
            </strong>
          </div>
        </div>
      </div>
      <section className="content-panel">
        <div className="section-heading">
          <h2>任务记录</h2>
          <span>提交请求后，以任务最终状态为准</span>
        </div>
        {jobsLoading ? (
          <Spinner label="正在读取任务记录" />
        ) : jobsError ? (
          <div className="panel-error">
            <ErrorBox error={jobsError} retry={refresh} />
          </div>
        ) : !jobs.length ? (
          <EmptyState
            icon={<History size={29} strokeWidth={1.3} />}
            title="暂时没有任务记录"
          >
            新的同步任务开始后，可以在这里查看进度、结果与需要处理的问题。
          </EmptyState>
        ) : (
          <div className="job-table">
            <div className="job-table-head">
              <span>任务</span>
              <span>开始时间</span>
              <span>状态</span>
            </div>
            {jobs.map((job, index) => (
              <div className="job-row" key={job.id || index}>
                <div>
                  <strong>
                    <span className="job-icon">
                      <RefreshCw size={14} />
                    </span>
                    {job.summary || "记忆同步任务"}
                  </strong>
                  <span className="mono">{job.id}</span>
                  {job.error && (
                    <p className="job-error">
                      <AlertCircle size={13} />
                      {job.error}
                    </p>
                  )}
                </div>
                <span>{formatDate(job.createdAt, true)}</span>
                <Badge tone={jobTone(job.status)} dot>
                  {jobLabels[job.status] ?? job.status}
                </Badge>
              </div>
            ))}
          </div>
        )}
      </section>
      <HistoryBackfill
        api={api}
        enabled={enabled}
        refreshKey={refreshKey}
        refresh={refresh}
        notify={notify}
      />
      <div className="subtle-note">
        <CircleHelp size={15} />
        <span>
          同步会使用 Hermes
          配置的模型路由。连接成功与历史提交记录不代表本次写入已经完成。
        </span>
      </div>
    </>
  );
}

function SettingsPage({
  api,
  enabled,
  status,
  statusError,
  refreshKey,
  refresh,
  notify,
  connect,
  disconnect,
}: {
  api: ConsoleApi;
  enabled: boolean;
  status: ConsoleStatus | null;
  statusError: unknown;
  refreshKey: number;
  refresh: () => void;
  notify: (message: string, error?: boolean) => void;
  connect: () => void;
  disconnect: () => void;
}) {
  const [settings, setSettings] = useState<JsonRecord | null>(null),
    [error, setError] = useState<unknown>(null),
    [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!enabled) {
      setSettings(null);
      setError(null);
      return;
    }
    let current = true;
    setLoading(true);
    api
      .settings()
      .then((value) => {
        if (current) {
          setSettings(value);
          setError(null);
        }
      })
      .catch((failure) => {
        if (current) setError(failure);
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [api, enabled, refreshKey]);
  const [indexBusy, setIndexBusy] = useState("");
  const indexAction = async (action: "prepare" | "reindex") => {
    setIndexBusy(action);
    try {
      const receipt = await (action === "prepare"
        ? api.prepareSearch()
        : api.reindex());
      if (!receipt.job_id && receipt.ok !== true)
        throw new Error("服务未返回任务回执。请刷新核对任务状态。");
      notify(
        action === "prepare"
          ? "模型准备任务已提交。首次运行会下载本地模型，请在同步与任务中查看结果。"
          : "索引更新任务已提交。完成前搜索结果可能暂不完整。",
      );
      refresh();
    } catch (failure) {
      notify(errorMessage(failure), true);
    } finally {
      setIndexBusy("");
    }
  };
  const compatibility = status?.compatibility ?? {};
  const incompatible =
    compatibility.compatible === false || compatibility.ok === false;
  return (
    <>
      <PageHeader
        eyebrow="CLEAR ABOUT THE DETAILS"
        title="设置与诊断"
        subtitle="看清服务连接、搜索能力，以及这份记忆的运行边界。"
      >
        <RefreshButton refresh={refresh} loading={loading} />
      </PageHeader>
      <div className="settings-grid">
        <section className="content-panel settings-card">
          <div className="card-title">
            <KeyRound size={19} />
            <h2>访问连接</h2>
            <Badge tone={enabled ? "green" : "neutral"}>
              {enabled ? "已设置令牌" : "尚未连接"}
            </Badge>
          </div>
          <p className="card-description">
            访问令牌仅保存在当前浏览器会话中，用于连接你部署的记忆服务。
          </p>
          <dl className="settings-dl">
            <div>
              <dt>服务地址</dt>
              <dd className="mono">{window.location.origin}</dd>
            </div>
            <div>
              <dt>API</dt>
              <dd className="mono">/api/v1</dd>
            </div>
            <div>
              <dt>访问令牌</dt>
              <dd>
                {enabled ? "•••• •••• ••••" : "未设置"}
                <LockKeyhole size={12} />
              </dd>
            </div>
          </dl>
          <div className="settings-actions">
            <button className="button button-secondary" onClick={connect}>
              <KeyRound size={14} />
              {enabled ? "更新令牌" : "连接服务"}
            </button>
            {enabled && (
              <button className="button button-quiet" onClick={disconnect}>
                <LogOut size={14} />
                断开当前会话
              </button>
            )}
          </div>
        </section>
        <section className="content-panel settings-card">
          <div className="card-title">
            <Database size={19} />
            <h2>搜索与索引</h2>
          </div>
          <p className="card-description">
            索引未完成或模型不可用时，搜索可能不完整。这里始终显示服务实际报告的状态。
          </p>
          <dl className="settings-dl">
            <div>
              <dt>当前检索模式</dt>
              <dd>{status?.search.mode ?? "尚未确认"}</dd>
            </div>
            <div>
              <dt>语义模型</dt>
              <dd>
                <Badge
                  tone={
                    status?.search.modelReady === true
                      ? "green"
                      : status?.search.modelReady === false
                        ? "amber"
                        : "neutral"
                  }
                  dot
                >
                  {status?.search.modelReady === true
                    ? "已就绪"
                    : status?.search.modelReady === false
                      ? "未就绪"
                      : "尚未确认"}
                </Badge>
              </dd>
            </div>
            <div>
              <dt>已索引 / 待索引</dt>
              <dd>
                {status?.search.indexed ?? "—"}
                <span className="text-muted">/</span>
                {status?.search.pending ?? "—"}
              </dd>
            </div>
          </dl>
          <div className="settings-actions">
            <button
              className="button button-secondary"
              onClick={() => void indexAction("prepare")}
              disabled={!enabled || !!indexBusy}
            >
              {indexBusy === "prepare" ? (
                <LoaderCircle size={14} className="spin" />
              ) : (
                <Database size={14} />
              )}
              准备语义模型
            </button>
            <button
              className="button button-quiet"
              onClick={() => void indexAction("reindex")}
              disabled={!enabled || !!indexBusy}
            >
              {indexBusy === "reindex" ? (
                <LoaderCircle size={14} className="spin" />
              ) : (
                <RefreshCw size={14} />
              )}
              更新索引
            </button>
          </div>
          <p className="field-note index-note">
            模型在 Hermes 主机本地运行。首次准备会联网下载模型文件。
          </p>
          {status && Object.keys(status.search.indexes).length > 0 && (
            <div className="index-states">
              {Object.entries(status.search.indexes).map(([key, value]) => (
                <div key={key}>
                  <code>{key}</code>
                  <span>{textValue((value as JsonRecord).state)}</span>
                  <small>
                    {textValue((value as JsonRecord).population_percent)}%
                  </small>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
      {!!statusError && <ErrorBox error={statusError} retry={refresh} />}
      <section className="content-panel settings-card diagnostics">
        <div className="card-title">
          <Terminal size={19} />
          <h2>环境诊断</h2>
          <Badge tone={incompatible ? "amber" : status ? "green" : "neutral"}>
            {incompatible
              ? "需要检查兼容性"
              : status
                ? "已读取状态"
                : "等待连接"}
          </Badge>
        </div>
        {loading ? (
          <Spinner label="正在读取安全配置" />
        ) : error ? (
          <ErrorBox error={error} retry={refresh} />
        ) : settings ? (
          <>
            <div className="diagnostic-fields">
              {Object.entries(settings)
                .filter(([key]) => !["ok"].includes(key))
                .map(([key, value]) => (
                  <div key={key}>
                    <span>{SETTING_LABELS[key] ?? key}</span>
                    <strong>{textValue(value)}</strong>
                  </div>
                ))}
            </div>
            {Object.keys(compatibility).length > 0 && (
              <details className="code-details">
                <summary>
                  Hermes 兼容性详情
                  <ChevronDown size={14} />
                </summary>
                <pre>{JSON.stringify(compatibility, null, 2)}</pre>
              </details>
            )}
            <details className="code-details">
              <summary>
                查看服务诊断响应
                <ChevronDown size={14} />
              </summary>
              <pre>{JSON.stringify(status?.raw ?? {}, null, 2)}</pre>
            </details>
          </>
        ) : (
          <div className="settings-unconnected">
            <Unplug size={22} />
            <p>连接后显示服务返回的安全配置与兼容性信息。</p>
          </div>
        )}
      </section>
      <div className="privacy-note">
        <ShieldCheck size={23} strokeWidth={1.5} />
        <div>
          <h3>关于这份工作台</h3>
          <p>
            这是面向单个 Hermes profile 的个人
            Alpha。工作台访问你部署的记忆服务；自动提取使用 Hermes
            配置的模型。停用记忆会停止后续检索，原会话与证据仍保留在宿主机。
          </p>
        </div>
      </div>
    </>
  );
}
const SETTING_LABELS: Record<string, string> = {
  home: "Hermes profile",
  profile: "当前 profile",
  hermes_home: "Hermes profile",
  database: "数据库",
  timezone: "时区",
  urn_prefix: "记忆命名空间",
  user_person_urn: "当前用户标识",
  model: "提取模型",
  version: "服务版本",
  enabled: "已启用",
  sync_paused: "自动提取已暂停",
  neo4j_url: "图谱地址",
  search_mode: "搜索模式",
  host: "服务主机",
};

const RELATION_LABELS: Record<string, string> = {
  HERMES_participant: "参与经历",
  HERMES_hasPreference: "记录偏好",
  HERMES_hasDecision: "记录决策",
  HERMES_locatedAt: "关联地点",
  HERMES_knows: "认识的人",
  LINKED_TO: "关联记忆",
  CAUSED_BY: "相关原因",
  RELATED_TO: "相关记忆",
  owl_sameAs: "同一身份",
};
function EvidenceList({ memory }: { memory: Memory }) {
  return (
    <>
      {memory.evidence.length ? (
        memory.evidence.map((evidence, index) => (
          <div
            className={`evidence-card ${evidence.kind === "user_edit" ? "user-edit-evidence" : ""}`}
            key={index}
          >
            <div className="evidence-heading">
              <MessageSquareQuote size={14} />
              <span>
                {evidence.kind === "user_edit"
                  ? "用户人工更正"
                  : evidence.kind === "source_background"
                    ? "来源背景 · 非更正依据"
                    : evidence.origin === "user" || !evidence.origin
                      ? "用户原话"
                      : evidence.origin}
              </span>
              {evidence.timestamp && (
                <time>{formatDate(evidence.timestamp, true)}</time>
              )}
            </div>
            {evidence.quote ? (
              <blockquote>{evidence.quote}</blockquote>
            ) : (
              <p className="user-edit-note">
                用户在工作台确认了更正内容。
                {evidence.reason && <span>更正原因：{evidence.reason}</span>}
              </p>
            )}
            <div className="evidence-footer">
              {evidence.sessionId && (
                <span title={evidence.sessionId}>
                  会话 {evidence.sessionId.slice(0, 12)}
                </span>
              )}
              {evidence.messageId !== undefined && (
                <span>消息 #{evidence.messageId}</span>
              )}
            </div>
          </div>
        ))
      ) : (
        <div className="no-evidence">
          <MessageSquareQuote size={18} />
          <p>这条记忆暂未提供可展示的原话证据。核对来源后，再据此判断。</p>
        </div>
      )}
      <p className="evidence-disclaimer">
        原话匹配只能证明来源，不代表模型对语义的理解已经正确。
      </p>
    </>
  );
}
function MemoryDrawer({
  api,
  selected,
  onClose,
  onNavigate,
  onChanged,
  notify,
}: {
  api: ConsoleApi;
  selected: Memory;
  onClose: () => void;
  onNavigate: (memory: Memory) => void;
  onChanged: () => void;
  notify: (message: string, error?: boolean) => void;
}) {
  const [memory, setMemory] = useState(selected),
    [loading, setLoading] = useState(true),
    [error, setError] = useState<unknown>(null);
  const [edit, setEdit] = useState<"correct" | "archive" | null>(null),
    [restoring, setRestoring] = useState(false),
    [revisionKey, setRevisionKey] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true);
    api
      .detail(selected.uri)
      .then((value) => {
        if (active) {
          setMemory(value);
          setError(null);
        }
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
  }, [api, selected.uri, revisionKey]);
  const canEdit =
    !loading && !error && !memory.readOnly && memory.version !== undefined;
  const changed = () => {
    setEdit(null);
    setRevisionKey((value) => value + 1);
    onChanged();
  };
  const restore = async () => {
    setRestoring(true);
    try {
      await api.restore(memory, "用户从个人记忆工作台恢复此条记忆");
      notify("记忆已恢复，可以继续检索。");
      changed();
    } catch (failure) {
      setError(failure);
    } finally {
      setRestoring(false);
    }
  };
  return (
    <>
      <Overlay
        onClose={onClose}
        className="drawer-overlay"
        labelledBy="memory-title"
      >
        <div className="drawer-top">
          <span>
            <TypeIcon type={memory.type} size={15} />
            {typeLabels[memory.type]}记忆
          </span>
          <div>
            {memory.readOnly && <Badge>共享 · 仅查看</Badge>}
            {memory.status === "archived" && <Badge>已停用</Badge>}
            <button
              className="icon-button"
              onClick={onClose}
              aria-label="关闭记忆详情"
            >
              <X size={19} />
            </button>
          </div>
        </div>
        <div className="drawer-scroll">
          <div className="detail-title">
            <div className="section-eyebrow">MEMORY DETAILS</div>
            <h2 id="memory-title">{memory.name}</h2>
            <div className="detail-meta">
              <span>
                <Clock3 size={12} />
                {memory.updatedAt
                  ? `更新于 ${formatDate(memory.updatedAt, true)}`
                  : "更新时间未提供"}
              </span>
              {memory.version !== undefined && (
                <span>版本 {memory.version}</span>
              )}
            </div>
          </div>
          {loading && (
            <div className="inline-loading">
              <LoaderCircle size={14} className="spin" />
              正在读取完整内容
            </div>
          )}
          {!!error && (
            <ErrorBox
              error={error}
              retry={() => setRevisionKey((value) => value + 1)}
              compact
            />
          )}
          {memory.readOnly && (
            <div className="info-banner shared-memory-banner">
              <LockKeyhole size={16} />
              <span>来自共享记忆，仅可查看</span>
            </div>
          )}
          {memory.status === "archived" && (
            <div className="archived-banner">
              <Pause size={16} />
              <span>
                这条记忆已停用，不会出现在正常检索中。来源记录仍保留。
              </span>
            </div>
          )}
          <section className="detail-section">
            <h3>
              <FileText size={15} />
              记忆内容
            </h3>
            <p className="memory-description">
              {memory.description || "该条目没有附加描述。"}
            </p>
            <dl className="detail-facts">
              {memory.eventTime && (
                <div>
                  <dt>事件时间</dt>
                  <dd>
                    {formatDate(
                      memory.eventTime,
                      memory.timePrecision !== "day",
                    )}
                    {memory.timePrecision === "day" && <Badge>日期精度</Badge>}
                  </dd>
                </div>
              )}
              {memory.endTime && (
                <div>
                  <dt>结束边界</dt>
                  <dd>{formatDate(memory.endTime, true)}</dd>
                </div>
              )}
              {memory.timePrecision === "unknown" && (
                <div>
                  <dt>发生时间</dt>
                  <dd>已发生，日期未知</dd>
                </div>
              )}
              {memory.participants.length > 0 && (
                <div>
                  <dt>参与人物</dt>
                  <dd>{memory.participants.join("、")}</dd>
                </div>
              )}
              {stringValue(
                memory.properties.hermes_decisionContext,
                memory.properties.decision_context,
              ) && (
                <div>
                  <dt>决策背景</dt>
                  <dd>
                    {textValue(
                      memory.properties.hermes_decisionContext ??
                        memory.properties.decision_context,
                    )}
                  </dd>
                </div>
              )}
              {memory.type === "preference" && (
                <>
                  <div>
                    <dt>偏好项目</dt>
                    <dd>
                      {textValue(
                        memory.properties.hermes_preferenceKey ??
                          memory.properties.preference_key,
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt>偏好内容</dt>
                    <dd>
                      {textValue(
                        memory.properties.hermes_preferenceValue ??
                          memory.properties.preference_value,
                      )}
                    </dd>
                  </div>
                </>
              )}
              {stringValue(
                memory.properties.hermes_note,
                memory.properties.note,
              ) && (
                <div>
                  <dt>备注</dt>
                  <dd>
                    {textValue(
                      memory.properties.hermes_note ?? memory.properties.note,
                    )}
                  </dd>
                </div>
              )}
              {stringValue(memory.properties.hermes_semanticScope) && (
                <div>
                  <dt>适用范围</dt>
                  <dd>{textValue(memory.properties.hermes_semanticScope)}</dd>
                </div>
              )}
              {memory.semanticStatus && (
                <div>
                  <dt>语义状态</dt>
                  <dd>{memory.semanticStatus}</dd>
                </div>
              )}
              {stringValue(memory.properties.hermes_statusNote) && (
                <div>
                  <dt>状态说明</dt>
                  <dd>{textValue(memory.properties.hermes_statusNote)}</dd>
                </div>
              )}
            </dl>
          </section>
          <section className="detail-section">
            <h3>
              <MessageSquareQuote size={15} />
              来源与证据<span>{memory.evidence.length}</span>
            </h3>
            <EvidenceList memory={memory} />
          </section>
          {memory.relations.length > 0 && (
            <section className="detail-section">
              <h3>
                <Layers3 size={15} />
                关联关系<span>{memory.relations.length}</span>
              </h3>
              <div className="relation-list">
                {memory.relations.map((relation, index) => {
                  const relatedUri =
                    stringValue(relation.related_uri) ??
                    (relation.from_urn === memory.uri
                      ? stringValue(relation.to_urn)
                      : stringValue(relation.from_urn));
                  const related = normalizeMemory({
                    uri: relatedUri,
                    type: relation.related_type,
                    read_only: relation.related_read_only,
                    name:
                      relation.related_name ??
                      relation.target_name ??
                      relation.to_name ??
                      "关联记忆",
                  });
                  const label =
                    stringValue(relation.relation_label) ??
                    RELATION_LABELS[
                      String(relation.type ?? relation.relation_type)
                    ] ??
                    "相关记忆";
                  const time = stringValue(relation.related_time);
                  return (
                    <button
                      className="related-memory"
                      key={index}
                      disabled={!relatedUri}
                      onClick={() => onNavigate(related)}
                    >
                      <TypeIcon type={related.type} size={16} />
                      <div>
                        <span>{label}</span>
                        <strong>{related.name}</strong>
                        {time && <time>{formatDate(time)}</time>}
                      </div>
                      <ChevronRight size={15} />
                    </button>
                  );
                })}
              </div>
            </section>
          )}
          <section className="detail-section">
            <h3>
              <History size={15} />
              修订记录<span>{memory.revisions.length}</span>
            </h3>
            {memory.revisions.length ? (
              <RevisionHistory revisions={memory.revisions} />
            ) : (
              <p className="section-empty">
                {memory.readOnly
                  ? "共享记忆只展示当前内容，不提供历史修订。"
                  : "暂时没有可展示的修订记录。"}
              </p>
            )}
          </section>
          <div className="detail-uri">
            <span>记忆标识</span>
            <code>{memory.uri}</code>
          </div>
          {!loading && !memory.readOnly && memory.version === undefined && (
            <p className="field-note">
              服务未提供记录版本。为避免覆盖未知修改，这条记忆暂时无法编辑。
            </p>
          )}
        </div>
        <div className="drawer-actions">
          {memory.status === "archived" ? (
            <>
              <span>原话与修订记录继续保留</span>
              <button
                className="button button-primary"
                disabled={!canEdit || restoring}
                onClick={() => void restore()}
              >
                {restoring ? (
                  <LoaderCircle size={15} className="spin" />
                ) : (
                  <Play size={15} />
                )}
                恢复使用
              </button>
            </>
          ) : (
            <>
              <button
                className="button button-quiet danger-text"
                disabled={!canEdit}
                onClick={() => setEdit("archive")}
              >
                <Pause size={15} />
                停用记忆
              </button>
              <button
                className="button button-primary"
                disabled={!canEdit}
                onClick={() => setEdit("correct")}
              >
                <PencilLine size={15} />
                纠正内容
              </button>
            </>
          )}
        </div>
      </Overlay>
      {edit && (
        <EditMemoryDialog
          api={api}
          memory={memory}
          mode={edit}
          onClose={() => setEdit(null)}
          onSuccess={changed}
          notify={notify}
        />
      )}
    </>
  );
}

function EditMemoryDialog({
  api,
  memory,
  mode,
  onClose,
  onSuccess,
  notify,
}: {
  api: ConsoleApi;
  memory: Memory;
  mode: "correct" | "archive";
  onClose: () => void;
  onSuccess: () => void;
  notify: (message: string, error?: boolean) => void;
}) {
  const [name, setName] = useState(memory.name),
    [content, setContent] = useState(memory.description),
    [eventTime, setEventTime] = useState(memory.eventTime ?? ""),
    [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState<unknown>(null);
  const close = useCallback(() => {
    if (!busy) onClose();
  }, [busy, onClose]);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "archive") await api.archive(memory, reason.trim());
      else {
        const changes: JsonRecord = {},
          props: JsonRecord = {};
        if (name.trim() !== memory.name) changes.name = name.trim();
        if (content.trim() !== memory.description)
          props[memory.type === "decision" ? "decision_text" : "description"] =
            content.trim();
        if (
          memory.type === "event" &&
          eventTime.trim() !== (memory.eventTime ?? "")
        )
          props.event_time = eventTime.trim();
        if (Object.keys(props).length) changes.props = props;
        if (!Object.keys(changes).length)
          throw new Error("请先修改需要更正的内容。");
        await api.correct(memory, changes, reason.trim());
      }
      notify(
        mode === "archive"
          ? "记忆已停用，正常检索将不再使用这条内容。"
          : "更正已保存，修订记录已保留。",
      );
      onSuccess();
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Overlay
      onClose={close}
      className="modal-overlay nested-modal"
      labelledBy="edit-title"
    >
      <form onSubmit={(event) => void submit(event)}>
        <div className="modal-header">
          <span
            className={`modal-icon ${mode === "archive" ? "amber-icon" : ""}`}
          >
            {mode === "archive" ? (
              <Pause size={21} />
            ) : (
              <PencilLine size={21} />
            )}
          </span>
          <button
            className="icon-button"
            type="button"
            onClick={close}
            aria-label="关闭编辑"
            disabled={busy}
          >
            <X size={18} />
          </button>
        </div>
        <h2 id="edit-title">
          {mode === "archive" ? "停止使用这条记忆" : "让理解更准确一些"}
        </h2>
        <p className="modal-subtitle">
          {mode === "archive"
            ? "停用后，这条内容将不再用于正常检索。原会话与证据仍然保留，你可以稍后恢复。"
            : "更正会保留为一次人工修订。请只填写你确认的事实。"}
        </p>
        <div className="modal-body">
          {mode === "correct" && (
            <>
              <label className="field-label">
                记忆名称
                <input
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  required
                  maxLength={300}
                  autoFocus
                />
              </label>
              <label className="field-label">
                {memory.type === "decision" ? "决策内容" : "记忆描述"}
                <textarea
                  value={content}
                  onChange={(event) => setContent(event.target.value)}
                  rows={4}
                />
              </label>
              {memory.type === "event" && (
                <label className="field-label">
                  事件时间
                  <input
                    value={eventTime}
                    onChange={(event) => setEventTime(event.target.value)}
                    placeholder="带时区的 ISO 日期或时间"
                  />
                  <span className="field-note">
                    保留真实日期和时区；日期不确定时，不要补造。
                  </span>
                </label>
              )}
            </>
          )}
          <label className="field-label">
            {mode === "archive" ? "停用原因" : "更正原因"}
            <textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              rows={2}
              required
              placeholder={
                mode === "archive"
                  ? "例如：这是对话中的假设，并非实际发生"
                  : "说明原内容哪里有误，便于日后回看"
              }
              maxLength={2000}
              autoFocus={mode === "archive"}
            />
          </label>
          {!!error && <ErrorBox error={error} compact />}
        </div>
        <div className="modal-actions">
          <button
            className="button button-secondary"
            type="button"
            onClick={close}
            disabled={busy}
          >
            取消
          </button>
          <button
            className={`button ${mode === "archive" ? "button-danger" : "button-primary"}`}
            type="submit"
            disabled={
              busy || !reason.trim() || (mode === "correct" && !name.trim())
            }
          >
            {busy ? (
              <LoaderCircle size={15} className="spin" />
            ) : (
              <Check size={15} />
            )}
            {mode === "archive" ? "确认停用" : "保存更正"}
          </button>
        </div>
      </form>
    </Overlay>
  );
}

function ReviewDrawer({
  api,
  review,
  onClose,
  onChanged,
  notify,
}: {
  api: ConsoleApi;
  review: Review;
  onClose: () => void;
  onChanged: () => void;
  notify: (message: string, error?: boolean) => void;
}) {
  const [busy, setBusy] = useState(""),
    [error, setError] = useState<unknown>(null),
    [rejecting, setRejecting] = useState(false),
    [reason, setReason] = useState("");
  const [editingUri, setEditingUri] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<
    Record<string, { name: string; content: string }>
  >({});
  const editedCount = Object.keys(drafts).length;
  const invalidDraft = Object.values(drafts).some(
    (draft) => !draft.name.trim(),
  );
  const setDraft = (memory: Memory, key: "name" | "content", value: string) =>
    setDrafts((current) => ({
      ...current,
      [memory.uri]: {
        name: current[memory.uri]?.name ?? memory.name,
        content: current[memory.uri]?.content ?? memory.description,
        [key]: value,
      },
    }));
  const close = useCallback(() => {
    if (!busy) onClose();
  }, [busy, onClose]);
  const isPending = !["approved", "rejected", "committed"].includes(
    review.status,
  );
  const act = async (action: "approve" | "reject") => {
    setBusy(action);
    setError(null);
    try {
      const expectedVersions =
        review.expectedVersions ??
        Object.fromEntries(
          review.entities
            .filter((memory) => memory.version !== undefined && memory.uri)
            .map((memory) => [memory.uri, memory.version]),
        );
      const changes: JsonRecord = {};
      if (action === "approve")
        review.entities.forEach((memory) => {
          const draft = drafts[memory.uri];
          if (!draft) return;
          const change: JsonRecord = {};
          if (draft.name.trim() !== memory.name)
            change.name = draft.name.trim();
          if (draft.content.trim() !== memory.description)
            change.props = {
              [memory.type === "decision" ? "decision_text" : "description"]:
                draft.content.trim(),
            };
          if (Object.keys(change).length) changes[memory.uri] = change;
        });
      await api.review(review.id, action, {
        operation_id: crypto.randomUUID(),
        ...(Object.keys(expectedVersions).length
          ? { expected_versions: expectedVersions }
          : {}),
        ...(reason.trim() ? { reason: reason.trim() } : {}),
        ...(Object.keys(changes).length ? { changes } : {}),
      });
      notify(
        action === "approve"
          ? "批次已批准。内容已按服务回执提交。"
          : "批次已拒绝，不会写入这批内容。",
      );
      onChanged();
      onClose();
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy("");
    }
  };
  return (
    <Overlay
      onClose={close}
      className="drawer-overlay review-drawer-overlay"
      labelledBy="review-title"
    >
      <div className="drawer-top">
        <span>
          <Inbox size={17} />
          批次审阅
        </span>
        <button
          className="icon-button"
          onClick={close}
          disabled={!!busy}
          aria-label="关闭批次审阅"
        >
          <X size={19} />
        </button>
      </div>
      <div className="drawer-scroll">
        <div className="detail-title">
          <div className="section-eyebrow">REVIEW BEFORE REMEMBERING</div>
          <h2 id="review-title">这批内容，值得记住吗？</h2>
          <div className="detail-meta">
            <span>{formatDate(review.createdAt, true)}</span>
            <span>
              {review.entities.length} 条记忆 · {review.relations.length} 条关系
            </span>
          </div>
        </div>
        {review.reason && (
          <div className="info-banner">
            <CircleHelp size={17} />
            <span>{reviewReason(review.reason)}</span>
          </div>
        )}
        <div className="review-checklist">
          <ShieldCheck size={16} />
          <p>
            核对是否为用户确认的事实，注意否定、假设、转述与时间。批准将提交整个批次。
          </p>
        </div>
        {review.entities.map((memory, index) => (
          <section className="review-entity" key={memory.uri || index}>
            <div className="review-entity-heading">
              <TypeIcon type={memory.type} size={16} />
              <div>
                <Badge>{typeLabels[memory.type]}</Badge>
                <h3>{drafts[memory.uri]?.name ?? memory.name}</h3>
              </div>
              {isPending && memory.uri && (
                <button
                  className="icon-button review-edit-button"
                  onClick={() =>
                    setEditingUri(editingUri === memory.uri ? null : memory.uri)
                  }
                  disabled={!!busy}
                  aria-label={`修改${memory.name}`}
                  title="更正名称与描述"
                >
                  <PencilLine size={14} />
                </button>
              )}
              <span className="entity-index">
                {String(index + 1).padStart(2, "0")}
              </span>
            </div>
            {editingUri === memory.uri ? (
              <div className="review-edit-fields">
                <label className="field-label">
                  名称
                  <input
                    value={drafts[memory.uri]?.name ?? memory.name}
                    onChange={(event) =>
                      setDraft(memory, "name", event.target.value)
                    }
                    maxLength={300}
                  />
                </label>
                <label className="field-label">
                  {memory.type === "decision" ? "决策内容" : "描述"}
                  <textarea
                    rows={3}
                    value={drafts[memory.uri]?.content ?? memory.description}
                    onChange={(event) =>
                      setDraft(memory, "content", event.target.value)
                    }
                  />
                </label>
                <div>
                  <span className="field-note">
                    仅更正当前条目，保留身份与关系端点。
                  </span>
                  <button
                    className="button button-quiet"
                    onClick={() => {
                      setDrafts((current) => {
                        const next = { ...current };
                        delete next[memory.uri];
                        return next;
                      });
                      setEditingUri(null);
                    }}
                  >
                    撤销更正
                  </button>
                  <button
                    className="button button-secondary"
                    onClick={() => setEditingUri(null)}
                  >
                    完成编辑
                  </button>
                </div>
              </div>
            ) : (
              (drafts[memory.uri]?.content ?? memory.description) && (
                <p className="review-entity-description">
                  {drafts[memory.uri]?.content ?? memory.description}
                </p>
              )
            )}
            {drafts[memory.uri] && (
              <div className="review-edit-label">
                <PencilLine size={11} />
                有人工更正 · 原引文将保留为来源背景
              </div>
            )}
            {memory.eventTime && (
              <div className="review-date">
                <CalendarDays size={13} />
                {formatDate(memory.eventTime, memory.timePrecision !== "day")}
              </div>
            )}
            <EvidenceList memory={memory} />
          </section>
        ))}
        {!review.entities.length && (
          <div className="no-evidence">
            <FileText size={20} />
            <p>服务未提供可展示的实体。请确认该批次内容与关系后再批准。</p>
          </div>
        )}
        {review.relations.length > 0 && (
          <section className="detail-section">
            <h3>
              <Layers3 size={15} />
              关系核对
            </h3>
            <div className="review-relations">
              {review.relations.map((relation, index) => (
                <div key={index}>
                  <code>
                    {textValue(
                      relation.from ?? relation.from_urn ?? relation.source,
                    )}
                  </code>
                  <span>
                    <ArrowRight size={12} />
                    {textValue(relation.type ?? relation.relation_type)}
                  </span>
                  <code>
                    {textValue(
                      relation.to ?? relation.to_urn ?? relation.target,
                    )}
                  </code>
                </div>
              ))}
            </div>
          </section>
        )}
        {!!error && <ErrorBox error={error} compact />}
        {(rejecting || editedCount > 0) && (
          <label className="field-label reject-reason">
            {rejecting ? "拒绝原因" : "更正说明"}
            <textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              rows={3}
              placeholder={
                rejecting
                  ? "简要说明不应保存的原因"
                  : "简要说明这次人工更正的依据"
              }
              autoFocus={rejecting}
            />
          </label>
        )}
        <div className="detail-uri">
          <span>批次标识</span>
          <code>{review.id}</code>
        </div>
      </div>
      <div className="drawer-actions">
        {isPending ? (
          rejecting ? (
            <>
              <button
                className="button button-quiet"
                onClick={() => setRejecting(false)}
                disabled={!!busy}
              >
                返回核对
              </button>
              <button
                className="button button-danger"
                onClick={() => void act("reject")}
                disabled={!!busy}
              >
                {busy === "reject" ? (
                  <LoaderCircle size={15} className="spin" />
                ) : (
                  <X size={15} />
                )}
                拒绝此批次
              </button>
            </>
          ) : (
            <>
              <button
                className="button button-quiet danger-text"
                onClick={() => setRejecting(true)}
                disabled={!!busy}
              >
                <X size={15} />
                拒绝批次
              </button>
              <button
                className="button button-primary"
                onClick={() => void act("approve")}
                disabled={
                  !!busy || invalidDraft || (editedCount > 0 && !reason.trim())
                }
              >
                {busy === "approve" ? (
                  <LoaderCircle size={15} className="spin" />
                ) : (
                  <CheckCheck size={15} />
                )}
                {editedCount ? "保存更正并批准" : "批准此批次"}
              </button>
            </>
          )
        ) : (
          <>
            <Badge tone={jobTone(review.status)}>
              {jobLabels[review.status] ?? review.status}
            </Badge>
            <button className="button button-secondary" onClick={close}>
              关闭
            </button>
          </>
        )}
      </div>
    </Overlay>
  );
}

function AuthDialog({
  initialValue,
  canClose,
  onClose,
  onConnected,
}: {
  initialValue: string;
  canClose: boolean;
  onClose: () => void;
  onConnected: (token: string) => void;
}) {
  const [value, setValue] = useState(initialValue),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<unknown>(null);
  const close = useCallback(() => {
    if (canClose && !busy) onClose();
  }, [canClose, busy, onClose]);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await createApi(() => value.trim()).status();
      onConnected(value.trim());
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Overlay
      onClose={close}
      className="modal-overlay auth-overlay"
      labelledBy="auth-title"
    >
      <form onSubmit={(event) => void submit(event)}>
        <div className="auth-heading">
          <Brand small />
          {canClose && (
            <button
              className="icon-button"
              type="button"
              onClick={close}
              disabled={busy}
              aria-label="关闭连接设置"
            >
              <X size={18} />
            </button>
          )}
        </div>
        <div className="auth-art" aria-hidden="true">
          <span />
          <span />
          <div>
            <KeyRound size={31} strokeWidth={1.2} />
          </div>
          <span />
        </div>
        <div className="section-eyebrow">YOUR MEMORIES, YOUR SPACE</div>
        <h2 id="auth-title">连接你的记忆工作台</h2>
        <p className="modal-subtitle">
          输入记忆服务的访问令牌。你的连接只保留在当前浏览器会话中。
        </p>
        <div className="modal-body">
          <label className="field-label">
            访问令牌
            <div className="token-input">
              <KeyRound size={16} />
              <input
                value={value}
                onChange={(event) => setValue(event.target.value)}
                type="password"
                autoComplete="off"
                spellCheck={false}
                placeholder="粘贴服务提供的访问令牌"
                autoFocus
                required
              />
            </div>
          </label>
          {!!error && <ErrorBox error={error} compact />}
          <button
            className="button button-primary auth-submit"
            type="submit"
            disabled={busy || !value.trim()}
          >
            {busy ? (
              <LoaderCircle size={16} className="spin" />
            ) : (
              <ArrowRight size={16} />
            )}
            {busy ? "正在验证连接" : "连接记忆服务"}
          </button>
          <p className="auth-hint">
            <LockKeyhole size={12} />
            令牌通过请求头发送，不写入请求地址。
          </p>
        </div>
      </form>
    </Overlay>
  );
}
