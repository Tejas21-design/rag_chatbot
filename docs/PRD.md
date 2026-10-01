# Product Requirements Document (PRD)

**Product:** HDFC Mutual Fund FAQ Assistant (RAG Chatbot)  
**Audience:** Class demo  
**Status:** Draft for implementation  
**Last updated:** 2026-09-30  
**Source brief:** `docs/problemstatement.txt`

---

## 1. Problem

Retail users and support/content teams repeatedly ask the same factual questions about mutual fund schemes: expense ratio, exit load, minimum SIP, ELSS lock-in, riskometer, benchmark, and how to download statements.

Today those answers live across AMC factsheets, scheme pages, and document hubs. Users either guess from unofficial blogs or get investment *advice* mixed with facts.

We will ship a small **facts-only RAG chatbot** that answers from a scoped set of **official public HDFC pages**, cites a source on every reply, and refuses advice.

The architecture must implement **both RAG stages as separate pipelines**: **data ingestion** and **data retrieval** (query-time generation). Answers use **only** the provided source data.

---

## 2. Goals

| Goal | Success look like |
|------|-------------------|
| Full RAG, two stages | Ingestion (load → chunk → embed → store) runs independently of query (question → embed → retrieve → LLM → answer) |
| Demo-ready chatbot | A working chat UI that retrieves from the approved corpus and answers in ≤3 sentences |
| Trust | Every answer includes **one citation URL** and a **last-updated-from-sources** line |
| Safety | No investment advice, no performance computation, no PII collection |
| Class deliverables | Prototype (or ≤3-min demo video), source list, README, sample Q&A, UI disclaimer, inspectable chunks file |

**Non-goals:** portfolio recommendations, return comparisons, live AMC backend access, multi-AMC coverage, authenticated account data, cloud embedding APIs.

---

## 3. Users

| Persona | Need |
|---------|------|
| Retail investor (demo user) | Fast facts while comparing a few HDFC schemes |
| Support / content (demo story) | Repeatable answers with a source link |
| Instructor / classmates | See a complete RAG pipeline: ingest official pages once, retrieve at query time, generate a grounded answer |

---

## 4. Scope

### 4.1 AMC and schemes

- **AMC:** HDFC Mutual Fund (`hdfcfund.com`)
- **Schemes (target 3–5, Direct–Growth where applicable):** include at least:
  - One **flexi-cap** (HDFC Flexi Cap Fund)
  - One **ELSS** (HDFC ELSS Tax Saver)
  - Additional schemes only if they appear on the approved public pages (e.g. large-cap if documented in the same corpus)

If a scheme is not represented in the five source URLs, the assistant must say it does not have that scheme in scope and point to the official explore/factsheet hubs.

### 4.2 Approved corpus (exactly these 5 public URLs)

| # | URL | Role in RAG |
|---|-----|-------------|
| 1 | https://www.hdfcfund.com/ | AMC home / navigation context |
| 2 | https://www.hdfcfund.com/explore/mutual-funds/hdfc-flexi-cap-fund/direct | Flexi-cap scheme facts (Direct Plan) |
| 3 | https://www.hdfcfund.com/explore/mutual-funds/hdfc-elss-tax-saver-fund/direct | ELSS scheme facts, Direct Plan (lock-in, tax saver) |
| 4 | https://www.hdfcfund.com/mutual-funds/factsheets | Official factsheets (expense ratio, riskometer, benchmark, etc.) |
| 5 | https://www.hdfcfund.com/mutual-funds/fund-documents | KIM/SID and fund documents |

**Source rules**

- Public sources only.
- No screenshots of app backends.
- No third-party blogs (Groww, Morningstar, YouTube, etc.) as retrieval sources.
- SEBI/AMFI pages are allowed *only if* we later expand the corpus; **v1 corpus is the five URLs above**.
- The LLM must answer using **only** retrieved chunks from this corpus (no extra web search at query time).

---

## 5. Product experience

### 5.1 Tiny UI (must-have)

- Welcome line explaining the assistant.
- **Three example questions** (clickable or copy-paste).
- Persistent note: **“Facts-only. No investment advice.”**
- Chat input + message list.
- Each bot reply shows:
  - Answer (≤3 sentences)
  - **One** source link
  - `Last updated from sources: <date or crawl timestamp>`
- Disclaimer snippet in the UI (same as deliverable).

Suggested example questions:

1. What is the expense ratio of HDFC Flexi Cap Fund (Direct–Growth)?
2. What is the lock-in for HDFC ELSS Tax Saver?
3. How do I download a capital-gains / account statement from HDFC Mutual Fund?

### 5.2 Answer types (in scope)

The assistant **must** attempt a grounded answer for:

- Expense ratio  
- Exit load  
- Minimum SIP / lumpsum (if stated on source pages)  
- ELSS lock-in  
- Riskometer  
- Benchmark  
- How to download statements / capital-gains / tax documents (process as published, not account login)

### 5.3 Refusals

| User intent | Behavior |
|-------------|----------|
| Buy/sell/hold, “best fund”, “should I invest”, allocation | Polite **facts-only** refusal; optional **one educational official link** (e.g. factsheets or fund documents) |
| Returns, CAGR, “which performed better” | Do **not** compute or compare. Direct user to the **official factsheet** URL |
| PAN, Aadhaar, account number, OTP, email, phone | Do **not** accept or store. Tell the user not to share PII; continue without those fields |
| Scheme / AMC outside corpus | Out-of-scope message + link to hdfcfund.com explore/factsheets |

---

## 6. RAG architecture (required)

Stages are **different**. Architecture and code must follow **all** RAG stages: **data ingestion** and **data retrieval**.

```
Ingestion (once, persisted):
  Load → Chunk → Embed → Store in Vector DB

Query (every question):
  Question → Embed → Retrieve top chunks → LLM → Answer
```

### 6.1 Ingestion pipeline

| Step | Requirement |
|------|-------------|
| **Load** | Fetch and parse the five approved public pages. |
| **Chunk** | Split loaded text using a documented strategy (see FR-1). |
| **Embed** | Embed every chunk with the locked embedding model. |
| **Store** | Persist vectors + metadata in ChromaDB on disk. |

Ingestion **must not** re-run on every app restart. Restart loads the existing ChromaDB directory.

### 6.2 Query pipeline

| Step | Requirement |
|------|-------------|
| **Question** | User question from the tiny UI (after guardrails). |
| **Embed** | Embed the question with the **same** embedding model as chunks. |
| **Retrieve** | Top-k similar chunks from ChromaDB (approved corpus only). |
| **LLM** | Groq generates the answer from retrieved context only. |
| **Answer** | ≤3 sentences, one citation URL, last-updated line. |

---

## 7. Tech constraints (locked)

These are product/engineering constraints for the class demo, not optional.

| Area | Constraint |
|------|------------|
| **Embedding model** | `sentence-transformers/all-MiniLM-L6-v2` |
| **Why this model** | Runs **locally**, **no API key**, **384-dimension** vectors |
| **Same model both sides** | Use this model to embed **chunks** and the **user question** |
| **Chunking** | Chosen **after inspecting the loaded data**, before writing ingestion code. Document: why it fits this corpus, **chunk size**, **overlap**, and **metadata per chunk**. Persist **all chunks** to a readable **`.txt`** file for inspection |
| **Vector DB** | **ChromaDB**, **persisted to disk** |
| **LLM** | **Groq** |
| **Secrets** | Groq API key in **`.env`**, **never committed** to Git |

Groq **model name** (e.g. which Llama/Mixtral SKU) can be chosen at implementation and listed in README; the **provider is Groq**.

---

## 8. Functional requirements

### FR-1 Ingest (Load → Chunk → Embed → Store)

- Fetch and parse the five approved pages (HTML; follow only those URLs unless a page is a listing that still counts as the same five). The AMC's CDN returns **403** to clients that do not look like a real browser navigation: ingest must send a full browser header set over HTTP/2 (see `app/config.py:BROWSER_HEADERS`). Verified 2026-09-30.
- **Before coding chunking:** inspect the loaded text; propose chunk size, overlap, and metadata; record the rationale (README or `docs/chunking.md`).
- Chunk text accordingly (likely heading-aware splits for scheme pages, tables, and FAQ-like blocks).
- Required metadata per chunk (minimum): `url`, `title`, `scheme` (if known), `fetched_at`, plus any extra fields justified in the chunking note.
- Embed chunks with `sentence-transformers/all-MiniLM-L6-v2` (384-d).
- Upsert into **ChromaDB** on disk.
- Write **all chunks** (text + metadata) to a human-readable **`.txt`** file so they can be inspected without opening the DB.

### FR-2 Retrieve (Question → Embed → Retrieve)

- Embed the user question with the **same** MiniLM model.
- Return top-k chunks **only** from the persisted Chroma collection (the five-URL corpus).
- Prefer the most specific scheme page or factsheet chunk for scheme-named questions.

### FR-3 Generate (Retrieve → LLM → Answer)

- Call **Groq** with retrieved chunks as the only knowledge source.
- If retrieval is empty or low confidence: say so; still cite the closest official hub if useful.
- Answers ≤ **3 sentences**.
- **Exactly one** citation link per answer (the best matching source URL).
- Append last-updated line from ingest timestamp (not the LLM’s training cutoff).

### FR-4 Guardrails

- System prompt: facts only, no advice, no return math, no PII.
- Blocklist / classifier (simple keyword or LLM classifier) for advice and PII.
- Never invent numbers not present in retrieved chunks.

### FR-5 Sample Q&A pack

Ship 5–10 query/answer/link triples that match live prototype behavior (for grading and demo).

### FR-6 Secrets and local persist

- `.env` holds `GROQ_API_KEY` (or equivalent); `.gitignore` excludes `.env`.
- Ship `.env.example` with key names only.
- Chroma persist path documented in README; not re-ingested on every start.

---

## 9. Non-functional requirements

| Area | Requirement |
|------|-------------|
| Demo | Local run via README, or hosted link, or ≤3-minute video |
| Ingest vs query | Ingestion is a one-time (or explicit re-run) job; query path does not scrape the five URLs on each question |
| Latency | Aim for interactive demo (a few seconds per answer is acceptable; local MiniLM + Groq) |
| Accuracy | Prefer “I don’t see this in the official pages” over hallucination |
| Privacy | No logs of PAN/Aadhaar/account/OTP/email/phone; strip if pasted |
| Transparency | Citation + source date on every answer |
| Reproducibility | Chunks `.txt` + persisted Chroma + documented chunking strategy |
| Cost | Embeddings local (no embed API). Groq for generation only |

---

## 10. Constraints (hard)

1. **Public sources only** — five HDFC URLs in v1.  
2. **No PII** — do not accept or store PAN, Aadhaar, account numbers, OTPs, emails, or phone numbers.  
3. **No performance claims** — do not compute or compare returns; link factsheets.  
4. **Clarity** — ≤3 sentences; last-updated line.  
5. **No advice** — refuse opinionated / portfolio questions.  
6. **RAG completeness** — both ingestion and retrieval stages, as specified.  
7. **Locked stack** — MiniLM-L6-v2, ChromaDB on disk, Groq + `.env` (never committed).  
8. **Grounding** — answers from provided source data only.

---

## 11. Deliverables (class)

| Deliverable | Location / format |
|-------------|-------------------|
| Working prototype | App or notebook; or ≤3-min demo video |
| Source list | CSV or Markdown of the 5 URLs |
| README | Setup, AMC + schemes, known limits, how to ingest vs how to chat, Groq env var, Chroma path |
| Chunking note | Strategy, size, overlap, metadata, why it fits this HTML corpus (before or with first ingest code) |
| Inspectable chunks | Readable `.txt` of all chunks |
| Sample Q&A | 5–10 queries with answers + links |
| Disclaimer | Facts-only, no investment advice (in UI + README) |
| Secrets hygiene | `.env.example`; `.env` not in Git |
| This PRD | `docs/PRD.md` |

---

## 12. Disclaimer (canonical copy for UI)

**Facts-only. No investment advice.**  
This assistant answers from a small set of **public HDFC Mutual Fund pages**. It is for education and a class demo only. It is **not** SEBI-registered advice, not a recommendation to buy or sell, and not a substitute for the Scheme Information Document (SID), Key Information Memorandum (KIM), or your advisor. Numbers can change; always verify on the official site. Do not share PAN, Aadhaar, account numbers, OTPs, or other personal data here.

---

## 13. Success criteria (demo day)

- [ ] Ingestion pipeline exists: load → chunk → embed → Chroma persist.  
- [ ] Query pipeline exists: question → MiniLM embed → retrieve → Groq → answer.  
- [ ] App restart does **not** require a full re-ingest.  
- [ ] Chunks are inspectable in a `.txt` file.  
- [ ] User can ask at least three fact questions and get cited answers.  
- [ ] Advice-style question is refused with an educational official link.  
- [ ] Returns question does not invent CAGR; points to factsheet.  
- [ ] UI shows welcome, 3 examples, and the facts-only note.  
- [ ] README lists HDFC + schemes + limits + Groq `.env` setup.  
- [ ] Source list matches the five URLs in this PRD.  
- [ ] `.env` is not committed.

---

## 14. Open items (implementation, not product)

- Exact **chunk size, overlap, and extra metadata** — **must be proposed after inspecting loaded pages**, then locked in the chunking note.  
- Groq **chat model** ID.  
- `top-k` for retrieval.  
- Whether factsheet PDFs linked from the factsheets page are in-scope (v1: stay on the five HTML URLs unless a page is unusable without a linked official PDF from the same domain).  
- Hosting vs local-only demo.  
- UI framework (Streamlit, Gradio, small web app, etc.).

**Not open:** embedding model, vector DB, LLM provider, 384-d vectors, persist-on-disk, inspectable chunks file.

---

## 15. Out of scope

- Multi-AMC or Groww as a knowledge source  
- Login, KYC, transactions, live NAV APIs as product features  
- Personalized tax computation  
- Storing user chat history with identity  
- Comparing funds on returns or ranking “best” schemes  
- OpenAI / other hosted embeddings for this demo  
- Re-scraping the five URLs on every user question  
- Committing API keys  
