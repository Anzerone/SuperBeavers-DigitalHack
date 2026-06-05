"""Small helpers for Ollama JSON responses and local LLM-result caching."""
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from backend.config import LLM_CACHE_DIR, LLM_CACHE_ENABLED

logger = logging.getLogger(__name__)


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def cache_key(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def get_cached_llm(namespace: str, key: Any) -> Any | None:
    """Read an LLM result from cache. Returns None on misses or corrupt files."""
    if not LLM_CACHE_ENABLED:
        return None

    path = Path(LLM_CACHE_DIR) / namespace / f"{cache_key(key)}.json"
    if not path.exists():
        return None

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("Failed to read LLM cache %s: %s", path, exc)
        return None


def set_cached_llm(namespace: str, key: Any, value: Any) -> None:
    """Write an LLM result to cache using an atomic replace."""
    if not LLM_CACHE_ENABLED:
        return

    cache_dir = Path(LLM_CACHE_DIR) / namespace
    os.makedirs(cache_dir, exist_ok=True)
    path = cache_dir / f"{cache_key(key)}.json"
    tmp_path = path.with_suffix(".tmp")

    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception as exc:
        logger.warning("Failed to write LLM cache %s: %s", path, exc)
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def parse_llm_json(response_text: str) -> Any:
    """Parse JSON from Ollama, tolerating markdown fences and common JSON slips."""
    if not response_text:
        raise ValueError("empty LLM response")

    text = response_text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)

    decoder = json.JSONDecoder()
    starts = [idx for idx, char in enumerate(text) if char in "[{"]
    if not starts:
        raise ValueError("no JSON object or array found in LLM response")

    last_error: Exception | None = None
    for start in starts:
        candidate = text[start:].strip()
        try:
            value, _ = decoder.raw_decode(candidate)
            return value
        except json.JSONDecodeError as exc:
            last_error = exc

        repaired = _repair_common_json(candidate)
        if repaired != candidate:
            try:
                value, _ = decoder.raw_decode(repaired)
                return value
            except json.JSONDecodeError as exc:
                last_error = exc

    raise ValueError(str(last_error or "failed to parse LLM JSON"))


def _repair_common_json(text: str) -> str:
    """Repair safe, local JSON formatting mistakes produced by small LLMs."""
    repaired = text
    repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)
    repaired = re.sub(r"}\s*{", "},{", repaired)
    repaired = re.sub(r"]\s*\[", "],[", repaired)
    return repaired
