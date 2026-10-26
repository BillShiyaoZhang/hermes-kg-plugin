"""Evidence-based retrieval metrics; all K values are explicit."""
from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict


def percentile(values, p):
    return sorted(values)[max(0, math.ceil(len(values) * p) - 1)] if values else None


def score_query(query, ranked, *, accepted=True):
    # Ranking IDs refer to unique evidence units, not overlapping chunks/cards.
    ranked = list(dict.fromkeys(ranked))
    relevance = query.get('gold', {}).get('relevance', {})
    relevant = {key for key, grade in relevance.items() if grade > 0}
    sufficient = {key for key, grade in relevance.items() if grade >= 2}
    answerable = query.get('gold', {}).get('answerable')
    sets = query.get('gold', {}).get('sufficient_sets', [[key] for key in sufficient])
    enough = any(set(required) <= set(ranked[:5]) for required in sets if required)
    structured = query.get('task_type') in {'aggregate', 'multihop', 'path', 'count'}
    scores = {f'recall@{k}': len(relevant.intersection(ranked[:k])) / len(relevant) if relevant else None for k in (5, 20, 50)}
    dcg = sum((2 ** relevance.get(key, 0) - 1) / math.log2(i + 2) for i, key in enumerate(ranked[:10]))
    ideal = sum((2 ** grade - 1) / math.log2(i + 2) for i, grade in enumerate(sorted(relevance.values(), reverse=True)[:10]))
    scores.update({'ndcg@10': dcg / ideal if ideal else None,
                   'mrr@10': next((1 / (i + 1) for i, key in enumerate(ranked[:10]) if key in relevant), 0) if relevant else None,
                   'precision@5': len(relevant.intersection(ranked[:5])) / 5 if relevant else None,
                   'sufficiency@5': float(enough) if answerable and not structured else None,
                   'acceptance_rate': float(bool(accepted)),
                   'noanswer_false_positive': float(bool(accepted)) if answerable is False else None,
                   'answerable_coverage': float(bool(accepted) and enough) if answerable is True and not structured else None,
                   'structured_task_success': None,
                   'safety_violation': float(bool(set(query.get('forbidden_sources', [])).intersection(ranked)))})
    return scores


def aggregate(rows):
    keys = sorted({key for row in rows for key in row.get('metrics', {})})
    result = {'queries': len(rows), 'metrics': {}, 'denominators': {},
              'family_macro_metrics': {}, 'primary_estimand': 'source-family macro mean',
              'latency_ms': {'p50': percentile([row['latency_ms'] for row in rows], .5),
                             'p95': percentile([row['latency_ms'] for row in rows], .95)}}
    for key in keys:
        values = [row['metrics'][key] for row in rows if row.get('metrics', {}).get(key) is not None]
        result['metrics'][key] = statistics.fmean(values) if values else None
        result['denominators'][key] = len(values)
        families = defaultdict(list)
        for row in rows:
            value = row.get('metrics', {}).get(key)
            if value is not None: families[row['source_family']].append(value)
        result['family_macro_metrics'][key] = statistics.fmean(statistics.fmean(v) for v in families.values()) if families else None
    result['safety_violations'] = sum(row.get('metrics', {}).get('safety_violation', 0) for row in rows)
    return result


def paired_clustered_bootstrap(baseline, candidate, *, metric='ndcg@10', iterations=2000, seed=8128):
    """Resample source families, keeping all paraphrases paired within a cluster.

    The estimand is a macro-average over source families, so prolific families
    do not dominate. p is a paired cluster sign-randomization test, not a
    bootstrap-tail probability mislabeled as a significance test.
    """
    if iterations < 200:
        raise ValueError('Use at least 200 statistical resamples')
    left = {row['id']: row for row in baseline}
    right = {row['id']: row for row in candidate}
    if left.keys() != right.keys():
        raise ValueError('Paired runs must contain identical query IDs')
    clusters = defaultdict(list)
    for key in sorted(left):
        a, b = left[key], right[key]
        if a['source_family'] != b['source_family']:
            raise ValueError('Paired source family mismatch')
        x, y = a['metrics'].get(metric), b['metrics'].get(metric)
        if x is not None and y is not None:
            clusters[a['source_family']].append(y - x)
    differences = [statistics.fmean(values) for _, values in sorted(clusters.items())]
    if not differences:
        return {'metric': metric, 'clusters': 0, 'difference': None, 'ci95': None, 'p': None}
    rng = random.Random(seed)
    observed = statistics.fmean(differences)
    samples, extreme = [], 0
    for _ in range(iterations):
        samples.append(statistics.fmean(rng.choices(differences, k=len(differences))))
        permuted = statistics.fmean(delta * rng.choice((-1, 1)) for delta in differences)
        extreme += abs(permuted) >= abs(observed) - 1e-14
    return {'metric': metric, 'clusters': len(differences), 'queries': sum(map(len, clusters.values())),
            'difference': observed, 'ci95': [percentile(samples, .025), percentile(samples, .975)],
            'p': (extreme + 1) / (iterations + 1), 'iterations': iterations, 'seed': seed,
            'estimand': 'paired source-family macro mean'}


def holm(comparisons, *, alpha=.05):
    valid = sorted(((i, row['p']) for i, row in enumerate(comparisons) if row.get('p') is not None), key=lambda item: item[1])
    adjusted, running = {}, 0.0
    for rank, (index, p) in enumerate(valid):
        running = max(running, min(1.0, (len(valid) - rank) * p))
        adjusted[index] = running
    return [{**row, 'p_holm': adjusted.get(i), 'reject_holm': adjusted.get(i, 1) <= alpha} for i, row in enumerate(comparisons)]
