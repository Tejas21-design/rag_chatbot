"""Guardrail regression suite. Run: python scripts/test_guardrails.py

Lives in the repo on purpose. A guardrail bug is silent by nature: a question
that should be refused is answered instead, and nothing in the output looks
wrong. `test_retrieval.py` covers Phase 5 and this covers Phase 6, so a change
to either is checked without a Groq key or a network call.

Two groups matter most. PII detection is the only thing standing between a
pasted PAN and an outbound API request, and out-of-scope detection is the only
thing stopping a question about a scheme that was never ingested from being
answered off a neighbouring page.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, guardrails as g

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


# --- 1. PII detection --------------------------------------------------------

group("1. PII detection")
for label, text, expected in [
    ("PAN", "my PAN is ABCDE1234F", ["pan"]),
    ("PAN lower in sentence", "Please check ABCDE1234F for me", ["pan"]),
    ("phone bare", "call me on 9876543210", ["phone"]),
    ("phone +91 spaced", "my number is +91 98765 43210", ["phone"]),
    ("phone hyphenated", "my number is 98765-43210", ["phone"]),
    ("phone with trunk 0", "09876543210", ["phone"]),
    ("email", "mail me at tejas@gmail.com", ["email"]),
    ("OTP word", "what is my otp", ["otp"]),
    ("passcode word", "give me the passcode", ["otp"]),
    ("Aadhaar context", "my aadhaar is 1234 5678 9012", ["aadhaar"]),
    ("Aadhaar all-same", "aadhaar 9999 9999 9999", ["aadhaar"]),
    ("account context", "my folio number 12345678901", ["account"]),
]:
    check(f"detects {label}", g.detect_pii(text) == expected, f"{text!r} -> {g.detect_pii(text)}")

# Ordinary questions must not be refused as PII. A false positive here is just
# as bad as a false negative: the user gets a refusal instead of an answer.
for label, text in [
    ("normal question", "What is the expense ratio of HDFC Flexi Cap Fund?"),
    ("exit load question", "What is the exit load on HDFC ELSS Tax Saver?"),
    ("short number", "Is 3 years the lock-in for ELSS?"),
    ("scheme with digits", "HDFC Flexi Cap Fund Direct Growth"),
]:
    check(f"no false PII on {label}", g.detect_pii(text) == [], f"{g.detect_pii(text)}")

# --- 2. scrubbing actually redacts what was detected -------------------------

group("2. scrubbing")
check("PAN is redacted", "ABCDE1234F" not in g.scrub_pii("my PAN is ABCDE1234F"))
check("phone is redacted", "9876543210" not in g.scrub_pii("call me on 9876543210"))
check("email is redacted", "tejas@gmail.com" not in g.scrub_pii("mail me at tejas@gmail.com"))
check("scrub keeps the question", "expense ratio" in g.scrub_pii("what is the expense ratio?"))
check(
    "detect and scrub agree on a PAN question",
    g.detect_pii("my PAN is ABCDE1234F") == ["pan"]
    and "ABCDE1234F" not in g.scrub_pii("my PAN is ABCDE1234F"),
)

# --- 3. refusal messages carry no PII ----------------------------------------

group("3. refusal messages are inert")
decision = g.classify("my PAN is ABCDE1234F")
check("PAN question refused", decision.allowed is False and decision.intent == "pii")
check("message does not echo the PAN", "ABCDE1234F" not in decision.message)
check("PAN question has no link", decision.link is None)
for label, text, intent in [
    ("phone", "my number is 9876543210", "pii"),
    ("email", "mail me at tejas@gmail.com", "pii"),
]:
    d = g.classify(text)
    check(f"{label} refused as pii", d.intent == intent and not d.allowed)

# --- 4. advice, performance, out of scope ------------------------------------

group("4. intent classification")
for label, text, intent in [
    ("advice: should i buy", "Should I buy HDFC Flexi Cap Fund right now?", "advice"),
    ("advice: can i invest", "Can I invest in this fund?", "advice"),
    ("advice: recommend", "which fund should I recommend", "advice"),
    ("advice: portfolio", "how should I rebalance my portfolio", "advice"),
    ("advice: allocation", "what asset allocation should I keep", "advice"),
    ("performance: cagr", "what is the 5 year CAGR", "performance"),
    ("performance: returns", "which fund gave better returns", "performance"),
    ("performance: profit", "how much profit will I make", "performance"),
    ("out_of_scope: other AMC", "tell me about SBI Liquid Fund", "out_of_scope"),
    ("out_of_scope: mid cap", "expense ratio of HDFC Mid Cap Fund", "out_of_scope"),
    ("out_of_scope: large cap", "exit load of HDFC Large Cap Fund", "out_of_scope"),
    ("out_of_scope: small cap", "aum of HDFC Small Cap Fund", "out_of_scope"),
    ("out_of_scope: g-sec", "tenure of HDFC G-Sec Fund", "out_of_scope"),
    ("out_of_scope: nifty", "Is HDFC Nifty 50 Index Fund in this corpus?", "out_of_scope"),
    ("fact: ter", "What is the expense ratio of HDFC Flexi Cap Fund?", "fact"),
    ("fact: exit load", "What is the exit load of HDFC ELSS Tax Saver?", "fact"),
    ("fact: lock-in", "What is the lock-in period for HDFC ELSS Tax Saver?", "fact"),
    ("fact: aum", "What is the AUM of HDFC Flexi Cap Fund?", "fact"),
]:
    d = g.classify(text)
    check(f"{label} -> {intent}", d.intent == intent, f"{text!r} -> {d.intent}")

# --- 5. hyphen / space tolerance in scheme names ----------------------------

group("5. hyphen and space tolerance")
for text in [
    "What is the expense ratio of HDFC Mid-Cap Fund?",
    "What is the expense ratio of HDFC Mid Cap Fund?",
    "What is the expense ratio of HDFC Mid-Cap?",
    "mid-cap fund expense ratio",
    "MID-CAP",
]:
    d = g.classify(text)
    check(f"hyphenated scheme refused: {text[:40]}", not d.allowed and d.intent == "out_of_scope", f"-> {d.intent}")

# A hyphen-tolerant pattern must not stop the literal ones from matching, and
# must not invent a match where the name differs.
for text, expected in [
    ("tenure of HDFC G-Sec Fund", "out_of_scope"),
    ("tenure of HDFC G Sec Fund", "out_of_scope"),
    ("expense ratio of HDFC Flexi-Cap Fund", "fact"),
    ("expense ratio of HDFC Flexi Cap Fund", "fact"),
    ("expense ratio of HDFC ELSS Tax Saver", "fact"),
]:
    d = g.classify(text)
    check(f"no false scope on {text[:38]}", d.intent == expected, f"-> {d.intent}")

# --- 6. PII wins over everything, and hyphen normalisation cannot break it ---

group("6. PII precedence and PII-pattern integrity")
d = g.classify("my PAN is ABCDE1234F, should I buy HDFC Flexi Cap?")
check("PII beats advice", d.intent == "pii" and not d.allowed, d.intent)
d = g.classify("my number is 98765-43210, which fund gave better returns?")
check("PII beats performance", d.intent == "pii" and not d.allowed, d.intent)
# A hyphen inside a phone number must still be visible to the PII patterns.
check("hyphenated phone still detected", g.detect_pii("call 98765-43210") == ["phone"], str(g.detect_pii("call 98765-43210")))
check("hyphenated aadhaar still detected", "aadhaar" in g.detect_pii("aadhaar 1234-5678-9012"), str(g.detect_pii("aadhaar 1234-5678-9012")))

# --- 7. links and message shape ---------------------------------------------

group("7. links and message shape")
for label, text in [
    ("advice", "Should I buy HDFC Flexi Cap Fund?"),
    ("performance", "which fund gave better returns"),
    ("out_of_scope", "tell me about SBI Liquid Fund"),
]:
    d = g.classify(text)
    check(f"{label} refusal links an approved page", d.link in set(config.APPROVED_URLS) | {config.FACTSHEETS_URL, config.EXPLORE_URL}, str(d.link))
    check(f"{label} message is at most 3 sentences", d.message.count(".") <= 3, str(d.message.count(".")))
    check(f"{label} message has no URL", "http" not in d.message)

d = g.classify("What is the expense ratio of HDFC Flexi Cap Fund?")
check("allowed question has no message", d.message == "")
check("allowed question has no link", d.link is None)

# --- 8. deliberate precedence, pinned ----------------------------------------

group("8. precedence where two intents both match")
# Performance outranks advice and out-of-scope on purpose. These pin the order
# so a later tweak to a pattern list cannot change it unnoticed.
for text, expected in [
    ("which fund gave better returns", "performance"),
    ("should I buy the fund with the best returns", "performance"),
    ("performance of the Nifty index fund", "performance"),
    ("my PAN is ABCDE1234F, should I buy?", "pii"),
    ("my PAN is ABCDE1234F, which gave better returns?", "pii"),
]:
    d = g.classify(text)
    check(f"precedence: {text[:42]} -> {expected}", d.intent == expected, f"-> {d.intent}")

print(f"\n{count - len(failures)}/{count} passed, {len(failures)} failure(s)")
if failures:
    print("\nFailed:")
    for f in failures:
        print(f"  - {f}")
sys.exit(1 if failures else 0)
