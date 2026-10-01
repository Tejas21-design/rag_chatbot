"""The one embedder, shared by ingestion and query.

Both pipelines must live in the same vector space (architecture section 1,
principle 2). Keeping the model behind a single module here is what stops
ingest and query drifting onto different models or different dimensions.

Two backends, same network:

* ``onnx``      -- ONNX Runtime, ~110 MB resident. Used whenever
                   ``models/minilm.onnx`` exists. This is what a 512 MB
                   container can actually run, since importing torch costs
                   ~450 MB before it does any work.
* ``torch``     -- sentence-transformers. The reference implementation, and
                   what the corpus was built with.

They agree to cosine 0.9999999+, so switching does not invalidate the stored
embeddings. ``EMBEDDER_BACKEND`` forces one; otherwise ONNX is preferred when
its files are present. Ingestion must use whichever backend the corpus was
built with -- see ``config.BACKEND_FINGERPRINT`` for how that is enforced.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from app import config


class OnnxEncoder:
    """sentence-transformers-equivalent encoding without importing torch.

    Reproduces exactly what ``SentenceTransformer.encode`` does for this model:
    mean-pool over the attention mask, then L2-normalise.
    """

    def __init__(self, model_path: Path | None = None, tokenizer_path: Path | None = None):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        model_path = model_path or (config.MODELS_DIR / "minilm.onnx")
        tokenizer_path = tokenizer_path or (config.MODELS_DIR / "tokenizer.json")
        if not model_path.exists() or not tokenizer_path.exists():
            raise FileNotFoundError(
                f"ONNX model missing ({model_path.name}, {tokenizer_path.name}). "
                "Run: python scripts/export_onnx.py"
            )
        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._tokenizer.enable_padding()
        # MiniLM's window is 256 word pieces. Truncating here matches the
        # sentence-transformers default and bounds the ONNX sequence axis.
        self._tokenizer.enable_truncation(max_length=config.MAX_SEQUENCE_LENGTH)

        options = ort.SessionOptions()
        # Default is one thread per core, and each thread arena costs memory.
        threads = os.getenv("OMP_NUM_THREADS", "1")
        options.intra_op_num_threads = int(threads)
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = ort.InferenceSession(str(model_path), options, providers=["CPUExecutionProvider"])
        self._dim = self._session.get_outputs()[0].shape[-1]
        if isinstance(self._dim, str) or int(self._dim) != config.EMBEDDING_DIM:
            raise RuntimeError(
                f"ONNX model outputs {self._dim}-d, expected {config.EMBEDDING_DIM}-d"
            )

    def encode(self, texts: list[str]) -> list[list[float]]:
        import numpy as np

        if not texts:
            return []
        encodings = self._tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        hidden = self._session.run(
            None, {"input_ids": input_ids, "attention_mask": attention}
        )[0]
        mask = attention[..., None].astype(hidden.dtype)
        pooled = (hidden * mask).sum(axis=1) / np.clip(mask.sum(axis=1), 1e-9, None)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        np.divide(pooled, norms, out=pooled, where=norms > 0)
        return [[float(x) for x in row] for row in pooled]


@lru_cache(maxsize=1)
def _torch_model():
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(config.EMBEDDING_MODEL)
    # get_sentence_embedding_dimension() is deprecated in sentence-transformers 5.x.
    getter = getattr(model, "get_embedding_dimension", None) or model.get_sentence_embedding_dimension
    dimension = getter()
    if dimension != config.EMBEDDING_DIM:
        raise RuntimeError(
            f"{config.EMBEDDING_MODEL} produced {dimension}-d vectors, "
            f"expected {config.EMBEDDING_DIM}. Ingest and query would not share "
            "a vector space."
        )
    return model


@lru_cache(maxsize=1)
def get_model():
    """The shared encoder, whichever backend is active."""
    return get_encoder()


def available_backend() -> str:
    """Which backend is active, and why.

    EMBEDDER_BACKEND pins it. Otherwise ONNX wins when models/minilm.onnx exists,
    because it is the only one that fits a small container.
    """
    forced = os.getenv("EMBEDDER_BACKEND", "").strip().lower()
    if forced in {"onnx", "torch"}:
        return forced
    if (config.MODELS_DIR / "minilm.onnx").exists():
        return "onnx"
    return "torch"


@lru_cache(maxsize=1)
def get_encoder():
    backend = available_backend()
    if backend == "onnx":
        return OnnxEncoder()
    return _TorchEncoder()


class _TorchEncoder:
    """Uniform wrapper so both backends expose ``encode(list[str]) -> list[list[float]]``."""

    def encode(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        return [
            [float(x) for x in row]
            for row in _torch_model().encode(
                texts,
                batch_size=batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        ]


def _as_vectors(encoded) -> list[list[float]]:
    vectors = [list(map(float, vector)) for vector in encoded]
    for vector in vectors:
        if len(vector) != config.EMBEDDING_DIM:
            raise RuntimeError(
                f"expected {config.EMBEDDING_DIM}-d vectors, got {len(vector)}-d"
            )
    return vectors


def embed_texts(texts: list[str], *, batch_size: int = 32, backend: str | None = None) -> list[list[float]]:
    """Embed chunk texts for ingestion. 384-d, L2-normalised by the model."""
    if not texts:
        return []
    if backend == "torch":
        return _as_vectors(_TorchEncoder().encode(texts, batch_size=batch_size))
    return _as_vectors(get_encoder().encode(texts))


def embed_query(text: str) -> list[float]:
    """Embed a user question using the same model and space as the chunks."""
    return _as_vectors(get_encoder().encode([text]))[0]
