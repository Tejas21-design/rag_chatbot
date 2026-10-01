"""Phase 7: grounded generation with Groq, plus the strict output contract.

Architecture section 6.4 / PRD FR-3. The contract this module enforces, in order:

1. **The context is the only knowledge source.** If the retrieved chunks do not
   contain the answer, the honest reply is that it is not in the official pages.
2. **Exactly one citation URL**, taken from chunk metadata via `best_url`, never
   from the model. Anything the model wrote in the body is stripped.
3. **At most 3 sentences**, enforced after generation, not merely requested.
4. **`last_updated_from_sources` comes from the ingest manifest**, never from the
   model's sense of time.
5. **No ungrounded fallback.** A missing key or an API error raises
   `GroqUnavailableError`; there is no path here that answers from memory.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from functools import lru_cache
from typing import Any

from app import config
from app.retrieve import RetrievedChunk, best_url, context_block

#: Cosine distance above which a match is not trusted. Measured against the real
#: corpus: the weakest honest hit ("capital gains statement", d=0.627) and the
#: strongest wrong-page hit ("ELSS vs regular ELSS", d=0.736) both sit below this,
#: while the best in-corpus hit is d=0.157. A cut at 1.0 therefore lets real
#: answers through and never fires on this corpus; it is a floor for a bad
#: query, not a tuning knob. See docs/chunking.md section 4.2.
LOW_CONFIDENCE_DISTANCE = 1.0

SYSTEM_PROMPT = """You are a facts-only assistant for HDFC Mutual Fund scheme pages.

Rules:
- Answer ONLY from the CONTEXT below. The context is the entire knowledge source.
- Use at most 3 sentences. No bulleted lists, no markdown headings, no tables.
- Never give investment advice, opinions, recommendations, or "you should" statements.
- Never calculate, compare, rank, or project returns, CAGR, or profit.
- Never state a number that does not appear verbatim in the CONTEXT.
- If the CONTEXT does not contain the answer, reply that the information is not
  in the official HDFC Mutual Fund pages. Do not guess, and do not fill the gap
  from general knowledge.
- Do not include any URL, link, or source name in your answer. The citation is
  added by the application.
- Answer in plain factual prose."""

NOT_IN_PAGES = (
    "The ingested HDFC Mutual Fund pages do not contain this information."
)

#: The model declining is not the same as it answering. When it says the pages
#: lack the information, it retrieved *something* but the top chunk is not a
#: source for the claim "this is absent" -- citing it sends the user to a page
#: that never contained the answer and reads as if it did.
_DECLINE = re.compile(
    r"not (?:in|provided in|available in|contained in|mentioned in)\b"
    r"|do(?:es)? not contain\b"
    r"|do(?:es)? not (?:have|include|provide)\b"
    r"|is not provided\b",
    re.IGNORECASE,
)


def _is_decline(text: str) -> bool:
    """True when the answer says the corpus lacks the information."""
    return bool(_DECLINE.search(text))

_URL_IN_TEXT = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")

#: A source label immediately followed by a URL. Removing only the URL strands
#: the label mid-sentence ("Per https://x, the TER is 0.77%." becomes "Per the
#: TER is 0.77%."), so the label goes with the link.
_LABEL_WITH_URL = re.compile(
    r"\b(?:source|sources|reference|refer|see|refer to|from|at|per)\b\s*[:\-]?\s*"
    r"(?:https?://\S+|www\.\S+)",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


class GroqUnavailableError(RuntimeError):
    """Missing key, or the API call failed. Never fall back to other knowledge."""


def read_ingest_manifest() -> dict[str, Any] | None:
    """data/ingest_manifest.json, or None when it has not been written yet."""
    if not config.INGEST_MANIFEST.exists():
        return None
    try:
        return json.loads(config.INGEST_MANIFEST.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def format_source_date(value: str | None) -> str:
    """ISO-8601 -> "30 Sep 2026". Falls back to the raw string if unparseable."""
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).strftime("%-d %b %Y")
    except ValueError:
        return value


def last_updated_from_sources(retrieved: list[RetrievedChunk]) -> str:
    """Prefer the manifest's max_fetched_at (the newest source), else the top
    chunk's fetched_at. Never the model's training cutoff (PRD FR-3)."""
    manifest = read_ingest_manifest()
    stamp = None
    if manifest:
        stamp = manifest.get("max_fetched_at")
    if not stamp and retrieved:
        stamp = retrieved[0].fetched_at
    formatted = format_source_date(stamp)
    return f"Last updated from sources: {formatted}" if formatted else ""


def is_low_confidence(retrieved: list[RetrievedChunk]) -> bool:
    """True when nothing retrieved is close enough to answer from.

    Uses the best *raw* distance, not the re-ranked score: the re-rank adds
    keyword boosts and must not be able to talk a weak match into confidence.
    """
    if not retrieved:
        return True
    return min(chunk.distance for chunk in retrieved) > LOW_CONFIDENCE_DISTANCE


#: A source label with nothing usable after it. Stripping a URL can leave the
#: lead-in behind ("Source: https://..." -> "Source:"), and a dangling label
#: reads as a broken citation when the real one is shown underneath.
_DANGLING_LABEL = re.compile(
    r"(?:\b(?:source|sources|reference|refer|see|refer to|from|at|per)\b\s*[:\-]?\s*)"
    r"(?=[.;,]|$)",
    re.IGNORECASE,
)


def strip_urls(text: str) -> str:
    """Remove URLs the model emitted. The citation comes from best_url()."""
    cleaned = _MD_LINK.sub(r"\1", text)
    # Label-plus-link first, so the label is not stranded by the URL removal.
    cleaned = _LABEL_WITH_URL.sub("", cleaned)
    cleaned = _URL_IN_TEXT.sub("", cleaned)
    # Tidy the punctuation left behind by the removal.
    cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned)
    cleaned = re.sub(r"(?:\(\s*\)|\[\s*\])", "", cleaned)
    # Then drop a label that has nothing left to point at, in either order: a
    # sentence that was only a link, and a link stripped from mid-sentence.
    cleaned = _DANGLING_LABEL.sub("", cleaned)
    cleaned = re.sub(r"\s*([.;,])\s*(?=[.;,]|$)", r"\1", cleaned)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def trim_sentences(text: str, limit: int = 3) -> str:
    """Hard cap at `limit` sentences. Enforced after generation, not requested."""
    cleaned = text.strip()
    if not cleaned:
        return ""
    parts = [part.strip() for part in _SENTENCE_SPLIT.split(cleaned) if part.strip()]
    return " ".join(parts[:limit])


def _postprocess(text: str) -> str:
    """URL stripping before trimming, so a stripped URL cannot leave a fragment
    that would otherwise be counted as a sentence."""
    return trim_sentences(strip_urls(text))


@lru_cache(maxsize=1)
def _client():
    if not config.GROQ_API_KEY:
        raise GroqUnavailableError(
            "GROQ_API_KEY is empty. Add your key to GROQ_API_KEY= in .env "
            "at the project root, then restart."
        )
    try:
        from groq import Groq
    except ImportError as exc:  # pragma: no cover - dependency is in requirements
        raise GroqUnavailableError("The 'groq' package is not installed.") from exc
    return Groq(api_key=config.GROQ_API_KEY)


def _call_groq(question: str, context: str) -> str:
    client = _client()
    try:
        completion = client.chat.completions.create(
            model=config.GROQ_MODEL,
            temperature=0,
            max_tokens=300,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"CONTEXT:\n{context}\n\nQUESTION: {question}"},
            ],
        )
    except Exception as exc:  # noqa: BLE001 - any SDK failure must surface as typed
        raise GroqUnavailableError(f"Groq request failed: {exc}") from exc

    content = completion.choices[0].message.content
    if not content or not content.strip():
        # An empty completion is a degradation, not a reason to answer from
        # anything else. Fall through to the honest not-in-pages path, which is
        # strictly better than surfacing a blank answer to the user.
        return ""
    return content


#: A figure, with the percent sign kept separate so "0.77" in the corpus and
#: "0.77%" from the model compare equal. The chunker keeps source line breaks, so
#: the corpus can write "0.77\n%" for what the model writes "0.77%".
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WHITESPACE = re.compile(r"\s+")
#: Indian numbering groups digits in thousands: 2,181.07. The corpus is
#: inconsistent about this, writing some figures as "2181.07" and others as
#: "113,606.47", while a model reliably regroups them by locale. Comparing the
#: digit string alone makes both spellings of one figure match.
_GROUPING = re.compile(r",")


def _normalise_numbers(text: str) -> set[str]:
    """Every number in `text`, compared on digits alone.

    Commas and whitespace are dropped, so "2,181.07" and "2181.07" are the same
    figure. Keeping the separators would let a correctly-grounded answer be
    demoted as fabricated purely over digit grouping.
    """
    return {_GROUPING.sub("", _WHITESPACE.sub("", match)) for match in _NUMBER.findall(text)}


def ungrounded_numbers(answer: str, retrieved: list[RetrievedChunk]) -> list[str]:
    """Numbers in the answer that do not appear in the retrieved context.

    A backstop, not the primary defence. The system prompt already forbids
    inventing figures, but "0.77%" is the one string a model is most likely to
    carry over from its training data onto a plausible-sounding question, and a
    confident wrong number is worse than a refusal. A grounded figure is kept;
    an ungrounded one demotes the whole answer to the not-in-pages path.
    """
    context_numbers = _normalise_numbers(" ".join(chunk.text for chunk in retrieved))
    return [
        number
        for number in _normalise_numbers(answer)
        if number not in context_numbers
    ]


def _not_in_pages_response(
    question: str, retrieved: list[RetrievedChunk]
) -> dict[str, Any]:
    """The honest path: no LLM call, no invented number, one approved citation."""
    return {
        "answer": NOT_IN_PAGES,
        "citation_url": best_url(retrieved) if retrieved else config.FACTSHEETS_URL,
        "last_updated_from_sources": last_updated_from_sources(retrieved),
        "refused": True,
        "debug": {
            "reason": "low_confidence",
            "best_distance": round(min((c.distance for c in retrieved), default=None) or 0.0, 4),
            "chunks": [
                {
                    "url": c.url,
                    "heading": c.heading,
                    "scheme": c.scheme,
                    "fetched_at": c.fetched_at,
                    "distance": round(c.distance, 4),
                    "rank_note": c.rank_note,
                }
                for c in retrieved
            ],
        },
    }


def generate_answer(
    question: str, retrieved: list[RetrievedChunk]
) -> dict[str, Any]:
    """Produce the architecture section 8.3 response dict."""
    if is_low_confidence(retrieved):
        return _not_in_pages_response(question, retrieved)

    answer = _postprocess(_call_groq(question, context_block(retrieved)))
    if not answer:
        return _not_in_pages_response(question, retrieved)

    if _is_decline(answer):
        # Honest decline after real retrieval. Keep the model's own wording, but
        # do not attribute the claim to whichever chunk happened to rank first.
        return {
            "answer": answer,
            "citation_url": config.FACTSHEETS_URL,
            "last_updated_from_sources": "",
            "refused": False,
            "debug": {
                "model": config.GROQ_MODEL,
                "reason": "model_declined",
                "chunks": [
                    {
                        "url": c.url,
                        "heading": c.heading,
                        "scheme": c.scheme,
                        "fetched_at": c.fetched_at,
                        "distance": round(c.distance, 4),
                        "rank_note": c.rank_note,
                    }
                    for c in retrieved
                ],
            },
        }

    orphans = ungrounded_numbers(answer, retrieved)
    if orphans:
        # A figure that is not in the context is a fabricated figure, whatever
        # the model intended. Do not show it, and do not try to repair it.
        response = _not_in_pages_response(question, retrieved)
        response["debug"]["reason"] = "ungrounded_number"
        response["debug"]["ungrounded_numbers"] = orphans
        return response

    return {
        "answer": answer,
        # From chunk metadata, never from the model (architecture section 6.4).
        "citation_url": best_url(retrieved),
        "last_updated_from_sources": last_updated_from_sources(retrieved),
        "refused": False,
        "debug": {
            "model": config.GROQ_MODEL,
            "chunks": [
                {
                    "url": c.url,
                    "heading": c.heading,
                    "scheme": c.scheme,
                    "fetched_at": c.fetched_at,
                    "distance": round(c.distance, 4),
                    "rank_note": c.rank_note,
                }
                for c in retrieved
            ],
        },
    }
