# HDFC Mutual Fund FAQ Assistant

A facts-only RAG assistant that answers questions about **five public HDFC Mutual Fund
pages**. It refuses to give investment advice, refuses to calculate returns, and says
*"the information is not in the official pages"* rather than guessing.

Every answer is grounded in text scraped from those pages, carries **one** source link,
and shows the date the pages were fetched. There is no fallback to general model
knowledge — if the corpus cannot support an answer, you get a refusal instead.

---

## What it can and cannot answer

| In scope | Out of scope |
|---|---|
| Expense ratio / TER, AUM, NAV, inception date | "Should I buy / sell / hold?" |
| Riskometer level, benchmark, exit load | CAGR, returns, rankings, "which performed better" |
| Minimum SIP, lock-in, entry load | Tax treatment specifics |
| Fund suitability ("Ideal for…") | Other AMCs (SBI, ICICI, Axis…) |
| Where to find SID / KIM / factsheets | HDFC schemes outside the five pages |

**Single AMC, single set of pages.** The corpus is HDFC Mutual Fund Flexi Cap and
HDFC ELSS Tax Saver, plus three hub pages. Questions about other HDFC schemes are
refused rather than answered from a neighbouring page.

---

## Setup

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# open .env and paste your Groq key after GROQ_API_KEY=
```

Get a key at <https://console.groq.com/keys>. To confirm the model your key can use:

```bash
python scripts/ask.py --models
```

> **macOS note:** Streamlit's first-run email prompt blocks the app from serving
> (you get a blank page / HTTP 503). Disable it once:
> ```bash
> mkdir -p ~/.streamlit && printf '[server]\nshowEmailPrompt = false\n' > ~/.streamlit/config.toml
> ```

---

## Ingest (run once)

```bash
python -m app.ingest
```

This fetches the five URLs, chunks the text, embeds it and writes
`data/chroma/` plus `data/ingest_manifest.json`. It takes about a minute and is
**idempotent** — running it twice will not duplicate anything.

```bash
python -m app.ingest --reingest    # force a rebuild after the pages change
```

**The app never ingests.** Starting the UI does not re-scrape the pages; if the corpus
is missing you get an explicit instruction rather than a silent slow start.

---

## Run the app

```bash
streamlit run app/app.py
```

Or without activating the venv:

```bash
.venv/bin/python -m streamlit run app/app.py
```

Then open <http://localhost:8501>.

## Test from the terminal

```bash
python scripts/ask.py                              # interactive
python scripts/ask.py "What is the AUM of HDFC Flexi Cap Fund?"
python scripts/ask.py --suite                      # the five gate questions
python scripts/ask.py -q "..." --sources           # show retrieved chunks
```

## Run the checks

```bash
python scripts/test_retrieval.py    # 10/10   Phase 5
python scripts/test_guardrails.py   # 74/74   Phase 6
python scripts/test_generation.py   # 90/90   Phase 7
python scripts/test_ui.py           # 54/54   Phase 8
```

All four need no API key and no network — they stub the model and the corpus and
assert on the decisions, not on the model's cooperation.

---

## How it works

```
INGEST (once)                      QUERY (per question)
  httpx HTTP/2 fetch  5 URLs          guardrails.classify(original)
  heading-aware chunk 63 chunks        refuse? -> return, no LLM call
  MiniLM embed       384-d             scrub_pii
  Chroma persist     data/chroma/      retrieve  63 chunks, re-rank, top 4
                                        confidence too low? -> refuse
                                        Groq  openai/gpt-oss-120b, temp 0
                                        strip URLs, trim to 3 sentences
                                        ungrounded figure? -> refuse
```

Two orderings are load-bearing and easy to get wrong:

- **`classify` runs on the original question, `scrub_pii` on the rest.** Scrubbing
  first turns a PAN into `[redacted-pan]`, so detection finds nothing and the
  PII-bearing question gets answered.
- **The citation comes from chunk metadata, never from the model.** Anything URL-like
  in the model's output is stripped.

### Configuration

| Setting | Value | Where |
|---|---|---|
| Embedding model | `sentence-transformers/all-MiniLM-L6-v2` (384-d, both sides) | `app/config.py` |
| LLM | `openai/gpt-oss-120b`, `temperature=0`, `max_tokens=300` | `.env` → `GROQ_MODEL` |
| `TOP_K` | 4 | `.env` |
| Vector store | ChromaDB, `data/chroma/` | `.env` → `CHROMA_DIR` |
| Chunking | heading-aware, 160-word target, 30 overlap | [`docs/chunking.md`](docs/chunking.md) |
| Low-confidence cut | 1.0 on the raw distance (not the re-ranked score) | `app/generate.py` |

> `llama-3.3-70b-versatile` was the original model choice but is retired on Groq and
> returns a 404 that looks like an auth failure. `openai/gpt-oss-120b` is verified working.

---

## Source pages

The five ingested URLs and what each contributes are in
[`docs/sources.md`](docs/sources.md). Real question/answer transcripts are in
[`sample_qa.md`](sample_qa.md).

---

## Known limits

- **Single AMC.** HDFC only. Other AMCs are refused.
- **Five pages only.** Hub pages are thin; several facts a user would expect (tax
  treatment, capital-gains downloads, fund manager names) are genuinely not there,
  and the bot says so.
- **No returns or comparisons.** By design — CAGR and "which fund performed better"
  are refusals.
- **No live data.** NAV and AUM are as of the fetch date shown under each answer,
  not real-time.
- **63 chunks.** Small enough that the last-updated date is meaningful, but it means
  thin coverage outside the two scheme pages.
- **The grounding backstop checks numbers, not claims.** A fluent but incorrect
  sentence containing no figures would pass. The prompt forbids it; nothing
  mechanically prevents it.
- **Requires a Groq key.** Without one every question raises a typed error rather
  than degrading — there is no fallback.

---

## Disclaimer

**Facts-only. No investment advice.**

This assistant answers from a small set of **public HDFC Mutual Fund pages**. It is
for education and a class demo only. It is **not** SEBI-registered advice, not a
recommendation to buy or sell, and not a substitute for the Scheme Information
Document (SID), Key Information Memorandum (KIM), or your advisor. Numbers can
change; always verify on the official site. Do not share PAN, Aadhaar, account
numbers, OTPs, or other personal data here.
