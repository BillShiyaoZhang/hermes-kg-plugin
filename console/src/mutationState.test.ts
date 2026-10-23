import { describe, expect, it } from "vitest";
import { ApiError, normalizeMemory, normalizeReview } from "./api";
import {
  isPendingReview,
  operationForIntent,
  rebaseMemoryDraft,
  reviewChanges,
  writeFailure,
} from "./mutationState";

describe("write intent and review safety", () => {
  it("reuses the idempotency key after a lost response, but never for an edited or rebased request", () => {
    const initial = operationForIntent(
      null,
      { action: "correct", version: 2, content: "A" },
      () => "first",
    );
    expect(
      operationForIntent(
        initial,
        { action: "correct", version: 2, content: "A" },
        () => "retry",
      ),
    ).toBe(initial);
    const edited = operationForIntent(
      initial,
      { action: "correct", version: 2, content: "B" },
      () => "edited",
    );
    expect(edited.id).toBe("edited");
    expect(
      operationForIntent(
        edited,
        { action: "correct", version: 3, content: "B" },
        () => "rebased",
      ).id,
    ).toBe("rebased");
  });

  it("keeps approval and rejection distinct even when their reason is unchanged", () => {
    const approved = operationForIntent(
      null,
      { action: "approve", reason: "checked" },
      () => "approval",
    );
    expect(
      operationForIntent(
        approved,
        { action: "reject", reason: "checked" },
        () => "rejection",
      ).id,
    ).toBe("rejection");
  });

  it("does not expose unsafe controls for unrecognised or terminal review states", () => {
    expect(isPendingReview("pending")).toBe(true);
    for (const state of [
      "approved",
      "rejected",
      "committed",
      "running",
      "unknown",
      "",
    ])
      expect(isPendingReview(state)).toBe(false);
  });

  it("does not count an untouched or reverted editor as an actual correction", () => {
    const review = normalizeReview({
      id: "batch",
      entities: [
        {
          uri: "urn:test:a",
          name: "A",
          type: "Decision",
          properties: { hermes_decisionText: "before" },
        },
      ],
    });
    expect(
      reviewChanges(review, {
        "urn:test:a": { name: " A ", content: "before" },
      }),
    ).toEqual({});
    expect(
      reviewChanges(review, { "urn:test:a": { name: "A", content: "after" } }),
    ).toEqual({
      "urn:test:a": { props: { decision_text: "after" } },
    });
  });

  it("rebases only untouched fields after a conflict and preserves explicit edits and clearing", () => {
    const previous = normalizeMemory({
      name: "Original",
      description: "Old content",
      event_time: "2026-01-01T09:00:00Z",
      properties: {
        hermes_preferenceKey: "Old key",
        hermes_preferenceValue: "Old value",
        hermes_decisionContext: "Old context",
      },
    });
    const latest = normalizeMemory({
      name: "Concurrent name",
      description: "Concurrent content",
      event_time: "2026-02-01T09:00:00Z",
      properties: {
        hermes_preferenceKey: "Concurrent key",
        hermes_preferenceValue: "Concurrent value",
        hermes_decisionContext: "Concurrent context",
      },
    });
    expect(
      rebaseMemoryDraft(
        {
          name: "Original",
          content: "My description",
          eventTime: "2026-01-01T09:00:00Z",
          preferenceKey: "Old key",
          preferenceValue: "My value",
          decisionContext: "",
        },
        previous,
        latest,
      ),
    ).toEqual({
      name: "Concurrent name",
      content: "My description",
      eventTime: "2026-02-01T09:00:00Z",
      preferenceKey: "Concurrent key",
      preferenceValue: "My value",
      decisionContext: "",
    });
  });

  it("preserves a deliberate conflicting name while taking an untouched concurrent description", () => {
    const previous = normalizeMemory({ name: "A", description: "before" });
    const latest = normalizeMemory({ name: "B", description: "newer" });
    expect(
      rebaseMemoryDraft(
        {
          name: "My name",
          content: "before",
          eventTime: "",
          preferenceKey: "",
          preferenceValue: "",
          decisionContext: "",
        },
        previous,
        latest,
      ),
    ).toMatchObject({ name: "My name", content: "newer" });
  });

  it("does not turn an untouched candidate name into a human override when only its description was edited", () => {
    const review = normalizeReview({
      id: "batch",
      expected_versions: { "urn:test:a": 2 },
      entities: [
        {
          uri: "urn:test:a",
          name: "Candidate name",
          type: "Concept",
          properties: { hermes_description: "candidate description" },
        },
      ],
    });
    // Reading current records changes expected versions, never the immutable
    // extraction candidate used to determine deliberate human edits.
    const refreshed = { ...review, expectedVersions: { "urn:test:a": 3 } };
    expect(
      reviewChanges(refreshed, {
        "urn:test:a": { name: "Candidate name", content: "human description" },
      }),
    ).toEqual({
      "urn:test:a": { props: { description: "human description" } },
    });
  });

  it("explains version conflicts without losing their status or exposing returned record values", () => {
    const result = writeFailure(new ApiError("private-returned-value", 409));
    expect(result).toMatchObject({ status: 409 });
    expect((result as Error).message).toContain("输入仍保留");
    expect((result as Error).message).not.toContain("private-returned-value");
    const offline = new ApiError("连接中断", 0);
    expect(writeFailure(offline)).toBe(offline);
  });
});
