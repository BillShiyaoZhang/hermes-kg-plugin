import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { GraphNode } from "./graphModel";
import EventTimeline, {
  eventDay,
  eventTimelineDistribution,
  eventTimelineSelection,
} from "./EventTimeline";

const event = (
  id: string,
  eventTime?: string,
  extra: Partial<GraphNode> = {},
): GraphNode => ({
  id,
  type: "event",
  layer: "saved",
  label: id,
  eventTime,
  ...extra,
});

describe("loaded event distributions", () => {
  it("uses calendar dates without inventing a timezone or precision", () => {
    expect(eventDay("2026-10-23T23:00:00-08:00")).toBe("2026-10-23");
    expect(eventDay("2024-02-29")).toBe("2024-02-29");
    expect(eventDay("2026-02-29")).toBeNull();
    expect(eventDay("2026-02-30")).toBeNull();
    expect(eventDay("2026-10")).toBeNull();
    expect(eventDay("unknown")).toBeNull();
  });
  it("counts saved and candidate objects separately while excluding schema and collections", () => {
    const nodes = [
      event("a", "2026-10-01"),
      event("b", undefined),
      event("c", "2026-10-01", { layer: "candidate" }),
      event("schema:event", undefined, { layer: "schema" }),
      event("aggregate:event", undefined, { layer: "aggregate", count: 400 }),
      event("not-event", "2026-10-01", { type: "person" }),
    ];
    const result = eventTimelineDistribution([...nodes, nodes[0]]);
    expect(result.total).toBe(3);
    expect(result.unknown).toBe(1);
    expect(result.buckets).toEqual([
      { start: "2026-10-01", end: "2026-10-01", count: 2 },
    ]);
  });
  it("keeps empty days visible and creates at most 30 truthful date intervals", () => {
    const daily = eventTimelineDistribution([
      event("a", "2026-10-01"),
      event("b", "2026-10-03"),
    ]);
    expect(daily.buckets.map((bucket) => bucket.count)).toEqual([1, 0, 1]);
    const broad = eventTimelineDistribution([
      event("a", "2000-01-01"),
      event("b", "2026-10-23"),
    ]);
    expect(broad.buckets.length).toBeLessThanOrEqual(30);
    expect(broad.buckets[0].start).toBe("2000-01-01");
    expect(broad.buckets[broad.buckets.length - 1].end).toBe("2026-10-23");
    expect(broad.buckets.reduce((sum, bucket) => sum + bucket.count, 0)).toBe(
      2,
    );
  });
  it("has a measured empty state and explicit unknown-only distribution", () => {
    expect(eventTimelineDistribution([])).toMatchObject({
      total: 0,
      unknown: 0,
      minDay: null,
      maxDay: null,
      buckets: [],
    });
    expect(
      eventTimelineDistribution([event("a"), event("b", "2026-10")]),
    ).toMatchObject({ total: 2, unknown: 2, buckets: [] });
  });
  it("brushes inclusive boundaries and treats the unknown checkbox independently", () => {
    const distribution = eventTimelineDistribution([
      event("a", "2026-10-01"),
      event("b", "2026-10-23"),
      event("c", "2026-10-31"),
      event("unknown"),
    ]);
    expect(
      eventTimelineSelection(distribution, "2026-10-01", "2026-10-23", false),
    ).toBe(2);
    expect(
      eventTimelineSelection(distribution, "2026-10-01", "2026-10-23", true),
    ).toBe(3);
    expect(eventTimelineSelection(distribution, "", "", true)).toBe(4);
    expect(
      eventTimelineSelection(distribution, "2027-01-01", "2027-12-31", false),
    ).toBe(0);
  });
});

describe("event timeline controls", () => {
  it("provides named start/end sliders, date fields, unknown counts and an accessible data alternative", () => {
    const html = renderToStaticMarkup(
      <EventTimeline
        nodes={[
          event("a", "2026-10-01"),
          event("b", "2026-10-23"),
          event("unknown"),
        ]}
        start="2026-10-01"
        end="2026-10-23"
        includeUnknown={false}
        onChange={() => {}}
      />,
    );
    expect(html.match(/type="range"/g)).toHaveLength(2);
    expect(html.match(/type="date"/g)).toHaveLength(2);
    expect(html).toContain("此分布不代表全库");
    expect(html).toContain("包含日期未知的事件");
    expect(html).toContain("查看时间分布明细");
    expect(html).toContain("保留 2 / 3 个已加载事件");
  });
});
