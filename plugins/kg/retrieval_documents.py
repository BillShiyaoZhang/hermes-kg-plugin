"""Deterministic, rebuildable retrieval cards; no database or model calls.

This is a projection, not an authorization service. Callers must supply an
authorized snapshot and re-hydrate references before using a retrieved card as
evidence. The dataset helper additionally excludes private foreign, archived,
disabled and quarantined facts by default. Inactive *definitions* remain rules
and do not make their historical facts disappear.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import math
from typing import Any, Literal

from .schema import (CUSTOM_TYPE, ENTITY_LABELS, PROPERTY_ALIASES,
                     RELATION_ALIASES, RESERVED_PROPERTIES)

Representation = Literal['values', 'entity', 'entity_relations']
DOCUMENT_REVISION = 'retrieval-cards-v1'
REPRESENTATIONS = ('values', 'entity', 'entity_relations')
_VALUE_KEYS = ('foaf_name', 'hermes_name', 'hermes_aliasNames', 'hermes_description',
               'hermes_note', 'hermes_preferenceKey', 'hermes_preferenceValue',
               'hermes_decisionText', 'hermes_decisionContext', 'hermes_semanticScope',
               'hermes_eventTime', 'hermes_eventEndTime')
_BUSINESS_KEYS = set(_VALUE_KEYS) | {
    'hermes_eventType', 'hermes_timePrecision', 'hermes_eventStatus', 'hermes_relLabel',
    'hermes_status', 'hermes_statusNote', 'geo_lat', 'geo_long',
}
_SYSTEM_KEYS = RESERVED_PROPERTIES | {
    'shared', 'hermes_enabled', 'hermes_timeYearInferred', 'hermes_canonicalUri',
    'hermes_aliasConfirmed', 'hermes_repairReason', 'hermes_repairId',
}
_EXCLUDED_LABELS = {'KGQuarantined', 'KGLegacyAlias', 'KGLegacyUnresolved'}
_PROPERTY_LABELS = {
    'foaf_name': '姓名', 'hermes_name': '名称', 'hermes_aliasNames': '别名',
    'hermes_description': '说明', 'hermes_note': '备注',
    'hermes_preferenceKey': '偏好项目', 'hermes_preferenceValue': '偏好值',
    'hermes_decisionText': '决定', 'hermes_decisionContext': '决定依据',
    'hermes_semanticScope': '语义范围', 'hermes_eventTime': '开始时间',
    'hermes_eventEndTime': '结束时间', 'hermes_eventType': '事件类型',
    'hermes_timePrecision': '时间精度', 'hermes_eventStatus': '事件状态',
    'hermes_status': '状态', 'hermes_statusNote': '状态说明',
    'hermes_relLabel': '关系说明', 'geo_lat': '纬度', 'geo_long': '经度',
}
_DEFINITION_FIELDS = ('key', 'label', 'description', 'aliases', 'domain', 'range',
                      'required_for', 'active', 'managed', 'value_shape',
                      'allowed_values', 'required_condition', 'storage_label', 'storage_type', 'semantic')
_SET_FIELDS = {'aliases', 'domain', 'range', 'required_for', 'allowed_values'}


def _json_value(value):
    """Convert native/Neo4j temporal values without lossy driver to_native()."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError('Retrieval properties must contain finite numbers')
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if type(value).__module__.startswith('neo4j.time') and callable(getattr(value, 'iso_format', None)):
        return value.iso_format()
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError('Retrieval property keys must be strings')
        return {key: _json_value(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise ValueError('Unsupported retrieval property value')


def _json(value):
    return json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class RetrievalDocument:
    id: str
    source_key: str
    kind: Literal['entity', 'relation', 'ontology']
    namespace: str
    text: str
    source_refs: tuple[str, ...]
    dependency_fingerprint: str
    metadata: dict[str, Any]

    def to_dict(self):
        """Return an independent JSON-serializable value, including temporal refs."""
        return _json_value({
            'id': self.id, 'source_key': self.source_key, 'kind': self.kind,
            'namespace': self.namespace, 'text': self.text,
            'source_refs': self.source_refs,
            'dependency_fingerprint': self.dependency_fingerprint,
            'metadata': self.metadata,
        })

    @property
    def fingerprint(self):
        return self.dependency_fingerprint


def _definitions(schema, collection):
    values = (schema or {}).get(collection, [])
    if isinstance(values, Mapping):
        values = [dict(value, key=key) for key, value in values.items()]
    return {item['key']: item for item in values
            if isinstance(item, Mapping) and isinstance(item.get('key'), str)}


def _definition_projection(definition):
    result = {key: _json_value(definition[key]) for key in _DEFINITION_FIELDS if key in definition}
    for key in _SET_FIELDS & result.keys():
        if isinstance(result[key], list):
            result[key] = sorted(result[key], key=_json)
    return result


def _schema_fingerprint(schema):
    projection = {collection: [_definition_projection(value) for _, value in
                              sorted(_definitions(schema, collection).items())]
                  for collection in ('entity_types', 'properties', 'relations')}
    if (schema or {}).get('profile_digest') is not None:
        projection['profile_digest'] = schema['profile_digest']
    return _hash(projection)


def _namespace(namespace, schema=None, data=None):
    value = namespace or (schema or {}).get('namespace') or (data or {}).get('namespace')
    uri = (data or {}).get('uri') or (data or {}).get('properties', {}).get('uri')
    if not value and isinstance(uri, str) and uri.startswith('urn:'):
        value = uri.rsplit(':', 1)[0] + ':'
    if not isinstance(value, str) or not value.strip():
        raise ValueError('An explicit retrieval namespace is required')
    if (schema or {}).get('namespace') not in (None, value):
        raise ValueError('Retrieval schema and snapshot namespaces differ')
    return value


def _kind(value, labels, schema):
    known = _definitions(schema, 'entity_types')
    if isinstance(value, str):
        for key, definition in known.items():
            if value.casefold() in {key.casefold(), str(definition.get('storage_label', '')).casefold()}:
                return key
        if value.lower() in ENTITY_LABELS or CUSTOM_TYPE.fullmatch(value):
            return value.lower()
    for key, label in ENTITY_LABELS.items():
        if label in labels:
            return key
    for key, definition in sorted(known.items()):
        if definition.get('storage_label') in labels:
            return key
    for label in sorted(labels):
        if label.startswith('KGCustom_') and CUSTOM_TYPE.fullmatch(label[9:]):
            return label[9:]
    if isinstance(value, str) and value:
        return value
    raise ValueError('Retrieval entity needs a type or recognized storage label')


def _property_aliases(schema):
    result = dict(PROPERTY_ALIASES)
    for key, definition in _definitions(schema, 'properties').items():
        for alias in definition.get('aliases', []):
            result[alias] = key
    return result


def _properties(raw, kind, schema, *, stored=False, serialize=True):
    if not isinstance(raw, Mapping):
        raise ValueError('Retrieval properties must be an object')
    # Stored literal keys already have their meaning. A later custom alias
    # must not reinterpret an old literal field from a legacy graph/backup.
    aliases = {} if stored else _property_aliases(schema)
    result = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ValueError('Retrieval property keys must be strings')
        target = key if stored else aliases.get(key, key.replace(':', '_'))
        if target == 'name':
            target = 'foaf_name' if kind == 'person' else 'hermes_name'
        if target in result and _json(result[target]) != _json(value):
            raise ValueError('Conflicting canonical and alias retrieval properties')
        result[target] = _json_value(value) if serialize else value
    return result


def _value_text(props):
    # Keep the existing renderer's native str() behavior, including datetimes,
    # rather than confounding the baseline with the new card's ISO serializer.
    pieces = []
    for key in (*_VALUE_KEYS, *sorted(key for key in props if CUSTOM_TYPE.fullmatch(key))):
        value = props.get(key)
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            value = ', '.join(str(item) for item in value)
        text = str(value).strip()
        if text:
            pieces.append(text)
    return '\n'.join(pieces)


def _entity(raw, schema, namespace):
    if not isinstance(raw, Mapping):
        raise ValueError('Retrieval entities must be objects')
    labels = raw.get('labels', [])
    if not isinstance(labels, (list, tuple)) or any(not isinstance(label, str) for label in labels):
        raise ValueError('Retrieval labels must be strings')
    kind = _kind(raw.get('type'), labels, schema)
    raw_props = raw.get('properties', raw.get('props', {}))
    props = _properties(raw_props, kind, schema,
                        stored='properties' in raw)
    values = _properties(raw_props, kind, schema, stored='properties' in raw, serialize=False)
    uri = raw.get('uri') or props.get('uri')
    fixture_id = raw.get('id')
    if not uri and isinstance(fixture_id, str) and fixture_id:
        uri = namespace + kind + '-' + fixture_id
    if not isinstance(uri, str) or not uri:
        raise ValueError('Retrieval entity needs a URI or fixture ID')
    if raw.get('name') is not None:
        props.setdefault('foaf_name' if kind == 'person' else 'hermes_name', _json_value(raw['name']))
        values.setdefault('foaf_name' if kind == 'person' else 'hermes_name', raw['name'])
    props['uri'] = uri
    for key in ('shared', 'hermes_enabled', 'hermes_status', 'hermes_revision', 'hermes_updatedAt'):
        if key in raw:
            props.setdefault(key, _json_value(raw[key]))
    for alias, key in (('version', 'hermes_revision'), ('revision', 'hermes_revision'),
                       ('updated_at', 'hermes_updatedAt')):
        if alias in raw:
            props.setdefault(key, _json_value(raw[alias]))
    in_scope = uri.startswith(namespace) or props.get('shared') is True
    visible = (in_scope
               and not _EXCLUDED_LABELS.intersection(labels)
               and not raw.get('archived') and not raw.get('disabled')
               and props.get('hermes_archivedByWorkbench') is not True
               and props.get('hermes_enabled') is not False
               and props.get('hermes_status') != 'disabled')
    return {'uri': uri, 'type': kind, 'properties': props, 'labels': sorted(labels),
            'fixture_id': fixture_id, 'visible': bool(visible), 'in_scope': in_scope,
            'values_text': _value_text(values) or uri}


def _label(key, schema, collection):
    definition = _definitions(schema, collection).get(key, {})
    label = definition.get('label')
    return str(label) if label else _PROPERTY_LABELS.get(key, key)


def _business_properties(props, schema):
    definitions = _definitions(schema, 'properties')
    return {key: value for key, value in sorted(props.items())
            if value is not None and key not in _SYSTEM_KEYS
            and not definitions.get(key, {}).get('managed')
            and (key in _BUSINESS_KEYS or CUSTOM_TYPE.fullmatch(key)
                 or key in definitions)}


def _display(value):
    return value.strip() if isinstance(value, str) else _json(value)


def _field_lines(props, schema):
    definitions = _definitions(schema, 'properties')
    result = []
    for key, value in _business_properties(props, schema).items():
        ranges = definitions.get(key, {}).get('range', [])
        datatype = ', '.join(sorted(ranges)) if ranges else (
            'boolean' if isinstance(value, bool) else 'integer' if isinstance(value, int) else
            'double' if isinstance(value, float) else 'array' if isinstance(value, list) else 'string')
        result.append(f'{_label(key, schema, "properties")} [{key}; {datatype}]: {_display(value)}')
    return result


def _entity_ref(entity):
    props = entity['properties']
    return {'uri': entity['uri'], 'revision': props.get('hermes_revision', 0),
            'updated_at': props.get('hermes_updatedAt', ''),
            'visible': entity['visible'], 'shared': props.get('shared') is True}


def _document(kind, namespace, source_key, text, refs, dependencies, metadata, identity=None):
    return RetrievalDocument(
        id=f'{kind}-' + _hash([DOCUMENT_REVISION, source_key, text, identity]),
        source_key=source_key, kind=kind, namespace=namespace, text=text,
        source_refs=tuple(dict.fromkeys(refs)),
        dependency_fingerprint=_hash([DOCUMENT_REVISION, dependencies]),
        metadata=_json_value({'document_revision': DOCUMENT_REVISION, **metadata}))


def _entity_card(entity, schema, namespace, representation):
    props = entity['properties']
    if representation == 'values':
        text = entity['values_text']
    else:
        text = '\n'.join(['实体事实', f'类型: {_label(entity["type"], schema, "entity_types")} ({entity["type"]})',
                          *_field_lines(props, schema)])
    source_key = 'entity-source-' + _hash([namespace, entity['uri']])
    ref = _entity_ref(entity)
    return _document('entity', namespace, source_key, text, [entity['uri']],
                     {'entity': ref, 'content': props, 'schema': _schema_fingerprint(schema),
                      'schema_version': (schema or {}).get('version', 0)},
                     {'is_rule': False, 'representation': representation, 'uri': entity['uri'],
                      'fixture_id': entity['fixture_id'], 'type': entity['type'],
                      'active': entity['visible'], 'scope': 'owned' if entity['uri'].startswith(namespace) else 'shared',
                      'source_revision': ref['revision'], 'source_updated_at': ref['updated_at'],
                      'schema_version': (schema or {}).get('version', 0), 'dependency_refs': [ref],
                      'property_keys': sorted(_business_properties(props, schema))})


def entity_card(entity, *, schema=None, namespace=None, representation: Representation = 'entity'):
    """Project one authorized entity; dataset_to_documents applies exclusions."""
    if representation not in REPRESENTATIONS:
        raise ValueError('Unsupported retrieval representation')
    namespace = _namespace(namespace, schema, entity)
    return _entity_card(_entity(entity, schema, namespace), schema, namespace, representation)


def _canonical_relation(value, schema):
    if not isinstance(value, str) or not value:
        raise ValueError('Retrieval relation needs a type')
    aliases = dict(RELATION_ALIASES)
    for key, definition in _definitions(schema, 'relations').items():
        aliases[key.upper()] = key
        for alias in definition.get('aliases', []):
            aliases[alias.upper()] = key
    return aliases.get(value.upper(), value)


def _endpoint(raw, names, lookup):
    value = next((raw[name] for name in names if raw.get(name) is not None), None)
    if isinstance(value, Mapping):
        value = value.get('uri') or value.get('id')
    if not isinstance(value, str) or value not in lookup:
        raise ValueError('Retrieval relationship endpoints must exist in the supplied snapshot')
    return lookup[value]


def _entity_lookup(entities, schema, namespace):
    result = {}
    for raw in entities:
        entity = _entity(raw, schema, namespace)
        for key in dict.fromkeys((entity['uri'], entity['fixture_id'])):
            if key is not None:
                if key in result:
                    raise ValueError('Retrieval snapshot has duplicate entity identifiers')
                result[key] = entity
    return result


def relation_cards(relations, entities, *, schema=None, namespace=None, include_inactive=False):
    """Preserve direction and distinct content/provenance; count exact duplicates.

    Physical edge IDs never enter the projection. source_key is the write
    model's logical triple; id is a rebuildable content identity. Exact physical
    duplicates share one card with duplicate_count, rather than being lost.
    """
    if (not isinstance(relations, Sequence) or isinstance(relations, (str, bytes))
            or any(not isinstance(item, Mapping) for item in relations)):
        raise ValueError('Retrieval relations must be an array of objects')
    namespace = _namespace(namespace, schema)
    lookup = _entity_lookup(entities, schema, namespace)
    grouped = {}
    for raw in relations:
        source = _endpoint(raw, ('from', 'from_urn', 'source'), lookup)
        target = _endpoint(raw, ('to', 'to_urn', 'target'), lookup)
        if not source['uri'].startswith(namespace):
            continue
        if not target['in_scope']:
            continue
        if not include_inactive and (not source['visible'] or not target['visible']
                                     or raw.get('archived') or raw.get('disabled')):
            continue
        stored_type = raw.get('relation_type') or raw.get('type') or raw.get('rel_type')
        kind = _canonical_relation(stored_type, schema)
        props = _properties(raw.get('properties', raw.get('props', {})), None, schema,
                            stored='properties' in raw)
        if raw.get('label') is not None:
            props.setdefault('hermes_relLabel', _json_value(raw['label']))
        source_key = 'relation-source-' + _hash([namespace, source['uri'], kind, target['uri']])
        provenance = {key: props[key] for key in ('hermes_origin', 'hermes_sourceSession',
                      'hermes_sourceSessions', 'hermes_extractionIds') if key in props}
        for key in ('hermes_sourceSessions', 'hermes_extractionIds'):
            if isinstance(provenance.get(key), list):
                provenance[key] = sorted(provenance[key], key=_json)
        # Evidence contents distinguish assertions without being copied into
        # embedding text or metadata. Unknown legacy qualifiers also distinguish
        # cards even when they are not on the readable-field allowlist.
        variant = {'properties': {key: value for key, value in props.items()
                                  if key not in _SYSTEM_KEYS}, 'provenance': provenance,
                   'evidence_hash': _hash(props.get('hermes_evidenceJson'))}
        source_name = source['properties'].get('foaf_name') or source['properties'].get('hermes_name') or source['uri']
        target_name = target['properties'].get('foaf_name') or target['properties'].get('hermes_name') or target['uri']
        text = '\n'.join([
            '关系事实（有向）',
            f'来源: {source_name} [{source["type"]}]',
            f'关系: {_label(kind, schema, "relations")} ({kind})',
            f'目标: {target_name} [{target["type"]}]',
            f'方向: {source_name} → {target_name}', *_field_lines(props, schema)])
        refs = [_entity_ref(source), _entity_ref(target)]
        document = _document('relation', namespace, source_key, text,
                             [source['uri'], target['uri']],
                             {'endpoints': refs, 'properties': props, 'variant': variant,
                              'schema': _schema_fingerprint(schema), 'schema_version': (schema or {}).get('version', 0)},
                             {'is_rule': False, 'representation': 'entity_relations', 'direction': 'out',
                              'from_uri': source['uri'], 'to_uri': target['uri'],
                              'from_id': source['fixture_id'], 'to_id': target['fixture_id'],
                              'relation_type': kind, 'stored_types': [stored_type],
                              'duplicate_count': 1, 'provenance': provenance,
                              'schema_version': (schema or {}).get('version', 0),
                              'dependency_refs': refs, 'self_loop': source['uri'] == target['uri']}, variant)
        if document.id in grouped:
            previous = grouped[document.id]
            previous.metadata['duplicate_count'] += 1
            previous.metadata['stored_types'] = sorted(set(previous.metadata['stored_types']) | {stored_type})
            # A duplicate's metadata revision can differ without changing its
            # content identity; preserve all dependency fingerprints deterministically.
            previous.metadata.setdefault('duplicate_dependencies', [previous.dependency_fingerprint])
            previous.metadata['duplicate_dependencies'].append(document.dependency_fingerprint)
        else:
            grouped[document.id] = document
    result = []
    for document in grouped.values():
        dependencies = document.metadata.get('duplicate_dependencies')
        if dependencies:
            dependencies.sort()
            document = RetrievalDocument(**{**document.__dict__,
                'dependency_fingerprint': _hash(dependencies)})
        result.append(document)
    return sorted(result, key=lambda document: document.id)


def ontology_cards(schema, *, namespace=None):
    """One explicit rule card per definition, never inferred fact edges/counts."""
    namespace = _namespace(namespace, schema)
    result = []
    names = {'entity_types': '实体类型', 'properties': '属性', 'relations': '关系'}
    for collection in names:
        for key, definition in sorted(_definitions(schema, collection).items()):
            projection = _definition_projection(definition)
            lines = ['本体规则（不是已发生的事实）', f'定义类型: {names[collection]}',
                     f'标识: {key}', f'名称: {_label(key, schema, collection)}',
                     f'状态: {"启用" if definition.get("active", True) else "停用；保留历史含义"}']
            if definition.get('description'):
                lines.append('说明: ' + str(definition['description']))
            semantic = projection.get('semantic')
            if isinstance(semantic, Mapping):
                for field, label in (('iri', '语义标识'), ('source', '定义来源'),
                                     ('source_version', '来源版本'), ('mapping', '对齐方式'),
                                     ('direction', '对齐方向'), ('note', '对齐说明')):
                    if semantic.get(field):
                        lines.append(f'{label}: {_display(semantic[field])}')
            for field, label in (('aliases', '别名'), ('domain', '适用来源类型'),
                                 ('range', '允许目标类型' if collection == 'relations' else '值类型'),
                                 ('required_for', '必填类型'), ('allowed_values', '允许值')):
                if field in projection:
                    lines.append(f'{label}: {_display(projection[field])}')
            if projection.get('required_condition'):
                lines.append('必填条件: ' + str(projection['required_condition']))
            ref = namespace + 'schema:' + collection + ':' + key
            source_key = 'ontology-source-' + _hash([namespace, collection, key])
            dependencies = {'definition': projection, 'schema_version': schema.get('version', 0)}
            if schema.get('profile_digest') is not None:
                dependencies['profile_digest'] = schema['profile_digest']
            result.append(_document('ontology', namespace, source_key, '\n'.join(lines), [ref],
                                    dependencies,
                                    {'is_rule': True, 'definition_kind': collection, 'definition_key': key,
                                     'active': definition.get('active', True),
                                     'schema_version': schema.get('version', 0)}))
    return sorted(result, key=lambda document: document.id)


def dataset_to_documents(dataset, representation: Representation = 'entity', *, schema=None,
                         namespace=None, include_ontology=False, include_inactive=False):
    """Project a research fixture or authorized {namespace,entities,relations} snapshot.

    values reproduces the existing value-only entity renderer. entity adds
    typed/labeled fields. entity_relations adds standalone directed fact cards.
    Ontology rules are a separate, explicit opt-in in every representation.
    """
    if representation not in REPRESENTATIONS:
        raise ValueError('Unsupported retrieval representation')
    if not isinstance(dataset, Mapping):
        raise ValueError('Retrieval dataset must be an object')
    schema = schema if schema is not None else dataset.get('schema', dataset.get('ontology', {}))
    namespace = _namespace(namespace or dataset.get('namespace'), schema)
    entities, relations = dataset.get('entities', []), dataset.get('relations', [])
    if not isinstance(entities, Sequence) or isinstance(entities, (str, bytes)):
        raise ValueError('Retrieval entities must be an array')
    lookup = _entity_lookup(entities, schema, namespace)
    normalized = {entity['uri']: entity for entity in lookup.values()}
    documents = [_entity_card(entity, schema, namespace, representation)
                 for _, entity in sorted(normalized.items())
                 if entity['in_scope'] and (include_inactive or entity['visible'])]
    if representation == 'entity_relations':
        documents.extend(relation_cards(relations, entities, schema=schema, namespace=namespace,
                                        include_inactive=include_inactive))
    if include_ontology:
        documents.extend(ontology_cards(schema, namespace=namespace))
    return sorted(documents, key=lambda document: (document.kind, document.id))
