"""ONNX INT8 embedder — fast CPU inference for transformer embeddings.

This module loads a quantized ONNX feature-extraction model and runs inference
using CPU cores. It is selected when EMBEDDING_BACKEND="onnx".

Conversion: python scripts/convert_to_onnx_int8.py
Throughput: ~150-250 texts/sec on 8-core AVX-512 CPU (vs ~10/s for fp32 PyTorch).
"""
import logging
import os
from pathlib import Path

import numpy as np

from backend.config import (
    EMBEDDING_MAX_SEQ_LENGTH,
    EMBEDDING_ONNX_DIR,
    EMBEDDING_ONNX_THREADS,
    EMBEDDING_POOLING,
    EMBEDDING_PROGRESS_CHUNK_SIZE,
)

logger = logging.getLogger(__name__)

_session = None
_tokenizer = None


def _get_session_and_tokenizer():
    """Lazy-load the ONNX INT8 session and tokenizer."""
    global _session, _tokenizer
    if _session is not None:
        return _session, _tokenizer

    import onnxruntime as ort
    from transformers import AutoTokenizer

    onnx_dir = Path(EMBEDDING_ONNX_DIR)
    if not onnx_dir.is_absolute():
        # Resolve relative to project root
        project_root = Path(__file__).resolve().parents[2]
        onnx_dir = project_root / onnx_dir

    candidates = ["model_quantized.onnx", "model.onnx"]
    model_path = None
    for name in candidates:
        if (onnx_dir / name).exists():
            model_path = onnx_dir / name
            break
    if model_path is None:
        raise FileNotFoundError(
            f"ONNX model not found in {onnx_dir}. "
            f"Run: python scripts/convert_to_onnx_int8.py"
        )

    n_threads = EMBEDDING_ONNX_THREADS or (os.cpu_count() or 4)

    session_options = ort.SessionOptions()
    session_options.intra_op_num_threads = n_threads
    session_options.inter_op_num_threads = 1
    session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL

    providers = ["CPUExecutionProvider"]
    _session = ort.InferenceSession(str(model_path), session_options, providers=providers)
    _tokenizer = AutoTokenizer.from_pretrained(str(onnx_dir))
    logger.info(
        "Loaded ONNX INT8 embedder: %s (threads=%s, providers=%s)",
        model_path.name, n_threads, providers,
    )
    return _session, _tokenizer


def _mean_pool(last_hidden: np.ndarray, attention_mask: np.ndarray | None) -> np.ndarray:
    if attention_mask is None:
        return last_hidden.mean(axis=1).astype(np.float32)
    mask = attention_mask.astype(np.float32)[..., None]
    summed = (last_hidden * mask).sum(axis=1)
    counts = np.maximum(mask.sum(axis=1), 1e-12)
    return (summed / counts).astype(np.float32)


def _encode_batch(session, tokenizer, batch: list[str]) -> np.ndarray:
    """Run one batch through ONNX and return normalized sentence embeddings."""
    inputs = tokenizer(
        batch,
        padding=True,
        truncation=True,
        max_length=EMBEDDING_MAX_SEQ_LENGTH,
        return_tensors="np",
    )

    # Build ONNX feed dict — only with inputs the model actually expects.
    expected = {inp.name for inp in session.get_inputs()}
    feed = {}
    for key in ("input_ids", "attention_mask", "token_type_ids"):
        if key in inputs and key in expected:
            arr = inputs[key]
            if arr.dtype != np.int64:
                arr = arr.astype(np.int64)
            feed[key] = arr

    outputs = session.run(None, feed)
    last_hidden = outputs[0]  # (batch, seq, hidden)

    if EMBEDDING_POOLING == "mean":
        sent_emb = _mean_pool(last_hidden, feed.get("attention_mask"))
    else:
        # bge-m3 uses [CLS] (first token) as sentence embedding.
        sent_emb = last_hidden[:, 0, :].astype(np.float32)

    # L2 normalize for cosine similarity
    norms = np.linalg.norm(sent_emb, axis=1, keepdims=True)
    return sent_emb / np.maximum(norms, 1e-12)


def compute_embeddings_onnx(
    texts: list[str],
    batch_size: int = 32,
    progress_callback=None,
) -> np.ndarray:
    """Compute embeddings via ONNX INT8 on CPU.

    Returns an array of L2-normalized vectors.
    """
    if not texts:
        return np.empty((0, 0), dtype=np.float32)

    session, tokenizer = _get_session_and_tokenizer()
    total = len(texts)
    logger.info("ONNX inference: %s texts, batch_size=%s", total, batch_size)

    chunks: list[np.ndarray] = []
    progress_step = max(batch_size, EMBEDDING_PROGRESS_CHUNK_SIZE)

    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        emb = _encode_batch(session, tokenizer, texts[start:end])
        chunks.append(emb)

        if progress_callback and (end % progress_step == 0 or end == total):
            progress_callback(end, total, f"ONNX анализ писем: {end}/{total}")

    return np.vstack(chunks).astype(np.float32, copy=False)
