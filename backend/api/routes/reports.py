"""Report generation and download endpoints."""
from collections import defaultdict
from datetime import datetime
import io

import openpyxl
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from openpyxl.chart import BarChart, PieChart, Reference, BarChart3D
from openpyxl.chart.label import DataLabelList
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.utils import get_column_letter
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.table import Table, TableStyleInfo
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.labels import format_severity
from backend.storage.database import get_db
from backend.storage.models import Appeal, ProblemCluster, ProcessingRun, Summary

router = APIRouter()

# === Styles ===
HEADER_FONT = Font(bold=True, color="FFFFFF", size=11, name="Calibri")
HEADER_FILL = PatternFill("solid", fgColor="1A3C6E")
TITLE_FONT = Font(bold=True, size=16, color="1A3C6E", name="Calibri")
SECTION_FONT = Font(bold=True, size=13, color="2E5E99", name="Calibri")
METRIC_FONT = Font(bold=True, size=22, color="1A3C6E", name="Calibri")
METRIC_LABEL_FONT = Font(bold=True, size=10, color="6B7280", name="Calibri")
QUOTE_FONT = Font(italic=True, size=10, color="4B5563", name="Calibri")

HEADER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT_TOP = Alignment(horizontal="left", vertical="top", wrap_text=True)
CENTER_MID = Alignment(horizontal="center", vertical="center", wrap_text=True)

THIN = Side(style="thin", color="D1D5DB")
THIN_BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
THICK = Side(style="medium", color="1A3C6E")
TITLE_BORDER = Border(bottom=THICK)

SEVERITY_FILLS = {
    "CRITICAL": PatternFill("solid", fgColor="FECACA"),  # red-200
    "HIGH": PatternFill("solid", fgColor="FED7AA"),       # orange-200
    "MEDIUM": PatternFill("solid", fgColor="FEF3C7"),     # yellow-200
    "LOW": PatternFill("solid", fgColor="D1FAE5"),        # green-200
}
SEVERITY_TEXT_COLORS = {
    "CRITICAL": "991B1B",  # red-800
    "HIGH": "9A3412",       # orange-800
    "MEDIUM": "92400E",     # yellow-800
    "LOW": "065F46",        # green-800
}
ZEBRA_FILL = PatternFill("solid", fgColor="F9FAFB")
CARD_FILL = PatternFill("solid", fgColor="EFF6FF")


# === Helpers ===

def _style_header_row(ws, row_num: int, col_count: int):
    for col in range(1, col_count + 1):
        cell = ws.cell(row=row_num, column=col)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = HEADER_ALIGN
        cell.border = THIN_BORDER


def _style_data_row(ws, row_num: int, col_count: int, zebra: bool = False):
    for col in range(1, col_count + 1):
        cell = ws.cell(row=row_num, column=col)
        cell.border = THIN_BORDER
        if cell.alignment.wrap_text is None or not cell.alignment.wrap_text:
            cell.alignment = LEFT_TOP
        if zebra:
            cell.fill = ZEBRA_FILL


def _color_severity_cell(cell, severity: str | None):
    sev = (severity or "").upper()
    if sev in SEVERITY_FILLS:
        cell.fill = SEVERITY_FILLS[sev]
        cell.font = Font(bold=True, size=10, color=SEVERITY_TEXT_COLORS[sev], name="Calibri")
        cell.alignment = CENTER_MID


def _auto_width(ws, max_width: int = 60):
    for col_idx, col in enumerate(ws.columns, 1):
        max_len = 0
        col_letter = get_column_letter(col_idx)
        for cell in col:
            if cell.value:
                max_len = max(max_len, min(len(str(cell.value)), max_width))
        ws.column_dimensions[col_letter].width = max(max_len + 2, 10)


def _compact_text(value: str | None) -> str:
    return " ".join(str(value or "").split())


def _is_centroid_fallback_name(name: str, category: str | None) -> bool:
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
        "severity": cluster.severity,
        "severity_label": format_severity(cluster.severity),
        "centroid_excerpt": cluster.centroid_text or "",
    }


def _top_issues_for_summary(summary: Summary, clusters_by_muni: dict[str, list[ProblemCluster]]) -> list[dict]:
    clusters = clusters_by_muni.get(summary.municipality, [])
    if clusters:
        sorted_clusters = sorted(clusters, key=lambda c: (-(c.appeal_count or 0), c.rank or 999999))
        return [_cluster_issue(cluster) for cluster in sorted_clusters[:3]]
    out = []
    for issue in (summary.top_issues or [])[:3]:
        out.append({
            "name": _problem_name(issue.get("name"), issue.get("category"), issue.get("description")),
            "count": issue.get("count", 0),
            "category": issue.get("category"),
            "severity": issue.get("severity"),
            "severity_label": format_severity(issue.get("severity")),
            "centroid_excerpt": issue.get("centroid_excerpt", ""),
        })
    return out


def _top_issues_text(top_issues: list[dict]) -> str:
    parts = []
    for idx, issue in enumerate(top_issues[:3], 1):
        name = issue.get("name", "")
        count = issue.get("count", 0)
        sev = issue.get("severity_label", "")
        parts.append(f"{idx}. {name} ({count} обращ., {sev})")
    return "\n".join(parts)


def _main_quote(top_issues: list[dict]) -> str:
    if not top_issues:
        return ""
    excerpt = _compact_text(top_issues[0].get("centroid_excerpt", ""))[:280]
    return f"«{excerpt}»" if excerpt else ""


# === MAIN ENDPOINT ===

@router.get("/reports/{run_id}/excel")
async def download_report(run_id: int, db: AsyncSession = Depends(get_db)):
    """Generate the analytical report with 4 sheets + charts."""
    run_q = await db.execute(select(ProcessingRun).where(ProcessingRun.id == run_id))
    run = run_q.scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Run not found")
    if run.status != "completed":
        raise HTTPException(400, "Processing not completed yet")

    # Fetch data
    sum_q = await db.execute(
        select(Summary).where(Summary.run_id == run_id).order_by(Summary.rank)
    )
    summaries = sum_q.scalars().all()

    cluster_q = await db.execute(
        select(ProblemCluster)
        .where(ProblemCluster.run_id == run_id)
        .order_by(ProblemCluster.municipality, ProblemCluster.rank)
    )
    all_clusters = cluster_q.scalars().all()
    clusters_by_muni: dict[str, list[ProblemCluster]] = defaultdict(list)
    for cluster in all_clusters:
        clusters_by_muni[cluster.municipality].append(cluster)

    # Severity counts per municipality
    sev_per_muni_q = await db.execute(
        select(
            Appeal.municipality,
            Appeal.severity,
            func.count().label("cnt"),
        )
        .where(and_(Appeal.run_id == run_id, Appeal.is_problem == True))
        .group_by(Appeal.municipality, Appeal.severity)
    )
    sev_per_muni: dict[str, dict[str, int]] = defaultdict(lambda: {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0})
    for muni, sev, cnt in sev_per_muni_q.all():
        if sev:
            sev_per_muni[muni][sev] = cnt

    # Category counts
    category_q = await db.execute(
        select(Appeal.category, func.count().label("count"))
        .where(and_(Appeal.run_id == run_id, Appeal.is_problem == True, Appeal.category.isnot(None)))
        .group_by(Appeal.category)
        .order_by(func.count().desc())
        .limit(10)
    )
    top_categories = category_q.all()

    # Global severity distribution
    global_sev_q = await db.execute(
        select(Appeal.severity, func.count().label("cnt"))
        .where(and_(Appeal.run_id == run_id, Appeal.is_problem == True))
        .group_by(Appeal.severity)
    )
    global_sev = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for sev, cnt in global_sev_q.all():
        if sev in global_sev:
            global_sev[sev] = cnt

    # Critical hotspots — top critical clusters
    critical_clusters = sorted(
        [c for c in all_clusters if (c.severity or "").upper() == "CRITICAL"],
        key=lambda c: -(c.appeal_count or 0),
    )[:5]

    # Date range
    date_q = await db.execute(
        select(func.min(Appeal.date_created), func.max(Appeal.date_created))
        .where(Appeal.run_id == run_id)
    )
    date_min, date_max = date_q.one()

    wb = openpyxl.Workbook()

    _build_executive_sheet(wb, run, summaries, clusters_by_muni, critical_clusters, global_sev, date_min, date_max)
    _build_top10_sheet(wb, summaries, clusters_by_muni, sev_per_muni)
    _build_top3_sheet(wb, summaries, clusters_by_muni)
    _build_categories_sheet(wb, top_categories, global_sev, run.problem_count or 0, sev_per_muni, summaries)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=report_{run_id}.xlsx"},
    )


# === Sheet 1: Executive Summary ===

def _build_executive_sheet(wb, run, summaries, clusters_by_muni, critical_clusters, global_sev, date_min, date_max):
    ws = wb.active
    ws.title = "Итоги для руководства"
    ws.sheet_view.showGridLines = False

    # Title
    ws.cell(row=1, column=1, value="📋 ИТОГИ ДЛЯ РУКОВОДСТВА")
    ws.cell(row=1, column=1).font = TITLE_FONT
    ws.merge_cells("A1:F1")
    ws.row_dimensions[1].height = 28

    period = "—"
    if date_min and date_max:
        period = f"{date_min.strftime('%d.%m.%Y')} — {date_max.strftime('%d.%m.%Y')}"
    ws.cell(row=2, column=1, value=f"Регион: Омская область    |    Период: {period}    |    Сформировано: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    ws.cell(row=2, column=1).font = Font(italic=True, size=10, color="6B7280")
    ws.merge_cells("A2:F2")

    # === Metric cards (row 4-6) ===
    raw = max(run.raw_records or 0, (run.total_records or 0) + (run.dropped_incident_type or 0) + (run.dropped_outcome or 0))
    if raw <= 0:
        raw = run.total_records or 0
    filtered = run.total_records or 0
    problems = run.problem_count or 0
    problem_pct = round(problems / max(filtered, 1) * 100, 1)
    clusters = run.cluster_count or 0
    critical = global_sev.get("CRITICAL", 0)

    metrics = [
        ("ОБРАЩЕНИЙ", f"{raw:,}".replace(",", " "), "в исходном файле"),
        ("АКТУАЛЬНЫХ", f"{filtered:,}".replace(",", " "), "после фильтрации"),
        ("ПРОБЛЕМНЫХ", f"{problems:,}".replace(",", " "), f"{problem_pct}% от актуальных"),
        ("КЛАСТЕРОВ", f"{clusters:,}".replace(",", " "), "уникальных проблем"),
        ("КРИТИЧЕСКИХ", f"{critical:,}".replace(",", " "), "обращений критической тяжести"),
    ]

    for idx, (label, value, sub) in enumerate(metrics):
        col = 1 + idx
        ws.cell(row=4, column=col, value=label).font = METRIC_LABEL_FONT
        ws.cell(row=4, column=col).alignment = CENTER_MID
        ws.cell(row=4, column=col).fill = CARD_FILL
        ws.cell(row=5, column=col, value=value).font = METRIC_FONT
        ws.cell(row=5, column=col).alignment = CENTER_MID
        ws.cell(row=5, column=col).fill = CARD_FILL
        ws.cell(row=6, column=col, value=sub).font = Font(size=9, color="6B7280")
        ws.cell(row=6, column=col).alignment = CENTER_MID
        ws.cell(row=6, column=col).fill = CARD_FILL

    ws.row_dimensions[4].height = 20
    ws.row_dimensions[5].height = 32
    ws.row_dimensions[6].height = 18

    # === Critical hotspots ===
    row = 8
    ws.cell(row=row, column=1, value="🚨 КРИТИЧЕСКИЕ ОЧАГИ (топ-5)")
    ws.cell(row=row, column=1).font = SECTION_FONT
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    row += 1

    if critical_clusters:
        headers = ["#", "Район", "Проблема", "Обращений", "Тяжесть", "Цитата гражданина"]
        for col, h in enumerate(headers, 1):
            ws.cell(row=row, column=col, value=h)
        _style_header_row(ws, row, len(headers))
        row += 1

        for idx, c in enumerate(critical_clusters, 1):
            ws.cell(row=row, column=1, value=idx)
            ws.cell(row=row, column=2, value=c.municipality)
            ws.cell(row=row, column=3, value=_problem_name(c.cluster_name, c.category, c.description))
            ws.cell(row=row, column=4, value=c.appeal_count)
            ws.cell(row=row, column=5, value=format_severity(c.severity))
            quote = _compact_text(c.centroid_text or "")[:200]
            ws.cell(row=row, column=6, value=f"«{quote}»" if quote else "")
            ws.cell(row=row, column=6).font = QUOTE_FONT
            _style_data_row(ws, row, len(headers), zebra=(idx % 2 == 0))
            _color_severity_cell(ws.cell(row=row, column=5), c.severity)
            row += 1
    else:
        ws.cell(row=row, column=1, value="Критических очагов не выявлено")
        ws.cell(row=row, column=1).font = Font(italic=True, color="6B7280")
        row += 1

    row += 1

    # === LLM summary text ===
    ws.cell(row=row, column=1, value="📝 Аналитическая справка")
    ws.cell(row=row, column=1).font = SECTION_FONT
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    row += 1

    summary_parts = []
    for s in summaries[:3]:
        if s.summary_text and s.summary_text.strip():
            summary_parts.append(f"• {s.municipality}: {_compact_text(s.summary_text)}")

    if summary_parts:
        analytical_text = "\n\n".join(summary_parts)
    else:
        analytical_text = (
            f"Проанализировано {filtered:,} актуальных обращений граждан, "
            f"из них {problems:,} ({problem_pct}%) классифицированы как реальные проблемы. "
            f"Сформировано {clusters} кластеров уникальных проблем по муниципалитетам Омской области. "
            f"Критическую тяжесть имеют {critical} обращений — требуют первоочередного реагирования."
        ).replace(",", " ")

    ws.cell(row=row, column=1, value=analytical_text)
    ws.cell(row=row, column=1).alignment = Alignment(wrap_text=True, vertical="top")
    ws.cell(row=row, column=1).font = Font(size=11, color="1F2937")
    ws.merge_cells(start_row=row, start_column=1, end_row=row + 5, end_column=6)
    ws.row_dimensions[row].height = 90
    row += 7

    # === Top-10 bar chart ===
    ws.cell(row=row, column=1, value="📊 Топ-10 проблемных районов")
    ws.cell(row=row, column=1).font = SECTION_FONT
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    row += 1

    chart_start = row
    ws.cell(row=row, column=1, value="Район")
    ws.cell(row=row, column=2, value="Проблем")
    _style_header_row(ws, row, 2)
    row += 1

    chart_data_start = row
    for idx, s in enumerate(summaries[:10], 1):
        ws.cell(row=row, column=1, value=s.municipality)
        ws.cell(row=row, column=2, value=s.problem_count)
        _style_data_row(ws, row, 2, zebra=(idx % 2 == 0))
        row += 1
    chart_data_end = row - 1

    if chart_data_end >= chart_data_start:
        bar = BarChart()
        bar.type = "bar"
        bar.style = 12
        bar.title = "Топ-10 районов по количеству проблемных обращений"
        bar.y_axis.title = None
        bar.x_axis.title = "Количество обращений"
        bar.legend = None
        data_ref = Reference(ws, min_col=2, min_row=chart_start, max_row=chart_data_end, max_col=2)
        cats_ref = Reference(ws, min_col=1, min_row=chart_data_start, max_row=chart_data_end)
        bar.add_data(data_ref, titles_from_data=True)
        bar.set_categories(cats_ref)
        bar.height = 12
        bar.width = 22
        ws.add_chart(bar, "D" + str(chart_start))

    # Column widths
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 26
    ws.column_dimensions["C"].width = 32
    ws.column_dimensions["D"].width = 16
    ws.column_dimensions["E"].width = 14
    ws.column_dimensions["F"].width = 48

    # Freeze top
    ws.freeze_panes = "A3"


# === Sheet 2: Top-10 ===

def _build_top10_sheet(wb, summaries, clusters_by_muni, sev_per_muni):
    ws = wb.create_sheet("Топ-10 районов")
    ws.sheet_view.showGridLines = False

    ws.cell(row=1, column=1, value="Топ-10 районов с распределением по тяжести")
    ws.cell(row=1, column=1).font = TITLE_FONT
    ws.merge_cells("A1:J1")
    ws.row_dimensions[1].height = 26

    headers = [
        "Ранг",
        "Район",
        "Всего проблем",
        "Критич.",
        "Высок.",
        "Средн.",
        "Низк.",
        "Главная проблема",
        "3 ключевые проблемы",
        "Главная цитата",
    ]
    for col, h in enumerate(headers, 1):
        ws.cell(row=3, column=col, value=h)
    _style_header_row(ws, 3, len(headers))

    data_start = 4
    for idx, summary in enumerate(summaries[:10], 1):
        row = data_start + idx - 1
        top_issues = _top_issues_for_summary(summary, clusters_by_muni)
        sev = sev_per_muni.get(summary.municipality, {})

        ws.cell(row=row, column=1, value=idx)
        ws.cell(row=row, column=2, value=summary.municipality)
        ws.cell(row=row, column=3, value=summary.problem_count)
        ws.cell(row=row, column=4, value=sev.get("CRITICAL", 0))
        ws.cell(row=row, column=5, value=sev.get("HIGH", 0))
        ws.cell(row=row, column=6, value=sev.get("MEDIUM", 0))
        ws.cell(row=row, column=7, value=sev.get("LOW", 0))
        ws.cell(row=row, column=8, value=top_issues[0]["name"] if top_issues else "")
        ws.cell(row=row, column=9, value=_top_issues_text(top_issues))
        ws.cell(row=row, column=10, value=_main_quote(top_issues))
        ws.cell(row=row, column=10).font = QUOTE_FONT

        _style_data_row(ws, row, len(headers), zebra=(idx % 2 == 0))

        # Colored severity counts
        if sev.get("CRITICAL", 0) > 0:
            ws.cell(row=row, column=4).fill = SEVERITY_FILLS["CRITICAL"]
            ws.cell(row=row, column=4).font = Font(bold=True, color=SEVERITY_TEXT_COLORS["CRITICAL"])
            ws.cell(row=row, column=4).alignment = CENTER_MID
        if sev.get("HIGH", 0) > 0:
            ws.cell(row=row, column=5).fill = SEVERITY_FILLS["HIGH"]
            ws.cell(row=row, column=5).alignment = CENTER_MID
        if sev.get("MEDIUM", 0) > 0:
            ws.cell(row=row, column=6).fill = SEVERITY_FILLS["MEDIUM"]
            ws.cell(row=row, column=6).alignment = CENTER_MID
        if sev.get("LOW", 0) > 0:
            ws.cell(row=row, column=7).fill = SEVERITY_FILLS["LOW"]
            ws.cell(row=row, column=7).alignment = CENTER_MID

    data_end = data_start + min(10, len(summaries)) - 1

    # Conditional formatting on "Всего проблем"
    if data_end >= data_start:
        ws.conditional_formatting.add(
            f"C{data_start}:C{data_end}",
            ColorScaleRule(
                start_type="min", start_color="D1FAE5",
                mid_type="percentile", mid_value=50, mid_color="FEF3C7",
                end_type="max", end_color="FECACA",
            ),
        )

    # AutoFilter
    ws.auto_filter.ref = f"A3:J{data_end}"

    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 26
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["D"].width = 10
    ws.column_dimensions["E"].width = 10
    ws.column_dimensions["F"].width = 10
    ws.column_dimensions["G"].width = 10
    ws.column_dimensions["H"].width = 36
    ws.column_dimensions["I"].width = 50
    ws.column_dimensions["J"].width = 60

    ws.freeze_panes = "C4"


# === Sheet 3: Top-3 detailed ===

def _build_top3_sheet(wb, summaries, clusters_by_muni):
    ws = wb.create_sheet("Топ-3 подробно")
    ws.sheet_view.showGridLines = False

    ws.cell(row=1, column=1, value="Топ-3 района — все проблемы")
    ws.cell(row=1, column=1).font = TITLE_FONT
    ws.merge_cells("A1:E1")
    ws.row_dimensions[1].height = 26

    row = 3
    for display_rank, summary in enumerate(summaries[:3], 1):
        clusters = clusters_by_muni.get(summary.municipality, [])
        clusters = sorted(clusters, key=lambda c: c.rank or 999999)
        muni_problems = sum(c.appeal_count or 0 for c in clusters)

        # Section title
        title = f"{display_rank}. {summary.municipality} — {muni_problems:,} проблемных обращений, {len(clusters)} кластеров".replace(",", " ")
        ws.cell(row=row, column=1, value=title)
        ws.cell(row=row, column=1).font = SECTION_FONT
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
        row += 1

        # Headers
        detail_headers = ["Ранг", "Проблема", "Обращений", "Тяжесть", "Цитата из центроида"]
        for col, h in enumerate(detail_headers, 1):
            ws.cell(row=row, column=col, value=h)
        _style_header_row(ws, row, len(detail_headers))
        row += 1

        data_start = row
        for idx, cluster in enumerate(clusters, 1):
            ws.cell(row=row, column=1, value=cluster.rank or idx)
            ws.cell(row=row, column=2, value=_problem_name(cluster.cluster_name, cluster.category, cluster.description))
            ws.cell(row=row, column=3, value=cluster.appeal_count)
            ws.cell(row=row, column=4, value=format_severity(cluster.severity))
            quote = _compact_text(cluster.centroid_text or "")[:400]
            ws.cell(row=row, column=5, value=f"«{quote}»" if quote else "")
            ws.cell(row=row, column=5).font = QUOTE_FONT

            _style_data_row(ws, row, len(detail_headers), zebra=(idx % 2 == 0))
            _color_severity_cell(ws.cell(row=row, column=4), cluster.severity)
            row += 1

        # Conditional formatting on "Обращений" within this block
        if row > data_start:
            ws.conditional_formatting.add(
                f"C{data_start}:C{row - 1}",
                ColorScaleRule(
                    start_type="min", start_color="D1FAE5",
                    mid_type="percentile", mid_value=50, mid_color="FEF3C7",
                    end_type="max", end_color="FECACA",
                ),
            )

        row += 2

    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 46
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["D"].width = 14
    ws.column_dimensions["E"].width = 70

    ws.freeze_panes = "A3"


# === Sheet 4: Categories + Severity charts ===

def _build_categories_sheet(wb, top_categories, global_sev, total_problems, sev_per_muni, summaries):
    ws = wb.create_sheet("Категории и тяжесть")
    ws.sheet_view.showGridLines = False

    ws.cell(row=1, column=1, value="📈 Категории проблем и распределение тяжести")
    ws.cell(row=1, column=1).font = TITLE_FONT
    ws.merge_cells("A1:H1")
    ws.row_dimensions[1].height = 26

    # === Categories table ===
    ws.cell(row=3, column=1, value="Категории обращений").font = SECTION_FONT
    ws.merge_cells("A3:C3")

    cat_headers = ["Категория", "Обращений", "Доля"]
    for col, h in enumerate(cat_headers, 1):
        ws.cell(row=4, column=col, value=h)
    _style_header_row(ws, 4, len(cat_headers))

    cat_start = 5
    row = cat_start
    for idx, (category, count) in enumerate(top_categories, 1):
        count = count or 0
        share = round(count / max(total_problems, 1) * 100, 1)
        ws.cell(row=row, column=1, value=category)
        ws.cell(row=row, column=2, value=count)
        ws.cell(row=row, column=3, value=f"{share}%")
        _style_data_row(ws, row, len(cat_headers), zebra=(idx % 2 == 0))
        row += 1
    cat_end = row - 1

    # Bar chart for categories
    if cat_end >= cat_start:
        bar = BarChart()
        bar.type = "bar"
        bar.style = 11
        bar.title = "Распределение обращений по категориям"
        bar.legend = None
        bar.x_axis.title = "Количество обращений"
        data_ref = Reference(ws, min_col=2, min_row=4, max_row=cat_end, max_col=2)
        cats_ref = Reference(ws, min_col=1, min_row=cat_start, max_row=cat_end)
        bar.add_data(data_ref, titles_from_data=True)
        bar.set_categories(cats_ref)
        bar.height = 10
        bar.width = 18
        ws.add_chart(bar, "E3")

    # === Severity distribution ===
    sev_row_title = cat_end + 3
    ws.cell(row=sev_row_title, column=1, value="Распределение по тяжести").font = SECTION_FONT
    ws.merge_cells(start_row=sev_row_title, start_column=1, end_row=sev_row_title, end_column=3)

    sev_headers = ["Тяжесть", "Обращений", "Доля"]
    for col, h in enumerate(sev_headers, 1):
        ws.cell(row=sev_row_title + 1, column=col, value=h)
    _style_header_row(ws, sev_row_title + 1, len(sev_headers))

    sev_data_start = sev_row_title + 2
    sev_order = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    row = sev_data_start
    for sev in sev_order:
        count = global_sev.get(sev, 0)
        share = round(count / max(total_problems, 1) * 100, 1)
        ws.cell(row=row, column=1, value=format_severity(sev))
        ws.cell(row=row, column=2, value=count)
        ws.cell(row=row, column=3, value=f"{share}%")
        _color_severity_cell(ws.cell(row=row, column=1), sev)
        ws.cell(row=row, column=2).border = THIN_BORDER
        ws.cell(row=row, column=3).border = THIN_BORDER
        ws.cell(row=row, column=2).alignment = CENTER_MID
        ws.cell(row=row, column=3).alignment = CENTER_MID
        row += 1
    sev_data_end = row - 1

    # Pie chart for severity
    if sev_data_end >= sev_data_start:
        pie = PieChart()
        pie.title = "Распределение по тяжести"
        data_ref = Reference(ws, min_col=2, min_row=sev_row_title + 1, max_row=sev_data_end, max_col=2)
        cats_ref = Reference(ws, min_col=1, min_row=sev_data_start, max_row=sev_data_end)
        pie.add_data(data_ref, titles_from_data=True)
        pie.set_categories(cats_ref)
        pie.dataLabels = DataLabelList(showPercent=True)
        pie.height = 10
        pie.width = 14
        ws.add_chart(pie, "E" + str(sev_row_title))

    # === Heatmap: район × тяжесть (top-10 районов) ===
    heat_row = sev_data_end + 3
    ws.cell(row=heat_row, column=1, value="Тепловая карта: район × тяжесть (топ-10)").font = SECTION_FONT
    ws.merge_cells(start_row=heat_row, start_column=1, end_row=heat_row, end_column=6)
    heat_row += 1

    heat_headers = ["Район", "Критич.", "Высок.", "Средн.", "Низк.", "Всего"]
    for col, h in enumerate(heat_headers, 1):
        ws.cell(row=heat_row, column=col, value=h)
    _style_header_row(ws, heat_row, len(heat_headers))
    heat_data_start = heat_row + 1

    row = heat_data_start
    for summary in summaries[:10]:
        sev = sev_per_muni.get(summary.municipality, {})
        ws.cell(row=row, column=1, value=summary.municipality)
        ws.cell(row=row, column=2, value=sev.get("CRITICAL", 0))
        ws.cell(row=row, column=3, value=sev.get("HIGH", 0))
        ws.cell(row=row, column=4, value=sev.get("MEDIUM", 0))
        ws.cell(row=row, column=5, value=sev.get("LOW", 0))
        ws.cell(row=row, column=6, value=summary.problem_count)
        for col in range(1, 7):
            ws.cell(row=row, column=col).border = THIN_BORDER
            ws.cell(row=row, column=col).alignment = CENTER_MID if col > 1 else LEFT_TOP
        row += 1
    heat_data_end = row - 1

    # Color scale on each severity column
    if heat_data_end >= heat_data_start:
        for col_letter, color_end in [("B", "FECACA"), ("C", "FED7AA"), ("D", "FEF3C7"), ("E", "D1FAE5")]:
            ws.conditional_formatting.add(
                f"{col_letter}{heat_data_start}:{col_letter}{heat_data_end}",
                ColorScaleRule(
                    start_type="min", start_color="FFFFFF",
                    end_type="max", end_color=color_end,
                ),
            )

    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["D"].width = 14
    ws.column_dimensions["E"].width = 14
    ws.column_dimensions["F"].width = 14
    ws.column_dimensions["G"].width = 4
    ws.column_dimensions["H"].width = 4

    ws.freeze_panes = "A3"
