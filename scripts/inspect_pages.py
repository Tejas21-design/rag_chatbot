"""Fetch the five approved HDFC pages, dump them to data/raw/, print stats.

Usage:
    python scripts/inspect_pages.py
    python scripts/inspect_pages.py --preview 400
    python scripts/inspect_pages.py --stats
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.loader import CorpusFetchError, load_all  # noqa: E402

SEPARATOR = "-" * 60


def _read_raw(path: Path) -> str:
    """Body of a data/raw file, with the URL/TITLE/... header stripped off."""
    return path.read_text(encoding="utf-8").split(SEPARATOR, 1)[1].strip()


def report_stats() -> int:
    """Offline structural analysis of data/raw. Feeds docs/chunking.md."""
    from collections import Counter

    try:
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(config.EMBEDDING_MODEL)
    except Exception as exc:  # noqa: BLE001 - stats should still work without it
        print(f"(tokenizer unavailable: {exc})", file=sys.stderr)
        tokenizer = None

    print("=" * 78)
    print("STRUCTURAL ANALYSIS OF data/raw")
    print("=" * 78)

    for path in sorted(config.RAW_DIR.glob("*.txt")):
        body = _read_raw(path)
        lines = body.splitlines()
        lengths = sorted(len(line) for line in lines)
        median = lengths[len(lengths) // 2] if lengths else 0
        tokens = len(tokenizer(body)["input_ids"]) if tokenizer else -1

        print(f"\n### {path.name}")
        print(f"  chars={len(body):6d}  words={len(body.split()):5d}  "
              f"lines={len(lines):4d}  tokens={tokens:5d}  median_line={median:4d}")
        if tokens > 0:
            print(f"  tokens per char = {tokens / len(body):.3f}  "
                  f"(so ~{len(body) / tokens:.1f} chars per token)")

        counts = Counter(lines)
        repeated = [(n, line) for line, n in counts.items() if n > 2 and len(line) > 8]
        redundant = sum(n - 1 for n, _ in repeated)
        print(f"  lines repeated >2x: {len(repeated)} distinct, "
              f"{redundant} redundant of {len(lines)} = {redundant / max(len(lines), 1):.0%}")
        for n, line in sorted(repeated, reverse=True)[:6]:
            print(f"      x{n:<3d} {line[:64]}")

        if not lines:
            print("  !! no text extracted; page is client-rendered")
            continue

        # Longest contiguous run of short "label-like" lines: the key-facts table.
        short = [len(line) < 40 for line in lines]
        best = current = 0
        for flag in short:
            current = current + 1 if flag else 0
            best = max(best, current)
        print(f"  longest run of <40-char lines: {best} "
              f"(key-facts label/value pairs land here)")

    print("\n" + "=" * 78)
    print("Headings present in the live HTML (h1/h2/h3 counts, fetched on demand):")
    print("=" * 78)
    try:
        import httpx
        from bs4 import BeautifulSoup

        with httpx.Client(
            http2=True,
            headers=config.BROWSER_HEADERS,
            timeout=config.HTTP_TIMEOUT,
            follow_redirects=True,
        ) as client:
            for url in config.APPROVED_URLS:
                soup = BeautifulSoup(client.get(url).text, "lxml")
                counts = {tag: len(soup.find_all(tag)) for tag in ("h1", "h2", "h3", "table")}
                sample = [
                    " ".join(h.get_text(" ", strip=True).split())[:52]
                    for h in soup.find_all(["h2", "h3"])[:5]
                    if h.get_text(strip=True)
                ]
                print(f"  {url.rsplit('/', 1)[-1] or 'home':28s} {counts}")
                for text in sample:
                    print(f"      - {text}")
    except Exception as exc:  # noqa: BLE001 - heading check is best effort
        print(f"  (heading check skipped: {exc})")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Load and inspect the approved corpus.")
    parser.add_argument(
        "--preview",
        type=int,
        default=200,
        help="characters of each page's text to print (default: 200).",
    )
    parser.add_argument(
        "--no-write", action="store_true", help="print stats without writing data/raw files."
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="structural analysis of data/raw (no network): line lengths, boilerplate "
        "repetition, token counts. Input to docs/chunking.md.",
    )
    args = parser.parse_args()

    if args.stats:
        return report_stats()

    print(f"Loading {len(config.APPROVED_URLS)} approved URLs from docs/PRD.md section 4.2...\n")
    try:
        documents = load_all()
    except CorpusFetchError as exc:
        print(f"CORPUS LOAD FAILED\n{exc}", file=sys.stderr)
        return 1

    config.RAW_DIR.mkdir(parents=True, exist_ok=True)

    header = f"{'#':<3}{'status':<8}{'chars':>8}{'words':>8}  url"
    print(header)
    print("-" * len(header))

    total_chars = 0
    for index, doc in enumerate(documents, start=1):
        chars = len(doc["text"])
        words = len(doc["text"].split())
        total_chars += chars
        print(f"{index:<3}{'ok':<8}{chars:>8}{words:>8}  {doc['url']}")

        if not args.no_write:
            out_path = config.RAW_DIR / f"{config.slug_for(doc['url'])}.txt"
            out_path.write_text(
                f"URL: {doc['url']}\n"
                f"TITLE: {doc['title']}\n"
                f"FETCHED_AT: {doc['fetched_at']}\n"
                f"CHARS: {chars}\n"
                f"WORDS: {words}\n"
                f"{'-' * 60}\n\n{doc['text']}\n",
                encoding="utf-8",
            )

    print(f"\nTotal: {len(documents)} pages, {total_chars} chars")

    for index, doc in enumerate(documents, start=1):
        print("\n" + "=" * 70)
        print(f"[{index}] {doc['title']}")
        print(f"    {doc['url']}")
        print(f"    fetched_at={doc['fetched_at']}  words={len(doc['text'].split())}")
        print("-" * 70)
        print(doc["text"][: args.preview].replace("\n", " | "))

    if not args.no_write:
        print(f"\nWrote {len(documents)} files to {config.RAW_DIR}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
