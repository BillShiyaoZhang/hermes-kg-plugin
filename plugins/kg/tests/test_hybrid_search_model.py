"""Opt-in real ONNX/tokenizer compatibility checks, using a prepared local cache."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kg.embedding import LocalEmbedder


@unittest.skipUnless(os.environ.get("KG_SEARCH_MODEL_CACHE"), "Set KG_SEARCH_MODEL_CACHE to an explicitly prepared E5 cache")
class RealLocalModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = LocalEmbedder(os.environ["KG_SEARCH_MODEL_CACHE"])
        cls.model.prepare(allow_download=False)

    def test_real_tokenizer_bounds_mixed_language_chunks_before_embedding(self):
        text = ("张敏喜欢燕麦奶拿铁。Independent designer working on inspectable memory. " * 100) + "最后一个完整事实 END."
        chunks = self.model.chunk(text)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(text.endswith(chunks[-1].text))
        for chunk in chunks:
            tokens = self.model._tokenizer.encode("passage: " + chunk.text, add_special_tokens=True)
            self.assertEqual(chunk.token_count, len(tokens.ids))
            self.assertLessEqual(chunk.token_count, 512)
        vectors = self.model.embed_passages([chunks[0].text, chunks[-1].text])
        self.assertEqual(len(vectors), 2)
        self.assertTrue(all(len(v) == 384 for v in vectors))
        self.assertTrue(all(abs(sum(x * x for x in v) - 1) < 1e-5 for v in vectors))

    def test_query_cpu_inference_uses_local_cache_without_download(self):
        vector = self.model.embed_query("What does 张敏 work on?")
        self.assertEqual(len(vector), 384)
        self.assertAlmostEqual(sum(x * x for x in vector), 1.0, places=5)


if __name__ == "__main__":
    unittest.main()
