"""Phase 8: the Streamlit chat UI.

    streamlit run app/app.py

Calls `answer_question` and nothing else. There is no loader, no chunker and no
ingestion on this path, and the app refuses to render until the corpus exists
rather than ingesting on startup (architecture section 5.4) -- a re-ingest is
slow and rate-limited against a live site, and doing it implicitly would mean
restarting the UI re-scraped five pages.

Nothing is written to disk. Chat history lives in `st.session_state` for the
lifetime of the browser session and holds question text and answer text only.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Streamlit puts the *script's own directory* on sys.path, which means this
# file's folder contains `app.py` and shadows the `app` package: `from app
# import config` would resolve to this very file and raise a circular-import
# error. Putting the project root first makes the package win.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if sys.path[0] != _PROJECT_ROOT:
    sys.path.insert(0, _PROJECT_ROOT)

import streamlit as st  # noqa: E402

from app import config, store  # noqa: E402
from app.answer import answer_question  # noqa: E402
from app.builder import CorpusBuilder, deadline_note  # noqa: E402
from app.generate import GroqUnavailableError  # noqa: E402

st.set_page_config(page_title="HDFC MF FAQ Assistant", layout="centered")

EXAMPLE_QUESTIONS = [
    "What is the expense ratio of HDFC Flexi Cap Fund (Direct–Growth)?",
    "What is the lock-in for HDFC ELSS Tax Saver?",
    "How do I download a capital-gains / account statement from HDFC Mutual Fund?",
]

DISCLAIMER = (
    "**Facts-only. No investment advice.**\n\n"
    "This assistant answers from a small set of **public HDFC Mutual Fund pages**. "
    "It is for education and a class demo only. It is **not** SEBI-registered advice, "
    "not a recommendation to buy or sell, and not a substitute for the Scheme "
    "Information Document (SID), Key Information Memorandum (KIM), or your advisor. "
    "Numbers can change; always verify on the official site. Do not share PAN, "
    "Aadhaar, account numbers, OTPs, or other personal data here."
)


def render_answer(response: dict) -> None:
    """One bot message: body, one link, the date, and an optional sources list."""
    st.markdown(response["answer"])

    # Exactly one link per answer, from chunk metadata and never from the model.
    # A refusal links the relevant official page instead of the cited corpus page,
    # because nothing was retrieved for it.
    if response.get("citation_url"):
        st.link_button("Source", response["citation_url"])

    if response.get("last_updated_from_sources"):
        st.caption(response["last_updated_from_sources"])

    debug = response.get("debug") or {}
    if debug.get("reason") == "model_declined":
        st.caption(
            "The ingested pages do not cover this. The link above is the official "
            "factsheet page, not a source for the answer."
        )
    if debug.get("reason") in {"low_confidence", "ungrounded_number"}:
        st.caption(
            "No grounded answer was found in the ingested pages, so no figure is shown."
        )
    if debug.get("ungrounded_numbers"):
        st.caption(f"Figures not found in the source pages: {', '.join(debug['ungrounded_numbers'])}")

    chunks = debug.get("chunks") or []
    if chunks:
        with st.expander(f"Sources ({len(chunks)} passage(s) used)"):
            for i, chunk in enumerate(chunks, 1):
                st.markdown(
                    f"**{i}. distance {chunk['distance']:.3f}** "
                    f"— {chunk.get('scheme') or 'page'} · {chunk.get('heading') or 'section'}"
                )
                st.markdown(f"[{chunk['url']}]({chunk['url']})")


def ask(question: str) -> None:
    st.session_state["messages"].append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Reading the HDFC pages…"):
            try:
                response = answer_question(question)
            except GroqUnavailableError as exc:
                st.error(str(exc))
                st.caption("Add GROQ_API_KEY to .env at the project root and restart.")
                return
    render_answer(response)
    st.session_state["messages"].append({"role": "assistant", "content": response["answer"]})


_status = store.population_status()
if not _status["populated"]:
    # The corpus is built during `render deploy`, but a host with an ephemeral
    # filesystem may not carry build output into the running container. Building
    # it here is the difference between a working demo and an empty screen.
    # It runs on a thread so the page stays responsive and shows which page it is
    # on; blocking the script on a 60 s scrape is what produced a spinner that
    # never resolved, and Streamlit re-runs this script on every interaction.
    _auto = os.getenv("AUTO_INGEST_ON_START", "true").strip().lower() not in {
        "0",
        "false",
        "no",
    }

    @st.cache_resource(show_spinner=False)
    def _builder():
        return CorpusBuilder()

    if _auto and _status["error"] is None:
        _builder().start()

        @st.fragment(run_every="2s")
        def _show_progress():
            _snap = _builder().snapshot()
            _st = _snap["status"]
            if _st["populated"]:
                st.rerun()
                return
            st.info(
                f"Building the corpus from the HDFC pages — "
                f"{_snap['lines'][-1] if _snap['lines'] else 'starting'}"
            )
            for _line in _snap["lines"]:
                st.caption(_line)
            if _snap["error"]:
                st.error(_snap["error"])
                st.caption(deadline_note())

        _show_progress()
        st.stop()

    st.error("Corpus not ingested.")
    st.code("python -m app.ingest", language="bash")
    st.caption("Then restart. Ingestion is a one-time step and is not run on app start.")
    if _status["error"]:
        st.caption(f"Chroma could not be read: `{_status['error']}`")
        st.caption(
            f"Expected `{config.CHROMA_DIR}` to hold `{config.COLLECTION_NAME}`. "
            "If that directory is empty, the build step did not persist its output."
        )
    st.stop()

if "messages" not in st.session_state:
    st.session_state["messages"] = []

st.title("HDFC Mutual Fund FAQ Assistant")
st.caption(
    "Answers from "
    f"{len(config.APPROVED_URLS)} public HDFC Mutual Fund pages. Facts only, no advice."
)

st.info(
    "**Facts-only. No investment advice.** Answers come only from the ingested "
    "HDFC pages. If the answer is not there, this says so rather than guessing."
)

with st.expander("Disclaimer"):
    st.markdown(DISCLAIMER)

for message in st.session_state["messages"]:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if not st.session_state["messages"]:
    st.markdown("Try one of these:")
    for i, example in enumerate(EXAMPLE_QUESTIONS):
        if st.button(example, key=f"example_{i}"):
            ask(example)

if prompt := st.chat_input("Ask a factual question about HDFC Mutual Fund schemes"):
    ask(prompt)
