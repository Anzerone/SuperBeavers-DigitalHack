"""Shared SQL/Python display fallbacks for nullable appeal fields."""
from __future__ import annotations

from sqlalchemy import case, func, select

from backend.storage.models import Appeal, AppealClusterMap, ProblemCluster


DEFAULT_CATEGORY = "Другое"
DEFAULT_SEVERITY = "MEDIUM"


def severity_rank(column):
    return case(
        (column == "CRITICAL", 1),
        (column == "HIGH", 2),
        (column == "MEDIUM", 3),
        (column == "LOW", 4),
        else_=99,
    )


def _cluster_field_for_appeal(column):
    return (
        select(column)
        .select_from(
            ProblemCluster.__table__.join(
                AppealClusterMap.__table__,
                AppealClusterMap.cluster_id == ProblemCluster.id,
            )
        )
        .where(AppealClusterMap.appeal_id == Appeal.id)
        .order_by(severity_rank(ProblemCluster.severity))
        .limit(1)
        .correlate(Appeal)
        .scalar_subquery()
    )


def appeal_display_severity():
    return func.coalesce(
        func.nullif(Appeal.severity, ""),
        _cluster_field_for_appeal(ProblemCluster.severity),
        DEFAULT_SEVERITY,
    )


def appeal_display_category():
    return func.coalesce(
        func.nullif(Appeal.category, ""),
        func.nullif(Appeal.group_name, ""),
        _cluster_field_for_appeal(ProblemCluster.category),
        DEFAULT_CATEGORY,
    )


def category_value(category: str | None, group_name: str | None = None) -> str:
    for value in (category, group_name):
        value = (value or "").strip()
        if value:
            return value
    return DEFAULT_CATEGORY


def severity_value(severity: str | None) -> str:
    severity = (severity or "").strip()
    return severity or DEFAULT_SEVERITY
