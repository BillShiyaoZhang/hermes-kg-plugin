"""Finite graph plans, exact counts and truthful bounded path evidence."""
from __future__ import annotations

from copy import deepcopy
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kg.config import KGConfig
from kg.ontology import _safe_node
from kg.retrieval_plan import RetrievalPlanError, StructuredGraphSearch, validate_plan

NS = 'urn:planfixture:kg:'


class PlanStore:
    """Semantic fake; query-shape assertions independently check scoped templates."""
    def __init__(self):
        self.config = KGConfig(urn_prefix=NS)
        self.calls, self.edges = [], []
        self.failure = None
        self.schema_failure = None
        self.overlay_reads = 0
        self.nodes = {}
        for name, kind in (('person-lin', 'Person'), ('x_project-alpha', 'KGCustom_x_project'),
                           ('concept-middle', 'Concept'), ('concept-end', 'Concept'),
                           ('concept-isolated', 'Concept'), ('concept-disabled', 'Concept'),
                           ('concept-quarantined', 'Concept')):
            self.nodes[name] = {'labels': [kind], 'properties': {
                'uri': NS + name, 'hermes_name': name, 'hermes_revision': 1}}
        self.nodes['person-lin']['properties']['foaf_name'] = '林舟'
        self.nodes['x_project-alpha']['properties']['hermes_name'] = 'Alpha'
        self.nodes['concept-disabled']['properties']['hermes_enabled'] = False
        self.nodes['concept-quarantined']['labels'].append('KGQuarantined')
        self.nodes['foreign'] = {'labels': ['Concept'], 'properties': {
            'uri': 'urn:foreign:kg:concept-hidden', 'hermes_name': 'PRIVATE-FOREIGN', 'shared': True}}
        for source, target, kind in (
            ('person-lin', 'x_project-alpha', 'X_LEADS'),
            ('person-lin', 'x_project-alpha', 'LEADS_PROJECT'),
            ('x_project-alpha', 'person-lin', 'RELATED_TO'),
            ('person-lin', 'person-lin', 'HERMES_knows'),
            ('person-lin', 'concept-middle', 'RELATED_TO'),
            ('concept-middle', 'concept-end', 'RELATED_TO'),
            ('person-lin', 'concept-isolated', 'UNKNOWN_LEGACY'),
            ('person-lin', 'foreign', 'RELATED_TO'),
            ('person-lin', 'concept-disabled', 'RELATED_TO'),
            ('person-lin', 'concept-quarantined', 'RELATED_TO'),
        ):
            self.add_edge(source, target, kind)

    def add_edge(self, source, target, kind):
        self.edges.append({
            'source_labels': self.nodes[source]['labels'],
            'source_properties': self.nodes[source]['properties'],
            'target_labels': self.nodes[target]['labels'],
            'target_properties': self.nodes[target]['properties'],
            'type': kind, 'properties': {'hermes_note': 'fixture assertion',
                                       'hermes_evidenceJson': 'PRIVATE-SOURCE-BODY'},
            'element_id': 'physical-' + str(len(self.edges)),
        })

    def ontology_overlay(self):
        self.overlay_reads += 1
        if self.schema_failure:
            raise self.schema_failure
        return {'version': 3, 'patch': {
            'entity_types': {'x_project': {'custom': True, 'active': False, 'label': '项目',
                'description': '历史项目', 'storage_label': 'KGCustom_x_project',
                'iri': NS + 'schema:x_project'}},
            'properties': {'x_score': {'custom': True, 'active': False, 'label': '得分',
                'description': '原文明确记录的整数', 'aliases': ['rating'],
                'domain': ['x_project'], 'range': ['integer'], 'required_for': [],
                'managed': False, 'iri': NS + 'schema:x_score'}},
            'relations': {'X_LEADS': {'custom': True, 'active': False, 'label': '负责项目',
                'description': '人物负责项目', 'storage_type': 'X_LEADS',
                'aliases': ['leads_project'], 'domain': ['person'], 'range': ['x_project'],
                'iri': NS + 'schema:X_LEADS'}},
        }}

    def run_read(self, query, params):
        self.calls.append((query, deepcopy(params)))
        if self.failure:
            raise self.failure
        active = lambda node: _safe_node(node, NS) and bool(set(node['labels']) & set(params['entity_labels']))
        nodes = [node for node in self.nodes.values() if active(node)]
        edges = [edge for edge in self.edges
                 if active({'labels': edge['source_labels'], 'properties': edge['source_properties']})
                 and active({'labels': edge['target_labels'], 'properties': edge['target_properties']})
                 and edge['type'].upper() in params['relation_aliases']]
        if 'structured_center' in query:
            return [node for node in nodes if node['properties']['uri'] == params['center_uri']][:2]
        if 'structured_count_entities' in query:
            selected = [node for node in nodes if set(node['labels']) & set(params['selected_labels'])]
            return [{'total': len(selected)}]
        if 'graph_path_endpoints' in query:
            return [node for node in nodes if node['properties']['uri'] in params['endpoint_uris']]
        if 'graph_path_adjacency' in query:
            both = 'a.uri IN $frontier OR' in query
            edges = [edge for edge in edges if edge['source_properties']['uri'] in params['frontier']
                     or both and edge['target_properties']['uri'] in params['frontier']]
            return sorted(edges, key=self._edge_order)[:params['read_limit']]
        if 'center_uri' in params:
            outgoing = 'AND a.uri=$center_uri' in query
            incoming = 'AND b.uri=$center_uri' in query
            edges = [edge for edge in edges
                     if (not incoming and edge['source_properties']['uri'] == params['center_uri'])
                     or (not outgoing and edge['target_properties']['uri'] == params['center_uri'])]
        if 'structured_relations_total' in query or 'structured_count_relations' in query:
            return [{'total': len(edges)}]
        if 'structured_relations_page' in query:
            return sorted(edges, key=self._edge_order)[params['offset']:params['offset'] + params['limit']]
        raise AssertionError('Unexpected read template')

    @staticmethod
    def _edge_order(edge):
        return edge['source_properties']['uri'], edge['type'], edge['target_properties']['uri'], edge['element_id']


class StructuredGraphSearchTests(unittest.TestCase):
    def setUp(self):
        self.store = PlanStore()
        self.search = StructuredGraphSearch(self.store)

    def relations(self, direction='both', **extra):
        return self.search.execute({'op': 'relations', 'center_uri': NS + 'person-lin',
                                    'direction': direction, **extra})

    def path(self, source='person-lin', target='concept-end', direction='out', **extra):
        return self.search.execute({'op': 'path', 'source_uri': NS + source,
                                    'target_uri': NS + target, 'direction': direction, **extra})

    def test_schema_search_only_returns_rule_evidence_including_inactive_definitions(self):
        result = self.search.execute({'op': 'schema', 'query': 'rating'})
        self.assertEqual(result['mode'], 'structured')
        self.assertEqual(result['kg_results'], [])
        self.assertEqual(result['status'], 'found')
        self.assertEqual(len(result['evidence']), 1)
        rule = result['evidence'][0]
        self.assertEqual(rule['kind'], 'rule')
        self.assertTrue(rule['is_rule'])
        self.assertEqual(rule['definition_key'], 'x_score')
        self.assertFalse(rule['definition']['active'])
        self.assertIn('不是已发生的事实', rule['text'])
        self.assertEqual(result['boundaries']['scope'], 'namespace_definitions')
        self.assertEqual(self.store.calls, [])

    def test_schema_query_matches_definition_iri_and_pages_without_fact_claims(self):
        specific = self.search.execute({'op': 'schema', 'query': NS + 'schema:X_LEADS', 'kinds': ['relations']})
        self.assertEqual(specific['evidence'][0]['definition_key'], 'X_LEADS')
        first = self.search.execute({'op': 'schema', 'kinds': ['relations'], 'limit': 1})
        second = self.search.execute({'op': 'schema', 'kinds': ['relations'], 'limit': 1, 'offset': 1})
        self.assertTrue(first['pagination']['has_more'])
        self.assertNotEqual(first['evidence'][0]['id'], second['evidence'][0]['id'])
        self.assertFalse(first['boundaries']['complete'])
        self.assertEqual(self.search.execute({'op': 'schema', 'query': 'nonexistent-rule'})['status'], 'empty')

    def test_relations_keep_direction_self_loops_and_historical_inactive_types(self):
        out, incoming, both = self.relations('out'), self.relations('in'), self.relations('both')
        self.assertEqual(out['pagination']['total'], 4)
        self.assertEqual(incoming['pagination']['total'], 2)
        self.assertEqual(both['pagination']['total'], 5)
        loops = [edge for edge in both['evidence'] if edge['source']['uri'] == edge['target']['uri']]
        self.assertEqual(len(loops), 1)
        self.assertEqual(loops[0]['relation_type'], 'HERMES_knows')
        custom = [edge for edge in out['evidence'] if edge['relation_type'] == 'X_LEADS']
        self.assertEqual(len(custom), 2)
        self.assertTrue(all(edge['target']['type'] == 'x_project' for edge in custom))
        self.assertNotIn('PRIVATE-', json.dumps(both))
        self.assertNotIn('UNKNOWN_LEGACY', json.dumps(both))

    def test_relation_pages_limit_edges_not_neighbors_and_aliases_are_canonical(self):
        first = self.relations('out', relation_types=['leads_project'], limit=1)
        second = self.relations('out', relation_types=['X_LEADS'], limit=1, offset=1)
        self.assertEqual(len(first['evidence']), 1)
        self.assertEqual(first['pagination']['total'], 2)
        self.assertEqual(first['pagination']['next_offset'], 1)
        self.assertFalse(second['pagination']['has_more'])
        self.assertTrue(first['boundaries']['truncated'])
        self.assertEqual(first['evidence'][0]['relation_type'], 'X_LEADS')
        self.assertEqual(second['evidence'][0]['relation_type'], 'X_LEADS')
        self.assertEqual(first['plan']['relation_types'], ['X_LEADS'])

    def test_empty_relations_and_missing_center_have_different_status(self):
        empty = self.search.execute({'op': 'relations', 'center_uri': NS + 'concept-isolated', 'direction': 'in'})
        missing = self.search.execute({'op': 'relations', 'center_uri': NS + 'concept-missing', 'direction': 'in'})
        self.assertEqual(empty['status'], 'empty')
        self.assertEqual(empty['pagination']['total'], 0)
        self.assertEqual(missing['status'], 'not_found')
        self.assertNotIn('pagination', missing)

    def test_complete_counts_use_full_graph_not_top_k_and_count_self_once(self):
        entities = self.search.execute({'op': 'count', 'target': 'entities'})
        self.assertEqual(entities['evidence'][0]['value'], 5)
        project = self.search.execute({'op': 'count', 'target': 'entities', 'types': ['x_project']})
        self.assertEqual(project['evidence'][0]['value'], 1)
        all_edges = self.search.execute({'op': 'count', 'target': 'relations'})
        self.assertEqual(all_edges['evidence'][0]['value'], 6)
        centered = self.search.execute({'op': 'count', 'target': 'relations',
                                       'center_uri': NS + 'person-lin', 'direction': 'both'})
        self.assertEqual(centered['evidence'][0]['value'], 5)
        for result in (entities, project, all_edges, centered):
            self.assertTrue(result['evidence'][0]['complete'])
            self.assertTrue(result['boundaries']['complete'])
            self.assertEqual(result['evidence'][0]['kind'], 'count')
        count_queries = [query for query, _ in self.store.calls if 'structured_count_' in query]
        self.assertTrue(all('LIMIT' not in query and 'SKIP' not in query for query in count_queries))

    def test_aggregate_failure_or_invalid_result_never_becomes_zero(self):
        self.store.failure = ConnectionError('PRIVATE-PASSWORD')
        failed = self.search.execute({'op': 'count', 'target': 'relations'})
        self.assertEqual(failed['status'], 'unavailable')
        self.assertEqual(failed['evidence'], [])
        self.assertFalse(failed['boundaries']['complete'])
        self.assertNotIn('PRIVATE-PASSWORD', json.dumps(failed))
        with patch.object(self.store, 'run_read', return_value=[]):
            invalid = self.search.execute({'op': 'count', 'target': 'entities'})
        self.assertEqual(invalid['status'], 'unavailable')
        self.assertEqual(invalid['evidence'], [])

    def test_schema_read_failure_and_concurrent_publication_withhold_evidence(self):
        self.store.schema_failure = ConnectionError('PRIVATE-SCHEMA-DETAIL')
        failed = self.search.execute({'op': 'count', 'target': 'entities'})
        self.assertEqual(failed['status'], 'unavailable')
        self.assertEqual(failed['errors'][0]['code'], 'ontology_schema_unavailable')
        self.assertIsNone(failed['boundaries']['schema_version'])
        self.assertNotIn('PRIVATE-SCHEMA-DETAIL', json.dumps(failed))
        self.store.schema_failure = None
        original = self.store.ontology_overlay
        reads = []
        def publishing():
            result = original()
            reads.append(True)
            if len(reads) > 1:
                result['version'] += 1
            return result
        with patch.object(self.store, 'ontology_overlay', side_effect=publishing):
            changed = self.search.execute({'op': 'count', 'target': 'entities'})
        self.assertEqual(changed['status'], 'unavailable')
        self.assertEqual(changed['evidence'], [])
        self.assertTrue(changed['boundaries']['schema_changed'])
        self.assertEqual(changed['errors'][0]['code'], 'ontology_schema_changed')

    def test_paths_keep_traversal_direction_separate_from_fact_direction(self):
        result = self.path()
        self.assertEqual(result['status'], 'found')
        self.assertEqual(result['evidence'][0]['node_uris'],
                         [NS + 'person-lin', NS + 'concept-middle', NS + 'concept-end'])
        self.assertTrue(all(not step['traversed_reverse'] for step in result['evidence'][0]['steps']))
        reverse = self.path('x_project-alpha', 'person-lin', direction='both', relation_types=['X_LEADS'])
        self.assertEqual(reverse['status'], 'found')
        self.assertTrue(all(path['steps'][0]['traversed_reverse'] for path in reverse['evidence']))
        self.assertTrue(all(path['steps'][0]['relation_source_uri'] == NS + 'person-lin'
                            for path in reverse['evidence']))
        directed = self.path('x_project-alpha', 'person-lin', relation_types=['X_LEADS'])
        self.assertEqual(directed['status'], 'no_path')

    def test_missing_no_path_budget_exhaustion_and_timeout_are_distinct(self):
        missing = self.path(target='concept-missing')
        no_path = self.path(target='concept-isolated')
        hop_bound = self.path(max_hops=1)
        self.assertEqual(missing['status'], 'not_found')
        self.assertEqual(no_path['status'], 'no_path')
        self.assertEqual(hop_bound['status'], 'no_path')
        self.assertIn('max_hops', hop_bound['boundaries']['negative_claim_scope'])
        with patch('kg.graph_exploration.PATH_EDGE_BUDGET', 1):
            exhausted = self.path()
        self.assertEqual(exhausted['status'], 'incomplete')
        self.assertTrue(exhausted['boundaries']['truncated'])
        self.assertFalse(exhausted['boundaries']['complete'])
        self.store.failure = TimeoutError('PRIVATE-TIMEOUT-DETAIL')
        timeout = self.path()
        self.assertEqual(timeout['status'], 'timed_out')
        self.assertTrue(timeout['boundaries']['timed_out'])
        self.assertNotIn('PRIVATE-TIMEOUT-DETAIL', json.dumps(timeout))

    def test_same_node_path_is_zero_hop_not_a_fabricated_self_relation(self):
        result = self.path(target='person-lin')
        self.assertEqual(result['status'], 'found')
        self.assertEqual(result['evidence'][0]['node_uris'], [NS + 'person-lin'])
        self.assertEqual(result['evidence'][0]['steps'], [])
        self.assertEqual(result['evidence'][0]['edge_ids'], [])

    def test_private_cross_namespace_shared_and_unknown_uri_kinds_are_rejected(self):
        for plan in (
            {'op': 'relations', 'center_uri': 'urn:foreign:kg:concept-hidden', 'direction': 'both'},
            {'op': 'path', 'source_uri': NS + 'person-lin',
             'target_uri': 'urn:foreign:kg:concept-hidden', 'direction': 'out'},
            {'op': 'relations', 'center_uri': NS + 'unknown-one', 'direction': 'both'},
        ):
            with self.subTest(plan=plan), self.assertRaises(RetrievalPlanError):
                self.search.execute(plan)
        self.assertEqual(self.store.calls, [])

    def test_templates_parameterize_inputs_and_constrain_both_endpoints(self):
        self.relations('both', relation_types=['leads_project'])
        self.search.execute({'op': 'count', 'target': 'entities', 'types': ['x_project']})
        for query, params in self.store.calls:
            self.assertNotIn(NS, query)
            self.assertNotIn('X_LEADS', query)
            self.assertEqual(params['prefix'], NS)
            self.assertIn('KGQuarantined', query)
            self.assertIn('KGLegacyAlias', query)
            self.assertIn('KGLegacyUnresolved', query)
            if 'MATCH (a)-[r]->(b)' in query:
                self.assertIn('a.uri STARTS WITH $prefix', query)
                self.assertIn('b.uri STARTS WITH $prefix', query)
                self.assertIn('toUpper(type(r)) IN $relation_aliases', query)

    def test_strict_invalid_plans_reject_extra_cypher_fields_and_unsupported_time(self):
        bad = [None, [], {}, {'op': 'cypher', 'query': 'MATCH (n) RETURN n'},
               {'op': 'schema', 'namespace': 'foreign'}, {'op': 'schema', 'query': []},
               {'op': 'schema', 'query': 'x' * 201}, {'op': 'schema', 'kinds': []},
               {'op': 'schema', 'kinds': ['facts']}, {'op': 'schema', 'limit': True},
               {'op': 'schema', 'offset': -1}, {'op': 'schema', 'limit': 101},
               {'op': 'relations', 'center_uri': NS + 'person-lin'},
               {'op': 'relations', 'center_uri': NS + 'person-lin', 'direction': 'out', 'limit': '2'},
               {'op': 'relations', 'center_uri': NS + 'person-lin', 'direction': 'out',
                'relation_types': ['X_LEADS`]->() DELETE n //']},
               {'op': 'count', 'target': 'entities', 'types': ['Unsupported']},
               {'op': 'count', 'target': 'entities', 'center_uri': NS + 'person-lin'},
               {'op': 'count', 'target': 'relations', 'direction': 'both'},
               {'op': 'count', 'target': 'relations', 'types': ['Person']},
               {'op': 'path', 'source_uri': NS + 'person-lin', 'target_uri': NS + 'concept-end', 'direction': 'in'},
               {'op': 'path', 'source_uri': NS + 'person-lin', 'target_uri': NS + 'concept-end',
                'direction': 'out', 'max_hops': 7},
               {'op': 'path', 'source_uri': NS + 'person-lin', 'target_uri': NS + 'concept-end',
                'direction': 'out', 'from': '2026-10-01'},
               {'op': 'path', 'source_uri': NS + 'person-lin', 'target_uri': NS + 'concept-end',
                'direction': 'out', 'filters': {'time': '2026-10-01'}},
               ]
        for plan in bad:
            with self.subTest(plan=plan), self.assertRaises(RetrievalPlanError):
                self.search.execute(plan)
        self.assertEqual(self.store.calls, [])
        with self.assertRaisesRegex(RetrievalPlanError, 'time filters'):
            self.path(start='2026-10-01')

    def test_validation_is_pure_canonical_and_does_not_mutate_input(self):
        schema = self.search._load_schema()
        original = {'op': 'relations', 'center_uri': NS + 'Person-lin', 'direction': 'out',
                    'relation_types': ['leads_project', 'X_LEADS']}
        before = deepcopy(original)
        result = validate_plan(original, schema, NS)
        self.assertEqual(original, before)
        self.assertEqual(result['center_uri'], NS + 'person-lin')
        self.assertEqual(result['relation_types'], ['X_LEADS'])
        self.assertEqual(self.store.calls, [])
        with self.assertRaisesRegex(RetrievalPlanError, 'namespace'):
            validate_plan(original, schema, 'urn:other:kg:')


@unittest.skipUnless(os.environ.get('KG_TEST_BOLT_URL'), 'isolated Neo4j test URL not configured')
class StructuredGraphSearchNeo4jTests(unittest.TestCase):
    def setUp(self):
        from kg.store import KGStore
        notifications = logging.getLogger('neo4j.notifications')
        original_level = notifications.level
        notifications.setLevel(logging.ERROR)
        self.addCleanup(notifications.setLevel, original_level)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.prefix = 'urn:plantest' + uuid.uuid4().hex + ':kg:'
        self.foreign = 'urn:planforeign' + uuid.uuid4().hex + ':kg:'
        config = KGConfig(home=Path(directory.name), neo4j_url=os.environ['KG_TEST_BOLT_URL'],
                          password=os.environ['KG_TEST_PASSWORD'], urn_prefix=self.prefix,
                          default_shared=False)
        self.store = KGStore(config)
        self.addCleanup(self.store.close)
        self.addCleanup(self.clear)
        patch_value = PlanStore().ontology_overlay()['patch']
        with self.store.driver.session(database=config.database) as session:
            session.run('''CREATE (:KGOntologySchema {namespace:$prefix,version:7,patch_json:$patch}),
                (a:Person {uri:$prefix+'person-lin',foaf_name:'Lin'}),
                (b:KGCustom_x_project {uri:$prefix+'x_project-alpha',hermes_name:'Alpha'}),
                (c:Concept {uri:$prefix+'concept-end',hermes_name:'End'}),
                (d:Concept {uri:$foreign+'concept-shared',hermes_name:'FOREIGN',shared:true}),
                (e:Concept {uri:$foreign+'concept-private',hermes_name:'PRIVATE-FOREIGN',shared:false}),
                (a)-[:X_LEADS {hermes_note:'first'}]->(b),
                (a)-[:X_LEADS {hermes_note:'second'}]->(b),
                (a)-[:RELATED_TO]->(a),(a)-[:RELATED_TO]->(c),
                (a)-[:RELATED_TO]->(d),(a)-[:RELATED_TO]->(e)''',
                {'prefix': self.prefix, 'foreign': self.foreign, 'patch': json.dumps(patch_value)}).consume()
        self.search = StructuredGraphSearch(self.store)

    def clear(self):
        with self.store.driver.session(database=self.store.config.database) as session:
            session.run('''MATCH (n) WHERE n.uri STARTS WITH $prefix OR n.uri STARTS WITH $foreign
                OR n.namespace=$prefix DETACH DELETE n''',
                {'prefix': self.prefix, 'foreign': self.foreign}).consume()
            remaining = session.run('''MATCH (n) WHERE n.uri STARTS WITH $prefix
                OR n.uri STARTS WITH $foreign OR n.namespace=$prefix RETURN count(n) AS total''',
                {'prefix': self.prefix, 'foreign': self.foreign}).single()['total']
            self.assertEqual(remaining, 0, 'Synthetic graph-plan namespace cleanup incomplete')

    def test_live_dynamic_rules_full_counts_direction_pagination_and_paths(self):
        rule = self.search.execute({'op': 'schema', 'query': 'rating'})
        self.assertEqual(rule['status'], 'found', rule)
        self.assertEqual(rule['evidence'][0]['kind'], 'rule')
        entities = self.search.execute({'op': 'count', 'target': 'entities'})
        relations = self.search.execute({'op': 'count', 'target': 'relations'})
        self.assertEqual(entities['evidence'][0]['value'], 3, entities)
        self.assertEqual(relations['evidence'][0]['value'], 4, relations)
        page = self.search.execute({'op': 'relations', 'center_uri': self.prefix + 'person-lin',
                                    'direction': 'out', 'relation_types': ['leads_project'], 'limit': 1})
        self.assertEqual(page['pagination']['total'], 2, page)
        self.assertTrue(page['pagination']['has_more'])
        second = self.search.execute({'op': 'relations', 'center_uri': self.prefix + 'person-lin',
                                      'direction': 'out', 'relation_types': ['X_LEADS'], 'limit': 1, 'offset': 1})
        self.assertNotEqual(page['evidence'][0]['id'], second['evidence'][0]['id'])
        for direction, total in (('out', 4), ('in', 1), ('both', 4)):
            local = self.search.execute({'op': 'relations', 'center_uri': self.prefix + 'person-lin',
                                         'direction': direction})
            self.assertEqual(local['pagination']['total'], total, local)
            self.assertNotIn(self.foreign, json.dumps(local))
        incoming = self.search.execute({'op': 'relations', 'center_uri': self.prefix + 'person-lin',
                                        'direction': 'in'})
        self.assertEqual(incoming['pagination']['total'], 1, incoming)
        path = self.search.execute({'op': 'path', 'source_uri': self.prefix + 'x_project-alpha',
                                    'target_uri': self.prefix + 'concept-end', 'direction': 'both'})
        self.assertEqual(path['status'], 'found', path)
        self.assertTrue(path['evidence'][0]['steps'][0]['traversed_reverse'])
        self.assertFalse(path['boundaries']['timed_out'])
        self.assertNotIn(self.foreign, json.dumps(path))
        directed = self.search.execute({'op': 'path', 'source_uri': self.prefix + 'x_project-alpha',
                                        'target_uri': self.prefix + 'concept-end', 'direction': 'out'})
        self.assertEqual(directed['status'], 'no_path', directed)
        missing = self.search.execute({'op': 'path', 'source_uri': self.prefix + 'person-missing',
                                       'target_uri': self.prefix + 'concept-end', 'direction': 'out'})
        self.assertEqual(missing['status'], 'not_found', missing)
        # A committed archival is visible on the very next explicit plan;
        # neither count nor graph plans depend on embedding/index refresh.
        with self.store.driver.session(database=self.store.config.database) as session:
            session.run('MATCH (n {uri:$uri}) SET n:KGQuarantined',
                        {'uri': self.prefix + 'concept-end'}).consume()
        after_count = self.search.execute({'op': 'count', 'target': 'entities'})
        self.assertEqual(after_count['evidence'][0]['value'], 2, after_count)
        after_relations = self.search.execute({'op': 'count', 'target': 'relations'})
        self.assertEqual(after_relations['evidence'][0]['value'], 3, after_relations)
        after_path = self.search.execute({'op': 'path', 'source_uri': self.prefix + 'person-lin',
                                          'target_uri': self.prefix + 'concept-end', 'direction': 'out'})
        self.assertEqual(after_path['status'], 'not_found', after_path)


if __name__ == '__main__':
    unittest.main()
