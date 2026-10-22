"""Extend an ontology with RDF serialization, semantic versioning and atomic writes."""
import argparse
import os
import re
from pathlib import Path
from uuid import uuid4
import _kg_import

ONTOLOGY_PATH = Path(__file__).resolve().parents[1] / "ontology" / "core.ttl"


def _term(value):
    from rdflib import Namespace, URIRef
    namespaces = {"hermesont": "urn:hermes:ontology:", "xsd": "http://www.w3.org/2001/XMLSchema#", "owl": "http://www.w3.org/2002/07/owl#", "foaf": "http://xmlns.com/foaf/0.1/"}
    if ":" not in value:
        value = "hermesont:" + value
    prefix, local = value.split(":", 1)
    if prefix not in namespaces or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", local):
        raise ValueError("Use an unprefixed identifier or a known hermesont/xsd/owl/foaf QName")
    return URIRef(namespaces[prefix] + local)


def _draft(name, description, property_type=None, domain="owl:Thing", range_="xsd:string"):
    from rdflib import Graph, Literal, Namespace
    from rdflib.namespace import OWL, RDF, RDFS
    graph = Graph()
    subject = _term(name)
    if not str(subject).startswith("urn:hermes:ontology:"):
        raise ValueError("New terms belong in hermesont")
    kind = OWL.Class if property_type is None else OWL.ObjectProperty if property_type == "ObjectProperty" else OWL.DatatypeProperty
    graph.add((subject, RDF.type, kind))
    graph.add((subject, RDFS.label, Literal(name.split(":")[-1])))
    graph.add((subject, RDFS.comment, Literal(description)))
    if kind != OWL.Class:
        graph.add((subject, RDFS.domain, _term(domain)))
        graph.add((subject, RDFS.range, _term(range_)))
    return graph.serialize(format="turtle")


def input_class():
    return _draft(input("Class name: ").strip(), input("Description: ").strip())


def input_property():
    name = input("Property name: ").strip()
    kind = input("Type [DatatypeProperty / ObjectProperty]: ").strip()
    if kind not in {"DatatypeProperty", "ObjectProperty"}:
        raise ValueError("Unsupported property type")
    domain = input("Domain [Person or hermesont:Person]: ").strip()
    range_ = input("Range [xsd:string or hermesont:Person]: ").strip()
    description = input("Description: ").strip()
    return _draft(name, description, kind, domain, range_)


def bump_version(content):
    from rdflib import Graph, Literal, URIRef
    from rdflib.namespace import OWL, RDF
    graph = Graph().parse(data=content, format="turtle")
    ontologies = list(graph.subjects(RDF.type, OWL.Ontology))
    if len(ontologies) != 1:
        raise ValueError("Exactly one ontology is required")
    subject = ontologies[0]
    current = str(graph.value(subject, OWL.versionInfo) or "0.0.0")
    match = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?", current)
    if not match:
        raise ValueError("Ontology version must be semantic version numbers")
    version = f"{int(match[1])}.{int(match[2]) + 1}.0"
    graph.set((subject, OWL.versionInfo, Literal(version)))
    graph.set((subject, OWL.versionIRI, URIRef(str(subject) + ":v" + version)))
    return graph.serialize(format="turtle")


def append_draft(draft, path=ONTOLOGY_PATH):
    from rdflib import Graph
    path = Path(path)
    original = Graph().parse(str(path), format="turtle")
    additions = Graph().parse(data=draft, format="turtle")
    existing_subjects = set(original.subjects())
    if existing_subjects & set(additions.subjects()):
        raise ValueError("Term already exists; edit its existing definition explicitly")
    updated = original + additions
    content = bump_version(updated.serialize(format="turtle"))
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        from validate import validate
        errors = validate(temporary)
        if errors:
            raise ValueError("; ".join(errors))
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def add_concept(interactive=True):
    if not interactive:
        return main()
    kind = input("Add a [C]lass or [P]roperty? ").strip().upper()
    if kind not in {"C", "P"}:
        raise ValueError("Choose C or P")
    draft = input_property() if kind == "P" else input_class()
    print(draft)
    if input("Write this draft? [y/N]: ").strip().lower() == "y":
        append_draft(draft)


def main(argv=None):
    _kg_import.configure_output()
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--class", dest="class_name")
    group.add_argument("--property", dest="property_name")
    parser.add_argument("--desc", default="")
    parser.add_argument("--property-type", choices=["DatatypeProperty", "ObjectProperty"], default="DatatypeProperty")
    parser.add_argument("--domain", default="owl:Thing")
    parser.add_argument("--range", dest="range_", default="xsd:string")
    parser.add_argument("--ontology", type=Path, default=ONTOLOGY_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.class_name and not args.property_name:
        return add_concept(True)
    draft = _draft(args.class_name or args.property_name, args.desc, args.property_type if args.property_name else None, args.domain, args.range_)
    if args.dry_run:
        print(draft)
    else:
        append_draft(draft, args.ontology)
        print("Ontology extension validated and written")


if __name__ == "__main__":
    main()
