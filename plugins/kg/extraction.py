"""Shared strict extraction validation; independent of Hermes and model SDKs."""
from __future__ import annotations
import json
import math

MAX_OUTPUT_CHARS = 100_000

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


def validate_extraction(text, snapshot, config):
    from kg.schema import RELATION_ENDPOINTS, prepare_entity, prepare_relation
    from kg.grounding import validate_entity_evidence, validate_relation_evidence
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
    for index, entity in enumerate(entities):
        try:
            prepared, linked = prepare_entity(entity, config)
            validate_entity_evidence(entity, snapshot, config)
        except ValueError as exc:
            raise ValueError(f'entities[{index}]: {exc}') from exc
        if prepared["uri"] in seen:
            raise ValueError("Duplicate entity identifiers are not allowed")
        seen.add(prepared["uri"])
        known[prepared["uri"]] = prepared["label"]
        implicit.extend(linked)
    for index, relation in enumerate(relations):
        try:
            validate_relation_evidence(relation, snapshot, config)
        except ValueError as exc:
            raise ValueError(f'relations[{index}]: {exc}') from exc
    for index, relation in enumerate(relations + implicit):
        prepared = prepare_relation(relation, config)
        if prepared["from"] not in known or prepared["to"] not in known:
            raise ValueError("An extraction relationship references an unknown endpoint")
        expected = RELATION_ENDPOINTS.get(prepared["type"])
        if expected and (known[prepared["from"]] not in expected[0] or known[prepared["to"]] not in expected[1]):
            raise ValueError(f"relationship[{index}] {prepared['type']} requires {'|'.join(sorted(expected[0]))} -> {'|'.join(sorted(expected[1]))}; got {known[prepared['from']]} -> {known[prepared['to']]}")
    return payload


