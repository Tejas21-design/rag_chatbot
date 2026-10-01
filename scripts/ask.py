"""Ask the bot a question, interactively or one-shot.

    python scripts/ask.py                                  # interactive REPL
    python scripts/ask.py "What is the expense ratio of HDFC Flexi Cap Fund?"
    python scripts/ask.py -q "..." --sources               # show the chunks used
    python scripts/ask.py -q "..." --json                  # raw response dict
    python scripts/ask.py --models                         # what your key can use
    python scripts/ask.py --suite                          # the five Gate 7 questions

This is the Phase 7 gate you would otherwise run by hand: the same
`answer_question` the Streamlit UI will call, so what you see here is exactly
what a user gets. No UI, no caching, no rate-limit hiding.

`--sources` is the one to reach for when an answer surprises you. It prints the
retrieved chunks with their raw distances, which is the only way to tell a
model failure (ignored a good context) from a retrieval failure (the right text
was never in the context).
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, generate, guardrails
from app.answer import answer_question
from app.generate import GroqUnavailableError

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, YELLOW, RED = "\033[32m", "\033[33m", "\033[31m"

SUITE = [
    ("What is the expense ratio of HDFC Flexi Cap Fund?", "in corpus"),
    ("What is the lock-in period for HDFC ELSS Tax Saver?", "in corpus"),
    ("How do I download a capital gains statement from HDFC Mutual Fund?", "not in corpus"),
    ("What is the expense ratio of HDFC Mid-Cap Fund?", "out of scope"),
    ("Should I buy HDFC Flexi Cap Fund right now?", "advice refusal"),
]


def _c(text: str, colour: str = "") -> str:
    return f"{colour}{text}{RESET}" if sys.stdout.isatty() and colour else text


def print_response(response: dict, *, sources: bool = False) -> None:
    """Print one response in the same shape the UI will use."""
    debug = response.get("debug") or {}
    body = response["answer"]

    if response["refused"]:
        kind = debug.get("intent") or debug.get("reason") or "refused"
        print(_c(f"  [{kind}]", YELLOW))
    print(textwrap.fill(body, width=88, initial_indent="  ", subsequent_indent="  "))

    if response.get("citation_url"):
        print(_c(f"  {DIM}source:{RESET} {response['citation_url']}"))
    if response.get("last_updated_from_sources"):
        print(_c(f"  {DIM}{response['last_updated_from_sources']}{RESET}"))

    if debug.get("ungrounded_numbers"):
        print(_c(f"  {DIM}dropped ungrounded figures: {debug['ungrounded_numbers']}{RESET}"))

    if sources and debug.get("chunks"):
        print(_c(f"\n  {DIM}retrieved {len(debug['chunks'])} chunk(s):{RESET}"))
        for i, chunk in enumerate(debug["chunks"], 1):
            print(
                _c(
                    f"    {i}. d={chunk['distance']:.3f}  {chunk.get('scheme') or '-'}  "
                    f"{chunk.get('rank_note') or ''}",
                    DIM,
                )
            )
            print(_c(f"       {chunk['url']}", DIM))
        if debug.get("chunks"):
            best = min(c["distance"] for c in debug["chunks"])
            print(
                _c(
                    f"    best raw distance {best:.3f} "
                    f"(low-confidence cut {generate.LOW_CONFIDENCE_DISTANCE})",
                    DIM,
                )
            )


def ask(question: str, *, sources: bool = False, as_json: bool = False) -> dict:
    started = time.perf_counter()
    try:
        response = answer_question(question)
    except GroqUnavailableError as exc:
        print(_c(f"\n  Groq error: {exc}\n", RED))
        return {}
    elapsed = time.perf_counter() - started

    if as_json:
        print(json.dumps(response, indent=2, default=str))
        return response

    decision = guardrails.classify(question)
    colour = YELLOW if response["refused"] else GREEN
    print(_c(f"\n  {DIM}q:{RESET} {question}"))
    print_response(response, sources=sources)
    body = response["answer"]
    sentences = body.count(".") if body else 0
    print(
        _c(
            f"  {DIM}{sentences} sentence(s), {len(body.split())} words, "
            f"intent={decision.intent}, {elapsed:.1f}s{RESET}",
            colour,
        )
    )
    return response


def list_models() -> int:
    try:
        from groq import Groq
    except ImportError:
        print("The 'groq' package is not installed. Run: pip install -r requirements.txt")
        return 1
    if not config.GROQ_API_KEY:
        print("GROQ_API_KEY is empty. Add it to .env at the project root.")
        return 1
    try:
        models = sorted(m.id for m in Groq(api_key=config.GROQ_API_KEY).models.list().data)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not list models: {exc}")
        return 1
    print(f"Configured: {config.GROQ_MODEL}\n")
    for model in models:
        mark = " <- in use" if model == config.GROQ_MODEL else ""
        print(f"  {model}{mark}")
    print(
        "\nOnly chat models can answer. The prompt-guard, whisper and orpheus\n"
        "entries are not general chat models. Change yours with GROQ_MODEL= in .env."
    )
    return 0


def run_suite() -> int:
    print(_c(f"{BOLD}Gate 7: the five questions{RESET}\n"))
    for i, (question, expectation) in enumerate(SUITE, 1):
        print(_c(f"{BOLD}[{i}/5] expected: {expectation}{RESET}"))
        ask(question, sources=True)
    print(_c(f"\n{BOLD}Done.{RESET} Judge each one against the 'expected' line."))
    return 0


def repl() -> int:
    print(_c(f"{BOLD}HDFC Mutual Fund FAQ assistant{RESET} {DIM}(Phase 7, no UI){RESET}"))
    print(
        _c(
            f"{DIM}Type a question. 'sources' shows the chunks used, 'json' dumps the\n"
            "raw dict, 'suite' runs the five Gate 7 questions, 'quit' to exit.{RESET}",
            DIM,
        )
    )
    # Fail fast and clearly rather than on the first question typed.
    if not config.GROQ_API_KEY:
        print(_c("\n  GROQ_API_KEY is empty. Add it to .env at the project root.\n", RED))
        return 1

    sources = False
    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        lowered = line.lower()
        if lowered in {"quit", "exit", ":q"}:
            return 0
        if lowered == "sources":
            sources = not sources
            print(f"  sources display {'on' if sources else 'off'}")
            continue
        if lowered == "json":
            try:
                pending = input("  question to dump as raw JSON> ").strip()
            except (EOFError, KeyboardInterrupt):
                return 0
            if pending:
                ask(pending, as_json=True)
            continue
        if lowered == "suite":
            run_suite()
            continue
        ask(line, sources=sources)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("question", nargs="?", help="question to ask; omit for interactive mode")
    parser.add_argument("-q", "--question", dest="question_flag", help="question to ask")
    parser.add_argument("--sources", action="store_true", help="show retrieved chunks and distances")
    parser.add_argument("--json", action="store_true", help="print the raw response dict")
    parser.add_argument("--suite", action="store_true", help="run the five Gate 7 questions")
    parser.add_argument("--models", action="store_true", help="list models this key can use")
    args = parser.parse_args()

    if args.models:
        return list_models()
    if args.suite:
        return run_suite()

    question = args.question_flag or args.question
    if question:
        if not config.GROQ_API_KEY:
            print("GROQ_API_KEY is empty. Add it to .env at the project root.")
            return 1
        ask(question, sources=args.sources, as_json=args.json)
        return 0
    return repl()


if __name__ == "__main__":
    sys.exit(main())
