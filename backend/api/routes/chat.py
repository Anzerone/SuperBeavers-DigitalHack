"""Retrieval-driven analytical chat with persisted memory.

Поток ответа максимально простой и быстрый:
1. Полнотекстовый поиск по обращениям (Postgres ts_vector + BM25-ранжирование).
2. Если по словам вопроса ничего не нашлось — векторный (семантический) поиск bge-m3.
3. LLM-резюме найденного с жёстким дедлайном. Если модель не успевает —
   отдаём найденные обращения без резюме (без зависаний и без 500).

Никаких SQL-планировщиков и заготовленных «блоков»: только полнотекстовый
поиск, векторный поиск и LLM.
"""
import asyncio
from collections import Counter
import io
import json
import math
import re
import time
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
    CHAT_ANSWER_DEADLINE_SECONDS,
    CHAT_LLM_MODEL,
    CHAT_LLM_TIMEOUT_SECONDS,
    OLLAMA_URL,
)
from backend.pipeline.llm_utils import ollama_response_text
from backend.storage.database import get_db
from backend.storage.models import ChatMemory, User

router = APIRouter()

_conversations: dict[tuple[int, int], dict] = {}


SEVERITY_LABELS = {
    "CRITICAL": "Критическая",
    "HIGH": "Высокая",
    "MEDIUM": "Средняя",
    "LOW": "Низкая",
}

# Сколько кандидатов вытягиваем из БД (полный набор для сводки и выгрузки).
RAG_MAX_CANDIDATES = 5000
# Размер ранжированного пула, который идёт в LLM и в примеры-цитаты.
RAG_RANKED_POOL = 100
# Сколько лучших обращений передаём в LLM как контекст.
RAG_LLM_CONTEXT = 8
# Предохранитель на размер Excel-выгрузки писем по теме.
RAG_EXPORT_HARD_LIMIT = 50000
# Примеры под ответом: 1-2 обращения достаточно, больше — шум.
RAG_TOP_K = 2
# Конфигурация полнотекстового поиска. 'russian' даёт стемминг: запрос «дорог»
# находит «дороги/дорогам/дорогах», а не только дословное совпадение.
FTS_CONFIG = "russian"
# Пул для чисто семантического поиска, когда слова вопроса не нашлись в текстах.
RAG_SEMANTIC_POOL = 120
# Порог косинусной близости для семантического фолбэка.
RAG_SEMANTIC_MIN = 0.4
RAG_STOPWORDS = {
    "что", "как", "где", "это", "или", "для", "над", "под", "при", "про",
    "все", "всех", "всем", "всей", "есть", "мне", "нам", "топ", "самые",
    "какие", "какая", "какой", "везде", "целом",
    "сколько", "покажи", "показать", "сравни", "сводка",
    "проблема", "проблемы", "обращение", "обращения",
    "больше", "меньше", "всего", "много", "мало", "самый", "самая", "самое",
    "почему", "когда", "куда", "распиши", "опиши", "список",
    # Общегеографические слова (во всех падежах) — не должны сужать поиск по тексту.
    "район", "районы", "районе", "района", "районом", "районам", "районах",
    "округ", "округе", "округа", "округом", "округов",
    "город", "городе", "города", "городом",
    "область", "области", "областью", "регион", "регионе", "региона", "региону",
}


# === Память диалога ===

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
    history = ctx.get("history", [])[-10:]
    # Полные письма (export_rows) тяжёлые — храним только у последнего ответа,
    # чтобы JSON памяти не разрастался. У остальных оставляем сводку и примеры.
    trimmed = []
    for index, entry in enumerate(history):
        if isinstance(entry, dict) and index < len(history) - 1:
            entry = {key: value for key, value in entry.items() if key != "export_rows"}
        trimmed.append(entry)
    return {
        "history": trimmed,
        **_context_response(ctx),
    }


def _clean_history_entry(entry: Any) -> Any:
    """Strip leaked service text from previously saved answers."""
    if not isinstance(entry, dict):
        return entry
    answer = _sanitize_answer(str(entry.get("answer") or ""))
    entry["answer"] = answer
    for key in ("narration", "fallback"):
        if entry.get(key):
            entry[key] = answer
    entry["citations"] = (entry.get("citations") or [])[:RAG_TOP_K]
    # SQL больше не используем; сводная таблица (data/columns) и письма
    # (export_rows) остаются.
    entry.pop("sql", None)
    return entry


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
        ctx["history"] = [_clean_history_entry(item) for item in stored.get("history", [])[-10:]]
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


# === Ollama ===

async def _call_ollama(
    prompt: str,
    system: str = "",
    timeout: int = 45,
    temperature: float = 0.1,
    model: str | None = None,
    num_predict: int = 320,
    json_mode: bool = False,
) -> str:
    request_timeout = min(max(timeout, 5), max(CHAT_LLM_TIMEOUT_SECONDS, 5))
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
                raise RuntimeError(f"Ollama error: {resp.status}")
            data = await resp.json()
            return ollama_response_text(data)


# === Кандидаты для сужения поиска (район / категория / тяжесть) ===

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
    limit: int = 8,
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
            if token.startswith("обращ"):
                continue
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
                    prefix_len = min(len(token), len(question_token), 5)
                    if prefix_len >= 5 and token[:prefix_len] == question_token[:prefix_len]:
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


async def _detect_filters(db: AsyncSession, run_id: int, message: str) -> dict[str, list[str]]:
    """Лёгкое определение района/категории/тяжести для сужения поиска."""
    municipalities = await _known_municipalities(db, run_id)
    categories = await _known_categories(db, run_id)
    muni_hits = _candidate_values(message, municipalities, allow_prefix=False)
    # Дополняем падежными формами («в Омске» → «Омск г.о.»).
    for name in _match_municipalities(message, municipalities):
        if name not in muni_hits:
            muni_hits.append(name)
    return {
        "municipalities": muni_hits,
        "categories": _candidate_values(message, categories),
        "severities": _candidate_severities(message),
    }


# === Поиск ===

def _rag_tokens(value: str) -> list[str]:
    tokens = _plain_tokens(value)
    return [token for token in tokens if len(token) >= 3 and token not in RAG_STOPWORDS]


# Общие слова в названиях районов — не годятся как опознавательные.
_GENERIC_PLACE_TOKENS = {
    "район", "округ", "округа", "городской", "город", "области", "область",
    "областной", "муниципальный", "муниципальное", "сельское", "сельский",
    "поселение", "поселок", "имени", "другое", "национальный", "немецкий",
    "омская", "омской", "рабочий",
}


def _common_prefix_len(a: str, b: str) -> int:
    count = 0
    for x, y in zip(a, b):
        if x != y:
            break
        count += 1
    return count


def _name_keys(name: str) -> list[str]:
    """Опознавательные слова из названия района/категории (без общих слов)."""
    return [t for t in _plain_tokens(name) if len(t) >= 3 and t not in _GENERIC_PLACE_TOKENS]


def _token_hits_key(token: str, key: str) -> bool:
    """Совпадает ли слово запроса с опознавательным словом (с учётом окончаний)."""
    if token == key:
        return True
    cp = _common_prefix_len(token, key)
    if cp >= 4 and (token.startswith(key) or key.startswith(token)):
        return True
    # Падежные формы прилагательных: «калачинском» ↔ «калачинский».
    return cp >= 5


def _match_municipalities(message: str, municipalities: list[str]) -> list[str]:
    """Определяем район, упомянутый в запросе, с учётом падежных форм."""
    q = [t for t in _plain_tokens(message) if len(t) >= 4 and t not in _GENERIC_PLACE_TOKENS]
    if not q:
        return []
    scored: dict[str, int] = {}
    for name in municipalities:
        keys = _name_keys(name)
        if not keys:
            continue
        best = 0
        for key in keys:
            for tok in q:
                cp = _common_prefix_len(tok, key)
                if tok == key:
                    best = max(best, 100)
                elif cp >= 4 and (tok.startswith(key) or key.startswith(tok)):
                    best = max(best, 50 + cp)
                elif cp >= 5:
                    best = max(best, 30 + cp)
        if best:
            scored[name] = best
    if not scored:
        return []
    top = max(scored.values())
    # Берём только явно лучшие совпадения, чтобы не цеплять похожие районы.
    return [name for name, score in sorted(scored.items(), key=lambda i: -i[1]) if score >= top - 5][:3]


def _strip_filter_terms(query_tokens: list[str], filters: dict[str, list[str]]) -> list[str]:
    """Убираем из текстового запроса слова, ставшие фильтром (категория/район).

    Иначе «Проблемы ЖКХ» = категория ЖКХ И текст содержит «жкх» — это сильно
    режет выдачу; а «в Омске» как OR-слово, наоборот, раздувает её.
    """
    keys: list[str] = []
    for name in (filters.get("categories") or []) + (filters.get("municipalities") or []):
        keys.extend(_name_keys(name))
    if not keys:
        return query_tokens
    kept = []
    for token in query_tokens:
        if any(_token_hits_key(token, key) for key in keys):
            continue
        kept.append(token)
    return kept


def _in_filter_sql(column_sql: str, values: list[str], prefix: str, params: dict[str, Any]) -> str | None:
    values = [str(value).strip() for value in values or [] if str(value).strip()]
    if not values:
        return None
    names = []
    for index, value in enumerate(values[:8]):
        key = f"{prefix}_{index}"
        params[key] = value
        names.append(f":{key}")
    return f"{column_sql} IN ({', '.join(names)})"


def _excerpt_for_terms(text_value: str, query_tokens: list[str], limit: int = 260) -> str:
    clean = " ".join(str(text_value or "").split())
    clean = clean.lstrip("'\"«,;:.- ")
    if len(clean) <= limit:
        return clean
    lowered = clean.casefold().replace("ё", "е")
    first_hit = -1
    for token in query_tokens:
        pos = lowered.find(token)
        if pos >= 0 and (first_hit < 0 or pos < first_hit):
            first_hit = pos
    if first_hit < 0:
        return clean[:limit].rstrip() + "..."
    start = max(first_hit - 70, 0)
    end = min(start + limit, len(clean))
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(clean) else ""
    return prefix + clean[start:end].strip("'\"«,;:- ").strip() + suffix


def _bm25_rank(rows: list[dict[str, Any]], query_tokens: list[str], top_k: int) -> list[dict[str, Any]]:
    if not rows or not query_tokens:
        return []

    docs = [_rag_tokens(row.get("incident_text") or "") for row in rows]
    doc_count = len(docs)
    avg_len = sum(len(doc) for doc in docs) / max(doc_count, 1)
    df: Counter[str] = Counter()
    for doc in docs:
        df.update(set(doc))

    query_unique = list(dict.fromkeys(query_tokens))
    k1 = 1.4
    b = 0.75
    scored: list[tuple[float, dict[str, Any]]] = []
    for row, doc in zip(rows, docs):
        if not doc:
            continue
        freq = Counter(doc)
        doc_len = len(doc)
        score = 0.0
        for token in query_unique:
            token_df = df.get(token, 0)
            if not token_df:
                continue
            idf = math.log(1 + (doc_count - token_df + 0.5) / (token_df + 0.5))
            tf = freq.get(token, 0)
            if not tf:
                continue
            denom = tf + k1 * (1 - b + b * doc_len / max(avg_len, 1))
            score += idf * (tf * (k1 + 1)) / max(denom, 1e-9)
        score += float(row.get("fulltext_score") or 0) * 0.2
        if score > 0:
            row["bm25_score"] = round(score, 4)
            scored.append((score, row))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in scored[:top_k]]


def _semantic_scores(query: str, texts: list[str]) -> list[float] | None:
    """Косинусная близость вопроса к текстам через bge-m3. None при ошибке."""
    try:
        import numpy as np

        from backend.config import EMBEDDING_BACKEND

        if EMBEDDING_BACKEND == "onnx":
            from backend.pipeline.embedder_onnx import compute_embeddings_onnx

            vectors = compute_embeddings_onnx([query] + texts, batch_size=16)
        else:
            from backend.pipeline.embedder import _get_model

            model = _get_model()
            vectors = model.encode(
                [query] + texts,
                batch_size=32,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        matrix = np.asarray(vectors, dtype=np.float32)
        query_vec = matrix[0]
        doc_vecs = matrix[1:]
        norms = np.linalg.norm(doc_vecs, axis=1) * (np.linalg.norm(query_vec) or 1.0)
        sims = (doc_vecs @ query_vec) / np.maximum(norms, 1e-9)
        return [float(value) for value in sims]
    except Exception:
        return None


def _base_select(filters: dict[str, list[str]], params: dict[str, Any], use_filters: bool) -> str:
    category_sql = "COALESCE(NULLIF(category, ''), NULLIF(group_name, ''), 'Другое')"
    severity_sql = "COALESCE(NULLIF(severity, ''), 'MEDIUM')"
    where = [
        "run_id = :run_id",
        "is_problem = true",
        "incident_text IS NOT NULL",
        "incident_text <> ''",
    ]
    if use_filters:
        for clause in (
            _in_filter_sql("municipality", filters.get("municipalities") or [], "muni", params),
            _in_filter_sql(category_sql, filters.get("categories") or [], "cat", params),
            _in_filter_sql(severity_sql, filters.get("severities") or [], "sev", params),
        ):
            if clause:
                where.append(clause)
    text_vector = f"to_tsvector('{FTS_CONFIG}', COALESCE(incident_text, ''))"
    text_query = f"plainto_tsquery('{FTS_CONFIG}', :query)"
    select_sql = f"""
        SELECT id, municipality,
               {category_sql} AS category,
               {severity_sql} AS severity,
               group_name, incident_type, outcome, incident_text,
               ts_rank_cd({text_vector}, {text_query}) AS fulltext_score
        FROM appeals
        WHERE {' AND '.join(where)}
    """
    return select_sql


async def _keyword_rows(
    db: AsyncSession,
    run_id: int,
    query_tokens: list[str],
    filters: dict[str, list[str]],
    use_filters: bool,
) -> list[dict[str, Any]]:
    """Полнотекстовый поиск (с ILIKE-фолбэком). Без эмбеддингов."""
    params: dict[str, Any] = {
        "run_id": run_id,
        "query": " ".join(query_tokens[:16]),
        # OR-запрос: письмо подходит, если содержит хотя бы одно слово темы.
        # Точность обеспечивает BM25-ранжирование уже после отбора кандидатов.
        "tsq": " | ".join(query_tokens[:16]),
        "limit": RAG_MAX_CANDIDATES,
    }
    base = _base_select(filters, params, use_filters)
    text_vector = f"to_tsvector('{FTS_CONFIG}', COALESCE(incident_text, ''))"
    text_query = f"to_tsquery('{FTS_CONFIG}', :tsq)"

    # Запрос состоит только из фильтров (например, «Проблемы ЖКХ в Омске»):
    # тематических слов нет — возвращаем весь срез по категории/району.
    if not query_tokens and use_filters:
        try:
            result = await db.execute(
                text(f"""
                    {base}
                    ORDER BY CASE severity
                        WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3
                    END, id
                    LIMIT :limit
                """),
                params,
            )
            return [dict(row._mapping) for row in result.fetchall()]
        except Exception:
            await db.rollback()
            return []
    if not query_tokens:
        return []

    try:
        result = await db.execute(
            text(f"""
                {base}
                  AND {text_vector} @@ {text_query}
                ORDER BY fulltext_score DESC, id
                LIMIT :limit
            """),
            params,
        )
        rows = [dict(row._mapping) for row in result.fetchall()]
    except Exception:
        await db.rollback()
        rows = []

    if rows:
        return rows

    like_parts = []
    for index, token in enumerate(query_tokens[:8]):
        key = f"like_{index}"
        params[key] = f"%{token}%"
        like_parts.append(f"incident_text ILIKE :{key}")
    if not like_parts:
        return []
    try:
        result = await db.execute(
            text(f"""
                {base}
                  AND ({' OR '.join(like_parts)})
                ORDER BY id
                LIMIT :limit
            """),
            params,
        )
        return [dict(row._mapping) for row in result.fetchall()]
    except Exception:
        await db.rollback()
        return []


async def _vector_rows(
    db: AsyncSession,
    run_id: int,
    message: str,
    filters: dict[str, list[str]],
    use_filters: bool,
) -> list[dict[str, Any]]:
    """Семантический фолбэк: слова не нашлись — ищем по смыслу через bge-m3."""
    params: dict[str, Any] = {
        "run_id": run_id,
        "query": message,
        "semantic_limit": RAG_SEMANTIC_POOL,
    }
    base = _base_select(filters, params, use_filters)
    try:
        result = await db.execute(
            text(f"""
                {base}
                ORDER BY CASE severity
                    WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3
                END, id
                LIMIT :semantic_limit
            """),
            params,
        )
        pool = [dict(row._mapping) for row in result.fetchall()]
    except Exception:
        await db.rollback()
        pool = []
    if not pool:
        return []
    pool_texts = [str(row.get("incident_text") or "")[:1200] for row in pool]
    scores = await asyncio.to_thread(_semantic_scores, message, pool_texts)
    if not scores or len(scores) != len(pool):
        return []
    ranked = []
    for cosine, row in sorted(zip(scores, pool), key=lambda item: item[0], reverse=True):
        if cosine < RAG_SEMANTIC_MIN:
            continue
        row["semantic_score"] = round(float(cosine), 4)
        ranked.append(row)
        if len(ranked) >= RAG_RANKED_POOL:
            break
    return ranked


async def _retrieve(
    db: AsyncSession,
    run_id: int,
    message: str,
    filters: dict[str, list[str]],
) -> tuple[list[dict[str, Any]], bool, dict[str, Any]]:
    """Полнотекстовый поиск с фолбэком на векторный.

    Возвращает (ranked, used_vector, meta):
    - ranked       — топ-пул для LLM и примеров-цитат;
    - used_vector  — был ли использован семантический поиск;
    - meta         — параметры запроса (topic_tokens, use_filters) для точной
                     агрегации сводки и полной Excel-выгрузки без обрезки.
    """
    query_tokens = _rag_tokens(message)
    has_filters = any(filters.get(key) for key in ("municipalities", "categories", "severities"))
    meta = {"topic_tokens": [], "use_filters": False}
    if not query_tokens and not has_filters:
        return [], False, meta

    # Слова, ставшие фильтром (категория/район), убираем из текстового запроса.
    topic_tokens = _strip_filter_terms(query_tokens, filters)
    meta["topic_tokens"] = topic_tokens

    # Запрос — это только категория/район («Проблемы ЖКХ в Омске»):
    # возвращаем весь срез по фильтрам, без сужения по тексту.
    if not topic_tokens and has_filters:
        rows = await _keyword_rows(db, run_id, [], filters, use_filters=True)
        if rows:
            meta["use_filters"] = True
            return rows[:RAG_RANKED_POOL], False, meta

    # 1. Полнотекстовый поиск с учётом фильтров.
    rows = await _keyword_rows(db, run_id, topic_tokens, filters, use_filters=has_filters)
    effective_filters = has_filters
    # 2. Если фильтры всё отсекли — повторяем полнотекстовый без них.
    if not rows and has_filters:
        rows = await _keyword_rows(db, run_id, topic_tokens, filters, use_filters=False)
        effective_filters = False
    if rows:
        ranked = _bm25_rank(rows, topic_tokens, top_k=RAG_RANKED_POOL) or rows[:RAG_RANKED_POOL]
        meta["use_filters"] = effective_filters
        return ranked, False, meta

    # 3. По словам не нашли — векторный (семантический) поиск.
    ranked = await _vector_rows(db, run_id, message, filters, use_filters=has_filters)
    if not ranked and has_filters:
        ranked = await _vector_rows(db, run_id, message, filters, use_filters=False)
    return ranked[:RAG_RANKED_POOL], True, meta


def _build_citations(hits: list[dict[str, Any]], query_tokens: list[str]) -> list[dict[str, Any]]:
    citations = []
    for index, row in enumerate(hits[:RAG_TOP_K], 1):
        citations.append({
            "rank": index,
            "appeal_id": row.get("id"),
            "municipality": row.get("municipality"),
            "category": row.get("category"),
            "severity": row.get("severity"),
            "score": row.get("semantic_score") or row.get("bm25_score"),
            "excerpt": _excerpt_for_terms(row.get("incident_text") or "", query_tokens),
        })
    return citations


def _summary_table(hits: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    """Небольшая сводная таблица: найденные обращения по районам."""
    if not hits:
        return [], []
    grouped: dict[str, dict[str, Any]] = {}
    for row in hits:
        muni = row.get("municipality") or "Не указан"
        entry = grouped.setdefault(
            muni, {"municipality": muni, "appeal_count": 0, "severe_count": 0}
        )
        entry["appeal_count"] += 1
        if (row.get("severity") or "").upper() in {"CRITICAL", "HIGH"}:
            entry["severe_count"] += 1
    rows = sorted(grouped.values(), key=lambda item: -item["appeal_count"])
    return ["municipality", "appeal_count", "severe_count"], rows


def _retrieval_where(
    topic_tokens: list[str],
    filters: dict[str, list[str]],
    use_filters: bool,
    params: dict[str, Any],
) -> tuple[list[str], str]:
    """Собираем WHERE так же, как в полнотекстовом поиске (для агрегации/выгрузки)."""
    category_sql = "COALESCE(NULLIF(category, ''), NULLIF(group_name, ''), 'Другое')"
    severity_sql = "COALESCE(NULLIF(severity, ''), 'MEDIUM')"
    where = [
        "run_id = :run_id",
        "is_problem = true",
        "incident_text IS NOT NULL",
        "incident_text <> ''",
    ]
    if use_filters:
        for clause in (
            _in_filter_sql("municipality", filters.get("municipalities") or [], "muni", params),
            _in_filter_sql(category_sql, filters.get("categories") or [], "cat", params),
            _in_filter_sql(severity_sql, filters.get("severities") or [], "sev", params),
        ):
            if clause:
                where.append(clause)
    if topic_tokens:
        params["tsq"] = " | ".join(topic_tokens[:16])
        where.append(
            f"to_tsvector('{FTS_CONFIG}', COALESCE(incident_text, '')) "
            f"@@ to_tsquery('{FTS_CONFIG}', :tsq)"
        )
    return where, severity_sql


async def _aggregate_summary(
    db: AsyncSession,
    run_id: int,
    topic_tokens: list[str],
    filters: dict[str, list[str]],
    use_filters: bool,
) -> tuple[list[str], list[dict[str, Any]], int]:
    """Точная сводка по районам через агрегацию в БД (без обрезки лимитом).

    Считает ВСЕ подходящие обращения, поэтому числа совпадают с дашбордом,
    а доля критичных/высоких не искажается срезом по severity.
    """
    params: dict[str, Any] = {"run_id": run_id}
    where, severity_sql = _retrieval_where(topic_tokens, filters, use_filters, params)
    sql = f"""
        SELECT municipality,
               COUNT(*) AS appeal_count,
               COUNT(*) FILTER (WHERE {severity_sql} IN ('CRITICAL', 'HIGH')) AS severe_count
        FROM appeals
        WHERE {' AND '.join(where)}
        GROUP BY municipality
        ORDER BY appeal_count DESC
    """
    try:
        result = await db.execute(text(sql), params)
        rows = [
            {
                "municipality": row[0] or "Не указан",
                "appeal_count": int(row[1] or 0),
                "severe_count": int(row[2] or 0),
            }
            for row in result.fetchall()
        ]
    except Exception:
        await db.rollback()
        return [], [], 0
    total = sum(row["appeal_count"] for row in rows)
    return ["municipality", "appeal_count", "severe_count"], rows, total


async def _export_all_rows(
    db: AsyncSession,
    run_id: int,
    topic_tokens: list[str],
    filters: dict[str, list[str]],
    use_filters: bool,
    limit: int = RAG_EXPORT_HARD_LIMIT,
) -> list[dict[str, Any]]:
    """Все письма по теме для Excel — без обрезки топ-пулом (только лимит безопасности)."""
    params: dict[str, Any] = {"run_id": run_id, "limit": limit}
    where, severity_sql = _retrieval_where(topic_tokens, filters, use_filters, params)
    category_sql = "COALESCE(NULLIF(category, ''), NULLIF(group_name, ''), 'Другое')"
    sql = f"""
        SELECT id, municipality,
               {category_sql} AS category,
               {severity_sql} AS severity,
               incident_text
        FROM appeals
        WHERE {' AND '.join(where)}
        ORDER BY CASE {severity_sql}
            WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3
        END, id
        LIMIT :limit
    """
    try:
        result = await db.execute(text(sql), params)
        return _export_rows([dict(row._mapping) for row in result.fetchall()])
    except Exception:
        await db.rollback()
        return []


def _export_rows(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Письма по теме для Excel-выгрузки (район, категория, тяжесть, текст)."""
    rows = []
    for row in hits:
        rows.append({
            "appeal_id": row.get("id"),
            "municipality": row.get("municipality") or "",
            "category": row.get("category") or "",
            "severity": SEVERITY_LABELS.get(row.get("severity"), row.get("severity") or ""),
            # Полный текст письма (ограничение Excel на ячейку — 32767 символов).
            "incident_text": " ".join(str(row.get("incident_text") or "").split())[:32000],
        })
    return rows


# === Формулировка ответа ===

ANSWER_SYSTEM = """Ты помощник-аналитик по обращениям граждан Омской области.
Тебе дают вопрос пользователя и список найденных обращений (район, категория, тяжесть, текст).
Ответь кратко, опираясь ТОЛЬКО на эти обращения.
Правила:
- Пиши ИСКЛЮЧИТЕЛЬНО на русском языке. Запрещены иероглифы, китайский, английский и любой другой язык.
- 2-4 предложения, прямой ответ по сути вопроса.
- Можешь обобщить, какие проблемы встречаются и в каких районах.
- Не выдумывай точные числа и факты, которых нет в данных.
- Без markdown, без списков-маркеров, без блоков кода и без ``` .
- Не упоминай внутренние механизмы (поиск, эмбеддинги, базу данных, SQL) и не описывай формат ответа.
Значение поля answer — только готовый текст ответа на русском. Верни строго JSON {"answer": "..."}.
"""

# Диапазоны иероглифов/CJK — если они есть в ответе, модель ушла не на тот язык.
_CJK_RE = re.compile(r"[\u3000-\u9fff\uff00-\uffef\u3040-\u30ff]")
# Утечка JSON в текст ответа: "…текст"}{"answer": …
_JSON_LEAK_RE = re.compile(r'\}\s*\{.*$|"\s*\}\s*$|"\s*\{.*$', re.DOTALL)

# Запросы не по теме обращений — не запускаем поиск и не показываем сводку.
_OFF_TOPIC_PHRASES = (
    "как тебя зовут", "как зовут", "твоё имя", "твое имя", "твое имя",
    "кто ты", "ты кто", "что ты", "ты бот", "ты робот", "ты ии", "ты ai",
    "представься", "как тебя называют", "как тебя называют",
    "привет", "здравствуй", "здравствуйте", "добрый день", "доброе утро",
    "добрый вечер", "спасибо", "благодарю", "пока", "до свидания",
    "как дела", "что умеешь", "что можешь", "чем можешь помочь",
    "help", "hello", "hi ",
)
# Слова, по которым одни не ищут обращения (имя бота, приветствие и т. п.).
_CHITCHAT_TOKENS = {
    "тебя", "зовут", "имя", "имени", "кто", "бот", "робот", "привет",
    "здравствуй", "здравствуйте", "спасибо", "благодарю", "пока",
    "дела", "умеешь", "можешь", "hello", "help",
}


def _is_off_topic(message: str) -> bool:
    """Вопрос не про обращения — не запускаем поиск."""
    low = " ".join(str(message or "").casefold().replace("ё", "е").split())
    if not low or len(low) > 120:
        return False
    if any(phrase in low for phrase in _OFF_TOPIC_PHRASES):
        return True
    tokens = _rag_tokens(message)
    plain = _plain_tokens(message)
    if tokens and not all(token in _CHITCHAT_TOKENS for token in tokens):
        return False
    if plain and not all(token in _CHITCHAT_TOKENS for token in plain):
        return False
    return bool(plain or tokens)


def _off_topic_answer(message: str) -> str:
    low = message.casefold().replace("ё", "е")
    if any(p in low for p in ("зовут", "имя", "кто ты", "ты кто", "представься")):
        return (
            "Я помощник по анализу обращений граждан Омской области. "
            "Задайте вопрос о проблемах, районах или категориях — "
            "например: «Проблемы ЖКХ в Омске» или «Освещение улиц»."
        )
    if any(p in low for p in ("привет", "здравств", "добрый", "доброе")):
        return (
            "Здравствуйте! Я помогаю искать и обобщать обращения граждан по темам и районам. "
            "Сформулируйте вопрос по проблеме — например: «Проблемы с дорогами»."
        )
    return (
        "Этот чат отвечает только по обращениям граждан Омской области. "
        "Уточните тему, район или категорию — например: «Холодные батареи в Омске»."
    )


def _llm_context(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    context = []
    for row in hits[:RAG_LLM_CONTEXT]:
        context.append({
            "municipality": row.get("municipality") or "не указан",
            "category": row.get("category") or "Другое",
            "severity": SEVERITY_LABELS.get(row.get("severity"), row.get("severity") or "—"),
            "text": " ".join(str(row.get("incident_text") or "").split())[:600],
        })
    return context


async def _llm_answer(message: str, hits: list[dict[str, Any]], budget_seconds: float) -> str | None:
    """LLM-резюме найденного с жёстким дедлайном. None — не успели/ошибка."""
    if budget_seconds < 1.0 or not hits:
        return None
    payload = {
        "question": message,
        "found_appeals": _llm_context(hits),
        "found_count": len(hits),
    }
    try:
        raw = await asyncio.wait_for(
            _call_ollama(
                json.dumps(payload, ensure_ascii=False, default=str),
                ANSWER_SYSTEM,
                timeout=max(int(budget_seconds), 1),
                temperature=0.1,
                model=CHAT_LLM_MODEL,
                num_predict=320,
                json_mode=True,
            ),
            timeout=budget_seconds,
        )
    except Exception:
        return None
    answer = _sanitize_answer(_extract_answer_text(raw))
    # Если модель ответила не по-русски / иероглифами — считаем попытку неудачной.
    return answer if _answer_is_valid(answer) else None


def _retrieval_answer(message: str, hits: list[dict[str, Any]]) -> str:
    """Ответ без LLM: краткая честная сводка по найденным обращениям."""
    if not hits:
        return (
            "По этому запросу подходящих обращений не нашлось. "
            "Попробуйте переформулировать или уточнить район либо категорию."
        )
    parts = ["По запросу нашлись релевантные обращения — ниже наиболее близкие примеры."]
    muni = Counter(row.get("municipality") for row in hits if row.get("municipality"))
    if muni:
        top_muni, _ = muni.most_common(1)[0]
        parts.append(f"Чаще всего среди найденных упоминается {top_muni}.")
    severe = sum(1 for row in hits if (row.get("severity") or "").upper() in {"CRITICAL", "HIGH"})
    if severe:
        parts.append(f"Среди найденных {severe} критической или высокой тяжести.")
    return " ".join(parts)


# Фрагменты, по которым распознаём эхо системного промта в ответе модели.
_ANSWER_LEAK_MARKERS = (
    "верни строго json",
    "никакого текста вне json",
    "found_appeals",
    "found_count",
    "select ",
    "run_id",
    "bm25",
    "полнотекстовый поиск",
    "эмбеддинг",
    "system prompt",
    "системный промт",
)


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


def _extract_answer_text(raw: str) -> str:
    """Достаём поле answer даже из обрезанного или двойного JSON."""
    raw = str(raw or "").strip()
    if not raw:
        return ""
    parsed = _parse_json_object(raw)
    answer = str(parsed.get("answer") or "").strip()
    if answer:
        return answer
    match = re.search(r'"answer"\s*:\s*"((?:[^"\\]|\\.)*)', raw, re.DOTALL)
    if match:
        fragment = match.group(1)
        try:
            return json.loads(f'"{fragment}"')
        except Exception:
            return fragment.replace('\\"', '"').replace("\\n", "\n").strip()
    if not raw.startswith("{"):
        return raw
    return ""


def _sanitize_answer(answer: str) -> str:
    text_value = str(answer or "").strip()
    if text_value.startswith("{"):
        inner = _extract_answer_text(text_value)
        if inner:
            text_value = inner
    # Обрезаем утечку JSON в конце: «…не}{"answer":…»
    text_value = _JSON_LEAK_RE.sub("", text_value)
    # Отрезаем всё, начиная с блока кода / json — модель иногда дублирует ответ туда.
    for marker in ("```", '{"answer', "以下是"):
        idx = text_value.find(marker)
        if idx > 0:
            text_value = text_value[:idx]
    # Убираем иероглифы/CJK, если просочились.
    text_value = _CJK_RE.sub("", text_value)
    kept_lines = []
    for line in text_value.splitlines():
        lowered = line.casefold()
        if any(marker in lowered for marker in _ANSWER_LEAK_MARKERS):
            continue
        kept_lines.append(line)
    text_value = "\n".join(kept_lines).strip().strip('"').strip()
    # Хвостовые скобки/кавычки от битого JSON.
    text_value = text_value.rstrip(' \t\n\r"\'{}')
    return text_value


def _answer_is_valid(answer: str) -> bool:
    """Ответ годен, если он не пустой, на русском и без иероглифов/мусора."""
    if len(answer) < 15:
        return False
    if _CJK_RE.search(answer):
        return False
    if "{" in answer or "}" in answer:
        return False
    # Должны присутствовать кириллические буквы (а не только латиница/цифры).
    if not re.search(r"[а-яА-Я]", answer):
        return False
    return True


# Фразы, сбрасывающие географический контекст («покажи по всем районам»).
_CONTEXT_GEO_RESET = (
    "по всем", "во всех", "везде", "все районы", "всех районах",
    "по области", "по всей области", "в целом", "по региону",
)


def _apply_context_filters(message: str, filters: dict[str, list[str]], ctx: dict) -> dict[str, list[str]]:
    """Подмешиваем контекст прошлых вопросов в неуказанные измерения.

    Пример: «Проблемы ЖКХ в Омске» → затем «а дороги?» унаследует район Омск,
    а «а в Тарском районе?» унаследует категорию ЖКХ.
    """
    low = message.casefold().replace("ё", "е")
    geo_reset = any(phrase in low for phrase in _CONTEXT_GEO_RESET)
    if geo_reset:
        ctx["last_municipalities"] = []
        ctx["last_municipality"] = None

    if not filters.get("municipalities") and not geo_reset and ctx.get("last_municipalities"):
        filters["municipalities"] = list(ctx["last_municipalities"])
    if not filters.get("categories") and ctx.get("last_categories"):
        filters["categories"] = list(ctx["last_categories"])
    if not filters.get("severities") and ctx.get("last_severity"):
        filters["severities"] = [ctx["last_severity"]]
    return filters


def _update_context_from_filters(ctx: dict, filters: dict[str, list[str]]) -> None:
    municipalities = filters.get("municipalities") or []
    categories = filters.get("categories") or []
    severities = filters.get("severities") or []
    if municipalities:
        ctx["last_municipalities"] = municipalities
        ctx["last_municipality"] = municipalities[0] if len(municipalities) == 1 else None
    if categories:
        ctx["last_categories"] = categories
        ctx["last_category"] = categories[0] if len(categories) == 1 else None
    if len(severities) == 1:
        ctx["last_severity"] = severities[0]


# === Эндпоинты ===

@router.post("/chat")
async def chat(
    run_id: int,
    message: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Полнотекстовый/векторный поиск + LLM-резюме с жёстким дедлайном."""
    user_id = current_user.id
    ctx = await _get_ctx(db, run_id, user_id)

    if _is_off_topic(message):
        answer = _off_topic_answer(message)
        entry = {
            "question": message,
            "answer": answer,
            "citations": [],
            "columns": [],
            "data": [],
            "export_rows": None,
            "export_query": None,
            "found_count": 0,
            "kind": "off_topic",
            "suggestions": [],
            "fast": True,
        }
        ctx["history"].append(entry)
        ctx["history"] = ctx["history"][-10:]
        await _save_ctx(db, run_id, user_id, ctx)
        return {
            "answer": answer,
            "narration": answer,
            "fallback": answer,
            "citations": [],
            "columns": [],
            "data": [],
            "found_count": 0,
            "kind": "off_topic",
            "suggestions": [],
            "fast": True,
            "context": _context_response(ctx),
        }

    started = time.monotonic()
    query_tokens = _rag_tokens(message)

    filters = await _detect_filters(db, run_id, message)
    # Учитываем контекст диалога: наследуем район/категорию из прошлых вопросов.
    filters = _apply_context_filters(message, filters, ctx)
    hits, used_vector, meta = await _retrieve(db, run_id, message, filters)
    citations = _build_citations(hits, query_tokens)

    remaining = CHAT_ANSWER_DEADLINE_SECONDS - (time.monotonic() - started)
    llm_attempted = bool(hits) and remaining >= 1.0
    answer = await _llm_answer(message, hits, remaining) if llm_attempted else None

    fast = answer is None
    if fast:
        answer = _retrieval_answer(message, hits)
        if llm_attempted:
            answer += f" (модель не успела ответить за {CHAT_ANSWER_DEADLINE_SECONDS} c — показаны найденные обращения)"

    answer = _sanitize_answer(answer) or _retrieval_answer(message, hits)
    _update_context_from_filters(ctx, filters)

    topic_tokens = meta.get("topic_tokens") or []
    use_filters = bool(meta.get("use_filters"))
    if used_vector:
        # Семантический поиск нельзя посчитать агрегацией — берём найденный пул.
        columns, data = _summary_table(hits)
        found_count = len(hits)
        export_rows = _export_rows(hits)
        export_query = None
    else:
        # Сводка и счётчик — точной агрегацией по БД (числа как на дашборде,
        # без искажения долей критичных из-за обрезки лимитом).
        columns, data, found_count = await _aggregate_summary(
            db, run_id, topic_tokens, filters, use_filters
        )
        # Полную выгрузку собираем при скачивании, чтобы не раздувать память диалога.
        export_rows = None
        export_query = {
            "topic_tokens": topic_tokens,
            "filters": {
                "municipalities": filters.get("municipalities") or [],
                "categories": filters.get("categories") or [],
                "severities": filters.get("severities") or [],
            },
            "use_filters": use_filters,
        }

    kind = "vector_rag" if used_vector else "fulltext_rag"
    entry = {
        "question": message,
        "answer": answer,
        "citations": citations,
        "columns": columns,
        "data": data,
        "export_rows": export_rows,
        "export_query": export_query,
        "found_count": found_count,
        "kind": kind,
        "suggestions": [],
        "fast": fast,
    }
    ctx["history"].append(entry)
    ctx["history"] = ctx["history"][-10:]
    await _save_ctx(db, run_id, user_id, ctx)

    return {
        "answer": answer,
        "narration": answer,
        "fallback": answer,
        "citations": citations,
        "columns": columns,
        "data": data,
        "found_count": found_count,
        "kind": kind,
        "suggestions": [],
        "fast": fast,
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
    # Полнотекстовый/фильтровый запрос — заново тянем ВСЕ письма по теме из БД.
    export_query = last.get("export_query")
    letters = None
    if export_query:
        letters = await _export_all_rows(
            db,
            run_id,
            export_query.get("topic_tokens") or [],
            export_query.get("filters") or {},
            bool(export_query.get("use_filters")),
        )
    # Иначе (семантический поиск) — сохранённый пул писем.
    if not letters:
        letters = last.get("export_rows")
    if not letters:
        letters = [
            {
                "appeal_id": citation.get("appeal_id"),
                "municipality": citation.get("municipality") or "",
                "category": citation.get("category") or "",
                "severity": SEVERITY_LABELS.get(citation.get("severity"), citation.get("severity") or ""),
                "incident_text": citation.get("excerpt") or "",
            }
            for citation in (last.get("citations") or [])
        ]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Обращения по теме"

    ws.append(["Вопрос:", last.get("question", "")])
    ws.append(["Ответ:", last.get("answer", "")])
    ws.append(["Найдено обращений:", last.get("found_count", len(letters))])
    ws.append([])

    ws.append(["#", "ID", "Район", "Категория", "Тяжесть", "Текст обращения"])
    for index, letter in enumerate(letters, 1):
        ws.append([
            index,
            letter.get("appeal_id", ""),
            letter.get("municipality", ""),
            letter.get("category", ""),
            letter.get("severity", ""),
            letter.get("incident_text", ""),
        ])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=chat.xlsx; filename*=UTF-8''%D0%A0%D0%B5%D0%B7%D1%83%D0%BB%D1%8C%D1%82%D0%B0%D1%82%20%D0%B7%D0%B0%D0%BF%D1%80%D0%BE%D1%81%D0%B0.xlsx"},
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
