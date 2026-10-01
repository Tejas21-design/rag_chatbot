"""Pipeline A: Load -> Chunk -> Embed -> Store, as a one-time CLI job.

Run explicitly, never from the chat request path (PRD section 6, architecture
section 4). Embeds locally with MiniLM and stores in Chroma on disk. Never
calls Groq: generation is a query-time concern (architecture section 10).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone

from app import config, store
from app.chunker import Chunk, chunk_document, write_chunks_txt
from app.embedder import embed_texts
from app.loader import extract_sections, load_all

BATCH_SIZE = 32


def _log(message: str) -> None:
    print(message, flush=True)


def ingest(*, reingest: bool = False, force: bool = False, on_progress=None) -> dict:
    """Build the corpus and store it. Skips when Chroma is already populated.

    ``on_progress`` receives a short status line after each step, so a caller
    driving this from a UI can show real progress instead of an indeterminate
    spinner.
    """
    if store.is_populated() and not (reingest or force):
        _log("Chroma already populated, skipping ingest.")
        _log(f"  collection : {config.COLLECTION_NAME}")
        _log(f"  persist dir: {config.CHROMA_DIR}")
        _log("re-run with: python -m app.ingest --reingest")
        return {"skipped": True, "chroma_dir": str(config.CHROMA_DIR)}

    if reingest or force:
        _log("Resetting collection (--reingest) so no chunks are duplicated...")
        store.reset_collection()

    deadline = time.monotonic() + config.INGEST_DEADLINE_SECONDS

    def step(message: str) -> None:
        _log(message)
        if on_progress is not None:
            on_progress(message)

    def check_deadline(stage: str) -> None:
        if deadline - time.monotonic() <= 0:
            raise TimeoutError(
                f"Ingest exceeded {config.INGEST_DEADLINE_SECONDS}s while {stage}. "
                "hdfcfund.com was slow or unreachable from this host. A build step "
                "has no such ceiling, so running `python -m app.ingest` where egress "
                "is normal is the workaround."
            )

    step(f"Fetching {len(config.APPROVED_URLS)} approved URLs (docs/PRD.md 4.2)...")
    check_deadline("fetching pages")
    documents = load_all()
    step(f"Fetched {len(documents)} pages")

    chunks: list[Chunk] = []
    dropped = []
    thin_pages: list[str] = []
    for doc in documents:
        check_deadline("chunking pages")
        # Reuse the HTML load_all already downloaded. Re-fetching here doubled
        # the network time of every ingest for identical output.
        _, sections = extract_sections(doc["html"], doc["url"])
        page_chunks, page_dropped = chunk_document(doc, sections)
        chunks.extend(page_chunks)
        dropped.extend(page_dropped)
        slug = config.slug_for(doc["url"])[:44]
        step(f"  {slug:46s} chunks={len(page_chunks):3d}")
        if not page_chunks:
            # A page can return HTTP 200 and still carry no usable text, because
            # the documents are rendered by JavaScript. It fetched, so nothing
            # errored, and the corpus just quietly misses it -- which reads the
            # same as a page that worked. Say so.
            thin_pages.append(doc["url"])


    for url in thin_pages:
        _log(f"  WARNING: no chunks from {url} (likely JS-rendered); questions "
             "about it will answer 'not in the official pages'.")

    if not chunks:
        raise RuntimeError("No chunks produced; refusing to store an empty corpus.")

    _log(f"Embedding {len(chunks)} chunks with {config.EMBEDDING_MODEL}...")
    vectors = embed_texts([chunk.text for chunk in chunks], batch_size=BATCH_SIZE)
    if len(vectors) != len(chunks):
        raise RuntimeError(f"embedded {len(vectors)} vectors for {len(chunks)} chunks")
    _log(f"  {len(vectors)} vectors of {len(vectors[0])} dimensions")

    collection = store.get_collection()
    collection.upsert(
        ids=[chunk.id for chunk in chunks],
        documents=[chunk.text for chunk in chunks],
        metadatas=[chunk.metadata for chunk in chunks],
        embeddings=vectors,
    )
    stored = collection.count()
    _log(f"Stored in {config.COLLECTION_NAME}: {stored} chunks")

    ingested_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    write_chunks_txt(
        chunks,
        config.CHUNKS_TXT,
        corpus=config.APPROVED_URLS,
        generated_at=ingested_at,
        dropped=dropped,
    )
    _log(f"Wrote {config.CHUNKS_TXT} ({len(chunks)} chunks, {len(dropped)} dropped)")

    manifest = {
        "ingested_at": ingested_at,
        # Drives the UI's "Last updated from sources:" line. Never the model's.
        "max_fetched_at": max(doc["fetched_at"] for doc in documents),
        "urls": [doc["url"] for doc in documents],
        "chunk_count": len(chunks),
        "dropped_count": len(dropped),
        "collection": config.COLLECTION_NAME,
        "embedding_model": config.EMBEDDING_MODEL,
        "embedding_dim": config.EMBEDDING_DIM,
        "chroma_dir": str(config.CHROMA_DIR),
        "chunks_txt": str(config.CHUNKS_TXT),
        "chunk_strategy": {
            "target_words": 160,
            "max_words": 200,
            "min_words": 12,
            "overlap_words": 30,
            "note": "docs/chunking.md",
        },
    }
    config.INGEST_MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _log(f"Wrote {config.INGEST_MANIFEST}")
    _log(f"  max_fetched_at (Last updated from sources) = {manifest['max_fetched_at']}")
    return {"skipped": False, **manifest}


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest the approved HDFC corpus into Chroma.")
    parser.add_argument(
        "--reingest",
        action="store_true",
        help="Delete and rebuild the collection instead of skipping when populated.",
    )
    args = parser.parse_args()
    try:
        ingest(reingest=args.reingest)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"INGEST FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
