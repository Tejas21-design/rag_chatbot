import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import guardrails as g, config

print("=== TEST 5: REFUSAL TABLE ===\n")
print(f"{'question':46s} {'intent':13s} {'blk':4s} link")
print("-"*110)
CASES = [
    ("my PAN is ABCDE1234F", "pii"),
    ("call me on 9876543210", "pii"),
    ("mail me at tejas@gmail.com", "pii"),
    ("my aadhaar is 9999 9999 9999", "pii"),
    ("my folio number is 12345678901", "pii"),
    ("Should I buy HDFC Flexi Cap Fund?", "advice"),
    ("which fund is best for me", "advice"),
    ("rebalance my portfolio please", "advice"),
    ("what is the asset allocation I should keep", "advice"),
    ("which fund gave better returns", "performance"),
    ("what is the 5 year CAGR", "performance"),
    ("how much profit will I make", "performance"),
    ("tell me about SBI Large Cap Fund", "out_of_scope"),
    ("expense ratio of HDFC Mid-Cap Fund", "out_of_scope"),
    ("what about HDFC Large Cap Fund", "out_of_scope"),
    ("the Nifty 50 index fund", "out_of_scope"),
    ("What is the expense ratio of HDFC Flexi Cap Fund?", "fact"),
    ("What is the minimum SIP for HDFC Flexi Cap Fund?", "fact"),
]
fails=0
for q, expect in CASES:
    d = g.classify(q)
    ok = d.intent == expect
    if not ok: fails += 1
    link = (d.link or "-").replace("https://www.hdfcfund.com","")
    print(f"{q[:46]:46s} {d.intent:13s} {'yes' if not d.allowed else 'NO':4s} {link}")
    if not ok: print(f"    ^ expected {expect}")
print(f"\n{len(CASES)-fails}/{len(CASES)} correct")

print("\n=== PII never echoed in a refusal ===")
leaks=0
for q in ["my PAN is ABCDE1234F", "call me on 9876543210", "mail me at tejas@gmail.com"]:
    d = g.classify(q)
    for tok in ["ABCDE1234F","9876543210","tejas@gmail.com"]:
        if tok in d.message: leaks+=1; print(f"LEAK: {tok} in {d.message!r}")
print(f"no PII in any refusal message: {leaks==0}")
sys.exit(1 if fails or leaks else 0)
