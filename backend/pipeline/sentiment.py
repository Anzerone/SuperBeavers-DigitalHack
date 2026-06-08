"""Lexicon-based sentiment detection for citizen complaints.

Канал sentiment описывает **эмоциональный тон** автора и отделён от
severity (объективная важность). Один и тот же CRITICAL inцидент
может быть написан спокойно или в ярости — это разные оси для PR-реакции.

Уровни:
- CALM       — нейтрально, описательно, благодарность
- NEUTRAL    — обычная жалоба без эмоций
- ANGRY      — раздражение, восклицания, ALL CAPS, мат
- DESPERATE  — отчаяние, угрозы, повторные обращения, обещание разобраться выше

Метод: вес слов из словаря + эвристики (CAPS, восклицательные, многоточия).
Быстро (200к/с на CPU), не требует LLM. Это базовая сигнатура — для прода
можно дополнить классификатором поверх эмбеддингов.
"""
from __future__ import annotations

import re

ANGRY_LEXICON = [
    "безобразие", "позор", "беспредел", "издеваются", "издевательство",
    "наглость", "оборзели", "бардак", "уроды", "хамство", "халтура",
    "обнаглели", "беспредел", "не могу больше", "сколько можно",
    "достал", "достало", "достали", "невозможно", "ужас", "кошмар",
    "невыносимо", "терпение лопнуло", "третий раз", "пятый раз",
    "звоните", "пришлите", "немедленно", "срочно", "почему",
    "не работает", "сломан", "разломан", "разбит",
    "грязь", "вонь", "воняет", "тараканы", "крысы",
]

DESPERATE_LEXICON = [
    "помогите", "умоляю", "прошу помощи", "не знаю что делать",
    "куда обращаться", "никто не помогает", "ребенок", "ребёнок",
    "инвалид", "пожилой", "одинокий", "погибну", "умру", "болею",
    "лекарств нет", "не выживем", "без денег", "не дозвониться",
    "обещали", "обманывают", "врут", "никто не слышит",
    "написал президенту", "буду писать", "обращусь в суд",
    "прокуратура", "уже месяц", "уже полгода", "уже год",
]

CALM_LEXICON = [
    "уведомить", "сообщить", "уточнить", "информац", "когда планируется",
    "будет ли", "запрос", "интересует", "благодар", "спасибо",
    "прошу разъяснить", "хотелось бы узнать",
]

WORD_RE = re.compile(r"[а-яА-ЯёЁa-zA-Z]+")
CAPS_RE = re.compile(r"[А-ЯA-Z]{4,}")  # подряд 4+ заглавных


def detect_sentiment(text: str) -> tuple[str, float]:
    """Возвращает (label, intensity 0..1).

    intensity — нормализованная сила негатива, удобно для сортировки
    «самые гневные жалобы».
    """
    if not text or not text.strip():
        return "NEUTRAL", 0.5

    t = text.lower()

    angry_hits = sum(1 for w in ANGRY_LEXICON if w in t)
    desperate_hits = sum(1 for w in DESPERATE_LEXICON if w in t)
    calm_hits = sum(1 for w in CALM_LEXICON if w in t)

    # Эвристики формы
    exclaim = text.count("!")
    caps_blocks = len(CAPS_RE.findall(text))
    triple_dots = text.count("...") + text.count("…")

    angry_score = angry_hits * 2 + exclaim * 0.5 + caps_blocks * 1.5
    desperate_score = desperate_hits * 2 + triple_dots * 0.8
    calm_score = calm_hits * 2

    total_neg = angry_score + desperate_score
    intensity = min(1.0, total_neg / 10.0)

    # Решение
    if desperate_score >= 4 and desperate_score > angry_score:
        return "DESPERATE", min(1.0, 0.7 + desperate_score / 20)
    if angry_score >= 4 and angry_score > calm_score:
        return "ANGRY", min(1.0, 0.6 + angry_score / 20)
    if calm_score >= 2 and calm_score > total_neg:
        return "CALM", 0.2
    return "NEUTRAL", min(0.6, 0.3 + intensity * 0.5)


def batch_sentiment(texts: list[str]) -> tuple[list[str], list[float]]:
    """Векторное применение к списку текстов."""
    labels: list[str] = []
    scores: list[float] = []
    for text in texts:
        label, score = detect_sentiment(text or "")
        labels.append(label)
        scores.append(round(score, 3))
    return labels, scores
