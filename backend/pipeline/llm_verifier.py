"""LLM verification for the lowest-confidence predictions."""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import requests

from backend.config import (
    CATEGORIES,
    LLM_MODEL,
    LLM_TIMEOUT_SECONDS,
    LLM_VERIFY_BATCH_SIZE,
    LLM_VERIFY_CONCURRENCY,
    LLM_VERIFY_MAX_RECORDS,
    LOW_CONFIDENCE_THRESHOLD,
    OLLAMA_URL,
    SEVERITIES,
)
from backend.pipeline.llm_utils import get_cached_llm, ollama_response_text, parse_llm_json, set_cached_llm

logger = logging.getLogger(__name__)


def _normalize_verify_results(results, batch_indices: list[int]) -> dict[int, dict]:
    updates = {}
    for order, raw in enumerate(results or []):
        if not isinstance(raw, dict):
            continue

        rid = raw.get("id")
        try:
            rid = int(rid)
        except (TypeError, ValueError):
            rid = None

        if rid is not None and 1 <= rid <= len(batch_indices):
            idx = batch_indices[rid - 1]
        elif rid in batch_indices:
            idx = rid
        elif order < len(batch_indices):
            idx = batch_indices[order]
        else:
            continue

        updates[idx] = raw
    return updates


def _verify_batch(texts: list[str], batch_indices: list[int], categories: list[str] | None = None, depth: int = 0) -> dict[int, dict]:
    cats = categories or CATEGORIES
    numbered = "\n".join(f"{pos + 1}. {texts[idx][:400]}" for pos, idx in enumerate(batch_indices))
    prompt = f"""Классифицируй каждое обращение. Ответь строго JSON:
{{"results": [{{"id": 1, "is_problem": true, "severity": "MEDIUM", "category": "ЖКХ"}}]}}

Обращения:
{numbered}

JSON:"""
    system = (
        "Ты - классификатор обращений. "
        "severity: CRITICAL/HIGH/MEDIUM/LOW. "
        "category: " + ", ".join(cats)
    )
    cache_payload = {
        "model": LLM_MODEL,
        "system": system,
        "texts": [texts[idx][:400] for idx in batch_indices],
    }

    cached = get_cached_llm("verify", cache_payload)
    if cached is not None:
        return {int(k): v for k, v in cached.items()}

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": LLM_MODEL,
                "prompt": prompt,
                "system": system,
                "stream": False,
                "think": False,
                "options": {"temperature": 0.1, "num_predict": 900},
                "format": "json",
            },
            timeout=LLM_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = parse_llm_json(ollama_response_text(resp.json()))

        if isinstance(data, list):
            results = data
        elif isinstance(data, dict):
            results = data.get("results", data.get("items", []))
            if not results and "id" in data:
                results = [data]
        else:
            results = []

        updates = _normalize_verify_results(results, batch_indices)
        set_cached_llm("verify", cache_payload, updates)
        return updates
    except Exception as exc:
        logger.warning("LLM verification batch failed: %s", exc)
        if len(batch_indices) > 1 and depth < 2:
            mid = max(1, len(batch_indices) // 2)
            merged = _verify_batch(texts, batch_indices[:mid], cats, depth + 1)
            merged.update(_verify_batch(texts, batch_indices[mid:], cats, depth + 1))
            return merged
        return {}


def verify_low_confidence(texts: list[str], predictions: dict, embeddings: np.ndarray, categories: list[str] | None = None, progress_callback=None) -> dict:
    """Re-classify the lowest-confidence records using LLM, in-place."""
    _ = embeddings
    confidences = predictions["confidence"]
    low_conf_indices = [i for i, confidence in enumerate(confidences) if confidence < LOW_CONFIDENCE_THRESHOLD]

    if not low_conf_indices:
        logger.info("No low-confidence records to verify")
        if progress_callback:
            progress_callback(1, 1)
        return predictions

    if LLM_VERIFY_MAX_RECORDS <= 0:
        logger.info("LLM verification disabled by LLM_VERIFY_MAX_RECORDS=%s", LLM_VERIFY_MAX_RECORDS)
        if progress_callback:
            progress_callback(1, 1)
        return predictions

    selected = sorted(low_conf_indices, key=lambda idx: confidences[idx])[:LLM_VERIFY_MAX_RECORDS]
    selected.sort()
    skipped = len(low_conf_indices) - len(selected)
    logger.info(
        "Verifying %s/%s low-confidence records via LLM; skipped %s by cap",
        len(selected),
        len(low_conf_indices),
        max(skipped, 0),
    )

    batches = [
        selected[start:start + LLM_VERIFY_BATCH_SIZE]
        for start in range(0, len(selected), LLM_VERIFY_BATCH_SIZE)
    ]

    cats = categories or CATEGORIES
    with ThreadPoolExecutor(max_workers=LLM_VERIFY_CONCURRENCY) as executor:
        futures = {executor.submit(_verify_batch, texts, batch, cats): batch for batch in batches}
        done = 0
        for future in as_completed(futures):
            updates = future.result()
            for idx, result in updates.items():
                predictions["is_problem"][idx] = bool(result.get("is_problem", predictions["is_problem"][idx]))

                sev = result.get("severity", predictions["severity"][idx])
                predictions["severity"][idx] = sev if sev in SEVERITIES else "MEDIUM"

                cat = result.get("category", predictions["category"][idx])
                predictions["category"][idx] = cat if cat in cats else cats[-1]

                predictions["confidence"][idx] = 0.85
                predictions["method"][idx] = "llm"

            done += 1
            if progress_callback:
                progress_callback(done, len(batches))

    verified = sum(1 for method in predictions["method"] if method == "llm")
    logger.info("Verified %s records via LLM", verified)
    return predictions
