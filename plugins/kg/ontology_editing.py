"""Versioned namespace overlays. Packaged ontology and runtime IDs stay immutable."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
import math
import re

from .schema import ENTITY_LABELS, PROPERTY_ALIASES, RELATION_ALIASES, RESERVED_PROPERTIES

COLLECTIONS = ('entity_types', 'properties', 'relations')
FIELDS = {'entity_types': {'label', 'description'},
          'properties': {'label', 'description', 'aliases', 'required_for', 'domain', 'range'},
          'relations': {'label', 'description', 'aliases', 'domain', 'range'}}
STATE_QUERY = '''MATCH (s:KGOntologySchema {namespace:$namespace})
    RETURN s.version AS version,s.patch_json AS patch_json'''


class OntologyEditError(ValueError):
    def __init__(self, code, *, version=None, impact=None, status=409):
        super().__init__(code)
        self.code, self.version, self.impact, self.status = code, version, impact, status

    def response(self):
        messages = {'ontology_version_conflict': '本体已发生变化，请读取最新版本后再核对。',
                    'ontology_data_conflict': '现有数据与拟发布约束冲突，请先修正数据或调整约束。',
                    'ontology_operation_conflict': '此操作标识已用于其他本体修改。',
                    'ontology_namespace_conflict': '工作空间已变化，未提交本体修改。',
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
    return {'version': int(rows[0]['version']), 'patch': json.loads(rows[0].get('patch_json') or '{}')}


def capabilities(base):
    readonly = [item['key'] for item in base['properties'] if item['managed']]
    return {'scope': 'namespace_overlay', 'can_edit': True, 'can_add_types': False,
            'can_change_storage_ids': False, 'structural_policy': 'tighten_only',
            'structural_relation_scope': 'current_namespace_active_endpoints',
            'alias_input_scope': 'properties_object_and_relation_type',
            'alias_pattern': '[A-Za-z][A-Za-z0-9_]{0,63}',
            'integer_policy': 'finite_integral_numeric_value',
            'entity_fields': sorted(FIELDS['entity_types']), 'property_fields': sorted(FIELDS['properties']),
            'relation_fields': sorted(FIELDS['relations']), 'readonly_properties': readonly,
            'immutable_keys': ['key', 'iri', 'storage_label', 'storage_type', 'runtime_supported', 'managed', 'value_shape'],
            'builtin_constraints': {
                'properties': {item['key']: {field: deepcopy(item[field]) for field in ('domain', 'range', 'required_for', 'aliases')}
                               for item in base['properties']},
                'relations': {item['key']: {field: deepcopy(item[field]) for field in ('domain', 'range', 'aliases')}
                              for item in base['relations']}},
            'datatypes_by_property': {item['key']: [] if item['managed'] else
                ['double', 'integer'] if item['key'] in {'geo_lat', 'geo_long'} else item['range']
                for item in base['properties']}}


def effective_schema(config, state):
    from .ontology import _schema, SCOPE
    result = deepcopy(_schema())
    for collection in COLLECTIONS:
        for item in result[collection]:
            custom = state['patch'].get(collection, {}).get(item['key'], {})
            item.update(deepcopy(custom))
            item['customized_fields'] = sorted(custom)
            item['count'] = None
    for entity in result['entity_types']:
        entity['properties'] = [deepcopy(item) for item in result['properties']
                                if 'any' in item['domain'] or entity['key'] in item['domain']]
    result.update(namespace=config.urn_prefix, scope=SCOPE, version=state['version'], read_only=False,
                  editing=capabilities(_schema()), stats_available=False, stats_error=None,
                  stats={'entities': None, 'relationships': None, 'unmapped_relationships': None})
    return result


def _types(values):
    return set(ENTITY_LABELS) if 'any' in values else set(values)


def merge_patch(config, state, patch):
    from .ontology import _schema
    if not isinstance(patch, dict) or not set(patch) <= set(COLLECTIONS):
        raise OntologyEditError('ontology_invalid_patch', status=400)
    base, merged, changes = _schema(), deepcopy(state['patch']), []
    for collection, definitions in patch.items():
        if not isinstance(definitions, dict) or len(definitions) > 32:
            raise OntologyEditError('ontology_invalid_patch', status=400)
        known = {item['key']: item for item in base[collection]}
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
                    normalized[field] = value.strip() if field == 'label' else value
                else:
                    if (not isinstance(value, list) or len(value) > (24 if field == 'aliases' else 6)
                            or any(not isinstance(item, str) or not item or len(item) > 64 for item in value)):
                        raise OntologyEditError('ontology_invalid_patch', status=400)
                    normalized[field] = sorted(set(value))
            previous = {**known[key], **merged.get(collection, {}).get(key, {})}
            updated = {**previous, **normalized}
            if collection in {'properties', 'relations'}:
                for field in ('domain', 'range') if collection == 'relations' else ('domain',):
                    values = updated[field]
                    if not values or not set(values) <= set(ENTITY_LABELS) | {'any'} or not _types(values) <= _types(known[key][field]):
                        raise OntologyEditError('ontology_invalid_patch', status=400)
                if 'aliases' in normalized and not set(known[key].get('aliases', [])) <= set(normalized['aliases']):
                    raise OntologyEditError('ontology_invalid_patch', status=400)
            if collection == 'properties':
                if (not set(updated['required_for']) <= set(ENTITY_LABELS)
                        or not set(known[key]['required_for']) <= set(updated['required_for'])
                        or not set(updated['required_for']) <= _types(updated['domain'])
                        or len(updated['range']) != 1
                        or updated['range'][0] not in capabilities(base)['datatypes_by_property'][key]):
                    raise OntologyEditError('ontology_invalid_patch', status=400)
            actual = sorted(field for field, value in normalized.items()
                            if (set(previous.get(field, [])) != set(value) if isinstance(value, list)
                                else previous.get(field) != value))
            if actual:
                merged.setdefault(collection, {}).setdefault(key, {}).update({field: normalized[field] for field in actual})
                changes.append({'kind': collection, 'key': key, 'fields': actual})
    candidate = {'version': state['version'], 'patch': merged}
    # Validate all custom aliases together, including previously published ones.
    aliases_for(candidate)
    return candidate, changes


def aliases_for(state):
    from .ontology import _schema
    result = {'properties': {}, 'relations': {}}
    base = _schema()
    for collection in result:
        known = {item['key']: item for item in base[collection]}
        for key, fields in state['patch'].get(collection, {}).items():
            for alias in set(fields.get('aliases', [])) - set(known[key].get('aliases', [])):
                normalized = alias.upper() if collection == 'relations' else alias
                forbidden = (set(RESERVED_PROPERTIES) | set(PROPERTY_ALIASES) | set(PROPERTY_ALIASES.values()) |
                             {item['key'] for item in base['properties']} | {'type', 'uri', 'id', 'urn', 'name', 'properties', 'props'})
                if (not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', alias)
                        or alias.lower() in {'uri', 'type', 'id', 'urn', 'name', 'properties', 'props', 'namespace', 'operation_id', 'expected_version'}
                        or (collection == 'properties' and (alias in forbidden or alias.lower().startswith(('hermes_', 'foaf_', 'geo_'))))
                        or (collection == 'relations' and normalized in RELATION_ALIASES)
                        or (normalized in result[collection] and result[collection][normalized] != key)):
                    raise OntologyEditError('ontology_invalid_patch', status=400)
                result[collection][normalized] = key
    return result


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
            if not isinstance(alias, str) or not isinstance(key, str) or key not in known:
                raise ValueError('Invalid review ontology alias snapshot')
            definition = patch.setdefault(collection, {}).setdefault(key, {'aliases': list(known[key]['aliases'])})
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
    if datatype == 'double': return isinstance(value, (int, float)) and not isinstance(value, bool)
    if datatype == 'dateTime': return isinstance(value, datetime) or type(value).__name__ == 'DateTime'
    if datatype == 'date': return isinstance(value, date) or type(value).__name__ == 'Date'
    return False


def entity_violations(schema, kind, props):
    violations = []
    for item in schema['properties']:
        if not set(item['customized_fields']) & {'domain', 'range', 'required_for'}:
            continue
        key, value = item['key'], props.get(item['key'])
        unknown_event = (key == 'hermes_eventTime' and kind == 'event'
                         and props.get('hermes_timePrecision') == 'unknown' and props.get('hermes_eventStatus') == 'occurred')
        if kind in item['required_for'] and (value is None or (isinstance(value, str) and not value.strip())) and not unknown_event:
            violations.append(('properties', key, 'required'))
        if value is not None:
            if kind not in _types(item['domain']): violations.append(('properties', key, 'domain'))
            values = value if item.get('value_shape') == 'array' and isinstance(value, list) else [value]
            if not all(_matches_type(value, item['range'][0]) for value in values): violations.append(('properties', key, 'datatype'))
    return violations


def relation_violations(schema, kind, source, target):
    item = next((item for item in schema['relations'] if item['key'] == kind), None)
    if item and set(item['customized_fields']) & {'domain', 'range'}:
        if source not in _types(item['domain']) or target not in _types(item['range']):
            return [('relations', kind, 'endpoints')]
    return []


def validate_batch(config, state, entities, relations, current):
    if not state['patch']:
        return
    schema = effective_schema(config, state)
    incoming = {item['uri']: item for item in entities}
    for item in entities:
        props = {**(current.get(item['uri']) or {}).get('properties', {}), **item['properties']}
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
        return next((key for key, label in ENTITY_LABELS.items() if label in (current.get(uri) or {}).get('labels', [])), None)
    for rel in relations:
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
        properties = [item['key'] for item in schema['properties'] if set(item['customized_fields']) & {'domain', 'range', 'required_for'}]
        params = {'prefix': self.config.urn_prefix, 'entity_labels': list(ENTITY_LABELS.values()),
                  'property_keys': sorted(set(properties) | {'hermes_timePrecision', 'hermes_eventStatus'})}
        rows = tx.run(f'''MATCH (n) WHERE {_active('n')}
            RETURN labels(n) AS labels,[key IN $property_keys | {{key:key,missing:n[key] IS NULL,
                empty:CASE WHEN valueType(n[key]) STARTS WITH 'STRING' THEN trim(n[key])='' ELSE false END,
                integral:CASE WHEN valueType(n[key]) STARTS WITH 'INTEGER' THEN true
                    WHEN valueType(n[key]) STARTS WITH 'FLOAT' THEN abs(n[key])<=1.7976931348623157e308 AND n[key]=floor(n[key])
                    ELSE false END,datatype:valueType(n[key])}}] AS values,
                n.hermes_timePrecision='unknown' AND n.hermes_eventStatus='occurred' AS unknown_event''', params)
        counts, checked = {}, 0
        for row in rows:
            checked += 1
            kind = next(key for key, label in ENTITY_LABELS.items() if label in row['labels'])
            values = {value['key']: value for value in row['values']}
            for definition in schema['properties']:
                if not set(definition['customized_fields']) & {'domain', 'range', 'required_for'}:
                    continue
                key = definition['key']
                value = values[key]
                issues = []
                unknown_event = key == 'hermes_eventTime' and kind == 'event' and row.get('unknown_event') is True
                if kind in definition['required_for'] and (value['missing'] or value['empty'] is True) and not unknown_event:
                    issues.append('required')
                if not value['missing']:
                    if kind not in _types(definition['domain']): issues.append('domain')
                    datatype = value['datatype'].upper().replace(' ', '')
                    expected = definition['range'][0]
                    valid = (datatype.startswith('STRING') if expected == 'string' else
                             datatype.startswith('BOOLEAN') if expected == 'boolean' else
                             value['integral'] is True if expected == 'integer' else
                             datatype.startswith(('INTEGER', 'FLOAT')) if expected == 'double' else
                             'DATETIME' in datatype if expected == 'dateTime' else
                             datatype.startswith('DATE') if expected == 'date' else False)
                    if not valid: issues.append('datatype')
                for code in issues:
                    violation = ('properties', key, code)
                    counts[violation] = counts.get(violation, 0) + 1
        relations = tx.run(f'''MATCH (a)-[r]->(b) WHERE {_active('a')} AND {_active('b')}
            RETURN labels(a) AS source_labels,labels(b) AS target_labels,type(r) AS type,count(r) AS count''', params)
        edge_count = 0
        for row in relations:
            edge_count += row['count']
            source = next(key for key, label in ENTITY_LABELS.items() if label in row['source_labels'])
            target = next(key for key, label in ENTITY_LABELS.items() if label in row['target_labels'])
            for violation in relation_violations(schema, _stored_relation(row['type']), source, target):
                counts[violation] = counts.get(violation, 0) + row['count']
        return {'checked_nodes': checked, 'checked_relations': edge_count,
                'violations': [{'kind': key[0], 'key': key[1], 'code': key[2], 'count': count} for key, count in sorted(counts.items())]}

    def _affected(self, tx, changes):
        from .ontology import _active
        labels = [ENTITY_LABELS[item['key']] for item in changes if item['kind'] == 'entity_types']
        properties = [item['key'] for item in changes if item['kind'] == 'properties']
        relations = {item['key'] for item in changes if item['kind'] == 'relations'}
        aliases = [alias for alias, canonical in RELATION_ALIASES.items() if canonical in relations]
        params = {'prefix': self.config.urn_prefix, 'entity_labels': list(ENTITY_LABELS.values()),
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

    def _preview(self, tx, expected_namespace, expected_version, patch):
        self._precondition(expected_namespace, expected_version)
        state = state_from_rows(tx.run(STATE_QUERY, {'namespace': self.config.urn_prefix}))
        if state['version'] != expected_version:
            raise OntologyEditError('ontology_version_conflict', version=state['version'])
        candidate, changes = merge_patch(self.config, state, patch)
        structural = any(set(fields) & {'domain', 'range', 'required_for'} for group in candidate['patch'].values() for fields in group.values())
        impact = self._impact(tx, candidate, structural)
        impact.update(self._affected(tx, changes))
        return {'namespace': self.config.urn_prefix, 'version': state['version'], 'expected_version': expected_version,
                'can_publish': bool(changes) and not impact['violations'], 'structural': structural,
                'changes': changes, 'impact': impact, 'schema': effective_schema(self.config, candidate)}, candidate

    def validate(self, *, expected_namespace, expected_version, patch):
        self._precondition(expected_namespace, expected_version)
        with self.store.driver.session(database=self.config.database, default_access_mode='READ') as session:
            return self.store._execute(session, 'read', lambda tx: self._preview(tx, expected_namespace, expected_version, patch)[0])

    def publish(self, *, expected_namespace, expected_version, patch, operation_id, reason=''):
        self._precondition(expected_namespace, expected_version)
        if (not isinstance(operation_id, str) or not 8 <= len(operation_id) <= 160
                or not isinstance(reason, str) or len(reason) > 2000):
            raise OntologyEditError('ontology_invalid_patch', status=400)
        digest = hashlib.sha256(json.dumps([expected_namespace, expected_version, patch, reason], sort_keys=True).encode()).hexdigest()
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
            preview, candidate = self._preview(tx, expected_namespace, expected_version, patch)
            if preview['impact']['violations']:
                raise OntologyEditError('ontology_data_conflict', version=preview['version'], impact=preview['impact'])
            if not preview['changes']: raise OntologyEditError('ontology_invalid_patch', status=400)
            candidate['version'] += 1
            schema = effective_schema(self.config, candidate)
            result = {'ok': True, 'namespace': self.config.urn_prefix, 'version': candidate['version'],
                      'operation_id': operation_id, 'idempotent': False, 'schema': schema}
            params = {'namespace': self.config.urn_prefix, 'version': candidate['version'], 'operation_id': operation_id,
                      'patch_json': json.dumps(candidate['patch'], ensure_ascii=False), 'request_hash': digest,
                      'result_json': json.dumps(result, ensure_ascii=False), 'reason': reason}
            tx.run('''MERGE (s:KGOntologySchema {namespace:$namespace})
                SET s.version=$version,s.patch_json=$patch_json,s.updated_at=datetime()
                CREATE (:KGOntologyRevision {namespace:$namespace,version:$version,patch_json:$patch_json,
                    operation_id:$operation_id,reason:$reason,created_at:datetime()})
                CREATE (:KGOntologyReceipt {namespace:$namespace,operation_id:$operation_id,
                    request_hash:$request_hash,result_json:$result_json,created_at:datetime()})''', params).consume()
            return result
        with self.store.driver.session(database=self.config.database) as session:
            return self.store._execute(session, 'write', save)
