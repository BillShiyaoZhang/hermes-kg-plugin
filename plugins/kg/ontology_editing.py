"""Versioned namespace overlays. Packaged ontology and runtime IDs stay immutable."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
import math
import re
import unicodedata

from .schema import ENTITY_LABELS, PROPERTY_ALIASES, RELATION_ALIASES, RESERVED_PROPERTIES

COLLECTIONS = ('entity_types', 'properties', 'relations')
CUSTOM_KEY_PATTERNS = {'entity_types': r'x_[a-z][a-z0-9_]{0,47}', 'properties': r'x_[a-z][a-z0-9_]{0,47}',
                       'relations': r'X_[A-Z][A-Z0-9_]{0,47}'}
DATATYPES = ['string', 'integer', 'double', 'boolean', 'dateTime', 'date']
FIELDS = {'entity_types': {'label', 'description'},
          'properties': {'label', 'description', 'aliases', 'required_for', 'domain', 'range'},
          'relations': {'label', 'description', 'aliases', 'domain', 'range'}}
STATE_QUERY = '''MATCH (s:KGOntologySchema {namespace:$namespace})
    RETURN s.version AS version,s.patch_json AS patch_json,s.presets_json AS presets_json'''


class OntologyEditError(ValueError):
    def __init__(self, code, *, version=None, impact=None, status=409):
        super().__init__(code)
        self.code, self.version, self.impact, self.status = code, version, impact, status

    def response(self):
        messages = {'ontology_version_conflict': '本体已发生变化，请读取最新版本后再核对。',
                    'ontology_data_conflict': '现有数据与拟发布约束冲突，请先修正数据或调整约束。',
                    'ontology_operation_conflict': '此操作标识已用于其他本体修改。',
                    'ontology_namespace_conflict': '工作空间已变化，未提交本体修改。',
                    'ontology_confirmation_conflict': '影响或确认内容已变化，请重新预览并确认。',
                    'ontology_stats_unavailable': '完整引用统计暂不可用，未执行变更，请稍后重试。',
                    'ontology_semantic_conflict': '修改超出已绑定标准的语义范围，请保留兼容约束或创建本地定义。',
                    'ontology_invalid_patch': '本体修改未通过校验，请检查可编辑字段和运行时约束。'}
        return {'detail': messages.get(self.code, '本体修改未完成。'), 'code': self.code,
                **({'version': self.version} if self.version is not None else {}),
                **({'impact': self.impact} if self.impact is not None else {})}


def state_from_rows(rows):
    rows = list(rows)
    if not rows or 'version' not in rows[0]:
        return {'version': 0, 'patch': {}}
    if len(rows) != 1:
        raise RuntimeError('Duplicate ontology state requires maintenance')
    state = {'version': int(rows[0]['version']), 'patch': json.loads(rows[0].get('patch_json') or '{}')}
    if rows[0].get('presets_json') is not None:
        from .ontology_presets import validate_presets
        state['presets'] = validate_presets(json.loads(rows[0]['presets_json']))
    return state


def capabilities(base):
    from .ontology import _schema
    floor = deepcopy(_schema())
    custom_types = {item['key'] for item in base['entity_types'] if item.get('custom')}
    for item in floor['properties']:
        if item['key'] == 'hermes_name':
            if 'any' not in item['domain']:
                item['domain'] = sorted(set(item['domain']) | custom_types)
            item['required_for'] = sorted(set(item['required_for']) | custom_types)
    readonly = [item['key'] for item in base['properties'] if item['managed']]
    return {'scope': 'namespace_overlay', 'can_edit': True, 'can_add_types': True,
            'can_add_properties': True, 'can_add_relations': True, 'can_manage_lifecycle': True,
            'custom_key_patterns': CUSTOM_KEY_PATTERNS, 'create_datatypes': DATATYPES,
            'create_value_shapes': ['scalar'],
            'can_change_storage_ids': False, 'structural_policy': 'tighten_only', 'custom_structural_policy': 'data_compatible',
            'structural_relation_scope': 'current_namespace_active_endpoints',
            'structural_property_scope': 'current_namespace_entities_and_owned_relationships',
            'alias_input_scope': 'properties_object_and_relation_type',
            'alias_pattern': '[A-Za-z][A-Za-z0-9_]{0,63}',
            'integer_policy': 'finite_integral_numeric_value',
            'entity_fields': sorted(FIELDS['entity_types']), 'property_fields': sorted(FIELDS['properties']),
            'relation_fields': sorted(FIELDS['relations']), 'readonly_properties': readonly,
            'immutable_keys': ['key', 'iri', 'storage_label', 'storage_type', 'runtime_supported', 'managed', 'value_shape'],
            'builtin_constraints': {
                'properties': {item['key']: {field: deepcopy(item[field]) for field in ('domain', 'range', 'required_for', 'aliases')}
                               for item in floor['properties']},
                'relations': {item['key']: {field: deepcopy(item[field]) for field in ('domain', 'range', 'aliases')}
                              for item in floor['relations']}},
            'datatypes_by_property': {item['key']: [] if item['managed'] else
                DATATYPES if item.get('custom') else ['double', 'integer'] if item['key'] in {'geo_lat', 'geo_long'} else item['range']
                for item in base['properties']}}


def _definitions(state):
    from .ontology import _schema
    result = deepcopy(_schema())
    from .ontology_presets import semantic_bindings
    bindings = semantic_bindings(state.get('presets', {}))
    for collection in COLLECTIONS:
        known = {item['key']: item for item in result[collection]}
        for key, custom in state['patch'].get(collection, {}).items():
            if key not in known:
                if not custom.get('custom'):
                    raise ValueError('Unknown ontology definition')
                item = deepcopy(custom)
                item['key'] = key
                result[collection].append(item)
            else:
                known[key].update(deepcopy(custom))
        for item in result[collection]:
            item.setdefault('custom', False)
            item.setdefault('active', True)
            item['customized_fields'] = sorted(state['patch'].get(collection, {}).get(item['key'], {}))
            item['count'] = None
            item['allowed_actions'] = [] if item.get('managed') else ['update', 'deactivate' if item['active'] else 'restore'] + (['delete'] if item['custom'] else [])
            if item['key'] in bindings.get(collection, {}):
                item['semantic'] = deepcopy(bindings[collection][item['key']])
    return result


def effective_schema(config, state):
    from .ontology import _schema, SCOPE
    from .ontology_presets import validate_semantic_constraints
    validate_semantic_constraints(state)
    result = _definitions(state)
    extra_types = [item['key'] for item in result['entity_types'] if item['custom']]
    for item in result['properties']:
        if item['key'] == 'hermes_name':
            if 'any' not in item['domain']:
                item['domain'] = sorted(set(item['domain']) | set(extra_types))
            item['required_for'] = sorted(set(item['required_for']) | set(extra_types))
    for entity in result['entity_types']:
        entity['properties'] = [deepcopy(item) for item in result['properties']
                                if 'any' in item['domain'] or entity['key'] in item['domain']]
    result.update(namespace=config.urn_prefix, scope=SCOPE, version=state['version'], read_only=False,
                  editing=capabilities(result), stats_available=False, stats_error=None,
                  stats={'entities': None, 'relationships': None, 'unmapped_relationships': None})
    if state.get('presets'):
        from .ontology_presets import preset_digest
        result['profile_digest'] = preset_digest(state['presets'])
        result['presets'] = [{key: value for key, value in item.items() if key in {'id', 'version', 'digest', 'sources'}}
                             for _, item in sorted(state['presets'].items())]
    return result


def _types(values, schema=None):
    return ({item['key'] for item in schema['entity_types']} if schema else set(ENTITY_LABELS)) if 'any' in values else set(values)


def _created_definition(config, kind, key, fields):
    if (not isinstance(key, str) or not re.fullmatch(CUSTOM_KEY_PATTERNS[kind], key)
            or not isinstance(fields, dict) or not isinstance(fields.get('label'), str)):
        raise OntologyEditError('ontology_invalid_patch', status=400)
    allowed = FIELDS[kind] | ({'value_shape'} if kind == 'properties' else set())
    if not set(fields) <= allowed or fields.get('value_shape', 'scalar') != 'scalar':
        raise OntologyEditError('ontology_invalid_patch', status=400)
    item = {'key': key, 'custom': True, 'active': True, 'label': key, 'description': '',
            'iri': config.urn_prefix + 'ontology:' + kind + ':' + key, 'runtime_supported': True}
    if kind == 'entity_types':
        item.update(storage_label='KGCustom_' + key, properties=[])
    elif kind == 'properties':
        item.update(domain=['any'], range=['string'], required_for=[], aliases=[], managed=False,
                    value_shape='scalar', required_condition=None, allowed_values=[])
    else:
        item.update(storage_type=key, domain=['any'], range=['any'], declared_domain=['any'],
                    declared_range=['any'], aliases=[], properties=[])
    item.update(deepcopy(fields))
    return item


def merge_patch(config, state, patch, operations=None):
    from .ontology import _schema
    if not isinstance(patch, dict) or not set(patch) <= set(COLLECTIONS):
        raise OntologyEditError('ontology_invalid_patch', status=400)
    operations = [] if operations is None else operations
    if not isinstance(operations, list) or len(operations) > 32:
        raise OntologyEditError('ontology_invalid_patch', status=400)
    base, merged, changes = _schema(), deepcopy(state['patch']), []
    current = _definitions(state)
    touched = {(kind, key) for kind, definitions in patch.items() if isinstance(definitions, dict) for key in definitions}
    if len(touched) + len(operations) > 32:
        raise OntologyEditError('ontology_invalid_patch', status=400)
    for operation in operations:
        if not isinstance(operation, dict) or not set(operation) <= {'action', 'kind', 'key', 'definition'}:
            raise OntologyEditError('ontology_invalid_patch', status=400)
        kind, key, action = (operation.get(field) for field in ('kind', 'key', 'action'))
        if kind not in COLLECTIONS or not isinstance(key, str) or (kind, key) in touched:
            raise OntologyEditError('ontology_invalid_patch', status=400)
        touched.add((kind, key))
        existing = next((item for item in current[kind] if item['key'] == key), None)
        if action == 'create':
            if existing:
                raise OntologyEditError('ontology_invalid_patch', status=400)
            merged.setdefault(kind, {})[key] = _created_definition(config, kind, key, operation.get('definition'))
        elif action in {'deactivate', 'restore', 'delete'}:
            if not existing or existing.get('managed') or 'definition' in operation or action not in existing['allowed_actions']:
                raise OntologyEditError('ontology_invalid_patch', status=400)
            if action == 'delete':
                merged.get(kind, {}).pop(key)
            else:
                merged.setdefault(kind, {}).setdefault(key, {})['active'] = action == 'restore'
        else:
            raise OntologyEditError('ontology_invalid_patch', status=400)
        changes.append({'kind': kind, 'key': key, 'fields': ['active'] if action in {'deactivate', 'restore'} else [], 'action': action})
    # Creation fields use the same strict field validation as updates below.
    patch = deepcopy(patch)
    for operation in operations:
        if operation['action'] == 'create':
            fields = {key: value for key, value in merged[operation['kind']][operation['key']].items() if key in FIELDS[operation['kind']]}
            patch.setdefault(operation['kind'], {})[operation['key']] = fields
    working = {**state, 'patch': merged}
    all_definitions = _definitions(working)
    type_keys = {item['key'] for item in all_definitions['entity_types']}
    for collection, definitions in patch.items():
        if not isinstance(definitions, dict) or len(definitions) > 32:
            raise OntologyEditError('ontology_invalid_patch', status=400)
        known = {item['key']: item for item in all_definitions[collection]}
        builtin = {item['key']: item for item in base[collection]}
        if collection == 'properties':
            builtin = deepcopy(builtin)
            custom_types = {item['key'] for item in all_definitions['entity_types'] if item['custom']}
            if 'any' not in builtin['hermes_name']['domain']:
                builtin['hermes_name']['domain'] = sorted(set(builtin['hermes_name']['domain']) | custom_types)
            builtin['hermes_name']['required_for'] = sorted(set(builtin['hermes_name']['required_for']) | custom_types)
        for key, fields in definitions.items():
            if key not in known or not isinstance(fields, dict) or not set(fields) <= FIELDS[collection]:
                raise OntologyEditError('ontology_invalid_patch', status=400)
            if known[key].get('managed'):
                raise OntologyEditError('ontology_invalid_patch', status=400)
            normalized = {}
            for field, value in fields.items():
                if field in {'label', 'description'}:
                    if not isinstance(value, str) or len(value) > (120 if field == 'label' else 4000) or (field == 'label' and not value.strip()):
                        raise OntologyEditError('ontology_invalid_patch', status=400)
                    normalized[field] = ' '.join(value.split()) if field == 'label' else value
                    if field == 'label' and any(unicodedata.category(character) == 'Cc' for character in normalized[field]):
                        raise OntologyEditError('ontology_invalid_patch', status=400)
                else:
                    if (not isinstance(value, list) or len(value) > (24 if field == 'aliases' else 128)
                            or any(not isinstance(item, str) or not item or len(item) > 64 for item in value)):
                        raise OntologyEditError('ontology_invalid_patch', status=400)
                    normalized[field] = sorted(set(value))
            previous = {**known[key], **merged.get(collection, {}).get(key, {})}
            if collection == 'properties' and key == 'hermes_name':
                if 'any' not in previous['domain']:
                    previous['domain'] = sorted(set(previous['domain']) | custom_types)
                previous['required_for'] = sorted(set(previous['required_for']) | custom_types)
            updated = {**previous, **normalized}
            if collection in {'properties', 'relations'}:
                for field in ('domain', 'range') if collection == 'relations' else ('domain',):
                    values = updated[field]
                    if (not values or not set(values) <= type_keys | {'any'} or ('any' in values and len(values) != 1)
                            or (key in builtin and not _types(values, all_definitions) <= _types(builtin[key][field], all_definitions))):
                        raise OntologyEditError('ontology_invalid_patch', status=400)
                if 'aliases' in normalized and not set(builtin.get(key, {}).get('aliases', [])) <= set(normalized['aliases']):
                    raise OntologyEditError('ontology_invalid_patch', status=400)
            if collection == 'properties':
                if (not set(updated['required_for']) <= type_keys
                        or not set(builtin.get(key, {}).get('required_for', [])) <= set(updated['required_for'])
                        or not set(updated['required_for']) <= _types(updated['domain'], all_definitions)
                        or len(updated['range']) != 1
                        or updated['range'][0] not in (DATATYPES if known[key].get('custom') else capabilities(base)['datatypes_by_property'][key])):
                    raise OntologyEditError('ontology_invalid_patch', status=400)
            actual = sorted(field for field, value in normalized.items()
                            if (set(previous.get(field, [])) != set(value) if isinstance(value, list)
                                else previous.get(field) != value))
            if actual:
                merged.setdefault(collection, {}).setdefault(key, {}).update({field: normalized[field] for field in actual})
                if not any(item['kind'] == collection and item['key'] == key for item in changes):
                    changes.append({'kind': collection, 'key': key, 'fields': actual, 'action': 'update'})
    candidate = {**state, 'patch': merged}
    # Validate all custom aliases together, including previously published ones.
    aliases_for(candidate)
    from .ontology_presets import validate_semantic_constraints
    validate_semantic_constraints(candidate)
    return candidate, changes


def aliases_for(state):
    from .ontology import _schema
    result = {'properties': {}, 'relations': {}}
    base = _schema()
    definitions = _definitions(state)
    for collection in result:
        known = {item['key']: item for item in definitions[collection]}
        builtin = {item['key']: item for item in base[collection]}
        for key, fields in state['patch'].get(collection, {}).items():
            for alias in set(fields.get('aliases', [])) - set(builtin.get(key, {}).get('aliases', [])):
                normalized = alias.upper() if collection == 'relations' else alias
                forbidden = (set(RESERVED_PROPERTIES) | set(PROPERTY_ALIASES) | set(PROPERTY_ALIASES.values()) |
                             {item['key'] for item in definitions['properties']} | {'type', 'uri', 'id', 'urn', 'name', 'properties', 'props'})
                if (not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', alias)
                        or alias.lower() in {'uri', 'type', 'id', 'urn', 'name', 'properties', 'props', 'namespace', 'operation_id', 'expected_version'}
                        or (collection == 'properties' and (alias in forbidden or alias.lower().startswith(('hermes_', 'foaf_', 'geo_'))))
                        or (collection == 'relations' and (normalized in RELATION_ALIASES or normalized in {value.upper() for value in known}))
                        or (normalized in result[collection] and result[collection][normalized] != key)):
                    raise OntologyEditError('ontology_invalid_patch', status=400)
                result[collection][normalized] = key
    return result


def validate_state(config, state):
    """Validate durable overlays separately from update-only request patches."""
    if not isinstance(state, dict) or not isinstance(state.get('patch'), dict) or not set(state['patch']) <= set(COLLECTIONS):
        raise ValueError('Invalid stored ontology')
    patch, operations, inactive = {}, [], []
    for kind, definitions in state['patch'].items():
        if not isinstance(definitions, dict):
            raise ValueError('Invalid stored ontology definitions')
        for key, fields in definitions.items():
            if not isinstance(fields, dict) or not isinstance(fields.get('active', True), bool):
                raise ValueError('Invalid stored ontology definition')
            if fields.get('custom'):
                editable = {field: value for field, value in fields.items() if field in FIELDS[kind] or field == 'value_shape'}
                expected = _created_definition(config, kind, key, editable)
                if set(fields) != set(expected) or any(fields.get(field) != expected[field] for field in set(expected) - FIELDS[kind] - {'active'}):
                    raise ValueError('Invalid custom ontology identity')
                operations.append({'action': 'create', 'kind': kind, 'key': key, 'definition': editable})
            else:
                if not set(fields) <= FIELDS[kind] | {'active'}:
                    raise ValueError('Invalid builtin ontology overlay')
                patch.setdefault(kind, {})[key] = {field: value for field, value in fields.items() if field != 'active'}
            if fields.get('active') is False:
                inactive.append({'action': 'deactivate', 'kind': kind, 'key': key})
    # State can accumulate more than one request's 32 definitions. Validate in
    # one semantic pass without weakening the per-request budget.
    staged = {'version': state.get('version', 0), 'patch': {}}
    if len(operations) + sum(map(len, patch.values())) <= 32:
        staged, _ = merge_patch(config, staged, patch, operations)
    else:
        # Seed identities first, then validate chunks against the full registry.
        staged['patch'] = {kind: {key: {**deepcopy(fields), 'active': True} for key, fields in definitions.items() if fields.get('custom')}
                           for kind, definitions in state['patch'].items()}
        for kind, definitions in state['patch'].items():
            entries = list(definitions.items())
            for offset in range(0, len(entries), 32):
                chunk = {key: {field: value for field, value in fields.items() if field in FIELDS[kind]} for key, fields in entries[offset:offset+32]}
                staged, _ = merge_patch(config, staged, {kind: chunk})
    for offset in range(0, len(inactive), 32):
        staged, _ = merge_patch(config, staged, {}, inactive[offset:offset+32])
    return effective_schema(config, state)


def alias_snapshot_state(snapshot):
    """Reconstruct only validated custom bindings, never historical constraints."""
    from .ontology import _schema
    if not isinstance(snapshot, dict) or not set(snapshot) <= {'properties', 'relations'}:
        raise ValueError('Invalid review ontology alias snapshot')
    patch = {}
    for collection in ('properties', 'relations'):
        bindings = snapshot.get(collection, {})
        known = {item['key']: item for item in _schema()[collection]}
        if not isinstance(bindings, dict):
            raise ValueError('Invalid review ontology alias snapshot')
        for alias, key in bindings.items():
            if (not isinstance(alias, str) or not isinstance(key, str)
                    or (key not in known and not re.fullmatch(CUSTOM_KEY_PATTERNS[collection], key))):
                raise ValueError('Invalid review ontology alias snapshot')
            definition = patch.setdefault(collection, {}).setdefault(key, {'aliases': list(known.get(key, {}).get('aliases', [])),
                                                                          **({'custom': True} if key not in known else {})})
            definition['aliases'].append(alias)
    state = {'version': 0, 'patch': patch}
    if aliases_for(state) != {collection: snapshot.get(collection, {}) for collection in ('properties', 'relations')}:
        raise ValueError('Invalid review ontology alias snapshot')
    return state


def canonical_input(item, state, *, relation=False):
    aliases = aliases_for(state)
    result = deepcopy(item)
    if not isinstance(result, dict):
        return result
    if any(key in aliases['properties'] for key in result):
        raise ValueError('Custom ontology aliases must be provided inside props or properties')
    provided, custom_targets = {}, set()
    for container in ('properties', 'props'):
        if isinstance(result.get(container), dict):
            values = {}
            for key, value in result[container].items():
                if not isinstance(key, str):
                    raise ValueError('Property names must be strings')
                canonical = aliases['properties'].get(key, PROPERTY_ALIASES.get(key, key.replace(':', '_')))
                if canonical == 'name' and not relation:
                    canonical = 'foaf_name' if str(result.get('type', '')).split(':')[-1].lower() == 'person' else 'hermes_name'
                if canonical in provided and provided[canonical] != value:
                    raise ValueError('Conflicting ontology property aliases')
                values[canonical] = provided[canonical] = value
                if key in aliases['properties']:
                    custom_targets.add(canonical)
            result[container] = values
    # The legacy preparer permits top-level builtin fields to override props.
    # Custom aliases must never disappear through that precedence rule.
    for key, value in result.items():
        canonical = PROPERTY_ALIASES.get(key, key)
        if key == 'name' and not relation:
            canonical = 'foaf_name' if str(result.get('type', '')).split(':')[-1].lower() == 'person' else 'hermes_name'
        if relation and key in {'label', 'rel_label'}:
            canonical = 'hermes_relLabel'
        if canonical in custom_targets and provided[canonical] != value:
            raise ValueError('Conflicting ontology property aliases')
    if relation:
        for key in ('type', 'rel_type', 'relation_type'):
            if isinstance(result.get(key), str):
                result[key] = aliases['relations'].get(result[key].upper(), result[key])
    return result


def _matches_type(value, datatype):
    if datatype == 'string': return isinstance(value, str)
    if datatype == 'boolean': return isinstance(value, bool)
    if datatype == 'integer': return ((isinstance(value, int) and not isinstance(value, bool))
                                     or (isinstance(value, float) and math.isfinite(value) and value.is_integer()))
    if datatype == 'double': return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if datatype == 'dateTime': return isinstance(value, datetime) or type(value).__name__ == 'DateTime'
    if datatype == 'date': return (isinstance(value, date) and not isinstance(value, datetime)) or type(value).__name__ == 'Date'
    return False


def entity_violations(schema, kind, props):
    violations = []
    for item in schema['properties']:
        if not item.get('active', True) or not (item.get('custom') or set(item['customized_fields']) & {'domain', 'range', 'required_for'}):
            continue
        key, value = item['key'], props.get(item['key'])
        unknown_event = (key == 'hermes_eventTime' and kind == 'event'
                         and props.get('hermes_timePrecision') == 'unknown' and props.get('hermes_eventStatus') == 'occurred')
        if kind in item['required_for'] and (value is None or (isinstance(value, str) and not value.strip())) and not unknown_event:
            violations.append(('properties', key, 'required'))
        if value is not None:
            if kind not in _types(item['domain'], schema): violations.append(('properties', key, 'domain'))
            values = value if item.get('value_shape') == 'array' and isinstance(value, list) else [value]
            if not all(_matches_type(value, item['range'][0]) for value in values): violations.append(('properties', key, 'datatype'))
    return violations


def relation_violations(schema, kind, source, target):
    item = next((item for item in schema['relations'] if item['key'] == kind), None)
    if item and item.get('active', True) and (item.get('custom') or set(item['customized_fields']) & {'domain', 'range'}):
        if source not in _types(item['domain'], schema) or target not in _types(item['range'], schema):
            return [('relations', kind, 'endpoints')]
    return []


def validate_batch(config, state, entities, relations, current):
    schema = effective_schema(config, state)
    registered = {item['key'] for item in schema['properties']}
    def unchanged(left, right):
        if type(left) is not type(right):
            native_left = left.to_native() if callable(getattr(left, 'to_native', None)) else left
            native_right = right.to_native() if callable(getattr(right, 'to_native', None)) else right
            return (type(native_left) is type(native_right) and isinstance(native_left, (date, datetime))
                    and native_left == native_right)
        if isinstance(left, list):
            return len(left) == len(right) and all(unchanged(a, b) for a, b in zip(left, right))
        return left == right
    def retained(previous, key, value):
        return (bool(previous) and key in previous[0] and unchanged(previous[0][key], value)
                and all(key in old and unchanged(previous[0][key], old[key]) for old in previous))
    # Old stores permitted arbitrary x_* values. Preserve an unchanged legacy
    # value during unrelated corrections, but never introduce unknown fields.
    for item in entities:
        old = (current.get(item['uri']) or {}).get('properties', {})
        for key, value in item['properties'].items():
            if key.startswith('x_') and key not in registered and (key not in old or not unchanged(old[key], value)):
                raise ValueError('Custom property is not defined in this namespace')
            if key.startswith('x_') and key not in registered:
                item['properties'][key] = old[key]
    for item in relations:
        previous = item.get('_existing_properties', [])
        for key, value in item['properties'].items():
            if key.startswith('x_') and key not in registered:
                if not retained(previous, key, value):
                    raise ValueError('Custom relationship property is not defined in this namespace')
                item['properties'][key] = previous[0][key]
    if not state['patch']:
        return
    incoming = {item['uri']: item for item in entities}
    for item in entities:
        definition = next((entry for entry in schema['entity_types'] if entry['key'] == item['type']), None)
        if definition is None or (not definition['active'] and not current.get(item['uri'])):
            raise ValueError('Published ontology type is inactive or unavailable')
        props = {**(current.get(item['uri']) or {}).get('properties', {}), **item['properties']}
        old_props = (current.get(item['uri']) or {}).get('properties', {})
        for entry in schema['properties']:
            if (not entry['active'] and entry['key'] in item['properties']
                    and (entry['key'] not in old_props or not unchanged(item['properties'][entry['key']], old_props[entry['key']]))):
                raise ValueError('Published ontology property is inactive')
            if not entry['active'] and entry['key'] in item['properties']:
                item['properties'][entry['key']] = props[entry['key']] = old_props[entry['key']]
        # The base coordinate normalizer emits floats; an integer restriction
        # additionally normalizes integral coordinates before persistence.
        for definition in schema['properties']:
            key = definition['key']
            if key in item['properties'] and definition['range'] == ['integer'] and isinstance(props.get(key), float) and props[key].is_integer():
                item['properties'][key] = props[key] = int(props[key])
        if entity_violations(schema, item['type'], props):
            raise ValueError('Published ontology constraints reject this entity')
    def kind(uri):
        if uri in incoming: return incoming[uri]['type']
        return next((item['key'] for item in schema['entity_types'] if item['storage_label'] in (current.get(uri) or {}).get('labels', [])), None)
    for rel in relations:
        definition = next((entry for entry in schema['relations'] if entry['key'] == rel['type']), None)
        if definition is None or not definition['active']:
            raise ValueError('Published ontology relationship is inactive or unavailable')
        for endpoint in (rel['from'], rel['to']):
            if endpoint.startswith(config.urn_prefix) and any(entry['key'] == kind(endpoint) and not entry['active'] for entry in schema['entity_types']):
                raise ValueError('Published ontology endpoint type is inactive')
        for entry in schema['properties']:
            if not entry['active'] and entry['key'] in rel['properties']:
                previous = rel.get('_existing_properties', [])
                if not retained(previous, entry['key'], rel['properties'][entry['key']]):
                    raise ValueError('Published ontology relationship property is inactive')
                rel['properties'][entry['key']] = previous[0][entry['key']]
        for entry in schema['properties']:
            key = entry['key']
            if (entry['active'] and (entry.get('custom') or set(entry['customized_fields']) & {'domain', 'range', 'required_for'})
                    and key in rel['properties'] and not _matches_type(rel['properties'][key], entry['range'][0])):
                raise ValueError('Published ontology constraints reject this relationship property datatype')
        if not rel['to'].startswith(config.urn_prefix):
            continue
        if relation_violations(schema, rel['type'], kind(rel['from']), kind(rel['to'])):
            raise ValueError('Published ontology constraints reject these relationship endpoints')


class OntologyEditor:
    def __init__(self, config, store):
        self.config, self.store = config, store

    def _precondition(self, expected_namespace, expected_version):
        if expected_namespace != self.config.urn_prefix:
            raise OntologyEditError('ontology_namespace_conflict')
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 0:
            raise OntologyEditError('ontology_invalid_patch', status=400)

    def _impact(self, tx, candidate, structural):
        if not structural:
            return {'checked_nodes': 0, 'checked_relations': 0, 'violations': []}
        from .ontology import _active, _stored_relation
        schema = effective_schema(self.config, candidate)
        definitions = [item for item in schema['properties'] if item['active'] and (item.get('custom') or set(item['customized_fields']) & {'domain', 'range', 'required_for'})]
        properties = [item['key'] for item in definitions]
        labels = {item['key']: item['storage_label'] for item in schema['entity_types']}
        params = {'prefix': self.config.urn_prefix, 'entity_labels': list(labels.values()),
                  'property_keys': sorted(set(properties) | {'hermes_timePrecision', 'hermes_eventStatus'})}
        rows = tx.run(f'''MATCH (n) WHERE {_active('n')}
            RETURN labels(n) AS labels,[key IN $property_keys | {{key:key,missing:n[key] IS NULL,
                empty:CASE WHEN valueType(n[key]) STARTS WITH 'STRING' THEN trim(n[key])='' ELSE false END,
                finite:CASE WHEN valueType(n[key]) STARTS WITH 'FLOAT' THEN abs(n[key])<=1.7976931348623157e308 ELSE true END,
                integral:CASE WHEN valueType(n[key]) STARTS WITH 'INTEGER' THEN true
                    WHEN valueType(n[key]) STARTS WITH 'FLOAT' THEN abs(n[key])<=1.7976931348623157e308 AND n[key]=floor(n[key])
                    ELSE false END,datatype:valueType(n[key])}}] AS values,
                n.hermes_timePrecision='unknown' AND n.hermes_eventStatus='occurred' AS unknown_event''', params)
        counts, checked = {}, 0
        for row in rows:
            checked += 1
            kind = next(key for key, label in labels.items() if label in row['labels'])
            values = {value['key']: value for value in row['values']}
            for definition in definitions:
                key = definition['key']
                value = values[key]
                issues = []
                unknown_event = key == 'hermes_eventTime' and kind == 'event' and row.get('unknown_event') is True
                if kind in definition['required_for'] and (value['missing'] or value['empty'] is True) and not unknown_event:
                    issues.append('required')
                if not value['missing']:
                    if kind not in _types(definition['domain'], schema): issues.append('domain')
                    datatype = value['datatype'].upper().replace(' ', '')
                    expected = definition['range'][0]
                    valid = (datatype.startswith('STRING') if expected == 'string' else
                             datatype.startswith('BOOLEAN') if expected == 'boolean' else
                             value['integral'] is True if expected == 'integer' else
                             datatype.startswith(('INTEGER', 'FLOAT')) and value['finite'] if expected == 'double' else
                             datatype.startswith(('LOCALDATETIME', 'ZONEDDATETIME', 'DATETIME')) if expected == 'dateTime' else
                             datatype.startswith('DATE') and 'DATETIME' not in datatype if expected == 'date' else False)
                    if not valid: issues.append('datatype')
                for code in issues:
                    violation = ('properties', key, code)
                    counts[violation] = counts.get(violation, 0) + 1
        relations = tx.run(f'''MATCH (a)-[r]->(b) WHERE {_active('a')}
            AND b.uri IS NOT NULL AND NOT b:KGQuarantined AND NOT b:KGLegacyAlias AND NOT b:KGLegacyUnresolved
            AND coalesce(b.hermes_aliasConfirmed,false)=false AND coalesce(b.hermes_enabled,true)=true
            AND coalesce(b.hermes_status,'')<>'disabled'
            RETURN labels(a) AS source_labels,labels(b) AS target_labels,type(r) AS type,
                b.uri STARTS WITH $prefix AS local_target,
                [key IN $property_keys | {{key:key,missing:r[key] IS NULL,datatype:valueType(r[key]),
                    finite:CASE WHEN valueType(r[key]) STARTS WITH 'FLOAT' THEN abs(r[key])<=1.7976931348623157e308 ELSE true END,
                    integral:CASE WHEN valueType(r[key]) STARTS WITH 'INTEGER' THEN true
                        WHEN valueType(r[key]) STARTS WITH 'FLOAT' THEN abs(r[key])<=1.7976931348623157e308 AND r[key]=floor(r[key])
                        ELSE false END}}] AS values,count(r) AS count''', params)
        edge_count = 0
        for row in relations:
            edge_count += row['count']
            source = next(key for key, label in labels.items() if label in row['source_labels'])
            target = next((key for key, label in labels.items() if label in row['target_labels']), None)
            for violation in relation_violations(schema, _stored_relation(row['type'], schema), source, target) if row['local_target'] and target else []:
                counts[violation] = counts.get(violation, 0) + row['count']
            values = {value['key']: value for value in row['values']}
            for definition in definitions:
                value, expected = values[definition['key']], definition['range'][0]
                if value['missing']:
                    continue
                datatype = value['datatype'].upper().replace(' ', '')
                valid = (datatype.startswith('STRING') if expected == 'string' else
                         datatype.startswith('BOOLEAN') if expected == 'boolean' else
                         value['integral'] is True if expected == 'integer' else
                         datatype.startswith(('INTEGER', 'FLOAT')) and value['finite'] if expected == 'double' else
                         datatype.startswith(('LOCALDATETIME', 'ZONEDDATETIME', 'DATETIME')) if expected == 'dateTime' else
                         datatype.startswith('DATE') and 'DATETIME' not in datatype if expected == 'date' else False)
                if not valid:
                    violation = ('properties', definition['key'], 'datatype')
                    counts[violation] = counts.get(violation, 0) + row['count']
        return {'checked_nodes': checked, 'checked_relations': edge_count,
                'violations': [{'kind': key[0], 'key': key[1], 'code': key[2], 'count': count} for key, count in sorted(counts.items())]}

    def _affected(self, tx, changes, schema=None):
        from .ontology import _active
        schema = schema or effective_schema(self.config, {'version': 0, 'patch': {}})
        registry = {item['key']: item['storage_label'] for item in schema['entity_types']}
        labels = [registry[item['key']] for item in changes if item['kind'] == 'entity_types' and item['key'] in registry]
        properties = [item['key'] for item in changes if item['kind'] == 'properties']
        relations = {item['key'] for item in changes if item['kind'] == 'relations'}
        aliases = [alias for alias, canonical in RELATION_ALIASES.items() if canonical in relations]
        aliases += [item['storage_type'].upper() for item in schema['relations'] if item['key'] in relations]
        params = {'prefix': self.config.urn_prefix, 'entity_labels': list(registry.values()),
                  'changed_labels': labels, 'changed_properties': properties, 'changed_relations': aliases}
        nodes = list(tx.run(f'''MATCH (n) WHERE {_active('n')}
            AND (any(label IN labels(n) WHERE label IN $changed_labels)
                OR any(key IN $changed_properties WHERE n[key] IS NOT NULL))
            RETURN count(n) AS total''', params))[0]['total'] if labels or properties else 0
        edges = list(tx.run(f'''MATCH (a)-[r]->(b) WHERE {_active('a')} AND {_active('b')}
            AND (toUpper(type(r)) IN $changed_relations
                OR any(key IN $changed_properties WHERE r[key] IS NOT NULL))
            RETURN count(r) AS total''', params))[0]['total'] if aliases or properties else 0
        return {'affected_nodes': nodes, 'affected_relations': edges}

    def _references(self, tx, state, candidate, changes):
        before, after = _definitions(state), _definitions(candidate)
        pending = list(tx.run('''MATCH (b:KGReviewBatch {namespace:$namespace,status:'pending'})
            RETURN b.payload_json AS payload_json,b.ontology_aliases_json AS aliases_json''', {'namespace': self.config.urn_prefix}))
        history = [json.loads(row['state_json']) for row in tx.run('''MATCH (v:KGMemoryRevision {namespace:$namespace})
            RETURN v.state_json AS state_json''', {'namespace': self.config.urn_prefix})]
        payloads = []
        for row in pending:
            frozen = alias_snapshot_state(json.loads(row['aliases_json'] or '{}'))
            payload = json.loads(row['payload_json'])
            payloads.append({'entities': [canonical_input(item, frozen) for item in payload['entities']],
                             'relations': [canonical_input(item, frozen, relation=True) for item in payload['relations']]})
        result = []
        for change in changes:
            kind, key = change['kind'], change['key']
            definition = next(item for item in before[kind] + after[kind] if item['key'] == key)
            label = definition.get('storage_label') if kind == 'entity_types' else None
            prop = key if kind == 'properties' else None
            rel_types = ([alias for alias, value in RELATION_ALIASES.items() if value == key] + [key.upper()]) if kind == 'relations' else []
            params = {'prefix': self.config.urn_prefix, 'label': label, 'property': prop, 'relation_types': rel_types}
            nodes = list(tx.run('''MATCH (n) WHERE n.uri STARTS WITH $prefix
                AND (($label IS NOT NULL AND $label IN labels(n)) OR ($property IS NOT NULL AND n[$property] IS NOT NULL))
                RETURN sum(CASE WHEN n:KGQuarantined OR n:KGLegacyAlias OR n:KGLegacyUnresolved
                    OR coalesce(n.hermes_aliasConfirmed,false) OR NOT coalesce(n.hermes_enabled,true)
                    OR n.hermes_status='disabled' THEN 0 ELSE 1 END) AS active,
                    count(n) AS total''', params))[0] if label or prop else {'active': 0, 'total': 0}
            edges = list(tx.run('''MATCH (a)-[r]->(b) WHERE a.uri STARTS WITH $prefix
                AND (toUpper(type(r)) IN $relation_types OR ($property IS NOT NULL AND r[$property] IS NOT NULL)
                    OR ($label IS NOT NULL AND ($label IN labels(a) OR $label IN labels(b))))
                RETURN sum(CASE WHEN a:KGQuarantined OR b:KGQuarantined OR a:KGLegacyAlias OR b:KGLegacyAlias
                    OR a:KGLegacyUnresolved OR b:KGLegacyUnresolved
                    OR coalesce(a.hermes_aliasConfirmed,false) OR coalesce(b.hermes_aliasConfirmed,false)
                    OR NOT coalesce(a.hermes_enabled,true) OR NOT coalesce(b.hermes_enabled,true)
                    OR a.hermes_status='disabled' OR b.hermes_status='disabled' THEN 0 ELSE 1 END) AS active,
                    count(r) AS total''', params))[0] if label or prop or rel_types else {'active': 0, 'total': 0}
            definitions = []
            if kind == 'entity_types':
                for group in ('properties', 'relations'):
                    definitions.extend(item for item in after[group] if any(key in item.get(field, []) for field in ('domain', 'range', 'required_for')))
            pending_count = 0
            for payload in payloads:
                matches = False
                for entity in payload['entities']:
                    entity_kind = str(entity.get('type', '')).split(':')[-1].lower()
                    matches |= kind == 'entity_types' and entity_kind == key
                    matches |= kind == 'properties' and key in (entity.get('properties', entity.get('props', {})) or {})
                    matches |= kind == 'properties' and key in entity
                    matches |= kind == 'properties' and any(PROPERTY_ALIASES.get(name, name) == key for name in entity)
                    matches |= kind == 'properties' and 'name' in entity and key == ('foaf_name' if entity_kind == 'person' else 'hermes_name')
                    implicit = ('HERMES_hasPreference' if entity_kind == 'preference' else
                                'HERMES_hasDecision' if entity_kind == 'decision' else
                                'HERMES_participant' if entity_kind == 'event' and (entity.get('agent') or entity.get('agent_urn')) else None)
                    matches |= kind == 'relations' and key == implicit
                for relation in payload['relations']:
                    value = relation.get('type', relation.get('rel_type', relation.get('relation_type', '')))
                    matches |= kind == 'relations' and (value.upper() in rel_types if isinstance(value, str) else False)
                    matches |= kind == 'properties' and key in (relation.get('properties', relation.get('props', {})) or {})
                    if kind == 'entity_types':
                        endpoints = [relation.get(name, '') for name in ('from', 'from_urn', 'to', 'to_urn')]
                        matches |= any(isinstance(uri, str) and uri.startswith(self.config.urn_prefix + key + '-') for uri in endpoints)
                pending_count += int(matches)
            history_count = 0
            for revision in history:
                matches = kind == 'entity_types' and label in revision.get('labels', [])
                matches |= kind == 'properties' and key in revision.get('properties', {})
                for relation in revision.get('relations', []):
                    matches |= kind == 'relations' and relation.get('type', '').upper() in rel_types
                    matches |= kind == 'properties' and key in relation.get('properties', {})
                    if kind == 'entity_types':
                        uri = relation.get('to_uri', '')
                        matches |= isinstance(uri, str) and uri.startswith(self.config.urn_prefix + key + '-')
                history_count += int(matches)
            values = {'kind': kind, 'key': key, 'label': definition['label'],
                      'active_nodes': int(nodes['active'] or 0), 'archived_nodes': int(nodes['total']) - int(nodes['active'] or 0),
                      'active_relations': int(edges['active'] or 0), 'archived_relations': int(edges['total']) - int(edges['active'] or 0),
                      'pending_reviews': pending_count, 'definition_references': len(definitions), 'history_revisions': history_count}
            values['reference_count'] = sum(values[field] for field in ('active_nodes', 'archived_nodes', 'active_relations', 'archived_relations', 'pending_reviews', 'definition_references', 'history_revisions'))
            result.append(values)
        return result

    def _alias_conflicts(self, tx, state, candidate):
        previous = aliases_for(state)['properties']
        added = {alias: key for alias, key in aliases_for(candidate)['properties'].items() if previous.get(alias) != key}
        if not added:
            return []
        params = {'prefix': self.config.urn_prefix, 'aliases': sorted(added)}
        counts = {alias: 0 for alias in added}
        for query in ('''UNWIND $aliases AS alias OPTIONAL MATCH (n)
                            WHERE n.uri STARTS WITH $prefix AND n[alias] IS NOT NULL RETURN alias,count(n) AS count''',
                      '''UNWIND $aliases AS alias OPTIONAL MATCH (a)-[r]->(b)
                            WHERE a.uri STARTS WITH $prefix AND r[alias] IS NOT NULL RETURN alias,count(r) AS count'''):
            for row in tx.run(query, params):
                counts[row['alias']] += row['count']
        for row in tx.run('''MATCH (v:KGMemoryRevision {namespace:$prefix}) RETURN v.state_json AS state_json''', params):
            value = json.loads(row['state_json'])
            for alias in added:
                counts[alias] += int(alias in value.get('properties', {}) or any(alias in edge.get('properties', {}) for edge in value.get('relations', [])))
        for row in tx.run('''MATCH (b:KGReviewBatch {namespace:$prefix,status:'pending'})
                            RETURN b.payload_json AS payload_json,b.ontology_aliases_json AS aliases_json''', params):
            value, frozen = json.loads(row['payload_json']), alias_snapshot_state(json.loads(row['aliases_json'] or '{}'))
            entities = [canonical_input(item, frozen) for item in value['entities']]
            relations = [canonical_input(item, frozen, relation=True) for item in value['relations']]
            for alias in added:
                counts[alias] += int(any(alias in item.get('properties', item.get('props', {})) or alias in item for item in entities + relations))
        return [{'kind': 'properties', 'key': added[alias], 'alias': alias, 'code': 'alias_field_in_use',
                 'count': count, 'suggestion': 'choose_different_alias'} for alias, count in sorted(counts.items()) if count]

    def _preview(self, tx, expected_namespace, expected_version, patch, operations=None):
        self._precondition(expected_namespace, expected_version)
        state = state_from_rows(tx.run(STATE_QUERY, {'namespace': self.config.urn_prefix}))
        if state['version'] != expected_version:
            raise OntologyEditError('ontology_version_conflict', version=state['version'])
        candidate, changes = merge_patch(self.config, state, patch, operations)
        return self._preview_candidate(tx, state, candidate, changes, expected_namespace,
                                       expected_version, patch, operations)

    def _preview_candidate(self, tx, state, candidate, changes, expected_namespace,
                           expected_version, patch, operations=None):
        """Validate an assembled candidate, including a trusted packaged preset.

        Public requests still pass through merge_patch's 32-definition budget.
        A preset may compose bounded chunks in memory before this one check.
        """
        structural = any(item['action'] in {'create', 'restore'} or set(item['fields']) & {'domain', 'range', 'required_for'} for item in changes)
        try:
            impact = self._impact(tx, candidate, structural)
            combined = _definitions(state)
            for kind in COLLECTIONS:
                combined[kind] += [item for item in _definitions(candidate)[kind] if item['key'] not in {entry['key'] for entry in combined[kind]}]
            impact.update(self._affected(tx, changes, combined))
            impact['definitions'] = self._references(tx, state, candidate, changes)
            impact['violations'].extend(self._alias_conflicts(tx, state, candidate))
        except OntologyEditError:
            raise
        except Exception as exc:
            raise OntologyEditError('ontology_stats_unavailable', status=503) from exc
        for change in changes:
            if change['action'] in {'create', 'restore'} and change['kind'] in {'properties', 'relations'}:
                schema = effective_schema(self.config, candidate)
                definition = next(item for item in schema[change['kind']] if item['key'] == change['key'])
                inactive = {item['key'] for item in schema['entity_types'] if not item['active']}
                references = set().union(*(set(definition.get(field, [])) for field in ('domain', 'range', 'required_for')))
                if inactive & references:
                    impact['violations'].append({'kind': change['kind'], 'key': change['key'], 'code': 'inactive_reference', 'count': len(inactive & references)})
            if change['action'] == 'delete':
                refs = next(item for item in impact['definitions'] if item['kind'] == change['kind'] and item['key'] == change['key'])
                if refs['reference_count']:
                    impact['violations'].append({'kind': change['kind'], 'key': change['key'], 'code': 'definition_in_use',
                                                 'count': refs['reference_count'], 'suggestion': 'deactivate'})
        high = [item for item in changes if item['action'] in {'deactivate', 'delete'}]
        medium = any(item['action'] in {'create', 'restore'} or set(item['fields']) & {'domain', 'range', 'required_for', 'aliases'} for item in changes)
        level = 'high' if high else 'medium' if medium else 'low'
        reasons = (['停用后历史事实仍保留，新写入将受限。' if item['action'] == 'deactivate' else '删除仅移除无引用的自定义定义，不删除任何事实。' for item in high]
                   if high else ['新增、恢复或结构修改会影响后续数据写入，已核对当前有效事实。'] if medium else ['仅更新定义的展示信息，保留既有事实。'])
        if high and any(item['reference_count'] for item in impact['definitions']):
            reasons.append('已有事实、待审批次或其他定义引用这些定义，请核对影响后确认；有引用的定义不能删除。')
        risk = {'level': level, 'reasons': list(dict.fromkeys(reasons)), 'requires_confirmation': bool(high)}
        if high:
            reference = next(item for item in impact['definitions'] if item['kind'] == high[0]['kind'] and item['key'] == high[0]['key'])
            phrase = ('停用 ' if high[0]['action'] == 'deactivate' else '删除 ') + ' '.join(reference['label'].split()) if len(high) == 1 else f'确认这{len(high)}项高风险变更'
            token = hashlib.sha256(json.dumps([expected_namespace, expected_version, patch, operations or [], impact['definitions']], sort_keys=True).encode()).hexdigest()
            risk.update(confirmation_phrase=phrase, confirmation_token=token)
        return {'namespace': self.config.urn_prefix, 'version': state['version'], 'expected_version': expected_version,
                'can_publish': bool(changes) and not impact['violations'], 'structural': structural,
                'changes': changes, 'impact': impact, 'risk': risk, 'schema': effective_schema(self.config, candidate)}, candidate

    def validate(self, *, expected_namespace, expected_version, patch=None, operations=None):
        self._precondition(expected_namespace, expected_version)
        with self.store.driver.session(database=self.config.database, default_access_mode='READ') as session:
            return self.store._execute(session, 'read', lambda tx: self._preview(tx, expected_namespace, expected_version, patch or {}, operations)[0])

    def publish(self, *, expected_namespace, expected_version, patch=None, operations=None, operation_id, reason='', confirmation=None, confirmation_token=None):
        self._precondition(expected_namespace, expected_version)
        if (not isinstance(operation_id, str) or not 8 <= len(operation_id) <= 160
                or not isinstance(reason, str) or len(reason) > 2000):
            raise OntologyEditError('ontology_invalid_patch', status=400)
        patch = patch or {}
        digest_input = [expected_namespace, expected_version, patch, reason]
        if operations:
            digest_input.append(operations)
        digest = hashlib.sha256(json.dumps(digest_input, sort_keys=True).encode()).hexdigest()
        def save(tx):
            receipts = list(tx.run('''MATCH (r:KGOntologyReceipt {namespace:$namespace,operation_id:$operation_id})
                RETURN r.request_hash AS request_hash,r.result_json AS result_json''',
                {'namespace': self.config.urn_prefix, 'operation_id': operation_id}))
            if len(receipts) > 1:
                raise RuntimeError('Duplicate ontology receipt requires maintenance')
            row = receipts[0] if receipts else None
            if row:
                if row['request_hash'] != digest: raise OntologyEditError('ontology_operation_conflict')
                return {**json.loads(row['result_json']), 'idempotent': True}
            preview, candidate = self._preview(tx, expected_namespace, expected_version, patch, operations)
            if preview['impact']['violations']:
                raise OntologyEditError('ontology_data_conflict', version=preview['version'], impact=preview['impact'])
            if not preview['changes']: raise OntologyEditError('ontology_invalid_patch', status=400)
            if preview['risk']['requires_confirmation'] and (confirmation != preview['risk']['confirmation_phrase'] or confirmation_token != preview['risk']['confirmation_token']):
                raise OntologyEditError('ontology_confirmation_conflict', version=preview['version'], impact=preview['impact'])
            candidate['version'] += 1
            schema = effective_schema(self.config, candidate)
            result = {'ok': True, 'namespace': self.config.urn_prefix, 'version': candidate['version'],
                      'operation_id': operation_id, 'idempotent': False, 'schema': schema}
            params = {'namespace': self.config.urn_prefix, 'version': candidate['version'], 'operation_id': operation_id,
                      'patch_json': json.dumps(candidate['patch'], ensure_ascii=False), 'request_hash': digest,
                      'result_json': json.dumps(result, ensure_ascii=False), 'reason': reason,
                      'presets_json': json.dumps(candidate['presets'], ensure_ascii=False) if 'presets' in candidate else None}
            tx.run('''MERGE (s:KGOntologySchema {namespace:$namespace})
                SET s.version=$version,s.patch_json=$patch_json,s.presets_json=$presets_json,s.updated_at=datetime()
                CREATE (:KGOntologyRevision {namespace:$namespace,version:$version,patch_json:$patch_json,
                    presets_json:$presets_json,operation_id:$operation_id,reason:$reason,created_at:datetime()})
                CREATE (:KGOntologyReceipt {namespace:$namespace,operation_id:$operation_id,
                    request_hash:$request_hash,result_json:$result_json,created_at:datetime()})''', params).consume()
            return result
        with self.store.driver.session(database=self.config.database) as session:
            return self.store._execute(session, 'write', save)
