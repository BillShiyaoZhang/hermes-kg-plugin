import { useId, useMemo } from "react";
import type { GraphNode } from "./graphModel";
import "./eventTimeline.css";

const DAY_MS = 86_400_000;
export interface EventTimelineBucket {
  start: string;
  end: string;
  count: number;
}
export interface EventTimelineDistribution {
  buckets: EventTimelineBucket[];
  dated: { id: string; day: string }[];
  unknown: number;
  total: number;
  bucketDays: number;
  minDay: number | null;
  maxDay: number | null;
}
export interface EventTimelineProps {
  nodes: GraphNode[];
  start: string;
  end: string;
  includeUnknown: boolean;
  onChange: (patch: {
    start?: string;
    end?: string;
    includeUnknown?: boolean;
  }) => void;
}

export function eventDay(value?: string): string | null {
  const day = value?.match(/^(\d{4}-\d{2}-\d{2})(?:T|\s|$)/)?.[1];
  if (!day) return null;
  const timestamp = Date.parse(`${day}T00:00:00Z`);
  return Number.isFinite(timestamp) &&
    new Date(timestamp).toISOString().slice(0, 10) === day
    ? day
    : null;
}
const dayNumber = (date: string) => Date.parse(`${date}T00:00:00Z`) / DAY_MS;
const dayString = (day: number) =>
  new Date(day * DAY_MS).toISOString().slice(0, 10);

/** The histogram describes loaded event objects, never full-library totals. */
export function eventTimelineDistribution(
  nodes: GraphNode[],
  maxBuckets = 30,
): EventTimelineDistribution {
  const events = [
    ...new Map(
      nodes
        .filter(
          (node) =>
            node.type === "event" &&
            (node.layer === "saved" || node.layer === "candidate"),
        )
        .map((node) => [node.id, node]),
    ).values(),
  ];
  const dated = events.flatMap((node) => {
    const day = eventDay(node.eventTime);
    return day ? [{ id: node.id, day }] : [];
  });
  if (!dated.length)
    return {
      buckets: [],
      dated,
      unknown: events.length,
      total: events.length,
      bucketDays: 1,
      minDay: null,
      maxDay: null,
    };
  const days = dated.map((item) => dayNumber(item.day));
  const minDay = Math.min(...days);
  const maxDay = Math.max(...days);
  const span = maxDay - minDay + 1;
  const budget = Number.isFinite(maxBuckets)
    ? Math.max(1, Math.min(30, Math.floor(maxBuckets)))
    : 30;
  const bucketDays = Math.max(1, Math.ceil(span / budget));
  const buckets: EventTimelineBucket[] = Array.from(
    { length: Math.ceil(span / bucketDays) },
    (_, index) => ({
      start: dayString(minDay + index * bucketDays),
      end: dayString(Math.min(maxDay, minDay + (index + 1) * bucketDays - 1)),
      count: 0,
    }),
  );
  for (const day of days)
    buckets[Math.floor((day - minDay) / bucketDays)].count++;
  return {
    buckets,
    dated,
    unknown: events.length - dated.length,
    total: events.length,
    bucketDays,
    minDay,
    maxDay,
  };
}

export function eventTimelineSelection(
  distribution: EventTimelineDistribution,
  start: string,
  end: string,
  includeUnknown: boolean,
): number {
  return (
    distribution.dated.filter(
      (item) => (!start || item.day >= start) && (!end || item.day <= end),
    ).length + (includeUnknown ? distribution.unknown : 0)
  );
}

export default function EventTimeline({
  nodes,
  start,
  end,
  includeUnknown,
  onChange,
}: EventTimelineProps) {
  const id = useId();
  const distribution = useMemo(() => eventTimelineDistribution(nodes), [nodes]);
  const { buckets, minDay, maxDay } = distribution;
  const hasDates = minDay !== null && maxDay !== null;
  const span = hasDates ? maxDay - minDay : 0;
  const position = (date: string, fallback: number) => {
    const day = eventDay(date);
    return hasDates && day
      ? Math.max(0, Math.min(span, dayNumber(day) - minDay))
      : fallback;
  };
  const startPosition = position(start, 0);
  const endPosition = position(end, span);
  const selected = eventTimelineSelection(
    distribution,
    start,
    end,
    includeUnknown,
  );
  const peak = Math.max(1, ...buckets.map((bucket) => bucket.count));
  const chooseStart = (value: string) =>
    onChange({ start: value && end && value > end ? end : value });
  const chooseEnd = (value: string) =>
    onChange({ end: value && start && value < start ? start : value });
  const minimum = hasDates ? dayString(minDay) : "";
  const maximum = hasDates ? dayString(maxDay) : "";
  return (
    <section className="evt-timeline" aria-labelledby={`${id}-title`}>
      <header>
        <h3 id={`${id}-title`}>事件时间</h3>
        <button
          onClick={() => onChange({ start: "", end: "", includeUnknown: true })}
          disabled={!start && !end && includeUnknown}
        >
          显示全部
        </button>
      </header>
      <p className="evt-scope">
        当前已加载的 {distribution.total}{" "}
        个事件对象；此分布不代表全库。候选与已保存对象分别统计。
      </p>
      {hasDates ? (
        <>
          <div className="evt-chart-wrap">
            <svg
              viewBox="0 0 600 112"
              role="img"
              aria-labelledby={`${id}-chart-title ${id}-chart-description`}
              preserveAspectRatio="none"
            >
              <title id={`${id}-chart-title`}>已加载事件的时间分布</title>
              <desc id={`${id}-chart-description`}>
                {minimum} 至 {maximum}，共 {distribution.dated.length}{" "}
                个有日期的事件，每格 {distribution.bucketDays}{" "}
                天。可用下方起止滑块或日期输入刷选。
              </desc>
              <line
                x1="0"
                x2="600"
                y1="104"
                y2="104"
                className="evt-baseline"
              />
              {buckets.map((bucket, index) => {
                const width = 600 / buckets.length;
                const selectedBucket =
                  (!start || bucket.end >= start) &&
                  (!end || bucket.start <= end);
                const height = bucket.count
                  ? Math.max(4, (bucket.count / peak) * 88)
                  : 2;
                return (
                  <rect
                    key={bucket.start}
                    x={index * width + Math.min(2, width / 8)}
                    y={104 - height}
                    width={Math.max(1, width - Math.min(4, width / 4))}
                    height={height}
                    rx="2"
                    className={`${selectedBucket ? "evt-bucket-selected" : "evt-bucket-dimmed"}${!bucket.count ? " evt-bucket-empty" : ""}`}
                  >
                    <title>
                      {bucket.start}
                      {bucket.end !== bucket.start ? ` 至 ${bucket.end}` : ""}：
                      {bucket.count} 个已加载事件
                    </title>
                  </rect>
                );
              })}
            </svg>
          </div>
          <div className="evt-axis" aria-hidden="true">
            <span>{minimum}</span>
            <span>{maximum}</span>
          </div>
          {distribution.bucketDays > 1 && (
            <p className="evt-bucket-note">
              每格合并 {distribution.bucketDays} 天，共 {buckets.length} 格。
            </p>
          )}
          <div className="evt-brush">
            <label htmlFor={`${id}-start-range`}>
              <span>起始</span>
              <output>{start || minimum}</output>
            </label>
            <input
              id={`${id}-start-range`}
              type="range"
              min={0}
              max={span}
              step={1}
              value={startPosition}
              disabled={!span}
              aria-valuetext={start || minimum}
              onChange={(event) => {
                if (minDay !== null)
                  chooseStart(
                    dayString(
                      minDay +
                        Math.min(Number(event.target.value), endPosition),
                    ),
                  );
              }}
            />
            <label htmlFor={`${id}-end-range`}>
              <span>结束</span>
              <output>{end || maximum}</output>
            </label>
            <input
              id={`${id}-end-range`}
              type="range"
              min={0}
              max={span}
              step={1}
              value={endPosition}
              disabled={!span}
              aria-valuetext={end || maximum}
              onChange={(event) => {
                if (minDay !== null)
                  chooseEnd(
                    dayString(
                      minDay +
                        Math.max(Number(event.target.value), startPosition),
                    ),
                  );
              }}
            />
          </div>
        </>
      ) : (
        <div className="evt-no-dates">
          {distribution.total
            ? "已加载事件尚无可按日分布的日期。"
            : "当前已加载内容中没有事件记录。"}
        </div>
      )}
      <div className="evt-dates">
        <label htmlFor={`${id}-start`}>
          起始日期
          <input
            id={`${id}-start`}
            type="date"
            value={start}
            max={end || undefined}
            disabled={!hasDates}
            onChange={(event) => chooseStart(event.target.value)}
          />
        </label>
        <label htmlFor={`${id}-end`}>
          结束日期
          <input
            id={`${id}-end`}
            type="date"
            value={end}
            min={start || undefined}
            disabled={!hasDates}
            onChange={(event) => chooseEnd(event.target.value)}
          />
        </label>
      </div>
      <label className="evt-unknown">
        <input
          type="checkbox"
          checked={includeUnknown}
          onChange={(event) =>
            onChange({ includeUnknown: event.target.checked })
          }
        />
        <span>包含日期未知的事件</span>
        <strong>{distribution.unknown}</strong>
      </label>
      <p className="evt-selection" role="status">
        当前时间条件保留 {selected} / {distribution.total}{" "}
        个已加载事件。其他类型不受此筛选影响。
      </p>
      {!!distribution.dated.length && (
        <details className="evt-distribution-table">
          <summary>查看时间分布明细</summary>
          <table>
            <caption className="sr-only">
              已加载事件的时间分布，不代表全库
            </caption>
            <thead>
              <tr>
                <th scope="col">日期范围</th>
                <th scope="col">事件数</th>
              </tr>
            </thead>
            <tbody>
              {buckets.map((bucket) => (
                <tr key={bucket.start}>
                  <th scope="row">
                    {bucket.start}
                    {bucket.start !== bucket.end ? ` 至 ${bucket.end}` : ""}
                  </th>
                  <td>{bucket.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </section>
  );
}
