"""Обезличивание текста обращений и маскирование нецензурной лексики.

Применяется на этапе загрузки, до построения эмбеддингов и сохранения в БД,
поэтому персональные данные и мат не попадают ни в индекс похожих обращений,
ни в кластеры, ни в выгрузки/отчёты.
"""
from __future__ import annotations

import re

# --- Персональные данные ------------------------------------------------------

# VK-упоминания вида [id123|Имя] и [club123|Имя]
_VK_MENTION_RE = re.compile(r"\[(?:id|club|public|event)\d+\|[^\]]*\]", re.IGNORECASE)
# @-упоминания (логины соцсетей)
_AT_MENTION_RE = re.compile(r"@[A-Za-z0-9_.]{2,}")
# Email
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Телефоны: +7/8, скобки, дефисы и пробелы
_PHONE_RE = re.compile(
    r"(?:\+7|8|7)?[\s\-]?\(?\d{3,4}\)?[\s\-]?\d{2,3}[\s\-]?\d{2}[\s\-]?\d{2}"
)
# Любая последовательность из 7+ цифр (с разделителями) — вероятный телефон/счёт
_LONG_DIGITS_RE = re.compile(r"\b(?:\d[\s\-]?){7,}\d\b")

# --- Нецензурная лексика ------------------------------------------------------
# Подобраны стеммы с упором на точность (чтобы не задевать обычные слова вроде
# "хлеб", "требовать", "хутор", "херувим").
_PROFANITY_PATTERNS = [
    r"ху[йёеяю]\w*",
    r"\w*пизд\w*",
    r"\w*бля[дть]\w*",
    r"\bбля\b",
    r"\bеб[аёеоуыилнтчя]\w*", r"\bёб\w*",
    r"за[еёъ]б\w*", r"на[еёъ]б\w*", r"у[еёъ]б\w*", r"вы[еёъ]б\w*",
    r"в[ъь][еёъ]б\w*", r"до[еёъ]б\w*", r"от[ъь][еёъ]б\w*", r"разъ[еёъ]б\w*",
    r"объ[еёъ]б\w*", r"съ[еёъ]б\w*", r"долбо[её]б\w*",
    r"\bсук[аиоуые]\w*", r"\bсучь\w*",
    r"пидор\w*", r"пидар\w*", r"педик\w*",
    r"г[ао]ндон\w*",
    r"мудак\w*", r"мудач\w*", r"мудил\w*",
    r"херн\w*", r"\bхер\b", r"херов\w*", r"херя\w*", r"нахер\w*", r"похер\w*",
    r"гавн\w*", r"говн\w*",
    r"залуп\w*", r"мраз[ьи]\w*", r"дроч\w*",
]
_PROFANITY_RE = re.compile("|".join(_PROFANITY_PATTERNS), re.IGNORECASE)

_MULTISPACE_RE = re.compile(r"\s+")


def mask_profanity(text: str) -> str:
    """Заменить нецензурные слова на ***."""
    if not text:
        return text
    return _PROFANITY_RE.sub("***", text)


def anonymize_text(text: str) -> str:
    """Удалить персональные данные и замаскировать мат."""
    if not text:
        return text or ""

    text = _EMAIL_RE.sub("***", text)
    text = _VK_MENTION_RE.sub(" ", text)
    text = _AT_MENTION_RE.sub(" ", text)
    text = _PHONE_RE.sub("***", text)
    text = _LONG_DIGITS_RE.sub("***", text)
    text = mask_profanity(text)

    text = _MULTISPACE_RE.sub(" ", text).strip()
    return text
