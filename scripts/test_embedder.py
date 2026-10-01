"""Tests for the dual-backend embedder.

The whole point of the ONNX backend is that serving fits in 512 MB. Two things
can silently undo that, and neither shows up in a functional test:

* the corpus being re-embedded with a different backend than the one serving
  queries, which drifts the vector space; and
* torch being imported again at query time, which triples resident memory
  without changing a single answer.

So this checks the contract, not the numbers.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config

fails = 0


def check(label, ok, detail=""):
    global fails
    print(("PASS  " if ok else "FAIL  ") + label + ("" if ok else f" -- {detail}"))
    if not ok:
        fails += 1


print("=== embedder: backends ===\n")

check("MAX_SEQUENCE_LENGTH is MiniLM's window", config.MAX_SEQUENCE_LENGTH == 256, str(config.MAX_SEQUENCE_LENGTH))
check("EMBEDDING_DIM is 384", config.EMBEDDING_DIM == 384, str(config.EMBEDDING_DIM))
check("MODELS_DIR exists or is creatable", config.MODELS_DIR.parent.exists(), str(config.MODELS_DIR))

onnx_ready = (config.MODELS_DIR / "minilm.onnx").exists()

if onnx_ready:
    print("\n-- ONNX present: this is the serving path --")
    import numpy as np

    from app.embedder import available_backend, embed_query, embed_texts

    check("ONNX backend is auto-selected", available_backend() == "onnx", available_backend())

    # The sidecar carries the weights. A graph without it loads but returns junk.
    sidecar = (config.MODELS_DIR / "minilm.onnx").with_suffix(".onnx.data")
    check("weights sidecar is present", sidecar.exists(), str(sidecar))
    if sidecar.exists():
        mb = sidecar.stat().st_size / 1e6
        check(f"sidecar holds real weights ({mb:.0f} MB)", mb > 20, f"{mb:.1f} MB")

    vec = embed_query("What is the expense ratio of HDFC Flexi Cap Fund?")
    check("query vector is 384-d", len(vec) == config.EMBEDDING_DIM, str(len(vec)))
    norm = sum(x * x for x in vec) ** 0.5
    check("query vector is L2-normalised", abs(norm - 1.0) < 1e-3, f"norm={norm}")

    batch = embed_texts(["Min SIP: Rs 100", "Riskometer: Very High"])
    check("batch encoding returns one vector per input", len(batch) == 2, str(len(batch)))

    # Truncation must agree across backends or long chunks embed differently at
    # ingest and query time.
    long_text = "expense ratio " * 400
    long_vec = embed_query(long_text)
    check("over-length input truncates instead of failing", len(long_vec) == config.EMBEDDING_DIM)

    check("empty input list returns []", embed_texts([]) == [])

    print("\n-- torch must stay out of the serving path --")
    for module in ("torch", "transformers", "sentence_transformers"):
        check(f"{module} not imported at query time", module not in sys.modules, "imported")

    print("\n-- ONNX and torch must produce the same vectors --")
    probes = [
        "What is the expense ratio of HDFC Flexi Cap Fund?",
        "Min SIP: Rs 100",
        "Riskometer: Very High",
        "total expense ratio 0.77 benchmark NIFTY 500 TRI lock in three years",
    ]
    onnx_vecs = np.array(embed_texts(probes))
    from app.embedder import embed_texts as embed_with

    previous = os.environ.get("EMBEDDER_BACKEND")
    os.environ["EMBEDDER_BACKEND"] = "torch"
    try:
        torch_vecs = np.array(embed_with(probes))
    finally:
        if previous is None:
            os.environ.pop("EMBEDDER_BACKEND", None)
        else:
            os.environ["EMBEDDER_BACKEND"] = previous

    cosine = (onnx_vecs * torch_vecs).sum(axis=1)
    for probe, c in zip(probes, cosine):
        print(f"      cosine {c:.7f}  {probe[:48]}")
    check(
        f"ONNX matches torch (min cosine {cosine.min():.7f})",
        cosine.min() > 0.999,
        f"min={cosine.min():.6f}",
    )
    check(
        f"corpus distances survive the switch (max delta {(np.abs(onnx_vecs-torch_vecs)).max():.2e})",
        np.abs(onnx_vecs - torch_vecs).max() < 1e-3,
        f"max delta={np.abs(onnx_vecs - torch_vecs).max():.2e}",
    )
else:
    print("\nSKIP: models/minilm.onnx absent. Run: python scripts/export_onnx.py")
    print("      (The serving checks need the export; the build does it automatically.)")

print(f"\n{'ALL PASS' if fails == 0 else str(fails) + ' FAILURE(S)'}")
sys.exit(1 if fails else 0)
