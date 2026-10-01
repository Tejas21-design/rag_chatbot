"""The one embedder, shared by ingestion and query.

Both pipelines must live in the same vector space (architecture section 1,
principle 2). Keeping the model behind a single module here is what stops
ingest and query drifting onto different models or different dimensions.
"""

from __future__ import annotations

from functools import lru_cache

from app import config


@lru_cache(maxsize=1)
def get_model():
    """Cached SentenceTransformer. The model is ~90 MB, so load it once."""
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


def _as_vectors(encoded) -> list[list[float]]:
    vectors = [list(map(float, vector)) for vector in encoded]
    for vector in vectors:
        if len(vector) != config.EMBEDDING_DIM:
            raise RuntimeError(
                f"expected {config.EMBEDDING_DIM}-d vectors, got {len(vector)}-d"
            )
    return vectors


def embed_texts(texts: list[str], *, batch_size: int = 32) -> list[list[float]]:
    """Embed chunk texts for ingestion. 384-d, L2-normalised by the model."""
    if not texts:
        return []
    return _as_vectors(get_model().encode(texts, batch_size=batch_size, show_progress_bar=False))


def embed_query(text: str) -> list[float]:
    """Embed a user question using the same model and space as the chunks."""
    return _as_vectors(get_model().encode([text], show_progress_bar=False))[0]
