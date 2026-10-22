"""Namespace-scoped migration and portable backups with typed time values."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from contextlib import closing
from pathlib import Path

from .schema import ENTITY_LABELS

LABELS=set(ENTITY_LABELS.values())|{"KGEntity","KGQuarantined","KGLegacyAlias","KGLegacyUnresolved","KGMemoryRevision","KGReviewBatch","KGSyncReceipt"}
SELECT_NODES="""MATCH (n) WHERE
    (n.uri STARTS WITH $prefix AND any(label IN labels(n) WHERE label IN $labels))
    OR ((n:KGMemoryRevision OR n:KGReviewBatch OR n:KGSyncReceipt) AND n.namespace=$prefix)
    OR (n:KGSyncReceipt AND any(uri IN coalesce(n.entity_uris,[]) WHERE uri STARTS WITH $prefix))
    RETURN elementId(n) AS id,labels(n) AS labels,properties(n) AS props"""

DB_NAMES = {"sync-state-v2.db", "console.db"}


class RestoreInProgress(RuntimeError):
    pass


def restore_guard_path(home):
    return Path(home) / "kg-sync" / "restore-in-progress.json"


def restore_in_progress(home):
    return restore_guard_path(home).exists()


def assert_not_restoring(home):
    if restore_in_progress(home):
        raise RestoreInProgress("A restore is incomplete; resume restore with the same backup before starting or writing memories")


def _fsync_directory(path):
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        # Windows does not expose directory fsync through os.open.
        pass


def _atomic_file(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _save_guard(path, guard):
    _atomic_file(path, json.dumps(guard, sort_keys=True).encode())


class _RestoreLock:
    """An OS-owned local lock prevents two resumptions from racing each other."""
    def __init__(self, home):
        self.path = Path(home) / "kg-sync" / "restore.lock"

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        self.file.seek(0, 2)
        if not self.file.tell():
            self.file.write(b"\0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise RestoreInProgress("Another restore process holds this profile's restore lock") from exc
        return self

    def __exit__(self, *_):
        self.file.close()


def encode(value):
    if value is None or isinstance(value,(str,int,float,bool)):
        return value
    if isinstance(value,(list,tuple)):
        return [encode(v) for v in value]
    if isinstance(value,dict):
        return {key:encode(v) for key,v in value.items()}
    from neo4j.time import DateTime,Date,Time,Duration
    for kind in (DateTime,Date,Time,Duration):
        if isinstance(value,kind):
            return {"__kg_type__":kind.__name__,"value":value.iso_format()}
    raise ValueError("Unsupported backup property type: "+type(value).__name__)


def decode(value):
    if isinstance(value,list):
        return [decode(v) for v in value]
    if isinstance(value,dict):
        if set(value)=={"__kg_type__","value"}:
            from neo4j.time import DateTime,Date,Time,Duration
            kinds={kind.__name__:kind for kind in (DateTime,Date,Time,Duration)}
            if value["__kg_type__"] not in kinds:
                raise ValueError("Unknown backup time type")
            return kinds[value["__kg_type__"]].from_iso_format(value["value"])
        return {key:decode(v) for key,v in value.items()}
    return value


def migration_preview(store):
    rows=store.run_read("""MATCH (n) WHERE n.uri STARTS WITH $prefix
       AND any(label IN labels(n) WHERE label IN $labels)
       RETURN n.uri AS uri,count(n) AS copies,collect(labels(n)) AS labels""",
       {"prefix":store.config.urn_prefix,"labels":list(ENTITY_LABELS.values())})
    duplicates=[r["uri"] for r in rows if r["copies"]!=1]
    return {"ok":not duplicates,"namespace":store.config.urn_prefix,"entities":len(rows),"duplicate_uris":duplicates,
            "preserves":["uri","visibility","quarantine","aliases"],"action":"label canonical legacy entities; version 0 remains readable; no facts re-extracted"}


def migrate(store):
    preview=migration_preview(store)
    if not preview["ok"]:
        raise ValueError("Duplicate URIs must be repaired before migration")
    store.ensure_schema()
    with store.driver.session(database=store.config.database) as session:
        store._execute(session,"write",lambda tx:tx.run("""MATCH (n) WHERE n.uri STARTS WITH $prefix
            AND any(label IN labels(n) WHERE label IN $labels)
            AND NOT n:KGLegacyAlias AND NOT n:KGLegacyUnresolved
            SET n:KGEntity RETURN count(n) AS migrated""",
            {"prefix":store.config.urn_prefix,"labels":list(ENTITY_LABELS.values())}).consume())
    return {**preview,"applied":True}


def backup(store,path):
    assert_not_restoring(store.config.home)
    path=Path(path).expanduser().resolve()
    if path.exists():
        raise ValueError("Backup destination already exists")
    files={}
    def collect(tx):
        nodes=[dict(row) for row in tx.run(SELECT_NODES,{"prefix":store.config.urn_prefix,"labels":list(ENTITY_LABELS.values())})]
        ids=[row['id'] for row in nodes]
        relations=[dict(row) for row in tx.run("""MATCH (a)-[r]->(b)
            WHERE elementId(a) IN $ids AND elementId(b) IN $ids
            RETURN elementId(a) AS source,elementId(b) AS target,type(r) AS type,properties(r) AS props""",{"ids":ids})]
        external = [dict(row) for row in tx.run("""MATCH (a)-[r]->(b)
            WHERE elementId(a) IN $ids AND NOT elementId(b) IN $ids
            RETURN elementId(a) AS source,b.uri AS target_uri,b.shared AS shared,
                type(r) AS type,properties(r) AS props""", {"ids": ids})]
        incoming = tx.run("""MATCH (a)-[r]->(b)
            WHERE elementId(b) IN $ids AND NOT elementId(a) IN $ids
            RETURN count(r) AS count""", {"ids": ids}).single()["count"]
        external_refs = [{key: row[key] for key in ("source", "target_uri", "type", "props")}
                         for row in external if row.get("target_uri") and row.get("shared") is True]
        # All KG writers hold the same graph workspace lock. SQLite is copied
        # while it is held, so its checkpoint can never get ahead of this graph.
        with tempfile.TemporaryDirectory() as directory:
            for name in ("sync-state-v2.db","console.db"):
                source=store.config.home/"kg-sync"/name
                if source.exists():
                    target=Path(directory)/name
                    with closing(sqlite3.connect(source.as_uri()+"?mode=ro",uri=True)) as src, closing(sqlite3.connect(target)) as dst:
                        src.backup(dst)
                    files[name]=target.read_bytes()
        return {"nodes":encode(nodes),"relations":encode(relations),
                "external_relations": encode(external_refs),
                "omitted_external_relations": len(external) - len(external_refs) + incoming}
    with store.driver.session(database=store.config.database) as session:
        graph=store._execute(session,"write",collect)
    files['graph.json']=json.dumps(graph,ensure_ascii=False,separators=(',',':')).encode()
    manifest={"format":1,"namespace":store.config.urn_prefix,"user_person_urn":store.config.user_person_urn,
              "files":{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}}
    path.parent.mkdir(parents=True,exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'wb') as file:
        with zipfile.ZipFile(file, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json',json.dumps(manifest))
            for name,data in files.items(): archive.writestr(name,data)
        file.flush()
        os.fsync(file.fileno())
    return {"ok":True,"path":str(path),"entities_and_metadata":len(graph['nodes']),
            "configuration_credentials_included":False,"contains_transcript_evidence":'sync-state-v2.db' in files,
            "external_reference_count": len(graph["external_relations"]),
            "omitted_external_relations": graph["omitted_external_relations"]}


def _read_backup(store, path):
    with zipfile.ZipFile(path) as archive:
        if len(archive.infolist()) > 4 or len({info.filename for info in archive.infolist()}) != len(archive.infolist()):
            raise ValueError("Duplicate or unexpected backup members")
        if any(info.file_size>256*1024*1024 for info in archive.infolist()):
            raise ValueError("Backup member too large")
        manifest=json.loads(archive.read('manifest.json'))
        if manifest.get('format')!=1 or manifest.get('namespace')!=store.config.urn_prefix:
            raise ValueError("Backup format/namespace does not match the destination")
        if manifest.get('user_person_urn') not in (None, store.config.user_person_urn):
            raise ValueError("Backup user identity does not match the destination profile")
        if not isinstance(manifest.get("files"), dict) or 'graph.json' not in manifest['files'] or not set(manifest['files'])<={'graph.json','sync-state-v2.db','console.db'}:
            raise ValueError("Unknown backup member")
        if set(archive.namelist()) != {'manifest.json', *manifest['files']}:
            raise ValueError("Backup member list does not match its manifest")
        files={name:archive.read(name) for name in manifest['files']}
    if any(hashlib.sha256(data).hexdigest()!=manifest['files'][name] for name,data in files.items()):
        raise ValueError("Backup checksum mismatch")
    graph=decode(json.loads(files['graph.json']))
    _validate_graph(store, graph)
    return _validated_sqlite_files(files), graph


def _validated_sqlite_files(files):
    """Reject unreadable SQLite and prepare paused console bytes before a guard exists."""
    result = dict(files)
    try:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name in DB_NAMES & set(files):
                (folder / name).write_bytes(files[name])
                with closing(sqlite3.connect(folder / name)) as conn:
                    if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                        raise ValueError("Backup SQLite integrity check failed")
            console = folder / 'console.db'
            with closing(sqlite3.connect(console)) as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                conn.execute("INSERT INTO settings VALUES('paused','true') ON CONFLICT(key) DO UPDATE SET value='true'")
                conn.commit()
            for name in DB_NAMES:
                if (folder / name).exists():
                    result[name] = (folder / name).read_bytes()
    except sqlite3.DatabaseError as exc:
        raise ValueError("Backup SQLite schema or integrity validation failed") from exc
    return result


def _validate_graph(store, graph):
    from .schema import RELATION_ALIASES, canonical_urn
    if not isinstance(graph, dict) or not isinstance(graph.get('nodes'), list) or not isinstance(graph.get('relations'), list):
        raise ValueError("Malformed backup graph")
    ids = set()
    uris = set()
    prefix = store.config.urn_prefix
    metadata_labels = {'KGMemoryRevision', 'KGReviewBatch', 'KGSyncReceipt'}
    for node in graph['nodes']:
        if not isinstance(node, dict) or set(node) != {'id', 'labels', 'props'} or not isinstance(node['id'], str):
            raise ValueError("Malformed backup node")
        if node['id'] in ids:
            raise ValueError("Duplicate backup node id")
        ids.add(node['id'])
        if not isinstance(node['labels'], list) or not set(node['labels'])<=LABELS or not node['labels']:
            raise ValueError("Unsupported graph labels in backup")
        props = node['props']
        if not isinstance(props, dict):
            raise ValueError("Malformed backup properties")
        labels = set(node['labels'])
        if labels & metadata_labels:
            if labels - metadata_labels:
                raise ValueError("Metadata cannot impersonate a business entity")
            if props.get('namespace') != prefix:
                # Old receipts did not store namespace; their complete local URI
                # set is the only supported ownership proof available.
                entity_uris = props.get('entity_uris')
                if labels != {'KGSyncReceipt'} or props.get('namespace') is not None or not isinstance(entity_uris, list) or not entity_uris or not all(isinstance(uri, str) and uri.startswith(prefix) for uri in entity_uris):
                    raise ValueError("Backup metadata belongs to another namespace")
            if 'uri' in props:
                raise ValueError("Backup metadata cannot impersonate an entity URI")
            identities = {'KGMemoryRevision': 'revision_id', 'KGReviewBatch': 'review_id', 'KGSyncReceipt': 'snapshot_id'}
            for label in labels:
                if not isinstance(props.get(identities[label]), str) or not props[identities[label]]:
                    raise ValueError("Backup metadata is missing its unique identifier")
            if 'KGMemoryRevision' in labels and not (isinstance(props.get('entity_uri'), str) and props['entity_uri'].startswith(prefix)):
                raise ValueError("Backup revision belongs to another namespace")
            if 'KGSyncReceipt' in labels and any(not isinstance(uri, str) or not uri.startswith(prefix) for uri in props.get('entity_uris', [])):
                raise ValueError("Backup receipt contains another namespace")
        else:
            uri = props.get('uri')
            if not isinstance(uri, str) or not uri.startswith(prefix):
                raise ValueError("Backup entity belongs to another namespace")
            if uri in uris:
                raise ValueError("Duplicate backup entity URI")
            uris.add(uri)
    allowed = set(RELATION_ALIASES.values())
    for rel in graph['relations']:
        if not isinstance(rel, dict) or rel.get('type') not in allowed or rel.get('source') not in ids or rel.get('target') not in ids or not isinstance(rel.get('props'), dict):
            raise ValueError("Unsupported relationship or missing backup endpoint")
    if not isinstance(graph.get('external_relations', []), list):
        raise ValueError("Malformed external relationship references")
    for rel in graph.get('external_relations', []):
        if not isinstance(rel, dict) or rel.get('source') not in ids or rel.get('type') not in allowed or not isinstance(rel.get('props'), dict):
            raise ValueError("Malformed external relationship reference")
        uri = rel.get('target_uri')
        canonical_urn(uri, prefix=prefix)
        if uri.startswith(prefix):
            raise ValueError("An external reference cannot create a missing local entity")


def _prepare_stage(folder, guard, files):
    stage = folder / ('.restore-' + guard['backup_hash'])
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)
    for name in sorted(DB_NAMES & set(files)):
        _atomic_file(stage / name, files[name])
        with closing(sqlite3.connect((stage / name).as_uri() + '?mode=ro', uri=True)) as conn:
            if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError("Backup SQLite integrity check failed")
    # Console bytes were already made paused during preflight validation.
    guard['stage_files'] = {name: hashlib.sha256((stage / name).read_bytes()).hexdigest()
        for name in sorted(DB_NAMES) if (stage / name).exists()}
    guard['state'] = 'staged'
    return stage


def _install_staged_file(source, destination, expected_hash):
    """Atomic, exclusive installation; a crash never leaves a half-copied DB."""
    if destination.exists():
        if hashlib.sha256(destination.read_bytes()).hexdigest() != expected_hash:
            raise ValueError("Restore destination changed; refusing to overwrite it")
        return
    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != expected_hash:
        raise ValueError("Restore staging file is missing or changed")
    # Both files are in kg-sync on the same filesystem. Hard-link creation is
    # atomic and refuses existing destinations on all three supported platforms.
    os.link(source, destination)
    _fsync_directory(destination.parent)


def restore(store, path):
    path = Path(path).expanduser().resolve()
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(block)
    backup_hash = digest.hexdigest()
    files, graph = _read_backup(store, path)
    with _RestoreLock(store.config.home):
        return _restore_locked(store, files, graph, backup_hash)


def _restore_locked(store, files, graph, backup_hash):
    folder = store.config.home / 'kg-sync'
    guard_path = restore_guard_path(store.config.home)
    resumed = guard_path.exists()
    if resumed:
        guard = json.loads(guard_path.read_text())
        if guard.get('format') != 1 or guard.get('namespace') != store.config.urn_prefix or guard.get('backup_hash') != backup_hash:
            raise RestoreInProgress("Resume the incomplete restore with its original backup and namespace")
        if not isinstance(guard.get('operation_id'), str) or guard.get('state') not in {'staging', 'staged', 'graph_committed', 'installed'}:
            raise RestoreInProgress("Restore journal is invalid; keep the destination paused for inspection")
    else:
        if any((folder / name).exists() for name in DB_NAMES):
            raise ValueError("Restore requires an empty destination sync directory")
        if store.run_read(SELECT_NODES, {'prefix': store.config.urn_prefix, 'labels': list(ENTITY_LABELS.values())}):
            raise ValueError("Restore requires an empty destination namespace")
        guard = {'format': 1, 'namespace': store.config.urn_prefix, 'backup_hash': backup_hash,
            'operation_id': uuid.uuid4().hex, 'state': 'staging', 'installed': []}
        _save_guard(guard_path, guard)
    stage = folder / ('.restore-' + backup_hash)
    if guard['state'] == 'staging':
        stage = _prepare_stage(folder, guard, files)
        _save_guard(guard_path, guard)
    if not isinstance(guard.get('stage_files'), dict) or not set(guard['stage_files']) <= DB_NAMES or 'console.db' not in guard['stage_files']:
        raise RestoreInProgress("Restore staging journal is invalid")
    store.ensure_schema()
    def write(tx):
        receipt = tx.run("MATCH (w:KGWorkspace {namespace:$namespace}) RETURN properties(w) AS receipt",
            {'namespace': store.config.urn_prefix}).single()['receipt']
        if receipt.get('restore_operation_id') == guard['operation_id']:
            if receipt.get('restore_hash') != backup_hash:
                raise RestoreInProgress("Restore receipt does not match this backup")
            return {'nodes': receipt['restore_nodes'], 'relations': receipt['restore_relations'],
                'external_relations_restored': receipt.get('restore_external_count', 0),
                'external_relations_unresolved': json.loads(receipt.get('restore_unresolved_json', '[]'))}
        if list(tx.run(SELECT_NODES, {'prefix': store.config.urn_prefix, 'labels': list(ENTITY_LABELS.values())})):
            raise ValueError("Restore requires an empty destination namespace")
        id_map = {}
        node_by_id = {node['id']: node for node in graph['nodes']}
        for node in graph['nodes']:
            labels = ':'.join('`' + label + '`' for label in node['labels'])
            id_map[node['id']] = tx.run(f"CREATE (n:{labels}) SET n=$props RETURN elementId(n) AS id",
                {'props': node['props']}).single()['id']
        for rel in graph['relations']:
            tx.run(f"MATCH (a),(b) WHERE elementId(a)=$source AND elementId(b)=$target CREATE (a)-[r:`{rel['type']}`]->(b) SET r=$props",
                {'source': id_map[rel['source']], 'target': id_map[rel['target']], 'props': rel['props']}).consume()
        external_count, unresolved = 0, []
        from .schema import RELATION_ENDPOINTS
        for rel in graph.get('external_relations', []):
            target = list(tx.run("""MATCH (b {uri:$uri}) WHERE b.shared=true
                AND NOT b:KGQuarantined AND NOT b:KGLegacyAlias AND NOT b:KGLegacyUnresolved
                AND coalesce(b.hermes_aliasConfirmed,false)=false
                RETURN elementId(b) AS id,labels(b) AS labels""", {'uri': rel['target_uri']}))
            expected = RELATION_ENDPOINTS.get(rel['type'])
            source_labels = set(node_by_id[rel['source']]['labels'])
            if len(target) != 1 or not source_labels & set(ENTITY_LABELS.values()) or not set(target[0]['labels']) & set(ENTITY_LABELS.values()) or (expected and (not source_labels & expected[0] or not set(target[0]['labels']) & expected[1])):
                unresolved.append({'target_uri': rel['target_uri'], 'type': rel['type']})
                continue
            tx.run(f"MATCH (a),(b) WHERE elementId(a)=$source AND elementId(b)=$target CREATE (a)-[r:`{rel['type']}`]->(b) SET r=$props",
                {'source': id_map[rel['source']], 'target': target[0]['id'], 'props': rel['props']}).consume()
            external_count += 1
        result = {'nodes': len(id_map), 'relations': len(graph['relations']),
            'external_relations_restored': external_count, 'external_relations_unresolved': unresolved}
        tx.run("""MATCH (w:KGWorkspace {namespace:$namespace})
            SET w.restore_hash=$backup_hash,w.restore_operation_id=$operation_id,
                w.restore_pending=true,
                w.restore_nodes=$nodes,w.restore_relations=$relations,w.restore_external_count=$external_count,
                w.restore_unresolved_json=$unresolved_json,w.restore_committed_at=datetime()""",
            {'namespace': store.config.urn_prefix, 'backup_hash': backup_hash, 'operation_id': guard['operation_id'],
             'nodes': result['nodes'], 'relations': result['relations'], 'external_count': external_count,
             'unresolved_json': json.dumps(unresolved)}).consume()
        return result
    with store.driver.session(database=store.config.database) as session:
        result = store._execute(session, 'write', write, maintenance=True)
    guard['state'] = 'graph_committed'
    _save_guard(guard_path, guard)
    for name, expected in guard['stage_files'].items():
        _install_staged_file(stage / name, folder / name, expected)
        if name not in guard['installed']:
            guard['installed'].append(name)
        _save_guard(guard_path, guard)
    guard['state'] = 'installed'
    _save_guard(guard_path, guard)
    def finish(tx):
        row = tx.run("""MATCH (w:KGWorkspace {namespace:$namespace})
            WHERE w.restore_operation_id=$operation_id AND w.restore_hash=$backup_hash
            SET w.restore_pending=false RETURN w.restore_hash AS restore_hash""",
            {'namespace': store.config.urn_prefix, 'operation_id': guard['operation_id'],
             'backup_hash': backup_hash}).single()
        if not row:
            raise RestoreInProgress("Restore completion receipt changed; keep the destination paused")
    with store.driver.session(database=store.config.database) as session:
        store._execute(session, 'write', finish, maintenance=True)
    shutil.rmtree(stage, ignore_errors=True)
    guard_path.unlink()
    _fsync_directory(folder)
    return {'ok': True, **result, 'resumed': resumed, 'sync_paused': True,
        'vector_index': 'rebuild required', 'omitted_external_relations': graph.get('omitted_external_relations', 0)}
