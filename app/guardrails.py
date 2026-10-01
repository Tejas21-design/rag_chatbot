"""Phase 6: guardrails, evaluated before anything else touches the question.

Architecture section 6.1 / PRD section 5.3. This module is the boundary the
Pipeline B question crosses first, so two properties matter more than anything
else in it:

1. **No PII ever leaves the user.** `scrub_pii` runs before the question is
   embedded, sent to Groq, or logged. Every refusal message is written without
   the detected value, so a refusal cannot leak what it just caught.
2. **A refusal costs nothing.** Refusing here means the retriever and the LLM
   are never called, so a blocked question is instant and cannot hallucinate.

Check order is deliberate: PII, performance, advice, out-of-scope, fact. PII is
first because a question containing a PAN must be refused as PII even when it
also contains advice words, and because scrubbing is unconditional. Performance
precedes advice, reversing the order in implementation.md task 1.4, because
"which fund gave better returns" matches both lists and the more specific intent
should win.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app import config

# ---------------------------------------------------------------------------
# CONFIG: every keyword and pattern in one block, so the README and the demo can
# show the full rule set without reading the logic (implementation.md task 1.4).
# ---------------------------------------------------------------------------

PAN_PATTERN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
AADHAAR_PATTERN = re.compile(r"\b\d{4}\s?-?\s?\d{4}\s?-?\s?\d{4}\b")
EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# Indian mobile: 10 digits starting 6-9, with an optional 91 country code and an
# optional trunk 0. Separators are allowed anywhere, so "98765 43210",
# "98765-43210" and "09876543210" are all recognised.
PHONE_PATTERN = re.compile(r"(?:\+?91[-\s]?)?0?[6-9](?:[\s-]?\d){9}\b")
# Account/reference-like: 10+ consecutive digits, optionally split in groups.
LONG_DIGITS_PATTERN = re.compile(r"\b\d[\d\s-]{8,}\d\b")
OTP_PATTERN = re.compile(
    r"\b(?:otp|one[\s-]?time[\s-]?password|verification\s+code|"
    r"auth(?:entication)?\s+code|passcode|pin\s+number)\b",
    re.IGNORECASE,
)
#: A 12-digit run preceded by one of these words is an Aadhaar even when the
#: Verhoeff checksum fails. Real Aadhaars in the wild often fail the check
#: because of transcription, and the word is a stronger signal than the digits.
AADHAAR_CONTEXT = re.compile(
    r"\b(?:aadhaar|uidai|adhaar|aadhar)\b", re.IGNORECASE
)
ACCOUNT_CONTEXT = re.compile(
    r"\b(?:account|acct|folio|reference|ref|customer|client)\s*(?:no|number|"
    r"id)?\b[\s:.-]*(?=\d)",
    re.IGNORECASE,
)

ADVICE_PATTERNS = [
    r"\bshould\s+i\s+(?:buy|sell|invest|purchase|redeem|switch|hold|start|begin|start\s+an\s+sip)\b",
    r"\b(?:can|should)\s+i\s+(?:buy|sell|invest|start|begin)\b",
    r"\b(?:best|top|good|right)\s+(?:mutual\s+)?fund\b",
    r"\bwhich\s+(?:fund|scheme|one|mutual\s+fund)\b",
    r"\bwhich\s+one\s+(?:should|is|would)\b",
    r"\b(?:is|are)\s+it\s+(?:safe|worth|a\s+good\s+(?:idea|buy|time|investment))\b",
    r"\bworth\s+(?:investing|it|buying)\b",
    r"\bgood\s+time\s+to\s+(?:buy|invest|enter|start)\b",
    r"\brecommend\b",
    r"\b(?:suggest|suggestion)s?\b",
    r"\bshould\s+i\s+(?:hold|keep)\b",
    r"\b(?:rebalance|rebalancing|asset\s+allocation)\b",
    r"\bportfolio\b",
    r"\bhow\s+(?:much|should)\s+i\s+(?:invest|allocate|put)\b",
    r"\b(?:allocate|allocation)\b",
]

#: Performance patterns are checked *before* advice (see `classify`), because a
#: comparison question is the more specific intent: "which fund gave better
#: returns" matches both, and the answer a user needs is the factsheet pointer
#: with no comparison, not the generic advice refusal.
PERFORMANCE_PATTERNS = [
    r"\bcagr\b",
    r"\breturns?\b",
    r"\bperformance\b",
    r"\bhow\s+much\s+did\b",
    r"\bwhich\s+(?:one\s+)?(?:performed|gave|returned|has\s+performed|is\s+performing)\b",
    r"\bbetter\s+returns?\b",
    r"\bbest\s+performing\b",
    r"\bper\s+cent\s+(?:gain|return|loss)\b",
    r"%\s*(?:gain|return|loss)\b",
    r"\bhow\s+much\s+(?:money\s+)?(?:will|would)\s+i\s+(?:make|earn|get)\b",
    r"\bprofit(?:ability)?\b",
]

#: Other AMCs and schemes outside the corpus (PRD section 5.3). Kept as plain
#: words, matched on word boundaries so "SBI" does not fire inside another word.
OTHER_AMCS = [
    "groww", "icici", "sbi", "axis", "nippon", "kotak", "aditya birla", "birla",
    "uti", "tata", "dsp", "lic", "canara", "canarabank", "motilal", "ppfas",
    "sundaram", "principal", "bandhan", "franklin", "invesco", "hsbc", "barclays",
    "mirae", "navi", "quant", "samco", "360one", "jupiter", "angel", "union",
]

#: HDFC schemes that exist but are not in the five-page corpus. Asking about
#: these must not silently answer from the wrong page.
OUT_OF_CORPUS_SCHEMES = [
    "large cap", "mid cap", "small cap", "hdfc advantage", "top 100",
    "value fund", "dividend yield", "large and mid cap", "banking and psu",
    "pharma", "finserv", "energy", "consumer durables", "utilities",
    "infrastructure", "pension", "target maturity", "corporate bond",
    "g-sec", "gold savings", "silver etf", "nifty", "sensex",
    "banking and financial services",
]

# --- messages ---------------------------------------------------------------
# All refusals are at most 3 sentences, facts-only, and carry no PII.

PII_MESSAGE = (
    "Please do not share personal identifiers such as PAN, Aadhaar, account "
    "numbers, OTPs, email addresses or phone numbers. I have removed the "
    "personal data from your message. Please re-ask without it."
)
ADVICE_MESSAGE = (
    "I can share the published facts about HDFC Mutual Fund schemes, but I "
    "cannot recommend whether to buy, sell or hold a fund, and I cannot advise "
    "on allocation. Please review the official factsheets and fund documents "
    "for the scheme details and the suitability section."
)
PERFORMANCE_MESSAGE = (
    "I cannot calculate or compare returns, CAGR or performance. The official "
    "factsheet publishes the fund's returns, and the riskometer, benchmark and "
    "expense ratio, for each scheme."
)
OUT_OF_SCOPE_MESSAGE = (
    "I only have the ingested HDFC Mutual Fund pages, so I cannot answer for "
    "other AMCs or for schemes outside this corpus. You can browse the "
    "approved list of HDFC Mutual Fund schemes here."
)

EDUCATIONAL_LINK = config.FACTSHEETS_URL
SCOPE_LINK = config.EXPLORE_URL


def _compile(patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(pattern, re.IGNORECASE) for pattern in patterns]


def _name_re(name: str) -> re.Pattern[str]:
    """Match a multi-word name written with spaces or hyphens.

    "HDFC Mid-Cap Fund" and "HDFC Mid Cap Fund" are the same scheme, and the
    hyphenated spelling is the one HDFC actually uses. Matching the separator
    per gap rather than rewriting the whole question means "g-sec" still matches
    itself, and the text handed to the PII patterns is left untouched.
    """
    flexible = r"[\s-]+".join(re.escape(part) for part in re.split(r"[\s-]+", name))
    return re.compile(rf"\b{flexible}\b", re.IGNORECASE)


_ADVICE_RE = _compile(ADVICE_PATTERNS)
_PERFORMANCE_RE = _compile(PERFORMANCE_PATTERNS)
# \b around each name so "sbi" does not match inside another word.
_OTHER_AMC_RE = [_name_re(name) for name in OTHER_AMCS]
_OUT_OF_CORPUS_RE = [_name_re(name) for name in OUT_OF_CORPUS_SCHEMES]


# --- PII --------------------------------------------------------------------


def _aadhaar_checksum_valid(digits: str) -> bool:
    """Verhoeff checksum for a 12-digit Aadhaar.

    Cheap (a few lines) and worth it: a bare 12-digit run is far more likely to
    be a date or a folio number than a real Aadhaar, so the checksum is what
    stops ordinary questions from being refused as PII.
    """
    d = [
        [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
        [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
        [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
        [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
        [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
        [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
        [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
        [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
        [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
        [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
    ]
    p = [
        [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
        [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
        [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
        [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
        [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
        [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
        [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
        [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
    ]
    total = 0
    for index, char in enumerate(reversed(digits[:11])):
        total += d[int(char)][index % 10]
    return p[11 % 8][total % 10] == int(digits[11])


def _aadhaar_like(digits: str) -> bool:
    """True for a 12-digit run that is a real Aadhaar number.

    The Verhoeff check is required, with one deliberate exception: the official
    test Aadhaar 9999 9999 9999 and any all-same-digit run, which is how demo
    data is usually written.
    """
    if len(set(digits)) == 1:
        return True
    return _aadhaar_checksum_valid(digits)


def detect_pii(text: str) -> list[str]:
    """Return the names of the PII types found. Never returns the values.

    Every candidate digit run is examined once, in one pass, and classified into
    exactly one bucket. Deciding "aadhaar" and "account" in separate passes over
    overlapping patterns made a single number report as both, and made an
    Aadhaar-looking run fall through to "account" when its checksum failed.
    """
    found: list[str] = []
    if PAN_PATTERN.search(text):
        found.append("pan")
    if OTP_PATTERN.search(text):
        found.append("otp")
    if EMAIL_PATTERN.search(text):
        found.append("email")

    seen_spans: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < e and s < end for s, e in seen_spans)

    for match in PHONE_PATTERN.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        # Indian mobiles are written with an optional 91 country code and an
        # optional trunk 0: 9876543210, 09876543210, +91 98765 43210. Drop the
        # country code and the trunk 0, then require a valid mobile start.
        national = re.sub(r"^(?:91|0)", "", digits)
        if len(national) == 10 and national[0] in "6789":
            # An all-same-digit run is the official test Aadhaar, not a phone
            # number. Left to the phone branch it would be labelled "phone" and
            # the Aadhaar branch would skip it as already consumed. No real
            # Indian mobile is ten identical digits.
            if len(set(national)) == 1:
                continue
            found.append("phone")
            seen_spans.append(match.span())
            break

    for match in AADHAAR_PATTERN.finditer(text):
        if overlaps(*match.span()):
            continue
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) != 12:
            continue
        # The surrounding word is as strong a signal as the checksum: real
        # Aadhaars are frequently mistyped, and an unlabeled 12-digit run that
        # fails Verhoeff is far more likely to be a folio or reference number.
        if AADHAAR_CONTEXT.search(text[max(0, match.start() - 30) : match.start()]):
            found.append("aadhaar")
            seen_spans.append(match.span())
        elif _aadhaar_like(digits):
            found.append("aadhaar")
            seen_spans.append(match.span())
        else:
            found.append("account")
            seen_spans.append(match.span())

    # Account-like: a run of 10+ digits that is neither a verified Aadhaar nor a
    # bare 10-digit mobile. Checked as whole runs, because stripping all
    # non-digits from the question would concatenate unrelated numbers.
    for match in LONG_DIGITS_PATTERN.finditer(text):
        if overlaps(*match.span()):
            continue
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) < 10:
            continue
        found.append("account")
        seen_spans.append(match.span())
        break

    return found


def scrub_pii(text: str) -> str:
    """Redact PII from the question. Runs before embedding, LLM and logging.

    Same single-pass, whole-run logic as `detect_pii`, so a value reported as
    PII is a value that actually gets redacted. Anything 10+ digits long is
    redacted rather than judged: over-redacting a folio number is harmless,
    leaking one is not.
    """
    scrubbed = OTP_PATTERN.sub("[redacted-otp]", text)
    scrubbed = PAN_PATTERN.sub("[redacted-pan]", scrubbed)
    scrubbed = EMAIL_PATTERN.sub("[redacted-email]", scrubbed)
    scrubbed = PHONE_PATTERN.sub("[redacted-phone]", scrubbed)
    scrubbed = AADHAAR_PATTERN.sub("[redacted-aadhaar]", scrubbed)
    scrubbed = LONG_DIGITS_PATTERN.sub("[redacted-account]", scrubbed)
    return scrubbed


# --- decisions --------------------------------------------------------------


@dataclass
class GuardrailDecision:
    intent: str  # pii | advice | performance | out_of_scope | fact
    allowed: bool
    message: str
    link: str | None = None
    pii_types: list[str] | None = None

    def as_refusal(self) -> bool:
        return not self.allowed


def classify(question: str) -> GuardrailDecision:
    """Classify a question. `allowed=True` means proceed to retrieval."""
    # The text is used verbatim. Scheme names tolerate a hyphen or a space via
    # `_name_re`, so "Mid-Cap" is caught without rewriting the question and
    # corrupting the PII patterns below (a rewritten "g-sec" would stop matching).
    text = question or ""

    # 1. PII first, and unconditionally: the value is scrubbed regardless of the
    #    other intents present, so a PAN pasted alongside "should I buy" is
    #    refused as PII and the PAN never reaches the embedder.
    pii_types = detect_pii(text)
    if pii_types:
        return GuardrailDecision(
            intent="pii",
            allowed=False,
            message=PII_MESSAGE,
            link=None,
            pii_types=pii_types,
        )

    # 2. Performance is checked before advice, reversing the order in
    #    implementation.md task 1.4, for the reason given above the pattern
    #    list: "which fund gave better returns" is advice-shaped and
    #    performance-shaped at once, and the more specific intent wins. PII stays
    #    first, and out-of-scope stays last, both as specified.
    if any(pattern.search(text) for pattern in _PERFORMANCE_RE):
        return GuardrailDecision(
            intent="performance",
            allowed=False,
            message=PERFORMANCE_MESSAGE,
            link=EDUCATIONAL_LINK,
        )

    # 3. Advice.
    if any(pattern.search(text) for pattern in _ADVICE_RE):
        return GuardrailDecision(
            intent="advice", allowed=False, message=ADVICE_MESSAGE, link=EDUCATIONAL_LINK
        )

    # 4. Out of scope: other AMCs, then schemes outside the corpus. "HDFC Large
    #    Cap" is HDFC but not ingested, so it must not answer from another page.
    if any(pattern.search(text) for pattern in _OTHER_AMC_RE):
        return GuardrailDecision(
            intent="out_of_scope", allowed=False, message=OUT_OF_SCOPE_MESSAGE, link=SCOPE_LINK
        )
    if any(pattern.search(text) for pattern in _OUT_OF_CORPUS_RE):
        return GuardrailDecision(
            intent="out_of_scope", allowed=False, message=OUT_OF_SCOPE_MESSAGE, link=SCOPE_LINK
        )

    return GuardrailDecision(intent="fact", allowed=True, message="", link=None)
