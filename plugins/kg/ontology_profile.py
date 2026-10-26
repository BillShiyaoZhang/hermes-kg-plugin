"""Compile a bounded, offline personal ontology profile into runtime bindings.

Upstream vocabularies supply meaning, not database identifiers or executable
instructions. Only the explicitly selected application constraints are compiled.
No import resolver, network access, or runtime fact materialization is used.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from functools import lru_cache
import hashlib
from importlib import resources
import json
import math
from pathlib import Path
import re

from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection
from rdflib.compare import to_canonical_graph
from rdflib.namespace import OWL, RDF, RDFS, XSD

APP = Namespace('urn:hermes:personal:profile#')
PERSONAL = Namespace('urn:hermes:personal:')
SCHEMA = Namespace('https://schema.org/')
PROV = Namespace('http://www.w3.org/ns/prov#')
SKOS = Namespace('http://www.w3.org/2004/02/skos/core#')
SH = Namespace('http://www.w3.org/ns/shacl#')
COMPILER_REVISION = 'personal-profile-3'
COLLECTIONS = ('entity_types', 'properties', 'relations')
DATATYPES = {'string', 'integer', 'double', 'boolean', 'date', 'dateTime'}
MAPPINGS = {'reuse', 'projection', 'local', 'annotation'}
_APP_FIELDS = {'id', 'version', 'kind', 'key', 'install', 'source', 'mapping', 'term',
               'label', 'description', 'note', 'direction', 'datatype', 'domain',
               'range', 'requiredFor', 'provenanceKey'}
_ALLOWED_PREDICATES = {RDF.type, RDFS.subClassOf, RDFS.subPropertyOf,
    URIRef('http://purl.org/dc/terms/title'), URIRef('http://purl.org/dc/terms/license'),
    URIRef('http://purl.org/dc/terms/conformsTo')} | {APP[name] for name in _APP_FIELDS}
_ANCESTOR_PREDICATES = (RDFS.subClassOf, RDFS.subPropertyOf)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _root(root=None):
    if root is not None:
        return Path(root)
    packaged = resources.files(__package__).joinpath('resources', 'skills', 'kg-memory', 'ontology', 'personal')
    if packaged.joinpath('profile.ttl').is_file():
        return packaged
    sibling = Path(__file__).resolve().parents[2] / 'skills' / 'kg-memory' / 'ontology' / 'personal'
    if sibling.joinpath('profile.ttl').is_file():
        return sibling
    raise FileNotFoundError('The installed personal ontology profile is missing')


def _read(root, name):
    if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', name)
            or any(part in {'', '.', '..'} for part in name.split('/'))):
        raise ValueError('Unsafe ontology resource path')
    return root.joinpath(*name.split('/')).read_bytes()


def _single(graph, subject, predicate, *, default=None, required=False):
    values = list(graph.objects(subject, predicate))
    if len(values) > 1 or (required and not values):
        raise ValueError('Ontology profile fields must have one value: ' + str(predicate))
    return values[0] if values else default


def _text(graph, subject, predicate, *, default='', required=False):
    value = _single(graph, subject, predicate, default=Literal(default), required=required)
    if not isinstance(value, Literal) or not isinstance(value.toPython(), str):
        raise ValueError('Ontology profile text must be a string: ' + str(predicate))
    return str(value)


def _values(graph, subject, predicate):
    values = list(graph.objects(subject, predicate))
    if any(not isinstance(value, Literal) or not isinstance(value.toPython(), str) for value in values):
        raise ValueError('Runtime ontology scopes must contain type keys')
    return sorted({str(value) for value in values})


def _sources(root):
    lock = json.loads(_read(root, 'sources.lock.json'))
    if not isinstance(lock, dict) or lock.get('format') != 1 or not isinstance(lock.get('sources'), list):
        raise ValueError('Invalid ontology source lock')
    result, graphs = [], {}
    for entry in lock['sources']:
        if (not isinstance(entry, dict) or not {'id', 'version', 'iri', 'file', 'format', 'url', 'sha256', 'license', 'license_url'} <= entry.keys()
                or not all(isinstance(entry[field], str) and entry[field] for field in ('id', 'version', 'iri', 'file', 'format', 'url', 'sha256', 'license', 'license_url'))
                or entry['id'] in graphs or entry['format'] not in {'turtle', 'xml', 'nt'}
                or not re.fullmatch('[a-f0-9]{64}', entry['sha256'])):
            raise ValueError('Invalid or duplicate locked ontology source')
        content = _read(root, entry['file'])
        if _sha(content) != entry['sha256']:
            raise ValueError('Ontology source checksum mismatch: ' + entry['id'])
        if b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
            raise ValueError('External XML declarations are not supported')
        graphs[entry['id']] = Graph().parse(data=content, format=entry['format'])
        result.append(deepcopy(entry))
    if set(graphs) != {'schema', 'skos', 'prov', 'dcterms', 'dcmitype'}:
        raise ValueError('The personal profile needs its five locked source files')
    return sorted(result, key=lambda item: item['id']), graphs


def _selected_graph(profile, source_graphs, terms):
    """Keep only selected declarations and bounded inheritance/disjoint axioms."""
    upstream = Graph()
    for graph in source_graphs.values():
        upstream += graph
    selected = Graph()
    selected += profile
    pending, seen = list(terms), set()
    while pending:
        term = pending.pop()
        if term in seen:
            continue
        seen.add(term)
        for predicate in (RDF.type, RDFS.subClassOf, RDFS.subPropertyOf, OWL.disjointWith):
            for value in upstream.objects(term, predicate):
                if isinstance(value, BNode):
                    # A general OWL expression is not part of this compiler.
                    continue
                selected.add((term, predicate, value))
                if predicate in _ANCESTOR_PREDICATES or predicate == OWL.disjointWith:
                    pending.append(value)
    return selected


def _ancestors(graph, term, predicate=RDFS.subClassOf):
    result, pending = set(), [term]
    while pending:
        value = pending.pop()
        if value in result:
            continue
        result.add(value)
        pending.extend(parent for parent in graph.objects(value, predicate) if isinstance(parent, URIRef))
    return result


def _check_semantics(profile, selected):
    for subject, predicate, value in profile:
        if isinstance(subject, BNode) or isinstance(value, BNode) or predicate not in _ALLOWED_PREDICATES:
            raise ValueError('Unsupported ontology profile structure: ' + str(predicate))
        if predicate in _ANCESTOR_PREDICATES and not isinstance(value, URIRef):
            raise ValueError('Inheritance needs a named class or property')
    for predicate in _ANCESTOR_PREDICATES:
        for subject in set(profile.subjects(predicate=predicate)):
            if any(subject in _ancestors(selected, parent, predicate) for parent in selected.objects(subject, predicate)):
                raise ValueError('Cyclic application inheritance: ' + str(subject))
    for subject in set(profile.subjects(RDFS.subClassOf, None)):
        parents = _ancestors(selected, subject)
        for left in parents:
            if any(right in parents for right in selected.objects(left, OWL.disjointWith)):
                raise ValueError('Incompatible inherited classes: ' + str(subject))
    if URIRef('https://schema.org/Project') in _ancestors(selected, PERSONAL.Project):
        raise ValueError('A task collection must not inherit Schema.org Project/Organization')
    if SKOS.Concept in _ancestors(selected, PERSONAL.LegacyConcept):
        raise ValueError('Legacy Concept cannot be globally reclassified as SKOS Concept')
    for term in (PERSONAL.Project, PERSONAL.Task, PERSONAL.Goal, PERSONAL.Skill, PERSONAL.Habit):
        if PROV.Entity in _ancestors(selected, term) or PROV.Activity in _ancestors(selected, term):
            raise ValueError('Business objects and provenance records must remain separate')


def _scope_groups(graph, term, field):
    """Return OR groups whose combination is AND, preserving source semantics."""
    result = []
    for parent in _ancestors(graph, term, RDFS.subPropertyOf):
        for predicate in (SCHEMA[field + 'Includes'], URIRef('http://purl.org/dc/dcam/' + field + 'Includes')):
            values = list(graph.objects(parent, predicate))
            if values:
                if any(not isinstance(value, URIRef) for value in values):
                    raise ValueError('Unsupported external candidate scope')
                result.append(values)  # Schema/DCAM candidates mean OR.
        for value in graph.objects(parent, RDFS[field]):
            if isinstance(value, URIRef):
                result.append([value])  # Multiple RDFS assertions mean AND.
            elif isinstance(value, BNode) and graph.value(value, OWL.unionOf) is not None:
                members = list(Collection(graph, graph.value(value, OWL.unionOf)))
                if not members or any(not isinstance(member, URIRef) for member in members):
                    raise ValueError('Unsupported external union scope')
                result.append(members)
            else:
                raise ValueError('Unsupported external domain/range expression')
    return result


def _check_source_compatibility(manifest, source_graphs, selected):
    """Check each local narrowing against its authoritative public term."""
    from .ontology import _schema
    core = _schema()
    originals = {(kind, item['key']): item for kind in COLLECTIONS for item in core[kind]}
    operations = {(item['kind'], item['key']): item['definition'] for item in manifest['operations']}
    types = manifest['bindings']['entity_types']
    combined = Graph()
    combined += selected
    for source in source_graphs.values():
        combined += source
    wire_types = {
        'string': {XSD.string, XSD.anyURI, SCHEMA.Text, SCHEMA.URL, RDFS.Literal},
        'integer': {XSD.integer, SCHEMA.Integer, SCHEMA.Number, RDFS.Literal},
        'double': {XSD.double, XSD.decimal, SCHEMA.Float, SCHEMA.Number, RDFS.Literal},
        'boolean': {XSD.boolean, SCHEMA.Boolean, RDFS.Literal},
        'date': {XSD.date, SCHEMA.Date, RDFS.Literal},
        'dateTime': {XSD.dateTime, SCHEMA.DateTime, RDFS.Literal},
    }
    def fits(kind, expected):
        # Schema's Thing is explicitly universal. It can describe a SKOS topic
        # or DCMI physical object without asserting a global cross-vocabulary
        # equivalentClass merely to satisfy this bounded application check.
        return expected in {SCHEMA.Thing, RDFS.Resource} or expected in _ancestors(selected, URIRef(types[kind]['iri']))
    for kind in ('properties', 'relations'):
        for key, binding in manifest['bindings'][kind].items():
            if binding['mapping'] == 'annotation':
                continue
            definition = operations.get((kind, key), originals.get((kind, key)))
            if definition is None:
                raise ValueError('A semantic binding needs a runtime definition')
            domains = list(types) if 'any' in definition.get('domain', []) else definition.get('domain', [])
            ranges = definition.get('range', [])
            if kind == 'relations' and binding['direction'] == 'reverse':
                domains, ranges = ranges, domains
            term = URIRef(binding['iri'])
            # This also checks local subproperties against inherited public
            # constraints; a local name cannot bypass the public parent meaning.
            for group in _scope_groups(combined, term, 'domain'):
                if any(not any(fits(value, expected) for expected in group) for value in domains):
                    raise ValueError('Application domain conflicts with public term: ' + key)
            for group in _scope_groups(combined, term, 'range'):
                if kind == 'relations':
                    compatible = all(any(fits(value, expected) for expected in group) for value in ranges)
                else:
                    compatible = len(ranges) == 1 and bool(wire_types.get(ranges[0], set()) & set(group))
                if not compatible:
                    raise ValueError('Application range/datatype conflicts with public term: ' + key)


def _core_contract(bindings):
    """Derive the existing mandatory fields from the authoritative runtime."""
    from .schema import ENTITY_REQUIRED_PROPERTIES, EVENT_TYPES
    from .ontology import _schema
    existing = {item['key']: item for item in _schema()['properties']}
    keys = set().union(*(set(values) for values in ENTITY_REQUIRED_PROPERTIES.values()))
    keys |= {'hermes_eventEndTime', 'hermes_timePrecision', 'hermes_eventStatus', 'hermes_eventType'}
    properties = {}
    for key in sorted(keys):
        definition = existing.get(key, {})
        datatype = definition.get('range', ['dateTime' if key == 'hermes_eventEndTime' else 'string'])[0]
        properties[key] = {'iri': bindings['properties'].get(key, {}).get('iri', 'urn:hermes:personal:storage-property:' + key),
                           'datatype': datatype}
    return {'required_properties': {kind: list(fields) for kind, fields in sorted(ENTITY_REQUIRED_PROPERTIES.items())},
            'properties': properties, 'event_types': sorted(EVENT_TYPES),
            'event_precisions': existing['hermes_timePrecision']['allowed_values']}


def _stable_turtle(graph):
    # Canonical, sorted N-Triples is also valid Turtle; unlike serializer output,
    # it is independent of hash seeds and blank-node allocation order.
    canonical = to_canonical_graph(graph)
    return '# Generated; edit profile.ttl, not this file.\n' + ''.join(
        sorted(' '.join(term.n3() for term in triple) + ' .\n' for triple in canonical))


def _or_classes(graph, node, classes, tag):
    if len(classes) == 1:
        graph.add((node, SH['class'], URIRef(classes[0])))
    else:
        members = []
        for index, term in enumerate(sorted(classes)):
            choice = BNode(tag + '-choice-' + str(index))
            graph.add((choice, SH['class'], URIRef(term)))
            members.append(choice)
        head = BNode(tag + '-or')
        Collection(graph, head, members)
        graph.add((node, SH['or'], head))


def _core_shapes(graph, manifest, markers):
    """Compile core required fields plus the existing explicit unknown-time case."""
    contract = manifest['core_contract']
    paths = {key: URIRef(value['iri']) for key, value in contract['properties'].items()}
    def field(shape, key, tag, *, minimum=None, maximum=1, value=None):
        constraint = BNode('core-' + tag)
        graph.add((shape, SH.property, constraint))
        graph.add((constraint, SH.path, paths[key]))
        if maximum is not None:
            graph.add((constraint, SH.maxCount, Literal(maximum)))
        if minimum is not None:
            graph.add((constraint, SH.minCount, Literal(minimum)))
        if value is not None:
            graph.add((constraint, SH.hasValue, Literal(value)))
        return constraint
    for kind, required in contract['required_properties'].items():
        if kind == 'event':
            continue
        shape = URIRef('urn:hermes:personal:shapes:core:' + kind)
        graph.add((shape, RDF.type, SH.NodeShape))
        graph.add((shape, SH.targetClass, URIRef(markers[kind])))
        for key in required:
            constraint = field(shape, key, kind + '-' + key, minimum=1)
            datatype = contract['properties'][key]['datatype']
            graph.add((constraint, SH.datatype, XSD[datatype]))
            if datatype == 'string':
                graph.add((constraint, SH.minLength, Literal(1)))
    shape = URIRef('urn:hermes:personal:shapes:core:event')
    graph.add((shape, RDF.type, SH.NodeShape))
    graph.add((shape, SH.targetClass, URIRef(markers['event'])))
    for key in ('hermes_eventTime', 'hermes_eventEndTime', 'hermes_timePrecision', 'hermes_eventStatus', 'hermes_eventType'):
        constraint = field(shape, key, 'event-' + key)
        graph.add((constraint, SH.datatype, XSD[contract['properties'][key]['datatype']]))
        values = contract['event_precisions'] if key == 'hermes_timePrecision' else contract['event_types'] if key == 'hermes_eventType' else None
        if values:
            head = BNode('core-values-' + key)
            Collection(graph, head, [Literal(value) for value in values])
            graph.add((constraint, SH['in'], head))
        if key == 'hermes_eventTime':
            graph.add((constraint, SH.lessThan, paths['hermes_eventEndTime']))
    known, unknown = BNode('core-event-known'), BNode('core-event-unknown')
    field(known, 'hermes_eventTime', 'event-known-start', minimum=1)
    precision_unknown = BNode('core-precision-is-unknown')
    field(precision_unknown, 'hermes_timePrecision', 'event-known-not-unknown', value='unknown')
    graph.add((known, SH['not'], precision_unknown))
    field(unknown, 'hermes_timePrecision', 'event-unknown-precision', value='unknown')
    field(unknown, 'hermes_eventStatus', 'event-unknown-status', value='occurred')
    field(unknown, 'hermes_eventTime', 'event-unknown-no-start', maximum=0)
    field(unknown, 'hermes_eventEndTime', 'event-unknown-no-end', maximum=0)
    head = BNode('core-event-timing-alternatives')
    Collection(graph, head, [known, unknown])
    graph.add((shape, SH['or'], head))


def _shapes(manifest):
    graph = Graph()
    bindings = manifest['bindings']
    types = {key: entry['iri'] for key, entry in bindings['entity_types'].items()}
    markers = {key: 'urn:hermes:personal:runtime-type:' + key for key in types}
    operations = manifest['operations']
    for key, term in sorted(types.items()):
        shape = URIRef('urn:hermes:personal:shapes:type:' + key)
        graph.add((shape, RDF.type, SH.NodeShape))
        graph.add((shape, SH.targetClass, URIRef(markers[key])))
        name = BNode('name-' + key)
        graph.add((shape, SH.property, name))
        graph.add((name, SH.path, SCHEMA.name))
        graph.add((name, SH.datatype, XSD.string))
        graph.add((name, SH.minCount, Literal(1)))
        graph.add((name, SH.maxCount, Literal(1)))
        graph.add((name, SH.minLength, Literal(1)))
        graph.add((name, SH.pattern, Literal(r'\S')))
    _core_shapes(graph, manifest, markers)
    for item in operations:
        kind, key, definition = item['kind'], item['key'], item['definition']
        if kind == 'entity_types':
            continue
        binding = bindings[kind][key]
        term = URIRef(binding['iri'])
        shape = URIRef('urn:hermes:personal:shapes:' + kind + ':' + key)
        graph.add((shape, RDF.type, SH.NodeShape))
        domains, ranges = definition['domain'], definition['range']
        if kind == 'relations' and binding['direction'] == 'reverse':
            domains, ranges = ranges, domains
        # Runtime markers restrict the application, never the global meaning of
        # a public property. A PROV record may legitimately use schema:about to
        # describe an Event even though this app's ABOUT edge targets a Topic.
        for domain in domains:
            graph.add((shape, SH.targetClass, URIRef(markers[domain])))
        constraint = BNode(key + '-value')
        graph.add((shape, SH.property, constraint))
        graph.add((constraint, SH.path, term))
        if kind == 'properties':
            graph.add((constraint, SH.datatype, XSD[ranges[0]]))
            graph.add((constraint, SH.maxCount, Literal(1)))
            if definition.get('required_for'):
                required = URIRef(str(shape) + ':required')
                graph.add((required, RDF.type, SH.NodeShape))
                for required_type in definition['required_for']:
                    graph.add((required, SH.targetClass, URIRef(markers[required_type])))
                required_value = BNode(key + '-required')
                graph.add((required, SH.property, required_value))
                graph.add((required_value, SH.path, term))
                graph.add((required_value, SH.minCount, Literal(1)))
                if ranges == ['string']:
                    graph.add((required_value, SH.minLength, Literal(1)))
                    graph.add((required_value, SH.pattern, Literal(r'\S')))
        else:
            graph.add((constraint, SH.nodeKind, SH.IRI))
            _or_classes(graph, constraint, [markers[value] for value in ranges], key + '-range')
        forbidden = URIRef(str(shape) + ':outside-domain')
        graph.add((forbidden, RDF.type, SH.NodeShape))
        for domain in sorted(set(types) - set(domains)):
            graph.add((forbidden, SH.targetClass, URIRef(markers[domain])))
        absent = BNode(key + '-outside-domain')
        graph.add((forbidden, SH.property, absent))
        graph.add((absent, SH.path, term))
        graph.add((absent, SH.maxCount, Literal(0)))
    # Record nodes are never the real-world entity URI. These constraints also
    # reject a node accidentally used as both source entity and extraction.
    for name in ('record_class', 'source_class', 'snapshot_class', 'activity_class'):
        shape = URIRef('urn:hermes:personal:shapes:provenance:' + name)
        graph.add((shape, RDF.type, SH.NodeShape))
        graph.add((shape, SH.targetClass, URIRef(manifest['provenance'][name])))
        exclusion = BNode('provenance-not-' + name)
        graph.add((shape, SH['not'], exclusion))
        forbidden = PROV.Entity if name == 'activity_class' else PROV.Activity
        graph.add((exclusion, SH['class'], forbidden))
    return graph


def build_personal_artifacts(root=None, *, profile_text=None):
    """Return deterministic manifest, SHACL and selected semantic graph text."""
    root = _root(root)
    sources, source_graphs = _sources(root)
    raw = _read(root, 'profile.ttl') if profile_text is None else profile_text.encode('utf-8')
    graph = Graph().parse(data=raw, format='turtle')
    roots = list(graph.subjects(RDF.type, OWL.Ontology))
    if len(roots) != 1:
        raise ValueError('The application profile needs exactly one ontology declaration')
    subject = roots[0]
    identifier = _text(graph, subject, APP.id, required=True)
    version = _text(graph, subject, APP.version, required=True)
    if identifier != 'personal' or not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Invalid personal ontology profile identity')
    source_map = {entry['id']: entry for entry in sources}
    bindings = {kind: {} for kind in COLLECTIONS}
    definitions, selected_terms, operations, installed_terms = {}, set(), [], set()
    from .schema import ENTITY_LABELS, PROPERTY_ALIASES, RELATION_ALIASES
    existing = {'entity_types': set(ENTITY_LABELS),
        'properties': set(PROPERTY_ALIASES.values()) | {'hermes_name', 'foaf_name'},
        'relations': set(RELATION_ALIASES.values())}
    for term in sorted(graph.subjects(APP.key, None), key=str):
        key = _text(graph, term, APP.key, required=True)
        kind = _text(graph, term, APP.kind, required=True)
        source = _text(graph, term, APP.source, required=True)
        mapping = _text(graph, term, APP.mapping, required=True)
        direction = _text(graph, term, APP.direction, default='forward')
        canonical = _single(graph, term, APP['term'], default=term)
        install = _single(graph, term, APP.install, default=Literal(True))
        if (kind not in COLLECTIONS or key in bindings[kind] or mapping not in MAPPINGS
                or direction not in {'forward', 'reverse'} or not isinstance(canonical, URIRef)
                or not isinstance(install, Literal) or type(install.toPython()) is not bool):
            raise ValueError('Invalid or duplicate personal ontology binding')
        if source == 'personal':
            if mapping != 'local' or not str(canonical).startswith(str(PERSONAL)) or not list(graph.objects(canonical, RDF.type)):
                raise ValueError('Local bindings need an explicitly declared local term')
            expected_types = {OWL.Class, RDFS.Class} if kind == 'entity_types' else {RDF.Property, OWL.ObjectProperty, OWL.DatatypeProperty}
            if not set(graph.objects(canonical, RDF.type)) & expected_types:
                raise ValueError('The local term has an incompatible declaration kind: ' + str(canonical))
            source_version = version
        elif source in source_map:
            if not list(source_graphs[source].predicate_objects(canonical)):
                raise ValueError('Unknown term in locked ontology source: ' + str(canonical))
            declared_types = set(source_graphs[source].objects(canonical, RDF.type))
            expected_types = {OWL.Class, RDFS.Class} if kind == 'entity_types' else {RDF.Property, OWL.ObjectProperty, OWL.DatatypeProperty}
            if not declared_types & expected_types:
                raise ValueError('The source term has an incompatible declaration kind: ' + str(canonical))
            source_version = source_map[source]['version']
            selected_terms.add(canonical)
        else:
            raise ValueError('Unknown ontology source: ' + source)
        bindings[kind][key] = {'iri': str(canonical), 'mapping': mapping, 'source': source,
            'source_version': source_version, 'direction': direction,
            'note': _text(graph, term, APP.note, default=_text(graph, term, APP.description))}
        if not install.toPython():
            if key not in existing[kind]:
                raise ValueError('A non-installed binding must reference an existing runtime identity')
            continue
        pattern = r'X_PERSONAL_[A-Z][A-Z0-9_]{0,39}' if kind == 'relations' else r'x_personal_[a-z][a-z0-9_]{0,38}'
        if not re.fullmatch(pattern, key) or key in existing[kind]:
            raise ValueError('Invalid personal ontology storage key: ' + key)
        if (kind, canonical) in installed_terms:
            raise ValueError('Two installed definitions cannot claim the same semantic term')
        installed_terms.add((kind, canonical))
        label = _text(graph, term, APP.label, required=True)
        description = _text(graph, term, APP.description, default=_text(graph, term, APP.note))
        if not label.strip() or len(label) > 120 or len(description) > 4000:
            raise ValueError('Invalid ontology display text')
        definition = {'label': label, 'description': description}
        if kind != 'entity_types':
            definition['domain'] = _values(graph, term, APP.domain)
            definition['range'] = (_values(graph, term, APP.range) if kind == 'relations'
                                   else [_text(graph, term, APP.datatype, required=True)])
            definition['aliases'] = []
            if kind == 'properties':
                definition['required_for'] = _values(graph, term, APP.requiredFor)
                definition['value_shape'] = 'scalar'
                if definition['range'][0] not in DATATYPES:
                    raise ValueError('Unsupported scalar ontology datatype')
        definitions[(kind, key)] = definition
        operations.append({'action': 'create', 'kind': kind, 'key': key, 'definition': definition})
    if not set(ENTITY_LABELS) <= set(bindings['entity_types']):
        raise ValueError('The profile must account for all existing business types')
    type_keys = set(bindings['entity_types'])
    for (kind, key), definition in definitions.items():
        if kind == 'entity_types':
            continue
        for field in ('domain', 'range') if kind == 'relations' else ('domain',):
            if not definition[field] or not set(definition[field]) <= type_keys:
                raise ValueError('Unknown or unrestricted application type scope: ' + key)
        if kind == 'properties' and not set(definition['required_for']) <= set(definition['domain']):
            raise ValueError('Required fields must be applicable to their types')
    provenance = {}
    for term in sorted(graph.subjects(APP.provenanceKey, None), key=str):
        key = _text(graph, term, APP.provenanceKey, required=True)
        if key in provenance:
            raise ValueError('Duplicate provenance binding')
        provenance[key] = str(term)
        if not str(term).startswith(str(PERSONAL)):
            if not any(list(item.predicate_objects(term)) for item in source_graphs.values()):
                raise ValueError('Unknown provenance term: ' + str(term))
            selected_terms.add(term)
    for parent in graph.objects(None, RDFS.subClassOf):
        if not str(parent).startswith(str(PERSONAL)):
            if not any(list(item.predicate_objects(parent)) for item in source_graphs.values()):
                raise ValueError('Unknown external parent class: ' + str(parent))
            selected_terms.add(parent)
    for parent in graph.objects(None, RDFS.subPropertyOf):
        if not any(list(item.predicate_objects(parent)) for item in source_graphs.values()):
            raise ValueError('Unknown external parent property: ' + str(parent))
        selected_terms.add(parent)
    selected = _selected_graph(graph, source_graphs, selected_terms)
    _check_semantics(graph, selected)
    core_contract = _core_contract(bindings)
    digest = _sha(_json({'compiler': COMPILER_REVISION, 'profile': _sha(raw),
                        'sources': sources, 'core_contract': core_contract}).encode('utf-8'))
    order = {name: index for index, name in enumerate(COLLECTIONS)}
    operations.sort(key=lambda item: (order[item['kind']], item['key']))
    manifest = {'id': identifier, 'version': version, 'digest': digest,
                'compiler': COMPILER_REVISION, 'sources': sources, 'operations': operations,
                'bindings': {kind: dict(sorted(values.items())) for kind, values in bindings.items()},
                'provenance': dict(sorted(provenance.items())), 'core_contract': core_contract}
    _check_source_compatibility(manifest, source_graphs, selected)
    return {'manifest': manifest, 'shapes': _stable_turtle(_shapes(manifest)),
            'selected': _stable_turtle(selected)}


def compile_personal_profile(root=None, *, profile_text=None):
    return build_personal_artifacts(root, profile_text=profile_text)['manifest']


@lru_cache(maxsize=1)
def _installed_profile():
    root = _root()
    artifacts = build_personal_artifacts(root)
    generated = json.loads(_read(root, 'manifest.json'))
    if generated != artifacts['manifest']:
        raise ValueError('Generated personal ontology manifest is stale; rebuild the profile')
    for name, key in (('shapes.ttl', 'shapes'), ('selected.ttl', 'selected')):
        if _read(root, name).decode('utf-8') != artifacts[key]:
            raise ValueError('Generated personal ontology artifact is stale: ' + name)
    return generated


def load_personal_profile():
    """Return an independent JSON-compatible, locally verified install bundle."""
    return deepcopy(_installed_profile())


def _projection_literal(value, datatype, timezone='Asia/Shanghai'):
    """Normalize allowed numeric/temporal representations without coercing text.

    A string containing digits must not pass a numeric constraint just because
    an RDF literal was forcibly assigned a numeric datatype.
    """
    if datatype in {XSD.double, XSD.integer}:
        numeric = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        if datatype == XSD.double and numeric:
            return Literal(float(value), datatype=XSD.double)
        if datatype == XSD.integer and numeric and int(value) == value:
            return Literal(int(value), datatype=XSD.integer)
        return Literal(value)
    if datatype == XSD.date and (isinstance(value, str) or isinstance(value, date) and not isinstance(value, datetime)):
        return Literal(value, datatype=XSD.date)
    if datatype == XSD.dateTime and isinstance(value, (str, datetime)):
        from .schema import parse_time
        try:
            return Literal(parse_time(value, timezone), datatype=XSD.dateTime)
        except ValueError:
            return Literal(value)
    return Literal(value)


def project_personal_entities(entities, relations=(), *, manifest=None, timezone='Asia/Shanghai'):
    """Create a temporary semantic graph; never mutate input or saved facts."""
    manifest = manifest or load_personal_profile()
    bindings = manifest['bindings']
    graph = Graph()
    known = {}
    from .schema import PROPERTY_ALIASES
    for entity in entities:
        kind = entity.get('type', '')
        binding = bindings['entity_types'].get(kind)
        uri = entity.get('uri') or entity.get('urn') or entity.get('id')
        if binding is None or not isinstance(uri, str) or not uri.startswith('urn:'):
            raise ValueError('Semantic projection needs a registered type and canonical URN')
        node = URIRef(uri)
        known[uri] = kind
        graph.add((node, RDF.type, URIRef(binding['iri'])))
        graph.add((node, RDF.type, URIRef('urn:hermes:personal:runtime-type:' + kind)))
        props = entity.get('properties', entity.get('props', {}))
        if not isinstance(props, dict):
            raise ValueError('Semantic projection properties must be an object')
        name = entity.get('name', props.get('foaf_name' if kind == 'person' else 'hermes_name'))
        if name is not None:
            graph.add((node, SCHEMA.name, Literal(name)))
            if kind == 'x_personal_topic':
                graph.add((node, SKOS.prefLabel, Literal(name)))
        for raw_key, value in props.items():
            if not isinstance(raw_key, str):
                raise ValueError('Semantic property keys must be strings')
            key = PROPERTY_ALIASES.get(raw_key, raw_key.replace(':', '_'))
            if value is None:
                continue
            if key in {'foaf_name', 'hermes_name'}:
                continue
            field = bindings['properties'].get(key)
            core_field = manifest.get('core_contract', {}).get('properties', {}).get(key)
            if field is None and core_field:
                field = core_field
            if field is None:
                if key.startswith('x_personal_'):
                    raise ValueError('Unknown personal field in semantic projection: ' + key)
                continue
            operation = next((item for item in manifest['operations'] if item['kind'] == 'properties' and item['key'] == key), None)
            if (operation is not None or core_field is not None) and isinstance(value, (list, dict)):
                raise ValueError('Personal ontology properties must be scalar')
            datatype = XSD[operation['definition']['range'][0]] if operation else XSD[core_field['datatype']] if core_field else None
            if key == 'hermes_preferenceValue' and isinstance(value, (bool, int, float)):
                value = json.dumps(value, allow_nan=False)
            values = value if isinstance(value, list) else [value]
            for item in values:
                graph.add((node, URIRef(field['iri']), _projection_literal(item, datatype, timezone)))
    for relation in relations:
        key = relation.get('type', relation.get('relation_type'))
        binding = bindings['relations'].get(key)
        if binding is None:
            raise ValueError('Unregistered semantic relationship')
        if binding['mapping'] == 'annotation':
            # A related reference is not a license to assert the public edge.
            continue
        left = relation.get('from', relation.get('from_urn'))
        right = relation.get('to', relation.get('to_urn'))
        if left not in known or right not in known:
            raise ValueError('Semantic relationship endpoints need explicit typed entities')
        if binding['direction'] == 'reverse':
            left, right = right, left
        graph.add((URIRef(left), URIRef(binding['iri']), URIRef(right)))
    return graph


def project_provenance(records, *, manifest=None):
    """Project record/source/extraction URIs, separate from business entity URIs."""
    manifest = manifest or load_personal_profile()
    terms = manifest['provenance']
    graph = Graph()
    for record in records:
        if not isinstance(record, dict) or not all(isinstance(record.get(key), str) and record[key].startswith('urn:') for key in ('uri', 'source_uri')):
            raise ValueError('Provenance requires canonical record and source URIs')
        record_uri, source_uri = URIRef(record['uri']), URIRef(record['source_uri'])
        if record_uri == source_uri or record.get('entity_uri') in {str(record_uri), str(source_uri)}:
            raise ValueError('A memory record must be distinct from its source and described entity')
        graph.add((record_uri, RDF.type, URIRef(terms['record_class'])))
        graph.add((source_uri, RDF.type, URIRef(terms['source_class'])))
        graph.add((record_uri, URIRef(terms['derived_from']), source_uri))
        if record.get('entity_uri'):
            graph.add((record_uri, SCHEMA.about, URIRef(record['entity_uri'])))
        if record.get('activity_uri'):
            activity = URIRef(record['activity_uri'])
            if str(activity) in {str(record_uri), str(source_uri), record.get('entity_uri')}:
                raise ValueError('Extraction activity must be distinct from records and real-world entities')
            graph.add((activity, RDF.type, URIRef(terms['activity_class'])))
            graph.add((record_uri, URIRef(terms['generated_by']), activity))
    return graph


def validate_personal_graph(data_graph, *, root=None):
    """Run actual SHACL offline in development; do not expose a write API."""
    try:
        from pyshacl import validate
    except ImportError as exc:
        raise RuntimeError('Offline ontology validation requires the ontology/test dependencies (pyshacl)') from exc
    artifacts = build_personal_artifacts(root)
    shapes = Graph().parse(data=artifacts['shapes'], format='turtle')
    semantic = Graph().parse(data=artifacts['selected'], format='turtle')
    # pySHACL uses only these supplied graphs. Import fetching is explicitly off;
    # inference stays in its temporary validation graph and is never persisted.
    conforms, report, text = validate(data_graph, shacl_graph=shapes, ont_graph=semantic,
        inference='rdfs', do_owl_imports=False, inplace=False, advanced=False)
    conflicts = check_personal_semantics(data_graph, semantic_graph=semantic)
    return {'conforms': bool(conforms) and not conflicts,
            'report': str(text) + ''.join('\nSemantic conflict: ' + entry for entry in conflicts),
            'results': len(list(report.subjects(RDF.type, SH.ValidationResult))) + len(conflicts)}


def check_personal_semantics(data_graph, *, semantic_graph=None, root=None):
    """Check selected disjointness/SKOS integrity, not arbitrary OWL reasoning.

    SHACL checks the bounded runtime-compatible application constraints. This
    additional offline pass checks vocabulary integrity conditions; it returns
    diagnostics and never modifies the input graph or stored facts.
    """
    if semantic_graph is None:
        semantic_graph = Graph().parse(data=build_personal_artifacts(root)['selected'], format='turtle')
    conflicts = []
    for node in sorted(set(data_graph.subjects(RDF.type, None)), key=str):
        classes = set().union(*(_ancestors(semantic_graph, term) for term in data_graph.objects(node, RDF.type)))
        for left in sorted(classes, key=str):
            for right in semantic_graph.objects(left, OWL.disjointWith):
                if right in classes:
                    conflicts.append('disjoint_classes:' + str(node) + ':' + str(left) + ':' + str(right))
        if SKOS.Concept in classes:
            seen = set()
            for label in data_graph.objects(node, SKOS.prefLabel):
                language = label.language if isinstance(label, Literal) else None
                if language in seen:
                    conflicts.append('duplicate_preferred_label:' + str(node))
                seen.add(language)
            preferred = set(data_graph.objects(node, SKOS.prefLabel))
            if preferred & (set(data_graph.objects(node, SKOS.altLabel)) | set(data_graph.objects(node, SKOS.hiddenLabel))):
                conflicts.append('incompatible_concept_labels:' + str(node))
    for left, right in data_graph.subject_objects(SKOS.related):
        broader = _ancestors(data_graph, left, SKOS.broader) - {left}
        narrower = _ancestors(data_graph, right, SKOS.broader) - {right}
        if right in broader or left in narrower:
            conflicts.append('related_and_hierarchical:' + str(left) + ':' + str(right))
    return sorted(set(conflicts))
