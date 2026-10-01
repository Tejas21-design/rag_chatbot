"""Chroma access. Shared by ingest and query so both hit the same collection."""

from __future__ import annotations

import chromadb
from chromadb.config import Settings

from app import config

_client = None


def get_client():
    """Persistent Chroma client. Cached so ingest and query share one handle."""
    global _client
    if _client is None:
        config.CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(
            path=str(config.CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False, allow_reset=True),
        )
    return _client


def get_collection():
    """The one collection, created on first use with cosine distance.

    Cosine matches MiniLM's normalised output, so distance ordering is the same
    ordering the model was trained for.
    """
    return get_client().get_or_create_collection(
        name=config.COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def is_populated() -> bool:
    """True when ingest has already run. The app start path checks this and
    skips ingestion entirely (architecture section 5.4)."""
    try:
        return get_collection().count() > 0
    except Exception:  # noqa: BLE001 - a missing collection means "not ingested"
        return False


def reset_collection() -> None:
    """Delete and recreate, so a re-ingest never duplicates demo data
    (architecture section 5.5)."""
    client = get_client()
    try:
        client.delete_collection(config.COLLECTION_NAME)
    except Exception:  # noqa: BLE001 - nothing to delete on a first run
        pass
