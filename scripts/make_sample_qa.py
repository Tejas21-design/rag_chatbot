"""Regenerate the transcripts in sample_qa.md.

    python scripts/make_sample_qa.py > /tmp/qa.json

Needs a Groq key and costs ~13 API calls. sample_qa.md is committed with real
output; this script exists so that output can be refreshed when the model or the
corpus changes, rather than being trusted indefinitely.
"""

import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.answer import answer_question

QS = [
    ("What is the expense ratio of HDFC Flexi Cap Fund (Direct–Growth)?", "PRD example 1"),
    ("What is the lock-in for HDFC ELSS Tax Saver?", "PRD example 2"),
    ("How do I download a capital-gains / account statement from HDFC Mutual Fund?", "PRD example 3, not in corpus"),
    ("What is the minimum SIP for HDFC Flexi Cap Fund?", "SIP from corpus"),
    ("What is the exit load on HDFC ELSS Tax Saver?", "exit load from corpus"),
    ("What is the riskometer level of HDFC Flexi Cap Fund?", "riskometer from corpus"),
    ("What is the benchmark of HDFC Flexi Cap Fund?", "benchmark from corpus"),
    ("Who is the fund manager of HDFC Flexi Cap Fund?", "not in corpus"),
    ("What is the SEBI registration number of HDFC Mutual Fund?", "not in corpus"),
    ("Should I buy HDFC Flexi Cap Fund right now?", "advice refusal"),
    ("Which fund gave better returns last year?", "performance refusal"),
    ("What is the expense ratio of HDFC Mid-Cap Fund?", "out-of-scope refusal"),
    ("Tell me about SBI Large Cap Fund", "other AMC refusal"),
]

out = []
for q, note in QS:
    r = answer_question(q)
    out.append({
        "q": q, "note": note, "answer": r["answer"],
        "cite": r.get("citation_url"),
        "date": r.get("last_updated_from_sources") or "",
        "refused": r["refused"],
        "debug": {k: v for k, v in (r.get("debug") or {}).items() if k != "chunks"},
    })
    print("done:", q[:50], flush=True)

print("\n@@@JSON@@@")
print(json.dumps(out, indent=2))
