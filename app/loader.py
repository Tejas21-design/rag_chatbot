"""Ingestion-side loader: fetch and parse the five approved HDFC pages.

This is the only module allowed to make HTTP requests, and it refuses any URL
outside app.config.APPROVED_URLS. Output matches the SourceDocument shape in
docs/architecture.md section 8.1: {url, title, text, fetched_at}.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import TypedDict

import httpx
from bs4 import BeautifulSoup

from app import config

# Tags whose entire subtree is noise for retrieval.
_NOISE_TAGS = ("script", "style", "nav", "header", "footer", "noscript", "svg", "iframe", "form")

# Substrings that mark cookie/consent/marketing wrappers.
_NOISE_MARKERS = ("cookie", "consent", "gdpr", "chatbot", "chat-widget", "newsletter", "footer")


class SourceDocument(TypedDict):
    url: str
    title: str
    text: str
    fetched_at: str


class CorpusFetchError(RuntimeError):
    """Raised when one or more approved URLs could not be loaded."""


def _require_approved(url: str) -> str:
    if url not in config.APPROVED_URLS:
        raise ValueError(
            f"URL is not part of the approved corpus (docs/PRD.md section 4.2): {url}"
        )
    return url


def _get_client() -> httpx.Client:
    """HTTP/2 client with a full browser header set.

    The AMC's CDN (Akamai) returns 403 to anything that does not look like a
    real browser navigation, so both the transport and the headers matter.
    """
    return httpx.Client(
        http2=True,
        headers=config.BROWSER_HEADERS,
        timeout=config.HTTP_TIMEOUT,
        follow_redirects=True,
    )


def fetch_html(url: str, *, timeout: int | None = None, attempts: int = 3) -> str:
    """GET an approved URL, retrying with backoff. Raises on final failure."""
    _require_approved(url)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with _get_client() as client:
                response = client.get(url, timeout=timeout or config.HTTP_TIMEOUT)
                response.raise_for_status()
                return response.text
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(2**attempt)
    raise CorpusFetchError(f"Failed to fetch {url} after {attempts} attempts: {last_error}")


def _is_noise(element) -> bool:
    classes = element.get("class") or []
    identifiers = element.get("id") or ""
    haystack = " ".join(classes) + " " + identifiers
    haystack = haystack.lower()
    return any(marker in haystack for marker in _NOISE_MARKERS)


def _clean_soup(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(list(_NOISE_TAGS)):
        tag.decompose()
    for element in soup.find_all(attrs={"class": True}) + soup.find_all(attrs={"id": True}):
        if element.decomposed or _is_noise(element):
            element.decompose()
    return soup


def _page_title(soup: BeautifulSoup) -> str:
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
        if title:
            return title
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)
    return ""


def extract_text(html: str, url: str) -> tuple[str, str]:
    """Return (title, cleaned_text). Keeps single newlines so lists and tables
    stay legible; collapses runs of blank lines and trailing whitespace."""
    soup = _clean_soup(html)
    title = _page_title(soup)
    text = soup.get_text(separator="\n")

    lines = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if line:
            lines.append(line)

    cleaned = "\n".join(lines)
    if not cleaned:
        raise CorpusFetchError(f"No readable text extracted from {url}")
    return title, cleaned


# Key-facts card labels observed on the scheme pages (docs/chunking.md §2). The
# label and its value are separate DOM nodes, so they must be re-paired or the
# embedding loses the number (e.g. "TER" 0.77).
_KEY_FACT_LABELS = (
    "returns",
    "inception date",
    "riskometer",
    "min sip",
    "ideal for",
    "entry load",
    "ter",
    "lock in",
    "nav",
    "aum",
    "benchmark",
    "exit load",
)


def _is_key_fact_label(line: str) -> bool:
    lowered = line.strip().lower().rstrip(":")
    return lowered in _KEY_FACT_LABELS


# Chrome, not values, despite looking like one. Without this the lookahead pairs
# "TER" with "Disclaimer" instead of the 0.77 three lines further down.
_NOT_A_VALUE = frozenset(
    {"disclaimer", "click here", "since inception", "read more", "view detail",
     "know more", "view all", "latest factsheet -", "view historical factsheet"}
)

_NUMERIC_VALUE = re.compile(
    r"^(?:[₹$€£]\s*)?[\d][\d,.]*%?\s*(?:cr\.?|lakh|cr)?$"      # ₹100, 16.17%, 1.21
    r"|^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$"                          # 01/01/2013
    r"|^n/?a$|^nil$|^none$|^not applicable$",                     # NA, NIL
    re.IGNORECASE,
)

# A bare as-on date, e.g. "(31/08/2026)" under the AUM label. It is a period
# stamp, not the AUM value, so it must not be paired as one.
_AS_ON_DATE = re.compile(r"^\(?\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\)?$")


_MAX_VALUE_CHARS = 45
_MAX_VALUE_WORDS = 5


def _is_value_line(line: str, label: str = "") -> bool:
    """True for the value half of a key-fact row.

    The card's values are all short and never end in a full stop, while its
    definitions are full sentences. That difference is the reliable signal, so
    short unpunctuated lines count whether they are numeric (0.77, 16.17%),
    placeholders (NA, NIL) or short text ("Very High", "3 years", "NIFTY 500
    Total Returns Index").

    `label` disambiguates dates: "01/01/2013" is the Inception Date value but
    only a period stamp under AUM.
    """
    if not line or line.startswith("## "):
        return False
    stripped = line.strip()
    if stripped.lower() in _NOT_A_VALUE or _is_key_fact_label(stripped):
        return False
    if len(stripped) > _MAX_VALUE_CHARS or len(stripped.split()) > _MAX_VALUE_WORDS:
        return False
    if _AS_ON_DATE.match(stripped) and "inception" not in label.lower():
        return False
    if _NUMERIC_VALUE.match(stripped):
        return True
    return not stripped.endswith((".", ":", ";"))


def _is_qualifier(line: str) -> bool:
    """A lowercase continuation of a label, e.g. "since inception" after "Returns"."""
    return (
        bool(line)
        and not line.startswith("## ")
        and not _is_key_fact_label(line)
        and not _is_value_line(line)
        and line[:1].islower()
        and len(line) <= 40
        and len(line.split()) <= 4
    )


def _pair_key_facts(cleaned: list[str]) -> list[str]:
    """Join a key-facts label with the value that follows it.

    The card renders each pair as sibling nodes, so a flat line walk yields
    "Min SIP" / "₹ 100" on two lines and the number loses its meaning once
    embedded alone. Done here at load time so the chunker only splits on
    headings and paragraph boundaries.
    """
    lines: list[str] = []
    index = 0
    while index < len(cleaned):
        line = cleaned[index]

        if not line.startswith("## ") and _is_key_fact_label(line):
            probe = index + 1
            qualifiers: list[str] = []
            # Absorb at most one label continuation, e.g. "Returns" + "since inception".
            while probe < len(cleaned) and len(qualifiers) < 1 and _is_qualifier(cleaned[probe]):
                qualifiers.append(cleaned[probe])
                probe += 1

            # Pair only when the value is immediately adjacent (after at most one
            # qualifier). Cards where the value sits behind the definition
            # prose ("TER" ... "0.77", "AUM" ... "₹113,606.47 Cr.") are left
            # alone on purpose: probing further picks up the wrong neighbour
            # (the as-on date, "Disclaimer"), and the definition plus its value
            # stay adjacent in the same chunk anyway, so the LLM still sees both.
            if probe < len(cleaned) and _is_value_line(cleaned[probe], line):
                lines.append(f"{' '.join([line, *qualifiers])}: {cleaned[probe]}")
                index = probe + 1
                continue

            lines.append(" ".join([line, *qualifiers]))
            index = probe
            continue
            # No value line (e.g. "TER" followed by its definition prose): keep the
            # label and its continuation as-is so the chunker keeps them together.
            lines.append(" ".join([line, *qualifiers]))
            index = probe
            continue

        lines.append(line)
        index += 1
    return lines


def extract_sections(html: str, url: str) -> tuple[str, list[dict]]:
    """Return (title, sections) preserving heading structure and key-fact pairs.

    Output line grammar, consumed by app/chunker.py (docs/chunking.md §3):
      "## <heading>"  -> a section boundary
      "Label: value"   -> one atomic key-fact row, label never separated from value
      anything else    -> plain body line
    """
    soup = _clean_soup(html)
    title = _page_title(soup)

    for level in ("h1", "h2", "h3", "h4"):
        for heading in soup.find_all(level):
            text = " ".join(heading.get_text(" ", strip=True).split())
            if text:
                heading.replace_with(soup.new_string(f"\n## {text}\n"))

    cleaned: list[str] = []
    for raw_line in soup.get_text(separator="\n").splitlines():
        line = " ".join(raw_line.split())
        if line:
            cleaned.append(line)

    lines = _pair_key_facts(cleaned)

    if not lines:
        raise CorpusFetchError(f"No readable text extracted from {url}")

    sections: list[dict] = []
    current = {"heading": "Overview", "lines": []}
    for line in lines:
        if line.startswith("## "):
            if current["lines"]:
                sections.append(current)
            current = {"heading": line[3:].strip(), "lines": []}
        else:
            current["lines"].append(line)
    if current["lines"]:
        sections.append(current)

    return title, sections


def fetch_document(url: str, *, timeout: int | None = None, attempts: int = 3) -> SourceDocument:
    """Load one approved page into a SourceDocument."""
    _require_approved(url)
    html = fetch_html(url, timeout=timeout, attempts=attempts)
    title, text = extract_text(html, url)
    return {
        "url": url,
        "title": title,
        "text": text,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def load_all(*, timeout: int | None = None, attempts: int = 3) -> list[SourceDocument]:
    """Load all five approved pages. Fails loudly rather than returning a
    partial corpus (docs/architecture.md section 11)."""
    documents: list[SourceDocument] = []
    failures: list[str] = []
    for url in config.APPROVED_URLS:
        try:
            documents.append(fetch_document(url, timeout=timeout, attempts=attempts))
        except Exception as exc:  # noqa: BLE001 - collected and reported together
            failures.append(f"  - {url}: {exc}")
    if failures:
        raise CorpusFetchError(
            "Corpus load failed for "
            f"{len(failures)} of {len(config.APPROVED_URLS)} approved URLs:\n"
            + "\n".join(failures)
        )
    return documents
