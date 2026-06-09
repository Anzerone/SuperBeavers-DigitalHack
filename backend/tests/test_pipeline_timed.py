"""Run the full pipeline on a real test file with cold caches and time each step."""
import sys
import os
import time
import logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import asyncio
from backend.storage.database import init_db, ensure_default_user, get_sync_db
from backend.storage.models import ProcessingRun
from backend.pipeline.orchestrator import run_pipeline


# Step timing collector
STEP_TIMES: dict[str, float] = {}
_step_starts: dict[str, float] = {}


class StepTimerFilter(logging.Filter):
    """Watch orchestrator's _update_run calls (via current_step changes) to time steps."""
    def __init__(self):
        super().__init__()
        self.current = None
        self.t = time.time()
        self.records: list[tuple[str, float]] = []

    def mark(self, step: str):
        now = time.time()
        if self.current is not None:
            elapsed = now - self.t
            self.records.append((self.current, elapsed))
            print(f"  [{elapsed:6.1f}s] {self.current}")
        self.current = step
        self.t = now

    def finish(self):
        if self.current is not None:
            elapsed = time.time() - self.t
            self.records.append((self.current, elapsed))
            print(f"  [{elapsed:6.1f}s] {self.current}")
            self.current = None


# Patch _update_run to capture steps
import backend.pipeline.orchestrator as orch
_original_update_run = orch._update_run
_timer = StepTimerFilter()


def _patched_update_run(session, run_id, **kwargs):
    step = kwargs.get("current_step")
    if step and step != _timer.current:
        _timer.mark(step)
    return _original_update_run(session, run_id, **kwargs)


orch._update_run = _patched_update_run


async def setup():
    await init_db()
    user_id = await ensure_default_user()
    return user_id


def main():
    print("=" * 70)
    print("ХОЛОДНЫЙ ПРОГОН ПАЙПЛАЙНА (кеши вычищены)")
    print("=" * 70)

    user_id = asyncio.run(setup())
    session = get_sync_db()
    run = ProcessingRun(user_id=user_id, filename="test_timed", status="running")
    session.add(run)
    session.commit()
    session.refresh(run)
    run_id = run.id
    session.close()

    filepath = r"C:\Users\Iaroslav\Downloads\тестовый файл.xlsx"
    print(f"\nFile: {filepath}")
    print(f"Run ID: {run_id}\n")
    print("Шаги (время каждого):")
    print("-" * 70)

    t0 = time.time()
    try:
        run_pipeline(run_id, filepath)
    finally:
        _timer.finish()

    total = time.time() - t0
    print("-" * 70)
    print(f"\nИТОГО: {total:.1f} сек  =  {total/60:.2f} мин\n")

    # Get DB stats
    session = get_sync_db()
    run = session.query(ProcessingRun).get(run_id)
    print(f"Записей: {run.total_records}, проблемных: {run.problem_count}, кластеров: {run.cluster_count}")

    if run.total_records:
        rate = run.total_records / total
        scale_400k = 400_000 / rate / 60
        print(f"\nСкорость: {rate:.0f} записей/сек")
        print(f"Экстраполяция на 400k: ~{scale_400k:.1f} мин")
    session.close()


if __name__ == "__main__":
    main()
