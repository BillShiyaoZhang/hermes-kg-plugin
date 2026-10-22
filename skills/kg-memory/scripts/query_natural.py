"""Natural-language KG queries and RDF-backed schema context."""
import argparse
import json
from pathlib import Path
import _kg_import
from kg.queries import parse_natural_query, execute_kg_query, query_natural

ONTOLOGY_PATH = Path(__file__).resolve().parents[1] / "ontology" / "core.ttl"


def load_schema_from_ontology(path=ONTOLOGY_PATH):
    from rdflib import Graph
    from rdflib.namespace import OWL, RDF, RDFS
    graph = Graph().parse(str(path), format="turtle")
    classes, properties = {}, {}
    for subject in graph.subjects(RDF.type, OWL.Class):
        classes[str(subject)] = {"comment": str(graph.value(subject, RDFS.comment) or ""), "properties": []}
    for property_type in (OWL.DatatypeProperty, OWL.ObjectProperty):
        for subject in graph.subjects(RDF.type, property_type):
            domains = [str(value) for value in graph.objects(subject, RDFS.domain)]
            properties[str(subject)] = {"comment": str(graph.value(subject, RDFS.comment) or ""), "domain": domains, "range": [str(value) for value in graph.objects(subject, RDFS.range)]}
            for domain in domains:
                if domain in classes:
                    classes[domain]["properties"].append(str(subject))
    return {"classes": classes, "properties": properties}


def main(argv=None):
    _kg_import.configure_output()
    parser = argparse.ArgumentParser(description="Query KG using common Chinese/English questions")
    parser.add_argument("question", nargs="+")
    parser.add_argument("--schema", action="store_true", help="Include parsed ontology definitions")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    result = query_natural(" ".join(args.question), limit=args.limit)
    if args.schema:
        result["schema_context"] = load_schema_from_ontology()
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
