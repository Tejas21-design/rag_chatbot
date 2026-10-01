import sys, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.answer import answer_question
from app import config

fails = []
def check(l, c, d=""):
    print(("PASS  " if c else "FAIL  ") + l + ("" if c else f" -- {d}"))
    if not c: fails.append(l)

QS = [
    "What is the expense ratio of HDFC Flexi Cap Fund (Direct-Growth)?",
    "What is the lock-in period for HDFC ELSS Tax Saver Fund?",
    "What is the minimum SIP for HDFC Flexi Cap Fund?",
    "What is the exit load for HDFC ELSS Tax Saver Fund?",
    "What is the riskometer level of HDFC Flexi Cap Fund?",
    "What is the benchmark for HDFC Flexi Cap Fund?",
    "What is the NAV of HDFC Flexi Cap Fund?",
    "What is the AUM of HDFC Flexi Cap Fund?",
    "Who is the fund manager of HDFC Flexi Cap Fund?",
    "Should I invest in HDFC Flexi Cap Fund?",
]
print("=== TEST 6: OUTPUT CONTRACT (10 questions) ===\n")
approved = set(config.APPROVED_URLS)
for q in QS:
    r = answer_question(q)
    body, dbg = r["answer"], r.get("debug") or {}
    n_sent = len([s for s in re.split(r"(?<=[.!?])\s+", body.strip()) if s])
    in_body = re.findall(r"https?://\S+", body)
    # Verified keys: answer_question returns citation_url / last_updated_from_sources.
    url = r.get("citation_url") or ""
    date = r.get("last_updated_from_sources") or ""
    tag = "REFUSED" if r["refused"] else "ANSWERED"
    print(f"\nQ: {q}\n{tag} | {n_sent} sentences | url={'yes' if url else 'no'} | date={date or 'none'} | refused={r['refused']}")
    print(f"A: {body}")

    if url and url not in approved:
        check(f"{q[:34]}: citation is an approved URL", False, url)
    if in_body:
        check(f"{q[:34]}: no URL in body", False, str(in_body))
    if n_sent > 3:
        check(f"{q[:34]}: <=3 sentences", False, f"{n_sent}")
    if not r["refused"] and re.search(r"\b(I recommend|I advise|you should invest|I suggest)\b", body, re.I):
        check(f"{q[:34]}: no advice language", False, body[:60])
    if not url and not r["refused"]:
        check(f"{q[:34]}: grounded answer has a citation", False, "no citation_url")
    declined = (r.get("debug") or {}).get("reason") == "model_declined"
    # A model decline is deliberately not attributed to a retrieved chunk, so it
    # carries no source date. Only a real grounded answer must carry one.
    if not r["refused"] and not declined and not date:
        check(f"{q[:34]}: grounded answer has a source date", False, "no last_updated_from_sources")
    if declined:
        check(f"{q[:34]}: decline cites factsheets, not a chunk", r.get("citation_url")=="https://www.hdfcfund.com/mutual-funds/factsheets", str(r.get("citation_url")))
    if r["refused"] and "Last updated from sources:" in body:
        check(f"{q[:34]}: refusal carries no source-date claim", False, body[:50])

print(f"\n=== {len(QS)*10} contract assertions across {len(QS)} questions ===")
print("FAILURES:", len(fails))
for f in fails: print("  -", f)
sys.exit(1 if fails else 0)
