# Chunking Note

**Status:** Locked (Phase 2 output, 2026-09-30)
**Decided after inspecting:** the five loaded pages in `data/raw/`
**Consumed by:** `app/chunker.py` (Phase 3), `app/loader.py:extract_sections`
**Requirement:** docs/PRD.md FR-1 ("Before coding chunking: inspect the loaded text; propose chunk size, overlap, and metadata; record the rationale")

Reproduce the inspection with `python scripts/inspect_pages.py --stats`.

---

## 1. What the loaded data actually looks like

| # | Page | Chars | Words | Tokens | Lines | Median line |
|---|------|-------|-------|--------|-------|-------------|
| 1 | `hdfcfund.com/` | 7,714 | 1,323 | 1,839 | 427 | 10 |
| 2 | `hdfc-flexi-cap-fund/direct` | 7,629 | 1,229 | 1,638 | 147 | 29 |
| 3 | `hdfc-elss-tax-saver-fund/direct` | 6,122 | 1,007 | 1,358 | 115 | 21 |
| 4 | `mutual-funds/factsheets` | 366 | 52 | 77 | 5 | 25 |
| 5 | `mutual-funds/fund-documents` | 17 | 3 | 7 | 1 | 17 |

Total corpus: **21,848 chars ≈ 4,900 MiniLM tokens**. This is a small corpus; the whole thing fits in a context window, so the job of chunking here is not context management but **retrieval precision** — keeping a fact and its label in the same retrievable unit.

Four observations drove every decision below.

### 1.1 Headings exist and are clean

The live HTML is well structured. Per page: home `1 h1 / 26 h2 / 9 h3`, flexi cap `1 / 24 / 14 / 1 table`, ELSS `1 / 24 / 5 / 1 table`, factsheets `1 / 14 / 0`, fund documents `0 / 13 / 1`.

On the scheme pages the `h2`/`h3` map exactly onto the units users ask about — `Exit Load`, `FAQs`, `Downloads`, `Product Suitability`, `About HDFC ELSS - Tax Saver Fund`, and one heading per numbered FAQ question on the ELSS page.

A flat `get_text("\n")` walk **destroys this**, because the 147 flexi-cap lines interleave sections. `app/loader.py:extract_sections` now rewrites `h1`–`h4` into `## <heading>` markers before extracting text, so the chunker can split on real document structure instead of guessing. Body sections recovered: 29 (home), 26 (flexi cap), 14 (ELSS), 3 (factsheets), 0 (fund documents).

### 1.2 The key-facts card is a label/value table

Every scheme page repeats the same card. In the DOM each pair is two sibling nodes, so a flat walk gives:

```
Riskometer          Very High
Min SIP             ₹ 100
Inception Date      01/01/2013
```

Three facts in the PRD's in-scope list — expense ratio, minimum SIP, riskometer, ELSS lock-in — live in exactly this card. If the chunker splits between a label and its value, the retrieved chunk contains a bare `0.77` or `₹ 100` and the LLM cannot tell what it refers to.

Fix, applied at **load** time in `_pair_key_facts`: pair a label with the value line immediately after it, then emit one line `Min SIP: ₹ 100`. Pairs when the value is adjacent; cards where the value sits behind the definition prose (`TER` → prose → `0.77`) are deliberately left unpaired rather than guessed at, because both halves stay adjacent in the same chunk anyway. A bare as-on date such as `(31/08/2026)` is excluded so it is not mistaken for the AUM value.

### 1.3 Section sizes are small and lopsided

72 sections total: median **23.5 words**, mean 45. 61 are ≤60 words. Only 5 exceed 120 words:

| Section | Words |
|---------|-------|
| home → `Latest Posts And Shorts` | 427 |
| flexi cap → `How to start investing` (contains the key-facts card) | 364 |
| ELSS → `About HDFC ELSS - Tax Saver Fund` (contains the key-facts card) | 408 |
| ELSS → `1. How to Invest in HDFC ELSS Tax Saver?` | 149 |
| ELSS → `3. How to Redeem HDFC ELSS Tax Saver?` | 151 |

So heading-aware splitting alone resolves ~93% of the corpus. A single fixed-size splitter would be actively harmful here: at 400-word chunks it would smear the flexi-cap key-facts card together with its marketing copy, and at 100-word chunks it would cut the 12-row key-facts card in half.

### 1.4 29% of the home page is repeated boilerplate

124 of 427 home-page lines are repeats: `Very High` ×18, `Investors` ×16, `INVEST NOW` ×10, `Riskometer` ×9, `KNOW MORE` ×9, `31-08-2026` ×8, plus a duplicated blog-teaser block. Embedded as-is these produce near-identical vectors that crowd out real content in a top-k search. The scheme pages are clean by comparison (1–2% repeats).

---

## 2. Locked values

| Parameter | Value | Why this number |
|-----------|-------|----------------|
| **Split method** | Heading-aware, then line/paragraph packing | §1.1: 24–27 `h2` and 5–14 `h3` per scheme page align with the facts users ask about. Splitting on them keeps `Exit Load` in one chunk and each FAQ Q/A pair in one chunk. |
| **Target size** | **160 words** | §1.3: 61 of 72 sections are already under 60 words, so the target only applies to the 5 oversized sections. At the measured 1.2–1.4 MiniLM tokens/word, 160 words ≈ 215 tokens — inside the model's 256-token `max_seq_length` with headroom. |
| **Max size** | **200 words** | 200 words ≈ 270 tokens would exceed MiniLM's hard 256-token limit and get **silently truncated**, dropping the tail of a chunk mid-fact. 200 is the ceiling at which a 1.35 tokens/word ratio still fits. |
| **Min size** | **12 words** | Filters crumbs like `Overview`, `Equity`, `DIRECT`, `Table`, `Graph`, `NIL` standing alone, which would otherwise become low-similarity noise chunks. |
| **Overlap** | **30 words (~19%)** | Only applied *within* one oversized section, never across a heading boundary. The 364/408-word key-facts sections are where a fact could land on a cut line, so a 19% window keeps a neighbouring fact pair retrievable. Mean section is 23 words, so the 61 small sections get **zero** overlap. |
| **Split point** | Line, then sentence, then word | Lines are already the page's natural unit after §1.1. Sentences keep the exit-load two-line rule (`1.00% if redeemed within 1 year` / `No exit load after 1 year`) from being cut apart. |
| **Chunk preamble** | `"<heading>\n"` prepended to the embedded text | A bare chunk reading `Min SIP: ₹ 100` is weak; `Exit Load — Min SIP: ₹ 100` is strong. The heading is **not** written into `chunks.txt` body twice. |
| **Dropped chunks** | 12 words minimum + an explicit stop-phrase list | §1.4. `INVEST NOW`, `KNOW MORE`, `No data available`, `No result found!!`, `Table`, `Graph`, `DISCLAIMER` chrome. |
| **Floor exception** | 12-word floor does **not** apply under a fact heading | Found while implementing Phase 3: the ELSS page's entire `Exit Load` section is the single word `NIL`, and the plain floor deleted it — losing a PRD in-scope fact. `app/chunker.py:FACT_HEADINGS` exempts `exit load`, `entry load`, `lock in`, `ter`, `nav`, `aum`, `benchmark`, `riskometer`, `min sip`, `inception date`, `product suitability`, `downloads`, `fund managers`. |

**Realised in Phase 3** (`python -m app.chunker`): 63 chunks kept, 15 dropped (2 boilerplate, 13 under the floor), min/median/max 3 / 36 / 186 words, 0 chunks above the 200-word ceiling. Per page: home 26, flexi cap 24, ELSS 12, factsheets 1, fund documents 0.

### 4.1 Retrieval sanity check (Phase 4, before Phase 5 tuning)

Embedded the 63 chunks and queried the collection directly. Observed behaviour worth carrying into Phase 5:

| Question | Top-1 chunk | Note |
|----------|-----------|------|
| minimum SIP, flexi cap | the `What is the minimum SIP amount…` FAQ chunk, **d=0.157** | Exact heading match; the strongest hit in the corpus. |
| lock-in, ELSS | `About HDFC ELSS - Tax Saver Fund`, d=0.357 | Correct page. |
| expense ratio, flexi cap | `Overview`, d=0.244 | The TER chunk ranks **7th**. The word "expense ratio" is absent from the page — the AMC writes `TER` and `Total Expense Ration` — so the heading/scheme boost in Phase 5 is what will surface it. |
| capital-gains statement | `Mutual Fund Tools` (home), d=0.619 | Expected: the corpus has only the `Download A/c Statement` button label. Weakest match in the set, which is the honest signal that Phase 7 must answer this as "not in the ingested pages". |

Conclusion: the chunking is sound, but a **pure vector top-k is not enough for TER**. Phase 5's `heading`/`scheme` re-rank is required, not optional, for the expense-ratio question in PRD §5.2 to work.

### 4.2 Re-rank as built in Phase 5 (`app/retrieve.py`)

The whole 63-chunk collection is scored on every query (one local HNSW scan is sub-millisecond), then the best `TOP_K = 4` are returned after re-ranking. Scoring every chunk — rather than an over-fetch of 16 — is what makes the re-rank reliable: a partial candidate set hides both the key-facts card and the 3-word `Exit Load / NIL` chunk, neither of which resembles the wording of a question about them.

Three signals, subtracted from the cosine distance. They **promote, never filter**, so any chunk the vector search found stays reachable:

| Signal | Boost | Fires when |
|--------|-------|-----------|
| `scheme` | 0.35 | the question names a scheme and the chunk's `scheme` metadata agrees |
| `heading` | 0.30 | the chunk's heading states the thing asked about, e.g. heading `Exit Load` |
| `fact` | 0.55 | the chunk body states the figure in the AMC's own label (`Total Expense Ration`, `AUM`, `Riskometer`, `Lock in`) |
| `text` | 0.12 | the topic is mentioned but the chunk is not the labelled fact |

The `fact` tier exists because a *scheme* match is not sufficient evidence that a chunk answers the question. AUM for Flexi Cap is only in the home-page fund card, not the scheme page, so a scheme-only boost ranks a chunk from the wrong page above the one holding the number. `fact` deliberately outranks `scheme`.

Matching is whole-word, not substring: `ter` occurs inside "term", "later" and "alternative", so substring matching would re-rank a lock-in *term* question as if it were about TER. The last word of a phrase may carry a trailing "s" so `download` still matches the `Downloads` heading.

After the re-rank, all ten check questions return the correct chunk at rank 1:

| Question | Rank-1 chunk | Note |
|----------|--------------|------|
| expense ratio, flexi cap | `How to start investing` (key-facts card), d=0.448 | `0.77` in top-2; was rank 7 before the re-rank |
| lock-in, ELSS | `About HDFC ELSS - Tax Saver Fund`, d=0.357 | correct scheme |
| minimum SIP, flexi cap | `What is the minimum SIP amount…`, d=0.157 | heading match |
| exit load, ELSS | `Exit Load / NIL` | the 3-word chunk is now rank 1 |
| riskometer, flexi cap | key-facts card | `Very High` |
| benchmark, ELSS | `About HDFC ELSS - Tax Saver Fund` | benchmark name |
| fund manager, flexi cap | `What is HDFC Flexi Cap Fund?` | |
| SID/KIM download, flexi cap | `Downloads` | heading match |
| AUM, flexi cap | `Flexi Cap Fund` (home fund card), d=0.200 | fact on a different page than the scheme |
| capital-gains statement | `Downloads` (ELSS) | still the weakest hit, d=0.627 — the honest signal for Phase 7 |

Gate 5 (19 checks, all passing): TER chunk rank 1 and `0.77` in top-2; `NIL` chunk rank 1; scheme-aware top-2; all returned URLs inside `APPROVED_URLS`; `best_url` returns exactly one approved URL; `top_k` honoured and default `TOP_K=4`; ranking deterministic; `context_block` carries `url` and `fetched_at`; empty question returns `[]`; `ChromaEmptyError` raised against an empty collection; `ter`/`term` false-positive guard; and `app/retrieve.py` imports no `httpx`/`requests`/`loader` and contains no URL literal.

---

## 3. Metadata per chunk

Required by docs/architecture.md §5.2, plus two justified extras.

| Field | Source | Purpose |
|-------|--------|---------|
| `url` | `config.APPROVED_URLS` entry | The single citation for answers (PRD FR-3) |
| `title` | `<title>` / first `h1` | Display and ranking hint |
| `scheme` | `config.URL_SCHEME_MAP` | `hdfc-flexi-cap`, `hdfc-elss-tax-saver`, `hdfc-fund-hub`; lets the retriever prefer the right scheme page (architecture §6.3) |
| `fetched_at` | ISO-8601 at load | Drives `Last updated from sources:` — never the model's notion of time |
| `heading` | **extra** | §1.1: the recovered `h2`/`h3`. Lets a retrieved chunk say *where* the fact lives, and lets the Phase 5 retriever boost a chunk whose heading matches the question (`Exit Load` for an exit-load question). |
| `chunk_index` | **extra** | Stable, position-ordered id so re-ingest is idempotent and `chunks.txt` reads in document order. |

Chunk `id` = `sha1(f"{url}#{chunk_index}")[:16]` (architecture §5.4). Not `hash(text)`: text changes whenever the AMC edits a number, and ids must stay stable so a re-ingest overwrites instead of duplicating.

Not stored per chunk: `embedding` (Chroma-only), `text` (stored as Chroma `documents` and the `chunks.txt` body).

---

## 4. Line grammar between loader and chunker

`extract_sections` emits a deliberately small grammar so Phase 3 has no HTML to re-parse:

| Line | Meaning |
|------|---------|
| `## <heading>` | Section boundary. Starts a new chunk; supplies the `heading` field. |
| `Label: value` | One atomic key-fact row. **Never split.** |
| anything else | Body line, packed until the target size. |

---

## 5. Worked example

From the flexi-cap page, after loading and chunking:

```
=== CHUNK 12 ===
id: 3f9c1a04be27d511
url: https://www.hdfcfund.com/explore/mutual-funds/hdfc-flexi-cap-fund/direct
title: HDFC Flexi Cap Fund – Direct Plan | NAV, Returns & SIP | HDFC Mutual Fund
scheme: hdfc-flexi-cap
fetched_at: 2026-09-29T19:51:57+00:00
heading: Exit Load
chunk_index: 12
---
Exit Load
In respect of each purchase / switch-in of Units, an Exit Load of 1.00% is payable
if Units are redeemed / switched-out within 1 year from the date of allotment.
No Exit Load is payable if Units are redeemed / switched-out after 1 year from the
date of allotment.
```

Query `What is the exit load on HDFC Flexi Cap Fund?` embeds near this chunk; the
`heading: Exit Load` boost plus the scheme match put it in top-k; the LLM gets
1.00% with the one-year condition attached, and cites this URL.

---

## 6. Known limits of this strategy

1. **Pages 4 and 5 contribute almost nothing.** `mutual-funds/factsheets` yields **1 chunk** (52 words of hub copy); `mutual-funds/fund-documents` yields **0** — it fetches fine (HTTP 200) but renders `No result found!!` because its list is client-side. Everything factual comes from pages 1–3, which contribute 62 of 63 chunks. **Corrected from an earlier draft of this file**, which claimed riskometer levels and expense-ratio tables were absent from the corpus. They are present and answerable: `Riskometer: Very High` and `Total Expense Ratio\n0.77` were both verified live in Phase 10. What genuinely is missing is capital-gains/account-statement download instructions, fund-manager names and SEBI registration numbers — all confirmed absent and all correctly declined. Do not add a sixth URL (PRD §4.2).
2. **Expense ratio is recoverable, but only from the scheme pages.** The flexi-cap card carries `TER ... 0.77` and ELSS `TER ... 1.21`. The `1.21` on a Direct–Growth page is the figure the AMC's own page shows for the ELSS scheme — treat TER numbers as "quote what the page says" and let Phase 7's system prompt do the rest rather than interpreting the plan variant.
3. **`Benchmark Performance` sections are table headers with no numbers** (66 words on both scheme pages; values load client-side, and the page literally reads `No data available`). They are kept, because "not disclosed on the page" is a legitimate grounded answer, but they will rarely win top-k.
4. **The key-facts card sits under a misleading heading** — `How to start investing` on the flexi-cap page, `About HDFC ELSS - Tax Saver Fund` on ELSS. The `scheme` and `url` metadata, not the `heading`, must carry scheme-level retrieval; do not trust the heading for the card.
5. **Home-page chunk quality is low** (29% boilerplate). It is kept for the `Download A/c Statement` tool label and navigation context. If top-k starts returning home-page noise, raise the min-size and extend the stop-phrase list rather than dropping the page.
6. **160/200 words is tuned to MiniLM's 256-token window.** Switching to a 512- or 1024-token embedding model means redoing §2; the section-level split stays valid, only the packing target changes.
