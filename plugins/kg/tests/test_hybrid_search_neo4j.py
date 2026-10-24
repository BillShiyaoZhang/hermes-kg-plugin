"""Opt-in real Cypher contract checks for the Docker development database."""
import os
import logging
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kg.config import KGConfig
from kg.embedding import TextChunk, LocalEmbedder
from kg.lifecycle import MemoryService
from kg.search import HybridSearch
from kg.store import KGStore

logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)


class DeterministicEmbedder:
    """Exercises ANN/index API mechanics, not model retrieval quality."""
    model_revision = "database-contract-fixture"
    embedding_revision = "mechanics-only-v1"
    def chunk(self, text):
        return [TextChunk(text, 20, 0)]
    def embed_passages(self, texts):
        return [[1.0] + [0.0] * 383 for _ in texts]
    def embed_query(self, text):
        return self.embed_passages([text])[0]
    def status(self):
        return {"ready": True, "synthetic": True}


@unittest.skipUnless(os.environ.get("KG_SEARCH_TEST_URL"), "Set KG_SEARCH_TEST_URL for disposable Docker Neo4j")
class Neo4jHybridContractTests(unittest.TestCase):
    def setUp(self):
        self.prefix = "urn:kgtest" + uuid.uuid4().hex + ":kg:"
        self.config = KGConfig(neo4j_url=os.environ["KG_SEARCH_TEST_URL"],
                               password=os.environ["KG_SEARCH_TEST_PASSWORD"], urn_prefix=self.prefix,
                               user_person_urn=self.prefix + "person-user")
        self.store = KGStore(self.config)
        self.search = HybridSearch(self.store, DeterministicEmbedder())
        self.store.ensure_schema()
        self.search.ensure_indexes()
        self.store.write_batch([
            {"id": "alice", "type": "Person", "name": "张敏", "props": {"alias_names": ["Mindy"], "description": "Designer"}},
            {"id": "meeting", "type": "Event", "name": "Product demo", "props": {"event_time": "2026-10-21T14:00:00+08:00", "description": "memory evidence"}},
        ], [{"from": self.prefix + "person-alice", "to": self.prefix + "event-meeting", "type": "HERMES_participant"}])
        self.search.index_pending()
        with self.store.driver.session(database=self.config.database) as session:
            session.run("CALL db.awaitIndexes(120)").consume()

    def tearDown(self):
        with self.store.driver.session(database=self.config.database) as session:
            session.run("MATCH (n) WHERE n.uri STARTS WITH $prefix OR n.namespace=$prefix DETACH DELETE n", {"prefix": self.prefix}).consume()
            session.run("MATCH (c:KGSearchChunk {namespace:$prefix}) DELETE c", {"prefix": self.prefix}).consume()
        self.store.close()

    def test_real_alias_fulltext_vector_hop_and_date_filters(self):
        result = self.search.search("Who is Mindy?", filters={"namespace": self.prefix})
        self.assertEqual(result["mode"], "hybrid")
        self.assertEqual(result["kg_results"][0]["uri"], self.prefix + "person-alice")
        self.assertIn(self.prefix + "event-meeting", [r["uri"] for r in result["kg_results"]])
        dated = self.search.search("memory evidence", filters={"type": "Event", "from": "2026-10-21", "to": "2026-10-22", "namespace": self.prefix})
        self.assertEqual([r["uri"] for r in dated["kg_results"]], [self.prefix + "event-meeting"])

    def test_type_filter_keeps_exact_person_context_for_graph_events_with_a_cold_model(self):
        result = HybridSearch(self.store).search('Mindy', filters={'namespace': self.prefix, 'type': 'Event'})
        self.assertEqual([row['uri'] for row in result['kg_results']], [self.prefix + 'event-meeting'])
        self.assertEqual(result['kg_results'][0]['channels'], ['graph'])
        self.assertEqual(result['mode'], 'keyword')

    def test_archive_after_index_cannot_leak_via_any_channel(self):
        uri = self.prefix + "person-alice"
        with self.store.driver.session(database=self.config.database) as session:
            session.run("MATCH (n {uri:$uri}) SET n:KGQuarantined,n.hermes_revision=1", {"uri": uri}).consume()
        for question in ("Mindy", uri, "Designer", "memory evidence"):
            self.assertNotIn(uri, [r["uri"] for r in self.search.search(question, filters={"namespace": self.prefix})["kg_results"]])

    def test_uri_seek_retains_legacy_typed_entity_without_kgentity_label(self):
        uri = self.prefix + "person-legacy"
        with self.store.driver.session(database=self.config.database) as session:
            session.run("CREATE (:Person {uri:$uri,foaf_name:'Legacy shared person',shared:false})", {"uri": uri}).consume()
        self.search.index_pending()
        result = self.search.search(uri, filters={"namespace": self.prefix})
        self.assertEqual(result["kg_results"][0]["uri"], uri)
        self.assertIn("vector", result["kg_results"][0]["channels"])

    def test_shared_profiles_cannot_starve_owned_indexing_or_filtered_candidates(self):
        foreign = 'urn:aa' + uuid.uuid4().hex + ':kg:'
        try:
            with self.store.driver.session(database=self.config.database) as session:
                session.run("""UNWIND range(0,129) AS ordinal
                    CREATE (n:KGEntity:Person {uri:$foreign+'person-'+toString(ordinal),
                        foaf_name:'memory evidence',hermes_description:'memory evidence',shared:true})
                    CREATE (:KGSearchChunk {id:$foreign+toString(ordinal),namespace:$foreign,
                        source_uri:n.uri,source_revision:0,source_updated_at:'',
                        model_revision:'another-profile-model',embedding_revision:'other-revision',
                        embedding_state:'ready',text:'memory evidence',embedding:$vector})""",
                    {'foreign': foreign, 'vector': [1.0] + [0.0] * 383}).consume()
                session.run('MATCH (c:KGSearchChunk {namespace:$prefix}) DELETE c',
                            {'prefix': self.prefix}).consume()
            # Even a one-entity batch must start with owned memories; a large
            # shared profile cannot consume the entire indexing allowance.
            self.assertEqual(self.search.index_pending(limit=1)['indexed'], 1)
            with self.store.driver.session(database=self.config.database) as session:
                row = session.run("""MATCH (c:KGSearchChunk {namespace:$prefix})
                    RETURN count(c) AS total,
                        sum(CASE WHEN c.source_uri STARTS WITH $prefix THEN 1 ELSE 0 END) AS owned""",
                    {'prefix': self.prefix}).single()
                self.assertEqual((row['total'], row['owned']), (1, 1))
            self.search.index_pending(limit=1000)
            result = self.search.search('memory evidence', filters={
                'namespace': self.prefix, 'type': 'Event', 'from': '2026-10-21', 'to': '2026-10-22'})
            self.assertEqual([row['uri'] for row in result['kg_results']], [self.prefix + 'event-meeting'])
            self.assertIn('keyword', result['kg_results'][0]['channels'])
            self.assertIn('vector', result['kg_results'][0]['channels'])
            # Omitting a namespace still applies the requested type before the
            # many shared Person hits can fill the candidate shortlist.
            typed = self.search.search('memory evidence', filters={'type': 'Event'})
            self.assertIn(self.prefix + 'event-meeting', [row['uri'] for row in typed['kg_results']])
        finally:
            with self.store.driver.session(database=self.config.database) as session:
                session.run('MATCH (n) WHERE n.uri STARTS WITH $foreign OR n.namespace=$foreign DETACH DELETE n',
                            {'foreign': foreign}).consume()

    def test_revision_update_prevents_old_chunk_and_allows_new_lexical_content(self):
        uri = self.prefix + "person-alice"
        with self.store.driver.session(database=self.config.database) as session:
            session.run("MATCH (n {uri:$uri}) SET n.hermes_revision=1,n.hermes_description='Astronaut',n.hermes_updatedAt=datetime()", {"uri": uri}).consume()
        result = self.search.search("Designer", filters={"namespace": self.prefix})
        # A one-hop current-source match is legal, but obsolete chunk content is never returned.
        self.assertTrue(all("Designer" not in str(r) for r in result["kg_results"]))
        self.assertGreater(self.search.status()["pending_count"], 0)
        self.search.index_pending()
        self.assertEqual(self.search.status()["pending_count"], 0)

    def test_unindexed_correction_and_approved_new_fact_survive_nonempty_fulltext(self):
        marker = 'orbitalhandoffmarker'
        self.store.write_batch([{'id':'control', 'type':'Concept', 'name':'Indexed control',
                                 'props':{'description':marker}}], [])
        self.search.index_uris([self.prefix+'concept-control'])
        memory = MemoryService(self.config, self.store)
        uri = self.prefix+'person-alice'
        memory.correct(uri, {'props':{'description':marker+' corrected astronaut'}},
                       expected_version=1, operation_id=uuid.uuid4().hex)
        # The control still hits fulltext, while Alice has no current chunk.
        cold = HybridSearch(self.store)
        result = cold.search(marker, filters={'namespace':self.prefix})
        by_uri = {row['uri']:row for row in result['kg_results']}
        self.assertIn(uri, by_uri)
        self.assertIn(self.prefix+'concept-control', by_uri)
        self.assertEqual(by_uri[uri]['version'], 2)
        self.assertIn('corrected astronaut', by_uri[uri]['description'])
        candidate = [{'id':'alice', 'type':'Person', 'name':'Automatic conflict',
                      'props':{'description':marker}},
                     {'id':'approved', 'type':'Concept', 'name':'New approved fact',
                      'props':{'description':marker+' approved addition'}}]
        pending = self.store.write_batch(candidate, [], origin='auto',
                                         review_payload={'entities':candidate,'relations':[]})
        self.assertEqual(pending['disposition'], 'deferred')
        # This database/search fixture has no Hermes transcript; source proof
        # is independently covered by the extraction grounding integration.
        memory.review_validator = lambda payload: None
        approved = memory.resolve_review(pending['review_batch_id'], 'approve',
                                        operation_id=uuid.uuid4().hex)
        self.assertEqual(approved['disposition'], 'applied')
        result = cold.search(marker, filters={'namespace':self.prefix})
        self.assertIn(self.prefix+'concept-approved', [row['uri'] for row in result['kg_results']])
        self.assertEqual(result['freshness']['semantic'], 'degraded')

    def test_targeted_refresh_replaces_current_vectors_and_removes_archive_chunks(self):
        memory = MemoryService(self.config, self.store)
        uri = self.prefix+'person-alice'
        memory.correct(uri, {'props':{'description':'Revised current profession'}},
                       expected_version=1, operation_id=uuid.uuid4().hex)
        report = self.search.index_uris([uri])
        self.assertEqual(report['semantic'], 'current')
        with self.store.driver.session(database=self.config.database) as session:
            rows = session.run('MATCH (c:KGSearchChunk {namespace:$prefix,source_uri:$uri}) '
                               'RETURN c.source_revision AS revision,c.text AS text,c.embedding_state AS state',
                               {'prefix':self.prefix,'uri':uri}).data()
        self.assertTrue(rows)
        self.assertTrue(all(row['revision'] == 2 and row['state'] == 'ready'
                            and 'Revised current profession' in row['text'] for row in rows))
        memory.archive(uri, expected_version=2, operation_id=uuid.uuid4().hex)
        self.search.index_uris([uri])
        with self.store.driver.session(database=self.config.database) as session:
            count = session.run('MATCH (c:KGSearchChunk {namespace:$prefix,source_uri:$uri}) RETURN count(c) AS total',
                                {'prefix':self.prefix,'uri':uri}).single()['total']
        self.assertEqual(count, 0)
        self.assertNotIn(uri, [row['uri'] for row in self.search.search('Revised current profession')['kg_results']])

    @unittest.skipUnless(os.environ.get('KG_SEARCH_MODEL_CACHE'), 'Set KG_SEARCH_MODEL_CACHE for real E5 + database freshness')
    def test_ready_real_e5_refresh_recalls_paraphrase_at_next_search(self):
        model = LocalEmbedder(os.environ['KG_SEARCH_MODEL_CACHE'])
        model.prepare(allow_download=False)
        actual = HybridSearch(self.store, model)
        self.store.write_batch([
            # Preference preparation links the configured owner implicitly;
            # this isolated namespace must supply that real endpoint too.
            {'id':self.config.user_person_urn,'type':'Person','name':'Synthetic preference owner'},
            {'id':'ritual','type':'Preference','name':'Morning ritual',
             'props':{'preference_key':'coffee','preference_value':'Black espresso'}},
            {'id':'control','type':'Concept','name':'Breakfast beverage note',
             'props':{'description':'A dictionary entry for breakfast beverage vocabulary'}}], [])
        uri = self.prefix+'preference-ritual'
        actual.index_uris([uri, self.prefix+'concept-control'])
        memory = MemoryService(self.config, self.store)
        memory.correct(uri, {'props':{'preference_value':'Oat milk cappuccino, no dairy'}},
                       expected_version=1, operation_id=uuid.uuid4().hex)
        report = actual.index_uris([uri])
        self.assertEqual(report['semantic'], 'current')
        # No manual/background reindex between this completed write hook and
        # the next paraphrased search; an independent fulltext hit also exists.
        result = actual.search('What is my preferred breakfast beverage?', filters={'namespace':self.prefix})
        target = next((row for row in result['kg_results'] if row['uri'] == uri), None)
        self.assertIsNotNone(target)
        self.assertEqual(target['preference_value'], 'Oat milk cappuccino, no dairy')
        self.assertIn('vector', target['channels'])

    def test_maintenance_removes_orphaned_chunks(self):
        uri = self.prefix + "event-meeting"
        self.store.delete_entity(uri)
        self.search.index_pending()
        with self.store.driver.session(database=self.config.database) as session:
            remaining = session.run("MATCH (c:KGSearchChunk {namespace:$prefix,source_uri:$uri}) RETURN count(c) AS count",
                                    {"prefix": self.prefix, "uri": uri}).single()["count"]
        self.assertEqual(remaining, 0)


if __name__ == "__main__":
    unittest.main()
