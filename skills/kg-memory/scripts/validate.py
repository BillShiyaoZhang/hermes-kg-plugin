"""Validate RDF syntax/mappings and optionally report graph contract violations."""
import argparse
from collections import Counter
from pathlib import Path
import _kg_import

ONTOLOGY_PATH = Path(__file__).resolve().parents[1] / "ontology" / "core.ttl"


def validate(ttl_path=ONTOLOGY_PATH):
    from rdflib import Graph, Namespace
    from rdflib.namespace import OWL, RDF, RDFS
    from kg.schema import ENTITY_LABELS, RELATION_ALIASES
    graph = Graph()
    try:
        graph.parse(str(ttl_path), format="turtle")
    except Exception as exc:
        return [f"RDF parse error: {exc}"]
    errors = []
    ont = Namespace("urn:hermes:ontology:")
    ontology_nodes = list(graph.subjects(RDF.type, OWL.Ontology))
    if len(ontology_nodes) != 1 or not graph.value(ontology_nodes[0], OWL.versionInfo):
        errors.append("Exactly one versioned owl:Ontology is required")
    for subject in graph.subjects(RDF.type, OWL.Class):
        if not graph.value(subject, RDFS.label) or not graph.value(subject, RDFS.comment):
            errors.append(f"Class needs label and comment: {subject}")
    for property_type in (OWL.DatatypeProperty, OWL.ObjectProperty):
        for subject in graph.subjects(RDF.type, property_type):
            if not graph.value(subject, RDFS.domain) or not graph.value(subject, RDFS.range):
                errors.append(f"Property needs domain and range: {subject}")
            for predicate in (RDFS.domain, RDFS.range):
                for target in graph.objects(subject, predicate):
                    if str(target).startswith(str(ont)) and (target, RDF.type, OWL.Class) not in graph:
                        errors.append(f"Property refers to an undefined local class: {subject}")
    labels = {str(value) for value in graph.objects(None, ont.neo4jLabel)}
    if not set(ENTITY_LABELS.values()) <= labels:
        errors.append("Ontology is missing canonical entity label mappings")
    relationships = {str(value) for value in graph.objects(None, ont.neo4jRelationship)}
    if not set(RELATION_ALIASES.values()) <= relationships:
        errors.append("Ontology is missing canonical relationship mappings")
    for annotation in (ont.neo4jLabel, ont.neo4jProperty, ont.neo4jRelationship):
        mappings = Counter(str(value) for value in graph.objects(None, annotation))
        if any(count > 1 for count in mappings.values()):
            errors.append(f"Duplicate storage mapping: {annotation}")
    return errors


def validate_graph(store=None):
    from kg.config import load_config
    from kg.schema import (RELATION_ENDPOINTS, normalize_rel, runtime_entity_labels,
                           schema_for_store, validate_event_timing)
    from kg.ontology_editing import entity_violations, relation_violations
    from kg.store import KGStore
    if store is None:
        with KGStore(load_config()) as connection:
            return validate_graph(connection)
    counts = Counter()
    ontology = schema_for_store(store)
    registry = runtime_entity_labels(ontology)
    kinds_by_label = {label: key for key, label in registry.items()}
    rows = store.run_read("""
        MATCH (n) WHERE n.uri STARTS WITH $prefix
            AND NOT n:KGLegacyAlias AND NOT n:KGLegacyUnresolved AND NOT n:KGQuarantined
        RETURN labels(n) AS labels, properties(n) AS properties
    """, {"prefix": store.config.urn_prefix})
    for row in rows:
        labels = set(row["labels"]) & set(registry.values())
        props = row["properties"]
        if len(labels) != 1:
            counts["missing_or_ambiguous_entity_type"] += 1
            continue
        kind = kinds_by_label[next(iter(labels))]
        if not isinstance(props.get("shared"), bool):
            counts["shared_not_boolean"] += 1
        name = props.get("foaf_name") if kind == "person" else props.get("hermes_name")
        if not isinstance(name, str) or not name.strip():
            counts["missing_name"] += 1
        if kind == "event":
            try:
                validate_event_timing(props, store.config.timezone)
            except (ValueError, TypeError, OverflowError):
                counts["invalid_event_time"] += 1
        required = {"preference": ("hermes_preferenceKey", "hermes_preferenceValue"), "decision": ("hermes_decisionText",)}.get(kind, ())
        if any(key not in props or props[key] == "" for key in required):
            counts["missing_required_property"] += 1
        if ontology and entity_violations(ontology, kind, props):
            counts["published_entity_constraint"] += 1
    edges = store.run_read("""
        MATCH (a)-[r]->(b) WHERE a.uri STARTS WITH $prefix
            AND NOT a:KGLegacyAlias AND NOT a:KGLegacyUnresolved AND NOT a:KGQuarantined
            AND NOT b:KGLegacyAlias AND NOT b:KGLegacyUnresolved AND NOT b:KGQuarantined
        RETURN labels(a) AS source_labels, type(r) AS type, labels(b) AS target_labels
    """, {"prefix": store.config.urn_prefix})
    for row in edges:
        try:
            canonical = normalize_rel(row["type"], ontology)
        except ValueError:
            canonical = row['type']  # Preserve the existing unmapped-history check policy.
        expected = RELATION_ENDPOINTS.get(canonical)
        invalid = expected and (not set(row["source_labels"]) & expected[0] or not set(row["target_labels"]) & expected[1])
        source = next((kinds_by_label[label] for label in row['source_labels'] if label in kinds_by_label), None)
        target = next((kinds_by_label[label] for label in row['target_labels'] if label in kinds_by_label), None)
        if invalid or (ontology and relation_violations(ontology, canonical, source, target)):
            counts["invalid_relationship_endpoint_type"] += 1
    return [f"{key}: {count}" for key, count in sorted(counts.items())]


def main(argv=None):
    _kg_import.configure_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ontology", type=Path, default=ONTOLOGY_PATH)
    parser.add_argument("--graph", action="store_true")
    args = parser.parse_args(argv)
    errors = validate(args.ontology)
    if args.graph:
        errors.extend(validate_graph())
    if errors:
        parser.exit(1, "\n".join(errors) + "\n")
    print("KG ontology" + (" and graph" if args.graph else "") + " valid")


if __name__ == "__main__":
    main()
