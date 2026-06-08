"""Pipeline orchestrator for the full complaint-processing flow."""
import logging
import time
from datetime import datetime
from typing import Any

import numpy as np

from backend.config import (
    BOOTSTRAP_SAMPLE_SIZE,
    DB_SAVE_CHUNK_SIZE,
    NON_PROBLEM_INCIDENT_TYPES,
    STORE_EMBEDDINGS_IN_DB,
)
from backend.pipeline.aggregator import build_summaries
from backend.pipeline.bootstrap import bootstrap_classify
from backend.pipeline.classifier import predict_with_cache, train_and_predict
from backend.pipeline.clusterer import cluster_problems
from backend.pipeline.embedder import compute_embeddings
from backend.pipeline.llm_verifier import verify_low_confidence
from backend.pipeline.loader import load_excel
from backend.storage.database import get_sync_db
from backend.storage.models import Appeal, AppealClusterMap, ProcessingRun, ProblemCluster, Summary

logger = logging.getLogger(__name__)


def _update_run(session, run_id: int, **kwargs):
    """Update processing run status."""
    run = session.get(ProcessingRun, run_id)
    if not run:
        return
    for key, value in kwargs.items():
        setattr(run, key, value)
    session.commit()


def _progress_updater(session, run_id: int, start: float, end: float, label: str, min_interval: float = 1.0):
    """Create a callback that maps done/total to a run progress range."""
    last_update = {"ts": 0.0}

    def callback(done: int, total: int, message: str | None = None):
        total = max(total, 1)
        ratio = min(max(done / total, 0.0), 1.0)
        progress = start + (end - start) * ratio
        now = time.time()
        if done >= total or now - last_update["ts"] >= min_interval:
            last_update["ts"] = now
            _update_run(session, run_id, progress=progress, current_step=message or f"{label}: {done}/{total}")

    return callback


def _cluster_progress_updater(session, run_id: int):
    dbscan_cb = _progress_updater(session, run_id, 0.70, 0.81, "Поиск повторяющихся проблем")
    naming_cb = _progress_updater(session, run_id, 0.81, 0.86, "Описание выявленных проблем")

    def callback(done: int, total: int, message: str | None = None):
        if message and message.startswith("NAME"):
            naming_cb(done, total, f"Описание проблем: {done}/{total}")
        else:
            dbscan_cb(done, total, f"Поиск повторяющихся проблем: {done}/{total}")

    return callback


def _clean_db_value(value: Any) -> Any:
    """Convert pandas/numpy missing values to None for SQLAlchemy inserts."""
    if value is None:
        return None
    try:
        if value != value:
            return None
    except Exception:
        pass
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime()
    return value


def _column_values(df, name: str, total: int, default: Any = "") -> list[Any]:
    if name not in df.columns:
        return [default] * total
    return [_clean_db_value(value) for value in df[name].tolist()]


def _save_appeals_bulk(session, run_id: int, df, texts: list[str], predictions: dict, embeddings: np.ndarray, progress_callback=None):
    """Save appeals in chunks; optionally skip embedding bytes for speed."""
    total = len(texts)
    columns = {
        "group": _column_values(df, "group", total, ""),
        "municipality": _column_values(df, "municipality", total, ""),
        "incident_type": _column_values(df, "incident_type", total, ""),
        "outcome": _column_values(df, "outcome", total, ""),
        "date_created": _column_values(df, "date_created", total, None),
        "date_closed": _column_values(df, "date_closed", total, None),
    }

    for start in range(0, total, DB_SAVE_CHUNK_SIZE):
        end = min(start + DB_SAVE_CHUNK_SIZE, total)
        rows = []
        for idx in range(start, end):
            is_problem = bool(predictions["is_problem"][idx])
            rows.append(
                {
                    "run_id": run_id,
                    "date_created": columns["date_created"][idx],
                    "date_closed": columns["date_closed"][idx],
                    "group_name": columns["group"][idx],
                    "municipality": columns["municipality"][idx],
                    "incident_type": columns["incident_type"][idx],
                    "outcome": columns["outcome"][idx],
                    "incident_text": texts[idx],
                    "is_problem": is_problem,
                    "classification_method": predictions["method"][idx],
                    "confidence": float(predictions["confidence"][idx]),
                    "severity": predictions["severity"][idx] if is_problem else None,
                    "category": predictions["category"][idx] if is_problem else None,
                    "sentiment": predictions.get("sentiment", [None] * total)[idx],
                    "sentiment_score": predictions.get("sentiment_score", [None] * total)[idx],
                    "embedding": embeddings[idx].tobytes() if STORE_EMBEDDINGS_IN_DB else None,
                }
            )

        session.bulk_insert_mappings(Appeal, rows)
        session.commit()
        if progress_callback:
            progress_callback(end, total)


def run_pipeline(run_id: int, filepath: str):
    """Run the complete processing pipeline in a background thread."""
    session = get_sync_db()
    started = time.time()

    try:
        _update_run(session, run_id, current_step="Чтение Excel-файла", progress=0.03)
        df, load_stats = load_excel(filepath)
        total = len(df)
        _update_run(
            session,
            run_id,
            raw_records=load_stats["raw_count"],
            total_records=total,
            dropped_incident_type=load_stats["dropped_incident_type"],
            dropped_outcome=load_stats["dropped_outcome"],
            progress=0.10,
            current_step="Письма загружены и отфильтрованы",
        )
        logger.info(
            "Loaded %s rows (raw=%s, dropped Нерешаемый=%s, закрытые=%s) from %s",
            total,
            load_stats["raw_count"],
            load_stats["dropped_incident_type"],
            load_stats["dropped_outcome"],
            filepath,
        )

        texts = df["incident_text"].tolist()
        dynamic_cats = df["group"].dropna().unique().tolist() if "group" in df.columns else None
        if dynamic_cats and "Другое" not in dynamic_cats:
            dynamic_cats.append("Другое")

        # Conditioning: префикс группы темы перед текстом даёт bge-m3
        # семантический сигнал о категории — кластеризация и similarity
        # улучшаются без увеличения времени (длина токенов растёт на 3-5).
        if "group" in df.columns:
            group_prefixes = df["group"].fillna("").tolist()
            embed_inputs = [
                f"[{(g or '').strip()}] {t}" if (g or "").strip() else t
                for g, t in zip(group_prefixes, texts)
            ]
        else:
            embed_inputs = texts

        _update_run(session, run_id, current_step="Анализ содержания писем", progress=0.10)
        # Dedup-aware: одинаковые строки кодируются один раз.
        from backend.pipeline.embedder import compute_embeddings_dedup
        embeddings = compute_embeddings_dedup(
            embed_inputs,
            progress_callback=_progress_updater(session, run_id, 0.10, 0.40, "Анализ содержания писем"),
        )
        _update_run(session, run_id, progress=0.40, current_step="Содержание писем понято")
        logger.info("Computed %s embeddings", len(embeddings))

        predictions = predict_with_cache(
            embeddings,
            categories=dynamic_cats,
            progress_callback=_progress_updater(session, run_id, 0.40, 0.55, "Категоризация писем (быстрый режим)"),
        )
        if predictions is not None:
            _update_run(session, run_id, progress=0.55, current_step="Категоризация завершена")
            logger.info("Used cached classifier; skipped bootstrap")
        else:
            sample_size = min(BOOTSTRAP_SAMPLE_SIZE, total)
            _update_run(session, run_id, current_step="Изучение примеров для разметки", progress=0.40)
            full_groups = df["group"].fillna("").tolist() if "group" in df.columns else None
            bootstrap_labels = bootstrap_classify(
                texts,
                sample_size=sample_size,
                categories=dynamic_cats,
                groups=full_groups,
                progress_callback=_progress_updater(session, run_id, 0.40, 0.48, "Изучение примеров"),
            )
            _update_run(session, run_id, current_step="Категоризация всех писем", progress=0.48)
            predictions = train_and_predict(
                embeddings,
                bootstrap_labels,
                categories=dynamic_cats,
                progress_callback=_progress_updater(session, run_id, 0.48, 0.55, "Категоризация писем"),
            )
            _update_run(session, run_id, progress=0.55, current_step="Категоризация завершена")

        _update_run(session, run_id, current_step="Уточнение сложных случаев", progress=0.55)
        predictions = verify_low_confidence(
            texts,
            predictions,
            embeddings,
            categories=dynamic_cats,
            progress_callback=_progress_updater(session, run_id, 0.55, 0.62, "Уточнение сложных случаев"),
        )
        _update_run(session, run_id, progress=0.62, current_step="Сложные случаи уточнены")

        # Override LLM-predicted category with the actual "Группа тем" from the
        # source file when it's available. The dataset already provides a
        # human-curated topic group — we trust it over the model's guess.
        if "group" in df.columns:
            group_values = df["group"].fillna("").tolist()
            dyn_set = set(dynamic_cats or [])
            for idx, group in enumerate(group_values):
                group = (group or "").strip()
                if group and group in dyn_set:
                    predictions["category"][idx] = group

        # Sentiment (lexicon-based, мгновенно)
        from backend.pipeline.sentiment import batch_sentiment
        sentiment_labels, sentiment_scores = batch_sentiment(texts)
        predictions["sentiment"] = sentiment_labels
        predictions["sentiment_score"] = sentiment_scores
        logger.info(
            "Sentiment: %d angry, %d desperate, %d calm, %d neutral",
            sentiment_labels.count("ANGRY"),
            sentiment_labels.count("DESPERATE"),
            sentiment_labels.count("CALM"),
            sentiment_labels.count("NEUTRAL"),
        )

        # Информационные письма принудительно не считаем проблемами.
        if "incident_type" in df.columns and NON_PROBLEM_INCIDENT_TYPES:
            non_problem_types = set(NON_PROBLEM_INCIDENT_TYPES)
            incident_types = df["incident_type"].fillna("").tolist()
            forced = 0
            for idx, itype in enumerate(incident_types):
                if (itype or "").strip() in non_problem_types and predictions["is_problem"][idx]:
                    predictions["is_problem"][idx] = False
                    forced += 1
            if forced:
                logger.info("Forced %s informational letters to non-problem", forced)

        problem_count = int(sum(predictions["is_problem"]))
        _update_run(session, run_id, problem_count=problem_count)

        _update_run(session, run_id, current_step="Запись писем в базу", progress=0.64)
        _save_appeals_bulk(
            session,
            run_id,
            df,
            texts,
            predictions,
            embeddings,
            progress_callback=_progress_updater(session, run_id, 0.64, 0.70, "Запись писем"),
        )

        appeal_ids = [
            row.id
            for row in session.query(Appeal.id)
            .filter(Appeal.run_id == run_id)
            .order_by(Appeal.id)
            .all()
        ]
        _update_run(session, run_id, progress=0.70, current_step="Письма сохранены")

        _update_run(session, run_id, current_step="Поиск повторяющихся проблем по районам", progress=0.70)
        problem_mask = np.array(predictions["is_problem"])
        problem_indices = np.where(problem_mask)[0]
        problem_embeddings = embeddings[problem_indices]
        all_municipalities = _column_values(df, "municipality", total, "")
        all_groups = _column_values(df, "group", total, "")
        problem_municipalities = [all_municipalities[int(i)] for i in problem_indices]
        problem_texts = [texts[int(i)] for i in problem_indices]
        problem_appeal_ids = [appeal_ids[int(i)] for i in problem_indices]
        problem_severities = [predictions["severity"][int(i)] for i in problem_indices]
        problem_categories = [predictions["category"][int(i)] for i in problem_indices]
        problem_groups = [all_groups[int(i)] for i in problem_indices]

        clusters_data = cluster_problems(
            problem_embeddings,
            problem_municipalities,
            problem_texts,
            problem_appeal_ids,
            problem_severities,
            problem_categories,
            group_names=problem_groups,
            categories_list=dynamic_cats,
            progress_callback=_cluster_progress_updater(session, run_id),
        )
        _update_run(session, run_id, progress=0.86, current_step="Проблемы сгруппированы")

        cluster_count = 0
        _update_run(session, run_id, current_step="Запись проблем в базу", progress=0.87)
        for cluster_data in clusters_data:
            cluster = ProblemCluster(
                run_id=run_id,
                municipality=cluster_data["municipality"],
                cluster_name=cluster_data["cluster_name"],
                description=cluster_data.get("description", ""),
                category=cluster_data["category"],
                severity=cluster_data["severity"],
                appeal_count=cluster_data["appeal_count"],
                rank=cluster_data.get("rank", 0),
                rank_score=cluster_data.get("rank_score"),
                topic_diversity=cluster_data.get("topic_diversity", 0),
                centroid_text=cluster_data.get("centroid_text", ""),
                example_texts=cluster_data.get("example_texts", []),
            )
            session.add(cluster)
            session.flush()

            for appeal_id in cluster_data["appeal_ids"]:
                session.add(AppealClusterMap(appeal_id=appeal_id, cluster_id=cluster.id))

            cluster_count += 1

        session.commit()
        _update_run(session, run_id, cluster_count=cluster_count, progress=0.90, current_step="Проблемы сохранены")

        _update_run(session, run_id, current_step="Подготовка аналитической сводки", progress=0.90)
        summaries = build_summaries(
            run_id,
            session,
            progress_callback=_progress_updater(session, run_id, 0.90, 0.96, "Справки по районам"),
        )
        for summary in summaries:
            session.add(
                Summary(
                    run_id=run_id,
                    municipality=summary["municipality"],
                    rank=summary["rank"],
                    problem_count=summary["problem_count"],
                    avg_rank=summary.get("avg_rank"),
                    top_issues=summary.get("top_issues", []),
                    summary_text=summary.get("summary_text", ""),
                    centroid_excerpt=summary.get("centroid_excerpt", ""),
                )
            )
        session.commit()

        elapsed = time.time() - started
        _update_run(
            session,
            run_id,
            status="completed",
            progress=1.0,
            current_step=f"Завершено за {elapsed / 60:.1f} мин",
            finished_at=datetime.utcnow(),
        )
        logger.info(
            "Pipeline completed in %.0fs: %s records, %s problems, %s clusters",
            elapsed,
            total,
            problem_count,
            cluster_count,
        )

    except Exception as exc:
        logger.exception("Pipeline failed for run %s", run_id)
        _update_run(session, run_id, status="failed", error_message=str(exc)[:1000])
        raise
    finally:
        session.close()
