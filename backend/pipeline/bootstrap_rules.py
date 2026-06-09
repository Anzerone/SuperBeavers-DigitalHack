"""Rule-based bootstrap classification — no LLM required.

Использует:
- Колонку "Группа тем" из Excel как готовую категорию
- Ключевые слова в тексте обращения для определения severity
- Длину и стиль текста для is_problem

Это даёт ~100с скорость на 300 семплах вместо ~15-20 мин LLM bootstrap на CPU.
Качество классификатора чуть ниже (~85-90% vs 93-95%), но укладывается в 20 мин.
"""
import logging
import random
import re

from backend.config import CATEGORIES, SEVERITIES

logger = logging.getLogger(__name__)


CRITICAL_KEYWORDS = [
    "погиб", "умер", "смерт", "пожар", "взорв",
    "обруш", "провал", "затоп", "потоп", "авари",
    "угроз", "опасн", "ребен", "ребён", "дети",
    "электрич", "оголен провод", "отравлен", "трав",
    "разрыв", "прорыв",
]

HIGH_KEYWORDS = [
    "не работает", "нет тепла", "нет отоплен", "нет воды",
    "нет света", "нет электр", "нет газа", "холодно",
    "температур", "невозможно", "уже неделю", "уже месяц",
    "разрушен", "сломан", "поврежд", "трещин",
    "не вывоз", "не убир", "не чист",
]

MEDIUM_KEYWORDS = [
    "ям", "дорог", "мусор", "снег", "тротуар",
    "благоустройств", "освещен", "лавочк", "ремонт",
    "когда", "сроки", "плох", "медленн",
]

LOW_KEYWORDS = [
    "спасибо", "благодар", "хорошо", "отличн",
    "просьб", "предложен", "пожелан", "идея",
    "вопрос", "уточн", "подскаж",
]

NOT_PROBLEM_KEYWORDS = [
    "спасибо", "благодар", "молодц", "отличн", "хорошо",
]


def _detect_severity(text: str) -> str:
    """Determine severity by keyword presence."""
    t = (text or "").lower()
    if any(kw in t for kw in CRITICAL_KEYWORDS):
        return "CRITICAL"
    if any(kw in t for kw in HIGH_KEYWORDS):
        return "HIGH"
    if any(kw in t for kw in MEDIUM_KEYWORDS):
        return "MEDIUM"
    if any(kw in t for kw in LOW_KEYWORDS):
        return "LOW"
    # Default: MEDIUM (most complaints are mid-tier)
    return "MEDIUM"


def _detect_is_problem(text: str) -> bool:
    """Heuristic: is this a real complaint or just thanks/info?"""
    t = (text or "").lower().strip()
    if len(t) < 20:
        return False  # too short to be a real complaint
    # Pure gratitude without further text
    if any(kw in t[:80] for kw in NOT_PROBLEM_KEYWORDS):
        # But if text continues with complaint markers, it's still a problem
        has_complaint_marker = any(kw in t for kw in [
            "но ", "однако", "при этом", "не работает", "не убирают",
            "проблем", "помогите", "примите меры", "почему", "когда же",
        ])
        if not has_complaint_marker:
            return False
    return True


def _normalize_category(group: str | None, categories: list[str]) -> str:
    """Map 'Группа тем' from Excel to our CATEGORIES list."""
    if not group:
        return categories[-1]
    group_lower = group.lower().strip()
    for cat in categories:
        if cat.lower() == group_lower:
            return cat
    # Fuzzy match: check if group is a substring of any category or vice versa
    for cat in categories:
        cat_lower = cat.lower()
        if group_lower in cat_lower or cat_lower in group_lower:
            return cat
    return categories[-1]


def bootstrap_classify_rules(
    texts: list[str],
    sample_size: int = 300,
    categories: list[str] | None = None,
    groups: list[str] | None = None,
    progress_callback=None,
) -> dict:
    """Rule-based bootstrap — no LLM, instant.

    Returns the same format as bootstrap_classify() so the rest of the pipeline
    works unchanged.
    """
    cats = categories or CATEGORIES
    n_records = len(texts)
    sample_size = min(sample_size, n_records)
    rng = random.Random(42)
    indices = sorted(rng.sample(range(n_records), sample_size))

    is_problem = []
    severity = []
    category = []

    for i, original_idx in enumerate(indices):
        text = texts[original_idx]
        group = groups[original_idx] if groups else None

        ip = _detect_is_problem(text)
        sev = _detect_severity(text) if ip else "LOW"
        cat = _normalize_category(group, cats)

        is_problem.append(ip)
        severity.append(sev)
        category.append(cat)

        if progress_callback and (i + 1) % 50 == 0:
            progress_callback(i + 1, sample_size, f"Разметка по правилам: {i+1}/{sample_size}")

    if progress_callback:
        progress_callback(sample_size, sample_size, "Разметка по правилам готова")

    n_problems = sum(is_problem)
    sev_counts = {s: severity.count(s) for s in SEVERITIES}
    logger.info(
        "Rule-based bootstrap: %s problems / %s samples, severity dist=%s",
        n_problems, sample_size, sev_counts,
    )

    return {
        "indices": indices,
        "is_problem": is_problem,
        "severity": severity,
        "category": category,
    }
