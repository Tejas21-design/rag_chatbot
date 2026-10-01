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
    """True when ingest has already run.

    Previously this returned False on any exception, which reported a Chroma
    failure as "Corpus not ingested" and hid the cause. Callers that care should
    use population_status(), which keeps the error.
    """
    return population_status()["populated"]


def population_status() -> dict:
    """Report whether the corpus is usable, and why not when it is not.

    A bare bool cannot distinguish "ingest never ran" from "Chroma is present but
    unreadable", and those need completely different fixes.
    """
    try:
        count = get_collection().count()
        return {"populated": count > 0, "count": count, "error": None}
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller, not hidden
        return {
            "populated": False,
            "count": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }


def reset_collection() -> None:
    """Delete and recreate, so a re-ingest never duplicates demo data
    (architecture section 5.5)."""
    client = get_client()
    try:
        client.delete_collection(config.COLLECTION_NAME)
    except Exception:  # noqa: BLE001 - nothing to delete on a first run
        pass
