"""Model-driven analytical chat endpoint with persisted memory."""
import io
import json
from decimal import Decimal
from typing import Any

import aiohttp
import openpyxl
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.config import (
    CHAT_INTENT_MODEL,
    CHAT_LLM_MODEL,
    CHAT_LLM_TIMEOUT_SECONDS,
    OLLAMA_URL,
)
from backend.pipeline.llm_utils import ollama_response_text
from backend.storage.database import get_db
from backend.storage.models import ChatMemory, User

router = APIRouter()

_conversations: dict[tuple[int, int], dict] = {}


CHAT_COLUMN_LABELS = {
    "municipality": "Район",
    "problem_count": "Проблемных обращений",
    "appeal_count": "Обращений",
    "rank": "Ранг",
    "avg_rank": "Средний ранг",
    "top_issues": "Ключевые проблемы",
    "summary_text": "Сводка",
    "cluster_name": "Проблема",
    "description": "Описание",
    "explanation": "Пояснение",
    "example": "Пример обращения",
    "category": "Категория",
    "severity": "Тяжесть",
    "centroid_text": "Выдержка",
    "cluster_count": "Кластеров",
    "count": "Количество",
    "severe_count": "Критичных и высоких",
    "municipality_count": "Муниципалитетов",
    "category_count": "Категорий",
    "share_percent": "Доля, %",
    "severe_share_percent": "Доля тяжелых, %",
    "group_name": "Группа тем",
    "incident_type": "Тип инцидента",
    "outcome": "Итог",
    "incident_text": "Текст обращения",
    "confidence": "Уверенность",
}

SEVERITY_LABELS = {
    "CRITICAL": "Критическая",
    "HIGH": "Высокая",
    "MEDIUM": "Средняя",
    "LOW": "Низкая",
}


def _ctx(run_id: int, user_id: int = 0) -> dict:
    key = (run_id, user_id)
    if key not in _conversations:
        _conversations[key] = {
            "history": [],
            "last_municipality": None,
            "last_municipalities": [],
            "last_category": None,
            "last_categories": [],
            "last_severity": None,
        }
    return _conversations[key]


def _context_response(ctx: dict) -> dict:
    return {
        "last_municipality": ctx.get("last_municipality"),
        "last_municipalities": ctx.get("last_municipalities", []),
        "last_category": ctx.get("last_category"),
        "last_categories": ctx.get("last_categories", []),
        "last_severity": ctx.get("last_severity"),
    }


def _memory_payload(ctx: dict) -> dict:
    return {
        "history": ctx.get("history", [])[-10:],
        **_context_response(ctx),
    }


async def _get_ctx(db: AsyncSession, run_id: int, user_id: int) -> dict:
    key = (run_id, user_id)
    if key in _conversations:
        return _conversations[key]

    row_q = await db.execute(
        select(ChatMemory).where(
            and_(ChatMemory.run_id == run_id, ChatMemory.user_id == user_id)
        )
    )
    row = row_q.scalar_one_or_none()
    ctx = _ctx(run_id, user_id)
    if row and isinstance(row.context, dict):
        stored = row.context
        ctx["history"] = stored.get("history", [])[-10:]
        ctx["last_municipality"] = stored.get("last_municipality")
        ctx["last_municipalities"] = stored.get("last_municipalities", [])
        ctx["last_category"] = stored.get("last_category")
        ctx["last_categories"] = stored.get("last_categories", [])
        ctx["last_severity"] = stored.get("last_severity")
    return ctx


async def _save_ctx(db: AsyncSession, run_id: int, user_id: int, ctx: dict) -> None:
    payload = _memory_payload(ctx)
    stmt = (
        pg_insert(ChatMemory)
        .values(run_id=run_id, user_id=user_id, context=payload)
        .on_conflict_do_update(
            index_elements=["run_id", "user_id"],
            set_={"context": payload, "updated_at": func.now()},
        )
    )
    await db.execute(stmt)
    await db.commit()


def _label_column(column: str) -> str:
    return CHAT_COLUMN_LABELS.get(column, column)


async def _call_ollama(
    prompt: str,
    system: str = "",
    timeout: int = 45,
    temperature: float = 0.1,
    model: str | None = None,
    num_predict: int = 500,
    json_mode: bool = False,
) -> str:
    request_timeout = min(max(timeout, 10), max(CHAT_LLM_TIMEOUT_SECONDS, 10))
    payload = {
        "model": model or CHAT_LLM_MODEL,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "think": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    if json_mode:
        payload["format"] = "json"

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{OLLAMA_URL}/api/generate",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=request_timeout),
        ) as resp:
            if resp.status != 200:
                raise HTTPException(500, f"Ollama error: {resp.status}")
            data = await resp.json()
            return ollama_response_text(data)


async def _known_municipalities(db: AsyncSession, run_id: int) -> list[str]:
    result = await db.execute(
        text("""
            SELECT DISTINCT municipality
            FROM appeals
            WHERE run_id = :run_id AND municipality IS NOT NULL AND municipality <> ''
            ORDER BY municipality
        """),
        {"run_id": run_id},
    )
    return [row[0] for row in result.fetchall() if row[0]]


async def _known_categories(db: AsyncSession, run_id: int) -> list[str]:
    result = await db.execute(
        text("""
            SELECT category
            FROM (
                SELECT DISTINCT category
                FROM appeals
                WHERE run_id = :run_id AND category IS NOT NULL AND category <> ''
                UNION
                SELECT DISTINCT category
                FROM problem_clusters
                WHERE run_id = :run_id AND category IS NOT NULL AND category <> ''
            ) AS categories
            ORDER BY category
        """),
        {"run_id": run_id},
    )
    return [row[0] for row in result.fetchall() if row[0]]


def _plain_tokens(value: str) -> list[str]:
    chars = []
    for char in str(value or "").casefold().replace("ё", "е"):
        chars.append(char if char.isalnum() else " ")
    return [part for part in "".join(chars).split() if len(part) >= 2]


def _candidate_values(
    question: str,
    values: list[str],
    limit: int = 24,
    allow_prefix: bool = True,
) -> list[str]:
    question_tokens = _plain_tokens(question)
    if not question_tokens:
        return []
    question_set = set(question_tokens)
    question_text = " ".join(question_tokens)
    scored: list[tuple[int, int, str]] = []

    for value in values:
        value_tokens = _plain_tokens(value)
        if not value_tokens:
            continue
        value_text = " ".join(value_tokens)
        score = 0
        if value_text and f" {value_text} " in f" {question_text} ":
            score += 20
        for token in value_tokens:
            if token in question_set:
                score += 6
                continue
            if allow_prefix and len(token) >= 4:
                for question_token in question_tokens:
                    if len(question_token) >= 4 and (
                        token.startswith(question_token) or question_token.startswith(token)
                    ):
                        score += 2
                        break
        if score:
            scored.append((score, len(value), value))

    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [value for _, _, value in scored[:limit]]


def _candidate_severities(question: str) -> list[str]:
    tokens = _plain_tokens(question)
    severities: list[str] = []

    def add(value: str) -> None:
        if value not in severities:
            severities.append(value)

    for token in tokens:
        if token.startswith("критич") or token == "critical":
            add("CRITICAL")
        if token.startswith("высок") or token == "high":
            add("HIGH")
        if token.startswith("средн") or token == "medium":
            add("MEDIUM")
        if token.startswith("низк") or token == "low":
            add("LOW")
    return severities


def _parse_json_object(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            value = json.loads(raw[start : end + 1])
        except Exception:
            return {}
    return value if isinstance(value, dict) else {}


SQL_PLANNER_SYSTEM = """Ты строишь один безопасный PostgreSQL SELECT для аналитического чата по обращениям граждан.
Верни строго JSON:
{
  "sql": "SELECT ...",
  "title": "короткое название результата",
  "answer_hint": "что пользователь увидит в таблице",
  "municipalities": ["точные названия из known_municipalities"],
  "categories": ["точные названия из known_categories"],
  "severity": "CRITICAL|HIGH|MEDIUM|LOW|null"
}
Никакого текста вне JSON.

Схема:
- appeals(id, run_id, municipality, group_name, incident_type, outcome, incident_text, is_problem, severity, category, confidence, sentiment)
- problem_clusters(id, run_id, municipality, cluster_name, description, category, severity, appeal_count, rank, rank_score, topic_diversity, centroid_text)
- summaries(id, run_id, municipality, rank, problem_count, avg_rank, top_issues, summary_text)

Обязательные правила:
- Только SELECT.
- Всегда фильтруй run_id = :run_id.
- Не используй параметры кроме :run_id; остальные фильтры пиши строковыми литералами.
- Всегда добавляй LIMIT от 1 до 50.
- Для appeals всегда добавляй is_problem = true, если вопрос не просит исходные непрофильные обращения.
- known_municipalities содержит только релевантные кандидаты из БД, а не весь список районов.
- Используй только точные категории из known_categories и точные муниципалитеты из known_municipalities.
- candidate_categories и candidate_municipalities уже отфильтрованы из БД под вопрос пользователя. Если список непустой и вопрос называет такую сущность, выбирай из него.
- Если candidate_categories пустой, не добавляй category IN со всеми категориями.
- Если candidate_municipalities пустой, не добавляй municipality IN со всеми муниципалитетами.
- Если known_municipalities пустой, в WHERE запрещены любые фильтры по municipality.
- Короткие названия пользователя сопоставляй с точными значениями из known_municipalities.
- "Омская область" без слова "другое" означает весь набор данных run_id, а не муниципалитет "Омская область, другое".
- Для вопроса "в Омской области" SQL должен идти по всему run_id без условия municipality.
- В SQL всегда пиши run_id = :run_id, не подставляй числовой run_id.
- Если пользователь просит проблемы, критические проблемы, расписать проблемы, причины или детализацию, используй problem_clusters.
- Для problem_clusters выбирай понятные поля: municipality, category, cluster_name, appeal_count, severity, description AS explanation, centroid_text AS example.
- Если пользователь просит категорию с тяжестью, фильтруй и category, и severity.
- Если candidate_severities непустой, SQL обязан фильтровать ровно эти уровни тяжести.
- "критические" означает severity = 'CRITICAL'. "критичные и высокие" означает severity IN ('CRITICAL','HIGH').
- Не добавляй severity, если пользователь явно не назвал тяжесть. Слово "проблемы" само по себе не означает CRITICAL или HIGH.
- Если пользователь просит топ категорий, группируй appeals по category и добавляй COUNT(*) AS problem_count, тяжелые обращения, охват муниципалитетов и долю.
- Если пользователь просит топ районов или рейтинг районов, используй summaries и добавляй summary_text AS explanation.
- Если пользователь просит сравнить районы, верни только сравниваемые районы из summaries и добавь top_issues, summary_text AS explanation.
- Для сравнения двух и более муниципалитетов всегда используй summaries, не problem_clusters.
- Если пользователь просит сравнить категории, группируй appeals только по сравниваемым категориям.
- Не превращай запрос о проблемах в рейтинг районов, если пользователь явно не просит районы, рейтинг или где больше.
- current_context и recent_history используй только если новый вопрос явно продолжает прошлый. Если вопрос самостоятельный, не переноси старые category, municipality или severity.
"""


SQL_REVIEW_SYSTEM = """Проверь, отвечает ли SQL точному вопросу пользователя.
Верни строго JSON:
{
  "ok": true,
  "sql": "SELECT ...",
  "title": "короткое название результата",
  "answer_hint": "что пользователь увидит в таблице",
  "municipalities": [],
  "categories": [],
  "severity": null
}

Если SQL не отвечает вопросу, верни ok=false и исправленный SQL в том же поле sql.
Если во входном JSON есть validation_error, исправь SQL так, чтобы ошибка исчезла.
sql не может быть null: всегда верни исправленный SELECT.
Проверяй особенно:
- названные категории и уровни тяжести должны быть в WHERE;
- не добавляй severity, если пользователь явно не назвал тяжесть; обычное слово "проблемы" не означает CRITICAL;
- сравнение должно возвращать только сравниваемые сущности;
- сравнение двух и более муниципалитетов должно использовать summaries;
- запрос "проблемы/распиши/критические проблемы" должен идти в problem_clusters с explanation и example;
- "Омская область" без слова "другое" означает весь run_id, не один муниципалитет;
- если known_municipalities пустой, исправленный SQL не должен иметь municipality в WHERE;
- run_id должен оставаться параметром :run_id;
- общий список проблем не должен превращаться в топ районов.
- не переноси фильтры из current_context, если новый вопрос самостоятельный.
Никакого текста вне JSON.
"""


ANSWER_SYSTEM = """Сформулируй ответ для аналитического чата.
Верни строго JSON {"answer": "..."}.
Правила:
- 1-3 коротких предложения на русском.
- Сразу итог, без рассуждений и без markdown.
- Объясни, какие фильтры и смысл таблицы: что является строкой, что означает счетчик, где смотреть пояснение/пример.
- Не добавляй факты, которых нет в данных.
"""


def _compact_sql(sql: str) -> str:
    return " ".join(str(sql or "").strip().split())


def _sql_tokens(sql: str) -> list[str]:
    chars = []
    for ch in str(sql or "").lower():
        if ch.isalnum() or ch in {"_", ":"}:
            chars.append(ch)
        else:
            chars.append(" ")
    return [part for part in "".join(chars).split() if part]


def _validate_model_sql(sql: Any) -> str | None:
    sql = _compact_sql(str(sql or ""))
    if sql.endswith(";"):
        sql = sql[:-1].strip()
    lowered = sql.lower()
    if not lowered.startswith("select "):
        return None
    if ";" in sql or "--" in lowered or "/*" in lowered or "*/" in lowered:
        return None
    if ":run_id" not in lowered:
        return None

    tokens = _sql_tokens(sql)
    forbidden = {
        "insert", "update", "delete", "drop", "alter", "truncate", "create",
        "copy", "grant", "revoke", "vacuum", "analyze", "call", "execute",
    }
    if any(token in forbidden for token in tokens):
        return None

    allowed_tables = {"appeals", "problem_clusters", "summaries", "appeal_cluster_map"}
    for index, token in enumerate(tokens[:-1]):
        if token in {"from", "join"} and tokens[index + 1] not in allowed_tables:
            return None

    params = [token for token in tokens if token.startswith(":")]
    if any(param != ":run_id" for param in params):
        return None

    if "limit" not in tokens:
        sql = f"{sql} LIMIT 50"
        tokens = _sql_tokens(sql)
    if "order" not in tokens:
        order_by = None
        if "problem_clusters" in tokens and "appeal_count" in tokens:
            order_by = "ORDER BY appeal_count DESC"
        elif "summaries" in tokens and "problem_count" in tokens:
            order_by = "ORDER BY problem_count DESC"
        if order_by:
            lowered_sql = sql.lower()
            limit_index = lowered_sql.rfind(" limit ")
            if limit_index >= 0:
                sql = f"{sql[:limit_index].strip()} {order_by}{sql[limit_index:]}"
            else:
                sql = f"{sql} {order_by}"
    return sql


def _clean_plan(plan: dict[str, Any]) -> dict[str, Any]:
    raw_severity = plan.get("severity")
    raw_severities = plan.get("severities")
    severity_values: list[str] = []
    if isinstance(raw_severities, list):
        source = " ".join(str(item).upper() for item in raw_severities)
    else:
        source = str(raw_severity or "").upper()
    for value in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        if value in source:
            severity_values.append(value)

    return {
        "sql": plan.get("sql"),
        "title": str(plan.get("title") or "").strip(),
        "answer_hint": str(plan.get("answer_hint") or "").strip(),
        "municipalities": [
            str(item).strip()
            for item in (plan.get("municipalities") or [])
            if str(item).strip()
        ],
        "categories": [
            str(item).strip()
            for item in (plan.get("categories") or [])
            if str(item).strip()
        ],
        "severity": "|".join(severity_values) if severity_values else None,
        "severities": severity_values,
    }


def _filter_plan_entities(
    plan: dict[str, Any],
    candidate_municipalities: list[str],
    known_categories: list[str],
) -> dict[str, Any]:
    muni_set = set(candidate_municipalities)
    category_set = set(known_categories)
    plan["municipalities"] = [
        item for item in (plan.get("municipalities") or [])
        if item in muni_set
    ]
    plan["categories"] = [
        item for item in (plan.get("categories") or [])
        if item in category_set
    ]
    return plan


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _plan_semantic_issue(
    sql: str,
    plan: dict[str, Any],
    all_municipalities: list[str],
    candidate_municipalities: list[str],
    candidate_categories: list[str],
    candidate_severities: list[str],
) -> str | None:
    compact = _compact_sql(sql)
    lowered = compact.lower()
    where_tail = ""
    if " where " in lowered:
        where_tail = lowered.split(" where ", 1)[1]
    candidate_muni_set = set(candidate_municipalities)

    municipality_filter_markers = (
        " municipality =",
        ".municipality =",
        " municipality in",
        ".municipality in",
        " municipality like",
        ".municipality like",
    )
    has_municipality_filter = any(marker in where_tail for marker in municipality_filter_markers)
    if has_municipality_filter and not candidate_municipalities:
        return (
            "Ошибка: SQL фильтрует municipality, но candidate_municipalities пустой. "
            "Убери все условия municipality из WHERE. Для фразы 'в Омской области' "
            "используй весь run_id без фильтра municipality."
        )

    tokens = _sql_tokens(sql)
    if len(candidate_municipalities) >= 2 and "summaries" not in tokens:
        return (
            "Ошибка: вопрос сравнивает несколько муниципалитетов. Для такого сравнения "
            "нужно использовать таблицу summaries и вернуть только эти муниципалитеты."
        )
    if len(candidate_municipalities) >= 2 and "summaries" in tokens and "category" in tokens:
        return (
            "Ошибка: в таблице summaries нет поля category. Для сравнения муниципалитетов "
            "верни municipality, rank, problem_count, avg_rank, top_issues и summary_text."
        )
    if len(candidate_municipalities) >= 2 and "summaries" in tokens:
        required = {"municipality", "rank", "problem_count", "avg_rank", "top_issues"}
        missing = sorted(required.difference(tokens))
        if missing:
            return (
                "Ошибка: для сравнения муниципалитетов в summaries не хватает колонок "
                f"{missing!r}. Верни municipality, rank, problem_count, avg_rank, top_issues, summary_text."
            )
    if len(candidate_municipalities) >= 2 and " or " in where_tail:
        return (
            "Ошибка: для сравнения нескольких муниципалитетов используй "
            "municipality IN (...), а не OR, чтобы фильтр run_id применялся ко всем строкам."
        )

    for municipality in all_municipalities:
        if _sql_literal(municipality) not in compact:
            continue
        if municipality not in candidate_muni_set:
            return (
                f"Ошибка: SQL фильтрует municipality {municipality!r}, но этого значения "
                "нет в candidate_municipalities. Убери этот фильтр или используй только "
                "candidate_municipalities."
            )

    for category in candidate_categories:
        if _sql_literal(category) not in compact:
            return f"Ошибка: вопрос выбрал категорию {category!r}; SQL обязан фильтровать эту категорию."

    for severity in candidate_severities:
        if _sql_literal(severity) not in compact:
            return f"Ошибка: вопрос выбрал тяжесть {severity!r}; SQL обязан фильтровать эту тяжесть."
    if len(candidate_severities) == 1:
        extra = [
            value for value in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
            if value not in candidate_severities and _sql_literal(value) in compact
        ]
        if extra:
            return (
                f"Ошибка: вопрос выбрал только тяжесть {candidate_severities[0]!r}; "
                f"SQL не должен добавлять {extra!r}."
            )

    return None


def _repair_plan_from_candidates(
    message: str,
    candidate_municipalities: list[str],
    candidate_categories: list[str],
    candidate_severities: list[str],
) -> dict[str, Any] | None:
    if len(candidate_municipalities) >= 2:
        values = ", ".join(_sql_literal(value) for value in candidate_municipalities[:5])
        return {
            "sql": (
                "SELECT municipality, rank, problem_count, ROUND(avg_rank::numeric, 1) AS avg_rank, "
                f"top_issues, summary_text AS explanation FROM summaries WHERE run_id = :run_id "
                f"AND municipality IN ({values}) LIMIT 50"
            ),
            "title": "Сравнение муниципалитетов",
            "answer_hint": "Таблица сравнивает выбранные муниципалитеты по рангу, количеству проблем и ключевым проблемам.",
            "municipalities": candidate_municipalities[:5],
            "categories": [],
            "severity": None,
            "severities": [],
        }

    if candidate_categories or candidate_severities:
        filters = ["run_id = :run_id"]
        if candidate_categories:
            values = ", ".join(_sql_literal(value) for value in candidate_categories[:5])
            filters.append(f"category IN ({values})")
        if candidate_severities:
            values = ", ".join(_sql_literal(value) for value in candidate_severities)
            filters.append(f"severity IN ({values})")
        return {
            "sql": (
                "SELECT municipality, category, cluster_name, appeal_count, severity, "
                "description AS explanation, centroid_text AS example "
                f"FROM problem_clusters WHERE {' AND '.join(filters)} "
                "ORDER BY appeal_count DESC LIMIT 50"
            ),
            "title": "Проблемные кластеры",
            "answer_hint": "Таблица показывает повторяющиеся проблемы с количеством обращений, пояснением и примером.",
            "municipalities": [],
            "categories": candidate_categories[:5],
            "severity": "|".join(candidate_severities) if candidate_severities else None,
            "severities": candidate_severities,
        }

    return {
        "sql": (
            "SELECT municipality, category, cluster_name, appeal_count, severity, "
            "description AS explanation, centroid_text AS example "
            "FROM problem_clusters WHERE run_id = :run_id ORDER BY appeal_count DESC LIMIT 50"
        ),
        "title": "Проблемные кластеры",
        "answer_hint": "Таблица показывает повторяющиеся проблемы с количеством обращений, пояснением и примером.",
        "municipalities": [],
        "categories": [],
        "severity": None,
        "severities": [],
    }


async def _model_sql_plan(message: str, run_id: int, db: AsyncSession, ctx: dict) -> dict[str, Any]:
    municipalities = await _known_municipalities(db, run_id)
    categories = await _known_categories(db, run_id)
    candidate_municipalities = _candidate_values(message, municipalities, allow_prefix=False)
    candidate_categories = _candidate_values(message, categories)
    candidate_severities = _candidate_severities(message)
    payload = {
        "run_id": run_id,
        "question": message,
        "current_context": {},
        "recent_history": [],
        "known_municipalities": candidate_municipalities,
        "known_categories": candidate_categories,
        "candidate_municipalities": candidate_municipalities,
        "candidate_categories": candidate_categories,
        "candidate_severities": candidate_severities,
        "allowed_severity": ["CRITICAL", "HIGH", "MEDIUM", "LOW"],
    }
    raw = await _call_ollama(
        json.dumps(payload, ensure_ascii=False),
        SQL_PLANNER_SYSTEM,
        timeout=CHAT_LLM_TIMEOUT_SECONDS,
        temperature=0,
        model=CHAT_INTENT_MODEL,
        num_predict=650,
        json_mode=True,
    )
    plan = _clean_plan(_parse_json_object(raw))
    plan = _filter_plan_entities(plan, candidate_municipalities, candidate_categories)
    checked_sql = _validate_model_sql(plan.get("sql"))
    initial_issue = None
    if checked_sql:
        plan["sql"] = checked_sql
        initial_issue = _plan_semantic_issue(
            checked_sql,
            plan,
            municipalities,
            candidate_municipalities,
            candidate_categories,
            candidate_severities,
        )
        if not initial_issue:
            return plan

    repaired = _repair_plan_from_candidates(
        message,
        candidate_municipalities,
        candidate_categories,
        candidate_severities,
    )
    if repaired and _validate_model_sql(repaired.get("sql")):
        return repaired

    review_payload = {
        **payload,
        "candidate_plan": plan,
        "validation_error": initial_issue,
    }
    review_raw = await _call_ollama(
        json.dumps(review_payload, ensure_ascii=False),
        SQL_REVIEW_SYSTEM,
        timeout=CHAT_LLM_TIMEOUT_SECONDS,
        temperature=0,
        model=CHAT_INTENT_MODEL,
        num_predict=650,
        json_mode=True,
    )
    review = _clean_plan(_parse_json_object(review_raw))
    review = _filter_plan_entities(review, candidate_municipalities, candidate_categories)
    review_sql = _validate_model_sql(review.get("sql"))
    if review_sql:
        review["sql"] = review_sql
        review_issue = _plan_semantic_issue(
            review_sql,
            review,
            municipalities,
            candidate_municipalities,
            candidate_categories,
            candidate_severities,
        )
        if not review_issue and (review.get("title") or review.get("answer_hint")):
            return review
    if checked_sql and not initial_issue:
        return plan
    plan["sql"] = None
    return plan


async def _run_sql(db: AsyncSession, sql: str, params: dict[str, Any] | None = None) -> tuple[list[str], list[dict]]:
    await db.execute(text("SET LOCAL statement_timeout = 5000"))
    result = await db.execute(text(sql), params or {})
    rows = result.fetchall()
    columns = list(result.keys())
    data = [dict(row._mapping) for row in rows]
    return columns, data


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _serialize_data(data: list[dict]) -> list[dict]:
    return [{key: _json_safe(value) for key, value in row.items()} for row in data]


def _display_value(key: str, value: Any) -> Any:
    if key == "top_issues":
        return _format_top_issues(value)
    if key == "severity" and isinstance(value, str):
        return SEVERITY_LABELS.get(value, value)
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
        name = issue.get("name") or issue.get("category") or ""
        count = issue.get("count", 0)
        if name:
            parts.append(f"{name} ({count})")
    return ", ".join(parts)


async def _model_answer(
    message: str,
    plan: dict[str, Any],
    columns: list[str],
    data: list[dict],
) -> str:
    if not data:
        return "По этому запросу в обработанных данных ничего не найдено."

    column_set = set(columns)
    row_count = len(data)
    row_word = "строка" if row_count == 1 else "строки" if 2 <= row_count <= 4 else "строк"
    hint = str(plan.get("answer_hint") or "").strip()

    if {"municipality", "rank", "problem_count"}.issubset(column_set):
        return (
            f"Сравнил выбранные муниципалитеты: {row_count} {row_word}. "
            "Строка — район, «Проблемных обращений» — общее количество проблем, "
            "«Ключевые проблемы» показывает основные темы и их числа."
        )

    if "cluster_name" in column_set:
        return (
            f"Показал проблемные кластеры: {row_count} {row_word}. "
            "Строка — отдельная повторяющаяся проблема; счетчик показывает число обращений. "
            "Пояснение и пример раскрывают суть проблемы."
        )

    if "category" in column_set and "municipality" not in column_set:
        return (
            f"Показал категории: {row_count} {row_word}. "
            "Строка — категория, счетчики показывают количество проблемных обращений и тяжелых случаев."
        )

    if hint:
        return hint
    if plan.get("title"):
        return f"{plan['title']}: {row_count} строк."
    return f"Показал результат по запросу: {row_count} строк."


def _update_context_from_plan(ctx: dict, plan: dict[str, Any]) -> None:
    municipalities = plan.get("municipalities") or []
    categories = plan.get("categories") or []
    severity = plan.get("severity")
    if municipalities:
        ctx["last_municipalities"] = municipalities
        ctx["last_municipality"] = municipalities[0] if len(municipalities) == 1 else None
    if categories:
        ctx["last_categories"] = categories
        ctx["last_category"] = categories[0] if len(categories) == 1 else None
    if severity in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
        ctx["last_severity"] = severity


@router.post("/chat")
async def chat(
    run_id: int,
    message: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fast single-LLM-call chat → SQL → rule-based table render.

    Target: 4-6 sec on GPU using qwen3:4b. No template fallbacks, no second LLM pass.
    """
    user_id = current_user.id
    ctx = await _get_ctx(db, run_id, user_id)

    munis = await _known_municipalities(db, run_id)
    cats = await _known_categories(db, run_id)
    munis_str = ", ".join(munis[:25]) or "—"
    cats_str = ", ".join(cats[:25]) or "—"

    system = f"""Ты — аналитик обращений граждан Омской области. Получаешь вопрос пользователя и формируешь один PostgreSQL SELECT-запрос к таблице problem_clusters.

Таблица problem_clusters (всегда фильтруй WHERE run_id = :run_id):
- municipality (text) — район
- cluster_name (text) — название проблемы
- description (text) — описание
- category (text) — категория
- severity (text) — CRITICAL | HIGH | MEDIUM | LOW
- appeal_count (int) — число обращений
- rank (int) — ранг проблемы внутри района
- centroid_text (text) — выдержка из типичного обращения

Доступные категории: {cats_str}
Доступные районы: {munis_str}

Правила:
1. Возвращай СТРОГО JSON: {{"title": "Краткое название ответа", "sql": "SELECT ..."}}
2. SQL — только SELECT, обязательно WHERE run_id = :run_id
3. LIMIT не более 20
4. Используй SUM(appeal_count) для агрегаций по району/категории
5. Колонки в SELECT именуй на русском через AS "Колонка"
6. Если ничего не подходит — верни топ-10 районов по числу проблем"""

    prompt = f'Вопрос: "{message}"\n\nJSON:'

    sql: str | None = None
    title = "Результат"
    try:
        raw = await _call_ollama(prompt, system=system, model=CHAT_LLM_MODEL, num_predict=350, timeout=20, json_mode=True)
        plan = _parse_json_object(raw) or {}
        sql_candidate = plan.get("sql") if isinstance(plan, dict) else None
        sql = _validate_model_sql(sql_candidate)
        if isinstance(plan, dict) and isinstance(plan.get("title"), str):
            t = plan["title"].strip()
            if t:
                title = t[:120]
    except Exception:
        sql = None

    columns: list[str] = []
    data: list[dict] = []
    if sql:
        try:
            columns, data = await _run_sql(db, sql, {"run_id": run_id})
        except Exception:
            await db.rollback()
            data = []

    # Безопасный fallback — топ-10 районов, без второго LLM-вызова
    if not data:
        sql_safe = (
            "SELECT municipality AS \"Район\", "
            "SUM(appeal_count)::int AS \"Обращений\", "
            "COUNT(*)::int AS \"Кластеров\" "
            "FROM problem_clusters WHERE run_id = :run_id "
            "GROUP BY municipality ORDER BY SUM(appeal_count) DESC NULLS LAST LIMIT 10"
        )
        try:
            columns, data = await _run_sql(db, sql_safe, {"run_id": run_id})
            sql = sql_safe
            title = "Топ-10 районов по обращениям"
        except Exception:
            await db.rollback()
            columns, data = [], []

    serialized = _serialize_data(data)
    display_data = _display_data(serialized)

    # Rule-based рендеринг таблицы — без второго LLM-вызова
    if not display_data:
        answer = "Данные не найдены. Загрузите файл или дождитесь окончания обработки."
    else:
        lines = [f"{title}:"]
        for i, row in enumerate(display_data[:10], 1):
            parts = []
            for col, val in list(row.items())[:5]:
                if val is None or val == "":
                    continue
                parts.append(f"{col}: {val}")
            lines.append(f"{i}. " + " · ".join(parts))
        if len(display_data) > 10:
            lines.append(f"\n(показано 10 из {len(display_data)})")
        answer = "\n".join(lines)

    entry = {
        "question": message,
        "answer": answer,
        "sql": sql,
        "data": display_data,
        "columns": columns,
        "citations": [],
        "kind": "llm_sql",
        "suggestions": [],
        "fast": False,
    }
    ctx["history"].append(entry)
    ctx["history"] = ctx["history"][-10:]
    await _save_ctx(db, run_id, user_id, ctx)

    return {
        "answer": answer,
        "narration": answer,
        "fallback": answer,
        "citations": [],
        "sql": sql,
        "data": display_data,
        "columns": columns,
        "kind": "llm_sql",
        "suggestions": [],
        "fast": False,
        "context": _context_response(ctx),
    }


@router.get("/chat/history")
async def get_chat_history(
    run_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ctx = await _get_ctx(db, run_id, current_user.id)
    return {
        "history": ctx.get("history", [])[-10:],
        "context": _context_response(ctx),
    }


@router.post("/chat/export")
async def export_chat_result(
    run_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    history = (await _get_ctx(db, run_id, current_user.id)).get("history", [])
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
    ws.append([_label_column(header) for header in headers])
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
async def reset_chat(
    run_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _conversations.pop((run_id, current_user.id), None)
    row_q = await db.execute(
        select(ChatMemory).where(
            and_(ChatMemory.run_id == run_id, ChatMemory.user_id == current_user.id)
        )
    )
    row = row_q.scalar_one_or_none()
    if row:
        await db.delete(row)
        await db.commit()
    return {"ok": True}
