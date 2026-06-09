"""Cold-cache run on the FULL 400k file with detailed step timing."""
import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import asyncio
from backend.storage.database import init_db, ensure_default_user, get_sync_db
from backend.storage.models import ProcessingRun
from backend.pipeline.orchestrator import run_pipeline


class StepTimer:
    def __init__(self):
        self.current = None
        self.t = time.time()
        self.t_start = time.time()
        self.records: list[tuple[str, float]] = []

    def mark(self, step: str):
        now = time.time()
        if self.current is not None:
            elapsed = now - self.t
            self.records.append((self.current, elapsed))
            since_start = now - self.t_start
            print(f"  [{elapsed:6.1f}s] [+{since_start/60:5.1f}min] {self.current}", flush=True)
        self.current = step
        self.t = now

    def finish(self):
        if self.current is not None:
            elapsed = time.time() - self.t
            self.records.append((self.current, elapsed))
            since_start = time.time() - self.t_start
            print(f"  [{elapsed:6.1f}s] [+{since_start/60:5.1f}min] {self.current}", flush=True)
            self.current = None


import backend.pipeline.orchestrator as orch
_original_update_run = orch._update_run
_timer = StepTimer()


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
    print("=" * 70, flush=True)
    print("ХОЛОДНЫЙ ПРОГОН ПОЛНОГО ФАЙЛА 400к (кеши вычищены)", flush=True)
    print("=" * 70, flush=True)

    user_id = asyncio.run(setup())
    session = get_sync_db()
    run = ProcessingRun(user_id=user_id, filename="cold_400k", status="running")
    session.add(run)
    session.commit()
    session.refresh(run)
    run_id = run.id
    session.close()

    filepath = r"C:\Users\Iaroslav\Downloads\основной файл.xlsx"
    file_size_mb = os.path.getsize(filepath) / 1024 / 1024
    print(f"\nFile: {filepath} ({file_size_mb:.0f} MB)", flush=True)
    print(f"Run ID: {run_id}\n", flush=True)
    print("Шаги (время каждого):", flush=True)
    print("-" * 70, flush=True)

    t0 = time.time()
    try:
        run_pipeline(run_id, filepath)
    finally:
        _timer.finish()

    total = time.time() - t0
    print("-" * 70, flush=True)
    print(f"\nИТОГО: {total:.1f} сек  =  {total/60:.2f} мин\n", flush=True)

    session = get_sync_db()
    run = session.query(ProcessingRun).get(run_id)
    print(f"Из файла прочитано (raw): {run.raw_records or 'н/д'}", flush=True)
    print(f"Актуальных после фильтра: {run.total_records}", flush=True)
    print(f"Проблемных: {run.problem_count}", flush=True)
    print(f"Кластеров: {run.cluster_count}", flush=True)

    if run.total_records:
        rate = run.total_records / total
        print(f"\nСкорость: {rate:.0f} актуальных записей/сек", flush=True)
    session.close()


if __name__ == "__main__":
    main()
