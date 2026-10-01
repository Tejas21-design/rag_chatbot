# Source pages

The corpus is exactly five URLs, fixed in `app/config.py` as `APPROVED_URLS` (PRD §4.2).
Nothing else is ever fetched, at ingest or at query time. Measured chunk counts are
from `data/chroma/` after the Phase 10 clean-room ingest.

| # | URL | Chunks | Role |
|---|-----|-------:|------|
| 1 | `https://www.hdfcfund.com/` | 26 | **Fund hub.** Fund listings, NAV/AUM/inception/riskometer summary tiles, investor-count and category marketing copy. The single largest contributor, and the source of most NAV and AUM figures. |
| 2 | `https://www.hdfcfund.com/explore/mutual-funds/hdfc-flexi-cap-fund/direct` | 24 | **Flexi Cap scheme page.** TER, min SIP, entry/exit load, lock-in, benchmark (NIFTY 500 TRI), riskometer, "Ideal for" suitability, and the benchmark-performance table. |
| 3 | `https://www.hdfcfund.com/explore/mutual-funds/hdfc-elss-tax-saver-fund/direct` | 12 | **ELSS Tax Saver scheme page.** Three-year statutory lock-in, TER, min SIP ₹500, NIL exit load, riskometer, benchmark. |
| 4 | `https://www.hdfcfund.com/mutual-funds/factsheets` | 1 | **Factsheets index.** Pointer to monthly factsheet PDFs, plus the factsheet FAQ copy. This is the educational link a performance refusal sends the user to. |
| 5 | `https://www.hdfcfund.com/mutual-funds/fund-documents` | **0** | **Fund-documents index.** Fetched successfully (HTTP 200) but rendered only `No result found!!` — the document list is built client-side in JavaScript, so there is no text to chunk. |

**Total: 63 chunks.** 15 candidates were dropped before embedding (13 under the 12-word
minimum, 2 classified as boilerplate). See [`chunking.md`](chunking.md) for the strategy
and the measurements behind these numbers.

## Consequence of page 5 being empty

This is a real gap, not a rounding error, and it shapes what the bot can answer:

- **Questions about downloading a capital-gains or account statement are unanswerable**,
  and the honest "not in the official pages" reply is the correct behaviour. The pages
  never described that flow in static HTML.
- **Where to find an SID or KIM** resolves to the factsheets index (page 4) instead.
- `app/ingest.py` now logs a warning for any approved page that yields zero chunks, so
  this cannot silently degrade again. It fetched fine, so nothing errored — and a silent
  empty page looks exactly like a working one from the outside.

## Why these five and not more

The PRD fixes the corpus at five URLs precisely so that "the answer is not in the pages"
stays a *correct* answer. Widening it would weaken the central guarantee, and HDFC's own
site renders most of its detail client-side, so additional URLs would likely return thin
text anyway. The two scheme pages carry the substance; the hub pages carry the summary
figures.

## Related links (never ingested)

`app/config.py` also holds two URLs that appear in refusals but are never scraped:

| Constant | URL | Used for |
|----------|-----|----------|
| `EXPLORE_URL` | `https://www.hdfcfund.com/explore/mutual-funds` | out-of-scope refusal ("browse the approved list of schemes") |
| `FACTSHEETS_URL` | `https://www.hdfcfund.com/mutual-funds/factsheets` | advice and performance refusal |

A refusal links the page that would actually help the user, which is not necessarily the
page the question would have been answered from — nothing was retrieved for a refused
question.
