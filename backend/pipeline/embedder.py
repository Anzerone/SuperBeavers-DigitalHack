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
        from backend.config import EMBEDDING_DEVICE, EMBEDDING_LORA_PATH, EMBEDDING_MODEL_NAME

        # Если есть LoRA-адаптер — модель из него (там сохранена базовая + adapter).
        # Иначе грузим из HF/local cache.
        fine_tuned = bool(EMBEDDING_LORA_PATH and os.path.isdir(EMBEDDING_LORA_PATH))
        if fine_tuned:
            logger.info("Loading fine-tuned embedding model from %s", EMBEDDING_LORA_PATH)
            model = SentenceTransformer(EMBEDDING_LORA_PATH, device=EMBEDDING_DEVICE)
        else:
            logger.info("Loading base embedding model %s on %s", EMBEDDING_MODEL_NAME, EMBEDDING_DEVICE)
            model = SentenceTransformer(EMBEDDING_MODEL_NAME, device=EMBEDDING_DEVICE)

        # Cap sequence length: attention cost grows quadratically with length.
        model.max_seq_length = EMBEDDING_MAX_SEQ_LENGTH
        # fp16 roughly halves compute on GPU with negligible quality loss.
        if EMBEDDING_USE_FP16 and str(EMBEDDING_DEVICE).startswith("cuda"):
            model = model.half()
        logger.info(
            "Embedding model ready (max_seq_length=%s, fp16=%s, fine_tuned=%s)",
            model.max_seq_length,
            EMBEDDING_USE_FP16 and str(EMBEDDING_DEVICE).startswith("cuda"),
            fine_tuned,
        )
        _model = model
    return _model


def _cache_key(texts: list[str]) -> str:
    """Stable hash over model config + texts for the on-disk embedding cache.

    Включает LoRA-путь, чтобы базовые и fine-tuned эмбеддинги не смешивались.
    """
    from backend.config import EMBEDDING_LORA_PATH, EMBEDDING_MODEL_NAME

    hasher = hashlib.sha256()
    hasher.update(EMBEDDING_MODEL_NAME.encode("utf-8"))
    if EMBEDDING_LORA_PATH:
        hasher.update(f"|lora={EMBEDDING_LORA_PATH}".encode("utf-8"))
        model_file = os.path.join(EMBEDDING_LORA_PATH, "model.safetensors")
        if os.path.exists(model_file):
            stat = os.stat(model_file)
            hasher.update(f"|lora_file={stat.st_size}:{stat.st_mtime_ns}".encode("utf-8"))
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
            progress_callback(len(texts), len(texts), "Эмбеддинги взяты из кеша")
        return cached.astype(np.float32, copy=False)

    batch_size = batch_size or EMBEDDING_BATCH_SIZE
    if progress_callback:
        progress_callback(0, len(texts), "Загрузка модели анализа писем")
    model = _get_model()
    if progress_callback:
        progress_callback(0, len(texts), "Модель анализа загружена, считаю векторы писем")
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
            progress_callback(total, total, f"Анализ содержания писем: {total}/{total}")
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
            progress_callback(end, total, f"Анализ содержания писем: {end}/{total}")

    result = np.vstack(chunks).astype(np.float32, copy=False)
    _save_cached(texts, result)
    return result


def compute_embeddings_dedup(texts: list[str], batch_size: int | None = None, progress_callback=None) -> np.ndarray:
    """Dedup-aware embedding: одинаковые тексты считаются один раз.

    Для типичных датасетов гражданских жалоб 5–15% строк — точные или
    почти-точные дубли (копипаста в соцсетях). На 400k это экономит
    до 30–60 тысяч encode-операций без потери качества.

    Возвращает массив shape=(N, dim) в исходном порядке texts.
    """
    if not texts:
        return np.empty((0, 0), dtype=np.float32)

    # Группируем по нормализованной строке
    unique_map: dict[str, int] = {}
    unique_texts: list[str] = []
    indices: list[int] = []
    for text in texts:
        key = (text or "").strip()
        idx = unique_map.get(key)
        if idx is None:
            idx = len(unique_texts)
            unique_map[key] = idx
            unique_texts.append(key)
        indices.append(idx)

    if len(unique_texts) == len(texts):
        # Дублей нет — обычный путь.
        return compute_embeddings(texts, batch_size=batch_size, progress_callback=progress_callback)

    logger.info(
        "Dedup: %s unique of %s texts (%.1f%% reduction)",
        len(unique_texts), len(texts),
        (1 - len(unique_texts) / len(texts)) * 100,
    )

    unique_embeddings = compute_embeddings(
        unique_texts,
        batch_size=batch_size,
        progress_callback=progress_callback,
    )

    # Разворачиваем обратно по индексам — каждая строка получает свой эмбеддинг
    return unique_embeddings[np.asarray(indices, dtype=np.int64)]
