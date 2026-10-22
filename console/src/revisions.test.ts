import { describe, expect, it } from "vitest";
import { normalizeMemory } from "./api";
import { revisionDiff } from "./RevisionHistory";

describe("human readable memory revisions", () => {
  it("retains the source snapshot and shows actual old/new decision content", () => {
    const memory = normalizeMemory({
      revisions: [
        {
          revision_id: "r2",
          version: 2,
          state: {
            labels: ["KGEntity", "Decision"],
            properties: {
              hermes_name: "工具选择",
              hermes_decisionText: "选择 B",
            },
          },
        },
        {
          revision_id: "r1",
          version: 1,
          state: {
            labels: ["KGEntity", "Decision"],
            properties: {
              hermes_name: "工具选择",
              hermes_decisionText: "选择 A",
            },
          },
        },
      ],
    });
    expect(memory.revisions[0].state.properties).toBeDefined();
    expect(
      revisionDiff(memory.revisions[0].state, memory.revisions[1].state),
    ).toEqual([{ label: "决策内容", before: "选择 A", after: "选择 B" }]);
  });
  it("shows a reversible archive as a status difference and hides internal bookkeeping", () => {
    const before = {
      labels: ["KGEntity", "Person"],
      properties: {
        foaf_name: "Alice",
        hermes_revision: 1,
        hermes_sourceSession: "private-source",
      },
    };
    const after = {
      labels: ["KGEntity", "Person", "KGQuarantined"],
      properties: {
        foaf_name: "Alice",
        hermes_revision: 2,
        hermes_sourceSession: "other-source",
      },
    };
    expect(revisionDiff(after, before)).toEqual([
      { label: "使用状态", before: "使用中", after: "已停用" },
    ]);
  });
});
