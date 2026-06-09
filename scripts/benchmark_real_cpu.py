"""Benchmark the real CPU deployment path on the main input workbook."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["EMBEDDING_BACKEND"] = "onnx"
os.environ["EMBEDDING_DEVICE"] = "cpu"
os.environ["EMBEDDING_USE_FP16"] = "false"
os.environ["EMBEDDING_ONNX_DIR"] = "models/bge-m3-onnx-int8"
os.environ["EMBEDDING_ONNX_THREADS"] = "8"
os.environ["EMBEDDING_BATCH_SIZE"] = "32"
os.environ["EMBEDDING_MAX_SEQ_LENGTH"] = "256"
os.environ["EMBEDDING_LORA_PATH"] = ""
os.environ["EMBEDDING_CACHE_ENABLED"] = "0"
os.environ["LLM_MODEL"] = "qwen3:4b"
os.environ["LLM_CONCURRENCY"] = "3"
os.environ["LLM_VERIFY_CONCURRENCY"] = "2"
os.environ["CLUSTER_NAME_CONCURRENCY"] = "3"


def _log(event: str, started: float, **fields) -> None:
    payload = {
        "event": event,
        "elapsed_sec": round(time.time() - started, 2),
        **fields,
    }
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _main() -> None:
    started = time.time()

    from backend.pipeline.bootstrap import bootstrap_classify
    from backend.pipeline.embedder import compute_embeddings_dedup
    from backend.pipeline.loader import load_excel

    input_dir = PROJECT_ROOT / "data" / "input"
    xlsx_files = sorted(input_dir.glob("*.xlsx"), key=lambda path: path.stat().st_size, reverse=True)
    if not xlsx_files:
        raise FileNotFoundError(f"No .xlsx files in {input_dir}")
    filepath = xlsx_files[0]

    _log(
        "config",
        started,
        file=str(filepath),
        cpu_threads=8,
        embedding_backend=os.environ["EMBEDDING_BACKEND"],
        embedding_max_seq_length=int(os.environ["EMBEDDING_MAX_SEQ_LENGTH"]),
        embedding_batch_size=int(os.environ["EMBEDDING_BATCH_SIZE"]),
        llm_model=os.environ["LLM_MODEL"],
    )

    load_started = time.time()
    df, stats = load_excel(str(filepath))
    texts = df["incident_text"].tolist()
    groups = df["group"].fillna("").tolist() if "group" in df.columns else [""] * len(texts)
    load_sec = time.time() - load_started

    embed_inputs = [
        f"[{(group or '').strip()}] {text}" if (group or "").strip() else text
        for group, text in zip(groups, texts)
    ]
    unique_count = len({(text or "").strip() for text in embed_inputs})
    avg_chars = int(sum(len(text or "") for text in texts) / max(len(texts), 1))

    _log(
        "loaded",
        started,
        raw_rows=stats["raw_count"],
        filtered_rows=len(df),
        unique_embed_inputs=unique_count,
        dedup_pct=round((1 - unique_count / max(len(embed_inputs), 1)) * 100, 2),
        avg_chars=avg_chars,
        load_sec=round(load_sec, 2),
    )

    embed_started = time.time()

    def progress(done: int, total: int, message: str | None = None) -> None:
        elapsed = time.time() - embed_started
        if done == total or done % 2048 == 0:
            _log(
                "embedding_progress",
                started,
                done=done,
                total=total,
                embed_elapsed_sec=round(elapsed, 2),
                texts_per_sec=round(done / elapsed, 2) if elapsed > 0 else 0,
                message=message or "",
            )

    embeddings = compute_embeddings_dedup(embed_inputs, progress_callback=progress)
    embed_sec = time.time() - embed_started
    _log(
        "embeddings_done",
        started,
        rows=len(embed_inputs),
        unique_embed_inputs=unique_count,
        shape=list(embeddings.shape),
        embed_sec=round(embed_sec, 2),
        unique_texts_per_sec=round(unique_count / embed_sec, 2) if embed_sec > 0 else 0,
        rows_per_sec=round(len(embed_inputs) / embed_sec, 2) if embed_sec > 0 else 0,
    )

    cats = df["group"].dropna().unique().tolist() if "group" in df.columns else None
    if cats and "Другое" not in cats:
        cats.append("Другое")

    bootstrap_started = time.time()
    bootstrap = bootstrap_classify(texts, sample_size=min(300, len(texts)), categories=cats, groups=groups)
    bootstrap_sec = time.time() - bootstrap_started
    _log(
        "bootstrap_done",
        started,
        sample_size=len(bootstrap["indices"]),
        bootstrap_sec=round(bootstrap_sec, 2),
        rows_per_sec=round(len(bootstrap["indices"]) / bootstrap_sec, 2) if bootstrap_sec > 0 else 0,
    )

    _log(
        "done",
        started,
        total_sec=round(time.time() - started, 2),
        measured_parts_sec=round(load_sec + embed_sec + bootstrap_sec, 2),
    )


if __name__ == "__main__":
    _main()
