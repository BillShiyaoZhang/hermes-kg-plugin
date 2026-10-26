"""Adapter contract tests use explicit fakes; they do not measure model quality."""
import contextlib
import importlib
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from kg.embedding import EmbeddingUnavailable
from kg.embedding_profiles import ModelProfile, available_profiles, create_embedder, main


class CharacterTokenizer:
    """Deterministic fake, deliberately unrelated to any real model tokenizer."""
    is_fast = True

    def __call__(self, text, *, add_special_tokens=True, return_offsets_mapping=False, **kwargs):
        result = {"input_ids": [1] * (len(text) + (2 if add_special_tokens else 0))}
        if return_offsets_mapping:
            result["offset_mapping"] = [(i, i + 1) for i in range(len(text))]
        return result


class FakeSentenceTransformer:
    dimensions = 768
    instances = []
    result = None

    def __init__(self, path, **kwargs):
        self.path, self.kwargs, self.calls = path, kwargs, []
        self.tokenizer = CharacterTokenizer()
        self.__class__.instances.append(self)

    def get_sentence_embedding_dimension(self):
        return self.dimensions

    def eval(self):
        self.evaluated = True

    def encode(self, texts, **kwargs):
        self.calls.append((texts, kwargs))
        if self.result is not None:
            return self.result
        return [[3.0, 4.0] + [0.0] * (self.dimensions - 2) for _ in texts]


def fake_torch(*, cuda=False):
    return types.SimpleNamespace(float32="fake-fp32", float16="fake-fp16", bfloat16="fake-bf16",
                                 set_num_threads=lambda count: None,
                                 inference_mode=contextlib.nullcontext,
                                 cuda=types.SimpleNamespace(is_available=lambda: cuda,
                                                           is_bf16_supported=lambda: cuda))


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name).resolve()
        FakeSentenceTransformer.instances = []
        FakeSentenceTransformer.result = None
        FakeSentenceTransformer.dimensions = 768

    def embedder(self, key="e5-base", **kwargs):
        return create_embedder(key, self.cache, **kwargs)

    def populate(self, embedder):
        for name in embedder._required_names():
            path = embedder.model_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic-test-artifact")
        modules = [
            {"idx": 0, "name": "0", "path": "", "type": "sentence_transformers.models.Transformer"},
            {"idx": 1, "name": "1", "path": "1_Pooling", "type": "sentence_transformers.models.Pooling"},
            {"idx": 2, "name": "2", "path": "2_Normalize", "type": "sentence_transformers.models.Normalize"},
        ]
        if embedder.profile.gated:
            modules[2:] = [
                {"idx": 2, "name": "2", "path": "2_Dense", "type": "sentence_transformers.models.Dense"},
                {"idx": 3, "name": "3", "path": "3_Dense", "type": "sentence_transformers.models.Dense"},
            ]
        (embedder.model_dir / "modules.json").write_text(json.dumps(modules), encoding="utf-8")
        mode = {"e5-base": "mean_tokens", "qwen3-0.6b": "lasttoken", "bge-m3": "cls_token"}.get(
            embedder.profile.key, "mean_tokens")
        (embedder.model_dir / "1_Pooling/config.json").write_text(json.dumps({"pooling_mode_" + mode: True}))

    @contextlib.contextmanager
    def backend(self, *, cuda=False):
        with patch.dict(sys.modules, {"torch": fake_torch(cuda=cuda),
                                      "sentence_transformers": types.SimpleNamespace(
                                          SentenceTransformer=FakeSentenceTransformer)}):
            yield

    def ready(self, key="e5-base", **kwargs):
        embedder = self.embedder(key, **kwargs)
        self.populate(embedder)
        FakeSentenceTransformer.dimensions = embedder.dimensions
        with self.backend():
            embedder.prepare()
        return embedder

    def test_all_public_model_revisions_are_immutable(self):
        profiles = available_profiles()
        self.assertEqual(len(profiles), 5)
        for profile in profiles:
            self.assertRegex(profile["model_revision"], r"^[a-f0-9]{40}$")
        self.assertTrue(profiles[-1]["gated"])
        profiles[0]["model_revision"] = "main"
        self.assertNotEqual(available_profiles()[0]["model_revision"], "main")

    def test_mutable_revision_rejected(self):
        with self.assertRaisesRegex(ValueError, "immutable"):
            ModelProfile("invalid", "test/model", "main", 2, 512, "mean", "{text}", "{text}")

    def test_stdlib_import_status_does_not_import_model_packages(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        result = subprocess.run([sys.executable, "-S", "-c",
            "import sys; from kg.embedding_profiles import create_embedder; "
            "e=create_embedder('e5-base','unused'); e.status(); e.model_manifest; "
            "assert not {'torch','sentence_transformers','fastembed','huggingface_hub'} & set(sys.modules)"],
            env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_constructor_does_not_create_cache(self):
        path = self.cache / "absent"
        create_embedder("e5-base", path).status()
        self.assertFalse(path.exists())

    def test_default_cpu_threads_are_eight(self):
        self.assertEqual(self.embedder().threads, 8)

    def test_invalid_parameters_fail_early(self):
        for kwargs in ({"threads": True}, {"threads": 0}, {"batch_size": -1},
                       {"max_tokens": 513}, {"max_tokens": 0}, {"device": "mps"},
                       {"device": "cuda:0;unsafe"}, {"precision": "int8"},
                       {"precision": "float16"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.embedder(**kwargs)

    def test_no_silent_precision_or_device_fallback(self):
        with self.assertRaises(ValueError):
            self.embedder("e5-small", device="cuda:0")
        with self.assertRaises(ValueError):
            self.embedder("embeddinggemma-300m", device="cuda:0", precision="float16")
        embedder = self.embedder(device="cuda:0")
        self.populate(embedder)
        with self.backend(), self.assertRaisesRegex(EmbeddingUnavailable, "requested_cuda_unavailable"):
            embedder.prepare()

    def test_offline_missing_model_never_downloads(self):
        download = unittest.mock.Mock(side_effect=AssertionError("network forbidden"))
        with patch.dict(sys.modules, {"huggingface_hub": types.SimpleNamespace(snapshot_download=download)}):
            with self.assertRaisesRegex(EmbeddingUnavailable, "model_not_cached"):
                self.embedder().prepare()
        download.assert_not_called()
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_explicit_download_is_pinned_and_selected_files_only(self):
        embedder = self.embedder()
        calls = []
        def download(**kwargs):
            calls.append(kwargs)
            self.populate(embedder)
        with patch.dict(sys.modules, {"huggingface_hub": types.SimpleNamespace(snapshot_download=download)}), self.backend():
            embedder.prepare(allow_download=True)
        self.assertEqual(calls[0]["revision"], embedder.model_revision)
        self.assertEqual(calls[0]["repo_id"], embedder.model_name)
        self.assertIn("model.safetensors", calls[0]["allow_patterns"])
        self.assertNotIn("onnx/*", calls[0]["allow_patterns"])
        self.assertTrue(embedder.status()["ready"])

    def test_loader_is_local_only_and_disables_remote_code(self):
        embedder = self.ready()
        kwargs = embedder._model.kwargs
        self.assertTrue(kwargs["local_files_only"])
        self.assertFalse(kwargs["trust_remote_code"])
        self.assertEqual(kwargs["model_kwargs"], {"torch_dtype": "fake-fp32", "attn_implementation": "eager"})
        self.assertTrue(embedder._model.evaluated)

    def test_remote_module_graph_rejected_without_importing_it(self):
        embedder = self.embedder()
        self.populate(embedder)
        (embedder.model_dir / "modules.json").write_text('[{"path":"","type":"arbitrary.module.Code"}]')
        with self.backend(), self.assertRaisesRegex(EmbeddingUnavailable, "unsupported_local_module_graph"):
            embedder.prepare()
        self.assertEqual(FakeSentenceTransformer.instances, [])

    def test_pooling_mismatch_rejected(self):
        embedder = self.embedder("qwen3-0.6b")
        self.populate(embedder)
        (embedder.model_dir / "1_Pooling/config.json").write_text('{"pooling_mode_mean_tokens":true}')
        with self.backend(), self.assertRaisesRegex(EmbeddingUnavailable, "model_pooling_mismatch"):
            embedder.prepare()

    def test_gemma_projection_modules_cannot_be_silently_dropped(self):
        embedder = self.embedder("embeddinggemma-300m")
        self.populate(embedder)
        path = embedder.model_dir / "modules.json"
        path.write_text(json.dumps(json.loads(path.read_text())[:2]))
        with self.backend(), self.assertRaisesRegex(EmbeddingUnavailable, "model_projection_modules_missing"):
            embedder.prepare()

    def test_loader_errors_are_sanitized(self):
        embedder = self.embedder()
        self.populate(embedder)
        with patch.object(embedder, "_load_sentence_transformer", side_effect=RuntimeError("secret-token-private-url")):
            with self.assertRaisesRegex(EmbeddingUnavailable, "^embedding_initialization_failed$"):
                embedder.prepare()
        self.assertNotIn("secret", json.dumps(embedder.status()))

    def test_manifest_records_hashes_and_prepared_fingerprint(self):
        embedder = self.embedder()
        before = embedder.fingerprint
        self.populate(embedder)
        with self.backend():
            embedder.prepare()
        manifest = embedder.model_manifest
        self.assertNotEqual(before, embedder.fingerprint)
        self.assertEqual(manifest["dimensions"], 768)
        self.assertEqual(manifest["precision"], "float32")
        self.assertEqual(manifest["query_format"], "query: {text}")
        self.assertRegex(manifest["files_sha256"]["model.safetensors"], r"^[a-f0-9]{64}$")
        files = list((self.cache / "research-manifests").glob("*.json"))
        self.assertEqual(len(files), 2)
        self.assertEqual(json.loads(next(path for path in files if "artifacts" not in path.name).read_text())["files_sha256"], manifest["files_sha256"])
        manifest["files_sha256"]["model.safetensors"] = "altered"
        self.assertNotEqual(embedder.model_manifest["files_sha256"]["model.safetensors"], "altered")

    def test_modified_sealed_artifact_rejected(self):
        embedder = self.ready()
        (embedder.model_dir / "model.safetensors").write_bytes(b"changed")
        with self.assertRaisesRegex(EmbeddingUnavailable, "model_cache_integrity_changed"):
            embedder.prepare()
        self.assertFalse(embedder.status()["ready"])

    def test_same_artifacts_and_settings_reproduce_fingerprint(self):
        first = self.ready()
        second = self.embedder()
        with self.backend():
            second.prepare()
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertNotEqual(first.fingerprint, self.embedder(batch_size=2).fingerprint)

    def test_runtime_dimension_mismatch_is_not_ready(self):
        embedder = self.embedder()
        self.populate(embedder)
        FakeSentenceTransformer.dimensions = 42
        with self.backend(), self.assertRaisesRegex(EmbeddingUnavailable, "model_dimension_mismatch"):
            embedder.prepare()
        self.assertFalse(embedder.status()["ready"])

    def test_e5_query_and_passage_prefix_exactly_once(self):
        embedder = self.ready()
        query = embedder.embed_query("问题")
        passages = embedder.embed_passages(["内容"])
        self.assertEqual(embedder._model.calls[0][0], ["query: 问题"])
        self.assertEqual(embedder._model.calls[1][0], ["passage: 内容"])
        self.assertEqual(embedder._model.calls[0][1]["prompt"], "")
        self.assertEqual(len(query), 768)
        self.assertAlmostEqual(sum(value * value for value in query), 1.0)
        self.assertEqual(passages[0][:2], [0.6, 0.8])

    def test_qwen_official_instruction_and_left_padding(self):
        embedder = self.ready("qwen3-0.6b")
        embedder.embed_query("中文查询")
        embedder.embed_passages(["文档"])
        self.assertEqual(embedder._model.calls[0][0], [
            "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:中文查询"])
        self.assertEqual(embedder._model.calls[1][0], ["文档"])
        self.assertEqual(embedder._model.kwargs["tokenizer_kwargs"], {"padding_side": "left"})

    def test_bge_dense_uses_no_instruction(self):
        embedder = self.ready("bge-m3")
        embedder.embed_query("查询")
        embedder.embed_passages(["文档"])
        self.assertEqual([call[0] for call in embedder._model.calls], [["查询"], ["文档"]])
        self.assertIn("pytorch_model.bin", embedder._required_names())

    def test_gemma_default_retrieval_prompts_and_dense_files(self):
        embedder = self.ready("embeddinggemma-300m")
        embedder.embed_query("query")
        embedder.embed_passages(["passage"])
        self.assertEqual([call[0] for call in embedder._model.calls], [
            ["task: search result | query: query"], ["title: none | text: passage"]])
        self.assertIn("2_Dense/model.safetensors", embedder._required_names())
        self.assertIn("3_Dense/model.safetensors", embedder._required_names())

    def test_chunk_counts_full_formatted_tokens_and_preserves_body(self):
        embedder = self.ready(max_tokens=20)
        chunks = embedder.chunk("abcdefghijklmnopqrstuv", overlap_tokens=2)
        self.assertGreater(len(chunks), 1)
        for index, chunk in enumerate(chunks):
            self.assertEqual(chunk.index, index)
            self.assertLessEqual(chunk.token_count, 20)
            self.assertEqual(chunk.token_count, len("passage: " + chunk.text) + 2)
        self.assertEqual(chunks[0].text, "abcdefghi")
        self.assertEqual(chunks[1].text[:2], "hi")
        self.assertTrue(chunks[-1].text.endswith("v"))

    def test_long_query_explicitly_uses_first_chunk(self):
        embedder = self.ready(max_tokens=20)
        embedder.embed_query("abcdefghijklmnopqrstuvwxyz")
        self.assertEqual(embedder._model.calls[0][0], ["query: abcdefghijk"])
        self.assertEqual(embedder.model_manifest["query_overflow"], "first_chunk")

    def test_passage_overflow_rejected_before_backend_truncation(self):
        embedder = self.ready(max_tokens=20)
        with self.assertRaisesRegex(ValueError, "chunk it first"):
            embedder.embed_passages(["abcdefghijklmnopqrstuvwxyz"])
        self.assertEqual(embedder._model.calls, [])

    def test_invalid_inputs_rejected(self):
        embedder = self.ready()
        for value in (None, "", "  "):
            with self.subTest(value=value), self.assertRaises(ValueError):
                embedder.embed_query(value)
        with self.assertRaises(ValueError):
            embedder.embed_passages("not a list")
        with self.assertRaises(ValueError):
            embedder.embed_passages([None])
        for overlap in (True, -1, 0.5):
            with self.assertRaises(ValueError):
                embedder.chunk("text", overlap_tokens=overlap)

    def test_empty_chunks_and_passages_are_empty(self):
        embedder = self.ready()
        self.assertEqual(embedder.chunk("  "), [])
        self.assertEqual(embedder.embed_passages([]), [])
        self.assertEqual(embedder._model.calls, [])

    def test_invalid_vectors_never_return_fake_substitutes(self):
        embedder = self.ready()
        for vector in ([0.0] * 768, [math.nan] * 768, [1.0] * 42):
            FakeSentenceTransformer.result = [vector]
            with self.subTest(vector=vector[:2]), self.assertRaises(ValueError):
                embedder.embed_query("query")
        FakeSentenceTransformer.result = []
        with self.assertRaisesRegex(EmbeddingUnavailable, "model_batch_size_mismatch"):
            embedder.embed_query("query")

    def test_e5_small_reuses_local_embedder_and_cache_layout(self):
        embedder = self.embedder("e5-small")
        self.assertEqual(embedder.model_dir, self.cache / embedder.model_revision)
        for name in embedder._required_names():
            path = embedder.model_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic")
        delegate = types.SimpleNamespace(_model=object(), prepare=unittest.mock.Mock(),
                                         _load_tokenizer=lambda: CharacterTokenizer())
        with patch("kg.embedding_profiles.LocalEmbedder", return_value=delegate) as factory:
            embedder.prepare()
        factory.assert_called_once_with(self.cache, model_revision=embedder.model_revision, threads=8)
        delegate.prepare.assert_called_once_with(allow_download=False)
        self.assertTrue(embedder.status()["ready"])

    def test_cli_prepare_defaults_to_offline_and_safe_error(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["prepare", "--model", "e5-base", "--cache-dir", str(self.cache)])
        self.assertEqual(code, 2)
        result = json.loads(output.getvalue())
        self.assertEqual(result["error"], "model_not_cached")
        self.assertNotIn(str(self.cache), output.getvalue())


if __name__ == "__main__":
    unittest.main()
