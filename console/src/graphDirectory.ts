import type { GraphQuery } from "./graphData";
import type { GraphLens } from "./graphWorkspace";
import type { OntologySchema } from "./ontologyTypes";

export const DIRECTORY_STATE_VERSION = 1;

/** Old Explore sessions persisted the former hidden-by-default sidebar. */
export function restoreDirectoryState<
  T extends { sidebar: boolean; directoryVersion?: number },
>(view: T, lens: GraphLens): T & { directoryVersion: number } {
  if (view.directoryVersion === DIRECTORY_STATE_VERSION)
    return view as T & { directoryVersion: number };
  return {
    ...view,
    sidebar: lens === "explore" ? true : view.sidebar,
    directoryVersion: DIRECTORY_STATE_VERSION,
  };
}

export function directoryDefinitions(schema: OntologySchema) {
  const available = (item: { runtime_supported: boolean; active?: boolean }) =>
    item.runtime_supported && item.active !== false;
  return {
    types: schema.entity_types.filter(available),
    relations: schema.relations.filter(available),
  };
}

export function allDirectoryTypesSelected(selected: string[], keys: string[]) {
  return (
    selected.length === keys.length &&
    keys.every((key) => selected.includes(key))
  );
}

export function currentDirectoryRelations(
  selected: string[],
  schema: OntologySchema,
) {
  const available = new Set(
    directoryDefinitions(schema).relations.map((item) => item.key),
  );
  const current = selected.filter(
    (key) => key === "__none__" || available.has(key),
  );
  if (!available.size) return ["__none__"];
  // Removing the last selected definition must not silently broaden to all.
  return selected.length && !current.length ? ["__none__"] : current;
}

export function toggleDirectoryRelation(
  selected: string[],
  key: string,
  available: string[],
) {
  const current = selected.length
    ? selected.filter((value) => value !== "__none__")
    : available;
  const next = current.includes(key)
    ? current.filter((value) => value !== key)
    : [...current, key];
  return next.length ? next : ["__none__"];
}

/** null is an explicit empty selection; the API's types: [] means all types. */
export function directoryGraphQuery(
  view: {
    filterQuery: string;
    types: string[];
    relations: string[];
    focusUri: string | null;
  },
  availableTypes: string[],
  schema?: OntologySchema,
): GraphQuery | null {
  if (!view.types.length) return null;
  const universe = schema
    ? schema.entity_types
        .filter((item) => item.runtime_supported)
        .map((item) => item.key)
    : availableTypes;
  const relations = schema
    ? currentDirectoryRelations(view.relations, schema)
    : view.relations;
  const activeRelations = schema
    ? directoryDefinitions(schema).relations.map((item) => item.key)
    : [];
  const hasInactiveRelations = schema?.relations.some(
    (item) => item.runtime_supported && item.active === false,
  );
  return {
    q: view.filterQuery,
    // [] is the entire runtime universe, which may include historical instances
    // of disabled definitions. "All" in this directory means its active keys.
    types: allDirectoryTypesSelected(view.types, universe)
      ? []
      : view.types,
    relation_types:
      !relations.length && hasInactiveRelations
        ? activeRelations
        : relations.filter((key) => key !== "__none__"),
    focus_uri: view.focusUri,
    node_limit: 200,
    edge_limit: 500,
  };
}

/** The local no-relations state shares an API query with all, but not its result. */
export function directoryGraphKey(
  namespace: string,
  query: GraphQuery | null,
  relations: string[],
) {
  return JSON.stringify([namespace, query, relations.includes("__none__")]);
}
