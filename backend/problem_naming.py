"""Human-friendly names for problem clusters."""
from __future__ import annotations

from backend.config import CATEGORIES


RAW_NAME_MARKERS = (
    "пожалуйста",
    "прошу",
    "добрый день",
    "от читателя",
    "г омск",
    "г. омск",
    "когда",
    "может",
)

PROBLEM_PATTERNS = (
    (("маршрут", "автобус", "останов", "транспорт", "трамва", "троллейбус"), "Маршруты и остановки"),
    (("мусор", "отход", "контейнер", "свалк", "тко", "вывоз"), "Вывоз мусора и контейнерные площадки"),
    (("дорог", "ям", "асфальт", "тротуар", "проезж", "коле", "снег", "барьер"), "Состояние дорог и тротуаров"),
    (("светофор", "фонар", "освещ", "свет "), "Освещение и светофоры"),
    (("школ", "садик", "детск", "класс", "учител"), "Школы и детские сады"),
    (("поликлиник", "больниц", "врач", "минздрав", "лекарств", "скор"), "Доступность медицинской помощи"),
    (("киоск", "ларек", "торгов", "рынок", "магазин"), "Торговые объекты и услуги"),
    (("стадион", "спорт", "площадк", "волейбол", "баскет"), "Спортивная инфраструктура"),
    (("парк", "сквер", "дерев", "набереж", "зел", "эколог"), "Зеленые зоны и благоустройство"),
    (("строй", "постро", "ремонт", "дом", "архитект"), "Строительство и ремонт объектов"),
    (("газ", "электр", "тепл", "вод", "котельн", "авар"), "Коммунальные аварии и ресурсы"),
    (("полици", "безопас", "дпс", "наркот", "драк", "шум"), "Безопасность и правопорядок"),
)


def _category_or_default(category: str | None, categories: list[str] | None = None) -> str:
    cats = categories or CATEGORIES
    if category and category in cats:
        return category
    return cats[-1] if cats else "Другое"


def _compact_text(value: str | None) -> str:
    return " ".join((value or "").replace("...", " ").split())


def looks_like_raw_quote(name: str | None, category: str | None = None) -> bool:
    raw = (name or "").strip()
    value = _compact_text(raw).strip("\"'«» ")
    if not value:
        return True

    low = value.lower()
    words = value.split()
    if ("..." in raw or "…" in raw) and len(value) > 24:
        return True
    if value[:1].isdigit() and len(value) > 18:
        return True
    if len(words) > 10:
        return True
    if category and low.startswith(f"{category.lower()}:"):
        tail = value[len(category) + 1 :].strip()
        if len(tail) > 65 and any(ch in tail for ch in ".!?"):
            return True

    if len(value) > 90:
        return True
    if len(value) > 28 and any(marker in low for marker in RAW_NAME_MARKERS):
        return True
    if " бы " in low and len(value) > 22:
        return True
    if len(value) > 35 and any(ch in value for ch in "!?"):
        return True
    return False


def fallback_problem_name(
    category: str | None,
    *texts: str | None,
    categories: list[str] | None = None,
) -> str:
    category = _category_or_default(category, categories)
    haystack = " ".join(_compact_text(text).lower() for text in texts if text)

    for keywords, label in PROBLEM_PATTERNS:
        if any(keyword in haystack for keyword in keywords):
            return f"{category}: {label}"

    return f"Прочие повторяющиеся обращения по теме «{category}»"


def display_problem_name(
    cluster_name: str | None,
    category: str | None,
    description: str | None = None,
    centroid_text: str | None = None,
    example_texts: list[str] | None = None,
) -> str:
    name = _compact_text(cluster_name)
    if name and not looks_like_raw_quote(cluster_name, category):
        return name[:200]

    examples = example_texts or []
    return fallback_problem_name(category, name, description, centroid_text, *examples)[:200]
