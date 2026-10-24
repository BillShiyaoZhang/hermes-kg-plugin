#!/usr/bin/env python3
"""Measure graph services on an isolated 10k-node / 30k-edge synthetic fixture.

Requires KG_TEST_BOLT_URL and KG_TEST_PASSWORD for a disposable development
Neo4j. Never points at the normal KG connection configuration. The random
namespace is owned by this invocation and is removed in finally.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import platform
from pathlib import Path
import re
import statistics
import sys
import tempfile
import time
import uuid

from neo4j import Query

from kg.config import KGConfig
from kg.ontology import OntologyService
from kg.schema import ENTITY_LABELS
from kg.store import KGStore


NODE_COUNT = 10_000
EDGE_COUNT = 30_000
ISOLATES = 100
SELF_LOOPS = 30
PARALLEL_EDGES = 1000


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def run(samples):
    url = os.environ.get('KG_TEST_BOLT_URL')
    password = os.environ.get('KG_TEST_PASSWORD')
    if not url or password is None:
        raise ValueError('Set KG_TEST_BOLT_URL and KG_TEST_PASSWORD for a disposable development database')
    prefix = 'urn:graphbenchmark' + uuid.uuid4().hex + ':kg:'
    check(bool(re.fullmatch(r'urn:graphbenchmark[a-f0-9]{32}:kg:', prefix)), 'invalid owned namespace')
    report = {'fixture': {'namespace': prefix, 'nodes': NODE_COUNT, 'edges': EDGE_COUNT,
                          'isolates': ISOLATES, 'self_loops': SELF_LOOPS, 'parallel_edges': PARALLEL_EDGES},
              'environment': {'os': platform.platform(), 'python': platform.python_version(),
                              'architecture': platform.machine(), 'cpu': platform.processor(),
                              'logical_cpus': os.cpu_count()},
              'method': {'transport': 'direct OntologyService / Bolt; no HTTP or browser',
                         'samples_per_operation': samples,
                         'cache': 'First read follows fixture creation; not a cold-cache measurement.',
                         'aggregation_check': 'Required from complete 10000/30000 counts; not a rendered UI assertion.'},
              'operations': {}, 'cleanup': {'complete': False}}
    with tempfile.TemporaryDirectory(prefix='graph-benchmark-') as temporary:
        config = KGConfig(home=Path(temporary), neo4j_url=url,
                          user=os.environ.get('KG_TEST_USER', 'neo4j'), password=password,
                          urn_prefix=prefix, default_shared=False)
        with KGStore(config) as store:
            report['method']['query_timeout_seconds'] = config.query_timeout
            def read(query, params=None):
                return store.run_read(query, {'prefix': prefix, **(params or {})})
            def write(query, params=None):
                with store.driver.session(database=config.database) as session:
                    session.run(Query(query, timeout=60), {'prefix': prefix, **(params or {})}).consume()
            check(read('MATCH (n) WHERE n.uri STARTS WITH $prefix RETURN count(n) AS total')[0]['total'] == 0,
                  'random namespace must be empty before fixture creation')
            try:
                created = time.perf_counter()
                offset, uris, counts = 0, [], {}
                for index, (kind, label) in enumerate(ENTITY_LABELS.items()):
                    count = NODE_COUNT // 6 if index < 5 else NODE_COUNT - offset
                    counts[kind] = count
                    # Only label text from the library's whitelist enters Cypher.
                    write(f'''UNWIND range($offset,$offset+$count-1) AS i
                        CREATE (n:KGEntity:{label} {{uri:$prefix+$kind+'-'+toString(i),
                            hermes_name:'Synthetic '+$kind+' '+toString(i),
                            hermes_description:'Synthetic graph capacity fixture',hermes_revision:1,
                            hermes_enabled:true,benchmark_index:i}})
                        SET n.foaf_name=CASE WHEN $kind='person' THEN 'Synthetic person '+toString(i) ELSE null END,
                            n.hermes_eventTime=CASE WHEN $kind='event' THEN datetime('2026-10-24T10:30:00+08:00') ELSE null END,
                            n.hermes_timePrecision=CASE WHEN $kind='event' THEN 'datetime' ELSE null END,
                            n.hermes_preferenceKey=CASE WHEN $kind='preference' THEN 'Synthetic preference' ELSE null END,
                            n.hermes_preferenceValue=CASE WHEN $kind='preference' THEN 'Synthetic value' ELSE null END,
                            n.hermes_decisionText=CASE WHEN $kind='decision' THEN 'Synthetic decision' ELSE null END''',
                          {'offset': offset, 'count': count, 'kind': kind})
                    uris.extend(prefix + kind + '-' + str(i) for i in range(offset, offset + count))
                    offset += count
                concept_start = sum(counts[kind] for kind in list(ENTITY_LABELS)[:-1])
                write('''MATCH (n:KGEntity) WHERE n.uri STARTS WITH $prefix
                    WITH n ORDER BY n.benchmark_index WITH collect(n) AS nodes
                    UNWIND range(0,$edges-1) AS i
                    WITH i,nodes,
                        CASE WHEN i<$parallel THEN $concept_start
                             WHEN i<$parallel+$self_loops THEN i-$parallel
                             ELSE (i-$parallel-$self_loops)%$connected END AS source_index
                    WITH i,nodes,source_index,
                        CASE WHEN i<$parallel THEN $concept_start+1
                             WHEN i<$parallel+$self_loops THEN source_index
                             ELSE (source_index*17+1)%$connected END AS target_index
                    WITH i,nodes[source_index] AS a,nodes[target_index] AS b
                    CREATE (a)-[:RELATED_TO {benchmark_sequence:i}]->(b)''',
                      {'edges': EDGE_COUNT, 'parallel': PARALLEL_EDGES, 'self_loops': SELF_LOOPS,
                       'connected': NODE_COUNT - ISOLATES, 'concept_start': concept_start})
                write('''MATCH (n) WHERE n.uri IN $dense_uris
                    SET n.hermes_name='Synthetic benchmark dense pair '+toString(n.benchmark_index)''',
                      {'dense_uris': [uris[concept_start], uris[concept_start + 1]]})
                report['fixture']['creation_seconds'] = round(time.perf_counter() - created, 6)
                report['fixture']['groups'] = counts
                check(read('''MATCH (n) WHERE n.uri STARTS WITH $prefix AND NOT EXISTS { MATCH (n)-[]-() }
                    RETURN count(n) AS total''')[0]['total'] == ISOLATES, 'isolate count differs')
                check(read('MATCH (n)-[r]->(n) WHERE n.uri STARTS WITH $prefix RETURN count(r) AS total')[0]['total'] == SELF_LOOPS,
                      'self-loop count differs')
                parallel_uris = [uris[concept_start], uris[concept_start + 1]]
                check(read('''MATCH (a)-[r]->(b) WHERE a.uri=$source AND b.uri=$target
                    RETURN count(r) AS total''', {'source': parallel_uris[0], 'target': parallel_uris[1]})[0]['total'] == PARALLEL_EDGES,
                      'parallel edge count differs')
                service = OntologyService(config, store)
                def measure(name, operation, validate):
                    durations, last = [], None
                    for _ in range(samples):
                        started = time.perf_counter()
                        last = operation()
                        durations.append(round(time.perf_counter() - started, 6))
                        validate(last)
                    report['operations'][name] = {'seconds': durations, 'median_seconds': round(statistics.median(durations), 6),
                                                 'min_seconds': min(durations), 'max_seconds': max(durations)}
                    return last
                def valid_overview(result):
                    check(result['stats_available'], 'complete statistics unavailable')
                    check(result['total_nodes'] == NODE_COUNT and result['total_edges'] == EDGE_COUNT, 'complete counts differ')
                    check({item['type']: item['count'] for item in result['groups']} == counts, 'type counts differ')
                    check(sum(item['count'] for item in result['relations']) == EDGE_COUNT, 'relation aggregates lost edges')
                overview = measure('overview', service.graph_overview, valid_overview)
                def valid_raw(result):
                    check(result['total_nodes'] == NODE_COUNT and len(result['nodes']) == 200, 'node budget differs')
                    check(len(result['edges']) == 500 and result['truncated_edges'], 'dense edge budget differs')
                    check(result['truncated_nodes'], 'node truncation not reported')
                    check(set(parallel_uris) <= {node['uri'] for node in result['nodes']}, 'parallel endpoints missing')
                raw = measure('raw_graph_200_500', lambda: service.graph(node_limit=200, edge_limit=500), valid_raw)
                report['raw_result'] = {'returned_nodes': len(raw['nodes']), 'returned_edges': len(raw['edges']),
                                        'total_nodes': raw['total_nodes'], 'returned_node_edge_total': raw['total_edges'],
                                        'truncated_nodes': raw['truncated_nodes'], 'truncated_edges': raw['truncated_edges'],
                                        'required_presentation': 'aggregate' if overview['total_nodes'] > 200 or overview['total_edges'] > 500 else 'complete'}
                def valid_dense_overview(result):
                    check(result['stats_available'] and result['total_nodes'] == 2 and result['total_edges'] == PARALLEL_EDGES,
                          'dense-pair complete counts differ')
                dense_overview = measure('dense_pair_overview', lambda: service.graph_overview(q='benchmark dense pair'), valid_dense_overview)
                def valid_dense_raw(result):
                    check(len(result['nodes']) == 2 and result['total_nodes'] == 2 and not result['truncated_nodes'],
                          'dense-pair node total differs')
                    check(len(result['edges']) == 500 and result['total_edges'] == PARALLEL_EDGES and result['truncated_edges'],
                          'dense-pair edge budget differs')
                dense_raw = measure('dense_pair_graph_200_500', lambda: service.graph(q='benchmark dense pair', node_limit=200, edge_limit=500), valid_dense_raw)
                report['edge_only_result'] = {'total_nodes': dense_overview['total_nodes'], 'total_edges': dense_overview['total_edges'],
                                             'returned_edges': len(dense_raw['edges']), 'truncated_nodes': dense_raw['truncated_nodes'],
                                             'truncated_edges': dense_raw['truncated_edges'],
                                             'required_presentation': 'aggregate' if dense_overview['total_edges'] > 500 else 'complete'}
                last_offset = (counts['concept'] - 1) // 50 * 50
                pages = []
                for name, offset in (('first', 0), ('second', 50), ('last', last_offset)):
                    def valid_page(page, offset=offset):
                        check(page['total'] == counts['concept'], 'type total differs')
                        check(page['offset'] == offset and len(page['items']) == min(50, counts['concept'] - offset), 'member page differs')
                        check(all(item['type'] == 'concept' and item['uri'].startswith(prefix) for item in page['items']), 'member scope differs')
                    page = measure('concept_members_' + name,
                                   lambda offset=offset: service.entities(type='concept', offset=offset, limit=50), valid_page)
                    pages.append(page)
                check(not ({item['uri'] for item in pages[0]['items']} & {item['uri'] for item in pages[1]['items']}),
                      'member pages overlap')
                check(pages[0]['has_more'] and pages[1]['has_more'] and not pages[-1]['has_more'], 'member pagination boundary differs')
                report['members_result'] = {'type': 'concept', 'total': counts['concept'],
                                             'offsets': [page['offset'] for page in pages],
                                             'returned': [len(page['items']) for page in pages],
                                             'has_more': [page['has_more'] for page in pages]}
            except Exception as exc:
                report['failure'] = {'error_type': type(exc).__name__}
                raise
            finally:
                # Exact invocation-owned namespace only; never accept a caller's
                # arbitrary prefix or enumerate another namespace for deletion.
                check(bool(re.fullmatch(r'urn:graphbenchmark[a-f0-9]{32}:kg:', prefix)), 'refusing unsafe cleanup prefix')
                write('MATCH (n) WHERE n.uri STARTS WITH $prefix DETACH DELETE n')
                remaining = read('MATCH (n) WHERE n.uri STARTS WITH $prefix RETURN count(n) AS total')[0]['total']
                report['cleanup'] = {'complete': remaining == 0, 'remaining_nodes': remaining}
                print(json.dumps(report, ensure_ascii=False, indent=2))
                check(remaining == 0, 'fixture cleanup incomplete')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.samples <= 20:
        parser.error('--samples must be from 1 to 20')
    logging.getLogger('neo4j.notifications').setLevel(logging.ERROR)
    try:
        run(args.samples)
    except Exception as exc:
        # Never print database exception text or connection credentials.
        print('Graph benchmark failed: ' + type(exc).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
