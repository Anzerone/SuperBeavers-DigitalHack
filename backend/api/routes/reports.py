"""Report generation and download endpoints."""
from collections import defaultdict
import io

import openpyxl
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.labels import format_severity
from backend.storage.database import get_db
from backend.storage.models import Appeal, ProblemCluster, ProcessingRun, Summary

router = APIRouter()

HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
HEADER_FILL = PatternFill("solid", fgColor="1A3C6E")
HEADER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)
WRAP_TOP = Alignment(vertical="top", wrap_text=True)
THIN_BORDER = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin"),
)


def _style_header(ws, row_num: int, col_count: int):
    for col in range(1, col_count + 1):
        cell = ws.cell(row=row_num, column=col)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = HEADER_ALIGN
        cell.border = THIN_BORDER


def _style_data_row(ws, row_num: int, col_count: int):
    for col in range(1, col_count + 1):
        cell = ws.cell(row=row_num, column=col)
        cell.border = THIN_BORDER
        cell.alignment = WRAP_TOP


def _auto_width(ws, max_width: int = 60):
    for col in ws.columns:
        max_len = 0
        col_letter = col[0].column_letter
        for cell in col:
            if cell.value:
                max_len = max(max_len, min(len(str(cell.value)), max_width))
        ws.column_dimensions[col_letter].width = max(max_len + 2, 10)


def _compact_text(value: str | None) -> str:
    return " ".join(str(value or "").split())


def _is_centroid_fallback_name(name: str, category: str | None) -> bool:
    """Detect generated fallback names like "Медицина: '<long quote>...'."""
    clean_name = _compact_text(name)
    clean_category = _compact_text(category)
    if not clean_name or not clean_category:
        return False

    has_category_prefix = clean_name.startswith(f"{clean_category}:")
    has_quote_like_excerpt = "'" in clean_name or '"' in clean_name or "«" in clean_name
    return has_category_prefix and (clean_name.endswith("...") or has_quote_like_excerpt or len(clean_name) > 90)


def _problem_name(name: str | None, category: str | None = None, description: str | None = None) -> str:
    clean_name = _compact_text(name)
    if clean_name and not _is_centroid_fallback_name(clean_name, category):
        return clean_name

    clean_description = _compact_text(description)
    if clean_description:
        first_sentence = clean_description.split(".")[0].strip()
        return (first_sentence or clean_description)[:140]

    clean_category = _compact_text(category)
    if clean_category:
        return f"Проблема категории «{clean_category}»"
    return "Проблема"


def _cluster_issue(cluster: ProblemCluster) -> dict:
    return {
        "name": _problem_name(cluster.cluster_name, cluster.category, cluster.description),
        "count": cluster.appeal_count or 0,
        "category": cluster.category,
        "severity": format_severity(cluster.severity),
        "centroid_excerpt": cluster.centroid_text or "",
    }


def _stored_issue(issue: dict) -> dict:
    return {
        **issue,
        "name": _problem_name(issue.get("name"), issue.get("category"), issue.get("description")),
    }


def _top_issues_for_summary(summary: Summary, clusters_by_muni: dict[str, list[ProblemCluster]]) -> list[dict]:
    clusters = clusters_by_muni.get(summary.municipality, [])
    if clusters:
        sorted_clusters = sorted(clusters, key=lambda c: (-(c.appeal_count or 0), c.rank or 999999))
        return [_cluster_issue(cluster) for cluster in sorted_clusters[:3]]
    return [_stored_issue(issue) for issue in (summary.top_issues or [])[:3]]


def _top_issues_text(top_issues: list[dict]) -> str:
    parts = []
    for idx, issue in enumerate(top_issues[:3], 1):
        name = _problem_name(issue.get("name"), issue.get("category"), issue.get("description"))
        count = issue.get("count", 0)
        parts.append(f"{idx}. {name} ({count} обращ.)")
    return "\n".join(parts)


def _centroid_excerpts_text(top_issues: list[dict]) -> str:
    parts = []
    for idx, issue in enumerate(top_issues[:3], 1):
        excerpt = " ".join((issue.get("centroid_excerpt") or "").split())[:200]
        parts.append(f"{idx}. {excerpt}")
    return "\n\n".join(parts)


def _summary_metric_rows(run: ProcessingRun) -> list[tuple[str, object, str]]:
    filtered = run.total_records or 0
    dropped = (run.dropped_incident_type or 0) + (run.dropped_outcome or 0)
    raw = max(run.raw_records or 0, filtered + dropped)
    if raw <= 0:
        raw = filtered

    problem_count = run.problem_count or 0
    problem_percent = round(problem_count / max(raw, 1) * 100, 1)

    return [
        (
            "Общая длина датасета",
            raw,
            "строк в исходном файле",
        ),
        (
            "Проблемных обращений",
            problem_count,
            f"{problem_percent}% помечены как проблема",
        ),
        (
            "Кластеров",
            run.cluster_count or 0,
            "по муниципалитетам",
        ),
    ]


@router.get("/reports/{run_id}/excel")
async def download_report(run_id: int, db: AsyncSession = Depends(get_db)):
    """Generate the analytical report: Top-10, Top-3, and Отчёт sheets."""
    run_q = await db.execute(select(ProcessingRun).where(ProcessingRun.id == run_id))
    run = run_q.scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Run not found")
    if run.status != "completed":
        raise HTTPException(400, "Processing not completed yet")

    sum_q = await db.execute(
        select(Summary).where(Summary.run_id == run_id).order_by(Summary.rank)
    )
    summaries = sum_q.scalars().all()

    cluster_q = await db.execute(
        select(ProblemCluster)
        .where(ProblemCluster.run_id == run_id)
        .order_by(ProblemCluster.municipality, ProblemCluster.rank)
    )
    clusters_by_muni = defaultdict(list)
    for cluster in cluster_q.scalars().all():
        clusters_by_muni[cluster.municipality].append(cluster)

    category_q = await db.execute(
        select(Appeal.category, func.count().label("count"))
        .where(and_(Appeal.run_id == run_id, Appeal.is_problem == True, Appeal.category.isnot(None)))
        .group_by(Appeal.category)
        .order_by(func.count().desc())
        .limit(6)
    )
    top_categories = category_q.all()

    wb = openpyxl.Workbook()

    # === Sheet 1: Топ-10 районов ===
    ws10 = wb.active
    ws10.title = "Топ-10 районов"
    headers_10 = [
        "Ранг",
        "Область/район",
        "Проблема",
        "Количество обращений",
        "3 ключевые проблемы",
        "Главная выдержка из центроида",
    ]
    ws10.append(headers_10)
    _style_header(ws10, 1, len(headers_10))

    top_10_summaries = summaries[:10]
    for display_rank, summary in enumerate(top_10_summaries, 1):
        top_issues = _top_issues_for_summary(summary, clusters_by_muni)
        main_problem = top_issues[0].get("name", "") if top_issues else ""
        ws10.append(
            [
                display_rank,
                summary.municipality,
                main_problem,
                summary.problem_count,
                _top_issues_text(top_issues),
                _centroid_excerpts_text(top_issues),
            ]
        )
        _style_data_row(ws10, ws10.max_row, len(headers_10))

    _auto_width(ws10)
    ws10.column_dimensions["B"].width = 26
    ws10.column_dimensions["C"].width = 38
    ws10.column_dimensions["E"].width = 48
    ws10.column_dimensions["F"].width = 70

    # === Sheet 2: Топ-3 подробно (каждый район — отдельная таблица со ВСЕМИ проблемами) ===
    ws3 = wb.create_sheet("Топ-3 подробно")
    current_row = 1
    detail_headers = ["#", "Проблема", "Количество обращений", "Ранг проблемы", "Главная выдержка из центроида"]

    for display_rank, summary in enumerate(summaries[:3], 1):
        title = (
            f"{display_rank}. {summary.municipality} — "
            f"{summary.problem_count} обращений"
        )
        ws3.cell(row=current_row, column=1, value=title)
        ws3.cell(row=current_row, column=1).font = Font(bold=True, size=13, color="1A3C6E")
        ws3.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=len(detail_headers))
        current_row += 1

        for col, header in enumerate(detail_headers, 1):
            ws3.cell(row=current_row, column=col, value=header)
        _style_header(ws3, current_row, len(detail_headers))
        current_row += 1

        cluster_q = await db.execute(
            select(ProblemCluster)
            .where(and_(ProblemCluster.run_id == run_id, ProblemCluster.municipality == summary.municipality))
            .order_by(ProblemCluster.rank)
        )
        clusters = cluster_q.scalars().all()

        for idx, cluster in enumerate(clusters, 1):
            ws3.cell(row=current_row, column=1, value=idx)
            ws3.cell(row=current_row, column=2, value=_problem_name(cluster.cluster_name, cluster.category, cluster.description))
            ws3.cell(row=current_row, column=3, value=cluster.appeal_count)
            ws3.cell(row=current_row, column=4, value=cluster.rank)
            ws3.cell(row=current_row, column=5, value=(cluster.centroid_text or "")[:500])
            _style_data_row(ws3, current_row, len(detail_headers))
            current_row += 1

        current_row += 2

    ws3.column_dimensions["B"].width = 44
    ws3.column_dimensions["C"].width = 20
    ws3.column_dimensions["D"].width = 16
    ws3.column_dimensions["E"].width = 72

    # === Sheet 3: Отчёт (summary blocks and top-10 municipalities) ===
    ws_report = wb.create_sheet("Отчёт")
    ws_report.cell(row=1, column=1, value="Информативная сводка")
    ws_report.cell(row=1, column=1).font = Font(bold=True, size=14, color="1A3C6E")
    ws_report.merge_cells(start_row=1, start_column=1, end_row=1, end_column=3)

    summary_headers = ["Показатель", "Значение", "Комментарий"]
    for col, header in enumerate(summary_headers, 1):
        ws_report.cell(row=2, column=col, value=header)
    _style_header(ws_report, 2, len(summary_headers))

    current_row = 3
    for label, value, note in _summary_metric_rows(run):
        ws_report.cell(row=current_row, column=1, value=label)
        ws_report.cell(row=current_row, column=2, value=value)
        ws_report.cell(row=current_row, column=3, value=note)
        _style_data_row(ws_report, current_row, len(summary_headers))
        current_row += 1

    current_row += 1
    ws_report.cell(row=current_row, column=1, value="Топ-6 категорий")
    ws_report.cell(row=current_row, column=1).font = Font(bold=True, size=13, color="1A3C6E")
    ws_report.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=3)
    current_row += 1

    category_headers = ["Категория", "Количество обращений", "Доля от проблемных"]
    for col, header in enumerate(category_headers, 1):
        ws_report.cell(row=current_row, column=col, value=header)
    _style_header(ws_report, current_row, len(category_headers))
    current_row += 1

    for category, count in top_categories:
        count = count or 0
        share = round(count / max(run.problem_count or 0, 1) * 100, 1)
        ws_report.cell(row=current_row, column=1, value=category)
        ws_report.cell(row=current_row, column=2, value=count)
        ws_report.cell(row=current_row, column=3, value=f"{share}%")
        _style_data_row(ws_report, current_row, len(category_headers))
        current_row += 1

    current_row += 1
    ws_report.cell(row=current_row, column=1, value="Топ-10 муниципалитетов")
    ws_report.cell(row=current_row, column=1).font = Font(bold=True, size=13, color="1A3C6E")
    ws_report.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=5)
    current_row += 1

    report_headers = ["Муниципалитет", "Количество обращений", "Ранг", "3 ключевые проблемы", "Выдержка из текста инцидента"]
    for col, header in enumerate(report_headers, 1):
        ws_report.cell(row=current_row, column=col, value=header)
    _style_header(ws_report, current_row, len(report_headers))
    current_row += 1

    for display_rank, summary in enumerate(top_10_summaries, 1):
        top_issues = _top_issues_for_summary(summary, clusters_by_muni)
        centroid_excerpt = (summary.centroid_excerpt or "")[:500]
        ws_report.cell(row=current_row, column=1, value=summary.municipality)
        ws_report.cell(row=current_row, column=2, value=summary.problem_count)
        ws_report.cell(row=current_row, column=3, value=display_rank)
        ws_report.cell(row=current_row, column=4, value=_top_issues_text(top_issues))
        ws_report.cell(row=current_row, column=5, value=centroid_excerpt)
        _style_data_row(ws_report, current_row, len(report_headers))
        current_row += 1

    _auto_width(ws_report)
    ws_report.column_dimensions["A"].width = 30
    ws_report.column_dimensions["D"].width = 50
    ws_report.column_dimensions["E"].width = 70

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=report_{run_id}.xlsx"},
    )
