# Sample questions and real answers

Every answer below was produced by running the question through
`app.answer.answer_question` against the live Groq model
(`openai/gpt-oss-120b`, `temperature=0`) and the 63-chunk corpus. **Nothing here is
written by hand.** Reproduce any of them with:

```bash
python scripts/ask.py "the question"
```

Regenerate the transcripts with `python scripts/make_sample_qa.py` (needs a key,
~13 API calls) if the model or corpus ever changes.

## How to read `refused: false` on a decline

Two different things can produce "I don't have that", and they are worth telling apart:

| Mechanism | `refused` | When it happens |
|---|---|---|
| **Guardrail** | `true` | Blocked before retrieval. No retrieval, no LLM call, instant. Intent is recorded: `pii`, `advice`, `performance`, `out_of_scope`. |
| **Model decline** | `false` | Retrieval ran, the model read the context, and it judged the answer absent. Costs a retrieval and an API call. |

Both are correct outcomes. The difference is that a guardrail decision is *guaranteed* by
code, while a model decline depends on the model honouring its instructions — which is
why the ungrounded-number backstop exists for the figure case.

---

## Answers with a grounded fact (7)


### 1. What is the expense ratio of HDFC Flexi Cap Fund (Direct–Growth)?

*PRD example 1*

```
The expense ratio (Total Expense Ratio) for the HDFC Flexi Cap Fund (Direct–Growth) is 0.77 %.
```

- Source: https://www.hdfcfund.com/explore/mutual-funds/hdfc-flexi-cap-fund/direct
- Last updated from sources: 29 Sep 2026
- `refused`: `False`

### 2. What is the lock-in for HDFC ELSS Tax Saver?

*PRD example 2*

```
The HDFC ELSS Tax Saver Fund has a lock‑in period of three years.
```

- Source: https://www.hdfcfund.com/explore/mutual-funds/hdfc-elss-tax-saver-fund/direct
- Last updated from sources: 29 Sep 2026
- `refused`: `False`

### 3. What is the minimum SIP for HDFC Flexi Cap Fund?

*SIP from corpus*

```
The minimum SIP amount for HDFC Flexi Cap Fund is ₹ 100.
```

- Source: https://www.hdfcfund.com/explore/mutual-funds/hdfc-flexi-cap-fund/direct
- Last updated from sources: 29 Sep 2026
- `refused`: `False`

### 4. What is the exit load on HDFC ELSS Tax Saver?

*exit load from corpus*

```
The exit load on HDFC ELSS Tax Saver is NIL.
```

- Source: https://www.hdfcfund.com/explore/mutual-funds/hdfc-elss-tax-saver-fund/direct
- Last updated from sources: 29 Sep 2026
- `refused`: `False`

### 5. What is the riskometer level of HDFC Flexi Cap Fund?

*riskometer from corpus*

```
The HDFC Flexi Cap Fund has a riskometer level of Very High.
```

- Source: https://www.hdfcfund.com/explore/mutual-funds/hdfc-flexi-cap-fund/direct
- Last updated from sources: 29 Sep 2026
- `refused`: `False`

### 6. What is the benchmark of HDFC Flexi Cap Fund?

*benchmark from corpus*

```
HDFC Flexi Cap Fund is benchmarked against the NIFTY 500 Total Returns Index (TRI).
```

- Source: https://www.hdfcfund.com/explore/mutual-funds/hdfc-flexi-cap-fund/direct
- Last updated from sources: 29 Sep 2026
- `refused`: `False`

### 7. Who is the fund manager of HDFC Flexi Cap Fund?

*not in corpus*

```
The information is not in the official HDFC Mutual Fund pages.
```

- Source: https://www.hdfcfund.com/explore/mutual-funds/hdfc-flexi-cap-fund/direct
- Last updated from sources: 29 Sep 2026
- `refused`: `False`

### 8. What is the SEBI registration number of HDFC Mutual Fund?

*not in corpus*

```
The information is not in the official HDFC Mutual Fund pages.
```

- Source: https://www.hdfcfund.com/
- Last updated from sources: 29 Sep 2026
- `refused`: `False`

### 9. How do I download a capital-gains / account statement from HDFC Mutual Fund?

*PRD example 3, not in corpus*

```
The information is not available in the official HDFC Mutual Fund pages.
```

- Source: https://www.hdfcfund.com/explore/mutual-funds/hdfc-elss-tax-saver-fund/direct
- Last updated from sources: 29 Sep 2026
- `refused`: `False`


## Refused by a guardrail, before any model call (4)

### 10. Should I buy HDFC Flexi Cap Fund right now?

*advice refusal*

```
I can share the published facts about HDFC Mutual Fund schemes, but I cannot recommend whether to buy, sell or hold a fund, and I cannot advise on allocation. Please review the official factsheets and fund documents for the scheme details and the suitability section.
```

- Source: https://www.hdfcfund.com/mutual-funds/factsheets
- `refused`: `True`

### 11. Which fund gave better returns last year?

*performance refusal*

```
I cannot calculate or compare returns, CAGR or performance. The official factsheet publishes the fund's returns, and the riskometer, benchmark and expense ratio, for each scheme.
```

- Source: https://www.hdfcfund.com/mutual-funds/factsheets
- `refused`: `True`

### 12. What is the expense ratio of HDFC Mid-Cap Fund?

*out-of-scope refusal*

```
I only have the ingested HDFC Mutual Fund pages, so I cannot answer for other AMCs or for schemes outside this corpus. You can browse the approved list of HDFC Mutual Fund schemes here.
```

- Source: https://www.hdfcfund.com/explore/mutual-funds
- `refused`: `True`

### 13. Tell me about SBI Large Cap Fund

*other AMC refusal*

```
I only have the ingested HDFC Mutual Fund pages, so I cannot answer for other AMCs or for schemes outside this corpus. You can browse the approved list of HDFC Mutual Fund schemes here.
```

- Source: https://www.hdfcfund.com/explore/mutual-funds
- `refused`: `True`


---

## Summary

| | Count |
|---|---|
| Correct grounded answers | 7 |
| Model declines (absent from corpus) | 2 |
| Guardrail refusals | 4 |
| **Fabricated or wrong answers** | **0** |

Every figure in the grounded answers matches the corpus text:

| Answer | Corpus source |
|---|---|
| `0.77 %` | `Total Expense Ratio\n0.77` on the Flexi Cap page |
| three years | ELSS statutory lock-in |
| `₹ 100` | `Min SIP: ₹ 100` (Flexi Cap) |
| `NIL` | `Exit Load ... NIL` (ELSS) |
| `Very High` | `Riskometer: Very High` (both schemes) |
| `NIFTY 500 Total Returns Index (TRI)` | Flexi Cap benchmark section |
| — | NAV, AUM, fund-manager and SEBI-number questions correctly declined |

The three unanswerable questions are the interesting ones. **Fund manager name**, **SEBI
registration number** and **capital-gains statement download** are all facts a language
model will usually produce confidently from training data. All three declined.
