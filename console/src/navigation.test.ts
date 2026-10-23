import { describe, expect, it } from "vitest";
import { pageFromSearch, pageLocation } from "./navigation";

describe("workspace navigation", () => {
  it("supports a direct ontology bookmark and rejects unknown page names", () => {
    for (const legacy of ["memories", "ontology", "reviews"])
      expect(pageFromSearch(`?view=${legacy}`)).toBe("graph");
    expect(pageFromSearch("?view=graph")).toBe("graph");
    expect(pageFromSearch("?view=__proto__")).toBe("graph");
    expect(pageFromSearch("?view=missing")).toBe("graph");
  });

  it("keeps page bookmarks without copying credentials or personal context to history", () => {
    expect(
      pageLocation(
        "graph",
        "http://localhost/?mode=personal#token=private&section=types",
      ),
    ).toBe("/?view=graph");
    expect(
      pageLocation(
        "graph",
        "http://localhost/?view=ontology#access_token=private",
      ),
    ).toBe("/?view=graph");
    expect(
      pageLocation(
        "graph",
        "http://localhost/?q=private&uri=urn:person:owner&token=secret#draft=private",
      ),
    ).toBe("/?view=graph");
  });
});
