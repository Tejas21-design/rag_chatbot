"""Phase 7 contract suite. Run: python scripts/test_generation.py

Verifies the output contract against a stubbed LLM, so it needs no API key and
no network. The stub is fed deliberately *bad* completions on purpose: a model
that behaves well proves nothing about whether the code, rather than the
model's cooperation, is what enforces ≤3 sentences, no URL in the body, and no
fabricated figure.

Live-model grounding is a separate check and is NOT covered here. Run
`python -m app.answer` with a real GROQ_API_KEY for that.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, generate, guardrails
from app.answer import answer_question
from app.retrieve import ChromaEmptyError, retrieve

failures: list[str] = []
count = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global count
    count += 1
    if condition:
        print(f"PASS  {label}")
    else:
        print(f"FAIL  {label}" + (f" -- {detail}" if detail else ""))
        failures.append(label)


def group(name: str) -> None:
    print(f"\n=== {name} ===")


# --- stub --------------------------------------------------------------------


class FakeCompletions:
    """Records every call so prompt contents can be asserted on."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    def create(self, model=None, temperature=None, max_tokens=None, messages=None):
        self.calls.append(
            {"model": model, "temperature": temperature, "max_tokens": max_tokens, "messages": messages}
        )
        msg = type("M", (), {"content": self.content})()
        return type("R", (), {"choices": [type("C", (), {"message": msg})()]})


def fake_client(content: str) -> tuple[object, FakeCompletions]:
    completions = FakeCompletions(content)
    client = type("C", (), {"chat": type("Ch", (), {"completions": completions})()})()
    return client, completions


# Deliberately badly behaved: two URLs, a markdown link, advice, five sentences,
# and a figure that is in the corpus so the number check is not what catches it.
NASTY = (
    "The TER is 0.77% and the fund is open ended. "
    "You should invest after reviewing https://leak.example.com/advice. "
    "See the [factsheet](https://hdfcfund.com/x) for details. "
    "Visit www.leak2.example.com. "
    "This is a fifth sentence."
)

ORIGINAL_CLIENT = generate._client
client, calls = fake_client(NASTY)
generate._client = lambda: client

# --- 1. post-processing against a bad completion -----------------------------

group("1. post-processing")
stripped = generate.strip_urls(NASTY)
check("no http survives", "http" not in stripped, stripped)
check("no bare www survives", "www." not in stripped, stripped)
check("markdown link text kept", "factsheet" in stripped, stripped)
check("no empty brackets", "[]" not in stripped and "()" not in stripped, stripped)
check("no stranded source label", not stripped.rstrip().endswith(("Source:", "See:")), stripped)
check("clean prose untouched", generate.strip_urls("The TER is 0.77%.") == "The TER is 0.77%.")
check("prose without a url is not mangled", generate.strip_urls("The TER is 0.77% from the factsheet.") == "The TER is 0.77% from the factsheet.")
check("label plus link removed together", "Per" not in generate.strip_urls("Per https://x.com, the TER is 0.77%."), generate.strip_urls("Per https://x.com, the TER is 0.77%."))
check("a link-only answer becomes empty", generate.strip_urls("Source: https://x.com") == "")

trimmed = generate.trim_sentences("A. B. C. D. E.")
check("trim keeps exactly 3 of 5", trimmed == "A. B. C.", trimmed)
check("trim keeps short text", generate.trim_sentences("One only.") == "One only.")
check("trim on empty is empty", generate.trim_sentences("   ") == "")
check("post-process order is strip then trim", len(generate._postprocess(NASTY).split(". ")) <= 3, generate._postprocess(NASTY))

# --- 2. the five Gate 7 questions --------------------------------------------

group("2. the five Gate 7 questions")
for question in [
    "What is the expense ratio of HDFC Flexi Cap Fund?",
    "What is the lock-in period for HDFC ELSS Tax Saver?",
    "How do I download a capital gains statement from HDFC Mutual Fund?",
    "What is the expense ratio of HDFC Mid-Cap Fund?",
    "Should I buy HDFC Flexi Cap Fund right now?",
]:
    r = answer_question(question)
    body = r["answer"]
    check(f"<=3 sentences: {question[:38]}", body.count(". ") + body.count(".") <= 3 or len(body) < 200, body[:70])
    check(f"no URL in body: {question[:38]}", "http" not in body and "www." not in body, body[:70])
    check(f"body is not empty: {question[:38]}", bool(body.strip()))
    allowed = set(config.APPROVED_URLS) | {config.FACTSHEETS_URL, config.EXPLORE_URL}
    if body:
        check(f"one citation: {question[:38]}", r["citation_url"] in allowed, str(r["citation_url"]))
    # Every response must agree with itself on whether it refused.
    check(f"refused flag is a bool: {question[:38]}", isinstance(r["refused"], bool))
    if r["refused"]:
        check(f"refusal cites nothing outside approved: {question[:38]}", (r["citation_url"] in allowed))

# --- 3. grounding -------------------------------------------------------------

group("3. grounding")


class GroundedStub(FakeCompletions):
    def __init__(self) -> None:
        super().__init__(generate.NOT_IN_PAGES)
        self.calls = []


grounded_client, grounded_calls = fake_client(generate.NOT_IN_PAGES)
generate._client = lambda: grounded_client
for question in [
    "What is the current NAV of HDFC Flexi Cap Fund?",
    "What is the manager fee of HDFC ELSS Tax Saver?",
    "How do I download a capital gains statement from HDFC Mutual Fund?",
]:
    r = answer_question(question)
    check(f"declines rather than guessing: {question[:38]}", r["answer"] == generate.NOT_IN_PAGES, r["answer"][:60])
    check(f"no invented figure: {question[:38]}", not any(ch.isdigit() for ch in r["answer"].replace("HDFC", "")), r["answer"][:60])

# --- 4. ungrounded-number backstop -------------------------------------------

group("4. ungrounded-number backstop")
generate._client = lambda: client
flexi = retrieve("What is the expense ratio of HDFC Flexi Cap Fund?")
check("0.77% is grounded in the flexi chunk", generate.ungrounded_numbers("The TER is 0.77%.", flexi) == [], str(generate.ungrounded_numbers("The TER is 0.77%.", flexi)))
check("0.77 % spaced is grounded", generate.ungrounded_numbers("The TER is 0.77 %.", flexi) == [])
# Indian digit grouping: the corpus writes both "2169.07" and "113,606.47",
# while the model reliably regroups. Both spellings must count as grounded.
# The figures are read from the retrieved chunks rather than hardcoded, because
# the live pages change and a stale literal turns this into a false failure.
nav = retrieve("What is the current NAV of HDFC Flexi Cap Fund?")
nav_context = " ".join(c.text for c in nav)
nav_figure = next(
    (m for m in generate._NUMBER.findall(nav_context) if "." in m and len(m.split(".")[0]) == 4),
    None,
)
check("found a NAV figure in the corpus to test against", nav_figure is not None, nav_context[:120])
if nav_figure:
    check(f"{nav_figure} is grounded exactly", generate.ungrounded_numbers(f"The NAV is {nav_figure}.", nav) == [])
    whole, frac = nav_figure.split(".")
    grouped = f"{int(whole):,}.{frac}"
    check(f"{grouped} is grounded against {nav_figure}", generate.ungrounded_numbers(f"The NAV is {grouped}.", nav) == [], str(generate.ungrounded_numbers(f"The NAV is {grouped}.", nav)))
check("113,606.47 is grounded", generate.ungrounded_numbers("AUM is 113,606.47 Cr.", nav) == [])
check("a regrouped but absent figure is still flagged", generate.ungrounded_numbers("The NAV is 299999.99.", nav) == ["299999.99"], str(generate.ungrounded_numbers("The NAV is 299999.99.", nav)))
check("0.68% is flagged", generate.ungrounded_numbers("The TER is 0.68%.", flexi) == ["0.68"], str(generate.ungrounded_numbers("The TER is 0.68%.", flexi)))
check("250 is flagged", generate.ungrounded_numbers("Min SIP is 250.", flexi) == ["250"])
check("100 is grounded", generate.ungrounded_numbers("Min SIP is 100.", flexi) == [])
check("prose flags nothing", generate.ungrounded_numbers("The scheme is open-ended.", flexi) == [])

original_call = generate._call_groq
generate._call_groq = lambda q, c: "The expense ratio of HDFC Flexi Cap Fund is 0.68%."
lying = generate.generate_answer("What is the expense ratio of HDFC Flexi Cap Fund?", flexi)
generate._call_groq = original_call
check("lying answer demoted to not-in-pages", lying["answer"] == generate.NOT_IN_PAGES, lying["answer"][:60])
check("demotion reason recorded", lying["debug"].get("reason") == "ungrounded_number", str(lying["debug"].get("reason")))
check("demotion names the figure", lying["debug"].get("ungrounded_numbers") == ["0.68"], str(lying["debug"].get("ungrounded_numbers")))
check("demotion still cites an approved URL", lying["citation_url"] in set(config.APPROVED_URLS))

# --- 5. low confidence --------------------------------------------------------

group("5. low confidence")
check("empty retrieval is low confidence", generate.is_low_confidence([]) is True)
far = [type("C", (), {"distance": 1.5})()]
check("distance 1.5 is low confidence", generate.is_low_confidence(far) is True)
near = [type("C", (), {"distance": 0.157})()]
check("distance 0.157 is confident", generate.is_low_confidence(near) is False)
check("threshold is 1.0", generate.LOW_CONFIDENCE_DISTANCE == 1.0, str(generate.LOW_CONFIDENCE_DISTANCE))

low_client, low_calls = fake_client(NASTY)
generate._client = lambda: low_client
far_chunks = [type("C", (), {"distance": 1.5, "url": config.APPROVED_URLS[0], "fetched_at": None, "heading": "h", "scheme": "s", "rank_note": "n", "text": "t", "score": 0.0})()]
r = generate.generate_answer("unrelated question", far_chunks)
check("low confidence skips the LLM", low_calls.calls == [], str(len(low_calls.calls)))
check("low confidence says not in pages", r["answer"] == generate.NOT_IN_PAGES)
check("low confidence still cites a page", r["citation_url"] == config.APPROVED_URLS[0], str(r["citation_url"]))

# --- 6. refusals are inert ----------------------------------------------------

group("6. refusals never reach the LLM")
refuse_client, refuse_calls = fake_client(NASTY)
generate._client = lambda: refuse_client
for question in [
    "Should I buy HDFC Flexi Cap?",
    "my PAN is ABCDE1234F",
    "which fund gave better returns",
    "tell me about SBI Liquid Fund",
    "What is the expense ratio of HDFC Mid-Cap Fund?",
]:
    r = answer_question(question)
    check(f"refused: {question[:38]}", r["refused"] is True, str(r["refused"]))
check("zero LLM calls for 5 refusals", refuse_calls.calls == [], f"{len(refuse_calls.calls)} calls")

# PII must not reach the prompt.
answer_question("my PAN is ABCDE1234F, should I buy HDFC Flexi Cap?")
check("PII question makes no LLM call", refuse_calls.calls == [])
allowed_r = answer_question("What is the expense ratio of HDFC Flexi Cap Fund?")
check("allowed question reaches the LLM", len(refuse_calls.calls) == 1, str(len(refuse_calls.calls)))
prompt = str(refuse_calls.calls[0]["messages"])
check("prompt carries the context block", "CONTEXT" in prompt)
check("prompt has no API key", (config.GROQ_API_KEY or "none") not in prompt)
check("temperature is 0", refuse_calls.calls[0]["temperature"] == 0)
check("max_tokens is 300", refuse_calls.calls[0]["max_tokens"] == 300)
check("model from config", refuse_calls.calls[0]["model"] == config.GROQ_MODEL)

# --- 7. failures are typed, never a fallback ---------------------------------

group("7. Groq failure is typed")
generate._client = generate._client
try:
    generate._client.cache_clear()
except AttributeError:
    pass


class Boom(FakeCompletions):
    def __init__(self) -> None:
        super().__init__(NASTY)

    def create(self, **kwargs):
        raise RuntimeError("401 Unauthorized")


boom = type("C", (), {"chat": type("Ch", (), {"completions": Boom()})()})()
generate._client = lambda: boom
try:
    generate.generate_answer("What is the expense ratio of HDFC Flexi Cap Fund?", flexi)
    check("raises GroqUnavailableError", False, "no exception")
except generate.GroqUnavailableError as exc:
    check("raises GroqUnavailableError", "401" in str(exc), str(exc))
except Exception as exc:  # noqa: BLE001
    check("raises GroqUnavailableError", False, f"{type(exc).__name__}: {exc}")

empty_client, _ = fake_client("")
generate._client = lambda: empty_client
r = generate.generate_answer("What is the expense ratio of HDFC Flexi Cap Fund?", flexi)
check("empty completion -> not in pages, not blank", r["answer"] == generate.NOT_IN_PAGES, r["answer"][:50])

generate._client = ORIGINAL_CLIENT

# --- 8. the source date ------------------------------------------------------

group("8. source date")
# Back on the bad-completion stub: the date must come from the manifest even
# when the body is post-processed, and without a key the real client raises.
generate._client = lambda: client
r = answer_question("What is the expense ratio of HDFC Flexi Cap Fund?")
stamp = r["last_updated_from_sources"]
check("date is present", bool(stamp), str(stamp))
check("date is human formatted", not stamp.isdigit() and "T" not in stamp, stamp)
check("date comes from the manifest", stamp.startswith("Last updated from sources:"), stamp)
refused = answer_question("Should I buy HDFC Flexi Cap Fund right now?")
check("a refusal carries no source date", not refused["last_updated_from_sources"], str(refused["last_updated_from_sources"]))

# --- 9. import hygiene --------------------------------------------------------

group("9. import hygiene")
import re  # noqa: E402

for module in ("generate", "answer"):
    src = Path(__file__).resolve().parent.parent / "app" / f"{module}.py"
    imports = set(re.findall(r"^(?:from|import)\s+([\w.]+)", src.read_text(), re.MULTILINE))
    check(
        f"{module}.py imports no loader/chunker/ingest/httpx",
        not ({"app.loader", "app.chunker", "app.ingest", "httpx", "requests"} & imports),
        str(sorted(imports)),
    )

check("ChromaEmptyError still handled by answer", "ChromaEmptyError" in Path(__file__).resolve().parent.parent.joinpath("app", "answer.py").read_text())

print(f"\n{count - len(failures)}/{count} passed, {len(failures)} failure(s)")
if failures:
    print("\nFailed:")
    for f in failures:
        print(f"  - {f}")
sys.exit(1 if failures else 0)
