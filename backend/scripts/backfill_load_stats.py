"""Backfill RunLoadStats for recent completed runs.

Закрытые до анализа и нерешаемые строки не сохраняются в appeals, поэтому для
старых прогонов разрезы по районам/категориям/месяцам пустые. Скрипт повторно
читает исходный файл (если он ещё лежит в data/input) и сохраняет агрегаты.

Запуск из корня проекта: python -m backend.scripts.backfill_load_stats
"""
import os

from backend.config import UPLOAD_DIR
from backend.pipeline.loader import load_excel
from backend.storage.database import get_sync_db, sync_engine
from backend.storage.models import Base, ProcessingRun, RunLoadStats

# Сколько последних завершённых прогонов обрабатывать (чтение больших файлов
# с обезличиванием текста занимает минуты — без ограничения скрипт может
# часами перечитывать одни и те же файлы по всем старым прогонам).
MAX_RUNS = 3


def main() -> None:
    Base.metadata.create_all(sync_engine)
    session = get_sync_db()
    try:
        runs = (
            session.query(ProcessingRun)
            .filter(ProcessingRun.status == "completed")
            .order_by(ProcessingRun.id.desc())
            .limit(MAX_RUNS)
            .all()
        )
        for run in runs:
            existing = session.query(RunLoadStats).filter(RunLoadStats.run_id == run.id).first()
            # Старые агрегаты могли не содержать срез resolution (средний срок по
            # закрытым до анализа). Если его нет — перечитываем файл и обновляем.
            has_resolution = bool(
                existing
                and ((existing.prefiltered or {}).get("resolved") or {}).get("resolution")
            )
            if existing and has_resolution:
                print(f"run {run.id}: уже есть агрегаты, пропуск")
                continue
            path = os.path.join(UPLOAD_DIR, run.filename or "")
            if not run.filename or not os.path.exists(path):
                print(f"run {run.id}: файл не найден ({run.filename}), пропуск")
                continue
            action = "обновляю" if existing else "читаю"
            print(f"run {run.id}: {action} {path} ...", flush=True)
            _, stats = load_excel(path)
            if stats.get("dropped_outcome") != (run.dropped_outcome or 0):
                print(
                    f"  внимание: dropped_outcome в файле={stats.get('dropped_outcome')}, "
                    f"в БД={run.dropped_outcome} (файл мог измениться)"
                )
            if existing:
                existing.prefiltered = stats.get("prefiltered") or {}
            else:
                session.add(RunLoadStats(run_id=run.id, prefiltered=stats.get("prefiltered") or {}))
            session.commit()
            resolved_total = stats["prefiltered"]["resolved"]["total"]
            unsolvable_total = stats["prefiltered"]["unsolvable"]["total"]
            print(f"  сохранено: закрыто до анализа={resolved_total}, нерешаемых={unsolvable_total}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
