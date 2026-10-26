"""Selected current sources, never cached bodies, become experimental evidence."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kg.retrieval_authority import RequestContext, RetrievalAuthority
from kg.retrieval_documents import _schema_fingerprint, dataset_to_documents

NS = 'urn:authority:test:'


def schema():
    return {'namespace': NS, 'version': 2, 'entity_types': [
        {'key': 'person', 'label': '人物', 'storage_label': 'Person'}],
        'properties': [], 'relations': [{'key': 'RELATED_TO', 'label': '关联'}]}


def node(uri, name='synthetic', **props):
    return {'labels': ['KGEntity', 'Person'], 'properties': {
        'uri': uri, 'foaf_name': name, 'hermes_revision': 1, **props}}


def snapshot():
    return {'entities': [node(NS + 'a', 'A'), node(NS + 'b', 'B')],
            'relations': [{'from': NS + 'a', 'to': NS + 'b', 'type': 'RELATED_TO',
                           'properties': {'hermes_note': 'first'}}]}


def context(**changes):
    return replace(RequestContext(NS, 'profile', 2, _schema_fingerprint(schema()),
        'model-fingerprint', 'entity_relations', 'retrieval-cards-v1', 'generation-1'), **changes)


def cards(data=None, definitions=None):
    return dataset_to_documents({'namespace': NS, **(data or snapshot())},
        'entity_relations', schema=definitions or schema(), include_ontology=True)


class MemoryAuthority(RetrievalAuthority):
    def __init__(self, data=None, **kwargs):
        self.data = deepcopy(data or snapshot())
        self.schema = schema()
        self.context = context()
        self.reads = 0
        self.after_read = lambda: None
        super().__init__(SimpleNamespace(config=SimpleNamespace(urn_prefix=NS)),
            current_context=lambda request: self.context,
            schema_reader=lambda: deepcopy(self.schema), **kwargs)

    def _read_sources(self, refs, pairs, schema, context):
        self.reads += 1
        result = deepcopy(self.data)
        self.after_read()
        return result


class RetrievalAuthorityTests(unittest.TestCase):
    def test_context_is_immutable_and_explicit(self):
        with self.assertRaises(FrozenInstanceError):
            context().scope = 'shared'
        for changes in ({'scope': 'all'}, {'schema_version': True}, {'index_generation': ''},
                        {'representation': 'unknown'}, {'schema_version': -1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                context(**changes)

    def test_rebuilds_current_content_instead_of_returning_cached_text_or_metadata(self):
        original = cards()
        poisoned = [replace(doc, text='CACHED PRIVATE BODY', metadata={**doc.metadata, 'token': 'secret'})
                    for doc in original]
        result = MemoryAuthority().validate(poisoned, context())
        self.assertEqual(result.status, 'current')
        self.assertEqual(result.documents, tuple(original))
        self.assertFalse(result.incomplete)
        self.assertFalse(any('PRIVATE' in doc.text or 'token' in doc.metadata for doc in result.documents))

    def test_body_change_without_revision_is_withheld(self):
        authority = MemoryAuthority()
        authority.data['entities'][0]['properties']['hermes_note'] = 'new content'
        old = next(doc for doc in cards() if doc.kind == 'entity' and doc.source_refs == (NS + 'a',))
        self.assertEqual(authority.validate([old], context()).documents, ())

    def test_revision_change_without_body_is_withheld(self):
        authority = MemoryAuthority()
        authority.data['entities'][0]['properties']['hermes_revision'] = 2
        old = [doc for doc in cards() if doc.kind == 'entity' and doc.source_refs == (NS + 'a',)]
        self.assertEqual(authority.validate(old, context()).status, 'incomplete')

    def test_rename_endpoint_without_revision_changes_relation_identity(self):
        authority = MemoryAuthority()
        authority.data['entities'][1]['properties']['foaf_name'] = 'new name'
        old = [doc for doc in cards() if doc.kind == 'relation']
        self.assertEqual(authority.validate(old, context()).documents, ())

    def test_relation_delete_and_qualifier_change_are_withheld(self):
        old = [doc for doc in cards() if doc.kind == 'relation']
        for changed in ([], [{'from': NS + 'a', 'to': NS + 'b', 'type': 'RELATED_TO',
                             'properties': {'hermes_note': 'changed'}}]):
            authority = MemoryAuthority()
            authority.data['relations'] = changed
            self.assertEqual(authority.validate(old, context()).documents, ())

    def test_distinct_parallel_variants_reverse_and_self_loop_are_preserved(self):
        data = snapshot()
        for a, b, text in (('a', 'b', 'second'), ('b', 'a', 'reverse'), ('a', 'a', 'self')):
            data['relations'].append({'from': NS + a, 'to': NS + b,
                'type': 'RELATED_TO', 'properties': {'hermes_note': text}})
        old = [doc for doc in cards(data) if doc.kind == 'relation']
        self.assertEqual(len(old), 4)
        self.assertEqual(len(MemoryAuthority(data).validate(old, context()).documents), 4)
        data['relations'].pop(0)
        result = MemoryAuthority(data).validate(old, context())
        self.assertEqual(len(result.documents), 3)
        self.assertEqual(len(result.rejected), 1)

    def test_physical_duplicate_dependency_changes_are_withheld(self):
        data = snapshot()
        old = [doc for doc in cards(data) if doc.kind == 'relation']
        data['relations'].append(deepcopy(data['relations'][0]))
        self.assertEqual(MemoryAuthority(data).validate(old, context()).documents, ())
        current = [doc for doc in cards(data) if doc.kind == 'relation']
        result = MemoryAuthority(data).validate(current, context())
        self.assertEqual(result.documents[0].metadata['duplicate_count'], 2)

    def test_archive_disable_quarantine_and_unresolved_are_unavailable(self):
        for key, value in (('hermes_archivedByWorkbench', True), ('hermes_enabled', False),
                           ('hermes_status', 'disabled'), ('label', 'KGQuarantined'),
                           ('label', 'KGLegacyAlias'), ('label', 'KGLegacyUnresolved')):
            authority = MemoryAuthority()
            if key == 'label':
                authority.data['entities'][0]['labels'].append(value)
            else:
                authority.data['entities'][0]['properties'][key] = value
            old = [doc for doc in cards() if NS + 'a' in doc.source_refs]
            with self.subTest(key=key, value=value):
                self.assertEqual(authority.validate(old, context()).documents, ())

    def test_foreign_shared_entity_is_only_allowed_in_explicit_shared_scope(self):
        data = {'entities': [node('urn:foreign:p', shared=True)], 'relations': []}
        old = [doc for doc in cards(data) if doc.kind == 'entity']
        authority = MemoryAuthority(data)
        self.assertEqual(authority.validate(old, context()).documents, ())
        authority.context = context(scope='shared')
        self.assertEqual(len(authority.validate(old, authority.context).documents), 1)
        authority.data['entities'][0]['properties']['shared'] = False
        self.assertEqual(authority.validate(old, authority.context).documents, ())

    def test_schema_change_and_generation_release_discard_entire_batch(self):
        for change in ({'schema_version': 3}, {'index_generation': 'generation-2'},
                       {'model_fingerprint': 'new-model'}, {'projection_revision': 'new-projection'}):
            authority = MemoryAuthority()
            authority.context = context(**change)
            result = authority.validate(cards(), context())
            self.assertEqual(result.documents, ())
            self.assertTrue(all(row['reason'] == 'context_changed' for row in result.rejected))

    def test_schema_content_change_without_version_is_detected(self):
        authority = MemoryAuthority()
        authority.schema['entity_types'][0]['label'] = 'new definition'
        self.assertEqual(authority.validate(cards(), context()).documents, ())

    def test_change_between_source_reads_is_withheld(self):
        authority = MemoryAuthority()
        def mutate():
            authority.data['entities'][0]['properties']['hermes_revision'] += 1
        authority.after_read = mutate
        result = authority.validate(cards(), context())
        self.assertEqual(result.documents, ())
        self.assertTrue(all(row['reason'] == 'sources_changed' for row in result.rejected))

    def test_release_during_read_is_withheld(self):
        authority = MemoryAuthority()
        authority.after_read = lambda: setattr(authority, 'context', context(index_generation='next'))
        self.assertEqual(authority.validate(cards(), context()).documents, ())

    def test_schema_publication_during_read_is_withheld(self):
        authority = MemoryAuthority()
        authority.after_read = lambda: authority.schema.update(version=3)
        self.assertEqual(authority.validate(cards(), context()).documents, ())

    def test_database_failure_is_not_no_answer_and_leaks_no_exception(self):
        authority = MemoryAuthority()
        def fail(*args):
            raise RuntimeError('secret-private-uri-password')
        authority._read_sources = fail
        result = authority.validate(cards(), context())
        self.assertEqual(result.status, 'unavailable')
        self.assertTrue(result.incomplete)
        self.assertNotIn('secret', str(result))

    def test_budgets_invalid_candidate_and_namespace_fail_closed(self):
        self.assertEqual(MemoryAuthority(max_candidates=1).validate(cards(), context()).documents, ())
        self.assertEqual(MemoryAuthority(max_refs=1).validate(cards(), context()).documents, ())
        authority = MemoryAuthority()
        self.assertEqual(authority.validate([None], context()).documents, ())
        self.assertEqual(authority.validate(cards(), context(namespace='urn:other:')).documents, ())
        bad = replace(cards()[0], source_refs=('x',) * 3, metadata=None)
        self.assertEqual(authority.validate([bad], context()).documents, ())

    def test_duplicate_candidates_return_one_rebuilt_card(self):
        doc = cards()[0]
        self.assertEqual(len(MemoryAuthority().validate([doc, doc], context()).documents), 1)


if __name__ == '__main__':
    unittest.main()
