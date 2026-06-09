"""Расчёт метрик производительности пайплайна (CPU vs CUDA, прогоны, fine-tune)."""
from __future__ import annotations

import gc
import math
import os
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import numpy as np
import torch
from sentence_transformers import SentenceTransformer
from sklearn.linear_model import LogisticRegression

from backend.config import (
    BOOTSTRAP_SAMPLE_SIZE,
    CLUSTER_NAME_CONCURRENCY,
    CLUSTER_NAME_MAX_TOTAL,
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_MAX_SEQ_LENGTH,
    LLM_BATCH_SIZE,
    LLM_CONCURRENCY,
    LLM_VERIFY_BATCH_SIZE,
    LLM_VERIFY_CONCURRENCY,
    LLM_VERIFY_MAX_RECORDS,
)
from backend.pipeline.loader import load_excel
from backend.pipeline.sentiment import batch_sentiment
from backend.scripts.finetune_bge import build_training_pairs

TEST_FILE = r"C:\Users\Iaroslav\Downloads\тестовый файл.xlsx"

# Среднее время одного раунда Ollama (эмпирика qwen3:8b, batch)
OLLAMA_BOOTSTRAP_ROUND_SEC = 25
OLLAMA_VERIFY_ROUND_SEC = 20
OLLAMA_NAME_ROUND_SEC = 8
DB_ROWS_PER_SEC = 8000


def fmt_min(sec: float) -> str:
    if sec < 60:
        return f"{sec:.0f} сек"
    return f"{sec / 60:.1f} мин"


def embed_bench(device: str, texts: list[str], n: int, fp16: bool = False) -> tuple[float, float]:
    model = SentenceTransformer("BAAI/bge-m3", device=device)
    model.max_seq_length = EMBEDDING_MAX_SEQ_LENGTH
    if fp16 and device.startswith("cuda"):
        model = model.half()
    sample = texts[:n]
    model.encode(sample[: min(32, n)], batch_size=EMBEDDING_BATCH_SIZE, show_progress_bar=False)
    if device.startswith("cuda"):
        torch.cuda.synchronize()
    t0 = time.time()
    model.encode(
        sample,
        batch_size=EMBEDDING_BATCH_SIZE,
        show_progress_bar=False,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    if device.startswith("cuda"):
        torch.cuda.synchronize()
    dt = time.time() - t0
    del model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return dt, n / dt


def main() -> None:
    print("=" * 72)
    print("МЕТРИКИ ПРОИЗВОДИТЕЛЬНОСТИ — Классификатор обращений")
    print("=" * 72)

    print("\n[1] ОКРУЖЕНИЕ")
    print(f"  CUDA доступна:     {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"  GPU:               {torch.cuda.get_device_name(0)}")
    print(f"  EMBEDDING_DEVICE:  {os.getenv('EMBEDDING_DEVICE', 'cuda')}")
    print(f"  max_seq_length:    {EMBEDDING_MAX_SEQ_LENGTH}")
    print(f"  batch_size:        {EMBEDDING_BATCH_SIZE}")

    print("\n[2] ТЕСТОВЫЙ ФАЙЛ")
    t0 = time.time()
    df, stats = load_excel(TEST_FILE)
    load_t = time.time() - t0
    texts = df["incident_text"].tolist()
    groups = df["group"].fillna("").tolist() if "group" in df.columns else [""] * len(texts)
    n = len(texts)
    raw = stats["raw_count"]
    avg_chars = sum(len(t or "") for t in texts) // max(n, 1)
    uniq = len({(t or "").strip() for t in texts})
    dedup_pct = (1 - uniq / max(n, 1)) * 100
    pairs = build_training_pairs(texts, groups, max_pairs_per_group=400)
    pairs_per_row = len(pairs) / max(n, 1)

    print(f"  Файл:              {TEST_FILE}")
    print(f"  Сырых строк:       {raw:,}")
    print(f"  После фильтров:    {n:,} ({n / max(raw, 1) * 100:.1f}%)")
    print(f"  Загрузка Excel:    {load_t:.1f} сек")
    print(f"  Средняя длина:     {avg_chars} символов")
    print(f"  Дедуп текстов:     {uniq:,} уникальных ({dedup_pct:.1f}% дублей)")
    print(f"  Fine-tune пар:     {len(pairs):,} ({pairs_per_row:.2f} на строку)")

    print("\n[3] СКОРОСТЬ ЭМБЕДДИНГОВ (bge-m3)")
    cuda_dt, cuda_tps = embed_bench("cuda", texts, n, fp16=True)
    cpu_dt, cpu_tps = embed_bench("cpu", texts, min(128, n), fp16=False)
    print(f"  CUDA fp16 ({n:,} rows):  {cuda_dt:.1f} sec  ->  {cuda_tps:.0f} texts/s")
    print(f"  CPU fp32 (128 rows):      {cpu_dt:.1f} sec  ->  {cpu_tps:.0f} texts/s")
    print(f"  CUDA/CPU speedup:         {cuda_tps / cpu_tps:.0f}x")

    print("\n[4] БЫСТРЫЕ ЭТАПЫ (замер на тестовом файле)")
    t0 = time.time()
    batch_sentiment(texts)
    sent_t = time.time() - t0
    X = np.random.randn(n, 1024).astype(np.float32)
    y = np.random.randint(0, 2, size=n)
    t0 = time.time()
    clf = LogisticRegression(max_iter=250, solver="liblinear")
    clf.fit(X[: min(500, n)], y[: min(500, n)])
    clf.predict(X)
    lr_t = time.time() - t0
    print(f"  Sentiment ({n:,}):         {sent_t:.3f} сек")
    print(f"  LogReg train500+pred:      {lr_t:.2f} сек")

    bootstrap_batches = math.ceil(BOOTSTRAP_SAMPLE_SIZE / LLM_BATCH_SIZE)
    bootstrap_rounds = math.ceil(bootstrap_batches / LLM_CONCURRENCY)
    verify_batches = math.ceil(LLM_VERIFY_MAX_RECORDS / LLM_VERIFY_BATCH_SIZE)
    verify_rounds = math.ceil(verify_batches / LLM_VERIFY_CONCURRENCY)
    name_rounds = math.ceil(CLUSTER_NAME_MAX_TOTAL / CLUSTER_NAME_CONCURRENCY)

    print("\n[5] LLM-ПАРАМЕТРЫ (Ollama qwen3:8b)")
    print(f"  Bootstrap:  {BOOTSTRAP_SAMPLE_SIZE} примеров, {bootstrap_batches} батчей, {bootstrap_rounds} раундов ×4")
    print(f"  Verify:     ≤{LLM_VERIFY_MAX_RECORDS} записей, {verify_rounds} раундов")
    print(f"  Именование: ≤{CLUSTER_NAME_MAX_TOTAL} кластеров, {name_rounds} раундов ×4")

    def pipeline_stages(
        rows: int,
        device: str,
        *,
        first_run: bool,
        cached_file: bool,
    ) -> dict[str, float]:
        scale = rows / max(n, 1)
        effective = int(rows * (uniq / max(n, 1)))

        if cached_file:
            embed = 2.0
        elif device == "cuda":
            embed = effective / cuda_tps
        else:
            embed = effective / cpu_tps

        load = load_t * scale
        bootstrap = bootstrap_rounds * OLLAMA_BOOTSTRAP_ROUND_SEC if first_run else 0
        verify = verify_rounds * OLLAMA_VERIFY_ROUND_SEC
        logreg = lr_t * scale
        sentiment = sent_t * scale
        dbsave = rows / DB_ROWS_PER_SEC
        problems = rows * 0.35
        cluster = max(30.0, problems / 500)
        naming = 0.0 if cached_file else name_rounds * OLLAMA_NAME_ROUND_SEC
        summaries = 5.0 if cached_file else 60.0
        total = load + embed + bootstrap + verify + logreg + sentiment + dbsave + cluster + naming + summaries
        return {
            "load": load,
            "embed": embed,
            "bootstrap": bootstrap,
            "verify": verify,
            "logreg": logreg,
            "sentiment": sentiment,
            "dbsave": dbsave,
            "cluster": cluster,
            "naming": naming,
            "summaries": summaries,
            "total": total,
        }

    datasets = [
        ("26 000", 26_000),
        ("400 000", 400_000),
    ]
    scenarios = [
        ("1-й прогон", True, False),
        ("Повтор (тот же файл)", False, True),
        ("Новый файл (классификатор есть)", False, False),
    ]

    print("\n[6] ПОЛНЫЙ ПАЙПЛАЙН — РАЗБИВКА ПО ЭТАПАМ (мин)")
    headers = ["Этап", "CUDA 26k", "CPU 26k", "CUDA 400k", "CPU 400k"]
    stage_keys = [
        ("load", "Загрузка Excel"),
        ("embed", "Эмбеддинги"),
        ("bootstrap", "Bootstrap LLM"),
        ("verify", "Verify LLM"),
        ("logreg", "LogReg"),
        ("sentiment", "Sentiment"),
        ("dbsave", "Запись в БД"),
        ("cluster", "DBSCAN + ранж."),
        ("naming", "Именование LLM"),
        ("summaries", "Сводки LLM"),
        ("total", "ИТОГО"),
    ]

    # first run only for table
    results: dict[tuple[int, str], dict[str, float]] = {}
    for label, rows in datasets:
        for dev in ("cuda", "cpu"):
            results[(rows, dev)] = pipeline_stages(rows, dev, first_run=True, cached_file=False)

    print(f"  {'Этап':<22} {'CUDA 26k':>10} {'CPU 26k':>10} {'CUDA 400k':>10} {'CPU 400k':>10}")
    print("  " + "-" * 66)
    for key, label in stage_keys:
        row = [label]
        for _, rows in datasets:
            for dev in ("cuda", "cpu"):
                row.append(fmt_min(results[(rows, dev)][key]))
        print(f"  {row[0]:<22} {row[1]:>10} {row[2]:>10} {row[3]:>10} {row[4]:>10}")

    print("\n[7] СЦЕНАРИИ ПРОГОНА — ИТОГО")
    print(f"  {'Сценарий':<32} {'26k CUDA':>10} {'26k CPU':>10} {'400k CUDA':>10} {'400k CPU':>10}")
    print("  " + "-" * 76)
    for scen_label, first, cached in scenarios:
        cols = [scen_label]
        for _, rows in datasets:
            for dev in ("cuda", "cpu"):
                s = pipeline_stages(rows, dev, first_run=first, cached_file=cached)
                cols.append(fmt_min(s["total"]))
        print(f"  {cols[0]:<32} {cols[1]:>10} {cols[2]:>10} {cols[3]:>10} {cols[4]:>10}")

    print("\n[8] FINE-TUNING (LoRA, 1 epoch, batch=32)")
    print(f"  {'Объём':<12} {'Пар':>10} {'Шагов':>8} {'CUDA':>12} {'CPU':>12}")
    print("  " + "-" * 58)
    for label, rows in datasets:
        est_pairs = int(pairs_per_row * rows)
        steps = max(1, est_pairs // 32)
        cuda_ft = steps * 3
        cpu_ft = steps * 45
        print(f"  {label:<12} {est_pairs:>10,} {steps:>8,} {fmt_min(cuda_ft):>12} {fmt_min(cpu_ft):>12}")

    print("\n[9] ЦЕЛЕВЫЕ ПОКАЗАТЕЛИ vs ФАКТ (CUDA)")
    targets = [
        ("26k — 1-й прогон", 26_000, 15, 35),
        ("26k — повтор", 26_000, 2, 12),
        ("400k — 1-й прогон", 400_000, 20, 60),
        ("400k — повтор", 400_000, 10, 25),
    ]
    print(f"  {'Сценарий':<24} {'Цель':>14} {'Расчёт CUDA':>14} {'Статус':>10}")
    print("  " + "-" * 64)
    for label, rows, lo, hi in targets:
        cached = "повтор" in label
        first = not cached
        est = pipeline_stages(rows, "cuda", first_run=first, cached_file=cached)["total"] / 60
        target = f"{lo}–{hi} мин"
        status = "OK" if lo <= est <= hi * 1.2 else ("HIGH" if est > hi * 1.2 else "LOW")
        print(f"  {label:<24} {target:>14} {est:>12.1f} мин {status:>10}")

    print("\n[10] РЕКОМЕНДАЦИИ")
    print("  • EMBEDDING_DEVICE=cuda required (CPU on 26k ~48 min embeddings only)")
    print("  • Повтор того же файла: EMBEDDING_CACHE + CLASSIFIER_CACHE → минус bootstrap")
    print("  • Fine-tuning на CPU для 400k нецелесообразен (сутки+)")
    print("=" * 72)


if __name__ == "__main__":
    main()
