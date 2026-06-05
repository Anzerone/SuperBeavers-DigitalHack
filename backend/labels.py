SEVERITY_LABELS = {
    "CRITICAL": "Критическая",
    "HIGH": "Высокая",
    "MEDIUM": "Средняя",
    "LOW": "Низкая",
}


def format_severity(value: str | None) -> str:
    return SEVERITY_LABELS.get(value or "", value or "Не определено")
