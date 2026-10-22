"""Report label coverage for the active namespace without dumping entity content."""
from pathlib import Path
import _kg_import
from kg.store import KGStore

ONTOLOGY_PATH = Path(__file__).resolve().parents[1] / "ontology" / "core.ttl"


def get_ontology_classes(ontology_path=ONTOLOGY_PATH):
    from rdflib import Graph, Namespace
    graph = Graph().parse(str(ontology_path), format="turtle")
    return {str(value) for value in graph.objects(None, Namespace("urn:hermes:ontology:").neo4jLabel)}


def get_kg_labels():
    with KGStore() as store:
        rows = store.run_read("MATCH (n) WHERE n.uri STARTS WITH $prefix UNWIND labels(n) AS label RETURN DISTINCT label", {"prefix": store.config.urn_prefix})
    return {row["label"] for row in rows}


def discover_gaps():
    expected = get_ontology_classes() | {"KGEntity", "KGLegacyAlias", "KGLegacyUnresolved", "KGQuarantined"}
    gaps = [f"Unmapped own-namespace label: {label}" for label in sorted(get_kg_labels() - expected)]
    with KGStore() as store:
        rows = store.run_read("MATCH (n) WHERE n.uri STARTS WITH $prefix AND size(labels(n))=0 RETURN count(n) AS count", {"prefix": store.config.urn_prefix})
    if rows and rows[0]["count"]:
        gaps.append(f"Unlabeled own-namespace nodes: {rows[0]['count']}")
    return gaps


if __name__ == "__main__":
    _kg_import.configure_output()
    print("\n".join(discover_gaps()) or "No schema gaps found")
