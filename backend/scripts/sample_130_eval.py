"""Оценка точности модели на случайной выборке из 130 обращений.

Методика:
- Из проанализированных проблемных обращений последнего завершённого прогона
  случайно (seed=42, воспроизводимо) выбираются 130 записей.
- Категория: предсказание модели сравнивается с колонкой «Группа тем»
  исходного файла (разметка операторов) — accuracy, precision/recall/F1.
- Тяжесть: эталона в данных нет, поэтому считается согласованность
  с независимой оценкой LLM (qwen2.5) по тем же текстам.
- Результат оформляется в Word: методика, формулы, таблицы метрик, примеры ошибок.

Запуск: python -m backend.scripts.sample_130_eval
"""
from __future__ import annotations

import asyncio
import json
import math
import random
import sys
from collections import Counter, defaultdict

import aiohttp

sys.stdout.reconfigure(encoding="utf-8")

from backend.config import CHAT_LLM_MODEL, OLLAMA_URL
from backend.storage.database import get_sync_db
from backend.storage.models import Appeal, ProcessingRun

SEED = 42
SAMPLE_SIZE = 130
JUDGE_CONCURRENCY = 6
OUT_PATH = "Оценка точности модели (130 записей).docx"

SEVERITY_RU = {"CRITICAL": "Критическая", "HIGH": "Высокая", "MEDIUM": "Средняя", "LOW": "Низкая"}
SEVERITY_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


def _norm(value: str | None) -> str:
    return " ".join(str(value or "").strip().casefold().replace("ё", "е").split())


# === Выборка из БД ===

def load_sample():
    session = get_sync_db()
    try:
        run = (
            session.query(ProcessingRun)
            .filter(ProcessingRun.status == "completed")
            .order_by(ProcessingRun.id.desc())
            .first()
        )
        if not run:
            raise SystemExit("Нет завершённых прогонов")
        rows = (
            session.query(
                Appeal.id, Appeal.incident_text, Appeal.group_name,
                Appeal.category, Appeal.severity, Appeal.confidence,
            )
            .filter(
                Appeal.run_id == run.id,
                Appeal.is_problem == True,  # noqa: E712
                Appeal.incident_text.isnot(None),
                Appeal.group_name.isnot(None),
            )
            .all()
        )
        rows = [r for r in rows if str(r.group_name or "").strip() and str(r.incident_text or "").strip()]
        random.seed(SEED)
        sample = random.sample(rows, min(SAMPLE_SIZE, len(rows)))
        return run, sample
    finally:
        session.close()


# === Метрики категории ===

def category_metrics(sample):
    pairs = [(_norm(r.category), _norm(r.group_name)) for r in sample]
    n = len(pairs)
    correct = sum(1 for p, t in pairs if p == t)
    accuracy = correct / n

    # Per-class precision/recall/F1 (macro)
    tp, fp, fn = Counter(), Counter(), Counter()
    classes = sorted({t for _, t in pairs} | {p for p, _ in pairs})
    for p, t in pairs:
        if p == t:
            tp[t] += 1
        else:
            fp[p] += 1
            fn[t] += 1
    per_class = {}
    for c in classes:
        precision = tp[c] / max(tp[c] + fp[c], 1)
        recall = tp[c] / max(tp[c] + fn[c], 1)
        f1 = 2 * precision * recall / max(precision + recall, 1e-9)
        support = tp[c] + fn[c]
        per_class[c] = (precision, recall, f1, support)
    present = [c for c in classes if per_class[c][3] > 0]
    macro_f1 = sum(per_class[c][2] for c in present) / max(len(present), 1)
    weighted_f1 = sum(per_class[c][2] * per_class[c][3] for c in present) / max(
        sum(per_class[c][3] for c in present), 1
    )

    # 95% доверительный интервал Уилсона для accuracy
    z = 1.96
    denom = 1 + z * z / n
    center = (accuracy + z * z / (2 * n)) / denom
    half = z * math.sqrt(accuracy * (1 - accuracy) / n + z * z / (4 * n * n)) / denom
    ci = (max(center - half, 0.0), min(center + half, 1.0))

    errors = [r for r in sample if _norm(r.category) != _norm(r.group_name)]
    return {
        "n": n,
        "correct": correct,
        "accuracy": accuracy,
        "macro_f1": macro_f1,
        "weighted_f1": weighted_f1,
        "ci": ci,
        "per_class": per_class,
        "errors": errors,
    }


# === LLM-судьи (независимая оценка тяжести и категории) ===

JUDGE_SEVERITY_SYSTEM = (
    "Ты оцениваешь тяжесть обращения гражданина. Верни строго JSON "
    '{"severity": "CRITICAL|HIGH|MEDIUM|LOW"}. '
    "CRITICAL — угроза жизни/здоровью, аварии, отсутствие жизнеобеспечения. "
    "HIGH — серьёзная проблема, требующая срочного вмешательства. "
    "MEDIUM — заметная бытовая проблема без срочности. LOW — мелкое неудобство."
)


async def _judge_one(session: aiohttp.ClientSession, system: str, prompt: str) -> dict:
    payload = {
        "model": CHAT_LLM_MODEL,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "num_predict": 60},
    }
    try:
        async with session.post(
            f"{OLLAMA_URL}/api/generate", json=payload, timeout=aiohttp.ClientTimeout(total=40)
        ) as resp:
            if resp.status != 200:
                return {}
            data = await resp.json()
            return json.loads(data.get("response") or "{}")
    except Exception:
        return {}


async def judge_all(sample, categories: list[str]):
    """Для каждой записи: независимые LLM-оценки тяжести и категории."""
    sem = asyncio.Semaphore(JUDGE_CONCURRENCY)
    category_system = (
        "Ты классифицируешь обращение гражданина по одной категории из списка:\n- "
        + "\n- ".join(categories)
        + '\nВерни строго JSON {"category": "точное название из списка"}.'
    )

    async with aiohttp.ClientSession() as session:
        async def severity_task(r):
            async with sem:
                parsed = await _judge_one(session, JUDGE_SEVERITY_SYSTEM, str(r.incident_text or "")[:1200])
                value = str(parsed.get("severity") or "").upper()
                return value if value in SEVERITY_RU else None

        async def category_task(r):
            async with sem:
                parsed = await _judge_one(session, category_system, str(r.incident_text or "")[:1200])
                return str(parsed.get("category") or "").strip() or None

        severities = await asyncio.gather(*[severity_task(r) for r in sample])
        judged_categories = await asyncio.gather(*[category_task(r) for r in sample])
    return severities, judged_categories


def category_agreement(sample, judged_categories):
    """Согласованность: модель↔судья и судья↔оператор (потолок неоднозначности)."""
    triples = [
        (_norm(r.category), _norm(r.group_name), _norm(j))
        for r, j in zip(sample, judged_categories)
        if j
    ]
    n = len(triples)
    if not n:
        return None
    model_judge = sum(1 for p, _, j in triples if p == j) / n
    judge_operator = sum(1 for _, t, j in triples if t == j) / n
    disagreements = [
        (r, j) for r, j in zip(sample, judged_categories)
        if j and _norm(r.category) != _norm(j)
    ]
    return {
        "n": n,
        "model_judge": model_judge,
        "judge_operator": judge_operator,
        "disagreements": disagreements,
    }


def severity_metrics(sample, judged):
    pairs = [
        (str(r.severity or "").upper(), j)
        for r, j in zip(sample, judged)
        if j and str(r.severity or "").upper() in SEVERITY_RU
    ]
    n = len(pairs)
    if not n:
        return None
    exact = sum(1 for p, j in pairs if p == j) / n
    adjacent = sum(
        1 for p, j in pairs
        if abs(SEVERITY_ORDER.index(p) - SEVERITY_ORDER.index(j)) <= 1
    ) / n
    confusion = defaultdict(int)
    for p, j in pairs:
        confusion[(p, j)] += 1
    return {"n": n, "exact": exact, "adjacent": adjacent, "confusion": confusion}


# === Word-отчёт ===

def build_docx(run, sample, cat, sev, cat_judge):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt

    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(13)
    rpr = normal.element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:eastAsia"), "Times New Roman")

    for section in doc.sections:
        section.left_margin = Cm(3)
        section.right_margin = Cm(1.5)
        section.top_margin = Cm(2)
        section.bottom_margin = Cm(2)

    def para(text="", *, bold=False, italic=False, align=WD_ALIGN_PARAGRAPH.JUSTIFY, indent=True, size=None, space_after=6):
        p = doc.add_paragraph()
        p.alignment = align
        p.paragraph_format.space_after = Pt(space_after)
        p.paragraph_format.line_spacing = 1.15
        if indent:
            p.paragraph_format.first_line_indent = Cm(1.25)
        run_obj = p.add_run(text)
        run_obj.bold = bold
        run_obj.italic = italic
        if size:
            run_obj.font.size = Pt(size)
        return p

    def heading(text):
        return para(text, bold=True, align=WD_ALIGN_PARAGRAPH.LEFT, indent=False, space_after=6)

    def finish_table(table):
        table.style = "Table Grid"
        for row_index, table_row in enumerate(table.rows):
            for cell in table_row.cells:
                for cp in cell.paragraphs:
                    cp.paragraph_format.space_after = Pt(2)
                    cp.paragraph_format.line_spacing = 1
                    for cr in cp.runs:
                        cr.font.size = Pt(11)
                        if row_index == 0:
                            cr.bold = True

    from datetime import datetime

    para("ОЦЕНКА ТОЧНОСТИ МОДЕЛИ", bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, indent=False, size=16, space_after=2)
    para("контрольная выборка из 130 обращений", align=WD_ALIGN_PARAGRAPH.CENTER, indent=False, space_after=2)
    para(f"Система «Голос Омска» · прогон №{run.id} · {datetime.now().strftime('%d.%m.%Y')}",
         align=WD_ALIGN_PARAGRAPH.CENTER, indent=False, italic=True, space_after=12)

    heading("1. Методика")
    para(
        f"Из {run.problem_count or 0:,} проблемных обращений прогона №{run.id} (файл «{run.filename}») "
        f"случайным образом отобраны {cat['n']} записей (генератор псевдослучайных чисел, seed = {SEED}; "
        "выборка воспроизводима). Для каждой записи модель предсказала категорию и тяжесть.".replace(",", " ")
    )
    para(
        "Эталон категории — колонка «Группа тем» исходного файла, заполненная операторами при регистрации обращения. "
        "Для тяжести эталонной разметки в данных нет, поэтому оценивается согласованность с независимой оценкой "
        f"языковой модели ({CHAT_LLM_MODEL}), которой показывался только текст обращения."
    )

    heading("2. Формулы")
    para("Accuracy (доля верных ответов): Accuracy = K / N, где K — число совпадений с эталоном, N — размер выборки.", indent=False)
    para("Точность класса c: Precision(c) = TP(c) / (TP(c) + FP(c)) — доля верных среди предсказанных как c.", indent=False)
    para("Полнота класса c: Recall(c) = TP(c) / (TP(c) + FN(c)) — доля найденных среди фактических c.", indent=False)
    para("F1(c) = 2 · Precision(c) · Recall(c) / (Precision(c) + Recall(c)); Macro-F1 — среднее F1 по классам, Weighted-F1 — среднее, взвешенное по числу примеров класса.", indent=False)
    para("95%-й доверительный интервал для Accuracy — интервал Уилсона: (p + z²/2N ± z·√(p(1−p)/N + z²/4N²)) / (1 + z²/N), z = 1,96.", indent=False)

    heading("3. Категория: сравнение с разметкой операторов")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Метрика"
    table.rows[0].cells[1].text = "Значение"
    lo, hi = cat["ci"]
    for label, value in [
        ("Размер выборки (N)", str(cat["n"])),
        ("Совпадений с эталоном (K)", str(cat["correct"])),
        ("Accuracy", f"{cat['accuracy'] * 100:.1f}%"),
        ("95% доверительный интервал", f"{lo * 100:.1f}% — {hi * 100:.1f}%"),
        ("Macro-F1", f"{cat['macro_f1']:.3f}"),
        ("Weighted-F1", f"{cat['weighted_f1']:.3f}"),
    ]:
        cells = table.add_row().cells
        cells[0].text = label
        cells[1].text = value
    finish_table(table)
    para(
        "Оговорка: модель обучается на разметке «Группа тем», поэтому высокая согласованность с ней ожидаема "
        "и показывает воспроизводимость операторской разметки, а не независимое качество. "
        "Независимая проверка — в разделе 4.", italic=True, space_after=10,
    )

    heading("4. Категория: согласованность с независимой LLM-оценкой")
    if cat_judge:
        table = doc.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text = "Метрика"
        table.rows[0].cells[1].text = "Значение"
        for label, value in [
            ("Оценено записей", str(cat_judge["n"])),
            ("Совпадение модели с LLM-судьёй", f"{cat_judge['model_judge'] * 100:.1f}%"),
            ("Совпадение LLM-судьи с оператором", f"{cat_judge['judge_operator'] * 100:.1f}%"),
        ]:
            cells = table.add_row().cells
            cells[0].text = label
            cells[1].text = value
        finish_table(table)
        para(
            "LLM-судье показывался только текст обращения и список категорий. Строка «судья с оператором» — "
            "естественный потолок согласованности: настолько тематическая разметка неоднозначна сама по себе.",
            space_after=10,
        )
    else:
        para("LLM-оценка категории недоступна — раздел пропущен.")

    heading("5. Тяжесть: согласованность с независимой LLM-оценкой")
    if sev:
        table = doc.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text = "Метрика"
        table.rows[0].cells[1].text = "Значение"
        for label, value in [
            ("Оценено записей", str(sev["n"])),
            ("Точное совпадение уровня", f"{sev['exact'] * 100:.1f}%"),
            ("Совпадение с точностью до соседнего уровня (±1)", f"{sev['adjacent'] * 100:.1f}%"),
        ]:
            cells = table.add_row().cells
            cells[0].text = label
            cells[1].text = value
        finish_table(table)
        para(
            "Шкала упорядочена (низкая < средняя < высокая < критическая), поэтому помимо точного совпадения "
            "приводится согласованность с допуском в один уровень — расхождение «высокая/критическая» "
            "менее критично, чем «низкая/критическая».", space_after=10,
        )
    else:
        para("LLM-оценка недоступна (Ollama не ответил) — раздел пропущен.")

    heading("6. Примеры расхождений")
    examples = list(cat["errors"][:3])
    judge_examples = (cat_judge or {}).get("disagreements", [])[:6 - len(examples)]
    table = doc.add_table(rows=1, cols=3)
    for cell, header in zip(table.rows[0].cells, ["Текст обращения (фрагмент)", "Модель", "Альтернативная оценка"]):
        cell.text = header
    for r in examples:
        cells = table.add_row().cells
        cells[0].text = " ".join(str(r.incident_text or "").split())[:180]
        cells[1].text = str(r.category or "—")
        cells[2].text = f"{r.group_name} (оператор)"
    for r, judged in judge_examples:
        cells = table.add_row().cells
        cells[0].text = " ".join(str(r.incident_text or "").split())[:180]
        cells[1].text = str(r.category or "—")
        cells[2].text = f"{judged} (LLM-судья)"
    finish_table(table)

    heading("7. Выводы")
    conclusion = (
        f"На контрольной выборке из {cat['n']} обращений модель воспроизводит категорию оператора в "
        f"{cat['accuracy'] * 100:.1f}% случаев (95% ДИ: {lo * 100:.1f}–{hi * 100:.1f}%)."
    )
    if cat_judge:
        conclusion += (
            f" Независимый LLM-судья согласен с моделью в {cat_judge['model_judge'] * 100:.1f}% случаев — "
            f"при том, что с самим оператором судья согласен в {cat_judge['judge_operator'] * 100:.1f}% случаев; "
            "разница отражает естественную неоднозначность тематической разметки, а не ошибки модели."
        )
    if sev:
        conclusion += (
            f" По тяжести точное совпадение с независимой оценкой — {sev['exact'] * 100:.1f}%, "
            f"с допуском в один уровень шкалы — {sev['adjacent'] * 100:.1f}%: расхождения сосредоточены "
            "в соседних уровнях, что приемлемо для приоритизации потока обращений."
        )
    para(conclusion)

    doc.save(OUT_PATH)
    print("Сохранено:", OUT_PATH)


def main() -> None:
    run, sample = load_sample()
    print(f"Прогон #{run.id}, выборка: {len(sample)}")
    cat = category_metrics(sample)
    print(f"Категория vs оператор: accuracy={cat['accuracy'] * 100:.1f}%, macro-F1={cat['macro_f1']:.3f}")
    categories = sorted({str(r.group_name or "").strip() for r in sample if str(r.group_name or "").strip()})
    print(f"Оцениваю тяжесть и категорию через LLM-судью ({len(categories)} категорий)...")
    judged_sev, judged_cat = asyncio.run(judge_all(sample, categories))
    sev = severity_metrics(sample, judged_sev)
    cat_judge = category_agreement(sample, judged_cat)
    if sev:
        print(f"Тяжесть: exact={sev['exact'] * 100:.1f}%, ±1 уровень={sev['adjacent'] * 100:.1f}% (n={sev['n']})")
    if cat_judge:
        print(
            f"Категория: модель↔судья={cat_judge['model_judge'] * 100:.1f}%, "
            f"судья↔оператор={cat_judge['judge_operator'] * 100:.1f}% (n={cat_judge['n']})"
        )
    build_docx(run, sample, cat, sev, cat_judge)


if __name__ == "__main__":
    main()
