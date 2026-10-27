"""Explicit, evidence-bound ontology reclassification of existing memories.

This is maintenance, not extraction. A reviewed local plan selects the types;
the application never guesses types or promotes layout edges into facts. Old
typed URIs and their relationships remain as hidden, resolvable history.
"""
from __future__ import annotations

import hashlib
import json

from .lifecycle import VersionConflict, dumps, validate_operation_id, validate_version
from .maintenance import encode
from .ontology_editing import STATE_QUERY, effective_schema, state_from_rows, _matches_type
from .schema import (RESERVED_PROPERTIES, canonical_urn, normalize_rel, prepare_entity,
                     runtime_entity_labels, runtime_kind)

EXCLUDED = {'KGQuarantined', 'KGLegacyAlias', 'KGLegacyUnresolved'}


def digest(value):
    return hashlib.sha256(dumps(encode(value)).encode('utf-8')).hexdigest()


def edge_key(edge):
    return digest({key: edge[key] for key in ('source', 'target', 'type', 'properties')})


def _snapshot(tx, store):
    state = state_from_rows(tx.run(STATE_QUERY, {'namespace': store.config.urn_prefix}))
    schema = effective_schema(store.config, state)
    labels = list(runtime_entity_labels(schema).values())
    nodes = [dict(row) for row in tx.run('''MATCH (n)
        WHERE n.uri STARTS WITH $prefix AND any(label IN labels(n) WHERE label IN $labels)
          AND NOT n:KGQuarantined AND NOT n:KGLegacyAlias AND NOT n:KGLegacyUnresolved
          AND coalesce(n.hermes_aliasConfirmed,false)=false
          AND coalesce(n.hermes_enabled,true) AND coalesce(n.hermes_status,'')<>'disabled'
        RETURN labels(n) AS labels,properties(n) AS properties ORDER BY n.uri''',
        {'prefix': store.config.urn_prefix, 'labels': labels})]
    uris = [node['properties']['uri'] for node in nodes]
    edges = [dict(row) for row in tx.run('''MATCH (a)-[r]->(b)
        WHERE a.uri STARTS WITH $prefix AND any(label IN labels(a) WHERE label IN $labels)
          AND (a.uri IN $uris OR b.uri IN $uris) AND b.uri IS NOT NULL
        RETURN elementId(r) AS id,a.uri AS source,b.uri AS target,type(r) AS type,
          properties(r) AS properties''', {'prefix': store.config.urn_prefix, 'labels': labels, 'uris': uris})]
    aliases = [dict(row) for row in tx.run('''MATCH (n:KGQuarantined)
        WHERE n.uri STARTS WITH $prefix AND n.hermes_aliasConfirmed=true
          AND n.hermes_canonicalUri IN $uris
        RETURN labels(n) AS labels,properties(n) AS properties ORDER BY n.uri''',
        {'prefix': store.config.urn_prefix, 'uris': uris})]
    fingerprint = digest({'nodes': sorted((digest(node) for node in nodes)),
                          'edges': sorted(edge_key(edge) for edge in edges),
                          'aliases': sorted(digest(node) for node in aliases)})
    return {'nodes': nodes, 'edges': edges, 'aliases': aliases, 'schema': schema, 'snapshot_sha256': fingerprint}


def classification_snapshot(store):
    """Private assessment input. Callers must not publish personal contents."""
    with store.driver.session(database=store.config.database) as session:
        return store._execute(session, 'read', _snapshot, store)


def target_uri(config, uri, kind, schema):
    # Names can collide; the old identity determines an immutable new identity.
    slug = 'reclassified-' + hashlib.sha256(uri.encode()).hexdigest()[:24]
    return canonical_urn(slug, kind, prefix=config.urn_prefix, ontology=schema)


def _target_properties(config, node, kind, schema):
    old = node['properties']
    old_kind = runtime_kind(node['labels'], schema)
    name = old.get('foaf_name' if old_kind == 'person' else 'hermes_name')
    properties = {}
    for definition in schema['properties']:
        key = definition['key']
        if (key in old and key not in RESERVED_PROPERTIES and definition.get('active', True)
                and ('any' in definition['domain'] or kind in definition['domain'])):
            properties[key] = old[key]
    properties.pop('foaf_name' if kind != 'person' else 'hermes_name', None)
    prepared, _ = prepare_entity({'type': kind, 'id': 'validation-only', 'name': name,
                                 'props': properties}, config, schema)
    properties = prepared['properties']
    for definition in schema['properties']:
        key = definition['key']
        if kind in definition['required_for'] and (properties.get(key) is None or properties.get(key) == ''):
            raise ValueError('Reclassification target is missing a required property')
        if key in properties:
            values = properties[key] if definition.get('value_shape') == 'array' else [properties[key]]
            if not isinstance(values, list) or not all(_matches_type(value, definition['range'][0]) for value in values):
                raise ValueError('Reclassification target property violates its datatype')
    # Never convert an old event time to a due date, nor invent missing fields.
    return properties, sorted(set(old) - set(properties) - RESERVED_PROPERTIES)


def _validate_plan(store, plan, snapshot):
    if not isinstance(plan, dict) or plan.get('format') != 1:
        raise ValueError('Unsupported reclassification plan format')
    validate_operation_id(plan.get('operation_id'))
    if plan.get('namespace_sha256') != hashlib.sha256(store.config.urn_prefix.encode()).hexdigest():
        raise ValueError('Reclassification plan belongs to another namespace')
    schema = snapshot['schema']
    if plan.get('ontology_version') != schema['version'] or plan.get('profile_digest') != schema.get('profile_digest'):
        raise VersionConflict('Ontology changed after classification assessment')
    if plan.get('snapshot_sha256') != snapshot['snapshot_sha256']:
        raise VersionConflict('Memories or relationships changed after classification assessment')
    entries, overrides = plan.get('entries'), plan.get('relation_overrides', [])
    if not isinstance(entries, list) or not 1 <= len(entries) <= 300 or not isinstance(overrides, list):
        raise ValueError('A reclassification plan requires 1 to 300 entries')
    nodes = {node['properties']['uri']: node for node in snapshot['nodes']}
    types = {item['key']: item for item in schema['entity_types'] if item.get('active', True)}
    selected, destinations = {}, set()
    for entry in entries:
        uri, kind = entry.get('uri'), entry.get('target_type')
        if uri not in nodes or uri in selected:
            raise ValueError('Reclassification sources must be unique active owned memories')
        node = nodes[uri]
        validate_version(entry.get('expected_version'))
        if entry['expected_version'] != node['properties'].get('hermes_revision', 0):
            raise VersionConflict('Memory version changed after classification assessment')
        if kind not in types or kind == runtime_kind(node['labels'], schema):
            raise ValueError('Reclassification target must be a different active ontology type')
        if not isinstance(entry.get('reason'), str) or not entry['reason'].strip():
            raise ValueError('Reclassification requires a semantic reason')
        support = entry.get('support')
        if not isinstance(support, list) or not support:
            raise ValueError('Reclassification requires support from saved source properties')
        for citation in support:
            if not isinstance(citation, dict):
                raise ValueError('Classification support must identify a saved property and quote')
            value, quote = node['properties'].get(citation.get('property')), citation.get('quote')
            if not isinstance(value, str) or not isinstance(quote, str) or not quote.strip() or quote not in value:
                raise ValueError('Classification quote is not present in the saved property')
        destination = target_uri(store.config, uri, kind, schema)
        if destination in destinations or destination in nodes:
            raise ValueError('Reclassification target identities collide')
        properties, removed = _target_properties(store.config, node, kind, schema)
        selected[uri] = {'uri': destination, 'type': kind, 'properties': properties,
                         'old': node, 'reason': entry['reason'], 'history_only_properties': removed}
        destinations.add(destination)
    relations = {item['key']: item for item in schema['relations'] if item.get('active', True)}
    override_map = {}
    for item in overrides:
        key = item.get('edge_sha256')
        if not isinstance(key, str) or key in override_map or not item.get('reason'):
            raise ValueError('Relationship overrides require unique edge hashes and semantic reasons')
        override_map[key] = item
    copied, used = [], set()
    for edge in snapshot['edges']:
        if not ({edge['source'], edge['target']} & set(selected)):
            continue
        # Historical, unresolved and external facts stay attached to old URIs.
        if edge['source'] not in nodes or edge['target'] not in nodes:
            continue
        key = edge_key(edge)
        override = override_map.get(key)
        kind = normalize_rel(override.get('new_type') if override else edge['type'], schema)
        definition = relations.get(kind)
        source_type = selected[edge['source']]['type'] if edge['source'] in selected else runtime_kind(nodes[edge['source']]['labels'], schema)
        target_type = selected[edge['target']]['type'] if edge['target'] in selected else runtime_kind(nodes[edge['target']]['labels'], schema)
        if (not definition or ('any' not in definition['domain'] and source_type not in definition['domain'])
                or ('any' not in definition['range'] and target_type not in definition['range'])):
            raise ValueError('An incident relationship is incompatible; provide a supported explicit mapping or defer the memory')
        if override:
            support = override.get('support', [])
            if not support or any(not isinstance(c, dict) or c.get('uri') not in {edge['source'], edge['target']}
                    or not isinstance(c.get('quote'), str) or not c['quote'].strip()
                    or not isinstance(nodes[c['uri']]['properties'].get(c.get('property')), str)
                    or c['quote'] not in nodes[c['uri']]['properties'][c['property']] for c in support):
                raise ValueError('Relationship mapping requires support from saved endpoint properties')
            used.add(key)
        copied.append({**edge, 'type': kind,
                       'source': selected.get(edge['source'], {}).get('uri', edge['source']),
                       'target': selected.get(edge['target'], {}).get('uri', edge['target'])})
    if set(override_map) != used:
        raise ValueError('Relationship override does not match a selected active fact')
    return selected, copied


def reclassify_memories(store, plan, *, dry_run=True):
    """Validate offline decisions, or atomically install their audited identities.

    A complete receipt makes retries idempotent even after later graph edits.
    Snapshot and ontology checks occur again under the normal workspace lock.
    """
    plan_hash = digest(plan)
    operation = validate_operation_id(plan.get('operation_id') if isinstance(plan, dict) else None)
    metadata = {'snapshot_id': 'operation-' + operation, 'extraction_hash': plan_hash,
                'payload_hash': plan_hash, 'operation_hash': plan_hash, 'request_hash': plan_hash,
                'source_session': None, 'checkpoint_json': '{}', 'last_message_id': None,
                'origin': 'manual', 'reason': 'Explicit ontology reclassification; original identities retained',
                'evidence': {}}

    def work(tx):
        prior = tx.run('''MATCH (r:KGSyncReceipt {snapshot_id:$snapshot_id})
            RETURN properties(r) AS receipt''', metadata).single()
        if prior:
            receipt = dict(prior['receipt'])
            if receipt.get('namespace') != store.config.urn_prefix or receipt.get('payload_hash') != plan_hash:
                raise ValueError('Reclassification operation ID was already used for a different plan')
            if receipt.get('complete'):
                return {**json.loads(receipt['reclassification_result_json']), 'idempotent': True, 'dry_run': dry_run}
        snapshot = _snapshot(tx, store)
        selected, copied = _validate_plan(store, plan, snapshot)
        aliases = {node['properties']['uri']: node for node in snapshot['aliases']
                   if node['properties']['hermes_canonicalUri'] in selected}
        for target in selected.values():
            if tx.run('MATCH (n {uri:$uri}) RETURN count(n) AS count', {'uri': target['uri']}).single()['count']:
                raise ValueError('Reclassification target URI already exists; refusing an implicit merge')
        result = {'ok': True, 'dry_run': dry_run, 'idempotent': False,
                  'reclassified': len(selected), 'relationships_copied': len(copied),
                  'aliases_redirected': len(aliases),
                  'retained_historical_relationships': True, 'plan_sha256': plan_hash,
                  'ontology_version': snapshot['schema']['version'],
                  'mappings': [{'from': uri, 'to': item['uri'], 'target_type': item['type'],
                                'history_only_properties': item['history_only_properties']}
                               for uri, item in selected.items()]}
        if dry_run:
            return result
        receipt = store._begin_receipt(tx, metadata)
        affected = {edge['source'] for edge in copied} - {item['uri'] for item in selected.values()}
        nodes = {node['properties']['uri']: node for node in snapshot['nodes']}
        nodes.update(aliases)
        for uri in sorted(set(selected) | affected | set(aliases)):
            old = nodes[uri]
            evidence = json.loads(old['properties'].get('hermes_evidenceJson', '[]'))
            store._save_revision(tx, uri, old['labels'], old['properties'],
                                 {**metadata, 'evidence': {'entities': {uri: evidence}}}, 'reclassification_baseline')
        labels = runtime_entity_labels(snapshot['schema'])
        for old_uri, item in selected.items():
            original = item['old']['properties']
            props = {key: value for key, value in original.items() if key in RESERVED_PROPERTIES
                     and key not in {'uri', 'hermes_aliasConfirmed', 'hermes_canonicalUri',
                                     'hermes_archiveReason', 'hermes_archivedByWorkbench'}}
            props.update(item['properties'], uri=item['uri'], hermes_revision=original.get('hermes_revision', 0) + 1,
                         hermes_lastOperationId=metadata['snapshot_id'], hermes_origin='manual')
            # Label values are derived exclusively from validated ontology keys.
            tx.run(f'''CREATE (n:KGEntity:{labels[item['type']]}) SET n=$properties,
                n.hermes_updatedAt=datetime(), n.hermes_createdAt=coalesce(n.hermes_createdAt,datetime())''',
                {'properties': props}).consume()
            tx.run('''MATCH (n {uri:$uri}) SET n:KGQuarantined,
                n.hermes_aliasConfirmed=true,n.hermes_canonicalUri=$target,
                n.hermes_archiveReason=$reason,n.hermes_revision=coalesce(n.hermes_revision,0)+1,
                n.hermes_lastOperationId=$operation,n.hermes_updatedAt=datetime()''',
                {'uri': old_uri, 'target': item['uri'], 'reason': item['reason'],
                 'operation': metadata['snapshot_id']}).consume()
        for uri, node in aliases.items():
            destination = selected[node['properties']['hermes_canonicalUri']]['uri']
            tx.run('''MATCH (n {uri:$uri}) SET n.hermes_canonicalUri=$target,
                n.hermes_revision=coalesce(n.hermes_revision,0)+1,
                n.hermes_lastOperationId=$operation,n.hermes_updatedAt=datetime()''',
                {'uri': uri, 'target': destination, 'operation': metadata['snapshot_id']}).consume()
        # CREATE, rather than MERGE, preserves the multiplicity and original
        # provenance of parallel edges. Original edges are never deleted.
        for edge in copied:
            tx.run(f'''MATCH (a {{uri:$source}}),(b {{uri:$target}})
                CREATE (a)-[r:{edge['type']}]->(b) SET r=$properties''', edge).consume()
        for uri in sorted(affected):
            tx.run('''MATCH (n {uri:$uri}) SET n.hermes_revision=coalesce(n.hermes_revision,0)+1,
                n.hermes_lastOperationId=$operation,n.hermes_updatedAt=datetime()''',
                {'uri': uri, 'operation': metadata['snapshot_id']}).consume()
        changed = sorted(set(selected) | {item['uri'] for item in selected.values()} | affected | set(aliases))
        for uri in changed:
            current = store._locked_entity(tx, uri)
            evidence = json.loads(current['properties'].get('hermes_evidenceJson', '[]'))
            store._save_revision(tx, uri, current['labels'], current['properties'],
                                 {**metadata, 'evidence': {'entities': {uri: evidence}}}, 'reclassify')
        result['affected_uris'] = changed
        tx.run('''MATCH (r:KGSyncReceipt {snapshot_id:$snapshot_id})
            SET r.reclassification_plan_json=$plan,r.reclassification_result_json=$result''',
            {**metadata, 'plan': dumps(plan), 'result': dumps(result)}).consume()
        receipt.update(entities_written=len(selected), relations_written=len(copied),
                       entity_uris=changed, relation_ids=[], disposition='applied')
        store._finish_receipt(tx, metadata, receipt)
        return result

    with store.driver.session(database=store.config.database) as session:
        result = store._execute(session, 'read' if dry_run else 'write', work)
    if not dry_run:
        # Facts have committed. Notification errors are reported as degradation,
        # keeping the successful receipt recoverable and retryable.
        try:
            from .freshness import bump_recall_generation
            bump_recall_generation(store.config.home)
            result['recall_notification'] = 'current'
        except OSError:
            result['recall_notification'] = 'degraded'
    return result
