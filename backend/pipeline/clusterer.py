"""Cluster problems with dense DBSCAN, split into small safe groups."""
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import requests
from sklearn.cluster import DBSCAN
from sklearn.metrics.pairwise import cosine_distances

from backend.config import (
    CATEGORIES,
    CLUSTER_MERGE_DISTANCE,
    CLUSTER_NAME_CONCURRENCY,
    CLUSTER_NAME_MAX_TOTAL,
    CLUSTER_NAME_TOP_PER_MUNICIPALITY,
    DBSCAN_EPS,
    DBSCAN_MIN_SAMPLES,
    DBSCAN_SPLIT_BY_CATEGORY,
    LLM_MODEL,
    LLM_TIMEOUT_SECONDS,
    OLLAMA_URL,
    RANK_WEIGHT_APPEALS,
    RANK_WEIGHT_SEVERITY,
    RANK_WEIGHT_TOPIC_DIVERSITY,
    SEVERITY_WEIGHTS,
)
from backend.pipeline.llm_utils import get_cached_llm, parse_llm_json, set_cached_llm

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def _find_centroid(embeddings: np.ndarray, texts: list[str]) -> tuple[int, str]:
    if len(embeddings) == 1:
        return 0, texts[0]
    mean_emb = embeddings.mean(axis=0, keepdims=True)
    dists = cosine_distances(mean_emb, embeddings)[0]
    centroid_idx = int(np.argmin(dists))
    return centroid_idx, texts[centroid_idx]


def _fallback_name(category: str, centroid_text: str, categories: list[str] | None = None) -> str:
    cats = categories or CATEGORIES
    category = category if category in cats else cats[-1]
    excerpt = " ".join((centroid_text or "").split())[:80]
    if excerpt:
        return f"{category}: {excerpt}..."
    return f"Проблема ({category})"


def _name_cluster_llm(cluster: dict) -> tuple[str, str]:
    sample = cluster["example_texts"][:5]
    sample_str = "\n".join(f"- {text[:300]}" for text in sample)
    municipality = cluster["municipality"]
    category = cluster["category"]
    cache_payload = {
        "model": LLM_MODEL,
        "municipality": municipality,
        "category": category,
        "examples": sample,
    }

    cached = get_cached_llm("cluster_names", cache_payload)
    if cached is not None:
        return cached.get("name", ""), cached.get("description", "")

    prompt = f"""На основе этих обращений граждан из района "{municipality}" создай:
1. Краткое название проблемы до 10 слов
2. Описание проблемы в 1-2 предложениях

Обращения:
{sample_str}

Ответ строго JSON: {{"name": "...", "description": "..."}}"""

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": LLM_MODEL,
                "prompt": prompt,
                "system": "Ты - аналитик обращений граждан. Отвечай только JSON.",
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 220},
                "format": "json",
            },
            timeout=LLM_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = parse_llm_json(resp.json().get("response", ""))
        if not isinstance(data, dict):
            raise ValueError("cluster naming response is not a JSON object")

        name = str(data.get("name") or _fallback_name(category, cluster.get("centroid_text", "")))[:200]
        desc = str(data.get("description") or "")[:500]
        set_cached_llm("cluster_names", cache_payload, {"name": name, "description": desc})
        return name, desc
    except Exception as exc:
        logger.warning("LLM naming failed: %s", exc)
        return _fallback_name(category, cluster.get("centroid_text", ""))[:200], ""


def _build_clusters_for_group(
    muni: str,
    indices: list[int],
    embeddings: np.ndarray,
    texts: list[str],
    appeal_ids: list[int],
    severities: list[str],
    categories: list[str],
    group_names: list[str],
    all_categories: list[str] | None = None,
) -> list[dict]:
    cats = all_categories or CATEGORIES
    group_embeddings = embeddings[indices]
    group_texts = [texts[i] for i in indices]
    group_appeal_ids = [appeal_ids[i] for i in indices]
    group_severities = [severities[i] for i in indices]
    group_categories = [categories[i] for i in indices]
    group_groups = [group_names[i] for i in indices]

    if len(indices) < 2:
        labels = [0]
    elif len(indices) <= 5000:
        dist_matrix = cosine_distances(group_embeddings).astype(np.float32, copy=False)
        np.maximum(dist_matrix, 0.0, out=dist_matrix)
        db = DBSCAN(eps=DBSCAN_EPS, min_samples=DBSCAN_MIN_SAMPLES, metric="precomputed")
        labels = db.fit_predict(dist_matrix)
    else:
        db = DBSCAN(eps=DBSCAN_EPS, min_samples=DBSCAN_MIN_SAMPLES, metric="cosine")
        labels = db.fit_predict(group_embeddings)

    # Collect real clusters (non-noise)
    cluster_groups = defaultdict(list)
    noise_indices = []
    for local_idx, label in enumerate(labels):
        if label == -1:
            noise_indices.append(local_idx)
        else:
            cluster_groups[label].append(local_idx)

    # Assign noise points to nearest cluster centroid
    if not cluster_groups:
        cluster_groups[0] = list(range(len(labels)))
        noise_indices = []
    elif noise_indices:
        centroids = {}
        for label, c_indices in cluster_groups.items():
            centroids[label] = group_embeddings[c_indices].mean(axis=0)
        centroid_labels = list(centroids.keys())
        centroid_matrix = np.array([centroids[l] for l in centroid_labels])

        noise_embs = group_embeddings[noise_indices]
        dists = cosine_distances(noise_embs, centroid_matrix)
        nearest = np.argmin(dists, axis=1)
        for ni, nearest_idx in zip(noise_indices, nearest):
            cluster_groups[centroid_labels[nearest_idx]].append(ni)

    # Слияние кластеров-синонимов: разные DBSCAN-кластеры одного района могут
    # описывать по сути одну и ту же проблему. Объединяем их, если центроиды
    # ближе порога CLUSTER_MERGE_DISTANCE (union-find по парным расстояниям).
    if CLUSTER_MERGE_DISTANCE > 0 and len(cluster_groups) > 1:
        labels_list = list(cluster_groups.keys())
        means = np.vstack([group_embeddings[cluster_groups[l]].mean(axis=0) for l in labels_list])
        pair_dist = cosine_distances(means)
        parent = list(range(len(labels_list)))

        def _find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i in range(len(labels_list)):
            for j in range(i + 1, len(labels_list)):
                if pair_dist[i, j] < CLUSTER_MERGE_DISTANCE:
                    parent[_find(j)] = _find(i)

        merged: dict[int, list[int]] = defaultdict(list)
        for idx, label in enumerate(labels_list):
            merged[_find(idx)].extend(cluster_groups[label])
        cluster_groups = merged

    clusters = []
    for cluster_indices in cluster_groups.values():
        c_embeddings = group_embeddings[cluster_indices]
        c_texts = [group_texts[i] for i in cluster_indices]
        c_appeal_ids = [group_appeal_ids[i] for i in cluster_indices]
        c_severities = [group_severities[i] for i in cluster_indices]
        c_categories = [group_categories[i] for i in cluster_indices]
        c_groups = [group_groups[i] for i in cluster_indices]

        # Тяжесть кластера = преобладающая (мода) тяжесть его обращений, а не
        # самая высокая. Иначе один критический случай делал весь кластер
        # «критическим», и средние/низкие кластеры вообще не появлялись.
        sev_counts = defaultdict(int)
        for sev in c_severities:
            sev_counts[sev if sev in SEVERITY_ORDER else "MEDIUM"] += 1
        max_sev = max(sev_counts, key=lambda s: (sev_counts[s], -SEVERITY_ORDER.get(s, 3)))
        cat_counts = defaultdict(int)
        for category in c_categories:
            cat_counts[category if category in cats else cats[-1]] += 1
        top_category = max(cat_counts, key=cat_counts.get)

        _, centroid_text = _find_centroid(c_embeddings, c_texts)
        example_texts = [text[:300] for text in c_texts[:5]]
        topic_groups = set(g for g in c_groups if g)

        clusters.append(
            {
                "municipality": muni,
                "appeal_count": len(cluster_indices),
                "appeal_ids": c_appeal_ids,
                "severity": max_sev,
                "category": top_category,
                "centroid_text": centroid_text[:500],
                "example_texts": example_texts,
                "cluster_name": "",
                "description": "",
                "topic_groups": topic_groups,
                "topic_diversity": len(topic_groups),
            }
        )

    return clusters


def cluster_problems(
    embeddings: np.ndarray,
    municipalities: list[str],
    texts: list[str],
    appeal_ids: list[int],
    severities: list[str],
    categories: list[str],
    group_names: list[str] | None = None,
    categories_list: list[str] | None = None,
    progress_callback=None,
) -> list[dict]:
    if len(embeddings) == 0:
        return []

    cats = categories_list or CATEGORIES
    if group_names is None:
        group_names = [""] * len(embeddings)

    groups_map = defaultdict(list)
    for idx, muni in enumerate(municipalities):
        municipality = muni or "Неизвестно"
        if DBSCAN_SPLIT_BY_CATEGORY:
            category = categories[idx] if categories[idx] in cats else cats[-1]
            key = (municipality, category)
        else:
            key = (municipality, None)
        groups_map[key].append(idx)

    all_clusters = []
    groups = list(groups_map.items())
    for done, ((muni, _category), indices) in enumerate(groups, 1):
        all_clusters.extend(
            _build_clusters_for_group(
                muni, indices, embeddings, texts, appeal_ids, severities, categories, group_names, cats
            )
        )
        if progress_callback:
            progress_callback(done, max(len(groups), 1), f"GROUP {done}/{len(groups)}")

    # Composite ranking within each municipality
    by_muni = defaultdict(list)
    for cluster in all_clusters:
        by_muni[cluster["municipality"]].append(cluster)

    for muni_clusters in by_muni.values():
        if not muni_clusters:
            continue
        max_appeals = max(c["appeal_count"] for c in muni_clusters)
        max_diversity = max((c["topic_diversity"] for c in muni_clusters), default=1) or 1

        for c in muni_clusters:
            norm_appeals = c["appeal_count"] / max_appeals if max_appeals else 0
            norm_diversity = c["topic_diversity"] / max_diversity if max_diversity else 0
            sev_weight = SEVERITY_WEIGHTS.get(c["severity"], 0.25)
            c["rank_score"] = (
                RANK_WEIGHT_APPEALS * norm_appeals
                + RANK_WEIGHT_TOPIC_DIVERSITY * norm_diversity
                + RANK_WEIGHT_SEVERITY * sev_weight
            )

        muni_clusters.sort(key=lambda c: -c["rank_score"])
        for rank, c in enumerate(muni_clusters, 1):
            c["rank"] = rank

    to_name = [
        cluster
        for cluster in all_clusters
        if cluster.get("rank", 0) <= CLUSTER_NAME_TOP_PER_MUNICIPALITY
    ]
    to_name.sort(key=lambda c: (-c["appeal_count"], c["municipality"]))
    to_name = to_name[:CLUSTER_NAME_MAX_TOTAL]

    named = 0
    if to_name:
        with ThreadPoolExecutor(max_workers=CLUSTER_NAME_CONCURRENCY) as executor:
            futures = {executor.submit(_name_cluster_llm, cluster): cluster for cluster in to_name}
            for done, future in enumerate(as_completed(futures), 1):
                cluster = futures[future]
                name, desc = future.result()
                cluster["cluster_name"] = name
                cluster["description"] = desc
                named += 1
                if progress_callback:
                    progress_callback(done, max(len(to_name), 1), f"NAME {done}/{len(to_name)}")

    for cluster in all_clusters:
        if not cluster["cluster_name"]:
            cluster["cluster_name"] = _fallback_name(cluster["category"], cluster["centroid_text"], cats)[:200]

    logger.info(
        "Created %s clusters across %s DBSCAN groups, named %s via LLM",
        len(all_clusters),
        len(groups),
        named,
    )
    return all_clusters
