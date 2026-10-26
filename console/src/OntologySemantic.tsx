import type { OntologySemanticBinding } from "./ontologyTypes";

const sourceNames: Record<string, string> = {
  schema: "Schema.org",
  skos: "SKOS",
  prov: "PROV-O",
  dcmi: "Dublin Core",
  dcterms: "Dublin Core",
  dcmitype: "Dublin Core",
  personal: "个人记忆本体",
};
const mappings: Record<string, string> = {
  reuse: "复用公开定义",
  subclass: "采用更具体的类型",
  subproperty: "采用更具体的属性或关系",
  projection: "存储与语义映射",
  local: "本地扩展",
  annotation: "相关参考",
};

function sourceLink(iri: string): string | undefined {
  try {
    const url = new URL(iri);
    return url.protocol === "https:" || url.protocol === "http:"
      ? url.href
      : undefined;
  } catch {
    return undefined;
  }
}

export default function OntologySemantic({
  semantic,
  showDirection = false,
}: {
  semantic?: OntologySemanticBinding;
  showDirection?: boolean;
}) {
  if (!semantic) return null;
  const link = sourceLink(semantic.iri);
  const direction = ["reverse", "inverse", "reversed"].includes(
    semantic.direction || "",
  )
    ? "语义投影时反向，保存的关系方向不变"
    : ["forward", "same"].includes(semantic.direction || "")
      ? "保持保存的关系方向"
      : semantic.direction;
  return (
    <details className="ov3-semantic">
      <summary>语义定义与来源</summary>
      <dl>
        <div>
          <dt>定义来源</dt>
          <dd>{sourceNames[semantic.source] || semantic.source}</dd>
        </div>
        <div>
          <dt>来源版本</dt>
          <dd>{semantic.source_version}</dd>
        </div>
        <div>
          <dt>语义标识</dt>
          <dd>
            {link ? (
              <a href={link} target="_blank" rel="noopener noreferrer">
                <code>{semantic.iri}</code>
              </a>
            ) : (
              <code>{semantic.iri}</code>
            )}
          </dd>
        </div>
        <div>
          <dt>对齐方式</dt>
          <dd>{mappings[semantic.mapping] || semantic.mapping}</dd>
        </div>
        {showDirection && direction && (
          <div>
            <dt>对齐方向</dt>
            <dd>{direction}</dd>
          </div>
        )}
      </dl>
      {semantic.note && <p>{semantic.note}</p>}
      <p className="ov3-semantic-note">
        来源信息只读；显示名称和本地规则以当前空间定义为准。
      </p>
    </details>
  );
}
