import { describe, expect, it } from "vitest";
import { reviewInteractionState } from "./App";

const idle = {
  busy: false,
  refreshing: false,
  editing: false,
  editedCount: 0,
  rejecting: false,
  reason: "",
  resolved: false,
  pending: true,
};

describe("review navigation protection", () => {
  it("leaves an untouched review freely navigable", () => {
    expect(reviewInteractionState(idle)).toEqual({
      locked: false,
      hasDraft: false,
    });
  });

  it("keeps editing and rejection modes in context without claiming unchanged inputs are a draft", () => {
    expect(reviewInteractionState({ ...idle, editing: true })).toEqual({
      locked: true,
      hasDraft: false,
    });
    expect(reviewInteractionState({ ...idle, rejecting: true })).toEqual({
      locked: true,
      hasDraft: false,
    });
  });

  it("requires an explicit discard for changed fields or a reason even after editing closes", () => {
    expect(reviewInteractionState({ ...idle, editedCount: 1 })).toEqual({
      locked: true,
      hasDraft: true,
    });
    expect(
      reviewInteractionState({
        ...idle,
        reason: "The source contradicts this proposal",
      }),
    ).toEqual({ locked: true, hasDraft: true });
    expect(reviewInteractionState({ ...idle, reason: "  \n " })).toEqual({
      locked: false,
      hasDraft: false,
    });
  });

  it("holds navigation until an in-flight submission or latest-version read finishes", () => {
    expect(reviewInteractionState({ ...idle, busy: true }).locked).toBe(true);
    expect(reviewInteractionState({ ...idle, refreshing: true }).locked).toBe(
      true,
    );
    expect(
      reviewInteractionState({
        ...idle,
        busy: true,
        pending: false,
        resolved: true,
      }).locked,
    ).toBe(true);
  });

  it("releases navigation after an acknowledged result even if its displayed draft remains for comparison", () => {
    expect(
      reviewInteractionState({
        ...idle,
        editing: true,
        editedCount: 2,
        rejecting: true,
        reason: "Correction rationale",
        resolved: true,
      }),
    ).toEqual({ locked: false, hasDraft: false });
  });

  it("does not lock a read-only terminal batch because of obsolete draft state", () => {
    expect(
      reviewInteractionState({
        ...idle,
        pending: false,
        editing: true,
        editedCount: 2,
        reason: "Old rationale",
      }),
    ).toEqual({ locked: false, hasDraft: false });
  });
});
