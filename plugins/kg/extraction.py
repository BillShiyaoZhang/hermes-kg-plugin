"""Shared strict extraction validation; independent of Hermes and model SDKs."""
from __future__ import annotations
import json
import math

MAX_OUTPUT_CHARS = 100_000


def read_current_ontology(config):
    """Read the selected namespace on every use; unavailable state never falls back."""
    from .store import KGStore
    with KGStore(config) as store:
        schema = store.ontology_schema()
    if not isinstance(schema, dict) or schema.get('namespace') != config.urn_prefix:
        raise ValueError('Current extraction ontology is unavailable or belongs to another namespace')
    return schema


def canonical_extraction_item(item, ontology, *, relation=False):
    """Freeze new aliases into canonical keys without rewriting old builtin payloads."""
    if ontology is None or not isinstance(item, dict):
        return item
    from .ontology import _schema
    from .ontology_editing import alias_snapshot_state, canonical_input
    builtin = _schema()
    aliases = {}
    for collection in ('properties', 'relations'):
        floor = {entry['key']: set(entry.get('aliases', [])) for entry in builtin[collection]}
        aliases[collection] = {
            alias.upper() if collection == 'relations' else alias: entry['key']
            for entry in ontology[collection]
            for alias in set(entry.get('aliases', [])) - floor.get(entry['key'], set())}
    properties = aliases['properties']
    uses_alias = any(key in properties for key in item)
    uses_alias |= any(isinstance(item.get(container), dict) and any(key in properties for key in item[container])
                      for container in ('props', 'properties'))
    if relation:
        uses_alias |= any(isinstance(item.get(key), str) and item[key].upper() in aliases['relations']
                          for key in ('type', 'rel_type', 'relation_type'))
    return canonical_input(item, alias_snapshot_state(aliases), relation=relation) if uses_alias else item


def _validate_properties(props, ontology):
    known = {entry['key']: entry for entry in ontology['properties']}
    from .ontology_editing import _matches_type
    for key, value in props.items():
        definition = known.get(key)
        if key.startswith('x_') and definition is None:
            raise ValueError('Custom property is not defined in this namespace')
        if definition is not None:
            if not definition.get('active', True):
                raise ValueError('Published ontology property is inactive')
            constrained = definition.get('custom') or set(definition.get('customized_fields', [])) & {'range', 'domain', 'required_for'}
            if constrained and not _matches_type(value, definition['range'][0]):
                raise ValueError('Published ontology property datatype rejects this extraction')


def _validate_entity(prepared, ontology):
    if ontology is None:
        return
    from .ontology_editing import entity_violations
    definition = next((entry for entry in ontology['entity_types'] if entry['key'] == prepared['type']), None)
    if definition is None or not definition.get('active', True):
        raise ValueError('Published ontology type is inactive or unavailable')
    _validate_properties(prepared['properties'], ontology)
    if entity_violations(ontology, prepared['type'], prepared['properties']):
        raise ValueError('Published ontology constraints reject this extraction entity')

def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(_):
    raise ValueError("Non-finite JSON number")


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite JSON number")
    return result


def validate_extraction(text, snapshot, config, ontology=None, *, enforce_ontology_constraints=True):
    from .schema import RELATION_ENDPOINTS, prepare_entity, prepare_relation, runtime_entity_labels
    from .grounding import validate_entity_evidence, validate_relation_evidence, reject_automatic_markers
    if ontology is not None and ontology.get('namespace') != config.urn_prefix:
        raise ValueError('Extraction ontology belongs to another namespace')
    if not isinstance(text, str) or len(text) > MAX_OUTPUT_CHARS:
        raise ValueError("Extraction output exceeds the supported bound")
    payload = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant, parse_float=_finite_float)
    if not isinstance(payload, dict) or set(payload) != {"session_id", "snapshot_id", "entities", "relations"}:
        raise ValueError("Extraction must have exactly the four required fields")
    if payload["session_id"] != snapshot["session_id"] or payload["snapshot_id"] != snapshot["snapshot_id"]:
        raise ValueError("Extraction provenance does not match the snapshot")
    entities, relations = payload["entities"], payload["relations"]
    if not isinstance(entities, list) or not isinstance(relations, list) or len(entities) > 16 or len(relations) > 32:
        raise ValueError("Extraction arrays exceed the supported bound")
    known = {config.user_person_urn: "Person"}
    seen = set()
    implicit = []
    # A reviewer may repair current custom constraints via explicit edits. The
    # original proposal still needs every builtin identity/evidence restriction;
    # the final edited batch is checked against the full registry in the store.
    preparation_schema = ontology if enforce_ontology_constraints or ontology is None else {**ontology, 'properties': []}
    for index, entity in enumerate(entities):
        try:
            if isinstance(entity, dict):
                reject_automatic_markers(entity)
            entity = canonical_extraction_item(entity, ontology)
            entities[index] = entity
            prepared, linked = prepare_entity(entity, config, preparation_schema)
            validate_entity_evidence(entity, snapshot, config, preparation_schema)
            if enforce_ontology_constraints:
                _validate_entity(prepared, ontology)
            if any(link['from']!=config.user_person_urn for link in linked
                   if link['type'] in {'HERMES_hasPreference','HERMES_hasDecision'}):
                raise ValueError('Automatic preferences and decisions must belong to the current user, never a third party')
        except ValueError as exc:
            raise ValueError(f'entities[{index}]: {exc}') from exc
        if prepared["uri"] in seen:
            raise ValueError("Duplicate entity identifiers are not allowed")
        seen.add(prepared["uri"])
        known[prepared["uri"]] = prepared["label"]
        implicit.extend(linked)
    for index, relation in enumerate(relations):
        try:
            relation = canonical_extraction_item(relation, ontology, relation=True)
            relations[index] = relation
            validate_relation_evidence(relation, snapshot, config)
        except ValueError as exc:
            raise ValueError(f'relations[{index}]: {exc}') from exc
    for index, relation in enumerate(relations + implicit):
        prepared = prepare_relation(relation, config, preparation_schema)
        if prepared['type'] in {'HERMES_hasPreference','HERMES_hasDecision'} and prepared['from']!=config.user_person_urn:
            raise ValueError('Automatic preferences and decisions must belong to the current user, never a third party')
        if prepared["from"] not in known or prepared["to"] not in known:
            raise ValueError("An extraction relationship references an unknown endpoint")
        if ontology is not None and enforce_ontology_constraints:
            from .ontology_editing import relation_violations
            definition = next((entry for entry in ontology['relations'] if entry['key'] == prepared['type']), None)
            if definition is None or not definition.get('active', True):
                raise ValueError('Published ontology relationship is inactive or unavailable')
            kinds = {label: kind for kind, label in runtime_entity_labels(ontology).items()}
            source, target = kinds[known[prepared['from']]], kinds[known[prepared['to']]]
            if any(not entry.get('active', True) and entry['key'] in {source, target}
                   for entry in ontology['entity_types']):
                raise ValueError('Published ontology endpoint type is inactive')
            _validate_properties(prepared['properties'], ontology)
            if relation_violations(ontology, prepared['type'], source, target):
                raise ValueError('Published ontology constraints reject these extraction endpoints')
        expected = RELATION_ENDPOINTS.get(prepared["type"])
        if expected and (known[prepared["from"]] not in expected[0] or known[prepared["to"]] not in expected[1]):
            raise ValueError(f"relationship[{index}] {prepared['type']} requires {'|'.join(sorted(expected[0]))} -> {'|'.join(sorted(expected[1]))}; got {known[prepared['from']]} -> {known[prepared['to']]}")
    return payload


