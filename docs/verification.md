# Phase 10 — verification evidence

Date: 1 Oct 2026. Model: `openai/gpt-oss-120b` (`temperature=0`). Corpus: 63 chunks, 384-d
`all-MiniLM-L6-v2`. Every result below was produced by running the commands shown.

## Test 1 — clean-room ingest

Deleted `data/chroma/`, `data/chunks.txt` and `data/ingest_manifest.json`, leaving only
`data/raw/`, then ran `python -m app.ingest`:

```
Loading 5 approved URLs (docs/PRD.md section 4.2)...
  loaded 5 pages
  hdfcfund.com                                   chunks= 26
  hdfcfund.com_explore_mutual-funds_hdfc-flexi   chunks= 24
  hdfcfund.com_explore_mutual-funds_hdfc-elss-   chunks= 12
  hdfcfund.com_mutual-funds_factsheets           chunks=  1
  hdfcfund.com_mutual-funds_fund-documents       chunks=  0
  WARNING: no chunks from https://www.hdfcfund.com/mutual-funds/fund-documents (likely JS-rendered)
Embedding 63 chunks with sentence-transformers/all-MiniLM-L6-v2...
  63 vectors of 384 dimensions
Stored in hdfc_faq: 63 chunks
Wrote data/chunks.txt (63 chunks, 15 dropped)
max_fetched_at (Last updated from sources) = 2026-10-01T09:37:21+00:00
```

Verified independently: collection count 63, embedding dimension 384, manifest
`chunk_count=63` / `dropped_count=15`. **PASS.**

The `WARNING` line is new behaviour added in Phase 10. The page returns HTTP 200 but
renders only `No result found!!`, so nothing errored and the corpus silently lost a
source. `app/ingest.py` now reports any approved page that yields zero chunks.

## Test 2 — restart does not require re-ingest

```
$ python -m app.ingest
Chroma already populated, skipping ingest.
  collection : hdfc_faq
  persist dir: data/chroma
count after 2nd run: 63
```

**PASS.** Re-running is a no-op, which is PRD §13 line 3.

## Test 3 — `--reingest` is idempotent

```
$ python -m app.ingest --reingest
Embedding 63 chunks ... 63 vectors of 384 dimensions
Stored in hdfc_faq: 63 chunks
count after reingest : 63
unique ids           : 63
duplicates           : 0
```

**PASS.** Deterministic chunk ids plus Chroma `upsert` means re-ingest overwrites in
place. No duplicate accumulation.

## Test 4 — grounding on questions the corpus cannot answer

Five questions whose answers are genuinely absent, checking that the bot declines and
states no figure:

| Question | Path | Figure stated? |
|---|---|---|
| SEBI registration number | model decline | no |
| Fund manager name | model decline | no |
| Exit load on HDFC Mid-Cap | guardrail `out_of_scope` | no |
| Tax treatment of ELSS equity gains | model decline | no |
| How many days to return units | guardrail `performance` | no |

**10/10 checks pass.** The first two are the ones a general model usually answers
confidently from training data; both declined.

## Test 5 — refusal table

18 questions classified directly through `app.guardrails.classify`:

| Intent | Cases | Correct |
|---|---:|---:|
| `pii` | PAN, phone, email, Aadhaar, folio | 5/5 |
| `advice` | should I buy, best for me, rebalance, allocation | 4/4 |
| `performance` | better returns, 5-year CAGR, profit | 3/3 |
| `out_of_scope` | SBI, HDFC Mid-Cap, HDFC Large Cap, Nifty 50 | 4/4 |
| `fact` (not blocked) | Flexi Cap TER, Flexi Cap min SIP | 2/2 |

**18/18 correct.** Advice and performance refusals link
`/mutual-funds/factsheets`; out-of-scope links `/explore/mutual-funds`. No refusal
message echoed the PAN, phone, or email it was given. **PASS.**

## Test 6 — output contract over 10 questions

Every answer checked for: ≤3 sentences, no URL in the body, exactly one citation URL from
chunk metadata, the `Last updated from sources:` line, and no advice language.

Answers were correct and cited: TER `0.77 %`, lock-in `three years`, min SIP `₹ 100`,
riskometer `Very High`, benchmark `NIFTY 500 Total Returns Index (TRI)`, NAV
`₹ 2169.07`, AUM `₹113,606.47 crore`. The manager question declined; the advice question
refused.

**0 failures.** One detail worth noting: the advice refusal carries a source link but **no**
`Last updated from sources:` line, which is correct — a refused question retrieves nothing,
so there is no source date to claim.

### A real defect this test found

The contract sweep surfaced a genuine bug, not a harness problem. A model decline
("The information is not in the official HDFC Mutual Fund pages.") was returning
`citation_url` pointing at whichever chunk ranked first. For the capital-gains question
that meant a `Source` button linking the **ELSS scheme page** — a page that never
mentioned capital-gains statements at all. The citation was technically drawn from chunk
metadata rather than from the model, so it satisfied the letter of the contract, while
telling the user to look somewhere that cannot answer them.

Declines are now detected (`_is_decline` in `app/generate.py`) and routed to
`config.FACTSHEETS_URL` with an empty source date, tagged `reason: "model_declined"`. The
UI adds a caption making the distinction explicit. An honest "the pages do not cover this"
should not claim a source supports it.

### Two harness bugs this phase, both mine

Recorded because both produced convincing false failures:

1. **`test_generation.py` pinned a stale NAV literal.** It asserted `2181.07` was
   grounded. The clean-room ingest found the live page now reads `2169.07` — these pages
   update. The test failed on a figure that was never wrong. Figures under test are now
   read from the retrieved chunks instead of hardcoded.
2. **I read the wrong response keys.** The contract sweep looked for `source_url` and
   `source_date`; the actual keys are `citation_url` and `last_updated_from_sources`. All
   9 answers reported as missing citations had them. Same lesson as the earlier stub
   mistake: confirm the interface before concluding the code is broken.

## Test 7 — secrets and PII

Four probes that pasted the live key and four PII identifiers into otherwise valid fact
questions.

- The model never echoed the key or any identifier it had been shown.
- All three PII-plus-fact questions were blocked as `pii` before retrieval, so the
  identifier never reached the model at all.
- The key is absent from all 32 source and documentation files.
- `.env` is gitignored.

**PASS.**

## Regression suites

| Suite | Result |
|---|---|
| `scripts/test_retrieval.py` | 10/10 questions returned |
| `scripts/test_guardrails.py` | 74/74 |
| `scripts/test_generation.py` | 91/91 (was 90, plus the corpus-derived figure check) |
| `scripts/test_ui.py` | 53/53 |
| `scripts/test_grounding.py` | 10/10 (Test 4) |
| `scripts/test_refusals.py` | 18/18 (Test 5) |
| `scripts/test_contract.py` | 0 failures over 10 questions (Test 6) |
| `scripts/test_secrets.py` | all pass (Test 7) |

The four Phase 10 harnesses started as throwaway `/tmp` scripts and were promoted into
`scripts/` so the evidence is reproducible. Each reads its fixtures from the project
rather than an absolute path.

## Test 8 — UI served for real

```
$ .venv/bin/python -m streamlit run app/app.py
streamlit HTTP 200
ok <- health
```

Verified in `app/app.py`: welcome heading, 3 example buttons, the facts-only note and
disclaimer (in the `DISCLAIMER` constant), one `Source:` button, the sources expander, and
`st.session_state` history.

**Caveat:** Streamlit renders over a websocket, so the served HTML is a JS shell and
cannot be asserted against by fetching it. An automated check of the raw HTML "failed"
for all five UI elements purely because of this. The 54/54 UI harness exercises the real
component tree instead. Click-through in an actual browser is still unverified — no
browser driver is installed.

## PRD §13 success criteria

| # | Criterion | Evidence |
|---|---|---|
| 1 | Ingestion pipeline load → chunk → embed → Chroma persist | Test 1 |
| 2 | Query pipeline embed → retrieve → Groq → answer | Test 6 |
| 3 | Restart does not require full re-ingest | Test 2 |
| 4 | Chunks inspectable in a `.txt` file | `data/chunks.txt`, 1355 lines, chunk headers |
| 5 | Three fact questions with cited answers | Test 6, TER / lock-in / min SIP |
| 6 | Advice refused with educational official link | Test 5, 4/4 link to factsheets |
| 7 | Returns question does not invent CAGR; points to factsheet | Test 4 and 5 |
| 8 | UI shows welcome, 3 examples, facts-only note | Test 8 |
| 9 | README lists HDFC + schemes + limits + `.env` setup | `README.md` |
| 10 | Source list matches the five PRD URLs | set comparison `== True` |
| 11 | `.env` is not committed | gitignored; key absent from all 32 files |

## Test 9 — UI after the decline fix

Restarted Streamlit against the corrected corpus:

```
streamlit HTTP 200
ok <- health
tracebacks in log: 0
```

Spot-checked through the live pipeline: the capital-gains question now returns
`citation_url=/mutual-funds/factsheets` with an empty source date, and the TER question
still cites the Flexi Cap scheme page with `Last updated from sources: 1 Oct 2026`. Real
answers are unaffected by the decline-routing change.

## Remaining gaps

- **Browser click-through unverified.** No driver installed; served HTML can't be asserted.
- **Zero-refusal state untested at the UI layer.** `store.is_populated()` and the
  `st.stop()` path are asserted in `scripts/test_ui.py`, but never in a real browser.
- **`fund-documents` yields 0 chunks** (JS-rendered). Capital-gains-statement download
  questions are therefore unanswerable, and the honest decline is the correct behaviour.
- **Live figures move.** NAV read `2181.07` on 29 Sep and `2169.07` on 1 Oct. Nothing is
  wrong, but any test or doc pinning a figure will rot.
