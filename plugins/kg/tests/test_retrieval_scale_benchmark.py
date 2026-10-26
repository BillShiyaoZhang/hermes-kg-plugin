"""Safety and metadata fidelity of the disposable scale benchmark harness."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from kg.config import KGConfig
from kg.embedding import TextChunk
from kg.search import HybridSearch

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('benchmark_retrieval_scale',ROOT/'scripts/benchmark_retrieval_scale.py')
benchmark=importlib.util.module_from_spec(spec);spec.loader.exec_module(benchmark)


class ScaleBenchmarkContractTests(unittest.TestCase):
    def setUp(self):self.namespace='urn:researchscale:'+'a'*32+':n1000:'

    def test_cleanup_namespace_rejects_other_data(self):
        self.assertEqual(benchmark.assert_namespace(self.namespace),self.namespace)
        for value in ('urn:hermes:kg:','urn:researchscale:',self.namespace+'extra','urn:researchscale:'+'a'*32+':n500:'):
            with self.assertRaises(ValueError):benchmark.assert_namespace(value)

    def test_fixture_has_exact_counts_isolation_hub_selfloop_and_parallel_edges(self):
        nodes,edges=benchmark.fixtures(1000,self.namespace)
        self.assertEqual((len(nodes),len(edges)),(1000,3000))
        connected={edge[key] for edge in edges for key in ('source','target')}
        self.assertEqual(len({n['uri'] for n in nodes}-connected),10)
        self.assertGreater(sum(edge['target']==nodes[0]['uri'] for edge in edges),1000)
        self.assertTrue(any(edge['source']==edge['target'] for edge in edges))
        self.assertLess(len({(edge['source'],edge['target']) for edge in edges}),len(edges))
        self.assertEqual(len({n['hermes_description'] for n in nodes}),1000)

    def test_bulk_chunk_metadata_exactly_matches_actual_hybridsearch_indexer(self):
        from types import SimpleNamespace
        config=KGConfig(urn_prefix=self.namespace)
        props=benchmark.fixtures(1000,self.namespace)[0][0]
        chunks=[TextChunk('independent fixture',5,0)]
        vectors=[[1.0]+[0.0]*383]
        embedder=SimpleNamespace(model_revision='fixture-model',embedding_revision='fixture-encoding',
            status=lambda:{'ready':True,'cached':True},chunk=lambda text:chunks,embed_passages=lambda texts:vectors)
        search=HybridSearch(SimpleNamespace(config=config),embedder)
        captured=[]
        def run(query,params=None,**kwargs):
            if params and 'chunks' in params:captured.extend(params['chunks']);return [{'written':len(params['chunks'])}]
            return []
        with patch.object(search,'_run',side_effect=run):
            search._index_sources([{'properties':props,'updated':props['hermes_updatedAt'],'indexed':[]}],limit=1)
        actual=benchmark.derived_rows(self.namespace,props,chunks,vectors,'fixture-model','fixture-encoding')
        self.assertEqual(actual,captured)

    def test_percentile_uses_nearest_rank(self):
        stats=benchmark.summary(list(range(1,201)))
        self.assertEqual((stats['samples'],stats['p50_ms'],stats['p95_ms']),(200,100,190))
        self.assertEqual(stats['p99_ms'],198)

    def test_hybrid_mode_does_not_hide_fulltext_failure(self):
        from types import SimpleNamespace
        response=SimpleNamespace(status_code=200,raise_for_status=lambda:None,
            json=lambda:{'mode':'hybrid','freshness':{'semantic':'current'},
                         'warnings':['fulltext_unavailable'],'kg_results':[]})
        client=SimpleNamespace(post=lambda *args,**kwargs:response)
        latency,count,details=benchmark.request_once(client,'synthetic',self.namespace)
        self.assertGreaterEqual(latency,0)
        self.assertEqual(count,0)
        self.assertFalse(details['clean_hybrid'])
        self.assertEqual(details['warnings'],['fulltext_unavailable'])


if __name__=='__main__':unittest.main()
