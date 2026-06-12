"""Report generation and download endpoints."""
from collections import defaultdict
from datetime import datetime
import io

import openpyxl
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from openpyxl.drawing.image import Image as XLImage
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.utils import get_column_letter
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.display_fields import appeal_display_category, appeal_display_severity, category_value, severity_value
from backend.labels import format_severity
from backend.storage.database import get_db
from backend.storage.models import Appeal, AppealClusterMap, ProblemCluster, ProcessingRun, RunLoadStats, Summary

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
    severity = severity_value(cluster.severity)
    return {
        "name": _problem_name(cluster.cluster_name, cluster.category, cluster.description),
        "count": cluster.appeal_count or 0,
        "category": category_value(cluster.category),
        "severity": severity,
        "severity_label": format_severity(severity),
        "centroid_excerpt": cluster.centroid_text or "",
    }


def _top_issues_for_summary(summary: Summary, clusters_by_muni: dict[str, list[ProblemCluster]]) -> list[dict]:
    clusters = clusters_by_muni.get(summary.municipality, [])
    if clusters:
        sorted_clusters = sorted(clusters, key=lambda c: (-(c.appeal_count or 0), c.rank or 999999))
        return [_cluster_issue(cluster) for cluster in sorted_clusters[:3]]
    out = []
    for issue in (summary.top_issues or [])[:3]:
        severity = severity_value(issue.get("severity"))
        out.append({
            "name": _problem_name(issue.get("name"), issue.get("category"), issue.get("description")),
            "count": issue.get("count", 0),
            "category": category_value(issue.get("category")),
            "severity": severity,
            "severity_label": format_severity(severity),
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


def _merge_resolved_counts(rows, key_attr: str, pre_counts: dict | None, limit: int = 8) -> list[dict]:
    """Слить решено/открыто из выборки с закрытыми до анализа строками файла."""
    merged: dict[str, dict] = {}
    for row in rows:
        key = getattr(row, key_attr) or "Не указано"
        merged[key] = {"name": key, "resolved": row.resolved or 0, "open": row.open or 0}
    for key, count in (pre_counts or {}).items():
        key = str(key).strip() or "Не указано"
        entry = merged.setdefault(key, {"name": key, "resolved": 0, "open": 0})
        entry["resolved"] += int(count)
    items = sorted(merged.values(), key=lambda e: e["resolved"] + e["open"], reverse=True)[:limit]
    for entry in items:
        total = entry["resolved"] + entry["open"]
        entry["rate"] = round(entry["resolved"] / max(total, 1) * 100, 1)
    return items


# === Графики как изображения ===
# Встроенные OOXML-диаграммы не отрисовываются рядом просмотрщиков
# (OfficeSuite, мобильные/онлайн вьюверы), поэтому графики рендерятся
# matplotlib-ом в PNG и вставляются в лист картинками — это работает везде.

BRAND_BLUE = "#2B3990"


def _fig_image(fig, width_px: int, height_px: int) -> XLImage:
    import matplotlib.pyplot as plt

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    image = XLImage(buf)
    image.width = width_px
    image.height = height_px
    return image


def _plt():
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    return plt


def _add_image(ws, image: XLImage, col: int, row: int) -> None:
    """Вставить картинку через twoCellAnchor (0-based col/row).

    openpyxl по умолчанию пишет oneCellAnchor, который часть просмотрщиков
    (OfficeSuite и другие упрощённые рендереры) не отрисовывает вовсе.
    Excel сам всегда использует twoCellAnchor — делаем так же, рамку
    рассчитываем по фактическим ширинам колонок и высотам строк листа.
    """
    from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor

    def column_px(index: int) -> int:
        dim = ws.column_dimensions.get(get_column_letter(index + 1))
        chars = dim.width if dim is not None and dim.width else 8.43
        return int(chars * 7 + 5)

    def row_px(index: int) -> int:
        dim = ws.row_dimensions.get(index + 1)
        points = dim.height if dim is not None and dim.height else 15.0
        return int(points * 96 / 72)

    end_col, remaining = col, image.width
    while remaining > 0 and end_col < col + 60:
        remaining -= column_px(end_col)
        end_col += 1
    end_row, remaining = row, image.height
    while remaining > 0 and end_row < row + 200:
        remaining -= row_px(end_row)
        end_row += 1

    anchor = TwoCellAnchor(editAs="oneCell")
    anchor._from = AnchorMarker(col=col, colOff=0, row=row, rowOff=0)
    anchor.to = AnchorMarker(col=end_col, colOff=0, row=end_row, rowOff=0)
    image.anchor = anchor
    ws.add_image(image)


def _bar_png(labels: list[str], values: list, title: str, width_px: int = 840, height_px: int = 420) -> XLImage:
    plt = _plt()
    fig, ax = plt.subplots(figsize=(8.4, 4.2))
    positions = range(len(labels))[::-1]
    bars = ax.barh(list(positions), values, color=BRAND_BLUE, height=0.62)
    ax.set_yticks(list(positions))
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_title(title, fontsize=11, fontweight="bold", color="#1A3C6E", loc="left")
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(axis="x", labelsize=8, colors="#6B7280")
    ax.grid(axis="x", color="#E5E7EB", linewidth=0.7)
    ax.set_axisbelow(True)
    max_value = max([v or 0 for v in values] + [1])
    for bar, value in zip(bars, values):
        ax.text(
            (value or 0) + max_value * 0.01, bar.get_y() + bar.get_height() / 2,
            f"{value:,}".replace(",", " "), va="center", fontsize=8, color="#374151",
        )
    fig.tight_layout()
    return _fig_image(fig, width_px, height_px)


def _pie_png(labels: list[str], values: list, colors: list[str], title: str, *, doughnut: bool = False,
             width_px: int = 430, height_px: int = 330) -> XLImage:
    plt = _plt()
    fig, ax = plt.subplots(figsize=(4.4, 3.4))
    shown = [(label, value, color) for label, value, color in zip(labels, values, colors) if (value or 0) > 0]
    if not shown:
        shown = [("Нет данных", 1, "#D1D5DB")]
    wedge_props = {"width": 0.45, "edgecolor": "white"} if doughnut else {"edgecolor": "white"}
    ax.pie(
        [item[1] for item in shown],
        labels=[item[0] for item in shown],
        colors=[item[2] for item in shown],
        autopct="%1.0f%%",
        startangle=90,
        counterclock=False,
        wedgeprops=wedge_props,
        textprops={"fontsize": 8.5, "color": "#374151"},
        pctdistance=0.78 if doughnut else 0.6,
    )
    ax.set_title(title, fontsize=11, fontweight="bold", color="#1A3C6E")
    fig.tight_layout()
    return _fig_image(fig, width_px, height_px)


def _line_png(labels: list[str], values: list, title: str, width_px: int = 840, height_px: int = 360) -> XLImage:
    plt = _plt()
    fig, ax = plt.subplots(figsize=(8.4, 3.6))
    xs = range(len(labels))
    ax.plot(xs, values, color=BRAND_BLUE, linewidth=2.2, marker="o", markersize=3.5)
    ax.fill_between(xs, values, color=BRAND_BLUE, alpha=0.08)
    step = max(len(labels) // 12, 1)
    ax.set_xticks(list(xs)[::step])
    ax.set_xticklabels(labels[::step], fontsize=8, rotation=45, ha="right")
    ax.set_title(title, fontsize=11, fontweight="bold", color="#1A3C6E", loc="left")
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(axis="y", labelsize=8, colors="#6B7280")
    ax.grid(axis="y", color="#E5E7EB", linewidth=0.7)
    ax.set_axisbelow(True)
    fig.tight_layout()
    return _fig_image(fig, width_px, height_px)


def _month_label(iso_value: str) -> str:
    try:
        return datetime.fromisoformat(str(iso_value)).strftime("%m.%Y")
    except Exception:
        return str(iso_value)[:7]


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

    display_severity = appeal_display_severity()
    display_category = appeal_display_category()

    # Severity counts per municipality
    sev_per_muni_q = await db.execute(
        select(
            Appeal.municipality,
            display_severity.label("severity"),
            func.count().label("cnt"),
        )
        .where(and_(Appeal.run_id == run_id, Appeal.is_problem == True))
        .group_by(Appeal.municipality, display_severity)
    )
    sev_per_muni: dict[str, dict[str, int]] = defaultdict(lambda: {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0})
    for muni, sev, cnt in sev_per_muni_q.all():
        sev_per_muni[muni][severity_value(sev)] = cnt

    # Global severity distribution
    global_sev_q = await db.execute(
        select(display_severity.label("severity"), func.count().label("cnt"))
        .where(and_(Appeal.run_id == run_id, Appeal.is_problem == True))
        .group_by(display_severity)
    )
    global_sev = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for sev, cnt in global_sev_q.all():
        global_sev[severity_value(sev)] = cnt

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

    resolved_by_muni_q = await db.execute(
        select(
            Appeal.municipality,
            func.count().filter(Appeal.date_closed.isnot(None)).label("resolved"),
            func.count().filter(Appeal.date_closed.is_(None)).label("open"),
            func.avg(func.extract("epoch", Appeal.date_closed - Appeal.date_created) / 86400).label("avg_days"),
        )
        .where(Appeal.run_id == run_id)
        .group_by(Appeal.municipality)
        .order_by(func.count().filter(Appeal.date_closed.isnot(None)).desc())
    )
    resolved_by_muni = resolved_by_muni_q.all()

    resolved_by_category_q = await db.execute(
        select(
            display_category.label("category"),
            func.count().filter(Appeal.date_closed.isnot(None)).label("resolved"),
            func.count().filter(Appeal.date_closed.is_(None)).label("open"),
        )
        .where(Appeal.run_id == run_id)
        .group_by(display_category)
        .order_by(func.count().filter(Appeal.date_closed.isnot(None)).desc())
    )
    resolved_by_category = resolved_by_category_q.all()

    # Сводные счетчики статуса (включая отброшенные при загрузке строки)
    # и показатели качества данных для листа «Решенные проблемы».
    status_totals_q = await db.execute(
        select(
            func.count().label("processed"),
            func.count().filter(Appeal.date_closed.isnot(None)).label("resolved"),
            func.count().filter(Appeal.date_closed.is_(None)).label("open"),
            func.avg(func.extract("epoch", Appeal.date_closed - Appeal.date_created) / 86400)
            .filter(and_(Appeal.date_closed.isnot(None), Appeal.date_closed >= Appeal.date_created))
            .label("avg_days"),
            func.count()
            .filter(and_(Appeal.date_closed.isnot(None), Appeal.date_closed < Appeal.date_created))
            .label("bad_dates"),
            func.count()
            .filter(and_(Appeal.date_closed.isnot(None), func.nullif(func.trim(Appeal.outcome), "").is_(None)))
            .label("closed_no_outcome"),
        )
        .where(Appeal.run_id == run_id)
    )
    status_row = status_totals_q.one()

    load_stats_q = await db.execute(select(RunLoadStats).where(RunLoadStats.run_id == run_id))
    load_stats_row = load_stats_q.scalar_one_or_none()
    prefiltered = (load_stats_row.prefiltered if load_stats_row else None) or {}
    pre_resolved = prefiltered.get("resolved") or {}

    resolved_prefiltered = run.dropped_outcome or 0
    unsolvable_count = run.dropped_incident_type or 0
    raw_total = max(run.raw_records or 0, (run.total_records or 0) + resolved_prefiltered + unsolvable_count)
    status = {
        "raw_total": raw_total,
        "processed": status_row.processed or 0,
        "resolved_in_data": status_row.resolved or 0,
        "resolved_prefiltered": resolved_prefiltered,
        "resolved_total": (status_row.resolved or 0) + resolved_prefiltered,
        "open": status_row.open or 0,
        "unsolvable": unsolvable_count,
        "avg_days": round(float(status_row.avg_days or 0), 1),
        "bad_dates": status_row.bad_dates or 0,
        "closed_no_outcome": status_row.closed_no_outcome or 0,
    }
    status["resolution_rate"] = round(status["resolved_total"] / max(raw_total, 1) * 100, 1)

    # Динамика закрытия: помесячно из выборки + закрытые до анализа.
    timeline_q = await db.execute(
        select(func.date_trunc("month", Appeal.date_closed).label("bucket"), func.count().label("count"))
        .where(and_(Appeal.run_id == run_id, Appeal.date_closed.isnot(None)))
        .group_by("bucket")
        .order_by("bucket")
    )
    timeline_counts: dict[str, int] = {}
    for row in timeline_q.all():
        if row.bucket:
            timeline_counts[row.bucket.isoformat()] = int(row.count or 0)
    for bucket, count in (pre_resolved.get("timeline") or {}).items():
        key = str(bucket)
        timeline_counts[key] = timeline_counts.get(key, 0) + int(count)
    timeline = sorted(timeline_counts.items())
    status["dated_resolved"] = sum(count for _, count in timeline)

    # Крупнейшие решённые проблемы: кластеры с наибольшим числом закрытых обращений.
    resolved_clusters_q = await db.execute(
        select(
            ProblemCluster.municipality,
            ProblemCluster.cluster_name,
            ProblemCluster.category,
            ProblemCluster.description,
            ProblemCluster.appeal_count,
            func.count().label("resolved_count"),
        )
        .join(AppealClusterMap, AppealClusterMap.cluster_id == ProblemCluster.id)
        .join(Appeal, Appeal.id == AppealClusterMap.appeal_id)
        .where(and_(ProblemCluster.run_id == run_id, Appeal.date_closed.isnot(None)))
        .group_by(ProblemCluster.id)
        .order_by(func.count().desc())
        .limit(6)
    )
    resolved_clusters = resolved_clusters_q.all()

    merged_by_muni = _merge_resolved_counts(resolved_by_muni, "municipality", pre_resolved.get("by_municipality"))
    merged_by_category = _merge_resolved_counts(resolved_by_category, "category", pre_resolved.get("by_category"))

    wb = openpyxl.Workbook()

    _build_executive_sheet(wb, run, summaries, clusters_by_muni, critical_clusters, global_sev, date_min, date_max, status)
    _build_top10_sheet(wb, summaries, clusters_by_muni, sev_per_muni)
    _build_top3_sheet(wb, summaries, clusters_by_muni)
    _build_resolved_sheet(wb, status, resolved_clusters, merged_by_muni, merged_by_category, timeline)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=report.xlsx; filename*=UTF-8''%D0%90%D0%BD%D0%B0%D0%BB%D0%B8%D1%82%D0%B8%D1%87%D0%B5%D1%81%D0%BA%D0%B8%D0%B9%20%D0%BE%D1%82%D1%87%D0%B5%D1%82%20%E2%80%94%20%D0%93%D0%BE%D0%BB%D0%BE%D1%81%20%D0%9E%D0%BC%D1%81%D0%BA%D0%B0.xlsx"},
    )


@router.get("/reports/{run_id}/docx")
async def download_report_docx(run_id: int, db: AsyncSession = Depends(get_db)):
    """Generate a compact Word analytical note for decision makers."""
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml.ns import qn
        from docx.shared import Cm, Pt
    except Exception as exc:
        raise HTTPException(500, "python-docx is not installed") from exc

    run_q = await db.execute(select(ProcessingRun).where(ProcessingRun.id == run_id))
    run = run_q.scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Run not found")
    if run.status != "completed":
        raise HTTPException(400, "Processing not completed yet")

    summaries_q = await db.execute(
        select(Summary).where(Summary.run_id == run_id).order_by(Summary.rank).limit(10)
    )
    summaries = summaries_q.scalars().all()

    clusters_q = await db.execute(
        select(ProblemCluster)
        .where(ProblemCluster.run_id == run_id)
        .order_by(ProblemCluster.appeal_count.desc(), ProblemCluster.rank)
        .limit(15)
    )
    clusters = clusters_q.scalars().all()

    display_category = appeal_display_category()
    display_severity = appeal_display_severity()
    categories_q = await db.execute(
        select(display_category.label("category"), func.count().label("count"))
        .where(and_(Appeal.run_id == run_id, Appeal.is_problem == True))
        .group_by(display_category)
        .order_by(func.count().desc())
        .limit(8)
    )
    categories = categories_q.all()

    severity_q = await db.execute(
        select(display_severity.label("severity"), func.count().label("count"))
        .where(and_(Appeal.run_id == run_id, Appeal.is_problem == True))
        .group_by(display_severity)
    )
    severity = {severity_value(row.severity): row.count for row in severity_q.all()}

    resolved_q = await db.execute(
        select(
            func.count().filter(Appeal.date_closed.isnot(None)).label("resolved"),
            func.count().filter(Appeal.date_closed.is_(None)).label("open"),
            func.avg(func.extract("epoch", Appeal.date_closed - Appeal.date_created) / 86400)
            .filter(and_(Appeal.date_closed.isnot(None), Appeal.date_closed >= Appeal.date_created))
            .label("avg_days"),
        )
        .where(Appeal.run_id == run_id)
    )
    resolved = resolved_q.one()

    date_q = await db.execute(
        select(func.min(Appeal.date_created), func.max(Appeal.date_created))
        .where(Appeal.run_id == run_id)
    )
    date_min, date_max = date_q.one()

    # === Расчет показателей (решено считается от всех записей файла) ===
    filtered = run.total_records or 0
    problems = run.problem_count or 0
    problem_pct = round(problems / max(filtered, 1) * 100, 1)
    severe_count = (severity.get("CRITICAL", 0) or 0) + (severity.get("HIGH", 0) or 0)
    resolved_in_data = resolved.resolved or 0
    resolved_prefiltered = run.dropped_outcome or 0
    unsolvable_count = run.dropped_incident_type or 0
    resolved_count = resolved_in_data + resolved_prefiltered
    open_count = resolved.open or 0
    raw_total = max(run.raw_records or 0, filtered + resolved_prefiltered + unsolvable_count)
    resolved_rate = round(resolved_count / max(raw_total, 1) * 100, 1)
    avg_days = round(float(resolved.avg_days or 0), 1)

    period = "за весь период данных"
    if date_min and date_max:
        period = f"за период с {date_min.strftime('%d.%m.%Y')} по {date_max.strftime('%d.%m.%Y')}"

    # === Классический официальный шаблон записки ===
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(13)
    rpr = normal.element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:eastAsia"), "Times New Roman")
    rfonts.set(qn("w:cs"), "Times New Roman")

    for section in doc.sections:
        section.left_margin = Cm(3)
        section.right_margin = Cm(1.5)
        section.top_margin = Cm(2)
        section.bottom_margin = Cm(2)

    def para(text="", *, bold=False, italic=False, align=WD_ALIGN_PARAGRAPH.JUSTIFY, indent=True, size=None, space_after=6, space_before=0):
        p = doc.add_paragraph()
        p.alignment = align
        fmt = p.paragraph_format
        fmt.space_after = Pt(space_after)
        fmt.space_before = Pt(space_before)
        fmt.line_spacing = 1.15
        if indent:
            fmt.first_line_indent = Cm(1.25)
        run_obj = p.add_run(text)
        run_obj.bold = bold
        run_obj.italic = italic
        if size:
            run_obj.font.size = Pt(size)
        return p

    def heading(text):
        return para(text, bold=True, align=WD_ALIGN_PARAGRAPH.LEFT, indent=False, space_after=6, space_before=12)

    def finish_table(table):
        table.style = "Table Grid"
        for row_index, table_row in enumerate(table.rows):
            for cell in table_row.cells:
                for cell_paragraph in cell.paragraphs:
                    cell_paragraph.paragraph_format.space_after = Pt(2)
                    cell_paragraph.paragraph_format.line_spacing = 1
                    for cell_run in cell_paragraph.runs:
                        cell_run.font.size = Pt(11)
                        if row_index == 0:
                            cell_run.bold = True

    # Шапка
    para("АНАЛИТИЧЕСКАЯ ЗАПИСКА", bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, indent=False, size=16, space_after=2)
    para(
        "по результатам автоматизированной обработки обращений граждан",
        align=WD_ALIGN_PARAGRAPH.CENTER, indent=False, space_after=2,
    )
    para(f"Омская область, {period}", align=WD_ALIGN_PARAGRAPH.CENTER, indent=False, space_after=2)
    para(
        f"г. Омск\t\t\t\t\t\t{datetime.now().strftime('%d.%m.%Y')}",
        align=WD_ALIGN_PARAGRAPH.LEFT, indent=False, italic=True, space_after=12,
    )

    # 1. Общие сведения
    heading("1. Общие сведения")
    para(
        f"Настоящая записка подготовлена по результатам автоматизированной обработки файла «{run.filename or 'не указан'}». "
        f"Всего в исходном файле содержится {raw_total:,} записей. ".replace(",", " ")
        + f"В автоматический анализ (классификация, определение тяжести, кластеризация повторяющихся проблем) "
        f"включено {filtered:,} актуальных обращений; ".replace(",", " ")
        + f"{resolved_prefiltered:,} обращений к моменту выгрузки уже были закрыты (итоги «Решено», «Закрыто», «Разъяснено», «Перенаправлено»), "
        .replace(",", " ")
        + f"{unsolvable_count:,} отнесены к категории «Нерешаемый».".replace(",", " ")
    )

    # 2. Ключевые показатели
    heading("2. Ключевые показатели")
    metrics = doc.add_table(rows=1, cols=2)
    metrics.rows[0].cells[0].text = "Показатель"
    metrics.rows[0].cells[1].text = "Значение"
    for label, value in [
        ("Всего записей в исходном файле", f"{raw_total:,}".replace(",", " ")),
        ("Включено в автоматический анализ", f"{filtered:,}".replace(",", " ")),
        ("Проблемных обращений", f"{problems:,} ({problem_pct}% от проанализированных)".replace(",", " ")),
        ("Кластеров повторяющихся проблем", f"{run.cluster_count or 0:,}".replace(",", " ")),
        ("Обращений критичной и высокой тяжести", f"{severe_count:,}".replace(",", " ")),
        ("Закрыто", f"{resolved_prefiltered:,}".replace(",", " ")),
        ("Нерешаемых обращений", f"{unsolvable_count:,}".replace(",", " ")),
        ("Средний срок решения", f"{avg_days} дн."),
    ]:
        row = metrics.add_row().cells
        row[0].text = label
        row[1].text = value
    finish_table(metrics)

    # 3. Территории
    heading("3. Территории с наибольшей нагрузкой")
    top_table = doc.add_table(rows=1, cols=4)
    for cell, label in zip(top_table.rows[0].cells, ["Ранг", "Муниципальное образование", "Проблемных обращений", "Ключевые темы"]):
        cell.text = label
    for summary in summaries:
        issues = []
        for issue in (summary.top_issues or [])[:3]:
            if isinstance(issue, dict):
                name = issue.get("name") or issue.get("category") or ""
                count = issue.get("count", 0)
                if name:
                    issues.append(f"{name} ({count})")
        row = top_table.add_row().cells
        row[0].text = str(summary.rank or "")
        row[1].text = summary.municipality or ""
        row[2].text = str(summary.problem_count or 0)
        row[3].text = "; ".join(issues)
    finish_table(top_table)

    # 4. Категории
    heading("4. Основные категории проблемных обращений")
    cat_table = doc.add_table(rows=1, cols=3)
    for cell, label in zip(cat_table.rows[0].cells, ["Категория", "Обращений", "Доля проблемных"]):
        cell.text = label
    for category, count in categories:
        row = cat_table.add_row().cells
        row[0].text = category or "Другое"
        row[1].text = str(count or 0)
        row[2].text = f"{round((count or 0) / max(problems, 1) * 100, 1)}%"
    finish_table(cat_table)

    # 5. Кластеры
    heading("5. Крупнейшие повторяющиеся проблемы")
    cluster_table = doc.add_table(rows=1, cols=5)
    for cell, label in zip(cluster_table.rows[0].cells, ["Муниципальное образование", "Проблема", "Категория", "Тяжесть", "Обращений"]):
        cell.text = label
    for cluster in clusters[:10]:
        row = cluster_table.add_row().cells
        row[0].text = cluster.municipality or ""
        row[1].text = _problem_name(cluster.cluster_name, cluster.category, cluster.description)
        row[2].text = category_value(cluster.category)
        row[3].text = format_severity(severity_value(cluster.severity))
        row[4].text = str(cluster.appeal_count or 0)
    finish_table(cluster_table)

    # 6. Выводы
    heading("6. Выводы и предложения")
    para(
        "Первоочередного внимания требуют территории и категории, в которых одновременно высоки количество обращений, "
        "доля случаев критичной и высокой тяжести и число открытых проблем."
    )
    para(
        "Рекомендуется поручить профильным ведомствам проработку крупнейших повторяющихся проблем, перечисленных в разделе 5, "
        "с установлением контрольных сроков, а также организовать мониторинг открытых обращений в районах с наибольшей нагрузкой."
    )
    para("Детальные таблицы по всем муниципальным образованиям приведены в Excel-версии отчета.")

    # Подпись
    para("", space_after=18, indent=False)
    para(
        "Исполнитель: автоматизированная система «Голос Омска»",
        align=WD_ALIGN_PARAGRAPH.LEFT, indent=False, space_after=2,
    )
    para(
        f"Дата составления: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        align=WD_ALIGN_PARAGRAPH.LEFT, indent=False, space_after=12,
    )
    para(
        "_____________________ /_____________________/",
        align=WD_ALIGN_PARAGRAPH.LEFT, indent=False,
    )

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": "attachment; filename=report.docx; filename*=UTF-8''%D0%90%D0%BD%D0%B0%D0%BB%D0%B8%D1%82%D0%B8%D1%87%D0%B5%D1%81%D0%BA%D0%B0%D1%8F%20%D0%B7%D0%B0%D0%BF%D0%B8%D1%81%D0%BA%D0%B0%20%E2%80%94%20%D0%93%D0%BE%D0%BB%D0%BE%D1%81%20%D0%9E%D0%BC%D1%81%D0%BA%D0%B0.docx"},
    )


# === Sheet 1: Executive Summary ===

def _build_executive_sheet(wb, run, summaries, clusters_by_muni, critical_clusters, global_sev, date_min, date_max, status):
    ws = wb.active
    ws.title = "Итоги для руководства"
    ws.sheet_view.showGridLines = False

    # Title
    ws.cell(row=1, column=1, value="ИТОГИ ДЛЯ РУКОВОДСТВА")
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
        ("КРИТИЧЕСКИХ", f"{critical:,}".replace(",", " "), "критической тяжести"),
        ("РЕШЕНО", f"{status['resolved_total']:,}".replace(",", " "), f"{status['resolution_rate']}% от всех записей"),
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
    ws.cell(row=row, column=1, value="КРИТИЧЕСКИЕ ОЧАГИ (топ-5)")
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
            severity = severity_value(c.severity)
            ws.cell(row=row, column=1, value=idx)
            ws.cell(row=row, column=2, value=c.municipality)
            ws.cell(row=row, column=3, value=_problem_name(c.cluster_name, c.category, c.description))
            ws.cell(row=row, column=4, value=c.appeal_count)
            ws.cell(row=row, column=5, value=format_severity(severity))
            quote = _compact_text(c.centroid_text or "")[:200]
            ws.cell(row=row, column=6, value=f"«{quote}»" if quote else "")
            ws.cell(row=row, column=6).font = QUOTE_FONT
            _style_data_row(ws, row, len(headers), zebra=(idx % 2 == 0))
            _color_severity_cell(ws.cell(row=row, column=5), severity)
            row += 1
    else:
        ws.cell(row=row, column=1, value="Критических очагов не выявлено")
        ws.cell(row=row, column=1).font = Font(italic=True, color="6B7280")
        row += 1

    row += 1

    # === LLM summary text ===
    ws.cell(row=row, column=1, value="Аналитическая справка")
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
    ws.merge_cells(start_row=row, start_column=1, end_row=row + 1, end_column=6)
    ws.row_dimensions[row].height = 58
    row += 3

    # Ширины колонок задаются до вставки картинок: рамка twoCellAnchor
    # рассчитывается по фактической геометрии листа.
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 26
    ws.column_dimensions["C"].width = 32
    ws.column_dimensions["D"].width = 16
    ws.column_dimensions["E"].width = 14
    ws.column_dimensions["F"].width = 48

    # === Диаграммы (PNG: отображаются в любом просмотрщике, включая OfficeSuite) ===
    ws.cell(row=row, column=1, value="Топ-10 проблемных районов")
    ws.cell(row=row, column=1).font = SECTION_FONT
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    row += 1

    top10 = summaries[:10]
    if top10:
        _add_image(
            ws,
            _bar_png(
                [s.municipality or "Не указано" for s in top10],
                [s.problem_count or 0 for s in top10],
                "Топ-10 районов по количеству проблемных обращений",
            ),
            0,
            row - 1,
        )
    row += 23

    ws.cell(row=row, column=1, value="СТРУКТУРА ОБРАЩЕНИЙ")
    ws.cell(row=row, column=1).font = SECTION_FONT
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    row += 1

    severity_rows = [
        ("Критическая", global_sev.get("CRITICAL", 0)),
        ("Высокая", global_sev.get("HIGH", 0)),
        ("Средняя", global_sev.get("MEDIUM", 0)),
        ("Низкая", global_sev.get("LOW", 0)),
    ]
    _add_image(
        ws,
        _pie_png(
            [label for label, _ in severity_rows],
            [count for _, count in severity_rows],
            ["#DC2626", "#F97316", "#EAB308", "#22C55E"],
            "Тяжесть проблемных обращений",
        ),
        0,
        row - 1,
    )
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
            severity = severity_value(cluster.severity)
            ws.cell(row=row, column=1, value=cluster.rank or idx)
            ws.cell(row=row, column=2, value=_problem_name(cluster.cluster_name, cluster.category, cluster.description))
            ws.cell(row=row, column=3, value=cluster.appeal_count)
            ws.cell(row=row, column=4, value=format_severity(severity))
            quote = _compact_text(cluster.centroid_text or "")[:400]
            ws.cell(row=row, column=5, value=f"«{quote}»" if quote else "")
            ws.cell(row=row, column=5).font = QUOTE_FONT

            _style_data_row(ws, row, len(detail_headers), zebra=(idx % 2 == 0))
            _color_severity_cell(ws.cell(row=row, column=4), severity)
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


def _build_resolved_sheet(wb, status, resolved_clusters, merged_by_muni, merged_by_category, timeline):
    ws = wb.create_sheet("Решенные проблемы")
    ws.sheet_view.showGridLines = False

    ws.cell(row=1, column=1, value="РЕШЕННЫЕ ПРОБЛЕМЫ")
    ws.cell(row=1, column=1).font = TITLE_FONT
    ws.merge_cells("A1:F1")
    ws.row_dimensions[1].height = 26

    # Ширины колонок — до вставки картинок (рамка twoCellAnchor по геометрии листа).
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 40
    ws.column_dimensions["C"].width = 22
    ws.column_dimensions["D"].width = 14
    ws.column_dimensions["E"].width = 16
    ws.column_dimensions["F"].width = 50

    # === Ключевые метрики ===
    row = 3
    ws.cell(row=row, column=1, value="Ключевые метрики").font = SECTION_FONT
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
    row += 1
    ws.cell(row=row, column=1, value="Показатель")
    ws.cell(row=row, column=2, value="Значение")
    _style_header_row(ws, row, 2)
    row += 1
    metric_rows = [
        ("Решено всего", f"{status['resolved_total']:,} ({status['resolution_rate']}% от всех записей)".replace(",", " ")),
        ("в т.ч. закрыто до анализа", f"{status['resolved_prefiltered']:,}".replace(",", " ")),
        ("Открыто (в работе)", f"{status['open']:,}".replace(",", " ")),
        ("Нерешаемые", f"{status['unsolvable']:,}".replace(",", " ")),
        ("Средний срок решения", f"{status['avg_days']} дн."),
    ]
    for idx, (label, value) in enumerate(metric_rows, 1):
        ws.cell(row=row, column=1, value=label)
        ws.cell(row=row, column=2, value=value)
        _style_data_row(ws, row, 2, zebra=(idx % 2 == 0))
        row += 1

    # === Основные решённые проблемы ===
    row += 1
    ws.cell(row=row, column=1, value="Основные решённые проблемы").font = SECTION_FONT
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    row += 1
    headers = ["Район", "Проблема", "Категория", "Закрыто", "Всего в кластере", "Описание"]
    for col, header in enumerate(headers, 1):
        ws.cell(row=row, column=col, value=header)
    _style_header_row(ws, row, len(headers))
    row += 1
    if resolved_clusters:
        for idx, cluster in enumerate(resolved_clusters, 1):
            ws.cell(row=row, column=1, value=cluster.municipality)
            ws.cell(row=row, column=2, value=_problem_name(cluster.cluster_name, cluster.category, cluster.description))
            ws.cell(row=row, column=3, value=category_value(cluster.category))
            ws.cell(row=row, column=4, value=int(cluster.resolved_count or 0))
            ws.cell(row=row, column=5, value=int(cluster.appeal_count or 0))
            ws.cell(row=row, column=6, value=_compact_text(cluster.description or "")[:220])
            _style_data_row(ws, row, len(headers), zebra=(idx % 2 == 0))
            row += 1
    else:
        ws.cell(row=row, column=1, value="В проанализированной выборке закрытых кластеров не найдено")
        ws.cell(row=row, column=1).font = Font(italic=True, color="6B7280")
        row += 1

    # === Краткие срезы: районы и категории ===
    row += 1
    ws.cell(row=row, column=1, value="Решено по районам (топ-8)").font = SECTION_FONT
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
    row += 1
    mini_headers = ["Район", "Решено", "Открыто", "Доля решённых"]
    for col, header in enumerate(mini_headers, 1):
        ws.cell(row=row, column=col, value=header)
    _style_header_row(ws, row, len(mini_headers))
    row += 1
    muni_start = row
    for idx, item in enumerate(merged_by_muni, 1):
        ws.cell(row=row, column=1, value=item["name"])
        ws.cell(row=row, column=2, value=item["resolved"])
        ws.cell(row=row, column=3, value=item["open"])
        ws.cell(row=row, column=4, value=f"{item['rate']}%")
        _style_data_row(ws, row, len(mini_headers), zebra=(idx % 2 == 0))
        row += 1
    if row - 1 >= muni_start:
        ws.conditional_formatting.add(
            f"B{muni_start}:B{row - 1}",
            ColorScaleRule(start_type="min", start_color="FFFFFF", end_type="max", end_color="D1FAE5"),
        )

    row += 1
    ws.cell(row=row, column=1, value="Решено по категориям (топ-8)").font = SECTION_FONT
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
    row += 1
    for col, header in enumerate(["Категория", "Решено", "Открыто", "Доля решённых"], 1):
        ws.cell(row=row, column=col, value=header)
    _style_header_row(ws, row, 4)
    row += 1
    for idx, item in enumerate(merged_by_category, 1):
        ws.cell(row=row, column=1, value=item["name"])
        ws.cell(row=row, column=2, value=item["resolved"])
        ws.cell(row=row, column=3, value=item["open"])
        ws.cell(row=row, column=4, value=f"{item['rate']}%")
        _style_data_row(ws, row, 4, zebra=(idx % 2 == 0))
        row += 1

    # === Положительная динамика ===
    row += 1
    ws.cell(row=row, column=1, value="Положительная динамика закрытия").font = SECTION_FONT
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    row += 1

    trend_note = ""
    counts = [count for _, count in timeline]
    if len(counts) >= 6:
        recent = sum(counts[-3:])
        previous = sum(counts[-6:-3])
        delta = round((recent - previous) / max(previous, 1) * 100)
        if delta >= 0:
            trend_note = f"За последние 3 месяца закрыто {recent:,} обращений — на {delta}% больше, чем в предыдущие 3 месяца.".replace(",", " ")
        else:
            trend_note = f"За последние 3 месяца закрыто {recent:,} обращений — на {abs(delta)}% меньше, чем в предыдущие 3 месяца.".replace(",", " ")
    if trend_note:
        ws.cell(row=row, column=1, value=trend_note)
        ws.cell(row=row, column=1).alignment = LEFT_TOP
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        row += 1

    if timeline:
        _add_image(
            ws,
            _line_png(
                [_month_label(bucket) for bucket, _ in timeline],
                [count for _, count in timeline],
                "Закрытые обращения по месяцам (с указанной датой закрытия)",
            ),
            0,
            row - 1,
        )
        row += 19

    # === Замечания к качеству данных ===
    row += 1
    ws.cell(row=row, column=1, value="Замечания к качеству данных").font = SECTION_FONT
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    row += 1

    undated = max(status["resolved_total"] - status["dated_resolved"], 0)
    undated_pct = round(undated / max(status["resolved_total"], 1) * 100, 1)
    remarks = [
        f"У {undated:,} из {status['resolved_total']:,} решённых обращений ({undated_pct}%) не указана дата закрытия — "
        "динамика закрытия построена только по датированным записям.".replace(",", " "),
    ]
    if status["bad_dates"]:
        remarks.append(
            f"{status['bad_dates']:,} записей имеют дату закрытия раньше даты создания — исключены из расчёта среднего срока.".replace(",", " ")
        )
    if status["closed_no_outcome"]:
        remarks.append(
            f"У {status['closed_no_outcome']:,} закрытых обращений в выборке не указан итог рассмотрения.".replace(",", " ")
        )
    if status["unsolvable"]:
        remarks.append(
            f"{status['unsolvable']:,} обращений отмечены как «Нерешаемые» — не учитываются в доле решённых.".replace(",", " ")
        )
    for remark in remarks:
        ws.cell(row=row, column=1, value=f"• {remark}")
        ws.cell(row=row, column=1).alignment = LEFT_TOP
        ws.cell(row=row, column=1).font = Font(size=10, color="92400E")
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        row += 1

    ws.freeze_panes = "A2"


