"""Immutable selection gates and content-bound local evidence checks."""
from __future__ import annotations

from datetime import datetime
from copy import deepcopy
import hashlib
from pathlib import Path

from . import FORMAT
from .common import digest, read_json

RULES = read_json(Path(__file__).with_name('protocol-v1.json'))


def protocol_template():
    return {'format': FORMAT, 'rules': deepcopy(RULES), 'rules_sha256': digest(RULES), 'frozen': False,
            'frozen_at': None, 'allowed_config_sha256': [], 'evidence': {key: None for key in RULES['required_evidence']}}


def _read_reference(reference):
    if not isinstance(reference, dict) or not isinstance(reference.get('path'), str): return None
    path = Path(reference['path'])
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != reference.get('sha256'): return None
    try:
        value = read_json(path)
        return value if isinstance(value, dict) else None
    except (ValueError, OSError): return None


def _evidence(protocol, name):
    return _read_reference(protocol.get('evidence', {}).get(name))


def evidence_blockers(protocol, run):
    if not protocol or protocol.get('rules') != RULES or protocol.get('rules_sha256') != digest(RULES):
        return ['frozen_protocol_rules_missing_or_changed']
    reasons, manifest = [], run['manifest']
    if not protocol.get('frozen') or manifest['config_sha256'] not in protocol.get('allowed_config_sha256', []): reasons.append('config_not_predeclared')
    if manifest.get('protocol_sha256') != digest(protocol): reasons.append('protocol_manifest_mismatch')
    freeze = _evidence(protocol, 'blind_freeze_proof')
    if not freeze or freeze.get('effective_fixture_sha256') != manifest['effective_fixture_sha256'] or freeze.get('blind_unseen_until_freeze') is not True or not freeze.get('reviewer'):
        reasons.append('blind_freeze_proof_missing')
    else:
        try:
            frozen_at = datetime.fromisoformat(protocol['frozen_at'].replace('Z', '+00:00'))
            started_at = datetime.fromisoformat(run['started_at'].replace('Z', '+00:00'))
            if frozen_at.tzinfo is None or frozen_at > started_at: reasons.append('protocol_not_frozen_before_run')
        except (ValueError, TypeError, KeyError): reasons.append('invalid_freeze_time')
    references = protocol.get('evidence', {}).get('ann_evidence')
    references = references if isinstance(references, list) else [references]
    ann_reports = [_read_reference(ref) for ref in references]
    def ann_matches(ann, scope):
        return bool(ann and ann.get('format') == 'kg-retrieval-ann-v1' and ann.get('ok')
                    and ann.get('real_vectors') is True and ann.get('backend') == 'neo4j_ann'
                    and ann.get('profile_fingerprint') == manifest['config']['model_fingerprint']
                    and ann.get('dataset_sha256') == manifest['dataset_sha256']
                    and ann.get('representation') == manifest['config']['representation']
                    and ann.get('tool_code_sha256') == manifest.get('tool_code_sha256')
                    and ann.get('namespace') == scope['namespace']
                    and ann.get('actualcards_digest') == scope['actualcards_digest']
                    and ann.get('top_k') == 50 and type(ann.get('query_count')) is int and ann['query_count'] > 0
                    and type(ann.get('ann_recall_at_50')) in {int, float}
                    and RULES['ann_recall_minimum'] <= ann['ann_recall_at_50'] <= 1
                    and isinstance(ann.get('cleanup'), dict)
                    and ann['cleanup'].get('complete') is True and ann['cleanup'].get('remaining') == 0
                    and ann.get('source') == 'isolated_snapshot')
    scopes = [scope for scope in manifest.get('card_scopes', []) if scope['track'] == 'facts']
    if not scopes or any(not any(ann_matches(ann, scope) for ann in ann_reports) for scope in scopes):
        reasons.append('real_ann_evidence_missing_or_below_gate')
    safety = _evidence(protocol, 'installed_safety_evidence')
    required = {'namespace_isolation', 'visibility_change', 'revision_race', 'direction'}
    if not safety or not safety.get('ok') or safety.get('synthetic_only') is not True or safety.get('backend') != 'installed_http' or safety.get('failures') != 0 or not required <= set(safety.get('checks', [])) or safety.get('cleanup') is not True:
        reasons.append('live_safety_evidence_missing')
    http = _evidence(protocol, 'http_performance_evidence')
    if not http or not http.get('ok') or http.get('transport') != 'http' or http.get('model_fingerprint') != manifest['config']['model_fingerprint'] or http.get('samples', 0) < RULES['http_samples_minimum'] or not isinstance(http.get('p95_ms'), (int, float)) or http['p95_ms'] > RULES['http_p95_ms_maximum']:
        reasons.append('real_http_performance_evidence_missing_or_below_gate')
    return reasons
