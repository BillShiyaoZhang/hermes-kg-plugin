/** Only page names enter browser history; memory URIs and credentials stay out. */
export type Page = "graph" | "sync" | "settings";

export const PAGE_LABELS: Record<Page, string> = {
  graph: "知识图谱",
  sync: "同步与任务",
  settings: "设置与诊断",
};

export function pageFromSearch(search: string): Page {
  const value = new URLSearchParams(search).get("view");
  if (value === "memories" || value === "ontology" || value === "reviews")
    return "graph";
  return value && Object.hasOwn(PAGE_LABELS, value) ? (value as Page) : "graph";
}

export function pageLocation(page: Page, href: string): string {
  const url = new URL(href);
  // The workspace only publishes page names. Unknown query/fragment values
  // may contain pasted credentials, search content or record identities.
  url.search = new URLSearchParams({ view: page }).toString();
  url.hash = "";
  return url.pathname + url.search + url.hash;
}
