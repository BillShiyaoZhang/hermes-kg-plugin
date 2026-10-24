import { describe, expect, it, vi } from "vitest";
import { createApi } from "./api";
import type { OntologyEditRequest } from "./ontologyTypes";

const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json" },
  });
const input: OntologyEditRequest = {
  expected_namespace: "urn:private:kg:",
  expected_version: 3,
  patch: {
    entity_types: {
      person: { label: "客户 · 私人定义", description: "当前空间的联系人" },
    },
  },
};
const publishInput = {
  ...input,
  operation_id: "synthetic-ontology-operation",
  reason: "人工核对",
};
const schema = {
  namespace: input.expected_namespace,
  version: 4,
  entity_types: [],
  relations: [],
  properties: [],
};
const receipt = {
  ok: true,
  namespace: input.expected_namespace,
  version: 4,
  operation_id: publishInput.operation_id,
  idempotent: false,
  schema,
};

describe("namespace ontology publication transport", () => {
  it("keeps draft definitions, scope and version inside authenticated POST bodies", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(response({ can_publish: true }))
      .mockResolvedValueOnce(response(receipt));
    const api = createApi(() => "synthetic-auth", fetcher);
    await api.ontologyValidate(input);
    expect(await api.ontologyPublish(publishInput)).toEqual(receipt);
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/ontology/validate",
      "/api/v1/ontology/publish",
    ]);
    for (const [index, [url, init]] of fetcher.mock.calls.entries()) {
      expect(String(url)).not.toMatch(/private|客户|synthetic-auth|\?/);
      expect(init?.method).toBe("POST");
      expect(init?.credentials).toBe("same-origin");
      expect(new Headers(init?.headers).get("Authorization")).toBe(
        "Bearer synthetic-auth",
      );
      expect(JSON.parse(String(init?.body))).toEqual(
        index === 0 ? input : publishInput,
      );
    }
  });
  it("retains structured conflicts for a manual rebase while making no automatic publish retry", async () => {
    const conflict = {
      detail: "Version changed",
      code: "ontology_version_conflict",
      version: 5,
      impact: { violations: [] },
    };
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(response(conflict, 409));
    await expect(
      createApi(() => "t", fetcher).ontologyPublish(publishInput),
    ).rejects.toMatchObject({ status: 409, detail: conflict });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("cancels superseded previews without resubmission", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(() => new Promise(() => {}));
    const controller = new AbortController();
    const pending = createApi(() => "t", fetcher).ontologyValidate(
      input,
      controller.signal,
    );
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(true);
  });
  it.each([
    { ...receipt, namespace: "urn:another:kg:" },
    { ...receipt, operation_id: "unexpected-operation" },
    { ...receipt, schema: { ...schema, version: 5 } },
    { ...receipt, schema: { ...schema, namespace: "urn:another:kg:" } },
    { ...receipt, schema: { namespace: input.expected_namespace, version: 4 } },
  ])(
    "rejects incomplete or mismatched receipts instead of reporting an acknowledged publication",
    async (data) => {
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(data));
      await expect(
        createApi(() => "t", fetcher).ontologyPublish(publishInput),
      ).rejects.toMatchObject({ status: 502 });
    },
  );
  it("retains the same publication identity for an explicit retry after an uncertain response", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockRejectedValueOnce(new Error("private network error"))
      .mockResolvedValueOnce(response({ ...receipt, idempotent: true }));
    const api = createApi(() => "t", fetcher);
    await expect(api.ontologyPublish(publishInput)).rejects.toMatchObject({
      status: 0,
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(await api.ontologyPublish(publishInput)).toMatchObject({
      idempotent: true,
    });
    expect(
      fetcher.mock.calls.map(
        ([, init]) => JSON.parse(String(init?.body)).operation_id,
      ),
    ).toEqual([publishInput.operation_id, publishInput.operation_id]);
  });
});
