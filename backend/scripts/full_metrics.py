"""Полный расчёт метрик (продуктовых и технических) для классификатора обращений.

Эталоны:
- Категория: колонка "Группа тем" (group) — реальная человеческая разметка (26 классов).
- is_problem / severity: валидационная выборка (VALIDATION_SAMPLE), размеченная
  независимым LLM-экспертом (qwen3:8b) — сравниваем с быстрым классификатором.

Запуск:
    $env:PYTHONIOENCODING='utf-8'; python -m backend.scripts.full_metrics
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import numpy as np

from backend.config import CATEGORIES, SEVERITIES, DBSCAN_EPS, DBSCAN_MIN_SAMPLES

MAIN_FILE = r"C:\Users\Iaroslav\Desktop\олимпиады\класификатор жалоб\data\input\основной файл.xlsx"
VALIDATION_SAMPLE = 120
KNN_QUERY_SAMPLE = 5000
SILHOUETTE_SAMPLE = 5000
RANDOM_SEED = 42

results: dict = {}


def section(title: str) -> None:
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


def main() -> None:
    t_start = time.time()

    # ------------------------------------------------------------------ #
    # 1. Загрузка данных
    # ------------------------------------------------------------------ #
    section("[A] ЗАГРУЗКА ДАННЫХ")
    from backend.pipeline.loader import load_excel

    df, stats = load_excel(MAIN_FILE)
    texts = [str(t or "") for t in df["incident_text"].tolist()]
    groups = df["group"].fillna("").astype(str).tolist() if "group" in df.columns else [""] * len(texts)
    municipalities = df["municipality"].fillna("").astype(str).tolist() if "municipality" in df.columns else [""] * len(texts)
    n = len(texts)
    raw = stats["raw_count"]
    print(f"RAW строк:        {raw:,}")
    print(f"После фильтров:   {n:,} ({n / raw * 100:.1f}%)")
    print(f"Отфильтровано:    {raw - n:,} ({(raw - n) / raw * 100:.1f}%)")

    # ------------------------------------------------------------------ #
    # 2. Эмбеддинги (dedup-aware) + предсказания кешированного классификатора
    # ------------------------------------------------------------------ #
    section("[B] ЭМБЕДДИНГИ + КЛАССИФИКАТОР")
    from backend.pipeline.embedder import compute_embeddings_dedup
    from backend.pipeline.classifier import predict_with_cache

    # conditioning как в проде: префикс группы
    embed_inputs = [f"[{g.strip()}] {t}" if g.strip() else t for g, t in zip(groups, texts)]

    t0 = time.time()
    embeddings = compute_embeddings_dedup(embed_inputs)
    embed_t = time.time() - t0
    print(f"Эмбеддинги: shape={embeddings.shape}, время={embed_t:.1f}с ({n / embed_t:.0f} текст/с)")

    dynamic_cats = sorted({g.strip() for g in groups if g.strip()})
    if "Другое" not in dynamic_cats:
        dynamic_cats.append("Другое")

    t0 = time.time()
    preds = predict_with_cache(embeddings, categories=dynamic_cats)
    if preds is None:
        print("ВНИМАНИЕ: кеш классификатора не подошёл, обучаю заново на bootstrap...")
        from backend.pipeline.bootstrap import bootstrap_classify
        from backend.pipeline.classifier import train_and_predict

        boot = bootstrap_classify(texts, sample_size=500, categories=dynamic_cats, groups=groups)
        preds = train_and_predict(embeddings, boot, categories=dynamic_cats)
    pred_t = time.time() - t0
    is_problem = np.array(preds["is_problem"])
    pred_sev = np.array(preds["severity"])
    pred_cat = np.array(preds["category"])
    confidence = np.array(preds["confidence"])
    print(f"Классификация: {pred_t:.1f}с, метод={preds['method'][0]}")

    # категория в проде переопределяется группой, если она валидна
    final_cat = np.array([
        g.strip() if g.strip() in dynamic_cats else pc
        for g, pc in zip(groups, pred_cat)
    ])

    # ------------------------------------------------------------------ #
    # 3. ПРОДУКТОВЫЕ МЕТРИКИ
    # ------------------------------------------------------------------ #
    section("[C] ПРОДУКТОВЫЕ МЕТРИКИ")
    from backend.pipeline.sentiment import batch_sentiment

    uniq = len({t.strip() for t in texts})
    dedup_pct = (1 - uniq / n) * 100
    problem_count = int(is_problem.sum())
    problem_pct = problem_count / n * 100

    sent_labels, _ = batch_sentiment(texts)
    sent_labels = np.array(sent_labels)
    sent_problem = sent_labels[is_problem]
    angry = int(np.isin(sent_problem, ["ANGRY", "DESPERATE"]).sum())
    angry_pct = angry / max(problem_count, 1) * 100

    print(f"Покрытие обработкой:   {n:,} / {raw:,} = {n / raw * 100:.1f}%")
    print(f"Дубли (дедуп):         {dedup_pct:.1f}% ({uniq:,} уникальных)")
    print(f"Проблемных:            {problem_count:,} ({problem_pct:.1f}% от обработанных)")
    print(f"Муниципалитетов:       {len({m for m in municipalities if m}):,}")
    print(f"Категорий (группа):    {len(dynamic_cats)}")
    print(f"Негатив (гнев+отчаяние): {angry_pct:.1f}% от проблем")

    print("\nРаспределение по тяжести (проблемные):")
    sev_counts = Counter(pred_sev[is_problem])
    for sev in SEVERITIES:
        c = sev_counts.get(sev, 0)
        print(f"  {sev:10} {c:>7,}  ({c / max(problem_count,1)*100:5.1f}%)")

    print("\nТоп-8 категорий (проблемные, по группе):")
    cat_counts = Counter(final_cat[is_problem])
    for cat, c in cat_counts.most_common(8):
        print(f"  {cat:38} {c:>7,}")

    print("\nТоп-8 муниципалитетов (проблемные):")
    muni_arr = np.array(municipalities)
    muni_counts = Counter(muni_arr[is_problem])
    for m, c in muni_counts.most_common(8):
        print(f"  {m:38} {c:>7,}")

    print("\nТональность (проблемные):")
    for lab, c in Counter(sent_problem).most_common():
        print(f"  {lab:12} {c:>7,}  ({c / max(problem_count,1)*100:5.1f}%)")

    results["product"] = {
        "raw": raw, "filtered": n, "coverage_pct": round(n / raw * 100, 1),
        "dedup_pct": round(dedup_pct, 1), "problem_count": problem_count,
        "problem_pct": round(problem_pct, 1),
        "municipalities": len({m for m in municipalities if m}),
        "categories": len(dynamic_cats), "negative_pct": round(angry_pct, 1),
        "severity": {s: sev_counts.get(s, 0) for s in SEVERITIES},
    }

    # ------------------------------------------------------------------ #
    # 4. ТЕХНИЧЕСКИЕ: КЛАССИФИКАЦИЯ КАТЕГОРИИ (эталон = Группа тем)
    # ------------------------------------------------------------------ #
    section("[D] КЛАССИФИКАЦИЯ КАТЕГОРИИ (эталон = «Группа тем»)")
    from sklearn.model_selection import train_test_split
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, f1_score, classification_report

    valid_mask = np.array([g.strip() in dynamic_cats for g in groups])
    Xv = embeddings[valid_mask]
    yv = np.array([g.strip() for g in groups])[valid_mask]
    print(f"Размеченных строк (группа валидна): {len(yv):,}")
    print(f"Уникальных классов:                 {len(set(yv))}")

    X_tr, X_te, y_tr, y_te = train_test_split(
        Xv, yv, test_size=0.3, random_state=RANDOM_SEED, stratify=None
    )
    clf = LogisticRegression(max_iter=300, C=1.0, class_weight="balanced", solver="liblinear")
    # OneVsRest для многоклассовости
    from sklearn.multiclass import OneVsRestClassifier
    clf = OneVsRestClassifier(clf)
    t0 = time.time()
    clf.fit(X_tr, y_tr)
    fit_t = time.time() - t0
    y_pred = clf.predict(X_te)

    # top-3 accuracy
    proba = clf.predict_proba(X_te)
    classes = clf.classes_
    top3_idx = np.argsort(proba, axis=1)[:, -3:]
    top3_hit = np.array([
        y_te[i] in classes[top3_idx[i]] for i in range(len(y_te))
    ])

    acc = accuracy_score(y_te, y_pred)
    macro_f1 = f1_score(y_te, y_pred, average="macro", zero_division=0)
    weighted_f1 = f1_score(y_te, y_pred, average="weighted", zero_division=0)
    top3_acc = top3_hit.mean()

    print(f"Обучение LogReg: {fit_t:.1f}с на {len(y_tr):,} примерах")
    print(f"Accuracy (top-1):   {acc * 100:.1f}%")
    print(f"Top-3 accuracy:     {top3_acc * 100:.1f}%")
    print(f"Macro-F1:           {macro_f1:.3f}")
    print(f"Weighted-F1:        {weighted_f1:.3f}")

    results["category"] = {
        "labeled_rows": int(len(yv)), "classes": int(len(set(yv))),
        "accuracy": round(acc * 100, 1), "top3_accuracy": round(top3_acc * 100, 1),
        "macro_f1": round(macro_f1, 3), "weighted_f1": round(weighted_f1, 3),
    }

    # ------------------------------------------------------------------ #
    # 5. ТЕХНИЧЕСКИЕ: КАЧЕСТВО ЭМБЕДДИНГОВ (kNN purity по категориям)
    # ------------------------------------------------------------------ #
    section("[E] КАЧЕСТВО ЭМБЕДДИНГОВ (kNN purity, эталон = группа)")
    from sklearn.neighbors import NearestNeighbors

    rng = np.random.default_rng(RANDOM_SEED)
    idx_all = np.where(valid_mask)[0]
    emb_v = embeddings[idx_all]
    lab_v = np.array([groups[i].strip() for i in idx_all])
    q = min(KNN_QUERY_SAMPLE, len(emb_v))
    q_idx = rng.choice(len(emb_v), size=q, replace=False)

    nn = NearestNeighbors(n_neighbors=6, metric="cosine")
    nn.fit(emb_v)
    _, neigh = nn.kneighbors(emb_v[q_idx])
    purities = []
    for row, qi in zip(neigh, q_idx):
        neighbors = [r for r in row if r != qi][:5]
        same = sum(1 for r in neighbors if lab_v[r] == lab_v[qi])
        purities.append(same / len(neighbors))
    knn_purity = float(np.mean(purities))
    # 1-NN accuracy
    nn1 = []
    for row, qi in zip(neigh, q_idx):
        first = next((r for r in row if r != qi), qi)
        nn1.append(lab_v[first] == lab_v[qi])
    nn1_acc = float(np.mean(nn1))

    print(f"Запросов:           {q:,}")
    print(f"1-NN accuracy:      {nn1_acc * 100:.1f}%  (ближайший сосед той же категории)")
    print(f"5-NN purity:        {knn_purity * 100:.1f}%  (доля соседей той же категории)")
    results["embeddings"] = {
        "knn1_acc": round(nn1_acc * 100, 1), "knn5_purity": round(knn_purity * 100, 1),
    }

    # ------------------------------------------------------------------ #
    # 6. ТЕХНИЧЕСКИЕ: КАЧЕСТВО КЛАСТЕРИЗАЦИИ (DBSCAN как в проде)
    # ------------------------------------------------------------------ #
    section("[F] КАЧЕСТВО КЛАСТЕРИЗАЦИИ (DBSCAN per район×категория)")
    from sklearn.cluster import DBSCAN
    from sklearn.metrics import silhouette_score

    prob_idx = np.where(is_problem)[0]
    prob_emb = embeddings[prob_idx]
    prob_muni = muni_arr[prob_idx]
    prob_cat = final_cat[prob_idx]

    total_clusters = 0
    total_noise = 0
    total_clustered = 0
    groups_processed = 0
    sizes = []
    # группировка по (муниципалитет, категория)
    from collections import defaultdict
    buckets = defaultdict(list)
    for i in range(len(prob_idx)):
        buckets[(prob_muni[i], prob_cat[i])].append(i)

    for key, members in buckets.items():
        if len(members) < DBSCAN_MIN_SAMPLES:
            total_noise += len(members)
            continue
        sub = prob_emb[members]
        db = DBSCAN(eps=DBSCAN_EPS, min_samples=DBSCAN_MIN_SAMPLES, metric="cosine")
        labels = db.fit_predict(sub)
        n_clusters = len({l for l in labels if l != -1})
        noise = int((labels == -1).sum())
        total_clusters += n_clusters
        total_noise += noise
        total_clustered += len(members) - noise
        groups_processed += 1
        for lab in set(labels):
            if lab != -1:
                sizes.append(int((labels == lab).sum()))

    compression = problem_count / max(total_clusters, 1)
    noise_pct = total_noise / max(problem_count, 1) * 100
    print(f"Проблемных обращений:    {problem_count:,}")
    print(f"Групп (район×категория): {groups_processed:,}")
    print(f"Кластеров найдено:       {total_clusters:,}")
    print(f"Шум (вне кластеров):     {total_noise:,} ({noise_pct:.1f}%)")
    print(f"Сжатие (обращ./кластер): {compression:.1f}×")
    if sizes:
        print(f"Размер кластера: median={int(np.median(sizes))}, max={max(sizes)}, avg={np.mean(sizes):.1f}")

    # silhouette по категориям (насколько эмбеддинги разделимы) — на сэмпле
    s_idx = rng.choice(len(emb_v), size=min(SILHOUETTE_SAMPLE, len(emb_v)), replace=False)
    try:
        sil = silhouette_score(emb_v[s_idx], lab_v[s_idx], metric="cosine")
    except Exception:
        sil = float("nan")
    print(f"Silhouette (по категориям, n={len(s_idx):,}): {sil:.3f}")
    results["clustering"] = {
        "clusters": total_clusters, "noise_pct": round(noise_pct, 1),
        "compression": round(compression, 1), "silhouette": round(float(sil), 3),
    }

    # ------------------------------------------------------------------ #
    # 7. ВАЛИДАЦИЯ is_problem / severity на выборке через LLM-эксперта
    # ------------------------------------------------------------------ #
    section(f"[G] ВАЛИДАЦИЯ is_problem/severity (LLM-эксперт, n={VALIDATION_SAMPLE})")
    from backend.pipeline.bootstrap import _call_ollama_batch
    from backend.config import LLM_BATCH_SIZE

    rng2 = np.random.default_rng(RANDOM_SEED)
    val_idx = sorted(rng2.choice(n, size=min(VALIDATION_SAMPLE, n), replace=False).tolist())
    val_texts = [texts[i] for i in val_idx]
    val_groups = [groups[i] for i in val_idx]

    print(f"Размечаю {len(val_idx)} обращений независимым LLM (qwen3:8b)...")
    llm_labels: dict[int, dict] = {}
    t0 = time.time()
    for start in range(0, len(val_texts), LLM_BATCH_SIZE):
        end = min(start + LLM_BATCH_SIZE, len(val_texts))
        btexts = val_texts[start:end]
        positions = list(range(start, end))
        bgroups = val_groups[start:end]
        res = _call_ollama_batch(btexts, positions, dynamic_cats, bgroups)
        llm_labels.update(res)
    llm_t = time.time() - t0
    print(f"LLM-разметка: {llm_t:.1f}с")

    # сравнение is_problem (LLM = эталон, классификатор = предсказание)
    y_true_prob, y_pred_prob = [], []
    sev_true, sev_pred = [], []
    for pos, gi in enumerate(val_idx):
        lab = llm_labels.get(pos)
        if not lab:
            continue
        gt = bool(lab.get("is_problem", True))
        pr = bool(is_problem[gi])
        y_true_prob.append(gt)
        y_pred_prob.append(pr)
        if gt and pr:
            sev_true.append(lab.get("severity", "MEDIUM"))
            sev_pred.append(str(pred_sev[gi]))

    y_true_prob = np.array(y_true_prob)
    y_pred_prob = np.array(y_pred_prob)
    tp = int(((y_pred_prob == True) & (y_true_prob == True)).sum())
    fp = int(((y_pred_prob == True) & (y_true_prob == False)).sum())
    fn = int(((y_pred_prob == False) & (y_true_prob == True)).sum())
    tn = int(((y_pred_prob == False) & (y_true_prob == False)).sum())
    prec = tp / max(tp + fp, 1)
    rec = tp / max(tp + fn, 1)
    f1 = 2 * prec * rec / max(prec + rec, 1e-9)
    acc = (tp + tn) / max(len(y_true_prob), 1)

    print(f"\nis_problem (эталон = LLM, n={len(y_true_prob)}):")
    print(f"  Accuracy:   {acc * 100:.1f}%")
    print(f"  Precision:  {prec * 100:.1f}%")
    print(f"  Recall:     {rec * 100:.1f}%")
    print(f"  F1:         {f1:.3f}")
    print(f"  TP={tp} FP={fp} FN={fn} TN={tn}")

    if sev_true:
        sev_match = sum(1 for a, b in zip(sev_true, sev_pred) if a == b)
        # ±1 уровень тяжести
        order = {s: i for i, s in enumerate(SEVERITIES)}
        within1 = sum(
            1 for a, b in zip(sev_true, sev_pred)
            if a in order and b in order and abs(order[a] - order[b]) <= 1
        )
        print(f"\nseverity (на согласных проблемах, n={len(sev_true)}):")
        print(f"  Точное совпадение:  {sev_match / len(sev_true) * 100:.1f}%")
        print(f"  В пределах ±1:      {within1 / len(sev_true) * 100:.1f}%")

    results["validation"] = {
        "n": int(len(y_true_prob)),
        "is_problem": {
            "accuracy": round(acc * 100, 1), "precision": round(prec * 100, 1),
            "recall": round(rec * 100, 1), "f1": round(f1, 3),
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        },
        "severity_exact": round(sev_match / len(sev_true) * 100, 1) if sev_true else None,
    }

    # ------------------------------------------------------------------ #
    section("[H] ИТОГО")
    print(f"Общее время расчёта метрик: {(time.time() - t_start) / 60:.1f} мин")
    out_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "output", "metrics_report.json")
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"JSON сохранён: {out_path}")


if __name__ == "__main__":
    main()
