"""Optional, local E5 embeddings. Importing this module never loads a model.

Only the console/index worker should instantiate ``LocalEmbedder``. The Hermes
provider uses the console's search API, so the agent process stays lightweight.
Model download is an explicit setup operation, never a side effect of a search.
"""
from __future__ import annotations

import hashlib
import math
import threading
from dataclasses import dataclass
from pathlib import Path

MODEL_NAME = "intfloat/multilingual-e5-small"
# A published, immutable HF git revision containing the standard ONNX export.
MODEL_REVISION = "ccc66d3bcd826f577e26b9a4072cc5fe3a7ad6a3"
MODEL_FILE = "onnx/model.onnx"
DIMENSIONS = 384
MAX_TOKENS = 512
EMBEDDING_REVISION = "e5-small-onnx-mean-l2-query-passage-v1"


class EmbeddingUnavailable(RuntimeError):
    """Expected offline/missing-package failure; callers may use lexical search."""


@dataclass(frozen=True)
class TextChunk:
    text: str
    token_count: int
    index: int


def normalized_vector(value):
    """Reject malformed/zero/NaN vectors before persistence or ANN queries."""
    values = [float(component) for component in value]
    if len(values) != DIMENSIONS or not all(math.isfinite(v) for v in values):
        raise ValueError("Embedding must have 384 finite components")
    norm = math.sqrt(sum(v * v for v in values))
    if not norm:
        raise ValueError("Embedding must have a nonzero norm")
    return [v / norm for v in values]


class LocalEmbedder:
    model_name = MODEL_NAME
    dimensions = DIMENSIONS
    embedding_revision = EMBEDDING_REVISION

    def __init__(self, cache_dir, *, model_revision=MODEL_REVISION, threads=2):
        if not model_revision or model_revision in {"main", "master", "latest"}:
            raise ValueError("A pinned model revision is required")
        self.cache_dir = Path(cache_dir).expanduser().resolve()
        self.model_revision = model_revision
        self.threads = threads
        self._model = self._tokenizer = None
        self._lock = threading.RLock()
        self._failure = None

    @property
    def fingerprint(self):
        return hashlib.sha256((self.model_name + "@" + self.model_revision + ":" +
                               self.embedding_revision).encode()).hexdigest()

    @property
    def model_dir(self):
        return self.cache_dir / self.model_revision

    def status(self):
        """No downloads, heavyweight imports, or model loading in health checks."""
        return {"model": self.model_name, "model_revision": self.model_revision,
                "embedding_revision": self.embedding_revision, "dimensions": DIMENSIONS,
                "ready": self._model is not None, "cached": self._cached(),
                "error": self._failure}

    def _cached(self):
        return (self.model_dir / MODEL_FILE).is_file() and (self.model_dir / "tokenizer.json").is_file()

    def prepare(self, *, allow_download=False):
        """Explicit initialization; download is opt-in and pinned to a revision."""
        with self._lock:
            try:
                if not self._cached():
                    if not allow_download:
                        raise EmbeddingUnavailable("model_not_cached")
                    from huggingface_hub import snapshot_download
                    self.model_dir.mkdir(parents=True, exist_ok=True)
                    snapshot_download(repo_id=MODEL_NAME, revision=self.model_revision,
                                      local_dir=str(self.model_dir),
                                      allow_patterns=[MODEL_FILE, "config.json", "tokenizer.json",
                                                      "tokenizer_config.json", "special_tokens_map.json",
                                                      "sentencepiece.bpe.model"])
                from fastembed import TextEmbedding
                from fastembed.common.model_description import ModelSource, PoolingType
                self._load_tokenizer()
                registered = {m["model"].lower() for m in TextEmbedding.list_supported_models()}
                if MODEL_NAME.lower() not in registered:
                    TextEmbedding.add_custom_model(
                        model=MODEL_NAME, pooling=PoolingType.MEAN, normalization=True,
                        sources=ModelSource(hf=MODEL_NAME), dim=DIMENSIONS,
                        model_file=MODEL_FILE, license="mit")
                self._model = TextEmbedding(model_name=MODEL_NAME,
                                            specific_model_path=str(self.model_dir),
                                            local_files_only=True, threads=self.threads,
                                            providers=["CPUExecutionProvider"])
                self._failure = None
            except (ImportError, OSError, RuntimeError, ValueError) as exc:
                # Never expose a URL, path, API key, or arbitrary library error to tools.
                self._failure = (str(exc) if isinstance(exc, EmbeddingUnavailable)
                                 else "embedding_initialization_failed")
                raise EmbeddingUnavailable(self._failure) from exc
        return self.status()

    def _load_tokenizer(self):
        if self._tokenizer is None:
            try:
                from tokenizers import Tokenizer
                self._tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
                # Count the real untruncated model tokens, including E5 prefix and
                # special tokens, rather than estimating from characters.
                self._tokenizer.no_truncation()
                self._tokenizer.no_padding()
            except Exception as exc:
                raise EmbeddingUnavailable("tokenizer_unavailable") from exc
        return self._tokenizer

    def chunk(self, text, *, prefix="passage: ", overlap_tokens=32):
        if not isinstance(text, str):
            raise ValueError("Chunk input must be text")
        if not text.strip():
            return []
        with self._lock:
            tokenizer = self._load_tokenizer()
            encoded = tokenizer.encode(text, add_special_tokens=False)
            offsets = [(start, end) for start, end in encoded.offsets if end > start]
            if not offsets:
                return []
            # The actual prefixed text is re-tokenized for every chunk because
            # prefix/body boundaries can change tokenization in SentencePiece.
            chunks, start = [], 0
            while start < len(offsets):
                low, high = start + 1, min(len(offsets), start + MAX_TOKENS)
                accepted, token_count = start, 0
                while low <= high:
                    end = (low + high) // 2
                    piece = text[offsets[start][0]:offsets[end - 1][1]]
                    count = len(tokenizer.encode(prefix + piece, add_special_tokens=True).ids)
                    if count <= MAX_TOKENS:
                        accepted, token_count, low = end, count, end + 1
                    else:
                        high = end - 1
                if accepted == start:
                    raise EmbeddingUnavailable("tokenizer_cannot_fit_chunk")
                piece = text[offsets[start][0]:offsets[accepted - 1][1]]
                chunks.append(TextChunk(piece, token_count, len(chunks)))
                if accepted == len(offsets):
                    break
                # Positive progress even for unusually expensive one-token spans.
                start = max(start + 1, accepted - min(overlap_tokens, accepted - start - 1))
            return chunks

    def _encode(self, texts, prefix):
        with self._lock:
            if self._model is None:
                self.prepare(allow_download=False)
            prefixed = []
            tokenizer = self._load_tokenizer()
            for text in texts:
                value = prefix + text
                if len(tokenizer.encode(value, add_special_tokens=True).ids) > MAX_TOKENS:
                    raise ValueError("Embedding input exceeds 512 tokens; chunk it first")
                prefixed.append(value)
            # Use embed(), not model-specific query_embed(), to add E5 prefixes
            # exactly once. Keep inference in this process with bounded batches.
            return [normalized_vector(v) for v in self._model.embed(prefixed, batch_size=16, parallel=None)]

    def embed_passages(self, texts):
        return self._encode(list(texts), "passage: ")

    def embed_query(self, text):
        # Long queries use their first model-sized token chunk explicitly.
        chunks = self.chunk(text, prefix="query: ", overlap_tokens=0)
        if not chunks:
            raise ValueError("A nonempty query is required")
        return self._encode([chunks[0].text], "query: ")[0]


E5Embedder = LocalEmbedder
