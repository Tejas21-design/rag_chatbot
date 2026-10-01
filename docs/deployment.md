# Deploying

## Why this app can run on a 512 MB container

The first Render deploy hung on every question. The cause was not the network and
not the code path: importing `torch` costs **~594 MB** resident before it does any
work, against a 512 MB limit. The OS then evicts pages continuously, so loading the
model took minutes instead of 10 seconds.

| Configuration | Peak RSS |
|---|---|
| torch via sentence-transformers (default PyPI wheel, CUDA) | **594 MB** |
| CPU-only torch wheel | 549 MB |
| **ONNX Runtime** | **187 MB** |

Measured with `resource.getrusage`, not estimated. The CPU-only wheel helps but
does not fit, so torch had to leave the serving path entirely.

`scripts/export_onnx.py` exports the same MiniLM network to ONNX at build time.
ONNX Runtime then serves it in 187 MB, and `app/embedder.py` never imports torch
when the export exists. The two backends agree to **cosine 0.9999999**, so the
stored corpus and all retrieval distances remain valid; `scripts/test_embedder.py`
asserts this on every run rather than taking it on trust.

Two side effects worth knowing:

- The default PyPI torch wheel pulls ~2.8 GB of CUDA libraries that are never used
  (`torch.cuda.is_available()` is `False` here). Installing from
  `https://download.pytorch.org/whl/cpu` drops that to zero CUDA packages.
- `models/minilm.onnx` is only 57 KB on its own; the 91 MB of weights live in
  `models/minilm.onnx.data`. Both files must be deployed together.

## Render (Web Service)

| Field | Value |
|---|---|
| Root Directory | blank |
| Build Command | see below |
| Start Command | see below |
| Instance Type | Free (512 MB) is enough |

**Build Command**

```sh
pip install -r requirements.txt && python scripts/export_onnx.py && python -m app.ingest
```

`export_onnx.py` must run before ingestion, because ingestion embeds 63 chunks and
prefers the ONNX backend once its files exist. It self-checks the export against
torch and fails the build if the vectors disagree by more than cosine 0.999.

**Start Command**

```sh
bash -c 'PY=$(dirname $(find /opt/render -name streamlit -path "*/bin/*" -type f 2>/dev/null | head -1))/python3; exec $PY -m streamlit run app/app.py --server.address 0.0.0.0 --server.port $PORT --server.headless true'
```

Two things that are easy to get wrong here:

- **`streamlit` and `python` are both `command not found`.** Render puts the venv
  on `PATH` for the build step but not for the runtime shell, which is why
  `python -m app.ingest` succeeds during the build and `python` is missing at
  start. The command above locates the interpreter from wherever `streamlit`
  actually landed instead of assuming a path.
- **`$PORT` must be passed explicitly.** Render injects it, and Streamlit defaults
  to 8501 otherwise.

**Environment variables**

| Variable | Value | Secret |
|---|---|---|
| `GROQ_API_KEY` | your key from console.groq.com/keys | yes |
| `GROQ_MODEL` | `openai/gpt-oss-120b` (the default; override only if your account has better) | no |
| `PYTHONUNBUFFERED` | `1` | no |
| `OMP_NUM_THREADS` | `1` | no — recommended |
| `AUTO_INGEST_ON_START` | `true` | no — `false` forbids ingest on app start |
| `INGEST_DEADLINE_SECONDS` | `150` | no — ceiling on an interactive build only |

`AUTO_INGEST_ON_START` (default `true`) lets the app rebuild a missing corpus at
startup. Render may not carry build output into the running container, so this is
what makes the demo work rather than showing an empty screen. Set it to `false` for
the strict PRD behaviour where ingestion never happens on app start.

That rebuild runs on a background thread (`app/builder.py`) with a
150 s deadline. Both exist for the same reason: blocking the Streamlit script on a
network job froze the entire UI, and since Streamlit re-runs the script on every
interaction, the scrape was retried on every click. With five URLs, a 30 s timeout
and three attempts each, "frozen" meant up to eight minutes.

Do **not** set `CHROMA_DIR`. It defaults to `data/chroma` relative to the project
root, which is where the build writes it; an absolute path would point the app at
a directory that was never populated.

`OMP_NUM_THREADS=1` is optional but worth setting: ONNX Runtime otherwise spawns one
thread per core and each thread arena costs memory.

## Expected latency

| Step | Time |
|---|---|
| Chroma open | 0.1 s |
| First query (loads ONNX session) | ~1 s |
| Subsequent queries | ~5 ms |
| Full answer (Groq round trip) | ~10 s |

The ~10 s floor is the Groq API, not local work. Free-tier instances also spin down
after 15 minutes idle, so the next request pays a cold start.

## Other hosts

Nothing here is Render-specific. The requirements and commands are portable. Any
host that gives ~256 MB or more of RAM works, and on a host with more memory the
torch backend still works -- set `EMBEDDER_BACKEND=torch` to pin it.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `streamlit: command not found` | Start Command lost the `python -m` wrapper |
| Page hangs on "Loading the embedding model" | Model load thrashing; confirm `models/minilm.onnx.data` shipped |
| `Corpus not ingested` | Build output did not survive into the running container (ephemeral filesystem) — the app now rebuilds on first start, so this only persists if that also failed |
| Declines everything | `models/minilm.onnx.data` missing, so the graph loads with no weights |
| First question 30 s+, then fast | Cold start on a free-tier spin-down |
| Spinner never resolves | Interactive ingest hit its 150 s deadline; the page shows the reason. A build step has no deadline |
