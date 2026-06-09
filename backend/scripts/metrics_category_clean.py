"""Честная оценка классификации категории: эмбеддинги БЕЗ префикса группы.

В full_metrics.py эмбеддинги считались как "[Группа] текст" — это утечка
эталона (категория зашита во вход). Здесь эмбеддим ЧИСТЫЙ текст и меряем,
насколько модель предсказывает «Группа тем» по содержанию обращения.
"""
from __future__ import annotations

import os
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import numpy as np

MAIN_FILE = r"C:\Users\Iaroslav\Desktop\олимпиады\класификатор жалоб\data\input\основной файл.xlsx"
SEED = 42


def main() -> None:
    from backend.pipeline.loader import load_excel
    from backend.pipeline.embedder import compute_embeddings_dedup
    from sklearn.model_selection import train_test_split
    from sklearn.linear_model import LogisticRegression
    from sklearn.multiclass import OneVsRestClassifier
    from sklearn.metrics import accuracy_score, f1_score
    from sklearn.neighbors import NearestNeighbors

    df, _ = load_excel(MAIN_FILE)
    texts = [str(t or "") for t in df["incident_text"].tolist()]
    groups = df["group"].fillna("").astype(str).tolist()
    n = len(texts)

    dynamic_cats = sorted({g.strip() for g in groups if g.strip()})

    print(f"Строк: {n:,}, классов: {len(dynamic_cats)}")
    print("Считаю эмбеддинги БЕЗ префикса группы (чистый текст)...")
    t0 = time.time()
    emb = compute_embeddings_dedup(texts)  # без префикса!
    print(f"Эмбеддинги: {emb.shape}, {time.time() - t0:.1f}с")

    mask = np.array([g.strip() in dynamic_cats for g in groups])
    X = emb[mask]
    y = np.array([g.strip() for g in groups])[mask]

    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.3, random_state=SEED)
    clf = OneVsRestClassifier(
        LogisticRegression(max_iter=300, C=1.0, class_weight="balanced", solver="liblinear")
    )
    t0 = time.time()
    clf.fit(X_tr, y_tr)
    print(f"LogReg обучен за {time.time() - t0:.1f}с на {len(y_tr):,}")
    y_pred = clf.predict(X_te)
    proba = clf.predict_proba(X_te)
    classes = clf.classes_
    top3 = np.argsort(proba, axis=1)[:, -3:]
    top3_hit = np.mean([y_te[i] in classes[top3[i]] for i in range(len(y_te))])

    acc = accuracy_score(y_te, y_pred)
    macro = f1_score(y_te, y_pred, average="macro", zero_division=0)
    weighted = f1_score(y_te, y_pred, average="weighted", zero_division=0)

    print("\n=== КАТЕГОРИЯ ПО ТЕКСТУ (без утечки) ===")
    print(f"Accuracy (top-1):  {acc * 100:.1f}%")
    print(f"Top-3 accuracy:    {top3_hit * 100:.1f}%")
    print(f"Macro-F1:          {macro:.3f}")
    print(f"Weighted-F1:       {weighted:.3f}")

    # kNN purity на чистых эмбеддингах
    rng = np.random.default_rng(SEED)
    q = min(5000, len(X))
    qi = rng.choice(len(X), size=q, replace=False)
    nn = NearestNeighbors(n_neighbors=6, metric="cosine")
    nn.fit(X)
    _, neigh = nn.kneighbors(X[qi])
    pur, nn1 = [], []
    for row, idx in zip(neigh, qi):
        nb = [r for r in row if r != idx][:5]
        pur.append(sum(1 for r in nb if y[r] == y[idx]) / len(nb))
        nn1.append(y[nb[0]] == y[idx])
    print(f"\n1-NN accuracy:     {np.mean(nn1) * 100:.1f}%")
    print(f"5-NN purity:       {np.mean(pur) * 100:.1f}%")


if __name__ == "__main__":
    main()
