"""Ingestion-side chunker. Implements the strategy locked in docs/chunking.md.

Input is the section list from app.loader.extract_sections (a "## <heading>"
grammar). One chunk per section while the section fits; only oversized sections
are packed down. Sizes, overlap and drop rules come from docs/chunking.md §2 and
are mirrored here as named constants so ingest cannot drift from the note.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, TypedDict

# --- locked values (docs/chunking.md section 2) ------------------------------

TARGET_WORDS = 160  # packing target for an oversized section
MAX_WORDS = 200  # hard ceiling: above this MiniLM truncates at 256 tokens
MIN_WORDS = 12  # drop crumbs ("Table", "DIRECT", "Graph")
OVERLAP_WORDS = 30  # ~19%, only inside a split section

#: The length floor exists to kill chrome, not facts. Under one of these
#: headings a short value IS the answer: the ELSS page's whole Exit Load section
#: is the single word "NIL", and dropping it would lose a PRD in-scope fact.
FACT_HEADINGS = frozenset(
    {
        "exit load",
        "entry load",
        "lock in",
        "ter",
        "nav",
        "aum",
        "benchmark",
        "riskometer",
        "min sip",
        "inception date",
        "product suitability",
        "downloads",
        "fund managers",
    }
)

#: Chrome seen on these pages. Embedding these produces near-identical vectors
#: that crowd out real content in a top-k search (docs/chunking.md section 1.4).
STOP_PHRASES = frozenset(
    {
        "invest now",
        "know more",
        "view detail",
        "view detail all",
        "view all",
        "read more",
        "table",
        "graph",
        "table graph",
        "disclaimer",
        "click here",
        "to view the total expense ratio",
        "no data available",
        "no result found!!",
        "no result found",
        "view more",
        "explore all mutual funds",
        "explore all funds",
        "complete your kyc",
        "login with mobile",
        "download our app",
        "start investing in mutual funds",
        "overseas",
        "all equity debt hybrid",
        "regular direct",
        "direct regular",
    }
)


@dataclass
class Chunk:
    """One retrievable unit: the embedded text plus its metadata."""

    id: str
    text: str  # what gets embedded and stored in chunks.txt
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def word_count(self) -> int:
        return len(self.text.split())


class ChunkDrop(TypedDict):
    reason: str
    text: str


def make_id(url: str, chunk_index: int) -> str:
    """Stable id from url + position (architecture section 5.4).

    Deliberately not a hash of the text: the AMC edits figures on these pages,
    and ids must survive that so a re-ingest overwrites instead of duplicating.
    """
    return hashlib.sha1(f"{url}#{chunk_index}".encode("utf-8")).hexdigest()[:16]


def _is_stop_phrase(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.strip().lower()).strip(" .:|")
    if normalized in STOP_PHRASES:
        return True
    # A chunk made entirely of repeated chrome tokens ("Very High Very High").
    words = normalized.split()
    return bool(words) and len(set(words)) <= 1 and len(words) > 1


def _is_key_fact_line(line: str) -> bool:
    """A "Label: value" row from the key-facts card; never split it."""
    match = re.match(r"^([A-Z][^:]{1,30}):\s*(.+)$", line.strip())
    return bool(match)


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(])", text)
    return [part for part in parts if part.strip()]


def _pack(lines: list[str], target: int = TARGET_WORDS) -> list[list[str]]:
    """Group lines into blocks of about `target` words, breaking on
    line, then sentence, then word so a fact is never cut mid-sentence."""
    blocks: list[list[str]] = []
    current: list[str] = []
    words = 0

    for line in lines:
        line_words = len(line.split())
        if line_words > MAX_WORDS:
            # One line longer than the ceiling: flush, then split on sentences.
            if current:
                blocks.append(current)
                current, words = [], 0
            pieces = _split_sentences(line)
            if all(len(piece.split()) <= MAX_WORDS for piece in pieces):
                blocks.append(pieces)
            else:
                blocks.append([line])
            continue

        if words + line_words > target and current:
            blocks.append(current)
            current, words = [], 0

        current.append(line)
        words += line_words

    if current:
        blocks.append(current)
    return blocks


def _overlap_tail(previous: list[str], overlap: int = OVERLAP_WORDS) -> list[str]:
    """Last `overlap` words of the previous block, re-aligned to line starts."""
    if not previous or overlap <= 0:
        return []
    tail: list[str] = []
    count = 0
    for line in reversed(previous):
        line_words = len(line.split())
        if count + line_words > overlap and tail:
            break
        tail.insert(0, line)
        count += line_words
    return tail if count < overlap else tail[1:]


def chunk_sections(
    url: str,
    title: str,
    sections: list[dict[str, Any]],
    scheme: str,
    fetched_at: str,
    *,
    min_words: int = MIN_WORDS,
) -> tuple[list[Chunk], list[ChunkDrop]]:
    """Build chunks for one page. Returns (kept, dropped)."""
    chunks: list[Chunk] = []
    dropped: list[ChunkDrop] = []
    index = 0

    for section in sections:
        heading = section.get("heading") or "Overview"
        lines = [line for line in section.get("lines", []) if line.strip()]
        if not lines:
            continue

        total_words = sum(len(line.split()) for line in lines)

        if total_words <= MAX_WORDS:
            blocks = [lines]
            needs_overlap = False
        else:
            blocks = _pack(lines)
            needs_overlap = True

        for position, block in enumerate(blocks):
            if needs_overlap and position > 0:
                block = _overlap_tail(blocks[position - 1]) + block

            body = "\n".join(block).strip()
            # The heading is prepended for embedding strength but stored once,
            # as metadata, so chunks.txt does not repeat it.
            text = f"{heading}\n{body}" if heading and heading != "Overview" else body

            if _is_stop_phrase(text) or _is_stop_phrase(body):
                dropped.append({"reason": "boilerplate", "text": body[:120]})
                continue
            if len(text.split()) < min_words and heading.strip().lower() not in FACT_HEADINGS:
                dropped.append({"reason": f"under {min_words} words", "text": body[:120]})
                continue

            chunks.append(
                Chunk(
                    id=make_id(url, index),
                    text=text,
                    metadata={
                        "url": url,
                        "title": title,
                        "scheme": scheme,
                        "fetched_at": fetched_at,
                        "heading": heading,
                        "chunk_index": index,
                    },
                )
            )
            index += 1

    return chunks, dropped


def chunk_document(
    doc: dict[str, Any],
    sections: list[dict[str, Any]],
    *,
    min_words: int = MIN_WORDS,
) -> tuple[list[Chunk], list[ChunkDrop]]:
    """Chunk one loaded SourceDocument using its extracted sections."""
    from app import config

    url = doc["url"]
    return chunk_sections(
        url=url,
        title=doc.get("title", ""),
        sections=sections,
        scheme=config.scheme_for(url),
        fetched_at=doc.get("fetched_at", ""),
        min_words=min_words,
    )


def format_chunks_txt(
    chunks: list[Chunk],
    *,
    corpus: list[str],
    generated_at: str,
    dropped: list[ChunkDrop] | None = None,
) -> str:
    """Human-readable dump for inspection (architecture section 5.5)."""
    lines = [
        "=" * 78,
        "CHUNK DUMP - HDFC Mutual Fund FAQ Assistant",
        "=" * 78,
        f"generated_at : {generated_at}",
        f"chunks       : {len(chunks)}",
        f"strategy     : heading-aware split, target {TARGET_WORDS} words, "
        f"max {MAX_WORDS}, overlap {OVERLAP_WORDS} (docs/chunking.md)",
        "",
        "corpus (docs/PRD.md section 4.2):",
    ]
    for url in corpus:
        lines.append(f"  - {url}")
    if dropped:
        reasons: dict[str, int] = {}
        for item in dropped:
            reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
        lines.append("")
        lines.append(f"dropped       : {len(dropped)}")
        for reason, count in sorted(reasons.items()):
            lines.append(f"  {count:4d}  {reason}")
    lines.append("=" * 78)

    for position, chunk in enumerate(chunks, start=1):
        lines.append("")
        lines.append(f"=== CHUNK {position} ===")
        lines.append(f"id: {chunk.id}")
        for key, value in chunk.metadata.items():
            lines.append(f"{key}: {value}")
        lines.append(f"words: {chunk.word_count}")
        lines.append("---")
        lines.append(chunk.text)

    lines.append("")
    lines.append("=" * 78)
    lines.append(f"END OF DUMP - {len(chunks)} chunks")
    lines.append("=" * 78)
    return "\n".join(lines) + "\n"


def write_chunks_txt(
    chunks: list[Chunk],
    path,
    *,
    corpus: list[str],
    generated_at: str,
    dropped: list[ChunkDrop] | None = None,
) -> None:
    path.write_text(
        format_chunks_txt(
            chunks, corpus=corpus, generated_at=generated_at, dropped=dropped
        ),
        encoding="utf-8",
    )


def _main() -> int:
    """Standalone: fetch, chunk, dump, print stats. Re-fetched on demand."""
    from datetime import datetime, timezone

    from app import config
    from app.loader import load_all, extract_sections, fetch_html

    docs = load_all()
    all_chunks: list[Chunk] = []
    all_dropped: list[ChunkDrop] = []

    for doc in docs:
        html = fetch_html(doc["url"])
        _, sections = extract_sections(html, doc["url"])
        chunks, dropped = chunk_document(doc, sections)
        all_chunks.extend(chunks)
        all_dropped.extend(dropped)
        print(
            f"{config.slug_for(doc['url'])[:46]:48s} "
            f"sections={len(sections):3d} chunks={len(chunks):3d} dropped={len(dropped):3d}"
        )

    write_chunks_txt(
        all_chunks,
        config.CHUNKS_TXT,
        corpus=config.APPROVED_URLS,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        dropped=all_dropped,
    )

    counts = sorted(chunk.word_count for chunk in all_chunks)
    if counts:
        median = counts[len(counts) // 2]
        print(
            f"\ntotal chunks={len(all_chunks)} dropped={len(all_dropped)}  "
            f"min/median/max words={counts[0]}/{median}/{counts[-1]}"
        )
        over = [c.word_count for c in all_chunks if c.word_count > MAX_WORDS]
        print(f"chunks above the {MAX_WORDS}-word ceiling: {len(over)}")
    print(f"wrote {config.CHUNKS_TXT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
