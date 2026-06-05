"""Quick test of the full pipeline on a small sample."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import asyncio
from backend.storage.database import init_db, ensure_default_user, get_sync_db
from backend.storage.models import ProcessingRun
from backend.pipeline.orchestrator import run_pipeline


async def setup():
    await init_db()
    user_id = await ensure_default_user()
    return user_id


def main():
    user_id = asyncio.run(setup())

    # Create run
    session = get_sync_db()
    run = ProcessingRun(user_id=user_id, filename="test", status="running")
    session.add(run)
    session.commit()
    session.refresh(run)
    run_id = run.id
    session.close()

    filepath = r"C:\Users\Iaroslav\Downloads\тестовый файл.xlsx"
    print(f"Starting pipeline for run_id={run_id}, file={filepath}")
    run_pipeline(run_id, filepath)
    print("Pipeline completed!")


if __name__ == "__main__":
    main()
