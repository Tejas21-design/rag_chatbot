"""Test Phase 5 retrieval against the persisted Chroma collection.

Runs no LLM call, so it exercises embedding, the HNSW search, the Phase 5
re-rank and the citation helpers without needing GROQ_API_KEY.

Usage:
    python scripts/test_retrieval.py                          # the standard set
    python scripts/test_retrieval.py --all                    # the standard set + extras
    python scripts/test_retrieval.py -q "What is the TER?"     # one question
    python scripts/test_retrieval.py -q "..." -k 6 --context   # wider, with context
    python scripts/test_retrieval.py --no-rerank              # show the raw vector order
    python scripts/test_retrieval.py --all --json             # machine-readable

The corpus must already be ingested (`python -m app.ingest`); this script never
fetches a page. If the collection is empty it says so instead of failing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, store  # noqa: E402
from app.retrieve import (  # noqa: E402
    ChromaEmptyError,
    best_url,
    context_block,
    retrieve,
)

#: The Phase 5 acceptance set, plus the Phase 4 baseline for comparison. These
#: are the questions the re-rank was built for; see docs/chunking.md section 4.2.
STANDARD = [
    ("What is the expense ratio of HDFC Flexi Cap Fund?", "TER on the flexi-cap card"),
    ("What is the lock-in period for HDFC ELSS Tax Saver?", "3 years, ELSS page"),
    ("What is the minimum SIP for HDFC Flexi Cap?", "Min SIP FAQ, flexi-cap page"),
    ("What is the exit load on HDFC ELSS Tax Saver?", "the NIL chunk"),
    ("What is the riskometer of HDFC Flexi Cap Fund?", "Very High, key-facts card"),
    ("What benchmark does HDFC ELSS Tax Saver use?", "benchmark name, ELSS page"),
    ("Who manages HDFC Flexi Cap Fund?", "fund manager, flexi-cap page"),
    ("How do I download the SID or KIM for HDFC Flexi Cap?", "Downloads section"),
    ("What is the AUM of HDFC Flexi Cap Fund?", "AUM, home-page fund card"),
    ("How do I download a capital gains statement?", "weakest expected hit"),
]

EXTRA = [
    ("What is the NAV of HDFC Flexi Cap Fund?", ""),
    ("When was HDFC Flexi Cap Fund launched?", "inception date"),
    ("Is HDFC Flexi Cap suitable for long-term investment?", "product suitability"),
    ("What is the entry load on HDFC Flexi Cap Fund?", ""),
    ("What is the ideal investment horizon for HDFC Flexi Cap?", ""),
    ("What is the investment strategy of HDFC Flexi Cap Fund?", ""),
    ("How do I start an SIP in HDFC Flexi Cap?", "min SIP"),
    ("What are the exit load slabs after 1 year for HDFC Flexi Cap?", ""),
    ("Who is eligible to invest in HDFC ELSS Tax Saver?", "80C eligibility"),
    ("What is the difference between ELSS and a regular ELSS?", ""),
]


def _format(chunk, position: int, no_rerank: bool = False) -> str:
    label = chunk.url.rsplit("/", 2)[-2] if "/" in chunk.url[8:] else "home"
    if no_rerank:
        # Show only the raw distance: printing the boosted score here would
        # misreport how the chunks were ordered.
        note = f"  [{chunk.rank_note} ignored]" if chunk.rank_note else ""
        print(f"  {position}. dist {chunk.distance:.3f}  [{label}] {chunk.heading[:34]}{note}")
    else:
        note = f"  <== {chunk.rank_note}" if chunk.rank_note else ""
        print(
            f"  {position}. score {chunk.score:+.3f}  dist {chunk.distance:.3f}  "
            f"[{label}] {chunk.heading[:34]}{note}"
        )
    body = chunk.text.replace("\n", " ")
    print(f"      {body[:96]}{'...' if len(body) > 96 else ''}")
    return label


def run_one(question: str, top_k: int, show_context: bool, no_rerank: bool) -> dict:
    print("=" * 78)
    print(f"Q: {question}")
    try:
        results = retrieve(question, top_k=top_k)
    except ChromaEmptyError as exc:
        print(f"  !! {exc}")
        return {"question": question, "error": str(exc), "results": []}

    if not results:
        print("  (no results)")
        return {"question": question, "results": []}

    if no_rerank:
        print("  (--no-rerank: showing the raw vector order, boosts not applied)")
        results = sorted(results, key=lambda c: c.distance)

    labels = [_format(chunk, i, no_rerank) for i, chunk in enumerate(results, start=1)]
    citation = best_url(results)
    print(f"  cite: {citation}")
    if show_context:
        print("\n" + context_block(results[:2]))

    return {
        "question": question,
        "results": [
            {
                "rank": i,
                "score": round(chunk.score, 4),
                "distance": round(chunk.distance, 4),
                "scheme": chunk.scheme,
                "heading": chunk.heading,
                "url": chunk.url,
                "fetched_at": chunk.fetched_at,
                "rank_note": chunk.rank_note,
                "approved": chunk.url in config.APPROVED_URLS,
            }
            for i, chunk in enumerate(results, start=1)
        ],
        "citation_url": citation,
        "top_scheme": labels[0] if labels else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-q", "--question", help="ask a single question")
    parser.add_argument("-k", "--top-k", type=int, default=config.TOP_K, help=f"chunks to return (default {config.TOP_K})")
    parser.add_argument("--all", action="store_true", help="include the extra probe questions")
    parser.add_argument("--context", action="store_true", help="also print the Phase 7 prompt context block")
    parser.add_argument("--no-rerank", action="store_true", help="show the raw vector order instead of the re-ranked order")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = parser.parse_args()

    if not store.is_populated():
        print("Corpus is empty. Run: python -m app.ingest", file=sys.stderr)
        return 1

    collection = store.get_collection()
    if not args.json:
        print(f"collection {collection.name}: {collection.count()} chunks | top_k={args.top_k}")

    if args.question:
        payload = [run_one(args.question, args.top_k, args.context, args.no_rerank)]
    else:
        questions = STANDARD + (EXTRA if args.all else [])
        if not args.json:
            print(f"running {len(questions)} questions\n")
        payload = [run_one(q, args.top_k, args.context, args.no_rerank) for q, _ in questions]

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        ok = [r for r in payload if r.get("results") and all(x["approved"] for x in r["results"])]
        print("=" * 78)
        print(f"{len(ok)}/{len(payload)} questions returned approved-URL results only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
