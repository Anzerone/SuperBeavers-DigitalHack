"""Кооперативная отмена обработки.

Флаг живёт в памяти процесса: эндпоинт ставит отмену, пайплайн проверяет её
на каждом обновлении прогресса и прерывается ближайшим шагом.
"""
import threading

_lock = threading.Lock()
_cancelled: set[int] = set()


class PipelineCancelled(Exception):
    """Обработка остановлена пользователем."""


def request_cancel(run_id: int) -> None:
    with _lock:
        _cancelled.add(run_id)


def is_cancelled(run_id: int) -> bool:
    with _lock:
        return run_id in _cancelled


def clear(run_id: int) -> None:
    with _lock:
        _cancelled.discard(run_id)
