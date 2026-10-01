"""Phase 7: the single entry point the UI calls.

Runs the whole query pipeline in the order architecture section 7 specifies:

    guardrails.classify(original) -> (refuse and return) -> scrub_pii -> retrieve ->
    low-confidence check -> generate

Two ordering rules are load-bearing:

- **`classify` sees the ORIGINAL question, not the scrubbed one.** Scrubbing
  first is a trap: it replaces the PAN with `[redacted-pan]`, so the detector
  no longer finds it and the question sails through as an ordinary fact query
  with the personal data still attached to the user's intent. Detection must
  run on what the user actually typed; scrubbing is for what we pass onward.
- **`scrub_pii` runs before `retrieve`**, so an allowed question is embedded
  and sent to Groq in redacted form. Allowed questions are exactly the ones that
  reach the LLM, so this is where the redaction has to happen.
"""

from __future__ import annotations

from typing import Any

from app import generate
from app import guardrails
from app.retrieve import ChromaEmptyError, RetrievedChunk, best_url, retrieve


def _empty_sources() -> list[dict[str, Any]]:
    return []


def _refusal_response(decision: guardrails.GuardrailDecision) -> dict[str, Any]:
    """Architecture section 8.3 with refused=True and no LLM call.

    `last_updated_from_sources` is left empty on purpose: nothing was retrieved,
    so there is no source date to quote for a message we did not ground in one.
    """
    return {
        "answer": decision.message,
        "citation_url": decision.link,
        "last_updated_from_sources": "",
        "refused": True,
        "debug": {"intent": decision.intent, "pii_types": decision.pii_types},
    }


def _empty_corpus_response() -> dict[str, Any]:
    return {
        "answer": (
            "The corpus has not been ingested yet. Run: python -m app.ingest"
        ),
        "citation_url": None,
        "last_updated_from_sources": "",
        "refused": True,
        "debug": {"reason": "empty_corpus"},
    }


def answer_question(question: str) -> dict[str, Any]:
    """Answer one question. Never raises for a blocked question."""
    original = question or ""

    # Detect on the original text: scrub_pii would erase the very evidence
    # detect_pii looks for, and a PII-bearing question would be answered.
    decision = guardrails.classify(original)
    if not decision.allowed:
        return _refusal_response(decision)

    # Redact before the question is embedded, sent to Groq, or logged.
    safe_question = guardrails.scrub_pii(original)

    try:
        retrieved: list[RetrievedChunk] = retrieve(safe_question)
    except ChromaEmptyError:
        return _empty_corpus_response()

    return generate.generate_answer(safe_question, retrieved)


__all__ = ["answer_question"]
