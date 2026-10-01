# Architecture

**Product:** HDFC Mutual Fund FAQ Assistant (RAG Chatbot)  
**Audience:** Class demo  
**Status:** Draft  
**Last updated:** 2026-09-30  
**Source of truth (product):** `docs/PRD.md`

This document describes **how** the system is built. Product rules (facts-only, five URLs, UI, refusals) live in the PRD. Implementation must keep **ingestion** and **query** as two separate pipelines.

---

## 1. Design principles

1. **Two RAG stages, always.** Ingest once (or on explicit re-run). Query never re-scrapes the five URLs.
2. **Same embedding space.** Chunks and questions use `sentence-transformers/all-MiniLM-L6-v2` (384-d, local, no API key).
3. **Grounded generation.** Groq sees retrieved chunks only. No web search at query time.
4. **Inspectable corpus.** Humans can read chunks from a `.txt` file without opening Chroma.
5. **Secrets stay local.** Groq key in `.env`, never in Git.
6. **Guardrails before retrieve.** Advice, returns-comparison, and PII do not need a full RAG round-trip (except an official educational URL when refusing advice).

---

## 2. System context

```
┌─────────────┐     question      ┌──────────────────┐     GROQ_API_KEY
│  Demo user  │ ───────────────►  │  Tiny chat UI    │ ◄──── .env (local)
└─────────────┘                   └────────┬─────────┘
                                           │
                                           ▼
                                  ┌──────────────────┐
                                  │  Query service   │
                                  │  (embed+retrieve │
                                  │   + Groq)        │
                                  └────────┬─────────┘
                         vectors           │
              ┌────────────────────────────┼────────────────────────────┐
              │                            ▼                            │
              │                   ┌─────────────────┐                   │
              │                   │  ChromaDB disk  │                   │
              │                   │  (persisted)    │                   │
              │                   └────────▲────────┘                   │
              │                            │ upsert                     │
              │                   ┌────────┴────────┐                   │
              │                   │ Ingest job      │                   │
              │                   │ Load→Chunk→     │                   │
              │                   │ Embed→Store     │                   │
              │                   └────────▲────────┘                   │
              │                            │ HTTP GET (ingest only)     │
              │              ┌─────────────┴─────────────┐              │
              │              │  5 public hdfcfund.com    │              │
              │              │  pages (approved corpus)  │              │
              │              └───────────────────────────┘              │
              │                   also writes chunks.txt                │
              └─────────────────────────────────────────────────────────┘
```

**Trust boundary:** The five HDFC URLs are the only knowledge source. Groq is a generator, not a knowledge base. The UI never sends PAN, Aadhaar, account numbers, OTPs, emails, or phones into logs or the LLM if they can be stripped first.

---

## 3. Locked stack

| Layer | Choice | Notes |
|-------|--------|--------|
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` | Local; 384 dimensions; used for **chunks and queries** |
| Vector store | ChromaDB | Persist directory on disk; load on app start |
| LLM | Groq | Chat completions; model ID chosen at implementation, listed in README |
| Config | `.env` | `GROQ_API_KEY`; `.env.example` committed, `.env` gitignored |
| Corpus | 5 HDFC URLs | See PRD §4.2 |

**Not locked:** UI framework, Groq model ID, `top-k`, exact chunk size/overlap (must be set **after inspecting loaded HTML**, then documented).

---

## 4. Logical components

| Component | Pipeline | Responsibility |
|-----------|----------|----------------|
| **Loader** | Ingest | GET the five URLs; extract visible text/title; record `fetched_at` |
| **Chunker** | Ingest | Split text per documented strategy; attach metadata |
| **Chunk dump** | Ingest | Write all chunk text + metadata to a readable `.txt` |
| **Embedder** | Both | MiniLM encode; shared module so ingest and query cannot drift |
| **Vector store** | Both | Chroma collection: documents, embeddings, metadata, ids |
| **Guardrails** | Query | Advice / PII / returns-compare / out-of-scope |
| **Retriever** | Query | Embed question → similarity search → top-k chunks |
| **Prompt builder** | Query | System + retrieved context + user question + output contract |
| **Generator** | Query | Groq; parse answer + pick one citation URL + last-updated |
| **Tiny UI** | Query | Welcome, 3 examples, facts-only note, chat, citation, disclaimer |

Keep ingest as a **CLI job or script** (`ingest` / `python -m ...ingest`). Keep query as the **app process**. Do not call the loader from the chat request path.

---

## 5. Pipeline A — Data ingestion

Runs **once** (or when an operator explicitly re-ingests). Does **not** run on every process start if the Chroma persist directory already has the collection.

```
Load → Chunk → Embed → Store
         └── also → chunks.txt
```

### 5.1 Load

- Fetch only the five approved URLs (PRD §4.2).
- Parse HTML to plain text (strip nav/boilerplate as far as practical).
- Capture: `url`, `title`, `raw_or_clean_text`, `fetched_at`.
- Do not follow arbitrary links off-site. Do not use Groww or blogs as sources.
- v1: stay on these five HTML pages unless a page is unusable without an official same-domain PDF linked from that page (see PRD open items).

### 5.2 Chunk (gated)

**Before writing chunker code:** inspect loaded text (length, headings, tables, noise). Then lock:

- chunk size
- overlap
- metadata fields
- why this fits **this** HTML corpus

Write that to `docs/chunking.md` (or README). Until that inspect step happens, size/overlap remain **TBD**.

**Minimum metadata per chunk**

| Field | Purpose |
|-------|---------|
| `url` | Single citation source for answers |
| `title` | Display / ranking hint |
| `scheme` | Flexi cap / ELSS / unknown |
| `fetched_at` | Drives `Last updated from sources:` |

Optional extra fields only if the chunking note justifies them (e.g. `heading`, `chunk_index`).

### 5.3 Embed

- Batch-encode chunk strings with MiniLM.
- Do not use a different model or an embedding API.

### 5.4 Store

- Upsert into Chroma with a stable `id` (e.g. hash of `url` + chunk index).
- Persist path e.g. `data/chroma/` (exact path in README).
- On app start: `PersistentClient(path=...)` and **skip ingest** if collection is populated.

### 5.5 Inspectable dump

- After chunking (before or after embed, but must include full chunk text + metadata), write `data/chunks.txt` (or similar).
- Format must be readable in a text editor (separators between chunks).

**Idempotency:** Re-ingest should overwrite or reset the collection so demo data does not duplicate silently. Document the command.

---

## 6. Pipeline B — Data retrieval (query)

Runs **on every user question**. Does **not** HTTP-fetch hdfcfund.com.

```
Question → Guardrails → Embed → Retrieve top-k → Groq → Answer (+ citation + last updated)
```

### 6.1 Guardrails (pre-retrieve)

Order of checks (simple keywords and/or a small classifier; Groq optional but keep it cheap):

| Intent | Action |
|--------|--------|
| PII (PAN, Aadhaar, account, OTP, email, phone) | Refuse to store; tell user not to share; do not put PII in prompts/logs |
| Advice (buy/sell/hold, “best fund”, allocation) | Polite facts-only refusal + one official educational URL (factsheets or fund-documents) |
| Performance compare / CAGR compute | Do not calculate; point to official factsheet URL |
| Out-of-corpus AMC/scheme | Out-of-scope + explore/factsheets link |

If the question is a normal fact query, continue to retrieve.

### 6.2 Embed question

- Same MiniLM instance/module as ingest.
- Query vector must be 384-d.

### 6.3 Retrieve

- Chroma similarity search, `top-k` (choose at implementation; document in README).
- Results **only** from the ingested collection (the five URLs).
- Prefer chunks whose `scheme` or `url` matches a named scheme in the question (filter or re-rank if easy; not required for v1 if top-k already surfaces the right page).

**Empty / low-confidence retrieval:** Do not invent numbers. Say the pages do not contain it; cite the closest hub URL if useful.

### 6.4 Generate

**Inputs to Groq**

- System: facts-only; ≤3 sentences; never advice; never invent figures; never compute returns; answer only from context.
- Context: top-k chunk texts + each chunk’s `url` and `fetched_at`.
- User: the question.

**Outputs the UI must show**

1. Answer body (≤3 sentences)
2. **Exactly one** citation URL (best matching chunk `url`)
3. `Last updated from sources: <ingest fetched_at, not training cutoff>`

If Groq returns multiple links, the app still displays **one**.

---

## 7. Sequence (happy path)

```
User          UI           Guardrails    Embedder     Chroma       Groq
 │             │                │            │           │           │
 │  question   │                │            │           │           │
 │────────────►│                │            │           │           │
 │             │  check         │            │           │           │
 │             │───────────────►│            │           │           │
 │             │  ok            │            │           │           │
 │             │◄───────────────│            │           │           │
 │             │  embed q       │            │           │           │
 │             │────────────────────────────►│           │           │
 │             │  vector                     │           │           │
 │             │◄────────────────────────────│           │           │
 │             │  query                      │           │           │
 │             │────────────────────────────────────────►│           │
 │             │  chunks                     │           │           │
 │             │◄────────────────────────────────────────│           │
 │             │  prompt + context                       │           │
 │             │────────────────────────────────────────────────────►│
 │             │  completion                             │           │
 │             │◄────────────────────────────────────────────────────│
 │  answer +   │                │            │           │           │
 │  1 URL +    │                │            │           │           │
 │  last updated             │            │           │           │
 │◄────────────│                │            │           │           │
```

Ingest is **offline** relative to this diagram.

---

## 8. Data model

### 8.1 Source document (post-load)

```
{
  "url": "https://www.hdfcfund.com/...",
  "title": "...",
  "text": "...",
  "fetched_at": "ISO-8601"
}
```

### 8.2 Chunk (Chroma + chunks.txt)

```
{
  "id": "stable-id",
  "text": "chunk body",
  "embedding": [384 floats],   // Chroma only, not required in .txt
  "metadata": {
    "url": "...",
    "title": "...",
    "scheme": "hdfc-flexi-cap | hdfc-elss-tax-saver | unknown",
    "fetched_at": "ISO-8601"
  }
}
```

### 8.3 Query response (to UI)

```
{
  "answer": "≤3 sentences",
  "citation_url": "https://www.hdfcfund.com/...",
  "last_updated_from_sources": "ISO-8601 or human date",
  "refused": false
}
```

Refusal responses still include a facts-only message and, when required by the PRD, one educational official link.

---

## 9. Suggested repo layout

Exact names can change; the **separation of ingest vs app** should not.

```
ChatBot/
  docs/
    PRD.md
    architecture.md          ← this file
    problemstatement.txt
    chunking.md              ← after inspecting loaded pages
  data/
    chroma/                  ← persisted Chroma (gitignored if large)
    chunks.txt               ← inspectable dump (may commit a sample)
  src/  or app/
    ingest.py                ← Load → Chunk → Embed → Store
    embedder.py              ← shared MiniLM
    retrieve.py
    generate.py              ← Groq
    guardrails.py
    app.py                   ← tiny UI
  .env.example
  .gitignore                 ← .env, chroma if needed
  README.md
```

---

## 10. Configuration

| Variable / path | Used by | Secret? |
|-----------------|---------|---------|
| `GROQ_API_KEY` | Generator | Yes |
| `GROQ_MODEL` (optional) | Generator | No |
| Chroma persist dir | Ingest + query | No |
| `TOP_K` (optional) | Retriever | No |

Never commit `.env`. Never call Groq at ingest time (ingest is local embed only).

---

## 11. Failure modes

| Failure | Behavior |
|---------|----------|
| Chroma empty on query | Error in UI: run ingest first |
| HTTP failure during ingest | Fail the job; do not store a half-corpus without documenting it |
| Groq down / bad key | UI error; do not fall back to ungounded model knowledge |
| Retrieval miss | Honest “not in official pages” + hub link |
| User pastes PII | Strip/refuse; no persist |

---

## 12. Security and compliance (demo)

- Public pages only; no AMC back-end screenshots.
- No identity, no chat history keyed to a person.
- Embeddings stay on disk locally; Groq receives question + retrieved public text only (after PII strip).
- UI disclaimer: PRD §12.

---

## 13. What this architecture does not decide

Document these when implementing; they do not change the two-pipeline shape:

- Chunk size, overlap, extra metadata (after data inspect)
- Groq chat model ID
- `top-k`
- Streamlit vs Gradio vs small web UI
- Hosting vs local-only demo

---

## 14. Traceability to PRD

| PRD | Architecture |
|-----|----------------|
| §6 RAG stages | §5 ingest, §6 query |
| §7 locked stack | §3 |
| FR-1 … FR-6 | Components §4, pipelines §5–6, config §10 |
| Guardrails / refusals | §6.1 |
| No re-scrape on question | System context §2, query §6 |
