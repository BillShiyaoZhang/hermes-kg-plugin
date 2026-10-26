"""Typed-graph diagnostic design and evidence metrics; model fakes only."""
from copy import deepcopy
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

SCRIPT = Path(__file__).resolve().parents[3] / 'scripts/research_graph_semantics.py'
SPEC = importlib.util.spec_from_file_location('graph_semantics_diagnostic', SCRIPT)
diagnostic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostic)


class FakeEmbedder:
    """Explicitly not semantic and never accepted by the real CLI contract."""
    model_manifest = {'ready': True, 'real_vectors': False, 'device': 'cpu', 'max_tokens': 512}
    fingerprint = 'fake-model-for-test-only'
    def __init__(self):
        self.prepare_calls = 0
    def prepare(self, **kwargs):
        self.prepare_calls += 1
        if kwargs != {'allow_download': False}:
            raise AssertionError('Diagnostic must stay offline')
    def chunk(self, text):
        return [SimpleNamespace(text=text)]
    def embed_passages(self, texts):
        vectors = []
        for text in texts:
            values = [int(diagnostic.digest(text)[index:index + 2], 16) + 1 for index in range(0, 16, 2)]
            vectors.append(values)
        return vectors
    def embed_query(self, text):
        return self.embed_passages([text])[0]


class GraphSemanticsDiagnosticTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.output = Path(directory.name) / 'diagnostic'
        self.dataset, self.questions = diagnostic.build_fixture()

    def question(self, identifier):
        return next(question for question in self.questions if question['id'] == identifier)

    def test_fixed_54_questions_cover_distinct_types_fields_graph_and_rule_tasks(self):
        self.assertEqual(len(self.questions), 54)
        self.assertEqual(len({question['family'] for question in self.questions}), 9)
        self.assertTrue(all(sum(q['family'] == family for q in self.questions) == 6
                            for family in {q['family'] for q in self.questions}))
        self.assertEqual({e['type'] for e in self.dataset['entities']}, {'person', 'event', 'x_project'})
        orion = next(entity for entity in self.dataset['entities'] if entity['uri'].endswith('x_project-orion'))
        self.assertEqual(orion['props']['x_score'], orion['props']['x_budget'])
        self.assertNotEqual(orion['props']['x_score'], orion['props']['x_priority'])
        self.assertTrue(any(q['iri_anchor'] for q in self.questions))
        self.assertTrue(all(q['answer_correctness_executed'] is False for q in self.questions))
        self.assertEqual(sum(q['track'] == 'schema' for q in self.questions), 6)

    def test_unanchored_attribute_lookup_gold_is_unique_and_has_no_entity_names(self):
        lookups = [query for query in self.questions if query['family'] == 'attribute_lookup']
        self.assertEqual(len(lookups), 6)
        names = {entity['name'] for entity in self.dataset['entities']}
        names.update(alias for entity in self.dataset['entities'] for alias in entity['props'].get('alias_names', []))
        for query in lookups:
            self.assertTrue(all(name not in query['question'] for name in names))
            self.assertTrue(all(target not in query['question'] for target in query['entity_targets']))
            matches = [entity['uri'] for entity in self.dataset['entities']
                       if entity['uri'].startswith(diagnostic.NS) and not entity.get('archived') and not entity.get('disabled')
                       and all(entity['props'].get(atom['key']) == atom['value'] for atom in query['property_atoms'])]
            self.assertEqual(matches, query['entity_targets'])

    def test_gold_covers_allowed_rules_without_inventing_missing_or_reversed_facts(self):
        edges = {(edge['from'], edge['type'], edge['to']) for edge in self.dataset['relations']}
        self.assertIn((diagnostic.uri('x_project-orion'), 'X_DEPENDS_ON', diagnostic.uri('x_project-atlas')), edges)
        self.assertNotIn((diagnostic.uri('x_project-atlas'), 'X_DEPENDS_ON', diagnostic.uri('x_project-orion')), edges)
        self.assertFalse(self.question('direction-05')['structured_gold']['relation_exists'])
        self.assertFalse(self.question('direction-06')['structured_gold']['rule_is_fact'])
        self.assertTrue(any(rule['key'] == 'X_LEADS' for rule in self.dataset['schema']['relations']))

    def test_scope_filters_and_parallel_qualifiers_preserve_exact_duplicate_count(self):
        docs = diagnostic.documents_for(self.dataset, 'entity_relations')
        self.assertEqual(sum(doc.kind == 'entity' for doc in docs), 12)
        self.assertEqual(sum(doc.kind == 'relation' for doc in docs), 8)
        self.assertFalse(any(diagnostic.FOREIGN in ref for doc in docs for ref in doc.source_refs))
        self.assertFalse(any('archived' in ref or 'disabled' in ref for doc in docs for ref in doc.source_refs))
        self.assertEqual(sorted(doc.metadata['duplicate_count'] for doc in docs if doc.kind == 'relation'),
                         [1, 1, 1, 1, 1, 1, 1, 2])
        self.assertTrue(any(doc.metadata.get('self_loop') for doc in docs))

    def test_entity_hit_does_not_imply_visible_property_or_answer_correctness(self):
        question = self.question('numeric-01')
        values = next(doc for doc in diagnostic.documents_for(self.dataset, 'values')
                      if question['entity_targets'][0] in doc.source_refs)
        entity = next(doc for doc in diagnostic.documents_for(self.dataset, 'entity')
                      if question['entity_targets'][0] in doc.source_refs)
        flat = diagnostic.score_question(question, [values])
        typed = diagnostic.score_question(question, [entity])
        self.assertEqual(flat['entity_hit@5'], 1)
        self.assertEqual(flat['visible_property_evidence@5'], 0)
        self.assertEqual(typed['visible_property_evidence@5'], 1)
        self.assertIsNone(typed['answer_correctness'])
        confused = deepcopy(self.question('numeric-02'))
        confused['property_atoms'][0]['value'] = 4  # Score/budget are 4, priority is 1.
        self.assertEqual(diagnostic.score_question(confused, [entity])['visible_property_evidence@5'], 0)

    def test_relation_direction_self_loop_and_qualifier_sufficiency_are_separate(self):
        docs = [doc for doc in diagnostic.documents_for(self.dataset, 'entity_relations') if doc.kind == 'relation']
        dependent = next(doc for doc in docs if doc.metadata['from_uri'].endswith('x_project-orion')
                         and doc.metadata['to_uri'].endswith('x_project-atlas'))
        positive = self.question('direction-03')
        self.assertEqual(diagnostic.score_question(positive, [dependent])['directed_relation_evidence@5'], 1)
        reversed_gold = deepcopy(positive)
        atom = reversed_gold['relation_atoms'][0]
        atom['from'], atom['to'] = atom['to'], atom['from']
        self.assertEqual(diagnostic.score_question(reversed_gold, [dependent])['directed_relation_evidence@5'], 0)
        phases = [doc for doc in docs if doc.metadata['relation_type'] == 'X_LEADS'
                  and doc.metadata['to_uri'].endswith('x_project-orion')]
        self.assertEqual(diagnostic.score_question(self.question('qualifiers-01'), phases[:1])['directed_relation_evidence@5'], 0)
        self.assertEqual(diagnostic.score_question(self.question('qualifiers-01'), phases)['directed_relation_evidence@5'], 1)
        loop = next(doc for doc in docs if doc.metadata['self_loop'])
        self.assertEqual(diagnostic.score_question(self.question('qualifiers-04'), [loop])['directed_relation_evidence@5'], 1)

    def test_unknown_time_marker_and_rules_do_not_turn_into_fact_answers(self):
        question = self.question('temporal-02')
        doc = next(doc for doc in diagnostic.documents_for(self.dataset, 'entity')
                   if question['entity_targets'][0] in doc.source_refs)
        self.assertIn('unknown', doc.text)
        self.assertEqual(diagnostic.score_question(question, [doc])['visible_property_evidence@5'], 1)
        rules = diagnostic.documents_for(self.dataset, 'entity_relations', True)
        rule = next(doc for doc in rules if doc.metadata['definition_key'] == 'X_LEADS')
        self.assertEqual(diagnostic.score_question(self.question('schema-04'), [rule])['schema_definition_hit@5'], 1)
        wrong = diagnostic.score_question(self.question('direction-01'), [rule])
        self.assertEqual(wrong['directed_relation_evidence@5'], 0)
        self.assertEqual(wrong['rule_as_fact'], 1)

    def test_structural_and_negative_tasks_never_receive_top_k_answer_accuracy(self):
        docs = diagnostic.documents_for(self.dataset, 'entity_relations')
        for question in self.questions:
            if question['task'] in ('structured', 'negative'):
                metrics = diagnostic.score_question(question, docs)
                self.assertIsNone(metrics['answer_correctness'])
                self.assertIsNone(metrics['structured_answer_correctness'])
        negative = diagnostic.score_question(self.question('scope-01'), docs)
        self.assertEqual(negative['negative_nonempty_candidates@5'], 1)
        self.assertEqual(negative['forbidden_source_returned'], 0)
        unsafe = replace(docs[0], source_refs=(diagnostic.FOREIGN + 'x_project-private',))
        self.assertEqual(diagnostic.score_question(self.question('scope-01'), [unsafe])['forbidden_source_returned'], 1)

    def test_manifest_precedes_model_calls_and_rejects_changed_gold_without_overwrite(self):
        bundle = diagnostic.prepare(self.output)
        self.assertTrue(bundle['manifest']['frozen_before_model_calls'])
        self.assertEqual({item['key'] for item in bundle['manifest']['model_profile_pins']},
                         {'e5-small', 'qwen3-0.6b'})
        before = {path.name: path.read_bytes() for path in self.output.iterdir()}
        self.assertEqual(diagnostic.prepare(self.output), bundle)
        changed = deepcopy(bundle['questions'])
        changed[0]['question'] = 'post-result tuning is forbidden'
        (self.output / 'questions.json').write_text(json.dumps(changed), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'never edit gold'):
            diagnostic.prepare(self.output)
        self.assertEqual((self.output / 'diagnostic-manifest.json').read_bytes(), before['diagnostic-manifest.json'])
        embedder = FakeEmbedder()
        bundle['questions'] = changed
        with self.assertRaisesRegex(ValueError, 'frozen manifest'):
            diagnostic.evaluate(bundle, embedder, require_real=False)
        self.assertEqual(embedder.prepare_calls, 0)

    def test_fake_vectors_cannot_masquerade_as_real_model_and_smoke_keeps_claims_blocked(self):
        bundle = diagnostic.prepare(self.output)
        with self.assertRaisesRegex(ValueError, 'real CPU embedding'):
            diagnostic.evaluate(bundle, FakeEmbedder())
        result = diagnostic.evaluate(bundle, FakeEmbedder(), require_real=False)
        self.assertFalse(result['real_vectors'])
        self.assertEqual(result['scope'], 'dev_diagnostic_only')
        self.assertEqual(result['promotion'], 'blocked')
        self.assertEqual(set(result['representations']), set(diagnostic.REPRESENTATIONS))
        self.assertEqual(result['manifest']['results_sha256'], diagnostic.digest(result['representations']))
        self.assertEqual(result['run_id'], diagnostic.digest(result['manifest']))
        for report in result['representations'].values():
            self.assertEqual(report['all']['queries'], 54)
            self.assertEqual(report['schema']['queries'], 6)
            self.assertEqual(report['facts']['queries'], 48)
            self.assertEqual(report['all']['denominators']['answer_correctness'], 0)
            self.assertEqual(report['all']['denominators']['structured_answer_correctness'], 0)


if __name__ == '__main__':
    unittest.main()
