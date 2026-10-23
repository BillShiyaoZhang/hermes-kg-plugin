import { describe, expect, it } from "vitest";
import { confirmedBackfillSessions } from "./backfillReceipt";

describe("backfill queue confirmation", () => {
  it("uses the confirmed subset rather than inventing success from the requested count", () => {
    expect(
      confirmedBackfillSessions({ ok: true, queued_sessions: ["b"] }, [
        "a",
        "b",
      ]),
    ).toEqual(["b"]);
    expect(
      confirmedBackfillSessions({ ok: true, queued_sessions: [] }, ["a"]),
    ).toEqual([]);
  });

  it.each([
    { ok: true },
    { ok: true, queued_sessions: 2 },
    { ok: false, queued_sessions: ["a"] },
    { ok: true, queued_sessions: ["unselected"] },
    { ok: true, queued_sessions: ["a", "a"] },
    { ok: true, queued_sessions: [null] },
  ])(
    "rejects an unverifiable receipt and leaves selection decisions to the caller",
    (receipt) => {
      expect(() => confirmedBackfillSessions(receipt, ["a", "b"])).toThrow(
        /已保留/,
      );
    },
  );
});
