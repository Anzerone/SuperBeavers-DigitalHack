"""Fast chat endpoint: deterministic analytics first, Text2SQL as fallback."""
import io
import json
import re
from typing import Any

import aiohttp
import openpyxl
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import CATEGORIES, LLM_MODEL, LLM_TIMEOUT_SECONDS, OLLAMA_URL
from backend.labels import format_severity
from backend.storage.database import get_db

router = APIRouter()

_conversations: dict[int, list[dict]] = {}

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

FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|copy|grant|revoke|vacuum|analyze|call|execute)\b",
    re.IGNORECASE,
)


async def _call_ollama(prompt: str, system: str = "", timeout: int = 45) -> str:
    """Call Ollama API for fallback Text2SQL."""
    request_timeout = min(max(timeout, 10), LLM_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession() as session:
        payload = {
            "model": LLM_MODEL,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 500},
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


def _detect_category(message: str) -> str | None:
    norm = _normalize(message)
    aliases = {
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
    for category in CATEGORIES:
        terms = aliases.get(category, [_normalize(category)])
        if any(term in norm for term in terms):
            return category
    return None


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


async def _known_municipalities(db: AsyncSession, run_id: int) -> list[str]:
    """Все районы с обращениями в run — берём из appeals (а не только тех, где есть кластеры)."""
    result = await db.execute(
        text(
            """
            SELECT DISTINCT municipality
            FROM appeals
            WHERE run_id = :run_id AND municipality IS NOT NULL AND municipality <> ''
            """
        ),
        {"run_id": run_id},
    )
    return [row[0] for row in result.fetchall() if row[0]]


async def _detect_municipality(db: AsyncSession, run_id: int, message: str) -> str | None:
    norm = _normalize(message)
    for municipality in await _known_municipalities(db, run_id):
        muni_norm = _normalize(municipality)
        short_norm = re.sub(r"\b(муниципальный|район|округ|городской|г\.?)\b", "", muni_norm).strip()
        if muni_norm and muni_norm in norm:
            return municipality
        if short_norm and len(short_norm) >= 4 and short_norm in norm:
            return municipality
    return None


def _append_filters(where: list[str], params: dict[str, Any], category: str | None, severity: str | None):
    if category:
        where.append("category = :category")
        params["category"] = category
    if severity:
        where.append("severity = :severity")
        params["severity"] = severity


async def _fast_intent(message: str, run_id: int, db: AsyncSession) -> tuple[str, dict[str, Any], str] | None:
    norm = _normalize(message)
    limit = _detect_limit(message)
    category = _detect_category(message)
    severity = _detect_severity(message)
    municipality = await _detect_municipality(db, run_id, message)

    asks_count = any(term in norm for term in ["сколько", "количество", "число "]) and not municipality
    if asks_count and category:
        where = ["run_id = :run_id", "is_problem = true"]
        params: dict[str, Any] = {"run_id": run_id}
        if category:
            where.append("category = :category")
            params["category"] = category
        if severity:
            where.append("severity = :severity")
            params["severity"] = severity
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

    if municipality:
        where = ["run_id = :run_id", "municipality = :municipality"]
        params: dict[str, Any] = {"run_id": run_id, "municipality": municipality, "limit": 50}
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
        return f"Всего проблемных обращений по запросу: {count}"

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


@router.post("/chat")
async def chat(run_id: int, message: str, db: AsyncSession = Depends(get_db)):
    """Chat with processed data."""
    fast = await _fast_intent(message, run_id, db)
    if fast:
        sql, params, kind = fast
        try:
            columns, data = await _run_sql(db, sql, params)
        except Exception as exc:
            raise HTTPException(500, f"Ошибка при выполнении быстрого запроса: {str(exc)[:200]}") from exc

        serialized = _serialize_data(data)
        display_data = _display_data(serialized)
        answer = _make_answer(kind, serialized, message)
        entry = {"question": message, "answer": answer, "sql": sql, "data": display_data}
        _conversations.setdefault(run_id, []).append(entry)
        return {"answer": answer, "sql": sql, "data": display_data, "columns": columns, "fast": True}

    history = _conversations.get(run_id, [])[-5:]
    context = ""
    for item in history:
        context += f"Пользователь: {item['question']}\nSQL: {item.get('sql', 'нет')}\nОтвет: {item.get('answer', '')}\n\n"

    system = SYSTEM_PROMPT.format(run_id=run_id)
    prompt = f"{context}Пользователь: {message}\nSQL:"
    sql_response = await _call_ollama(prompt, system)
    sql = _extract_sql(sql_response)

    if not sql or not _validate_llm_sql(sql, run_id):
        answer = "Я не смог безопасно построить SQL-запрос. Попробуйте уточнить вопрос: район, категория, тяжесть или топ-N."
        entry = {"question": message, "answer": answer, "sql": None, "data": None}
        _conversations.setdefault(run_id, []).append(entry)
        return {"answer": answer, "sql": None, "data": None, "fast": False}

    try:
        columns, data = await _run_sql(db, sql)
    except Exception as exc:
        answer = f"Ошибка при выполнении SQL-запроса: {str(exc)[:200]}"
        entry = {"question": message, "answer": answer, "sql": sql, "data": None}
        _conversations.setdefault(run_id, []).append(entry)
        return {"answer": answer, "sql": sql, "data": None, "fast": False}

    serialized = _serialize_data(data)
    display_data = _display_data(serialized)
    answer = _make_answer("cluster_list", serialized, message)
    entry = {"question": message, "answer": answer, "sql": sql, "data": display_data}
    _conversations.setdefault(run_id, []).append(entry)
    return {"answer": answer, "sql": sql, "data": display_data, "columns": columns, "fast": False}


@router.post("/chat/export")
async def export_chat_result(run_id: int):
    """Export the last chat result table as Excel."""
    history = _conversations.get(run_id, [])
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
