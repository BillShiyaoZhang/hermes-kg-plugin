"""Pinned, opt-in embedding backends for retrieval research.

This module uses only the standard library until ``prepare`` is called. It does
not change the production E5 backend or install packages into the Hermes host.
The SentenceTransformers module graph is retained (notably Gemma's Dense layers).
Downloads require an explicit ``prepare(allow_download=True)``. Offline loads
always use a local directory, without remote code or automatic model fallback.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from .embedding import EmbeddingUnavailable, LocalEmbedder, TextChunk, normalized_vector


RESEARCH_REQUIREMENTS = (
    "torch==2.8.0", "sentence-transformers==5.1.2", "transformers==4.56.2",
    "huggingface-hub==0.35.3", "tokenizers==0.22.1", "safetensors==0.6.2",
    "sentencepiece==0.2.1", "numpy==2.2.6",
)
PROFILE_REVISION = "research-dense-offline-v1"


@dataclass(frozen=True)
class ModelProfile:
    key: str
    model_name: str
    model_revision: str
    dimensions: int
    native_max_tokens: int
    pooling: str
    query_format: str
    passage_format: str
    backend: str = "sentence-transformers"
    gated: bool = False

    def __post_init__(self):
        if not re.fullmatch(r"[0-9a-f]{40}", self.model_revision):
            raise ValueError("A full immutable model commit SHA is required")


# SHAs verified against public HF model metadata on 2026-10-24. E5-small
# intentionally retains the verified production ONNX revision. Encoding sources:
# https://huggingface.co/intfloat/multilingual-e5-base
# https://huggingface.co/Qwen/Qwen3-Embedding-0.6B
# https://huggingface.co/BAAI/bge-m3
# https://huggingface.co/google/embeddinggemma-300m
_PROFILES = (
    ModelProfile("e5-small", "intfloat/multilingual-e5-small",
                 "ccc66d3bcd826f577e26b9a4072cc5fe3a7ad6a3", 384, 512,
                 "masked_mean+l2", "query: {text}", "passage: {text}", "fastembed-onnx"),
    ModelProfile("e5-base", "intfloat/multilingual-e5-base",
                 "d128750597153bb5987e10b1c3493a34e5a4502a", 768, 512,
                 "masked_mean+l2", "query: {text}", "passage: {text}"),
    ModelProfile("qwen3-0.6b", "Qwen/Qwen3-Embedding-0.6B",
                 "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3", 1024, 32768,
                 "last_token+l2",
                 "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:{text}",
                 "{text}"),
    ModelProfile("bge-m3", "BAAI/bge-m3",
                 "5617a9f61b028005a4858fdac845db406aefb181", 1024, 8192,
                 "cls+l2", "{text}", "{text}"),
    ModelProfile("embeddinggemma-300m", "google/embeddinggemma-300m",
                 "57c266a740f537b4dc058e1b0cda161fd15afa75", 768, 2048,
                 "mean+repository_dense+l2", "task: search result | query: {text}",
                 "title: none | text: {text}", gated=True),
)
_BY_KEY = {item.key: item for item in _PROFILES}
_ALIASES = {"qwen3": "qwen3-0.6b", "gemma": "embeddinggemma-300m"}
_ST_FILES = (
    "config.json", "config_sentence_transformers.json", "sentence_bert_config.json",
    "modules.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
    "added_tokens.json", "tokenizer.model", "sentencepiece.bpe.model", "vocab.json", "merges.txt",
    "1_Pooling/config.json", "2_Dense/config.json", "2_Dense/model.safetensors",
    "3_Dense/config.json", "3_Dense/model.safetensors",
)
_ONNX_FILES = ("config.json", "tokenizer.json", "tokenizer_config.json",
               "special_tokens_map.json", "sentencepiece.bpe.model", "onnx/model.onnx")


def available_profiles():
    """JSON-safe immutable model identities; no imports, I/O, or downloads."""
    return [asdict(item) for item in _PROFILES]


def create_embedder(key, cache_dir, *, device="cpu", precision="float32", threads=8,
                    batch_size=8, max_tokens=None):
    key = _ALIASES.get(key, key)
    if key not in _BY_KEY:
        raise ValueError("Unknown research embedding profile")
    return ProfileEmbedder(_BY_KEY[key], cache_dir, device=device, precision=precision,
                           threads=threads, batch_size=batch_size, max_tokens=max_tokens)


def _positive(value, name, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer between 1 and {maximum}")
    return value


def _digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


class ProfileEmbedder:
    def __init__(self, profile, cache_dir, *, device="cpu", precision="float32", threads=8,
                 batch_size=8, max_tokens=None):
        if precision not in {"float32", "float16", "bfloat16"}:
            raise ValueError("Unsupported embedding precision")
        if not isinstance(device, str) or not re.fullmatch(r"cpu|cuda(?::[0-9]+)?", device):
            raise ValueError("Device must be cpu or an explicit CUDA device")
        if device == "cpu" and precision == "float16":
            raise ValueError("CPU float16 is unsupported")
        if profile.key == "embeddinggemma-300m" and precision == "float16":
            raise ValueError("EmbeddingGemma does not support float16 activations")
        if profile.backend == "fastembed-onnx" and (device != "cpu" or precision != "float32"):
            raise ValueError("The reused E5-small ONNX backend requires CPU float32")
        self.profile = profile
        self.model_name, self.model_revision = profile.model_name, profile.model_revision
        self.dimensions = profile.dimensions
        self.embedding_revision = f"{profile.key}-{PROFILE_REVISION}"
        self.cache_dir = Path(cache_dir).expanduser().resolve()
        self.device, self.precision = device, precision
        self.threads = _positive(threads, "threads", 256)
        self.batch_size = _positive(batch_size, "batch_size", 1024)
        self.max_tokens = _positive(profile.native_max_tokens if max_tokens is None else max_tokens,
                                    "max_tokens", profile.native_max_tokens)
        self._model = self._tokenizer = self._torch = self._delegate = None
        self._file_hashes, self._failure = {}, None
        self._lock = threading.RLock()

    @property
    def model_dir(self):
        if self.profile.backend == "fastembed-onnx":
            return self.cache_dir / self.model_revision
        return self.cache_dir / self.profile.key / self.model_revision

    def _artifact_names(self):
        if self.profile.backend == "fastembed-onnx":
            return _ONNX_FILES
        weights = "pytorch_model.bin" if self.profile.key == "bge-m3" else "model.safetensors"
        return (*_ST_FILES, weights)

    def _required_names(self):
        if self.profile.backend == "fastembed-onnx":
            return ("onnx/model.onnx", "tokenizer.json")
        weights = "pytorch_model.bin" if self.profile.key == "bge-m3" else "model.safetensors"
        required = ["config.json", "modules.json", "tokenizer.json", "1_Pooling/config.json", weights]
        if self.profile.gated:
            required.extend(("2_Dense/model.safetensors", "3_Dense/model.safetensors"))
        return required

    def _cached(self):
        return all((self.model_dir / name).is_file() for name in self._required_names())

    def _versions(self):
        names = ("fastembed", "onnxruntime", "tokenizers", "numpy") if self.profile.backend == "fastembed-onnx" else (
            "torch", "sentence-transformers", "transformers", "huggingface-hub", "tokenizers",
            "safetensors", "sentencepiece", "numpy")
        result = {}
        for name in names:
            try:
                result[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                result[name] = None
        return result

    @property
    def model_manifest(self):
        return {**asdict(self.profile), "model": self.model_name,
                "implementation_sha256": _json_hash({
                    name: _digest(Path(__file__).with_name(name))
                    for name in ("embedding.py", "embedding_profiles.py")}),
                "embedding_revision": self.embedding_revision, "precision": self.precision,
                "device": self.device, "threads": self.threads, "batch_size": self.batch_size,
                "max_tokens": self.max_tokens, "query_overflow": "first_chunk",
                "passage_overflow": "reject_and_require_chunking", "output": "dense_l2_float32",
                "files_sha256": dict(self._file_hashes), "packages": self._versions(),
                "python": platform.python_version(), "machine": platform.machine(),
                "ready": self._model is not None, "artifact_integrity": "local_sha256",
                "fingerprint_requires_prepare": True}

    @property
    def fingerprint(self):
        manifest = self.model_manifest
        for field in ("ready", "fingerprint_requires_prepare"):
            manifest.pop(field)
        return _json_hash(manifest)

    def status(self):
        return {"profile": self.profile.key, "model": self.model_name,
                "model_revision": self.model_revision, "embedding_revision": self.embedding_revision,
                "dimensions": self.dimensions, "ready": self._model is not None,
                "cached": self._cached(), "gated": self.profile.gated, "error": self._failure}

    def _hash_artifacts(self):
        # Include only loader inputs, excluding HF bookkeeping and our own manifest.
        return {name: _digest(self.model_dir / name) for name in self._artifact_names()
                if (self.model_dir / name).is_file()}

    def _validate_modules(self):
        modules = json.loads((self.model_dir / "modules.json").read_text(encoding="utf-8"))
        allowed = {"sentence_transformers.models.Transformer", "sentence_transformers.models.Pooling",
                   "sentence_transformers.models.Normalize", "sentence_transformers.models.Dense"}
        if not isinstance(modules, list) or not modules:
            raise EmbeddingUnavailable("invalid_local_module_graph")
        for module in modules:
            if (not isinstance(module, dict) or module.get("type") not in allowed
                    or module.get("path", "") not in {"", "1_Pooling", "2_Normalize", "2_Dense", "3_Dense", "4_Normalize"}):
                raise EmbeddingUnavailable("unsupported_local_module_graph")
        by_path = {module.get("path", ""): module["type"].rsplit(".", 1)[-1] for module in modules}
        if by_path.get("") != "Transformer" or by_path.get("1_Pooling") != "Pooling":
            raise EmbeddingUnavailable("model_module_graph_mismatch")
        if self.profile.gated:
            if any(by_path.get(path) != "Dense" for path in ("2_Dense", "3_Dense")):
                raise EmbeddingUnavailable("model_projection_modules_missing")
        elif by_path != {"": "Transformer", "1_Pooling": "Pooling", "2_Normalize": "Normalize"}:
            raise EmbeddingUnavailable("model_module_graph_mismatch")
        pooling = json.loads((self.model_dir / "1_Pooling/config.json").read_text(encoding="utf-8"))
        expected = {"e5-base": "pooling_mode_mean_tokens", "qwen3-0.6b": "pooling_mode_lasttoken",
                    "bge-m3": "pooling_mode_cls_token"}.get(self.profile.key)
        if expected:
            enabled = {key for key, value in pooling.items() if key.startswith("pooling_mode_") and value is True}
            if enabled != {expected}:
                raise EmbeddingUnavailable("model_pooling_mismatch")

    def prepare(self, *, allow_download=False):
        if not isinstance(allow_download, bool):
            raise ValueError("allow_download must be boolean")
        with self._lock:
            try:
                if not self._cached():
                    if not allow_download:
                        raise EmbeddingUnavailable("model_not_cached")
                    if self.profile.backend == "fastembed-onnx":
                        # Reuse the production pinned download + ONNX initialization.
                        self._delegate = LocalEmbedder(self.cache_dir, model_revision=self.model_revision,
                                                       threads=self.threads)
                        self._delegate.prepare(allow_download=True)
                    else:
                        from huggingface_hub import snapshot_download
                        self.model_dir.mkdir(parents=True, exist_ok=True)
                        snapshot_download(repo_id=self.model_name, revision=self.model_revision,
                                          local_dir=str(self.model_dir),
                                          allow_patterns=list(self._artifact_names()))
                if not self._cached():
                    raise EmbeddingUnavailable("model_cache_incomplete")
                hashes = self._hash_artifacts()
                seal = self.cache_dir / "research-manifests" / f"{self.profile.key}-{self.model_revision}-artifacts.json"
                if seal.is_file() and json.loads(seal.read_text(encoding="utf-8")) != hashes:
                    raise EmbeddingUnavailable("model_cache_integrity_changed")
                if self._model is None:
                    if self.profile.backend == "fastembed-onnx":
                        if self._delegate is None:
                            self._delegate = LocalEmbedder(self.cache_dir, model_revision=self.model_revision,
                                                           threads=self.threads)
                            self._delegate.prepare(allow_download=False)
                        self._model = self._delegate._model
                        self._tokenizer = self._delegate._load_tokenizer()
                    else:
                        self._load_sentence_transformer()
                self._file_hashes = hashes
                self._failure = None
                seal.parent.mkdir(parents=True, exist_ok=True)
                # First successful local load seals the exact artifacts; this is not
                # a claim that an arbitrary user-provided cache has HF attestation.
                if not seal.exists():
                    seal.write_text(json.dumps(hashes, sort_keys=True, indent=2) + "\n", encoding="utf-8")
                manifest_path = seal.parent / f"{self.profile.key}-{self.fingerprint}.json"
                manifest_path.write_text(json.dumps(self.model_manifest, ensure_ascii=False, sort_keys=True,
                                                    indent=2) + "\n", encoding="utf-8")
            except Exception as exc:
                self._model = self._tokenizer = None
                self._failure = str(exc) if isinstance(exc, EmbeddingUnavailable) else "embedding_initialization_failed"
                raise EmbeddingUnavailable(self._failure) from exc
        return self.status()

    def _load_sentence_transformer(self):
        self._validate_modules()
        import torch
        from sentence_transformers import SentenceTransformer
        if self.device.startswith("cuda"):
            if not torch.cuda.is_available():
                raise EmbeddingUnavailable("requested_cuda_unavailable")
            if self.precision == "bfloat16" and not torch.cuda.is_bf16_supported():
                raise EmbeddingUnavailable("requested_bfloat16_unavailable")
        torch.set_num_threads(self.threads)
        dtype = getattr(torch, self.precision)
        tokenizer_kwargs = {"padding_side": "left"} if self.profile.key == "qwen3-0.6b" else {}
        model = SentenceTransformer(str(self.model_dir), device=self.device, local_files_only=True,
                                    trust_remote_code=False, backend="torch",
                                    model_kwargs={"torch_dtype": dtype, "attn_implementation": "eager"},
                                    tokenizer_kwargs=tokenizer_kwargs)
        if model.get_sentence_embedding_dimension() != self.dimensions:
            raise EmbeddingUnavailable("model_dimension_mismatch")
        if not getattr(model.tokenizer, "is_fast", False):
            raise EmbeddingUnavailable("fast_tokenizer_required_for_exact_chunking")
        model.max_seq_length = self.max_tokens
        model.eval()
        self._torch, self._model, self._tokenizer = torch, model, model.tokenizer

    def _ensure_ready(self):
        if self._model is None:
            self.prepare(allow_download=False)

    def _token_ids(self, text):
        if self.profile.backend == "fastembed-onnx":
            return self._tokenizer.encode(text, add_special_tokens=True).ids
        return self._tokenizer(text, add_special_tokens=True, truncation=False, padding=False)["input_ids"]

    def _offsets(self, text):
        if self.profile.backend == "fastembed-onnx":
            offsets = self._tokenizer.encode(text, add_special_tokens=False).offsets
        else:
            offsets = self._tokenizer(text, add_special_tokens=False, truncation=False,
                                      padding=False, return_offsets_mapping=True)["offset_mapping"]
        return [(start, end) for start, end in offsets if end > start]

    def _chunks(self, text, template, overlap_tokens):
        if not isinstance(text, str):
            raise ValueError("Chunk input must be text")
        if isinstance(overlap_tokens, bool) or not isinstance(overlap_tokens, int) or overlap_tokens < 0:
            raise ValueError("overlap_tokens must be a nonnegative integer")
        if not text.strip():
            return []
        self._ensure_ready()
        offsets = self._offsets(text)
        chunks, start = [], 0
        while start < len(offsets):
            low, high = start + 1, min(len(offsets), start + self.max_tokens)
            accepted, count = start, 0
            while low <= high:
                end = (low + high) // 2
                piece = text[offsets[start][0]:offsets[end - 1][1]]
                token_count = len(self._token_ids(template.format(text=piece)))
                if token_count <= self.max_tokens:
                    accepted, count, low = end, token_count, end + 1
                else:
                    high = end - 1
            if accepted == start:
                raise EmbeddingUnavailable("tokenizer_cannot_fit_chunk")
            chunks.append(TextChunk(text[offsets[start][0]:offsets[accepted - 1][1]], count, len(chunks)))
            if accepted == len(offsets):
                break
            start = max(start + 1, accepted - min(overlap_tokens, accepted - start - 1))
        return chunks

    def chunk(self, text, *, overlap_tokens=32):
        with self._lock:
            return self._chunks(text, self.profile.passage_format, overlap_tokens)

    def _encode(self, texts, template):
        self._ensure_ready()
        formatted = []
        for text in texts:
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Embedding input must be nonempty text")
            value = template.format(text=text)
            if len(self._token_ids(value)) > self.max_tokens:
                raise ValueError("Embedding input exceeds the profile token budget; chunk it first")
            formatted.append(value)
        if not formatted:
            return []
        if self.profile.backend == "fastembed-onnx":
            vectors = list(self._model.embed(formatted, batch_size=self.batch_size, parallel=None))
        else:
            # Empty prompt suppresses repository defaults because formats above
            # have already been applied exactly once. Output precision remains
            # float32; model activation precision is specified at load time.
            with self._torch.inference_mode():
                vectors = self._model.encode(formatted, batch_size=self.batch_size,
                                             prompt="", normalize_embeddings=True,
                                             precision="float32", convert_to_numpy=True,
                                             show_progress_bar=False)
        if len(vectors) != len(formatted):
            raise EmbeddingUnavailable("model_batch_size_mismatch")
        return [normalized_vector(vector, dimensions=self.dimensions) for vector in vectors]

    def embed_passages(self, texts):
        if isinstance(texts, (str, bytes)):
            raise ValueError("Passages must be a sequence of texts")
        with self._lock:
            return self._encode(list(texts), self.profile.passage_format)

    def embed_query(self, text):
        with self._lock:
            chunks = self._chunks(text, self.profile.query_format, 0)
            if not chunks:
                raise ValueError("A nonempty query is required")
            return self._encode([chunks[0].text], self.profile.query_format)[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("list", "status", "prepare"))
    parser.add_argument("--model", choices=list(_BY_KEY), default="e5-small")
    parser.add_argument("--cache-dir", type=Path, default=Path(".model-cache"))
    parser.add_argument("--allow-download", action="store_true")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--precision", default="float32")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--max-tokens", type=int)
    args = parser.parse_args(argv)
    if args.action == "list":
        print(json.dumps(available_profiles(), ensure_ascii=False, indent=2))
        return 0
    embedder = create_embedder(args.model, args.cache_dir, device=args.device,
                               precision=args.precision, threads=args.threads, max_tokens=args.max_tokens)
    try:
        if args.action == "prepare":
            embedder.prepare(allow_download=args.allow_download)
        print(json.dumps({"status": embedder.status(), "manifest": embedder.model_manifest,
                          "fingerprint": embedder.fingerprint}, ensure_ascii=False, indent=2))
        return 0
    except EmbeddingUnavailable as exc:
        print(json.dumps({"status": embedder.status(), "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
