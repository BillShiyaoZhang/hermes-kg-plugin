import { GRAPH_TYPE_COLORS } from "./graphModel";
import {
  directoryDefinitions,
  toggleDirectoryRelation,
} from "./graphDirectory";
import { displayCount, knownCount, relationLabel } from "./ontologyHelpers";
import { ontologyDefinitionDescription } from "./ontologyPresentation";
import type { OntologySchema } from "./ontologyTypes";
import { ontologyDefinitionLabel } from "./ontologyV3";

export default function OntologyDirectory({
  schema,
  types,
  relations,
  onTypesChange,
  onRelationsChange,
}: {
  schema: OntologySchema | null;
  types: string[];
  relations: string[];
  onTypesChange: (types: string[]) => void;
  onRelationsChange: (relations: string[]) => void;
}) {
  if (!schema)
    return <p className="gw-muted">连接后读取当前空间的本体目录。</p>;
  const definitions = directoryDefinitions(schema);
  const typeKeys = definitions.types.map((item) => item.key);
  const relationKeys = definitions.relations.map((item) => item.key);
  const selectedCount = typeKeys.filter((key) => types.includes(key)).length;
  const count = (value: number | null, noun: string) => (
    <span
      className="gw-directory-count"
      aria-label={knownCount(value) ? `当前空间 ${value} ${noun}` : "数量未知"}
      title={knownCount(value) ? `当前空间有效${noun}数量` : "当前空间数量未知"}
    >
      {displayCount(value)}
    </span>
  );
  return (
    <>
      <fieldset className="gw-concept-directory">
        <legend>本体概念 · {definitions.types.length}</legend>
        <p className="gw-muted">
          按本体概念筛选。数量为当前空间的有效记录，包含尚未加载到画布的内容。
        </p>
        <div className="gw-directory-actions">
          <span>
            已选 {selectedCount} / {typeKeys.length}
          </span>
          <button
            disabled={selectedCount === typeKeys.length}
            onClick={() => onTypesChange([...typeKeys])}
          >
            全选
          </button>
          <button disabled={!selectedCount} onClick={() => onTypesChange([])}>
            清空
          </button>
        </div>
        {definitions.types.map((definition) => (
          <label
            key={definition.key}
            className={`gw-directory-definition${definition.count === 0 ? " is-empty" : ""}`}
          >
            <input
              type="checkbox"
              checked={types.includes(definition.key)}
              onChange={() =>
                onTypesChange(
                  types.includes(definition.key)
                    ? types.filter((key) => key !== definition.key)
                    : [...types, definition.key],
                )
              }
              aria-label={ontologyDefinitionLabel(definition)}
            />
            <i
              aria-hidden="true"
              style={{
                background:
                  GRAPH_TYPE_COLORS[definition.key]?.stroke ?? "#657a69",
              }}
            />
            <span className="gw-directory-copy">
              <strong>{ontologyDefinitionLabel(definition)}</strong>
              <small>
                {ontologyDefinitionDescription(definition) ||
                  "当前本体定义的有效概念。"}
              </small>
              {definition.count === 0 && (
                <small className="gw-directory-empty">暂无记录</small>
              )}
            </span>
            {count(definition.count, "条记录")}
          </label>
        ))}
        {!definitions.types.length && (
          <p className="gw-empty">当前没有可用于筛选的有效概念。</p>
        )}
      </fieldset>
      <details open={relations.length > 0}>
        <summary>
          业务关系
          {relations.length
            ? ` · ${relations.includes("__none__") ? "全部隐藏" : relations.length}`
            : ""}
        </summary>
        <p className="gw-muted">筛选关系，保留符合概念筛选的孤立记录。</p>
        {definitions.relations.map((definition) => (
          <label
            key={definition.key}
            className="gw-directory-definition gw-directory-relation"
          >
            <input
              type="checkbox"
              checked={!relations.length || relations.includes(definition.key)}
              onChange={() =>
                onRelationsChange(
                  toggleDirectoryRelation(
                    relations,
                    definition.key,
                    relationKeys,
                  ),
                )
              }
              aria-label={relationLabel(definition)}
            />
            <span className="gw-directory-copy">
              <strong>{relationLabel(definition)}</strong>
              <small>{ontologyDefinitionDescription(definition)}</small>
            </span>
            {count(definition.count, "条关系")}
          </label>
        ))}
        {!definitions.relations.length && (
          <p className="gw-empty">当前没有可用于筛选的有效关系。</p>
        )}
        <button onClick={() => onRelationsChange([])}>显示全部关系</button>
      </details>
    </>
  );
}
