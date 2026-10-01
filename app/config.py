"""Shared configuration and the approved-corpus definition.

Single source of truth for paths, env-backed settings, and the five approved
hdfcfund.com URLs. Only ingestion-time code (loader) is allowed to make HTTP
requests, and only to URLs listed in APPROVED_URLS.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")

# --- paths -----------------------------------------------------------------

DATA_DIR = PROJECT_ROOT / "data"
CHROMA_DIR = Path(os.getenv("CHROMA_DIR", DATA_DIR / "chroma"))
CHUNKS_TXT = DATA_DIR / "chunks.txt"
RAW_DIR = DATA_DIR / "raw"
INGEST_MANIFEST = DATA_DIR / "ingest_manifest.json"

# --- env-backed settings ---------------------------------------------------

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
# Default is a model that is actually served. `llama-3.3-70b-versatile` was the
# original choice but is retired on Groq and now 404s, which reads like an auth
# failure and is not one. Override with GROQ_MODEL in .env if your account
# offers something better; `python scripts/ask.py --models` lists what you have.
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
TOP_K = int(os.getenv("TOP_K", "4"))

# --- corpus ----------------------------------------------------------------

# Single source of truth for the corpus (docs/PRD.md section 4.2). Nothing in
# this project may fetch, embed, or cite a URL outside this list.
APPROVED_URLS: list[str] = [
    "https://www.hdfcfund.com/",
    "https://www.hdfcfund.com/explore/mutual-funds/hdfc-flexi-cap-fund/direct",
    "https://www.hdfcfund.com/explore/mutual-funds/hdfc-elss-tax-saver-fund/direct",
    "https://www.hdfcfund.com/mutual-funds/factsheets",
    "https://www.hdfcfund.com/mutual-funds/fund-documents",
]

FACTSHEETS_URL = "https://www.hdfcfund.com/mutual-funds/factsheets"
FUND_DOCUMENTS_URL = "https://www.hdfcfund.com/mutual-funds/fund-documents"
EXPLORE_URL = "https://www.hdfcfund.com/explore/mutual-funds"

# Official same-domain links used when refusing advice / performance / scope
# questions (docs/PRD.md section 5.3). These are already part of the corpus.
EDUCATIONAL_URLS: list[str] = [FACTSHEETS_URL, FUND_DOCUMENTS_URL, EXPLORE_URL]

EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
COLLECTION_NAME = "hdfc_faq"

# Scheme label per approved URL; "hdfc-fund-hub" for the two document hubs and
# the AMC home page. Used as chunk metadata (docs/architecture.md section 8.2).
URL_SCHEME_MAP: dict[str, str] = {
    "https://www.hdfcfund.com/": "hdfc-fund-hub",
    "https://www.hdfcfund.com/explore/mutual-funds/hdfc-flexi-cap-fund/direct": "hdfc-flexi-cap",
    "https://www.hdfcfund.com/explore/mutual-funds/hdfc-elss-tax-saver-fund/direct": "hdfc-elss-tax-saver",
    "https://www.hdfcfund.com/mutual-funds/factsheets": "hdfc-fund-hub",
    "https://www.hdfcfund.com/mutual-funds/fund-documents": "hdfc-fund-hub",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# The AMC's CDN (Akamai) rejects requests that do not look like a real browser
# navigation with 403, so the full header set below is required for ingest to
# work. Verified against all five approved URLs.
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.5",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

HTTP_TIMEOUT = 30
HTTP_ATTEMPTS = 3


def slug_for(url: str) -> str:
    """Stable filesystem-friendly slug for a corpus URL."""
    return url.replace("https://www.", "").replace("https://", "").strip("/").replace("/", "_") or "home"


_SCHEME_BY_NORMALIZED = {url.rstrip("/"): scheme for url, scheme in URL_SCHEME_MAP.items()}


def scheme_for(url: str) -> str:
    return _SCHEME_BY_NORMALIZED.get(url.rstrip("/"), "unknown")
