#!/usr/bin/env python3
"""Offline by default: freeze/preflight replay inputs before any main-model call."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins'))
from kg import research_e2e as e2e


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', choices=('template', 'preflight', 'execute', 'route', 'score'), default='preflight')
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--baseline-run', type=Path)
    parser.add_argument('--candidate-run', type=Path)
    parser.add_argument('--home', type=Path, help='Never defaults to a real profile; required for route/execute')
    parser.add_argument('--output', type=Path, help='Local reports/.../private/... artifact or execution directory')
    parser.add_argument('--human-scores', type=Path)
    args = parser.parse_args(argv)
    result = None
    try:
        if args.action == 'template':
            if args.output is None:
                raise ValueError('An explicit private output file is required')
            from kg.research.common import write_json
            write_json(e2e.private_path(args.output, ROOT), e2e.plan_template(), replace=False)
            result = {'format': e2e.FORMAT, 'ready': False, 'external_calls': 0, 'reason': 'template_requires_human_lock_and_freeze'}
        elif args.action == 'route':
            value = e2e.profile_request(args.home, mode='probe')
            result = {'format': e2e.FORMAT, 'external_calls': 0, 'route': value['route'], 'promotion': 'blocked'}
        elif args.action == 'score':
            if args.output is None or args.human_scores is None:
                raise ValueError('Explicit execution directory and human score file are required')
            result = e2e.audit_human_scores(args.output, e2e.read(e2e.private_path(args.human_scores, ROOT)), root=ROOT)
        else:
            if args.plan is None or args.baseline_run is None or args.candidate_run is None:
                raise ValueError('Explicit plan, baseline-run and candidate-run are required; no profile or blind run was loaded')
            plan = e2e.read(e2e.private_path(args.plan, ROOT))
            runs = {arm: e2e.read(e2e.private_path(path, ROOT)) for arm, path in
                    [('baseline', args.baseline_run), ('candidate', args.candidate_run)]}
            prepared = e2e.prepare_replay(plan, runs, root=ROOT)
            if args.action == 'preflight':
                result = {'format': e2e.FORMAT, 'ready': True, 'planned_calls': prepared['planned_calls'],
                          'external_calls': 0, 'promotion': 'blocked', 'human_scores_status': 'pending',
                          'mode': prepared['mode'], 'plan_sha256': prepared['plan_sha256']}
            else:
                if args.output is None or args.home is None:
                    raise ValueError('Execution requires explicit --home and private --output')
                result = e2e.execute_replay(plan, prepared, home=args.home, output=args.output, root=ROOT)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        if args.action == 'execute':
            return 0 if result.get('complete') == e2e.MAX_CALLS and not result.get('unknown_or_reserved') else 2
        return 0 if result.get('ok', result.get('ready', True)) else 2
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        print(json.dumps({'format': e2e.FORMAT, 'ready': False, 'promotion': 'blocked',
                          'external_calls': None if args.action == 'execute' else 0,
                          'error_type': type(exc).__name__, 'reason': str(exc) if type(exc) is ValueError else 'input_or_execution_unavailable'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
