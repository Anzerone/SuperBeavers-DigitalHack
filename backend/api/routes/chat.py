"""Fast chat endpoint: deterministic analytics + LLM narration + memory."""
import io
import json
import re
from typing import Any

import aiohttp
import openpyxl
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import CATEGORIES, LLM_MODEL, LLM_TIMEOUT_SECONDS, OLLAMA_URL
from backend.labels import format_severity
from backend.storage.database import get_db
from backend.storage.models import Appeal

router = APIRouter()

# Per-run conversation memory: history + sticky context (last muni/category)
_conversations: dict[int, dict] = {}


def _ctx(run_id: int) -> dict:
    """Get-or-create conversation context for a run."""
    if run_id not in _conversations:
        _conversations[run_id] = {
            "history": [],
            "last_municipality": None,
            "last_category": None,
            "last_severity": None,
        }
    return _conversations[run_id]


SYSTEM_PROMPT = """Ты - аналитик обращений граждан Омской области. У тебя есть БД PostgreSQL.

Таблицы:
- problem_clusters: id, run_id, municipality, cluster_name, description, category, severity, appeal_count, rank, rank_score, topic_diversity, centroid_text
- summaries: id, run_id, municipality, rank, problem_count, avg_rank, top_issues (jsonb), summary_text, centroid_excerpt
- appeals: id, run_id, municipality, group_name, incident_type, outcome, incident_text, is_problem, severity, category, confidence

Текущий run_id = {run_id}.

Правила:
1. Сгенерируй ровно один SELECT-запрос. Без markdown, без точки с запятой.
2. Всегда фильтруй по run_id = {run_id}.
3. Для рейтинга/топа районов — таблица summaries, сортировка по rank ASC.
4. Для конкретного района — problem_clusters с municipality = ... и сортировкой по rank.
5. Для общего числа обращений по категории — appeals с is_problem = true.
6. Добавляй LIMIT 50 или меньше.
7. Категория = одно из значений колонки "Группа тем" датасета (например 'ЖКХ', 'Дороги', 'Здравоохранение')."""

NARRATE_SYSTEM = """Ты — аналитик обращений граждан. Из таблицы данных и вопроса пользователя составь короткий, естественный, дружелюбный ответ на русском.
Правила:
- 2–4 предложения максимум.
- Назови ключевые цифры словами (не списком). Округляй большие числа.
- Если данных мало или они шумные — честно скажи об этом.
- Не повторяй вопрос. Не используй markdown."""

RAG_NARRATE_SYSTEM = """Ты — аналитик обращений граждан. У тебя есть данные + конкретные цитаты обращений (пронумерованы [1], [2], ...).
Сформулируй естественный ответ на русском (3–6 предложений):
- Опирайся на цифры из данных
- Где уместно — приводи доводы со ссылками [1], [2], [3] на цитаты
- Цитаты не вставляй целиком — только ссылайся номером
- Не используй markdown, только обычный текст"""

FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|copy|grant|revoke|vacuum|analyze|call|execute)\b",
    re.IGNORECASE,
)

ALIASES = {
    "ЖКХ": ["жкх", "коммун", "отоплен", "отопл", "водоснаб", "воды", "канализац", "трубы", "горячая вода", "холодная вода"],
    "Дороги": ["дорог", "ям", "асфальт", "тротуар", "разметк"],
    "Образование": ["школ", "детск сад", "детсад", "образован", "учитель", "учеб"],
    "Физическая культура и спорт": ["спорт", "физкультур", "стадион", "тренаж", "секци"],
    "Здравоохранение": ["медицин", "больниц", "поликлиник", "врач", "здравоохран", "лечен", "скорая", "лекарств"],
    "Благоустройство": ["благоустр", "двор", "снег", "парк ", "сквер", "уборк", "газон", "детск площадк"],
    "Общественный транспорт": ["автобус", "маршрут", "транспорт", "остановк", "троллейбус", "трамвай", "перевозк"],
    "Социальное обслуживание и защита": ["социальн", "соцзащит", "пенси", "пособ", "льгот", "инвалид", "ветеран"],
    "Военная служба": ["военн", "армия", "мобилиз", "сво", "контракт"],
    "Безопасность и правопорядок": ["безопасн", "полиц", "правопоряд", "правонаруш", "хулиган", "престу"],
    "Энергетика": ["электр", "энерг", "свет ", "освещен", "фонар", "лэп"],
    "Экология": ["эколог", "загрязн", "выброс", "воздух", "река"],
    "Обращение с отходами": ["мусор", "отход", "свалк", "контейнер", "тко"],
    "Связь и телевидение": ["связь", "телевиден", "интернет", "мобильн", "сигнал", "вышк"],
    "Строительство и архитектура": ["строит", "стройк", "архитект", "застр", "новостройк"],
    "Культура": ["культур", "музей", "театр", "библиотек", "дом культур"],
    "Земельные отношения": ["земельн", "участок", "земля", "межеван", "кадастр"],
    "Торговля и услуги": ["торгов", "магазин", "услуг", "рынок", "цены"],
    "Трудовые отношения": ["трудов", "зарплат", "работодател", "увольнен"],
    "Миграционная политика": ["миграц", "мигрант", "паспорт", "гражданств"],
    "Туризм": ["туризм", "турист"],
    "Молодежная политика": ["молодеж"],
    "Имущественные отношения": ["имуществ", "собственн", "жильё", "жилье", "квартир"],
    "Регистрация актов гражд. состояния": ["загс", "брак", "развод", "свидетельств о рожд"],
    "Ветеринария": ["ветеринар", "животн", "собак", "кошк", "бродяч"],
    "Другое": ["другое", "прочее"],
}


async def _call_ollama(prompt: str, system: str = "", timeout: int = 45, temperature: float = 0.1) -> str:
    request_timeout = min(max(timeout, 10), LLM_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession() as session:
        payload = {
            "model": LLM_MODEL,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": 500},
        }
        async with session.post(
            f"{OLLAMA_URL}/api/generate",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=request_timeout),
        ) as resp:
            if resp.status != 200:
                raise HTTPException(500, f"Ollama error: {resp.status}")
            data = await resp.json()
            return data.get("response", "").strip()


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower().replace("ё", "е")).strip()


def _detect_limit(message: str) -> int:
    norm = _normalize(message)
    match = re.search(r"(?:топ|top)\D{0,3}(\d{1,2})", norm)
    if match:
        return max(1, min(int(match.group(1)), 50))
    return 10


def _detect_categories_all(message: str) -> list[str]:
    """Detect ALL categories mentioned (для сравнения)."""
    norm = _normalize(message)
    found = []
    for category in CATEGORIES:
        terms = ALIASES.get(category, [_normalize(category)])
        if any(term in norm for term in terms) and category not in found:
            found.append(category)
    return found


def _detect_category(message: str) -> str | None:
    found = _detect_categories_all(message)
    return found[0] if found else None


def _detect_severity(message: str) -> str | None:
    norm = _normalize(message)
    if any(term in norm for term in ["критич", "critical", "аварийн"]):
        return "CRITICAL"
    if any(term in norm for term in ["высок", "high"]):
        return "HIGH"
    if any(term in norm for term in ["средн", "medium"]):
        return "MEDIUM"
    if any(term in norm for term in ["низк", "low"]):
        return "LOW"
    return None


def _has_follow_up_marker(message: str) -> bool:
    """'А что в...', 'А там', 'А по этому', 'и там' — пользователь имеет в виду что-то из контекста."""
    norm = _normalize(message)
    return bool(re.search(r"\b(а\s+(что|как|там|по|где|сколько|у)|и\s+(там|по)|еще|ещё|подробне|расскажи больше|развернут)\b", norm))


async def _known_municipalities(db: AsyncSession, run_id: int) -> list[str]:
    result = await db.execute(
        text("""
            SELECT DISTINCT municipality
            FROM appeals
            WHERE run_id = :run_id AND municipality IS NOT NULL AND municipality <> ''
        """),
        {"run_id": run_id},
    )
    return [row[0] for row in result.fetchall() if row[0]]


async def _detect_municipalities_all(db: AsyncSession, run_id: int, message: str) -> list[str]:
    """Detect ALL municipalities mentioned, для сравнения."""
    norm = _normalize(message)
    found = []
    for municipality in await _known_municipalities(db, run_id):
        muni_norm = _normalize(municipality)
        short_norm = re.sub(r"\b(муниципальный|район|округ|городской|г\.?)\b", "", muni_norm).strip()
        if muni_norm and muni_norm in norm and municipality not in found:
            found.append(municipality)
        elif short_norm and len(short_norm) >= 4 and short_norm in norm and municipality not in found:
            found.append(municipality)
    return found


async def _detect_municipality(db: AsyncSession, run_id: int, message: str) -> str | None:
    found = await _detect_municipalities_all(db, run_id, message)
    return found[0] if found else None


def _append_filters(where: list[str], params: dict[str, Any], category: str | None, severity: str | None):
    if category:
        where.append("category = :category")
        params["category"] = category
    if severity:
        where.append("severity = :severity")
        params["severity"] = severity


async def _fast_intent(message: str, run_id: int, db: AsyncSession) -> tuple[str, dict[str, Any], str] | None:
    norm = _normalize(message)
    ctx = _ctx(run_id)
    limit = _detect_limit(message)
    category = _detect_category(message)
    severity = _detect_severity(message)
    municipality = await _detect_municipality(db, run_id, message)
    all_categories = _detect_categories_all(message)
    all_munis = await _detect_municipalities_all(db, run_id, message)

    # Follow-up resolution: use sticky context
    if _has_follow_up_marker(message) or (not municipality and not category and not severity):
        if not municipality and ctx.get("last_municipality"):
            municipality = ctx["last_municipality"]
        if not category and ctx.get("last_category"):
            category = ctx["last_category"]

    # Update sticky context
    if municipality:
        ctx["last_municipality"] = municipality
    if category:
        ctx["last_category"] = category
    if severity:
        ctx["last_severity"] = severity

    # === COMPARISON: две категории или два района ===
    if len(all_categories) >= 2:
        return (
            """
            SELECT category, COUNT(*) AS problem_count,
                   COUNT(*) FILTER (WHERE severity IN ('CRITICAL','HIGH')) AS severe_count,
                   COUNT(DISTINCT municipality) AS muni_count
            FROM appeals
            WHERE run_id = :run_id AND is_problem = true AND category = ANY(:cats)
            GROUP BY category
            """,
            {"run_id": run_id, "cats": all_categories[:5]},
            "category_compare",
        )

    if len(all_munis) >= 2:
        return (
            """
            SELECT municipality, problem_count, ROUND(avg_rank::numeric, 1) AS avg_rank, top_issues
            FROM summaries
            WHERE run_id = :run_id AND municipality = ANY(:munis)
            """,
            {"run_id": run_id, "munis": all_munis[:5]},
            "muni_compare",
        )

    # === SEVERITY BREAKDOWN by district/category ===
    asks_severity_breakdown = "тяжест" in norm or "критич" in norm and ("где" in norm or "район" in norm)
    if asks_severity_breakdown and not severity:
        where = ["run_id = :run_id", "is_problem = true", "severity IS NOT NULL"]
        params: dict[str, Any] = {"run_id": run_id, "limit": limit}
        _append_filters(where, params, category, None)
        return (
            f"""
            SELECT municipality, severity, COUNT(*) AS count
            FROM appeals
            WHERE {' AND '.join(where)}
            GROUP BY municipality, severity
            ORDER BY count DESC
            LIMIT :limit
            """,
            params,
            "severity_by_muni",
        )

    # === COUNT ===
    asks_count = any(term in norm for term in ["сколько", "количество", "число "])
    if asks_count and (category or severity) and not municipality:
        where = ["run_id = :run_id", "is_problem = true"]
        params = {"run_id": run_id}
        _append_filters(where, params, category, severity)
        return (
            f"""
            SELECT COUNT(*) AS problem_count
            FROM appeals
            WHERE {' AND '.join(where)}
            LIMIT 1
            """,
            params,
            "count",
        )

    # === TOP DISTRICTS ===
    asks_top_districts = any(term in norm for term in ["топ", "рейтинг", "самые проблем", "лидер", "хуже всего"]) and any(term in norm for term in ["район", "муниципалит", "област"])
    if asks_top_districts:
        return (
            """
            SELECT municipality, problem_count, ROUND(avg_rank::numeric, 1) AS avg_rank, top_issues, summary_text
            FROM summaries
            WHERE run_id = :run_id
            ORDER BY rank
            LIMIT :limit
            """,
            {"run_id": run_id, "limit": limit},
            "district_top",
        )

    # === MUNICIPALITY PROBLEMS ===
    if municipality:
        where = ["run_id = :run_id", "municipality = :municipality"]
        params = {"run_id": run_id, "municipality": municipality, "limit": 50}
        _append_filters(where, params, category, severity)
        return (
            f"""
            SELECT cluster_name, appeal_count, rank, category, severity, centroid_text
            FROM problem_clusters
            WHERE {' AND '.join(where)}
            ORDER BY rank
            LIMIT :limit
            """,
            params,
            "municipality_problems",
        )

    # === CATEGORY SUMMARY across districts ===
    if category and any(term in norm for term in ["сводк", "район", "где", "топ"]):
        where = ["run_id = :run_id"]
        params = {"run_id": run_id, "limit": limit}
        _append_filters(where, params, category, severity)
        return (
            f"""
            SELECT municipality, SUM(appeal_count) AS problem_count, COUNT(*) AS cluster_count
            FROM problem_clusters
            WHERE {' AND '.join(where)}
            GROUP BY municipality
            ORDER BY problem_count DESC
            LIMIT :limit
            """,
            params,
            "category_summary",
        )

    if category or severity:
        where = ["run_id = :run_id"]
        params = {"run_id": run_id, "limit": 50}
        _append_filters(where, params, category, severity)
        return (
            f"""
            SELECT municipality, cluster_name, appeal_count, rank, category, severity, centroid_text
            FROM problem_clusters
            WHERE {' AND '.join(where)}
            ORDER BY appeal_count DESC
            LIMIT :limit
            """,
            params,
            "cluster_list",
        )

    if any(term in norm for term in ["главн", "ключев", "проблем"]):
        return (
            """
            SELECT municipality, cluster_name, appeal_count, rank, category, severity, centroid_text
            FROM problem_clusters
            WHERE run_id = :run_id
            ORDER BY appeal_count DESC
            LIMIT :limit
            """,
            {"run_id": run_id, "limit": limit},
            "cluster_list",
        )

    return None


def _extract_sql(text_resp: str) -> str | None:
    text_resp = re.sub(r"```sql\s*", "", text_resp, flags=re.IGNORECASE)
    text_resp = re.sub(r"```\s*", "", text_resp)
    match = re.search(r"(SELECT\s.+?)(?:;|$)", text_resp.strip(), re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    sql = match.group(1).strip().rstrip(";")
    if not re.match(r"^\s*SELECT\s", sql, re.IGNORECASE):
        return None
    if ";" in sql or FORBIDDEN_SQL.search(sql):
        return None
    if "LIMIT" not in sql.upper():
        sql += " LIMIT 50"
    return sql


def _validate_llm_sql(sql: str, run_id: int) -> bool:
    normalized = _normalize(sql)
    if FORBIDDEN_SQL.search(sql) or ";" in sql:
        return False
    if not normalized.startswith("select "):
        return False
    if " run_id" not in normalized and ".run_id" not in normalized:
        return False
    if str(run_id) not in normalized:
        return False
    return True


async def _run_sql(db: AsyncSession, sql: str, params: dict[str, Any] | None = None) -> tuple[list[str], list[dict]]:
    await db.execute(text("SET LOCAL statement_timeout = 5000"))
    result = await db.execute(text(sql), params or {})
    rows = result.fetchall()
    columns = list(result.keys())
    data = [dict(row._mapping) for row in rows]
    return columns, data


def _json_safe(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _serialize_data(data: list[dict]) -> list[dict]:
    return [{key: _json_safe(value) for key, value in row.items()} for row in data]


def _display_value(key: str, value: Any) -> Any:
    if key == "severity":
        return format_severity(value)
    if isinstance(value, str) and value in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
        return format_severity(value)
    return value


def _display_data(data: list[dict]) -> list[dict]:
    return [
        {key: _display_value(key, value) for key, value in row.items()}
        for row in data
    ]


def _format_top_issues(top_issues: Any) -> str:
    if isinstance(top_issues, str):
        try:
            top_issues = json.loads(top_issues)
        except Exception:
            return top_issues
    if not isinstance(top_issues, list):
        return ""
    parts = []
    for issue in top_issues[:3]:
        if not isinstance(issue, dict):
            continue
        parts.append(f"{issue.get('name', '')} ({issue.get('count', 0)})")
    return ", ".join(parts)


def _make_answer(kind: str, data: list[dict], message: str) -> str:
    if not data:
        return "По этому запросу ничего не найдено."

    if kind == "count":
        row = data[0]
        count = row.get("problem_count", 0)
        return f"Найдено {count} обращений по запросу."

    if kind == "category_compare":
        lines = ["Сравнение категорий:"]
        for row in data:
            lines.append(
                f"• {row.get('category')}: {row.get('problem_count')} проблем, "
                f"из них {row.get('severe_count')} крит./высок., "
                f"затронуто {row.get('muni_count')} муниципалитетов"
            )
        return "\n".join(lines)

    if kind == "muni_compare":
        lines = ["Сравнение районов:"]
        for row in data:
            issues = _format_top_issues(row.get("top_issues"))
            lines.append(
                f"• {row.get('municipality')}: {row.get('problem_count')} обращений, "
                f"средний ранг {row.get('avg_rank')}. Ключевые: {issues}"
            )
        return "\n".join(lines)

    if kind == "severity_by_muni":
        lines = ["Разбивка по тяжести:"]
        for row in data:
            lines.append(f"• {row.get('municipality')} — {format_severity(row.get('severity'))}: {row.get('count')}")
        return "\n".join(lines)

    if kind == "district_top":
        lines = ["Топ районов по проблемным обращениям:"]
        for idx, row in enumerate(data, 1):
            issues = _format_top_issues(row.get("top_issues"))
            avg_rank = row.get("avg_rank")
            suffix = f", средний ранг {avg_rank}" if avg_rank is not None else ""
            issue_part = f". Ключевые проблемы: {issues}" if issues else ""
            lines.append(f"{idx}. {row.get('municipality')}: {row.get('problem_count')} обращений{suffix}{issue_part}")
        return "\n".join(lines)

    if kind == "category_summary":
        lines = ["Сводка по категории:"]
        for idx, row in enumerate(data, 1):
            lines.append(f"{idx}. {row.get('municipality')}: {row.get('problem_count')} обращений, {row.get('cluster_count')} кластеров")
        return "\n".join(lines)

    lines = ["Найденные проблемы:"]
    for idx, row in enumerate(data[:10], 1):
        muni = row.get("municipality")
        muni_prefix = f"{muni}: " if muni else ""
        name = row.get("cluster_name", "Проблема")
        count = row.get("appeal_count")
        rank = row.get("rank")
        category = row.get("category")
        severity = format_severity(row.get("severity")) if row.get("severity") else None
        excerpt = " ".join(str(row.get("centroid_text") or "").split())[:180]
        meta = ", ".join(str(item) for item in [category, severity, f"ранг {rank}" if rank else None] if item)
        lines.append(f"{idx}. {muni_prefix}{name} - {count} обращений ({meta}).")
        if excerpt:
            lines.append(f"   Выдержка: {excerpt}")
    if len(data) > 10:
        lines.append(f"Показано 10 из {len(data)} строк. Полную таблицу можно скачать в XLSX.")
    return "\n".join(lines)


async def _fetch_citations(db: AsyncSession, run_id: int, data: list[dict], limit: int = 3) -> list[dict]:
    """Подтянуть 3 конкретных обращения для цитирования по результатам запроса.

    Стратегия: если в результатах есть municipality+category — берём оттуда
    реальные тексты из appeals. Если есть cluster — выгружаем appeals кластера.
    """
    if not data:
        return []

    munis = list({row.get("municipality") for row in data if row.get("municipality")})[:3]
    cats = list({row.get("category") for row in data if row.get("category")})[:3]

    if not munis and not cats:
        return []

    conds = [Appeal.run_id == run_id, Appeal.is_problem == True]
    if munis:
        conds.append(Appeal.municipality.in_(munis))
    if cats:
        conds.append(Appeal.category.in_(cats))

    res = await db.execute(
        select(Appeal)
        .where(and_(*conds))
        .order_by(Appeal.confidence.desc().nullslast())
        .limit(limit)
    )
    appeals = res.scalars().all()
    return [
        {
            "id": a.id,
            "text": (a.incident_text or "")[:400],
            "municipality": a.municipality,
            "category": a.category,
            "severity": a.severity,
        }
        for a in appeals
    ]


async def _narrate_with_rag(
    message: str,
    kind: str,
    data: list[dict],
    citations: list[dict],
) -> str | None:
    """RAG-narration: LLM получает данные + цитаты, отвечает со ссылками [1][2]."""
    if not data or not citations:
        return None
    compact_data = [{k: (v[:120] if isinstance(v, str) else v) for k, v in row.items()} for row in data[:6]]
    cites_str = "\n".join(
        f"[{i+1}] {c['municipality']} · {c['category']}: {c['text'][:300]}"
        for i, c in enumerate(citations)
    )
    prompt = f"""Вопрос: {message}

Тип запроса: {kind}

Агрегированные данные (JSON):
{json.dumps(compact_data, ensure_ascii=False, default=str)[:1400]}

Конкретные обращения для цитирования:
{cites_str}

Сформулируй ответ:"""
    try:
        text_resp = await _call_ollama(prompt, RAG_NARRATE_SYSTEM, timeout=25, temperature=0.3)
        if not text_resp or len(text_resp) > 2000:
            return None
        return text_resp.strip()
    except Exception:
        return None


async def _narrate(message: str, kind: str, data: list[dict]) -> str | None:
    """LLM-генерирует человеческий ответ по данным. Fallback на табличный _make_answer."""
    if not data:
        return None
    # Сжимаем данные для промпта: до 8 строк, обрезаем длинные тексты
    compact = []
    for row in data[:8]:
        compact_row = {}
        for k, v in row.items():
            if isinstance(v, str):
                compact_row[k] = v[:150]
            else:
                compact_row[k] = v
        compact.append(compact_row)

    prompt = f"""Вопрос пользователя: {message}

Тип ответа: {kind}
Данные (JSON, до 8 строк):
{json.dumps(compact, ensure_ascii=False, default=str)[:2000]}

Сформулируй короткий, естественный ответ:"""
    try:
        text_resp = await _call_ollama(prompt, NARRATE_SYSTEM, timeout=20, temperature=0.3)
        # Защита от слишком длинного или пустого ответа
        if not text_resp or len(text_resp) > 1500:
            return None
        return text_resp.strip()
    except Exception:
        return None


def _suggestions(kind: str, data: list[dict], ctx: dict) -> list[str]:
    """Предложить 2-3 follow-up вопроса."""
    out = []
    if kind == "district_top" and data:
        top1 = data[0].get("municipality")
        if top1:
            out.append(f"Расскажи подробнее про {top1}")
        out.append("Какие категории чаще всего жалуются?")
    elif kind == "municipality_problems":
        if ctx.get("last_municipality"):
            out.append(f"Сравни {ctx['last_municipality']} с Омском")
        out.append("Покажи только критические проблемы")
    elif kind == "category_summary" and data:
        out.append("Покажи топ районов")
        if ctx.get("last_category"):
            out.append(f"Какая тяжесть у {ctx['last_category']}?")
    elif kind == "count":
        out.append("Покажи по районам")
        out.append("Какие критические?")
    elif kind in ("cluster_list", "category_compare"):
        out.append("Покажи топ-10 районов")
        out.append("Какие проблемы у этих кластеров?")
    else:
        out = ["Топ-10 районов", "Критические проблемы ЖКХ", "Сводка по дорогам"]
    return out[:3]


@router.post("/chat")
async def chat(run_id: int, message: str, db: AsyncSession = Depends(get_db)):
    """Chat with processed data: fast intent → LLM narration → suggestions."""
    ctx = _ctx(run_id)
    fast = await _fast_intent(message, run_id, db)
    if fast:
        sql, params, kind = fast
        try:
            columns, data = await _run_sql(db, sql, params)
        except Exception as exc:
            raise HTTPException(500, f"Ошибка при выполнении запроса: {str(exc)[:200]}") from exc

        serialized = _serialize_data(data)
        display_data = _display_data(serialized)

        # RAG: подтягиваем реальные обращения для цитирования
        citations = await _fetch_citations(db, run_id, serialized, limit=3)
        narration = (
            await _narrate_with_rag(message, kind, serialized, citations)
            if citations
            else await _narrate(message, kind, serialized)
        )
        fallback = _make_answer(kind, serialized, message)
        answer = narration or fallback

        suggestions = _suggestions(kind, serialized, ctx)
        entry = {"question": message, "answer": answer, "sql": sql, "data": display_data, "citations": citations}
        ctx["history"].append(entry)
        ctx["history"] = ctx["history"][-10:]

        return {
            "answer": answer,
            "narration": narration,
            "fallback": fallback,
            "citations": citations,
            "sql": sql,
            "data": display_data,
            "columns": columns,
            "kind": kind,
            "suggestions": suggestions,
            "fast": True,
            "context": {
                "last_municipality": ctx.get("last_municipality"),
                "last_category": ctx.get("last_category"),
                "last_severity": ctx.get("last_severity"),
            },
        }

    # LLM fallback for free-form questions
    history = ctx.get("history", [])[-5:]
    history_str = ""
    for item in history:
        history_str += f"Пользователь: {item['question']}\nSQL: {item.get('sql', 'нет')}\nОтвет: {(item.get('answer') or '')[:200]}\n\n"

    system = SYSTEM_PROMPT.format(run_id=run_id)
    prompt = f"{history_str}Пользователь: {message}\nSQL:"
    sql_response = await _call_ollama(prompt, system)
    sql = _extract_sql(sql_response)

    if not sql or not _validate_llm_sql(sql, run_id):
        answer = "Я не смог построить точный запрос. Уточните: район, категория, тяжесть или 'топ-N'."
        entry = {"question": message, "answer": answer, "sql": None, "data": None}
        ctx["history"].append(entry)
        return {
            "answer": answer,
            "sql": None,
            "data": None,
            "fast": False,
            "suggestions": ["Топ-10 районов", "Критические проблемы ЖКХ", "Главные проблемы по всем районам"],
        }

    try:
        columns, data = await _run_sql(db, sql)
    except Exception as exc:
        answer = f"Ошибка SQL: {str(exc)[:200]}"
        entry = {"question": message, "answer": answer, "sql": sql, "data": None}
        ctx["history"].append(entry)
        return {"answer": answer, "sql": sql, "data": None, "fast": False}

    serialized = _serialize_data(data)
    display_data = _display_data(serialized)
    narration = await _narrate(message, "cluster_list", serialized)
    fallback = _make_answer("cluster_list", serialized, message)
    answer = narration or fallback

    entry = {"question": message, "answer": answer, "sql": sql, "data": display_data}
    ctx["history"].append(entry)

    return {
        "answer": answer,
        "narration": narration,
        "fallback": fallback,
        "sql": sql,
        "data": display_data,
        "columns": columns,
        "kind": "cluster_list",
        "suggestions": _suggestions("cluster_list", serialized, ctx),
        "fast": False,
    }


@router.post("/chat/export")
async def export_chat_result(run_id: int):
    """Export the last chat result table as Excel."""
    history = _ctx(run_id).get("history", [])
    if not history:
        raise HTTPException(404, "No chat history")

    last = history[-1]
    if not last.get("data"):
        raise HTTPException(400, "Last response has no data")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Результат запроса"

    ws.append(["Вопрос:", last["question"]])
    ws.append(["Ответ:", last["answer"]])
    ws.append([])

    data = last["data"]
    headers = list(data[0].keys())
    ws.append(headers)
    for row in data:
        ws.append([str(row.get(header, "")) for header in headers])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=chat_result.xlsx"},
    )


@router.post("/chat/reset")
async def reset_chat(run_id: int):
    """Сбросить контекст разговора."""
    _conversations.pop(run_id, None)
    return {"ok": True}
