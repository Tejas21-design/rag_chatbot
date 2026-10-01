# Implementation Guide (Phase-wise)

**Product:** HDFC Mutual Fund FAQ Assistant (RAG Chatbot)
**Audience:** Class demo — this file is the build playbook you hand to Cursor, one phase at a time.
**Last updated:** 2026-09-30
**Upstream docs:** `docs/PRD.md` (what/why), `docs/architecture.md` (how), `docs/problemstatement.txt` (raw brief)

---

## 0. How to use this file

- Phases are **strictly ordered**. Each phase ends with a **Gate** — a check you must pass before starting the next phase.
- Every phase has a **Cursor prompt** block. Paste that block into Cursor (as a new chat, with the repo root as context) so the agent has a tight, self-contained task.
- Non-negotiables carried into every phase (from PRD §10, architecture §1):
  1. Ingestion and query stay **two separate pipelines**. The chat path never fetches hdfcfund.com.
  2. Embeddings: `sentence-transformers/all-MiniLM-L6-v2` only, for chunks **and** questions.
  3. Vector store: ChromaDB persisted to disk.
  4. LLM: Groq, key in `.env`, never committed.
  5. Answers: ≤3 sentences, exactly one citation URL, `Last updated from sources: <date>`, facts-only, no advice, no return math, no PII.
- Phases map to PRD requirements like this:

| Phase | Deliverable | PRD coverage |
|-------|-------------|--------------|
| 0 | Project skeleton + env + gitignore | FR-6, §7 |
| 1 | Loader for the 5 approved URLs | FR-1 step 1 |
| 2 | Data inspection + `docs/chunking.md` (gated) | FR-1, §14 open items |
| 3 | Chunker + `data/chunks.txt` | FR-1 steps 2, 5 |
| 4 | Embedder + Chroma store | FR-1 steps 3, 4 |
| 5 | Retriever | FR-2 |
| 6 | Guardrails | FR-4, §5.3 |
| 7 | Groq generator + citation/last-updated | FR-3 |
| 8 | Tiny chat UI | §5.1, FR-5 |
| 9 | Deliverables: README, sample Q&A, source list, disclaimer | §11, §13 |
| 10 | End-to-end verification pass | §13 success criteria |

---

## Phase 0 — Project skeleton, env, gitignore

**Goal:** A runnable Python project with the locked stack installed and secrets hygiene in place. No RAG code yet.

### Tasks

1. Create the layout from architecture §9 (exact names may vary, ingest/app split must not):

```
ChatBot/
  docs/           PRD.md, architecture.md, implementation.md, problemstatement.txt, chunking.md
  data/
    chroma/       persisted Chroma (gitignored)
    chunks.txt    inspectable chunk dump
  app/
    __init__.py
    config.py     env + paths + corpus constants
    embedder.py   Phase 4 (stub now)
    ingest.py     Phase 4
    retrieve.py   Phase 5
    guardrails.py Phase 6
    generate.py   Phase 7
    app.py        Phase 8
  scripts/
    inspect_pages.py   Phase 1/2 helper
  .env.example
  .gitignore
  requirements.txt
  README.md        stub now, filled in Phase 9
```

2. `.env.example` — names only, no values:

```
GROQ_API_KEY=
GROQ_MODEL=llama-3.3-70b-versatile
TOP_K=4
CHROMA_DIR=data/chroma
```

3. `.env` — real key, created locally, **never committed**.

4. `.gitignore` must include: `.env`, `.venv/`, `__pycache__/`, `data/chroma/`.

5. `requirements.txt`: `chromadb`, `sentence-transformers`, `groq`, `python-dotenv`, `beautifulsoup4`, `lxml`, `streamlit`, `httpx[http2]`, `brotli`. (`requests` alone is not enough: the AMC CDN blocks it, see the Phase 1 finding.)

6. `app/config.py`: load `.env` via `python-dotenv`, expose `GROQ_API_KEY`, `GROQ_MODEL`, `TOP_K`, `CHROMA_DIR`, and the **five approved URLs** as a constant with a comment marking it the single source of truth for the corpus. Add the two hub URLs used for refusals (factsheets, fund-documents) — they are already in the corpus list.

### Gate 0

- `pip install -r requirements.txt` succeeds.
- `git status` shows no `.env`; `.env.example` is tracked.
- `python -c "import app.config"` prints the five URLs.

### Cursor prompt — Phase 0

```
Create the project skeleton for an HDFC Mutual Fund FAQ RAG chatbot in this repo.

Follow docs/architecture.md section 9 (repo layout) and docs/PRD.md section 7 (locked stack).

Do exactly this and nothing more:
1. Create app/, scripts/, data/chroma/ directories and the empty module files listed in
   architecture.md section 9.
2. Write app/config.py: load .env with python-dotenv; expose GROQ_API_KEY, GROQ_MODEL,
   TOP_K, CHROMA_DIR, CHUNKS_TXT; and define APPROVED_URLS as the exact five URLs from
   PRD section 4.2, plus EDUCATIONAL_URLS for the factsheets and fund-documents hubs.
   Add a comment saying APPROVED_URLS is the single source of truth for the corpus.
3. Write .env.example (names only, no real values) and .gitignore covering .env, .venv/,
   __pycache__/, data/chroma/.
4. Write requirements.txt with: chromadb, sentence-transformers, groq, python-dotenv,
   beautifulsoup4, lxml, streamlit, httpx[http2], brotli.
5. Write a stub README.md with a "Setup" heading only.

Do not implement any RAG logic yet. Do not hardcode any other website URLs.
```

---

## Phase 1 — Loader for the five approved URLs

**Goal:** Fetch + parse the approved pages into a clean `SourceDocument` (architecture §8.1). This is the *only* code in the repo allowed to make HTTP calls.

### Tasks

1. `app/loader.py`:
   - `fetch(url)` with **httpx over HTTP/2** and a full browser header set, 30s timeout, retry (3 attempts, backoff), and raise on failure.
   - **Finding (Phase 1, 2026-09-30):** the AMC's CDN (Akamai) returns `403` to a plain `requests`/`httpx` HTTP/1.1 call regardless of `User-Agent` or individual `Sec-Fetch-*` headers. Only the complete browser navigation header set **plus HTTP/2** gets `200`. Keep `BROWSER_HEADERS` in `app/config.py` intact; do not "simplify" it.
   - Hard guard: **refuse any URL not in `APPROVED_URLS`**. This enforces PRD §4.2 "no third-party blogs".
   - Parse with BeautifulSoup (`lxml`): drop `script`, `style`, `nav`, `header`, `footer`, `noscript`, and elements with cookie/consent class names; normalize whitespace; keep line breaks so tables/lists stay readable.
   - Extract `<title>` (or first `<h1>`).
   - Record `fetched_at` as ISO-8601 UTC.
   - Return `SourceDocument(url, title, text, fetched_at)` exactly as architecture §8.1.
2. `scripts/inspect_pages.py`: CLI that loops the five URLs, writes each doc to `data/raw/<slug>.txt`, and prints a table: URL, HTTP status, char count, word count, first 300 chars.
3. Handle partial failure: if any of the five URLs fails, **fail the run and say which** (architecture §11). Do not silently ingest four pages.

### Gate 1

- `python scripts/inspect_pages.py` prints five successful rows and writes five files under `data/raw/`.
- Spot-check one file: no `<script>` text, no nav boilerplate, scheme facts present.
- `fetch_document("https://groww.in/")` raises `ValueError` (corpus guard).
- One URL failing must fail the whole run, not silently produce a four-page corpus.

### Corpus findings after Phase 1 (carry into Phase 2)

| # | Page | Words | Usable content |
|---|------|-------|----------------|
| 1 | `hdfcfund.com/` | ~1,320 | Nav/scheme list, "Download A/c Statement" link label, blog teasers. Mostly boilerplate. |
| 2 | `hdfc-flexi-cap-fund/direct` | ~1,230 | **Rich**: exit load (1.00% within 1 year), lock-in definition, TER explainer, benchmark (NIFTY 500 TRI), min SIP ₹100, FAQ block. |
| 3 | `hdfc-elss-tax-saver-fund/direct` | ~1,010 | **Rich**: 3-year statutory lock-in, 80C ₹1.5 lakh, min ₹500 SIP/lumpsum, TER/lock-in/benchmark explainers. |
| 4 | `mutual-funds/factsheets` | ~52 | Thin hub page. Explains what a factsheet contains, plus "Latest Factsheet" / "View Historical Factsheet" links. Numbers load client-side. |
| 5 | `mutual-funds/fund-documents` | ~3 | **"No result found!!"** — list is rendered client-side; no server-side text. |

Implications for later phases:

- Pages 4 and 5 cannot supply scheme facts. Expense-ratio **numbers**, riskometer levels and capital-gains statement instructions are not in the v1 HTML corpus.
- The statement-download answer must be sourced from what page 1 actually publishes (the "Download A/c Statement" entry point), or answered honestly as "not stated in the ingested pages; see the official page".
- Phase 2 should record the minimum-chunk rule carefully so page 5's 3-word page does not become a junk chunk.
- Do **not** add a sixth URL to work around this; PRD §4.2 keeps v1 at five, and the honest "not in the official pages" path is the graded behavior.

### Cursor prompt — Phase 1

```
Implement the ingestion-side loader. Context: docs/architecture.md sections 5.1 and 8.1,
docs/PRD.md section 4.2.

1. Create app/loader.py with:
   - fetch_document(url) -> dict with keys url, title, text, fetched_at (ISO-8601 UTC).
   - httpx.Client(http2=True) with the full browser navigation header set from
     app.config.BROWSER_HEADERS. IMPORTANT: the AMC's CDN (Akamai) returns 403 to plain
     requests/httpx HTTP/1.1 calls no matter what User-Agent you set. The combination of
     HTTP/2 and the complete header set (Accept, Accept-Language, Upgrade-Insecure-Requests,
     Sec-Fetch-Dest/Mode/Site/User) is what returns 200. Do not trim the headers.
   - 30s timeout, 3 retries with exponential backoff, raise on failure.
   - A hard assertion that url is in app.config.APPROVED_URLS. Raise ValueError otherwise.
   - BeautifulSoup(lxml) parsing: remove script/style/nav/header/footer/noscript/svg/iframe
     and any element whose class or id contains cookie, consent, gdpr, chatbot or newsletter;
     get title from <title> or first <h1>; raise if the extracted text is empty; collapse
     excess blank lines but keep single newlines so lists and tables stay legible.
   - load_all() that fetches all five URLs and raises a CorpusFetchError naming any URL that
     failed, so a half corpus is never stored.
2. Create scripts/inspect_pages.py: runs load_all(), writes data/raw/<slug>.txt per page with a
   URL/TITLE/FETCHED_AT header, and prints a table of url | status | chars | words, then a
   per-page preview. Add a --preview N flag and a --no-write flag.
3. Add httpx[http2] and brotli to requirements.txt.

Then run the script and show me the table. Do not write chunking code yet.
```

---

## Phase 2 — Data inspection + chunking decision (**GATED**)

**Goal:** Decide chunk size / overlap / metadata **from the real data**, then write `docs/chunking.md`. PRD and architecture both forbid writing the chunker before this.

### Tasks

1. `scripts/inspect_pages.py --stats` and read `data/raw/*.txt` by hand. Record:
   - per-page char/word counts and the total corpus size;
   - heading structure (are there real `h1`–`h4`s after parsing?);
   - table-like blocks (fund-fact tables, "Key Facts" grids);
   - boilerplate / nav residue / duplicate footer text;
   - where the ELSS lock-in sentence and the factsheet/fund-documents listing blocks live.
2. Also inspect the **live HTML** for headings: a flat text walk hides them. `app/loader.py:extract_sections` exists to recover them.
3. Choose and justify:
   - **chunk size** (must respect MiniLM's 256-token `max_seq_length`; measure tokens/word, do not guess),
   - **overlap** (pick one; ~10–15% is typical),
   - **split method**: heading-aware / paragraph-then-recursive-split, with a fixed split on double newline first,
   - **metadata per chunk**: required `url`, `title`, `scheme`, `fetched_at`; justified extras such as `heading`, `chunk_index`,
   - **`scheme` values**: exactly `hdfc-flexi-cap`, `hdfc-elss-tax-saver`, `hdfc-fund-hub`, `unknown` (PRD §4.1 / architecture §8.2).
4. Write `docs/chunking.md` with: observations table, the chosen numbers, the rationale tied to *this* HTML corpus, the metadata schema, and an example chunk.

### Decision (recorded in docs/chunking.md, §2)

| Parameter | Locked value |
|-----------|--------------|
| Split method | heading-aware (`## <heading>` markers), then line/sentence/word packing |
| Target size | 160 words (~215 MiniLM tokens) |
| Max size | 200 words (hard ceiling: above this MiniLM silently truncates at 256 tokens) |
| Min size | 12 words, plus an explicit boilerplate stop-phrase list |
| Overlap | 30 words (~19%), only inside an oversized section, never across a heading |
| Extra metadata | `heading`, `chunk_index` |

### Gate 2

- `docs/chunking.md` exists and states chunk size, overlap, strategy, metadata fields, and why.
- You have *read* the raw text, not guessed. If a page is too thin to answer questions (e.g. a hub page that is pure navigation), say so in the note and plan the honest "not in official pages" path for Phase 7.

### Cursor prompt — Phase 2

```
I need the chunking strategy decided from real data before any chunker code exists.

Steps:
1. Read docs/PRD.md (FR-1) and docs/architecture.md (section 5.2). Do not write the chunker yet.
2. Run `python scripts/inspect_pages.py --stats`, then actually read data/raw/*.txt. Report
   per-page char/word counts, heading structure, table-like blocks, nav/footer noise, and where
   facts like expense ratio, ELSS lock-in and the statements-download text appear.
3. Check the LIVE HTML for headings, because a flat get_text("\\n") walk hides them. Count h1/h2/
   h3/table per page and list the h2/h3 titles. Use app.loader.fetch_html plus BeautifulSoup.
4. Check the embedding model's real limits: SentenceTransformer("sentence-transformers/
   all-MiniLM-L6-v2").max_seq_length, and measure tokens per word on two real samples from the
   corpus. The chunk max size must fit inside max_seq_length or chunks get silently truncated.
5. Based on what you read, propose: chunk size, overlap, split method, the scheme label set, and
   the full metadata schema (url, title, scheme, fetched_at plus any justified extras). Justify
   each choice against this specific HTML corpus in one or two sentences.
6. Write docs/chunking.md with: an observations table, the locked numbers, the metadata schema,
   the line grammar the chunker will consume, one worked example chunk, and a known-limits
   section covering the pages that cannot answer questions.
7. Stop there and show me the proposal. Do not create app/chunker.py yet.
```

---

## Phase 3 — Chunker + inspectable `data/chunks.txt`

**Goal:** Deterministic, metadata-rich chunks written to a human-readable file.

### Tasks

1. `app/chunker.py`, implementing the strategy locked in `docs/chunking.md`:
   - Input is the section list from `app/loader.py:extract_sections` (the `## <heading>` grammar), not raw HTML and not `extract_text`.
   - `chunk_document(doc, sections) -> list[Chunk]` where `Chunk = {id, text, metadata}`.
   - `id` = `sha1(f"{url}#{chunk_index}")[:16]` (architecture §5.4) — stable across re-ingest so a re-run overwrites instead of duplicating. Do **not** hash the text: the AMC edits numbers and ids must survive that.
   - One chunk per section while the section is ≤200 words, which covers 61 of 72 sections. Only a section over 200 words is packed down, with a line → sentence → word fallback and a 160-word target.
   - Never split a `Label: value` key-fact line.
   - Overlap 30 words, applied only *within* a split section, never across a `##` boundary.
   - Prepend `"<heading>\n"` to the embedded text; `metadata["heading"]` holds the same string.
   - Drop chunks under 12 words and chunks matching the boilerplate stop-phrase list. Log the drop count.
   - Metadata per chunk: `url`, `title`, `scheme`, `fetched_at`, `heading`, `chunk_index`.
2. `write_chunks_txt(chunks, path)`:
   - human-readable: header with corpus + timestamp, then per chunk a separator, `CHUNK #i`, `id`, all metadata key/values, then the text. Reviewer must be able to eyeball a random chunk and see where it came from.
3. Wire the dump into the ingest job (Phase 4) but make it runnable standalone for testing.

### Gate 3

- `python -c "from app.chunker import ..."` chunking the five raw docs produces sensible chunk sizes matching `docs/chunking.md` within tolerance.
- Open `data/chunks.txt`: no chunk is a lone nav crumb, no chunk is empty, metadata is present on every chunk.

### Cursor prompt — Phase 3

```
Implement the chunker. Use the locked numbers from docs/chunking.md (you wrote it in
Phase 2) and match docs/architecture.md sections 5.2 and 5.5.

1. Create app/chunker.py with:
   - A Chunk dataclass: id, text, metadata.
   - chunk_document(doc) -> list[Chunk]:
     * split on the document's headings first, remembering the nearest heading for metadata;
     * then split each section into paragraphs on blank lines;
     * then recursively split any oversized paragraph on sentence boundaries, then on
       characters, until every chunk is <= the size in docs/chunking.md;
     * apply the overlap from docs/chunking.md between consecutive chunks of the same section;
     * id = sha1(url + "#" + chunk_index)[:16];
     * metadata: url, title, scheme (map page URL to the scheme labels in docs/chunking.md),
       fetched_at, plus heading and chunk_index;
     * drop chunks that are below the minimum length recorded in docs/chunking.md, and
       return the drop count in a log line.
2. Add write_chunks_txt(chunks, path): writes a file with a header (corpus, generated_at,
   chunk count), then for each chunk a "=== CHUNK 7 ===" separator, its id, each metadata
   key: value on its own line, then the chunk text.
3. Add a __main__ block to app/chunker.py that loads data/raw/*.txt (reuse app/loader.py's
   parsing on the saved text, or re-fetch) and writes data/chunks.txt.
4. Run it and show me: total chunk count, min/median/max chunk length in words, and the
   first 2 chunks from data/chunks.txt.
```

---

## Phase 4 — Embedder + Chroma store (completes ingestion)

**Goal:** Pipeline A is end-to-end: `Load → Chunk → Embed → Store`, persisted to disk, rerunnable.

### Tasks

1. `app/embedder.py` — **single shared module** so ingest and query cannot drift:
   - `@lru_cache(maxsize=1) get_model()` returning `SentenceTransformer(config.EMBEDDING_MODEL)`, asserting the dimension is 384 (use `get_embedding_dimension()`, falling back to the deprecated name for older sentence-transformers).
   - `embed_texts(texts: list[str], batch_size=32) -> list[list[float]]`, asserting `len(vec) == 384`.
   - `embed_query(text) -> list[float]`.
2. `app/ingest.py` — CLI job, not imported by the app:
   - flags: `--reingest` (reset collection first), default = **skip if collection already populated** (architecture §5.4, PRD FR-6),
   - steps: load 5 URLs → `extract_sections` → chunk → write `data/chunks.txt` → embed → `store.get_collection()` → upsert with `ids`, `documents`, `metadatas`, `embeddings` (pass explicit embeddings so ingest and query share the model),
   - write `data/ingest_manifest.json`: `{ingested_at, max_fetched_at, urls, chunk_count, collection, embedding_model, embedding_dim, chroma_dir, chunk_strategy}` — `max_fetched_at` drives the "Last updated from sources" line,
   - on re-ingest: delete and recreate the collection so demo data never duplicates (architecture §5.5).
3. `app/store.py`: `get_client()` (cached `PersistentClient`, `anonymized_telemetry=False`), `get_collection()` (`get_or_create_collection(name, metadata={"hnsw:space": "cosine"})`), `is_populated()`, `reset_collection()`.
4. No Groq call anywhere in ingest (architecture §10).

**Realised:** 63 chunks, all 384-d, all ids matching `make_id(url, chunk_index)`, every url inside `APPROVED_URLS`, all six metadata fields present on every chunk. Second `python -m app.ingest` skips; `--reingest` rebuilds to the same count.

### Gate 4

- `python -m app.ingest` creates `data/chroma/`, reports chunk count, writes chunks.txt and the manifest.
- `python -m app.ingest` a second time **skips** and does not duplicate.
- `python -m app.ingest --reingest` resets and re-stores cleanly.
- `app/ingest.py` contains no `groq` import.
- **Add a retrieval sanity check before moving on** (§ docs/chunking.md 4.1). Query the stored collection with the Phase 5 example questions and record which chunks win and at what cosine distance. A weak hit for expense ratio means Phase 5's re-rank is mandatory, not optional.

### Cursor prompt — Phase 4

```
Complete the ingestion pipeline. Context: docs/architecture.md sections 5.3, 5.4, 5.5 and 10.

1. app/embedder.py: a single shared embedder used by BOTH ingest and query.
   - get_model() returns a cached SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2").
   - embed_texts(texts, batch_size=32) returns list[list[float]] and asserts len(v) == 384.
   - embed_query(text) returns one 384-d vector using the same instance.
2. app/store.py: get_collection() (chromadb.PersistentClient(path=CHROMA_DIR),
   get_or_create_collection("hdfc_faq", metadata={"hnsw:space": "cosine"})), is_populated(),
   reset_collection().
3. app/ingest.py as a CLI, imported by nothing else:
   - default behaviour: if is_populated() and --reingest is not passed, log
     "Chroma already populated, skipping ingest" and exit 0.
   - --reingest: reset_collection() first, so re-runs never duplicate.
   - load the five URLs via app/loader.py, chunk via app/chunker.py, write data/chunks.txt,
     embed, then upsert into the collection with explicit ids/documents/metadatas/embeddings.
   - write data/ingest_manifest.json with ingested_at, urls, chunk_count, collection,
     embedding_model, chroma_dir, and max_fetched_at (max of the documents' fetched_at).
   - must NOT import or call groq.
4. Run: python -m app.ingest, then again to prove the skip, then with --reingest.
   Show me chunk count, collection count(), and the manifest.
```

---

## Phase 5 — Retriever (start of Pipeline B)

**Goal:** Question → embed → top-k chunks, from the persisted collection only.

### Tasks

1. `app/retrieve.py`:
   - `retrieve(question: str, top_k: int = TOP_K) -> list[RetrievedChunk]`, `RetrievedChunk = {text, metadata, distance, url, fetched_at}`.
   - Use `app/embedder.embed_query` (the same module as Phase 4) and `collection.query(query_embeddings=[...], n_results=top_k, include=["documents","metadatas","distances"])`.
   - Raise a clear `ChromaEmptyError` when the collection is missing/empty → UI shows "run ingest first" (architecture §11).
   - **The `heading`/`scheme` re-rank is required, not optional.** Measured in `docs/chunking.md` §4.1: a plain vector top-k ranks the flexi-cap TER chunk 7th for "What is the expense ratio…", because the page says `TER` and `Total Expense Ration`, never "expense ratio". Re-rank on two signals:
     * `scheme` match — question mentions "flexi cap" / "elss" and the chunk's `scheme` agrees → promote;
     * `heading` match — the question's keywords appear in the chunk's `heading` (`exit load`, `lock in`, `min sip`, `riskometer`, `benchmark`, `ter`, `expense`) → promote.
     Keep it under ~25 lines, promote rather than filter (never drop a chunk the vector search found), and document the rule.
   - Expose `context_block(retrieved)` that renders numbered context with each chunk's `url` and `fetched_at` for the prompt, and `best_url(retrieved)` that returns the single citation URL (top-scoring chunk that belongs to one of the five approved URLs).
2. Choose and document `top_k` (start at 4; justify in README in Phase 9). Re-check the Phase 5 Gate against the Phase 4 sanity table in `docs/chunking.md` §4.1 — expense ratio must reach the TER chunk inside top-k.

### Gate 5

- `python -c "from app.retrieve import retrieve; print(retrieve('expense ratio of HDFC Flexi Cap'))"` returns chunks whose `url` is the flexi-cap page first.
- Every returned URL is in `APPROVED_URLS`.
- No HTTP calls in this module.

**Realised.** All passing (19 checks, see `docs/chunking.md` §4.2 for the full table and the measured distances):

- **The TER regression is fixed.** `retrieve("What is the expense ratio of HDFC Flexi Cap Fund?")` puts the key-facts card holding `0.77` at rank 1, against rank 7 for a pure vector top-k. This was the reason the re-rank was made mandatory.
- `retrieve("What is the exit load on HDFC ELSS Tax Saver?")` returns the 3-word `Exit Load / NIL` chunk at rank 1.
- Scheme-aware: the top 2 for a lock-in question are both `hdfc-elss-tax-saver`; both for a min-SIP question are `hdfc-flexi-cap`.
- `best_url` returns exactly one URL and it is in `APPROVED_URLS`; `context_block` numbers chunks and embeds each chunk's `url` and `fetched_at` for the Phase 7 prompt.
- `top_k` is honoured, default `TOP_K=4`; ranking is deterministic; `""` returns `[]`.
- `ChromaEmptyError` verified against a reset (count 0) collection; the corpus was restored to 63 afterwards.
- `app/retrieve.py` imports no `httpx`, `requests`, `urllib` or `app.loader` and contains no URL literal — Pipeline B touches no network (architecture §6.3).

Two decisions worth keeping:

- **`TOP_K = 4`.** Enough to cover a fact that spans the key-facts card plus a FAQ chunk, while keeping the Phase 7 prompt inside Groq's context cheaply. `TOP_K` stays configurable in `app/config.py`.
- **Score the whole collection, not an over-fetch.** `n_results` is set to `collection.count()`. The corpus is 63 chunks and the scan is sub-millisecond; fetching only 16 candidates was what hid both the key-facts card and the `Exit Load / NIL` chunk, because neither resembles the wording of a question about it. Prefer correctness over a micro-optimisation at this corpus size; revisit only if the corpus grows well past a few hundred chunks.

### Cursor prompt — Phase 5

```
Build the retrieval half of the query pipeline. Context: docs/architecture.md section 6.2/6.3.

1. app/retrieve.py with:
   - RetrievedChunk dataclass: text, metadata, distance, url, fetched_at.
   - ChromaEmptyError raised when the collection is absent or count() == 0.
   - retrieve(question, top_k=config.TOP_K) -> list[RetrievedChunk]: embed the question with
     app/embedder.embed_query, then collection.query(query_embeddings=[vec],
     n_results=top_k, include=["documents","metadatas","distances"]).
   - a light scheme re-rank: if the question mentions "flexi cap" or "elss", move chunks
     whose metadata.scheme matches to the front. Keep it under ~20 lines.
   - context_block(retrieved) -> a string for the LLM prompt, numbered, with each chunk's
     url and fetched_at included.
   - best_url(retrieved) -> exactly one URL: the top chunk's url if it is in
     app.config.APPROVED_URLS, else the first retrieved url that is.
2. This module must not import `httpx`/`requests` and must never hit hdfcfund.com.
3. Run a quick check in the terminal for these questions and show the top 3 chunk openings
   plus their url, heading and distance each:
   - "What is the expense ratio of HDFC Flexi Cap Fund?"   <- must reach the TER chunk
   - "What is the lock-in period for HDFC ELSS Tax Saver?"
   - "What is the minimum SIP for HDFC Flexi Cap?"
   - "What is the exit load on HDFC ELSS Tax Saver?"
   - "How do I download a capital gains statement?"
   Compare against the table in docs/chunking.md section 4.1 and report what the re-rank
   changed.
```

---

## Phase 6 — Guardrails (pre-retrieve)

**Goal:** Architecture §6.1 — PII, advice, returns-comparison, and out-of-corpus intent short-circuit before any retrieval or LLM call.

### Tasks

1. `app/guardrails.py`:
   - `classify(question) -> GuardrailDecision(intent, allowed, message, link)` with intents: `pii`, `advice`, `performance`, `out_of_scope`, `fact`.
   - **PII detection**: regex/heuristics for PAN (`[A-Z]{5}\d{4}[A-Z]`), Aadhaar (12 digits, with the Verhoeff/XXXX-XXXX checksum check if cheap), account-like long digit runs, OTP keywords, email address, and Indian phone (`[6-9]\d{9}`). On detection: return a refusal, and **scrub** the PII from the question before anything else runs or is logged. Never echo the PII back in the message.
   - **Advice**: keyword/regex list (`should I buy`, `should I sell`, `best fund`, `recommend`, `worth investing`, `which one to choose`, `allocate`, `portfolio`, `good time to`, `is it safe to`) → polite facts-only refusal + one educational official URL (factsheets or fund-documents).
   - **Performance**: (`CAGR`, `returns`, `performance`, `how much did .* return`, `which performed better`, `best performing`) → do not compute; point to the official factsheet URL.
   - **Out of scope**: other AMC names (`Groww`, `ICICI`, `SBI`, `Axis`, `Nippon`, `Kotak`, `Aditya Birla`, `UTI`, `Tata`, `DSP`, `LIC`, `Canara`) or schemes not in the corpus list → out-of-scope message + explore/factsheets link.
   - Order matters: **PII first**, then advice, then performance, then out-of-scope, else `fact`.
   - Keyword lists live in one config block, not scattered through the code, so the README/demo can show them.
2. All refusal messages ≤3 sentences, facts-only tone, and include the required single link where the PRD demands one.

### Gate 6

- Table of test inputs → decision, covering: PAN paste, phone, email, "should I buy", "best fund", "which fund gave better returns", "tell me about SBI Liquid Fund", and a normal fact question.
- No refusal path calls the retriever or Groq.

**Realised** (`app/guardrails.py`, 352 lines; 122 checks passing across 9 groups):

| Group | Result |
|-------|--------|
| Decision table (20 inputs: PAN, Aadhaar, phone, email, account, OTP, 4 advice, 3 performance, 3 out-of-scope, 4 fact) | all match the expected intent and `allowed` flag |
| PII types and formats (21 inputs) | PAN, Aadhaar, phone, account, email, OTP each detected in every written form; all 8 legitimate numeric questions clean |
| PII never echoed | no refusal message contains any of the 5 test secrets; `pii_types` recorded for inspection without the value |
| `scrub_pii` | removes every PAN/email/phone/Aadhaar/account; leaves `1.00%`, `113,606.47 Cr`, `01/01/2013` and all clean questions untouched |
| Check order | PII > performance > advice > out-of-scope > fact, each verified with a question that matches two patterns |
| No false positives | all 12 in-corpus questions (min SIP, exit load, lock-in, riskometer, benchmark, manager, SID, NAV, AUM, launch date, suitability) pass through as `fact` |
| Single approved link | every refusal link is inside `config.EDUCATIONAL_URLS`; `allowed` decisions carry `link=None` |
| ≤3 sentences | PII refusals 3 sentences, all others 2 |
| Refusal path inert | module imports only `re`, `dataclasses` and `app.config`; no retriever, embedder, Groq or HTTP import |

Integration check: a refused question returns before `retrieve()` is called — 0 retriever calls across advice and PII refusals.

**Five deviations from the task list above, all deliberate.** Items 4 and 5 are bugs found later by `scripts/test_guardrails.py` rather than design choices, but they are recorded here because both change documented behaviour above.

1. **Performance is checked before advice, not after.** "which fund gave better returns" matches both lists. `which fund` is advice-shaped, but a comparison request is the more specific intent, and the user needs the factsheet pointer with no comparison rather than the generic advice refusal. PII remains first and out-of-scope last, as specified.
2. **Aadhaar detection accepts the surrounding word, not just the Verhoeff checksum.** The checksum is implemented and required, but a real-world Aadhaar that fails it is almost always a mistyped one, so a 12-digit run preceded by "aadhaar"/"UIDAI" is treated as Aadhaar even when the checksum fails. An *unlabelled* 12-digit run that fails the checksum is reported as `account`, which is the more likely truth. An all-same-digit run is accepted, since that is how demo data is written.
3. **Digit runs are classified in a single pass, and each number gets exactly one type.** Deciding `aadhaar` and `account` in separate passes over overlapping patterns made one number report as both, and made a checksum-failing Aadhaar fall through to `account`. `detect_pii` and `scrub_pii` now share the same logic, so a value reported as PII is a value that actually gets redacted. Over-redaction is deliberate: any 10+ digit run is redacted, because leaking a folio number is worse than redacting one.

**PII scrub must run before `retrieve()`, and `classify` must run before the scrub.** These are two different requirements and the order between them is not interchangeable. `classify` needs the **original** text: scrubbing first replaces a PAN with `[redacted-pan]`, `detect_pii` then finds nothing, and the PII-bearing question is classified `fact` and answered. So the sequence is `classify(original) -> refuse if needed -> scrub_pii(allowed) -> retrieve`, where the scrubbed text is what gets embedded and sent. Scrubbing is for what leaves the process, not for what it inspects.

**Two more bugs found later, by `scripts/test_guardrails.py`.** Both were introduced while fixing the hyphen bug below, which is the kind of change that fixes one case and quietly breaks another.

4. **Rewriting the question to handle `Mid-Cap` silently un-broke `g-sec`.** The first fix normalised hyphens across the whole question with `text.replace("-", " ")` before matching. That made `Mid-Cap` catchable and made `g-sec` *not* catchable, because the literal name was rewritten to `g sec` and the pattern still required a hyphen. It also fed rewritten text to the PII patterns. Fixed by matching the separator per gap — a name compiles to `\bmid[\s-]+cap\b` — so the question itself is never altered. Group 5 of the suite pins both directions: `Mid-Cap` and `Mid Cap` are refused, and `Flexi-Cap` and `Flexi Cap` are still answered.
5. **The official test Aadhaar `9999 9999 9999` was reported as a phone number.** `PHONE_PATTERN` matched a 10-digit window inside the 12-digit run, claimed the span, and the Aadhaar branch then skipped it as already consumed — so a value deliberately accepted as an Aadhaar in deviation 2 above was mislabelled `phone`. The phone branch now ignores an all-same-digit run, which no real Indian mobile is. The value was refused either way, so this changed the reported type rather than the decision, but a guardrail that reports the wrong type is harder to trust when it matters.

### Cursor prompt — Phase 6

```
Implement the pre-retrieval guardrails. Context: docs/architecture.md section 6.1 and
docs/PRD.md section 5.3.

Create app/guardrails.py with:
- A GuardrailDecision dataclass: intent, allowed, message, link (link may be None).
- class GuardrailDecision intents: "pii", "advice", "performance", "out_of_scope", "fact".
- PII detection with regex + simple validation: PAN pattern, Aadhaar 12-digit (validate the
  Verhoeff checksum if you can do it in a few lines), runs of 10+ digits, OTP keywords, email
  regex, and Indian 10-digit phone starting 6-9. On detection, return a refusal that tells the
  user not to share personal data and does NOT repeat the detected value.
- scrub_pii(text) that redacts any detected PII; it must run before the question is embedded,
  sent to Groq, or logged.
- Advice keywords/regex: should I buy, should I sell, should I invest, best fund, best mutual
  fund, recommend, worth investing, which one should I choose, allocate, portfolio, is it a
  good time, which is better to invest. Response: polite facts-only refusal + the official
  factsheet URL as the single educational link.
- Performance keywords/regex: CAGR, returns, return, performance, how much did ... return,
  which performed better, best performing, % gain. Response: do not compute anything, point to
  the official factsheet URL.
- Out of scope: names of other AMCs (Groww, ICICI, SBI, Axis, Nippon, Kotak, Aditya Birla, UTI,
  Tata, DSP, LIC, Canara, Motilal, PPFAS) -> out-of-scope message + the explore/factsheets link
  from app.config.
- Check order: pii -> advice -> performance -> out_of_scope -> fact.
- All keyword lists in one CONFIG block at the top of the file.
- classify(question) -> GuardrailDecision and scrub_pii(question) as the public API.

Then run a script that prints a table of ~10 test questions and their intent/allowed/link,
including one PAN, one email, one phone, "should I buy HDFC Flexi Cap", "which fund gave better
CAGR", "tell me about SBI Large Cap", and "what is the ELSS lock-in".
```

---

## Phase 7 — Generator: Groq + citation + last-updated

**Goal:** Grounded generation with the strict output contract of PRD FR-3 and architecture §6.4.

### Tasks

1. `app/generate.py`:
   - Groq client from `GROQ_API_KEY` in `.env`; model id from `GROQ_MODEL` (default `llama-3.3-70b-versatile`; record the actual choice in README).
   - `SYSTEM_PROMPT`: facts-only; answer **only** from the provided context; ≤3 sentences; never give advice or opinions; never compute or compare returns; never invent a number that is absent from the context; if the context does not contain the answer, say exactly that it is not in the official pages; do not output more than one URL; do not output markdown headers.
   - Build the user message from `context_block(retrieved)` + the question.
   - `low_confidence(retrieved)` check: if the collection returns nothing usable, or all distances exceed a documented threshold, **skip the LLM entirely** and return an honest "not in the official pages" answer plus the closest hub URL.
   - Return a dict matching architecture §8.3: `{answer, citation_url, last_updated_from_sources, refused, debug?}`. Populate `debug` with the retrieved chunks so the UI can offer a "sources" expander.
   - **Sentence trimming**: post-process the Groq output to ≤3 sentences (split on `. `, keep the first three, re-join). Never let a long completion through.
   - **One link only**: strip any URLs the model emitted from the answer body; the citation comes from `best_url(retrieved)`, not from the model.
- `last_updated_from_sources` comes from `data/ingest_manifest.json` `max_fetched_at` (or the retrieved chunk's `fetched_at`), formatted as a human date — never a model training cutoff.
- On Groq error/bad key: raise a typed error the UI can render; **no fallback to ungrounded model knowledge** (architecture §11).
- **Ungrounded-number backstop**: after trimming, compare every number in the answer against the numbers in the retrieved context. Any figure not present verbatim is treated as fabricated, and the answer is demoted to the not-in-pages path with `debug.reason == "ungrounded_number"`. See the note below.
2. `app/answer.py` (thin orchestrator) or a function in `app/generate.py`: `answer_question(question) -> QueryResponse` running guardrails → (skip) → retrieve → generate. This is the single entry point the UI calls.

### Gate 7

- Answers for the three example questions are ≤3 sentences, contain exactly one URL, and the last-updated line matches the ingest timestamp.
- Fabricated-number check: ask something absent from the corpus ("What is the expense ratio of HDFC Mid-Cap Fund?") and confirm the reply says it is not in the official pages rather than guessing.
- No URL from the model body leaks into the answer text.

**Realised.** `app/generate.py` (265 lines) and `app/answer.py` (81 lines). **86 checks passing** in `scripts/test_generation.py`, with the LLM stubbed so the output contract is verified against deliberately bad completions rather than against a cooperative model. `scripts/test_guardrails.py` (74 checks) covers Phase 6 and `scripts/test_retrieval.py` covers Phase 5, so all three phases are checkable without an API key.

### Checking the work

| Command | Covers | Needs a key? |
|---------|--------|--------------|
| `python scripts/test_retrieval.py` | Phase 5: 63 chunks, re-ranking, approved-URL-only results | no |
| `python scripts/test_guardrails.py` | Phase 6: PII, advice, performance, out-of-scope, precedence | no |
| `python scripts/test_generation.py` | Phase 7: the output contract, grounding, failure handling | no |
| `python -m app.answer` | Phase 7 end to end against the real model | **yes** |

The first three are committed on purpose. A guardrail bug is silent by nature — a question that should be refused gets answered instead, and nothing in the output looks wrong — so the suite is what makes a change to the rule sets safe to make. Each stubs the component under test's *input* (the LLM, or the retriever's corpus) and asserts on the decision, which is the only part of this project where a silent regression is worst.

| Group | Result |
|-------|--------|
| Post-processing | a completion containing 2 URLs, a markdown link, advice and 5 sentences comes back as 3 sentences, no URL, link text preserved, no empty brackets |
| Trimming / stripping | 5 sentences → 3; short and empty text untouched; bare `www.` removed; clean prose unchanged |
| `last_updated_from_sources` | `Last updated from sources: 29 Sep 2026`, taken from the manifest, human-formatted, never ISO and never a model cutoff |
| Low confidence | `[]` and d=1.5 both skip the LLM entirely; d=0.9 is trusted; **0 Groq calls** on the low-confidence path; still cites one approved URL |
| Five Gate 7 questions | all ≤3 sentences, no URL in the body, exactly one citation, last-updated present |
| Grounding | Mid-Cap and Small Cap exit load refused by guardrails; capital-gains statement and fund-manager questions answered "not in the official HDFC Mutual Fund pages" with no figure |
| Ungrounded-number backstop | `0.77%` and `0.77 %` grounded; `0.68%` and `250` flagged; a lying completion is demoted with `debug.reason == "ungrounded_number"` |
| Refusals inert | advice / PII / performance / out-of-scope all refused with **0 Groq calls** |
| PII never reaches the prompt | a PII question makes no LLM call; an allowed question's prompt contains the context block and no secret; `temperature=0`, `max_tokens=300`, model from config |
| Groq failure typed | 401 → `GroqUnavailableError`; empty completion → not-in-pages rather than a blank answer; no ungrounded fallback anywhere |
| Import hygiene | neither module imports `loader`, `chunker`, `ingest`, `httpx` or `requests` |

**Three real bugs this phase caught, all of which would have shipped silently:**

1. **PII was not being detected, and was being answered.** `answer_question` scrubbed the question *before* classifying it, so `"my PAN is ABCDE1234F"` became `"my PAN is [redacted-pan]"` — `detect_pii` no longer found a PAN, the question was classified `fact`, and the PII-bearing question was embedded and sent to Groq. `classify` now runs on the **original** text and `scrub_pii` runs on the question that is passed onward. Phase 6's note that scrubbing must precede classification was the wrong way round for detection; scrubbing is for what leaves the process, not for what it inspects.
2. **`"HDFC Mid-Cap Fund"` escaped the out-of-scope list.** The pattern was the literal `mid cap`, and the hyphenated spelling the AMC actually uses did not match, so the question retrieved from a neighbouring page and answered instead of being refused. Scheme names now compile with a flexible separator (`\bmid[\s-]+cap\b`). See item 4 for the regression this first introduced.
3. **The number backstop first flagged a grounded figure.** The corpus writes `0.77\n%` (the chunker preserves the source line break) while the model writes `0.77%`, so the real TER read as fabricated. Numbers are now compared with whitespace removed and the percent sign kept out of the token, so both forms match and only genuinely absent figures are flagged.

**Live run against `openai/gpt-oss-120b`.** All five Gate 7 questions pass end to end, verified through `scripts/ask.py`. The two in-corpus questions answer with the right figures (TER `0.77`, lock-in `three years`), the capital-gains question declines, and Mid-Cap and "should I buy" are refused by guardrails before any model call.

**Two claims that only the live run could correct:**

- **NAV *is* in the corpus, contrary to the note recorded above.** The stub had been made to decline NAV and manager-fee questions, and that stub behaviour was mistaken for the model's. The corpus carries the NAV (`2169.07` as of the Phase 10 re-ingest; it read `2181.07` two days earlier — these are live pages), so the honest answer is a figure, not a refusal. The fund *manager* name is genuinely absent and is still declined correctly. Anything concluded from a stub about what the corpus contains is now treated as unverified.
- **`llama-3.3-70b-versatile` is retired on Groq and returns 404**, which reads like an auth failure and is not one. The default is now `openai/gpt-oss-120b`, confirmed working, with `qwen/qwen3.8-27b` and `openai/gpt-oss-20b` as alternatives. `python scripts/ask.py --models` lists what a given key can actually use.

**One bug the live run caught that 90 stub checks had not:** the ungrounded-number backstop flagged the correct NAV as fabricated. The corpus writes `2181.07` and `113,606.47` — grouping inconsistently — while the model writes `2,181.07`. Commas are now dropped before comparison, so both spellings of one figure match and only genuinely absent numbers are flagged. A backstop that rejects correct answers is worse than none: it turns a good answer into a refusal.

**Decisions:**

- **`LOW_CONFIDENCE_DISTANCE = 1.0`**, and it is computed from the **raw** distance, not the re-ranked score — the Phase 5 keyword boosts must not be able to talk a weak match into confidence. Measured context: best honest hit d=0.157, weakest honest hit d=0.627, strongest wrong-page hit d=0.736. A cut at 1.0 never fires on this corpus, so it is a floor against a nonsense query rather than a tuning knob. Raising it above ~0.75 would start rejecting real questions.
- **`GROQ_MODEL = llama-3.3-70b-versatile`**, `temperature=0`, `max_tokens=300`.
- **An empty completion is not an error to surface.** It falls through to the not-in-pages path; a blank answer in the UI is worse than an honest "not in the pages". A genuine API failure still raises `GroqUnavailableError`.
- **Refusals carry no `last_updated_from_sources`.** Nothing was retrieved, so there is no source date to quote for a message not grounded in one.
- **The ungrounded-number check is a backstop, not the main defence.** The system prompt already forbids inventing figures, but a confident wrong number is the worst possible failure for a facts-only bot, so it is worth the second check. It compares against all retrieved chunks, not just the cited one, so a correct number in a second chunk is not punished.

### Cursor prompt — Phase 7

```
Implement grounded generation with Groq. Context: docs/architecture.md sections 6.4, 8.3, 11
and docs/PRD.md FR-3.

Create app/generate.py with:
- A SYSTEM_PROMPT that states: answer only from the provided context; at most 3 sentences;
  never give advice, opinions, or recommendations; never compute or compare returns; never
  state a number that does not appear in the context; if the context lacks the answer, say
  it is not in the official HDFC pages; output no markdown headings.
- read_ingest_manifest() reading data/ingest_manifest.json (return None if missing).
- is_low_confidence(retrieved) -> bool: True when retrieved is empty, or when every distance
  is above a named constant LOW_CONFIDENCE_DISTANCE = 1.0. In that case do NOT call Groq and
  return an honest "not in the official pages" answer with the closest hub URL from
  app.config.APPROVED_URLS.
- generate_answer(question, retrieved) -> dict with keys answer, citation_url,
  last_updated_from_sources, refused, debug.
  * calls Groq chat.completions.create with model=config.GROQ_MODEL, temperature 0,
    max_tokens 300.
  * strips any http(s) URL the model put in the answer text,
  * trims the answer to at most 3 sentences by splitting on sentence boundaries,
  * citation_url comes from app.retrieve.best_url(retrieved), never from the model output,
  * last_updated_from_sources comes from the manifest's max_fetched_at, else from the top
    retrieved chunk's fetched_at, formatted as "Last updated from sources: 30 Sep 2026".
  * debug holds the retrieved chunks for a UI sources expander.
- raise a clear GroqUnavailableError on API failure or missing key; do not fall back to any
  other knowledge.
- Also create app/answer.py with answer_question(question) -> dict that runs:
  guardrails.classify(original) -> (refuse and return early if not allowed) -> scrub_pii ->
  retrieve -> low-confidence check -> generate_answer, returning the same dict shape.
  `classify` reads the original question; the scrubbed text is what is embedded and sent on.

Then run a script that prints, for these five questions, the answer, the citation_url and the
last-updated line, plus the sentence count of each answer:
  1. What is the expense ratio of HDFC Flexi Cap Fund?
  2. What is the lock-in period for HDFC ELSS Tax Saver?
  3. How do I download a capital gains statement from HDFC Mutual Fund?
  4. What is the expense ratio of HDFC Mid-Cap Fund?   (not in corpus)
  5. Should I buy HDFC Flexi Cap Fund right now?      (refusal)
```

---

## Phase 8 — Tiny chat UI

**Goal:** PRD §5.1 — welcome line, 3 clickable example questions, persistent "Facts-only. No investment advice." note, chat input, and per-answer citation + last-updated + disclaimer.

### Tasks

1. `app/app.py` (Streamlit, from `requirements.txt`):
   - `st.set_page_config(page_title="HDFC MF FAQ Assistant", layout="centered")`.
   - Welcome line, the three example questions as clickable buttons (the PRD's suggested three), the persistent facts-only note, and the full disclaimer from PRD §12 in an expander or footer.
   - `st.chat_input("Ask a factual question about HDFC Mutual Fund schemes")` + `st.chat_message` loop with a capped in-memory history (`st.session_state`), no identity, no persistence to disk.
   - Call only `answer_question` from `app/answer.py` — **no loader, no chunker, no ingestion on the query path**.
   - On start, check `is_populated()`; if empty, show "Corpus not ingested. Run: `python -m app.ingest`" and stop.
   - Per bot message: answer body, exactly one link (`st.link_button` or markdown link), `Last updated from sources: ...`, and a "Sources" expander listing the retrieved chunks with their URLs.
   - Sanity: the UI should never display more than one URL per answer.
2. A `run_app` convenience script or document the exact command in README (Phase 9).

### Gate 8

- `streamlit run app/app.py` shows the welcome line, 3 example buttons, the facts-only note, and the disclaimer.
- Clicking an example returns a cited answer; the answers show one link and a last-updated line.
- "Should I buy HDFC Flexi Cap?" returns a refusal with an official educational link.
- Restarting the app does **not** re-run ingestion.

**Realised.** `app/app.py`, run as `streamlit run app/app.py`. **54 checks passing** in `scripts/test_ui.py`, plus a real server start on port 8599/8601/8603.

| Group | Result |
|-------|--------|
| Query path | imports only `app.answer`, `app.generate`, `app.config`, `app.store`; no loader, chunker, ingest or httpx anywhere |
| Ingestion | imports `store` **only** to call `is_populated()`; no ingest call on any path |
| Empty corpus | `is_populated()` false → "Corpus not ingested." + `python -m app.ingest`, then `st.stop()`; verified with the collection emptied, and the collection was still empty afterwards |
| Render, 6 real answers | body rendered, **exactly one** `link_button` each, link inside `APPROVED_URLS ∪ EDUCATIONAL_URLS`, **no URL in the answer body** |
| Refusals | advice and out-of-scope each link an approved educational page and show **no** last-updated line |
| Layout | one `set_page_config`, called first; `chat_input`, `chat_message`, title, 3 PRD example questions, facts-only note, disclaimer expander |
| Executed top to bottom | `app/app.py` runs against a stubbed Streamlit without raising, on both the populated and empty corpus |

**One real bug, and it is the kind that hides:** the UI imported cleanly and crashed only under `streamlit run`. Streamlit puts the script's own directory on `sys.path`, so `app/app.py` **shadowed the `app` package** — `from app import config` resolved to the script itself and raised a circular import. Unit-level checks could not have found it. The fix puts the project root at the front of `sys.path` before any `app` import, and the suite now executes the file top to bottom under a stub so the class of failure is covered rather than one instance of it.

**Decisions:**

- **The disclaimer is a module-level constant**, rendered from one string, and the suite reads it with `ast.literal_eval` rather than grepping the source. A grep for `"Scheme Information Document (SID)"` fails on a correct value purely because the string wraps across lines, which is exactly the kind of false negative that erodes trust in a suite.
- **No session persistence to disk.** History lives in `st.session_state` for the browser session and holds question and answer text only.
- **`GroqUnavailableError` is caught in the UI**, so a missing key shows the error and the fix rather than a Streamlit traceback.
- **Both PRD example questions verified live**: "What is the expense ratio of HDFC Flexi Cap Fund (Direct–Growth)?" → `0.77 %`, and "What is the lock-in for HDFC ELSS Tax Saver?" → three years. Naming the plan is what makes the model add the `%`, since the bare-plan table renders the figure without one.
- **Not verified:** no browser driver is installed, so the suite exercises the Streamlit API through a stub rather than a real DOM. The server was confirmed to start and serve HTTP 200 on all three ports, but no click-through of a real session was performed. `st.button` returning `False` under the stub means the example buttons' click path is exercised only as far as `ask()` being called.

### Cursor prompt — Phase 8

```
Build the tiny chat UI with Streamlit. Context: docs/PRD.md section 5.1 and 12,
docs/architecture.md section 4 and 6.

Create app/app.py:
- st.set_page_config(page_title="HDFC MF FAQ Assistant", layout="centered").
- A welcome line explaining this is a facts-only assistant for HDFC Mutual Fund.
- The three example questions from PRD section 5.1 rendered as clickable buttons that
  prefill and submit the question.
- A persistent note: "Facts-only. No investment advice."
- The full disclaimer text from PRD section 12 in an expander.
- st.chat_input("Ask a factual question about HDFC Mutual Fund schemes") and a message
  loop using st.session_state for in-memory history only. No chat history on disk, no
  user identity, no logging.
- On startup, call app.store.is_populated(); if false, print "Corpus not ingested. Run:
  python -m app.ingest" and stop instead of searching an empty collection.
- Every bot message renders: the answer, exactly ONE source link, the line
  "Last updated from sources: <date>", and a "Sources" expander listing the retrieved
  chunks with their urls.
- The UI may import app.answer.answer_question, app.store.is_populated and app.config only.
  It must NOT import app.loader, app.chunker or app.ingest.

Then start the app, and tell me the exact command to run it. Do not start ingestion from
the UI.
```

---

## Phase 9 — Deliverables pack

**Goal:** Everything PRD §11 lists, so grading/demo-day is a copy-paste.

### Tasks

1. `README.md` with: what it is, AMC + schemes in scope, **setup steps** (venv, install, copy `.env.example` to `.env`, add `GROQ_API_KEY`), **how to ingest** (`python -m app.ingest`, `--reingest` semantics, "ingest runs once; app start does not re-ingest"), **how to chat** (`streamlit run app/app.py`), the Groq model ID actually used, the Chroma persist path, `TOP_K`, the chunk size/overlap summary with a pointer to `docs/chunking.md`, the five-URL source list, the disclaimer, and **known limits** (single AMC, five pages only, hub pages may be thin, no returns, no PII, no live NAV).
2. `docs/sources.md` (or `sources.csv`) — the five approved URLs, one per row, with the role of each.
3. `sample_qa.md` — 5–10 query / answer / citation triples produced by **actually running** the app, not written by hand. Include at least: the 3 example questions, a minimum-SIP or exit-load question, a riskometer/benchmark question, a statement-download question, one refusal, and one out-of-scope question.
4. `docs/chunking.md` finished (from Phase 2) and updated if anything changed during Phases 3–4.
5. Confirm `.env` is not tracked; `.env.example` is.
6. `data/chunks.txt` present and readable (commit it or commit a clearly-marked sample if large).

### Gate 9

- A fresh clone + the README steps reaches a working chat on another machine.
- Every PRD §13 checkbox has evidence: a command output, a file, or a screenshot.

### Cursor prompt — Phase 9

```
Finish the deliverables. Context: docs/PRD.md section 11 and 13.

1. Write README.md covering: overview; AMC and schemes in scope; setup (venv, pip install,
   cp .env.example .env, add GROQ_API_KEY); how to ingest (python -m app.ingest, what
   --reingest does, and the note that app start never re-ingests); how to chat (streamlit run
   app/app.py); the Groq model id in use; the Chroma persist path; TOP_K; chunk size and
   overlap with a link to docs/chunking.md; the five source URLs; the disclaimer from PRD
   section 12; and known limits.
2. Write docs/sources.md: a table of the five approved URLs with the role of each.
3. Write sample_qa.md by actually running the app through app.answer.answer_question for
   8-10 questions: the three example questions, one minimum SIP or exit load question, one
   riskometer or benchmark question, one statement download question, one advice question
   (refusal), one out-of-scope AMC question, and one question whose answer is not in the pages.
   Paste the real answers and the real citation url for each. Do not invent answers.
4. Review docs/chunking.md and update it if the final implementation differs from the Phase 2
   proposal.
5. Verify with git status that .env is untracked and .env.example is tracked. Show me the
   git status output and a checklist mapping each PRD section 13 item to the file or command
   that proves it.
```

---

## Phase 10 — End-to-end verification

**Goal:** Prove the success criteria, not just the features. Run this before the demo, and keep the output as evidence.

### Tasks

1. Clean-room test: `rm -rf data/chroma data/chunks.txt` → `python -m app.ingest` → app start → 3 example questions answer correctly.
2. Restart test: start the app twice; confirm the second start does not re-fetch the pages (check logs / timings).
3. Idempotency test: run ingest twice, confirm `collection.count()` is stable.
4. Grounding test: pick 5 questions whose answers are definitely **not** in the corpus and verify the app says so instead of guessing.
5. Refusal test: run the full guardrail table and record outcomes.
6. Sentence/length test: assert every answer body is ≤3 sentences and displays exactly one URL.
7. Secrets test: `git log -p | grep -i groq` should find no key value.
8. PRD §13 checklist signed off with evidence.

### Gate 10

- All PRD §13 boxes checked with evidence.
- Demo script: 3-minute walkthrough — 1 answer, 1 refusal, 1 out-of-scope, 1 "not in the pages", 1 look at `data/chunks.txt`.

### Cursor prompt — Phase 10

```
Run a full verification pass and report results, one section per test, with the actual
command output pasted in. Do not skip a test because it looks like it will pass.

1. Clean room: delete data/chroma and data/chunks.txt, run python -m app.ingest, report chunk
   count and collection count.
2. Restart: run ingest again and confirm it skips and the collection count is unchanged.
3. Re-ingest: run with --reingest and confirm the count is still correct with no duplicates.
4. Grounding: for 5 questions that are definitely not answerable from the five pages (for
   example HDFC Mid-Cap expense ratio, HDFC Small Cap exit load, best performing HDFC fund,
   current NAV, tax treatment specifics), call app.answer.answer_question and confirm each
   says the information is not in the official pages and does not state a number.
5. Refusals: run the guardrail cases (PAN, email, phone, "should I buy", "best fund",
   "which gave better CAGR", "tell me about SBI Large Cap") and show intent + message + link.
6. Output contract: for 10 varied questions assert the answer body is at most 3 sentences and
   that exactly one citation url is returned. Print a pass/fail table.
7. Secrets: run git log -p and grep for the GROQ_API_KEY value pattern; confirm .env is
   untracked and .env.example is tracked.
8. Finish with a checklist mapping every item in docs/PRD.md section 13 to the evidence you
   just produced, and list anything that is still failing.
```

---

## Appendix A — Locked vs. chosen values

Decide these once, record them, and do not drift:

| Item | Status | Where it is recorded |
|------|--------|---------------------|
| Embedding model | **Locked** `sentence-transformers/all-MiniLM-L6-v2` (384-d) | PRD §7, arch §3 |
| Vector store | **Locked** ChromaDB on disk | PRD §7, arch §3 |
| LLM provider | **Locked** Groq, key in `.env` | PRD §7, arch §3 |
| Corpus | **Locked** the 5 URLs | PRD §4.2, `app/config.py` |
| Chroma persist dir | **Locked in Phase 0** `data/chroma/` | `app/config.py`, README |
| `GROQ_MODEL` | **`openai/gpt-oss-120b`** (chosen Phase 7, confirmed live), `temperature=0`, `max_tokens=300`. `llama-3.3-70b-versatile` was the original pick but is retired on Groq and 404s | `app/config.py`, README |
| `TOP_K` | **4** (chosen Phase 5, see Gate 5) | `app/config.py`, README |
| Chunk size / overlap | **Gated** in Phase 2 from real data | `docs/chunking.md` |
| `LOW_CONFIDENCE_DISTANCE` | **1.0** (chosen Phase 7), on the raw distance not the re-ranked score | `app/generate.py` + README |
| UI framework | **Chosen Phase 8: Streamlit 1.64**, run as `streamlit run app/app.py` | `requirements.txt`, `app/app.py`, README |

## Appendix B — Things Cursor keeps getting wrong here

Feed these into the prompt whenever relevant:

- **The app must never fetch the corpus.** If `app/app.py` or `app/retrieve.py` imports `httpx`/`requests` or `app.loader`, that is a bug — it breaks the two-pipeline rule in PRD §6.
- **Do not trim `BROWSER_HEADERS` or drop HTTP/2.** The AMC CDN returns 403 to anything else, and ingest will fail.
- **Same embedding model both sides.** Do not let ingest use one model and query another; both must go through `app/embedder.py`. Assert 384 dimensions.
- **The citation URL comes from chunk metadata, not from the LLM.** The model's URLs get stripped.
- **The last-updated date comes from the ingest manifest**, never from the model's notion of time.
- **Ingest idempotency.** Re-running must not duplicate chunks; reset on `--reingest`.
- **Do not expand the corpus.** If a needed fact is missing, the honest "not in the official pages" path is the correct behavior, not a sixth URL.
- **No PII anywhere**: not in prompts, not in logs, not in chat history, not in `chunks.txt`.
- **No return math.** CAGR, comparisons, and rankings are refusals, not answers.
- **No secrets in Git**, and no `.env` in the commit.
- **`app/app.py` shadows the `app` package.** Streamlit puts the script's own directory on `sys.path`, so a bare `from app import config` resolves to the file itself and raises a circular import — but only under `streamlit run`, never on import. Keep the project root first in `sys.path`, and keep `scripts/test_ui.py`, which executes the file top to bottom.
- **`classify` must run before `scrub_pii`, not after.** Scrubbing first turns a PAN into `[redacted-pan]`, so detection finds nothing and the PII-bearing question gets answered. Scrubbing is for what leaves the process, not for what it inspects.
- **Never conclude what the corpus contains from a stub.** The stub was written to decline NAV questions and that was mistaken for the model's behaviour; the NAV was in the corpus all along. The same trap then bit a *test*: `test_generation.py` hardcoded `2181.07`, and the Phase 10 clean-room ingest found the live page now reads `2169.07`, so the grounding test failed on a figure that was never wrong. Figures under test are now read from the retrieved chunks, not pinned as literals.
