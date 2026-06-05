"""Compute embeddings using sentence-transformers bge-m3 on GPU."""
import hashlib
import logging
import os

import numpy as np

from backend.config import (
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_CACHE_DIR,
    EMBEDDING_CACHE_ENABLED,
    EMBEDDING_MAX_SEQ_LENGTH,
    EMBEDDING_PROGRESS_CHUNK_SIZE,
    EMBEDDING_USE_FP16,
)

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        from backend.config import EMBEDDING_DEVICE, EMBEDDING_MODEL_NAME
        logger.info(f"Loading embedding model {EMBEDDING_MODEL_NAME} on {EMBEDDING_DEVICE}")
        model = SentenceTransformer(EMBEDDING_MODEL_NAME, device=EMBEDDING_DEVICE)
        # Cap sequence length: attention cost grows quadratically with length.
        model.max_seq_length = EMBEDDING_MAX_SEQ_LENGTH
        # fp16 roughly halves compute on GPU with negligible quality loss.
        if EMBEDDING_USE_FP16 and str(EMBEDDING_DEVICE).startswith("cuda"):
            model = model.half()
        logger.info(
            "Embedding model ready (max_seq_length=%s, fp16=%s)",
            model.max_seq_length,
            EMBEDDING_USE_FP16 and str(EMBEDDING_DEVICE).startswith("cuda"),
        )
        _model = model
    return _model


def _cache_key(texts: list[str]) -> str:
    """Stable hash over model config + texts for the on-disk embedding cache."""
    from backend.config import EMBEDDING_MODEL_NAME

    hasher = hashlib.sha256()
    hasher.update(EMBEDDING_MODEL_NAME.encode("utf-8"))
    hasher.update(f"|seq={EMBEDDING_MAX_SEQ_LENGTH}|n={len(texts)}|".encode("utf-8"))
    for text in texts:
        hasher.update(text.encode("utf-8", errors="ignore"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


def _load_cached(texts: list[str]) -> np.ndarray | None:
    if not EMBEDDING_CACHE_ENABLED:
        return None
    path = os.path.join(EMBEDDING_CACHE_DIR, f"{_cache_key(texts)}.npy")
    if not os.path.exists(path):
        return None
    try:
        embeddings = np.load(path)
        if len(embeddings) == len(texts):
            logger.info("Loaded %s embeddings from cache %s", len(embeddings), path)
            return embeddings
    except Exception as exc:
        logger.warning("Failed to read embedding cache %s: %s", path, exc)
    return None


def _save_cached(texts: list[str], embeddings: np.ndarray) -> None:
    if not EMBEDDING_CACHE_ENABLED:
        return
    os.makedirs(EMBEDDING_CACHE_DIR, exist_ok=True)
    key = _cache_key(texts)
    path = os.path.join(EMBEDDING_CACHE_DIR, f"{key}.npy")
    tmp_path = os.path.join(EMBEDDING_CACHE_DIR, f"{key}.tmp.npy")
    try:
        np.save(tmp_path, embeddings)
        os.replace(tmp_path, path)
        logger.info("Saved %s embeddings to cache %s", len(embeddings), path)
    except Exception as exc:
        logger.warning("Failed to write embedding cache %s: %s", path, exc)


def compute_embeddings(texts: list[str], batch_size: int | None = None, progress_callback=None) -> np.ndarray:
    """Compute embeddings for all texts using GPU batching.

    Returns numpy array of shape (N, 1024).
    """
    if not texts:
        return np.empty((0, 0), dtype=np.float32)

    cached = _load_cached(texts)
    if cached is not None:
        if progress_callback:
            progress_callback(len(texts), len(texts))
        return cached.astype(np.float32, copy=False)

    batch_size = batch_size or EMBEDDING_BATCH_SIZE
    model = _get_model()
    logger.info(f"Computing embeddings for {len(texts)} texts, batch_size={batch_size}")

    total = len(texts)

    # Single encode call is faster — avoids repeated GPU sync overhead
    if total <= batch_size * 2:
        embeddings = model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        result = np.asarray(embeddings, dtype=np.float32)
        _save_cached(texts, result)
        if progress_callback:
            progress_callback(total, total)
        return result

    chunks = []
    chunk_size = max(batch_size, EMBEDDING_PROGRESS_CHUNK_SIZE)
    for start in range(0, total, chunk_size):
        end = min(start + chunk_size, total)
        chunk_embeddings = model.encode(
            texts[start:end],
            batch_size=batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        chunks.append(np.asarray(chunk_embeddings, dtype=np.float32))
        if progress_callback:
            progress_callback(end, total)

    result = np.vstack(chunks).astype(np.float32, copy=False)
    _save_cached(texts, result)
    return result
