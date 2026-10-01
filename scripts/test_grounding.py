import sys, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.answer import answer_question
fails=0
def check(l,c,d=""):
    global fails
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f" -- {d}"))
    if not c: fails+=1

print("=== TEST 4: GROUNDING (answers genuinely absent from the corpus) ===\n")
UNANSWERABLE = [
    ("What is the SEBI registration number of HDFC Mutual Fund?", "no SEBI number anywhere"),
    ("Who is the fund manager of HDFC Flexi Cap Fund?", "no manager name in corpus"),
    ("What is the exit load on HDFC Mid-Cap Fund?", "scheme out of corpus"),
    ("What is the tax treatment of ELSS equity taxation?", "no tax content"),
    ("How many days did it take to return my mutual fund units?", "no settlement-time content"),
]
for q, why in UNANSWERABLE:
    r = answer_question(q)
    body = r["answer"]
    # Three legitimate decline paths: a guardrail refusal (instant, refused=True),
    # or the model saying the pages lack it (varied wording).
    declined = r["refused"] or re.search(
        r"not (in|available in|provided in|contain) the (official|ingested)", body, re.I
    )
    figs = re.findall(r"\d[\d,]*\.?\d*\s*%", body)
    check(f"declines: {q[:46]}", declined, body[:70])
    check(f"no figure stated: {q[:46]}", not figs, f"figures={figs}")
    print(f"      -> {body[:100]}")
    print(f"      -> {why}, refused={r['refused']}, reason={(r.get('debug') or {}).get('reason','-')}\n")

print(f"\n{5*2-fails}/{10} checks passed, {fails} failure(s)")
sys.exit(1 if fails else 0)
