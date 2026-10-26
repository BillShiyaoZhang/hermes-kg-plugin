"""Read-only, local research readiness audit. Never labels, freezes or runs models.

References in a supplied manifest are {path, sha256} objects. All artifact reads
remain below this checkout's reports/ or private/ directories. A hash proves
content integrity, not human identity or production authorization.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins'))

from kg.research.common import digest, read_json
from kg.research.fixtures import apply_labels, validate_fixture, validate_labels
from kg.research.protocol import RULES, evidence_blockers
from kg.research.runner import tool_fingerprint, validate_run

FORMAT = 'kg-research-readiness-v1'
SHA = re.compile(r'[a-f0-9]{64}')


def _inside(root, value):
    path = Path(value)
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(root) or not any(path.is_relative_to((root / name).resolve()) for name in ('reports', 'private')):
        raise ValueError('artifact_outside_workspace_reports')
    return path


def reference(path, root=ROOT):
    root = Path(root).resolve()
    path = _inside(root, path)
    return {'path': path.relative_to(root).as_posix(),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


class Reader:
    def __init__(self, root):
        self.root, self.checked = Path(root).resolve(), {}

    def read(self, ref):
        if not isinstance(ref, dict) or set(ref) != {'path', 'sha256'} or not isinstance(ref['path'], str):
            raise ValueError('invalid_artifact_reference')
        if not isinstance(ref['sha256'], str) or not SHA.fullmatch(ref['sha256']):
            raise ValueError('invalid_artifact_digest')
        path = _inside(self.root, ref['path'])
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != ref['sha256']:
            raise ValueError('artifact_digest_mismatch')
        value = read_json(path)
        if not isinstance(value, dict):
            raise ValueError('artifact_not_object')
        self.checked[path] = ref['sha256']
        return value

    def unchanged(self):
        try:
            return all(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == sha
                       for path, sha in self.checked.items())
        except OSError:
            return False


def _binding(report, manifest):
    """Stronger than the frozen v1 gate: old baseline HTTP is not candidate proof."""
    expected = {'tool_code_sha256': manifest.get('tool_code_sha256'),
                'dataset_sha256': manifest.get('dataset_sha256'),
                'representation': manifest.get('config', {}).get('representation'),
                'scope': manifest.get('config', {}).get('scope'),
                'model_fingerprint': manifest.get('config', {}).get('model_fingerprint')}
    return bool(report and all(value is not None and report.get(key) == value
                               for key, value in expected.items()))


def _cleanup(value):
    return value is True or (isinstance(value, dict) and value.get('complete') is True
                             and value.get('remaining') == 0)


def _core_blockers(protocol, run, reader):
    # Fence nested references before the frozen helper can open them. It uses
    # cwd for relative references; do not silently resolve those elsewhere.
    if protocol:
        references = protocol.get('evidence', {})
        if not isinstance(references, dict):
            raise ValueError('invalid_protocol_references')
        for refs in references.values():
            for ref in refs if isinstance(refs, list) else [refs]:
                if ref is None:
                    continue
                reader.read(ref)
                if not Path(ref['path']).is_absolute() and Path.cwd().resolve() != reader.root:
                    return ['protocol_relative_reference_requires_workspace_cwd']
    return evidence_blockers(protocol, run)


def audit(spec, *, root=ROOT):
    if not isinstance(spec, dict) or spec.get('format') != FORMAT:
        raise ValueError('invalid_readiness_manifest')
    reader, checks, target_results = Reader(root), [], []

    def check(key, passed, *, target=None, reason=None, status=None, **details):
        item = {'id': key, 'status': status or ('passed' if passed else 'blocked')}
        if target is not None:
            item['target'] = target
        if reason and not passed:
            item['reason'] = reason
        if details:
            item['details'] = details
        checks.append(item)
        return passed

    def read(name, ref, *, target=None):
        if ref is None:
            check(name, False, target=target, reason='not_supplied', status='not_run')
            return None
        try:
            value = reader.read(ref)
        except (ValueError, OSError, TypeError, KeyError, AttributeError):
            check(name, False, target=target, reason='invalid_missing_or_changed_artifact')
            return None
        check(name, True, target=target)
        return value

    dataset, fixture = read('dataset_reference', spec.get('dataset')), read('fixture_reference', spec.get('fixture'))
    fixture_ok, validation, effective = False, {'ok': False, 'reviewed': 0, 'required': 100}, None
    if dataset is not None and fixture is not None:
        try:
            validate_fixture(fixture, dataset)
            ontology = [q for q in fixture['queries'] if q['track'] == 'ontology']
            counts = {s: sum(q['split'] == s for q in ontology) for s in ('dev', 'blind')}
            fixture_ok = counts == {'dev': 20, 'blind': 40}
            check('fixture_integrity_and_split', fixture_ok, reason='ontology_allocation_not_20_40', ontology_counts=counts)
        except (ValueError, TypeError, KeyError, AttributeError):
            check('fixture_integrity_and_split', False, reason='invalid_fixture_or_family_leakage')
    source = spec.get('review_source')
    if source:
        old_data, old_fixture = read('review_source_dataset', source.get('dataset')), read('review_source_fixture', source.get('fixture'))
        compatible = False
        if dataset is not None and fixture is not None and old_data is not None and old_fixture is not None:
            try:
                validate_fixture(old_fixture, old_data)
                real = lambda value: [q for q in value.get('queries', []) if q.get('provenance') == 'real_pending_human']
                compatible = digest(old_data) == digest(dataset) and real(old_fixture) == real(fixture)
            except (ValueError, TypeError, KeyError, AttributeError):
                compatible = False
        check('review_source_import_compatibility', compatible, reason='source_or_real_rows_changed')

    labels, lock = read('labels_reference', spec.get('labels')), read('labels_lock_reference', spec.get('labels_lock'))
    human_ok, lock_ok = False, False
    if fixture_ok and labels is not None:
        try:
            if 'submission_sha256' in labels and labels['submission_sha256'] != digest({k: v for k, v in labels.items() if k != 'submission_sha256'}):
                raise ValueError('submission_digest_mismatch')
            validation = validate_labels(fixture, dataset, labels)
            human_ok = validation.get('ok') is True and validation.get('reviewed') == RULES['human_real_gold_required']
            effective, applied = apply_labels(fixture, dataset, labels, lock)
            lock_ok = human_ok and applied.get('ok') is True
        except (ValueError, TypeError, KeyError, AttributeError):
            validation = {'ok': False, 'reviewed': 0, 'required': 100}
    check('human_gold', human_ok, reason='100_valid_human_rows_required', reviewed=validation['reviewed'], required=100)
    check('human_lock', lock_ok, reason='lock_must_bind_current_fixture_and_validated_labels')
    targets = spec.get('targets', [])
    if not isinstance(targets, list):
        targets = []
    per_target_protocols = bool(len(targets) >= 2 and all(isinstance(target, dict) and target.get('protocol') for target in targets))
    protocol = read('protocol_reference', spec.get('protocol')) if spec.get('protocol') or not per_target_protocols else None
    protocol_ok = bool(protocol and protocol.get('rules') == RULES and protocol.get('rules_sha256') == digest(RULES)
                       and protocol.get('frozen') is True and protocol.get('frozen_at')
                       and protocol.get('allowed_config_sha256'))
    try:
        protocol_ok = bool(protocol_ok and datetime.fromisoformat(protocol['frozen_at'].replace('Z', '+00:00')).tzinfo
                           and all(isinstance(value, str) and SHA.fullmatch(value) for value in protocol['allowed_config_sha256']))
    except (ValueError, TypeError, KeyError):
        protocol_ok = False
    check('frozen_protocol', protocol_ok or per_target_protocols, reason='valid_frozen_protocol_required')
    ids = [target.get('id') for target in targets if isinstance(target, dict)]
    check('target_matrix', len(targets) >= 2 and all(isinstance(value, str) for value in ids)
          and len(ids) == len(set(ids)) == len(targets), reason='at_least_two_unique_targets_required', targets=len(targets))
    for target in targets:
        if not isinstance(target, dict) or not isinstance(target.get('id'), str):
            check('target_contract', False, reason='invalid_target')
            continue
        name = target['id']
        run = read('run_reference', target.get('run'), target=name)
        if run is None:
            continue
        try:
            validate_run(run)
            check('run_integrity', True, target=name)
            manifest, config = run['manifest'], run['manifest']['config']
            bound = (fixture_ok and manifest.get('dataset_sha256') == digest(dataset)
                     and manifest.get('fixture_sha256') == digest(fixture)
                     and manifest.get('tool_code_sha256') == tool_fingerprint()
                     and config.get('scope') == spec.get('scope', 'profile'))
            valid_run = check('run_integrity_and_target_binding', bound, target=name, reason='fixture_tool_or_scope_mismatch')
        except (ValueError, TypeError, KeyError, AttributeError):
            check('run_integrity', False, target=name, reason='invalid_run_integrity')
            check('run_integrity_and_target_binding', False, target=name, reason='invalid_run_integrity')
            continue
        labeled = bool(lock_ok and manifest.get('labels_validation') == validation
                       and manifest.get('effective_fixture_sha256') == digest(effective))
        check('run_human_binding', labeled, target=name, reason='run_not_bound_to_current_human_lock')
        blind = config.get('split') == 'blind' and config.get('smoke_limit') is None
        check('blind_execution', blind, target=name, reason='blind_not_executed', status=None if blind else 'not_run')
        selected_protocol = read('target_protocol_reference', target['protocol'], target=name) if target.get('protocol') else protocol
        # Frozen core gate is still required; this audit adds binding checks,
        # never suppresses one of its blockers.
        try:
            blockers = _core_blockers(selected_protocol, run, reader)
        except (ValueError, TypeError, KeyError, AttributeError, OSError):
            blockers = ['invalid_protocol_or_evidence']
        check('frozen_evidence_gates', not blockers, target=name, reason='core_gate_blocked', blockers=blockers)
        base_evidence, extra_evidence = (selected_protocol or {}).get('evidence', {}), target.get('evidence', {})
        evidence = {**(base_evidence if isinstance(base_evidence, dict) else {}),
                    **(extra_evidence if isinstance(extra_evidence, dict) else {})}
        ann_refs = evidence.get('ann_evidence')
        ann_refs = ann_refs if isinstance(ann_refs, list) else [ann_refs]
        ann = [read('ann_evidence_reference_' + str(index), ref, target=name) for index, ref in enumerate(ann_refs)]
        fact_scopes = [scope for scope in manifest.get('card_scopes', []) if scope.get('track') == 'facts']
        ann_bound = bool(fact_scopes and all(any(report and report.get('retrieval_scope') == config.get('scope')
                        and report.get('namespace') == scope.get('namespace')
                        and report.get('actualcards_digest') == scope.get('actualcards_digest')
                        for report in ann) for scope in fact_scopes))
        check('ann_evidence_scope_binding', ann_bound, target=name, reason='ann_actual_card_scope_binding_required')
        for kind in ('installed_safety_evidence', 'http_performance_evidence', 'authority_evidence'):
            value = read(kind + '_reference', evidence.get(kind), target=name)
            binding = _binding(value, manifest)
            check(kind + '_target_binding', binding, target=name, reason='candidate_dataset_tool_scope_representation_model_binding_required')
            if kind == 'authority_evidence':
                # No static card/index fingerprint can demonstrate a request
                # pinning one online generation or rechecking authority.
                required = {'namespace_isolation', 'visibility_change', 'revision_race', 'direction', 'generation_fixed'}
                rows = value.get('checks', {}) if value else {}
                cases_ok = isinstance(rows, dict) and required <= set(rows) and all(
                    isinstance(rows[key], dict) and type(rows[key].get('cases')) is int and rows[key]['cases'] > 0
                    and rows[key].get('passed') == rows[key]['cases'] and rows[key].get('failed') == 0 for key in required)
                passed = bool(binding and value.get('format') == 'kg-retrieval-authority-v1' and value.get('ok') is True
                              and value.get('synthetic_only') is True and value.get('index_generation')
                              and cases_ok and _cleanup(value.get('cleanup')))
                check('online_authority_and_generation', passed, target=name, reason='live_authority_generation_evidence_required')
        target_results.append({'id': name, 'run_id': manifest['run_id'], 'split': config.get('split'),
                               'model': manifest.get('model', {}).get('key', 'lexical'),
                               'representation': config.get('representation'), 'scope': config.get('scope'),
                               'integrity_valid': True, 'valid_bound_run': valid_run,
                               'human_bound': labeled, 'blind_executed': blind})
    comparison = read('comparison_reference', spec.get('comparison'))
    expected_runs = {row['run_id'] for row in target_results}
    compared = bool(comparison and len(expected_runs) >= 2 and comparison.get('baseline') in expected_runs and comparison.get('promotion')
                    and {row.get('run_id') for row in comparison['promotion']} == expected_runs - {comparison['baseline']}
                    and all(row.get('status') == 'eligible_for_manual_promotion' for row in comparison['promotion']))
    check('paired_comparison_gates', compared, reason='eligible_paired_comparison_required')
    e2e = spec.get('e2e', {}) if isinstance(spec.get('e2e', {}), dict) else {}
    for kind in ('freeze', 'results', 'human_scores'):
        value = read('e2e_' + kind + '_reference', e2e.get(kind))
        # The independent E2E runner owns this contract. Until an executable
        # validator is available, a JSON template is deliberately insufficient.
        check('e2e_' + kind + '_validated', False, reason='e2e_validation_not_available' if value else 'not_run', status='not_run')
    check('inputs_unchanged_during_audit', reader.unchanged(), reason='artifact_changed_during_read')
    blockers = [item['id'] + (':' + item['target'] if item.get('target') else '')
                for item in checks if item['status'] != 'passed']
    return {'format': FORMAT, 'execution_success': True, 'read_only': True,
            'status': 'blocked' if blockers else 'ready_for_release_review',
            'promotion': 'blocked', 'human_reviewed': validation['reviewed'], 'human_required': 100,
            'tool_code_sha256': tool_fingerprint(), 'checks': checks, 'targets': target_results,
            'blockers': blockers, 'blind_executed_by_audit': False, 'external_model_calls': 0,
            'interpretation': 'Content hashes do not authenticate a reviewer. This audit never creates labels, locks, freeze declarations or blind results, and never executes deployment.'}


def discovered_spec(args, root=ROOT):
    """Only explicitly named local paths; never scans Downloads or a profile."""
    root = Path(root).resolve()
    folder = _inside(root, args.fixture_dir)
    optional = lambda value: reference(value, root) if value else None
    spec = {'format': FORMAT, 'scope': args.scope,
            'dataset': reference(folder / 'dataset.json', root),
            'fixture': reference(folder / 'fixture.json', root),
            'labels': optional(args.labels), 'labels_lock': optional(args.labels_lock),
            'protocol': optional(args.protocol or (folder / 'protocol-template.json')),
            'targets': [{'id': 'target-' + str(index + 1), 'run': reference(path, root)} for index, path in enumerate(args.run)]}
    if args.review_source:
        source = _inside(root, args.review_source)
        spec['review_source'] = {name: reference(source / (name + '.json'), root) for name in ('dataset', 'fixture')}
    return spec


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    source = cli.add_mutually_exclusive_group(required=True)
    source.add_argument('--manifest', type=Path)
    source.add_argument('--fixture-dir', type=Path)
    cli.add_argument('--review-source', type=Path)
    cli.add_argument('--labels', type=Path)
    cli.add_argument('--labels-lock', type=Path)
    cli.add_argument('--protocol', type=Path)
    cli.add_argument('--run', action='append', type=Path, default=[])
    cli.add_argument('--scope', choices=('profile', 'shared'), default='profile')
    cli.add_argument('--output', type=Path, help='Optional new reports/ JSON; never overwrites an artifact')
    args = cli.parse_args(argv)
    try:
        spec = read_json(_inside(ROOT, args.manifest)) if args.manifest else discovered_spec(args, ROOT)
        result = audit(spec, root=ROOT)
        if args.output:
            path = _inside(ROOT, args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('x', encoding='utf-8') as stream:
                json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 2 if result['status'] == 'blocked' else 0
    except (ValueError, OSError, TypeError, KeyError):
        print(json.dumps({'format': FORMAT, 'execution_success': False, 'read_only': True,
                          'status': 'error', 'promotion': 'blocked', 'error': 'invalid_missing_or_changed_local_input'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
