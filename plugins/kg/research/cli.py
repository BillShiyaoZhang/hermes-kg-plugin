"""Reproducible local research CLI; no default profile or business mutations."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import sys

from . import FORMAT
from .common import digest, read_json, write_json
from .fixtures import validate_labels, write_prepared
from .review import review_html
from .runner import SmokeEmbedder, evaluate, report_runs


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare-fixture', help='200 synthetic + 100 pending real + 60 ontology + 120 static safety')
    prepare.add_argument('--output', required=True)
    prepare.add_argument('--real-snapshot', help='Explicit authorized local snapshot; never sent to an external LLM')
    validate = commands.add_parser('validate-labels')
    validate.add_argument('--fixture-dir', required=True)
    validate.add_argument('--labels', required=True)
    validate.add_argument('--lock', help='Create an immutable lock only after all 100 human slots validate')
    run = commands.add_parser('evaluate')
    run.add_argument('--fixture-dir', required=True)
    run.add_argument('--output', required=True)
    run.add_argument('--labels')
    run.add_argument('--labels-lock')
    run.add_argument('--protocol')
    run.add_argument('--model', default='smoke', help='smoke is explicitly nonsemantic and never promotable')
    run.add_argument('--model-cache', default='reports/retrieval-research/models')
    run.add_argument('--vector-cache', default='reports/retrieval-research/vector-cache.sqlite3')
    run.add_argument('--representation', choices=('values', 'entity', 'entity_relations'), default='entity')
    run.add_argument('--mode', choices=('vector', 'lexical', 'hybrid', 'hybrid_graph'), default='vector')
    run.add_argument('--scope', choices=('profile', 'shared'), default='profile',
                     help='profile restricts every evidence URI to the selected namespace; shared also permits visible shared facts')
    run.add_argument('--split', choices=('dev', 'blind', 'regression'), default='dev')
    run.add_argument('--track', choices=('all', 'facts', 'ontology', 'safety'), default='all')
    run.add_argument('--limit', type=int, help='Small smoke subset, always blocked from promotion')
    run.add_argument('--candidate-budget', type=int, default=50)
    run.add_argument('--graph-direction', choices=('out', 'in', 'both'), default='out')
    run.add_argument('--acceptance-threshold', type=float)
    run.add_argument('--threads', type=int, default=8)
    run.add_argument('--batch-size', type=int, default=8)
    run.add_argument('--max-tokens', type=int, default=512, help='Same declared context limit across compared models')
    run.add_argument('--device', default='cpu', help='cpu, cuda or cuda:N; never silently falls back to CPU')
    run.add_argument('--precision', choices=('float32', 'float16', 'bfloat16'), default='float32')
    report = commands.add_parser('report')
    report.add_argument('--baseline', required=True)
    report.add_argument('--candidate', action='append', required=True)
    report.add_argument('--protocol')
    report.add_argument('--resamples', type=int, default=2000)
    report.add_argument('--output', required=True)
    return cli


def _bundle(path):
    root = Path(path)
    return read_json(root / 'dataset.json'), read_json(root / 'fixture.json')


def _safe_summary(result):
    # Do not echo actual questions, URIs, source text or private profile paths.
    if 'rows' in result:
        groups = {}
        for name in ('facts', 'ontology', 'safety'):
            group = result['groups'][name]
            metrics = group['family_macro_metrics']
            groups[name] = {'queries': group['queries'], 'ndcg@10': metrics.get('ndcg@10'),
                            'recall@50': metrics.get('recall@50'), 'noanswer_fpr': metrics.get('noanswer_false_positive'),
                            'p95_ms': group['latency_ms']['p95']}
        return {'ok': True, 'run_id': result['manifest']['run_id'], 'queries': len(result['rows']),
                'scope': result['manifest']['config']['scope'],
                'promotion': result['promotion'], 'groups': groups, 'index': result['index']}
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == 'prepare-fixture':
            result = write_prepared(args.output, real_snapshot=read_json(args.real_snapshot) if args.real_snapshot else None)
            dataset, fixture = _bundle(args.output)
            page = review_html(dataset, fixture, read_json(Path(args.output) / 'labels-template.json'))
            (Path(args.output) / 'human-review.html').write_text(page, encoding='utf-8')
            from .protocol import protocol_template
            write_json(Path(args.output) / 'protocol-template.json', protocol_template(), replace=False)
        elif args.command == 'validate-labels':
            dataset, fixture = _bundle(args.fixture_dir)
            labels = read_json(args.labels)
            if 'submission_sha256' in labels:
                unsigned = {key: value for key, value in labels.items() if key != 'submission_sha256'}
                if labels['submission_sha256'] != digest(unsigned): raise ValueError('Human submission digest mismatch')
            result = validate_labels(fixture, dataset, labels)
            if args.lock:
                if not result['ok']: raise ValueError('All 100 real items must be manually reviewed before locking')
                write_json(args.lock, {'format': FORMAT, 'validation': result}, replace=False)
        elif args.command == 'evaluate':
            dataset, fixture = _bundle(args.fixture_dir)
            embedder = None
            if args.mode != 'lexical':
                if args.model == 'smoke': embedder = SmokeEmbedder()
                else:
                    if not re.fullmatch(r'cpu|cuda(?::[0-9]+)?', args.device): raise ValueError('Device must be cpu, cuda or cuda:N')
                    from kg.embedding_profiles import create_embedder
                    embedder = create_embedder(args.model, args.model_cache, device=args.device, precision=args.precision,
                                               threads=args.threads, batch_size=args.batch_size, max_tokens=args.max_tokens)
            result = evaluate(dataset, fixture, embedder=embedder, representation=args.representation,
                              mode=args.mode, split=args.split, track=args.track, scope=args.scope, cache_path=args.vector_cache,
                              labels=read_json(args.labels) if args.labels else None,
                              lock=read_json(args.labels_lock) if args.labels_lock else None,
                              protocol=read_json(args.protocol) if args.protocol else None,
                              limit=args.limit, candidate_budget=args.candidate_budget,
                              graph_direction=args.graph_direction, acceptance_threshold=args.acceptance_threshold)
            write_json(args.output, result, replace=False)
        else:
            result = report_runs(read_json(args.baseline), [read_json(path) for path in args.candidate],
                                 protocol=read_json(args.protocol) if args.protocol else None, iterations=args.resamples)
            write_json(args.output, result, replace=False)
        print(json.dumps(_safe_summary(result), ensure_ascii=False, allow_nan=False))
        return 2 if args.command == 'validate-labels' and not result['ok'] else 0
    except (ValueError, OSError, ImportError, RuntimeError) as exc:
        # Arbitrary adapter exceptions may contain private cache paths or URLs.
        print(json.dumps({'ok': False, 'error_type': type(exc).__name__, 'error': str(exc) if isinstance(exc, ValueError) else 'Research operation failed; inspect private diagnostics locally.'}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
