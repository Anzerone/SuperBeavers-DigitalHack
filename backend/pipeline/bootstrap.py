"""Bootstrap classification using batched Ollama calls."""
import logging
import random
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from backend.config import (
    CATEGORIES,
    LLM_BATCH_SIZE,
    LLM_CONCURRENCY,
    LLM_MODEL,
    LLM_TIMEOUT_SECONDS,
    OLLAMA_URL,
    SEVERITIES,
)
from backend.pipeline.llm_utils import get_cached_llm, parse_llm_json, set_cached_llm

logger = logging.getLogger(__name__)

CLASSIFY_SYSTEM = """Ты - классификатор обращений граждан Омской области.
Для каждого обращения определи:
1. is_problem: true/false (жалоба/нерешенная проблема = true, чистая благодарность/информация = false)
2. severity: CRITICAL|HIGH|MEDIUM|LOW (только если is_problem=true)
3. category: одна из [{categories}] (только если is_problem=true)

Правила:
- Благодарность + нерешенная проблема = true
- Вопрос о сроках решения = true
- Чистая благодарность или справочная информация = false

Ответ строго JSON: {{"results": [{{"id": 1, "is_problem": true, "severity": "MEDIUM", "category": "ЖКХ"}}]}}"""


def _default_result(position: int, categories: list[str] | None = None) -> dict:
    cats = categories or CATEGORIES
    return {
        "id": position + 1,
        "is_problem": True,
        "severity": "MEDIUM",
        "category": cats[-1],
    }


def _normalize_results(results, positions: list[int]) -> dict[int, dict]:
    """Map LLM ids back to sample positions, tolerating local ids after split retries."""
    by_position = {}
    positions_by_display_id = {pos + 1: pos for pos in positions}

    for order, raw in enumerate(results or []):
        if not isinstance(raw, dict):
            continue
        rid = raw.get("id")
        try:
            rid = int(rid)
        except (TypeError, ValueError):
            rid = None

        if rid in positions_by_display_id:
            position = positions_by_display_id[rid]
        elif rid is not None and 1 <= rid <= len(positions):
            position = positions[rid - 1]
        elif order < len(positions):
            position = positions[order]
        else:
            continue

        fixed = dict(raw)
        fixed["id"] = position + 1
        by_position[position] = fixed

    return by_position


def _call_ollama_batch(texts: list[str], positions: list[int], categories: list[str] | None = None, groups: list[str] | None = None, depth: int = 0) -> dict[int, dict]:
    """Call Ollama with a batch of texts for classification.

    `groups` (опц.) — параллельный список "Группа тем" для каждого текста.
    Если задан — добавляется в промпт как метка категории-подсказки.
    """
    cats = categories or CATEGORIES
    if groups and len(groups) == len(texts):
        numbered = "\n".join(
            f"{pos + 1}. [Группа: {g or 'не указана'}] {text[:330]}"
            for pos, text, g in zip(positions, texts, groups)
        )
    else:
        numbered = "\n".join(f"{pos + 1}. {text[:350]}" for pos, text in zip(positions, texts))
    prompt = f"Обращения:\n{numbered}\n\nJSON:"
    system = CLASSIFY_SYSTEM.format(categories=", ".join(cats))
    cache_payload = {
        "model": LLM_MODEL,
        "system": system,
        "texts": [text[:350] for text in texts],
        "groups": [(group or "") for group in groups] if groups else None,
        "positions": positions,
    }

    cached = get_cached_llm("bootstrap", cache_payload)
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
                "options": {"temperature": 0.1, "num_predict": 1800},
                "format": "json",
            },
            timeout=LLM_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = parse_llm_json(resp.json().get("response", ""))

        if isinstance(data, list):
            results = data
        elif isinstance(data, dict):
            results = data.get("results", data.get("items", []))
            if not results and "id" in data:
                results = [data]
        else:
            results = []

        normalized = _normalize_results(results, positions)
        set_cached_llm("bootstrap", cache_payload, normalized)
        return normalized
    except Exception as exc:
        logger.warning("LLM bootstrap batch failed: %s", exc)
        if len(texts) > 1 and depth < 2:
            mid = max(1, len(texts) // 2)
            sub_groups_l = groups[:mid] if groups else None
            sub_groups_r = groups[mid:] if groups else None
            merged = _call_ollama_batch(texts[:mid], positions[:mid], cats, sub_groups_l, depth + 1)
            merged.update(_call_ollama_batch(texts[mid:], positions[mid:], cats, sub_groups_r, depth + 1))
            return merged
        return {pos: _default_result(pos, cats) for pos in positions}


def bootstrap_classify(texts: list[str], sample_size: int = 500, categories: list[str] | None = None, groups: list[str] | None = None, progress_callback=None) -> dict:
    """Classify a deterministic sample of texts for LogReg training.

    `groups` (опц.) — параллельный с texts список "Группа тем", если он есть в датасете.
    Подсказка LLM ускоряет и улучшает качество классификации тяжести/проблемности.
    """
    cats = categories or CATEGORIES
    n_records = len(texts)
    sample_size = min(sample_size, n_records)
    rng = random.Random(42)
    indices = sorted(rng.sample(range(n_records), sample_size))
    sample_texts = [texts[i] for i in indices]
    sample_groups = [groups[i] for i in indices] if groups else None

    batches = []
    for start in range(0, len(sample_texts), LLM_BATCH_SIZE):
        end = min(start + LLM_BATCH_SIZE, len(sample_texts))
        batch_groups = sample_groups[start:end] if sample_groups else None
        batches.append((sample_texts[start:end], list(range(start, end)), batch_groups))

    logger.info(
        "Bootstrap: %s samples, %s batches, %s threads",
        len(sample_texts),
        len(batches),
        LLM_CONCURRENCY,
    )

    all_results = {}
    with ThreadPoolExecutor(max_workers=LLM_CONCURRENCY) as executor:
        futures = {
            executor.submit(_call_ollama_batch, btexts, positions, cats, bgroups): positions
            for btexts, positions, bgroups in batches
        }
        done = 0
        for future in as_completed(futures):
            all_results.update(future.result())
            done += 1
            if progress_callback:
                progress_callback(done, len(batches))
            if done % 5 == 0:
                logger.info("Bootstrap: %s/%s batches done", done, len(batches))

    is_problem = []
    severity = []
    category = []

    for i in range(len(sample_texts)):
        result = all_results.get(i, _default_result(i, cats))
        is_problem.append(bool(result.get("is_problem", True)))

        sev = result.get("severity", "MEDIUM")
        severity.append(sev if sev in SEVERITIES else "MEDIUM")

        cat = result.get("category", cats[-1])
        category.append(cat if cat in cats else cats[-1])

    logger.info("Bootstrap done: %s/%s classified by LLM", len(all_results), len(sample_texts))

    return {
        "indices": indices,
        "is_problem": is_problem,
        "severity": severity,
        "category": category,
    }
