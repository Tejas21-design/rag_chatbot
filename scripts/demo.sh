#!/usr/bin/env bash
# Demo-day walkthrough for the HDFC Mutual Fund FAQ assistant.
#
#   ./.venv/bin/python scripts/ask.py --suite     # the five graded questions
#   ./.venv/bin/python -m streamlit run app/app.py
#
# Run from the project root with the virtualenv active.

set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

say "1. Corpus is present"
if [ ! -d data/chroma ]; then
  echo "No corpus. Run: python -m app.ingest"
  exit 1
fi
echo "data/chroma/ present, chunks.txt is $(wc -l < data/chunks.txt) lines."

say "2. The five graded questions"
.venv/bin/python scripts/ask.py --suite

say "3. One refusal (advice) — should link the factsheets page"
.venv/bin/python scripts/ask.py "Should I buy HDFC Flexi Cap Fund?"

say "4. One refusal (PII) — should not echo the identifier back"
.venv/bin/python scripts/ask.py "my PAN is ABCDE1234F"

say "5. One honest decline (absent from the corpus)"
.venv/bin/python scripts/ask.py "Who is the fund manager of HDFC Flexi Cap Fund?"

say "6. Start the UI"
echo "    .venv/bin/python -m streamlit run app/app.py"
echo "    then open http://localhost:8501"
echo
echo "Click the three example questions. Each answer shows one source link"
echo "and the 'Last updated from sources' date."
