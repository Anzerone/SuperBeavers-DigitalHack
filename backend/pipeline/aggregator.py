"""Build district summaries from precomputed problem clusters."""
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from sqlalchemy.orm import Session

from backend.config import LLM_CONCURRENCY, LLM_MODEL, LLM_TIMEOUT_SECONDS, OLLAMA_URL, SUMMARY_TOP_N
from backend.labels import format_severity
from backend.pipeline.llm_utils import get_cached_llm, ollama_response_text, parse_llm_json, set_cached_llm
from backend.storage.models import ProblemCluster

logger = logging.getLogger(__name__)


def build_summaries(run_id: int, session: Session, progress_callback=None) -> list[dict]:
    """Aggregate clusters into district summaries sorted by problem count."""
    clusters = (
        session.query(ProblemCluster)
        .filter(ProblemCluster.run_id == run_id)
        .all()
    )

    muni_data = defaultdict(list)
    for cluster in clusters:
        muni_data[cluster.municipality].append(cluster)

    summaries = []
    for muni, muni_clusters in muni_data.items():
        total_appeals = sum(cluster.appeal_count for cluster in muni_clusters)
        ranks = [cluster.rank for cluster in muni_clusters if cluster.rank]
        avg_rank = sum(ranks) / len(ranks) if ranks else 0
        sorted_clusters = sorted(muni_clusters, key=lambda cluster: (-cluster.appeal_count, cluster.rank or 999999))

        top_issues = [
            {
                "name": cluster.cluster_name,
                "count": cluster.appeal_count,
                "severity": cluster.severity,
                "category": cluster.category,
                "rank": cluster.rank,
                "centroid_excerpt": (cluster.centroid_text or "")[:500],
            }
            for cluster in sorted_clusters[:3]
        ]

        centroid_excerpt = sorted_clusters[0].centroid_text if sorted_clusters else ""
        summaries.append(
            {
                "municipality": muni,
                "problem_count": total_appeals,
                "avg_rank": avg_rank,
                "top_issues": top_issues,
                "centroid_excerpt": (centroid_excerpt or "")[:500],
                "summary_text": "",
            }
        )

    summaries.sort(key=lambda item: -item["problem_count"])
    for rank, summary in enumerate(summaries, 1):
        summary["rank"] = rank

    top_for_llm = summaries[:SUMMARY_TOP_N]
    if top_for_llm:
        with ThreadPoolExecutor(max_workers=LLM_CONCURRENCY) as executor:
            futures = {executor.submit(_generate_summary, summary): summary for summary in top_for_llm}
            for done, future in enumerate(as_completed(futures), 1):
                futures[future]["summary_text"] = future.result()
                if progress_callback:
                    progress_callback(done, len(top_for_llm))

    logger.info("Built %s district summaries", len(summaries))
    return summaries


def _generate_summary(summary: dict) -> str:
    """Generate a concise district summary using LLM and cache it."""
    issues_str = "\n".join(
        f"- {issue['name']} ({issue['count']} обращений, тяжесть: {format_severity(issue.get('severity'))})"
        for issue in summary.get("top_issues", [])
    )
    cache_payload = {
        "model": LLM_MODEL,
        "municipality": summary["municipality"],
        "problem_count": summary["problem_count"],
        "top_issues": summary.get("top_issues", []),
        "centroid_excerpt": summary.get("centroid_excerpt", "")[:300],
    }

    cached = get_cached_llm("summaries", cache_payload)
    if cached is not None:
        return str(cached.get("summary_text", ""))[:500]

    prompt = f"""Напиши краткую аналитическую справку для руководства о проблемах в районе.

Район: {summary['municipality']}
Всего проблемных обращений: {summary['problem_count']}
Основные проблемы:
{issues_str}

Главная выдержка из центроида: {summary.get('centroid_excerpt', '')[:300]}

Справка на 2-3 предложения:"""

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": LLM_MODEL,
                "prompt": prompt,
                "system": "Ты - аналитик для руководства региона. Пиши кратко, по делу, официальным языком.",
                "stream": False,
                "think": False,
                "options": {"temperature": 0.3, "num_predict": 320},
            },
            timeout=LLM_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        response_text = ollama_response_text(resp.json())
        try:
            parsed = parse_llm_json(response_text)
            if isinstance(parsed, dict):
                response_text = str(parsed.get("summary", parsed.get("text", response_text)))
        except Exception:
            pass
        summary_text = response_text[:500]
        set_cached_llm("summaries", cache_payload, {"summary_text": summary_text})
        return summary_text
    except Exception as exc:
        logger.warning("Summary generation failed: %s", exc)
        return ""
