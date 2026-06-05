"""Train LogReg classifiers on bootstrap labels and predict for all data."""
import logging
import os
import pickle

import numpy as np
from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.multiclass import OneVsRestClassifier
from sklearn.preprocessing import LabelEncoder

from backend.config import CATEGORIES, CLASSIFIER_CACHE_DIR, SEVERITIES

logger = logging.getLogger(__name__)

CACHE_VERSION = 5
CACHE_FILE = os.path.join(CLASSIFIER_CACHE_DIR, "classifier_cache.pkl")


def _make_logreg():
    return LogisticRegression(
        max_iter=250,
        C=1.0,
        class_weight="balanced",
        solver="liblinear",
        tol=1e-3,
    )


def _fit_classifier(X: np.ndarray, y: np.ndarray):
    if len(y) == 0:
        raise ValueError("Cannot train classifier on an empty label array")
    unique_labels = np.unique(y)
    if len(unique_labels) < 2:
        clf = DummyClassifier(strategy="constant", constant=y[0])
    elif len(unique_labels) > 2:
        clf = OneVsRestClassifier(_make_logreg())
    else:
        clf = _make_logreg()
    clf.fit(X, y)
    return clf


def _save_cache(clf_problem, clf_severity, clf_category, le_sev, le_cat):
    """Save trained classifiers to disk."""
    os.makedirs(CLASSIFIER_CACHE_DIR, exist_ok=True)
    with open(CACHE_FILE, "wb") as f:
        pickle.dump(
            {
                "version": CACHE_VERSION,
                "clf_problem": clf_problem,
                "clf_severity": clf_severity,
                "clf_category": clf_category,
                "le_sev": le_sev,
                "le_cat": le_cat,
            },
            f,
        )
    logger.info("Classifier cache saved to %s", CACHE_FILE)


def _load_cache():
    """Load cached classifiers. Returns None if not found or outdated."""
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "rb") as f:
            cache = pickle.load(f)
        if cache.get("version") != CACHE_VERSION:
            logger.info("Classifier cache version changed; rebuilding classifier")
            return None
        logger.info("Loaded classifier from cache; skipping bootstrap")
        return cache
    except Exception as exc:
        logger.warning("Failed to load classifier cache: %s", exc)
        return None


def predict_with_cache(embeddings: np.ndarray, progress_callback=None) -> dict | None:
    """Try to predict using cached classifiers. Returns None if no cache."""
    cache = _load_cache()
    if cache is None:
        return None

    n_records = len(embeddings)
    clf_problem = cache["clf_problem"]
    clf_severity = cache["clf_severity"]
    clf_category = cache["clf_category"]
    le_sev = cache["le_sev"]
    le_cat = cache["le_cat"]

    proba = clf_problem.predict_proba(embeddings)
    pred_problem = clf_problem.predict(embeddings)
    confidence = np.max(proba, axis=1)
    if progress_callback:
        progress_callback(1, 3)

    pred_sev_enc = clf_severity.predict(embeddings)
    pred_severity = le_sev.inverse_transform(pred_sev_enc)
    if progress_callback:
        progress_callback(2, 3)

    pred_cat_enc = clf_category.predict(embeddings)
    pred_category = le_cat.inverse_transform(pred_cat_enc)
    if progress_callback:
        progress_callback(3, 3)

    logger.info("Cache predict: %s / %s problems", sum(pred_problem), n_records)

    return {
        "is_problem": [bool(p) for p in pred_problem],
        "severity": [str(s) for s in pred_severity],
        "category": [str(c) for c in pred_category],
        "confidence": [float(c) for c in confidence],
        "method": ["embedding_cached"] * n_records,
    }


def train_and_predict(embeddings: np.ndarray, bootstrap: dict, categories: list[str] | None = None, progress_callback=None) -> dict:
    """Train LogReg on bootstrap labels, classify all records, and save cache."""
    cats = categories or CATEGORIES
    indices = bootstrap["indices"]
    train_X = embeddings[indices]
    n_records = len(embeddings)

    train_y_problem = np.array([1 if p else 0 for p in bootstrap["is_problem"]])
    clf_problem = _fit_classifier(train_X, train_y_problem)
    if progress_callback:
        progress_callback(1, 6)

    proba = clf_problem.predict_proba(embeddings)
    pred_problem = clf_problem.predict(embeddings)
    confidence = np.max(proba, axis=1)
    if progress_callback:
        progress_callback(2, 6)

    logger.info("Problem classifier: %s / %s problems", sum(pred_problem), n_records)

    problem_mask = np.array(bootstrap["is_problem"])
    train_X_prob = train_X[problem_mask]

    le_sev = LabelEncoder()
    le_sev.fit(SEVERITIES)
    train_y_sev = np.array(bootstrap["severity"])[problem_mask]
    if len(train_y_sev) == 0:
        train_X_prob = train_X[:1]
        train_y_sev_enc = le_sev.transform(["MEDIUM"])
    else:
        train_y_sev_enc = le_sev.transform(train_y_sev)

    clf_severity = _fit_classifier(train_X_prob, train_y_sev_enc)
    pred_sev_enc = clf_severity.predict(embeddings)
    pred_severity = le_sev.inverse_transform(pred_sev_enc)
    if progress_callback:
        progress_callback(4, 6)

    le_cat = LabelEncoder()
    le_cat.fit(cats)
    train_y_cat = np.array(bootstrap["category"])[problem_mask]
    if len(train_y_cat) == 0:
        train_X_prob = train_X[:1]
        train_y_cat = np.array([cats[-1]])
    valid_cats = [c if c in cats else cats[-1] for c in train_y_cat]
    train_y_cat_enc = le_cat.transform(valid_cats)

    clf_category = _fit_classifier(train_X_prob, train_y_cat_enc)
    pred_cat_enc = clf_category.predict(embeddings)
    pred_category = le_cat.inverse_transform(pred_cat_enc)
    if progress_callback:
        progress_callback(5, 6)

    _save_cache(clf_problem, clf_severity, clf_category, le_sev, le_cat)
    if progress_callback:
        progress_callback(6, 6)

    return {
        "is_problem": [bool(p) for p in pred_problem],
        "severity": [str(s) for s in pred_severity],
        "category": [str(c) for c in pred_category],
        "confidence": [float(c) for c in confidence],
        "method": ["embedding"] * n_records,
    }
