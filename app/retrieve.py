"""Pipeline B, step 1: embed the question, retrieve the top-k chunks.

Reads only the persisted Chroma collection. This module never makes a network
call and never imports the loader (architecture section 6.3): the five approved
pages are fetched at ingest time, not per question.

A pure vector top-k is not enough for this corpus. Measured in
docs/chunking.md section 4.1: "What is the expense ratio of HDFC Flexi Cap
Fund?" ranks the TER chunk 7th, because the AMC writes "TER" and "Total Expense
Ration" and never "expense ratio" on the scheme page. The re-rank below
promotes chunks by scheme and by heading match. It promotes, never filters, so
a chunk the vector search found is always still reachable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app import config, store
from app.embedder import embed_query

# Query words that name a section on the page. "expense" maps to "ter" because
# the AMC's own label for the figure is TER.
#: question phrases -> heading key. These decide *what the user is asking about*.
_HEADING_KEYWORDS: dict[str, tuple[str, ...]] = {
    "exit load": ("exit load", "exit loads", "redemption charge"),
    "lock in": ("lock in", "lock-in", "lockin", "lock in period"),
    "min sip": ("minimum sip", "min sip", "sip amount", "small sip"),
    "riskometer": ("riskometer", "risk meter", "riskometer level", "risk level"),
    "benchmark": ("benchmark", "index"),
    "ter": ("expense ratio", "ter", "expense", "total expense ratio", "charges"),
    "aum": ("aum", "assets under management", "fund size"),
    "nav": ("nav", "net asset value"),
    "downloads": ("download", "sid", "kim", "scheme information document",
                  "key information memorandum", "factsheet", "leaflet"),
    "fund managers": ("fund manager", "who manages", "managed by"),
    "entry load": ("entry load",),
    "inception date": ("inception", "when did it start", "launched"),
    "product suitability": ("who should", "suitable for", "am i eligible"),
}

#: The AMC's own label for a fact, which is what appears in the chunk body.
#: A hit here is a definitive answer ("Total Expense Ration: 0.77%"), so it earns
#: the full body boost. This is what fixes the expense-ratio case: the scheme
#: pages never say "expense ratio", they say "TER" or "Total Expense Ration".
_FACT_LABELS: dict[str, tuple[str, ...]] = {
    "ter": ("total expense ration", "ter:", "ter ", "expense ratio", "expense ratio:"),
    "aum": ("aum", "assets under management"),
    "nav": ("nav", "net asset value"),
    "exit load": ("exit load",),
    "lock in": ("lock in", "lock-in"),
    "min sip": ("min sip", "minimum sip", "sip amount"),
    "riskometer": ("riskometer",),
    "benchmark": ("benchmark",),
    "fund managers": ("fund manager",),
    "inception date": ("inception date", "inception date:"),
    "entry load": ("entry load",),
}

_SCHEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "hdfc-flexi-cap": ("flexi cap", "flexicap", "hdfc flexi"),
    "hdfc-elss-tax-saver": ("elss", "tax saver", "80c", "hdfc elss"),
}

# Promotions, in cosine-distance units subtracted from the raw distance.
_SCHEME_BOOST = 0.35  # question names a scheme and the chunk's scheme agrees
_HEADING_BOOST = 0.30  # the section heading names the thing asked about
_BODY_BOOST = 0.55  # the chunk states the fact in the AMC's own label
_INCIDENTAL_BOOST = 0.12  # the phrase appears but is not a labelled fact

_WORD = re.compile(r"[a-z0-9]+")


class ChromaEmptyError(RuntimeError):
    """The collection is missing or empty. The UI must say "run ingest first"."""

    def __init__(self, message: str = "Corpus not ingested. Run: python -m app.ingest"):
        super().__init__(message)


@dataclass
class RetrievedChunk:
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    distance: float = 0.0
    score: float = 0.0

    @property
    def url(self) -> str:
        return self.metadata.get("url", "")

    @property
    def fetched_at(self) -> str:
        return self.metadata.get("fetched_at", "")

    @property
    def heading(self) -> str:
        return self.metadata.get("heading", "")

    @property
    def scheme(self) -> str:
        return self.metadata.get("scheme", "unknown")

    @property
    def rank_note(self) -> str:
        return self.metadata.get("rank_note", "")


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _contains(haystack: str, needle: str) -> bool:
    """Whole-word phrase match, with a tolerant trailing plural.

    Plain substring matching is unsafe: "ter" occurs inside "term", "later" and
    "alternative", and "nav" inside "navigate", so a question about a term
    length would be re-ranked as if it were about TER or NAV. Requiring whole
    words also breaks "download" against the "Downloads" heading, so the final
    word of the phrase may carry an "s".
    """
    hay_words = _words(haystack)
    needle_words = _words(needle)
    if not hay_words or not needle_words or len(needle_words) > len(hay_words):
        return False
    for start in range(len(hay_words) - len(needle_words) + 1):
        window = hay_words[start : start + len(needle_words)]
        if window[:-1] == needle_words[:-1] and window[-1] in (
            needle_words[-1],
            needle_words[-1] + "s",
        ):
            return True
    return False


#: Checked in this order, and the first key that matches wins, so the result does
#: not depend on set iteration order. Most specific first.
_HEADING_ORDER = (
    "exit load",
    "lock in",
    "min sip",
    "riskometer",
    "ter",
    "benchmark",
    "aum",
    "nav",
    "downloads",
    "fund managers",
    "entry load",
    "inception date",
    "product suitability",
)


def _matched_headings(question: str) -> list[str]:
    """Heading keys whose keywords appear in the question, in priority order."""
    lowered = question.lower()
    return [
        key
        for key in _HEADING_ORDER
        if any(_contains(lowered, phrase) for phrase in _HEADING_KEYWORDS[key])
    ]


def _matched_schemes(question: str) -> set[str]:
    lowered = question.lower()
    return {
        scheme
        for scheme, phrases in _SCHEME_KEYWORDS.items()
        if any(_contains(lowered, phrase) for phrase in phrases)
    }


def _rerank_score(chunk: RetrievedChunk, question: str) -> tuple[float, str]:
    """Return (boost, human-readable reason). Promotes, never filters."""
    boost = 0.0
    notes: list[str] = []

    if chunk.scheme in _matched_schemes(question):
        boost += _SCHEME_BOOST
        notes.append("scheme")

    matched = _matched_headings(question)
    if not matched:
        return boost, "+".join(notes)

    # Heading match is whole-phrase based: the page's heading may be the whole
    # FAQ question ("What is the minimum SIP amount in HDFC Flexi Cap Fund?")
    # rather than the bare keyword ("min sip"). Exact matching on the key-facts
    # card also fails, because that card sits under a marketing heading
    # ("How to start investing") that says nothing about the figure.
    heading = chunk.heading.strip().lower()
    body = chunk.text.lower()
    for key in matched:
        phrases = _HEADING_KEYWORDS[key]
        if any(_contains(heading, phrase) for phrase in phrases):
            boost += _HEADING_BOOST
            notes.append(f"heading:{key}")
            break

        labels = _FACT_LABELS.get(key, ())
        if any(_contains(body, label) for label in labels):
            # The chunk states the figure itself. Outranks a scheme-only match,
            # which matters when the fact lives on a different page (AUM is only
            # in the home-page fund card, the ELSS lock-in only on the ELSS page).
            boost += _BODY_BOOST
            notes.append(f"fact:{key}")
        elif any(_contains(body, phrase) for phrase in phrases):
            # The topic is mentioned but the chunk is not the labelled fact.
            boost += _INCIDENTAL_BOOST
            notes.append(f"text:{key}")
        else:
            continue
        break

    return boost, "+".join(notes)


def retrieve(question: str, top_k: int | None = None) -> list[RetrievedChunk]:
    """Embed the question, search Chroma, re-rank. Never hits the network."""
    top_k = top_k or config.TOP_K
    question = question.strip()
    if not question:
        return []

    if not store.is_populated():
        raise ChromaEmptyError()

    collection = store.get_collection()
    total = collection.count()
    # Score the whole collection, then take the best top_k after re-ranking.
    # The corpus is 63 chunks, one local HNSW search is sub-millisecond, and this
    # is what makes the re-rank reliable: a partial candidate set hides the
    # key-facts card (expense ratio, riskometer, AUM) and the ELSS
    # "Exit Load: NIL" chunk, none of which resemble the question wording.
    fetch_n = total
    result = collection.query(
        query_embeddings=[embed_query(question)],
        n_results=fetch_n,
        include=["documents", "metadatas", "distances"],
    )

    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]

    chunks: list[RetrievedChunk] = []
    for text, metadata, distance in zip(documents, metadatas, distances):
        chunk = RetrievedChunk(
            text=text,
            metadata=dict(metadata or {}),
            distance=float(distance),
        )
        boost, note = _rerank_score(chunk, question)
        chunk.score = chunk.distance - boost
        if note:
            chunk.metadata["rank_note"] = note
        chunks.append(chunk)

    chunks.sort(key=lambda item: item.score)
    return chunks[:top_k]


def context_block(retrieved: list[RetrievedChunk]) -> str:
    """Numbered context for the LLM prompt, each chunk tagged with its source
    and fetch date so the generator can cite exactly one URL (architecture 6.4)."""
    if not retrieved:
        return "(no context retrieved)"
    blocks: list[str] = []
    for position, chunk in enumerate(retrieved, start=1):
        blocks.append(
            f"[{position}] source: {chunk.url}  (source date: {chunk.fetched_at})\n"
            f"{chunk.text}"
        )
    return "\n\n".join(blocks)


def best_url(retrieved: list[RetrievedChunk]) -> str:
    """Exactly one citation URL: the top chunk's, if approved, else the first
    approved one. Never invented, never more than one (PRD FR-3)."""
    approved = set(config.APPROVED_URLS)
    for chunk in retrieved:
        if chunk.url in approved:
            return chunk.url
    return config.FACTSHEETS_URL if not retrieved else config.APPROVED_URLS[0]
