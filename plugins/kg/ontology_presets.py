"""Explicit, atomic installation of trusted, locally packaged ontology profiles.

Semantic bindings are durable metadata, never editable definition patches or
tool aliases. No function in this module follows an RDF/network import.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
import uuid


COLLECTIONS = ('entity_types', 'properties', 'relations')
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_KEY = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,127}\Z')
_VERSION = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+\Z')
_MAPPINGS = {'reuse', 'local', 'projection', 'annotation', 'subclass', 'subproperty'}
_BINDING_FIELDS = {'iri', 'mapping', 'source', 'source_version', 'direction', 'note'}
_SOURCE_FIELDS = {'id', 'version', 'iri', 'sha256', 'license', 'file', 'format', 'url', 'license_url', 'modified'}


def _text(value, *, limit=2000):
    return isinstance(value, str) and 0 < len(value) <= limit


def _iri(value):
    return _text(value) and value.startswith(('https://', 'http://', 'urn:'))


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def validate_presets(value):
    """Validate self-contained backup metadata without consulting newer bundles."""
    if not isinstance(value, dict) or len(value) > 8:
        raise ValueError('Invalid ontology preset metadata')
    allowed = {'id', 'version', 'digest', 'sources', 'installed_keys', 'bindings', 'constraints', 'provenance'}
    for preset_id, item in value.items():
        if (not isinstance(preset_id, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}', preset_id)
                or not isinstance(item, dict) or not set(item) <= allowed or item.get('id') != preset_id
                or not isinstance(item.get('version'), str) or len(item['version']) > 64 or not _VERSION.fullmatch(item['version'])
                or not isinstance(item.get('digest'), str) or not _HASH.fullmatch(item['digest'])):
            raise ValueError('Invalid ontology preset identity')
        if not isinstance(item.get('sources'), list) or len(item['sources']) > 32 or any(not isinstance(source, dict) for source in item['sources']):
            raise ValueError('Invalid ontology preset sources')
        source_versions = set()
        for source in item['sources']:
            if (not {'id', 'version', 'iri', 'sha256', 'license'} <= source.keys()
                    or not set(source) <= _SOURCE_FIELDS
                    or not _text(source['id'], limit=64) or not _text(source['version'], limit=64)
                    or not _iri(source['iri']) or not _text(source['license'], limit=240)
                    or not isinstance(source['sha256'], str) or not _HASH.fullmatch(source['sha256'])
                    or any(not _text(source[field]) or not source[field].startswith(('https://', 'http://'))
                           for field in ('url', 'license_url') if field in source)
                    or ('file' in source and not _text(source['file']))
                    or ('format' in source and (not isinstance(source['format'], str) or source['format'] not in {'xml', 'turtle', 'nt'}))
                    or ('modified' in source and not isinstance(source['modified'], bool))):
                raise ValueError('Invalid ontology preset source declaration')
            source_versions.add((source['id'], source['version']))
        keys, bindings, constraints = (item.get(field) for field in ('installed_keys', 'bindings', 'constraints'))
        if any(not isinstance(group, dict) or not set(group) <= set(COLLECTIONS) for group in (keys, bindings, constraints)):
            raise ValueError('Invalid ontology preset definitions')
        for collection in COLLECTIONS:
            installed = keys.get(collection, [])
            if (not isinstance(installed, list) or len(installed) > 1024
                    or any(not isinstance(key, str) or not _KEY.fullmatch(key) for key in installed)
                    or len(set(installed)) != len(installed)):
                raise ValueError('Invalid ontology preset installed keys')
            group = bindings.get(collection, {})
            if not isinstance(group, dict) or not set(group) <= set(installed):
                raise ValueError('Invalid ontology preset bindings')
            for binding in group.values():
                if (not isinstance(binding, dict) or not set(binding) <= _BINDING_FIELDS
                        or not _iri(binding.get('iri'))
                        or not isinstance(binding.get('mapping'), str) or binding['mapping'] not in _MAPPINGS
                        or not _text(binding.get('source'), limit=64)
                        or not _text(binding.get('source_version'), limit=64)
                        or (binding['source'] != 'personal' and (binding['source'], binding['source_version']) not in source_versions)
                        or not isinstance(binding.get('direction', 'forward'), str)
                        or binding.get('direction', 'forward') not in {'forward', 'reverse'}
                        or ('note' in binding and (not isinstance(binding['note'], str) or len(binding['note']) > 4000))):
                    raise ValueError('Invalid ontology semantic binding')
            floors = constraints.get(collection, {})
            if not isinstance(floors, dict) or not set(floors) <= set(group):
                raise ValueError('Invalid ontology semantic constraints')
            for fields in floors.values():
                if (not isinstance(fields, dict) or not set(fields) <= {'domain', 'range', 'required_for'}
                        or any(not isinstance(entries, list) or not entries
                               or any(not isinstance(entry, str) for entry in entries)
                               for name, entries in fields.items() if name != 'required_for')
                        or ('required_for' in fields and (not isinstance(fields['required_for'], list)
                            or any(not isinstance(entry, str) for entry in fields['required_for'])))):
                    raise ValueError('Invalid ontology semantic constraint fields')
        if 'provenance' in item and (not isinstance(item['provenance'], dict)
                or len(item['provenance']) > 64
                or any(not _text(key, limit=64) or not _iri(term) for key, term in item['provenance'].items())):
            raise ValueError('Invalid ontology provenance projection')
    # Bounds and JSON-only values also prevent nested executable objects from
    # entering a runtime schema through a malformed backup.
    if len(_json(value)) > 1_000_000:
        raise ValueError('Ontology preset metadata is too large')
    return deepcopy(value)


def semantic_bindings(presets):
    result = {collection: {} for collection in COLLECTIONS}
    for preset_id, item in validate_presets(presets).items():
        for collection, bindings in item['bindings'].items():
            for key, binding in bindings.items():
                if key in result[collection]:
                    raise ValueError('Conflicting ontology semantic bindings')
                result[collection][key] = {**deepcopy(binding), 'preset_id': preset_id,
                                            'profile_version': item['version'], 'profile_digest': item['digest']}
    return result


def preset_digest(presets):
    return hashlib.sha256(_json(validate_presets(presets)).encode('utf-8')).hexdigest()


def validate_semantic_constraints(state):
    """A bound standard definition may tighten its selected application shape."""
    if not state.get('presets'):
        return
    from .ontology_editing import _definitions, _types, OntologyEditError
    definitions = _definitions(state)
    for item in validate_presets(state['presets']).values():
        for collection, floors in item['constraints'].items():
            current = {entry['key']: entry for entry in definitions[collection]}
            for key, floor in floors.items():
                if key not in current:  # User deletion is durable, not an invitation to recreate it.
                    continue
                definition = current[key]
                for field in ('domain', 'range') if collection == 'relations' else ('domain',) if collection == 'properties' else ():
                    if field in floor and not _types(definition[field], definitions) <= _types(floor[field], definitions):
                        raise OntologyEditError('ontology_semantic_conflict', status=400)
                if collection == 'properties':
                    old_type, new_type = floor.get('range'), definition.get('range')
                    if old_type and new_type != old_type and not (old_type == ['double'] and new_type == ['integer']):
                        raise OntologyEditError('ontology_semantic_conflict', status=400)
                    if not set(floor.get('required_for', [])) <= set(definition.get('required_for', [])):
                        raise OntologyEditError('ontology_semantic_conflict', status=400)


def _profile():
    from .ontology_profile import load_personal_profile
    return load_personal_profile()


def _assemble(config, state, profile):
    from .ontology_editing import _definitions, merge_patch
    from .ontology import _schema
    presets = validate_presets(state.get('presets', {}))
    previous = presets.get(profile['id'])
    if not isinstance(profile.get('version'), str) or not _VERSION.fullmatch(profile['version']):
        raise ValueError('Invalid packaged ontology preset release version')
    if previous and tuple(map(int, profile['version'].split('.'))) < tuple(map(int, previous['version'].split('.'))):
        raise ValueError('An older ontology preset cannot replace an installed version')
    if previous and previous['version'] == profile['version']:
        if previous['digest'] != profile['digest']:
            raise ValueError('A published ontology preset version has a different content digest')
        return deepcopy(state), [], []
    candidate = deepcopy(state)
    existing = {collection: {item['key'] for item in _definitions(state)[collection]} for collection in COLLECTIONS}
    installed = deepcopy(previous['installed_keys']) if previous else {collection: [] for collection in COLLECTIONS}
    pending = []
    for operation in profile['operations']:
        collection, key = operation['kind'], operation['key']
        if operation.get('action') != 'create' or collection not in COLLECTIONS:
            raise ValueError('Packaged ontology presets may only create definitions')
        if key in installed.get(collection, []):
            continue
        if key in existing[collection]:
            raise ValueError(f'Ontology preset definition conflicts with an existing key: {collection}/{key}')
        pending.append(deepcopy(operation))
        installed.setdefault(collection, []).append(key)
    ordered = [operation for collection in COLLECTIONS for operation in pending if operation['kind'] == collection]
    changes = []
    # Type identities must exist before property or relationship constraints.
    # Every chunk is in-memory; only the final candidate is ever published.
    for collection in COLLECTIONS:
        operations = [operation for operation in ordered if operation['kind'] == collection]
        for offset in range(0, len(operations), 32):
            candidate, changed = merge_patch(config, candidate, {}, operations[offset:offset + 32])
            changes.extend(changed)
    bindings = deepcopy(previous['bindings']) if previous else {collection: {} for collection in COLLECTIONS}
    constraints = deepcopy(previous['constraints']) if previous else {collection: {} for collection in COLLECTIONS}
    actual = {collection: {item['key']: item for item in _definitions(candidate)[collection]} for collection in COLLECTIONS}
    core = {collection: {item['key']: item for item in _schema()[collection]} for collection in COLLECTIONS}
    for collection, entries in profile['bindings'].items():
        for key, binding in entries.items():
            if key not in actual[collection] or key in bindings.setdefault(collection, {}):
                continue
            bindings[collection][key] = deepcopy(binding)
            if key not in installed.setdefault(collection, []):
                installed[collection].append(key)
            if binding['mapping'] not in {'local', 'annotation'}:
                fields = ('domain', 'range', 'required_for') if collection == 'properties' else ('domain', 'range') if collection == 'relations' else ()
                # Existing compatible user narrowing is not an upstream rule.
                # Preserve the canonical floor, so users can later relax their
                # own restriction within the originally selected semantics.
                baseline = core[collection].get(key, actual[collection][key])
                constraints.setdefault(collection, {})[key] = {field: deepcopy(baseline[field]) for field in fields}
    sources = deepcopy(previous.get('sources', [])) if previous else []
    for source in profile['sources']:
        if source not in sources:
            sources.append(deepcopy(source))
    metadata = {'id': profile['id'], 'version': profile['version'], 'digest': profile['digest'],
                'sources': sources, 'installed_keys': {key: sorted(value) for key, value in installed.items()},
                'bindings': bindings, 'constraints': constraints}
    if profile.get('provenance'):
        metadata['provenance'] = deepcopy(previous.get('provenance', profile['provenance'])) if previous else deepcopy(profile['provenance'])
    presets[profile['id']] = metadata
    candidate['presets'] = validate_presets(presets)
    validate_semantic_constraints(candidate)
    return candidate, changes, ordered


def _install_in_transaction(store, tx, profile):
    from .ontology_editing import STATE_QUERY, state_from_rows, OntologyEditor, OntologyEditError, effective_schema
    namespace = store.config.urn_prefix
    state = state_from_rows(tx.run(STATE_QUERY, {'namespace': namespace}))
    candidate, changes, operations = _assemble(store.config, state, profile)
    if candidate == state:
        return {'ok': True, 'namespace': namespace, 'preset': profile['id'], 'version': state['version'],
                'idempotent': True, 'changes': [], 'schema': effective_schema(store.config, state)}
    editor = OntologyEditor(store.config, store)
    preview, _ = editor._preview_candidate(tx, state, candidate, changes, namespace, state['version'], {}, operations)
    if preview['impact']['violations']:
        raise OntologyEditError('ontology_data_conflict', version=state['version'], impact=preview['impact'])
    return {'ok': True, 'namespace': namespace, 'preset': profile['id'], 'version': state['version'],
            'idempotent': False, 'changes': changes, 'preview': preview, '_candidate': candidate}


def _commit(store, tx, profile, result):
    from .ontology_editing import effective_schema
    candidate = result.pop('_candidate', None)
    if candidate is None:
        return result
    result.pop('preview', None)
    candidate['version'] += 1
    operation_id = 'preset-' + profile['id'] + '-' + profile['digest']
    result.update(version=candidate['version'], operation_id=operation_id, schema=effective_schema(store.config, candidate))
    params = {'namespace': store.config.urn_prefix, 'version': candidate['version'], 'operation_id': operation_id,
              'patch_json': _json(candidate['patch']), 'presets_json': _json(candidate['presets']),
              'request_hash': profile['digest'], 'result_json': _json(result), 'reason': 'Install packaged personal ontology preset'}
    tx.run('''MERGE (s:KGOntologySchema {namespace:$namespace})
        SET s.version=$version,s.patch_json=$patch_json,s.presets_json=$presets_json,s.updated_at=datetime()
        CREATE (:KGOntologyRevision {namespace:$namespace,version:$version,patch_json:$patch_json,
            presets_json:$presets_json,operation_id:$operation_id,reason:$reason,created_at:datetime()})
        CREATE (:KGOntologyReceipt {namespace:$namespace,operation_id:$operation_id,
            request_hash:$request_hash,result_json:$result_json,created_at:datetime()})''', params).consume()
    return result


def install_preset(store, preset='personal', *, dry_run=False):
    """Install only the allowlisted packaged preset; preview performs no writes."""
    if preset != 'personal':
        raise ValueError('Unknown ontology preset')
    profile = _profile()
    if not dry_run:
        store.ensure_schema()
    with store.driver.session(database=store.config.database, **({'default_access_mode': 'READ'} if dry_run else {})) as session:
        def apply(tx):
            result = _install_in_transaction(store, tx, profile)
            if dry_run:
                result.pop('_candidate', None)
                return {**result, 'dry_run': True}
            return _commit(store, tx, profile, result)
        return store._execute(session, 'read' if dry_run else 'write', apply)


def initialize_personal_preset(store):
    """Claim and initialize a genuinely empty namespace in one transaction.

    Unlike normal writers this must observe whether KGWorkspace already existed.
    The same unique node is locked before inspecting facts/history. Creation,
    the complete profile, and its receipt commit together or all roll back.
    """
    from .maintenance import assert_not_restoring, RestoreInProgress
    from .store import _SafeTransaction
    from neo4j import unit_of_work
    assert_not_restoring(store.config.home)
    attempt = uuid.uuid4().hex
    namespace = store.config.urn_prefix

    @unit_of_work(timeout=store.config.query_timeout)
    def initialize(transaction):
        tx = _SafeTransaction(transaction)
        row = tx.run('''MERGE (w:KGWorkspace {namespace:$namespace})
            ON CREATE SET w.preset_bootstrap_attempt=$attempt
            SET w.lock_counter=coalesce(w.lock_counter,0)+1
            RETURN w.preset_bootstrap_attempt AS attempt,coalesce(w.restore_pending,false) AS restore_pending''',
            {'namespace': namespace, 'attempt': attempt}).single()
        if row and row.get('restore_pending'):
            raise RestoreInProgress('This namespace has an incomplete restore; resume it before writing')
        assert_not_restoring(store.config.home)
        if not row or row.get('attempt') != attempt:
            return {'ok': True, 'status': 'existing_namespace', 'installed': False}
        existing = tx.run('''MATCH (n) WHERE NOT n:KGWorkspace AND
            (n.uri STARTS WITH $namespace OR n.namespace=$namespace OR
             (n:KGSyncReceipt AND any(uri IN coalesce(n.entity_uris,[]) WHERE uri STARTS WITH $namespace)))
            RETURN count(n) AS count''', {'namespace': namespace}).single()
        if existing and existing.get('count', 0):
            result = {'ok': True, 'status': 'existing_namespace', 'installed': False}
        else:
            # Existing spaces opted out of automatic installation. They must
            # not depend on compiling this release's optional preset bundle.
            profile = _profile()
            result = _commit(store, tx, profile, _install_in_transaction(store, tx, profile))
            result.update(status='initialized', installed=True)
        tx.run('''MATCH (w:KGWorkspace {namespace:$namespace}) REMOVE w.preset_bootstrap_attempt''',
               {'namespace': namespace}).consume()
        return result

    with store.driver.session(database=store.config.database) as session:
        return session.execute_write(initialize)
