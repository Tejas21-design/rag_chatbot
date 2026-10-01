"""Export the MiniLM sentence encoder to ONNX for low-memory serving.

torch costs ~450 MB resident even when idle, which does not fit a 512 MB
container. ONNX Runtime serves the same network in ~110 MB and produces vectors
that agree with torch to cosine 0.9999999 (see docs/verification.md), so the
same corpus and the same retrieval distances still work.

Run once at build time, where torch is available:

    python scripts/export_onnx.py

Writes models/minilm.onnx plus the tokenizer next to it. app/embedder.py uses
the ONNX files when they exist and falls back to sentence-transformers when they
do not, so local development needs no export step.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config

MODEL_DIR = config.MODELS_DIR
ONNX_PATH = MODEL_DIR / "minilm.onnx"
TOKENIZER_PATH = MODEL_DIR / "tokenizer.json"
OPSET = 17


def main() -> int:
    import torch
    from sentence_transformers import SentenceTransformer
    from transformers import AutoTokenizer

    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Exporting {config.EMBEDDING_MODEL} to ONNX (opset {OPSET})...")
    sentence_encoder = SentenceTransformer(config.EMBEDDING_MODEL, device="cpu")

    # save_pretrained writes the bare transformer; exporting the wrapper would
    # bake in the pooling and normalisation, which embed_texts applies itself.
    inner = sentence_encoder[0].auto_model
    inner.eval()
    torch.onnx.export(
        inner,
        (
            torch.ones(1, 16, dtype=torch.long),  # input_ids
            torch.ones(1, 16, dtype=torch.long),  # attention_mask
        ),
        str(ONNX_PATH),
        input_names=["input_ids", "attention_mask"],
        output_names=["last_hidden_state"],
        dynamic_axes={
            "input_ids": {0: "batch", 1: "seq"},
            "attention_mask": {0: "batch", 1: "seq"},
            "last_hidden_state": {0: "batch", 1: "seq"},
        },
        do_constant_folding=True,
        opset_version=OPSET,
    )
    # torch 2.x puts tensors over a few MB into a sidecar file next to the
    # graph, so the .onnx alone is small and useless. Both are required.
    sidecar = ONNX_PATH.with_suffix(".onnx.data")
    total = ONNX_PATH.stat().st_size + (sidecar.stat().st_size if sidecar.exists() else 0)
    print(f"  wrote {ONNX_PATH.name} + {sidecar.name if sidecar.exists() else 'inline'}"
          f" ({total / 1e6:.1f} MB total; keep them together)")

    tokenizer = AutoTokenizer.from_pretrained(config.EMBEDDING_MODEL)
    tokenizer.save_pretrained(str(MODEL_DIR))
    assert (MODEL_DIR / "tokenizer.json").exists(), "tokenizer.json missing"
    print(f"  wrote {MODEL_DIR / 'tokenizer.json'}")

    # Refuse to ship an export that does not agree with the model it replaces.
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer

    from app.embedder import embed_texts

    probes = [
        "What is the expense ratio of HDFC Flexi Cap Fund?",
        "Min SIP: Rs 100",
        "Riskometer: Very High",
    ]
    reference = np.array(embed_texts(probes, backend="torch"))

    from app.embedder import OnnxEncoder

    produced = np.array(OnnxEncoder().encode(probes))
    cosine = (reference * produced).sum(axis=1)
    print(f"  cosine vs torch: min={cosine.min():.7f} max={cosine.max():.7f}")
    if cosine.min() < 0.999:
        raise SystemExit(
            f"ONNX export disagrees with torch (min cosine {cosine.min():.6f}). "
            "Refusing to serve vectors that would not match the corpus."
        )

    session = ort.InferenceSession(str(ONNX_PATH), providers=["CPUExecutionProvider"])
    print(f"  onnxruntime accepts the graph; output {session.get_outputs()[0].name}")
    print("Export OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
