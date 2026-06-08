"""Configuration constants for the complaint classifier."""
import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


# Database
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://classifier:classifier@localhost:5432/classifier_db")
DATABASE_URL_SYNC = os.getenv("DATABASE_URL_SYNC", "postgresql://classifier:classifier@localhost:5432/classifier_db")

# Auth
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-superbober-jwt-secret-change-me")
JWT_EXPIRE_HOURS = _int_env("JWT_EXPIRE_HOURS", 12)
DEFAULT_ADMIN_USERNAME = os.getenv("DEFAULT_ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.getenv("DEFAULT_ADMIN_PASSWORD", "admin123")
DEFAULT_USER_USERNAME = os.getenv("DEFAULT_USER_USERNAME", "user")
DEFAULT_USER_PASSWORD = os.getenv("DEFAULT_USER_PASSWORD", "user123")

# Ollama
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3:8b")
LLM_FALLBACK_MODEL = "qwen2.5:7b"

# Embedding
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-m3")
EMBEDDING_BATCH_SIZE = _int_env("EMBEDDING_BATCH_SIZE", 64)
EMBEDDING_PROGRESS_CHUNK_SIZE = _int_env("EMBEDDING_PROGRESS_CHUNK_SIZE", 2048)
EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "cuda")
# Cap sequence length: attention cost is quadratic in length, and complaint
# text rarely needs more than a few hundred tokens for classification.
EMBEDDING_MAX_SEQ_LENGTH = _int_env("EMBEDDING_MAX_SEQ_LENGTH", 320)
# Use fp16 on GPU for a large speedup with negligible quality loss.
EMBEDDING_USE_FP16 = _bool_env("EMBEDDING_USE_FP16", True)

# LoRA adapter path: если задан, загружается поверх базовой bge-m3 модели.
# Адаптер обучается через `python -m backend.scripts.finetune_bge`.
_embedding_lora_path = os.getenv("EMBEDDING_LORA_PATH", "").strip()
if _embedding_lora_path and not os.path.isabs(_embedding_lora_path):
    _embedding_lora_path = str(PROJECT_ROOT / _embedding_lora_path)
EMBEDDING_LORA_PATH = _embedding_lora_path

# Skip the slow HuggingFace Hub update check on every startup (model is cached
# locally after the first download). Set EMBEDDING_HF_OFFLINE=0 to re-enable.
if _bool_env("EMBEDDING_HF_OFFLINE", True):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

# Embedding cache on disk: a repeat run on the same file skips recomputation.
EMBEDDING_CACHE_ENABLED = _bool_env("EMBEDDING_CACHE_ENABLED", True)

# Column indices (0-based) for pandas
COL_INDICES = {
    "date_created": 17,    # Дата создания (для timeline)
    "date_closed": 18,     # Дата закрытия
    "group": 19,           # Группа тем
    "municipality": 22,    # Муниципалитет
    "incident_type": 28,   # Тип инцидента
    "outcome": 29,         # Итог
    "incident_text": 34,   # Текст инцидента
}

# Row-level filters applied after loading
EXCLUDE_INCIDENT_TYPES = ["Нерешаемый"]
EXCLUDE_OUTCOMES = ["Закрыто", "Разъяснено", "Решено", "Перенаправлено"]

# Информационные письма — это не жалобы/проблемы. Обращения с такими типами
# инцидента никогда не помечаются как проблемные (не попадают в кластеры,
# карту, тяжесть и т.д.), даже если LLM решил иначе.
NON_PROBLEM_INCIDENT_TYPES = ["Информационный"]

# Classification
BOOTSTRAP_SAMPLE_SIZE = _int_env("BOOTSTRAP_SAMPLE_SIZE", 500)
LOW_CONFIDENCE_THRESHOLD = _float_env("LOW_CONFIDENCE_THRESHOLD", 0.55)
LLM_BATCH_SIZE = _int_env("LLM_BATCH_SIZE", 30)
LLM_CONCURRENCY = _int_env("LLM_CONCURRENCY", 4)
LLM_TIMEOUT_SECONDS = _int_env("LLM_TIMEOUT_SECONDS", 240)
LLM_VERIFY_BATCH_SIZE = _int_env("LLM_VERIFY_BATCH_SIZE", 15)
LLM_VERIFY_MAX_RECORDS = _int_env("LLM_VERIFY_MAX_RECORDS", 20)
LLM_VERIFY_CONCURRENCY = _int_env("LLM_VERIFY_CONCURRENCY", LLM_CONCURRENCY)

# Classifier cache (skip bootstrap on repeat runs)
CLASSIFIER_CACHE_DIR = str(PROJECT_ROOT / "data" / "cache")

# Embedding cache (skip recomputation on repeat runs of the same texts)
EMBEDDING_CACHE_DIR = os.path.join(CLASSIFIER_CACHE_DIR, "embeddings")

# LLM result cache (cluster names, summaries, repeated bootstrap/verification batches)
LLM_CACHE_DIR = os.path.join(CLASSIFIER_CACHE_DIR, "llm")
LLM_CACHE_ENABLED = os.getenv("LLM_CACHE_ENABLED", "1") != "0"

# DBSCAN
DBSCAN_EPS = _float_env("DBSCAN_EPS", 0.35)
DBSCAN_MIN_SAMPLES = _int_env("DBSCAN_MIN_SAMPLES", 2)
DBSCAN_N_JOBS = _int_env("DBSCAN_N_JOBS", -1)
DBSCAN_SPLIT_BY_CATEGORY = _bool_env("DBSCAN_SPLIT_BY_CATEGORY", True)
# Слияние кластеров-синонимов: если центроиды двух кластеров одного района
# ближе этого косинусного расстояния — объединяем их в один.
CLUSTER_MERGE_DISTANCE = _float_env("CLUSTER_MERGE_DISTANCE", 0.12)

# LLM naming / report generation limits. Fast defaults for 400k rows.
CLUSTER_NAME_TOP_PER_MUNICIPALITY = _int_env("CLUSTER_NAME_TOP_PER_MUNICIPALITY", 3)
CLUSTER_NAME_MAX_TOTAL = _int_env("CLUSTER_NAME_MAX_TOTAL", 10)
CLUSTER_NAME_CONCURRENCY = _int_env("CLUSTER_NAME_CONCURRENCY", LLM_CONCURRENCY)
SUMMARY_TOP_N = _int_env("SUMMARY_TOP_N", 0)

# Persistence. Embeddings are needed for clustering during the run, not for reports/chat.
STORE_EMBEDDINGS_IN_DB = _bool_env("STORE_EMBEDDINGS_IN_DB", False)
DB_SAVE_CHUNK_SIZE = _int_env("DB_SAVE_CHUNK_SIZE", 5000)

# Severity levels
SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]

# Ranking weights
RANK_WEIGHT_APPEALS = 0.5
RANK_WEIGHT_TOPIC_DIVERSITY = 0.3
RANK_WEIGHT_SEVERITY = 0.2
SEVERITY_WEIGHTS = {"CRITICAL": 1.0, "HIGH": 0.75, "MEDIUM": 0.5, "LOW": 0.25}

# Categories (from real dataset "Группа тем" column)
CATEGORIES = [
    "ЖКХ",
    "Дороги",
    "Образование",
    "Физическая культура и спорт",
    "Здравоохранение",
    "Благоустройство",
    "Общественный транспорт",
    "Социальное обслуживание и защита",
    "Военная служба",
    "Безопасность и правопорядок",
    "Энергетика",
    "Экология",
    "Обращение с отходами",
    "Связь и телевидение",
    "Строительство и архитектура",
    "Культура",
    "Земельные отношения",
    "Торговля и услуги",
    "Трудовые отношения",
    "Миграционная политика",
    "Туризм",
    "Молодежная политика",
    "Имущественные отношения",
    "Регистрация актов гражд. состояния",
    "Ветеринария",
    "Другое",
]

# Upload / output
UPLOAD_DIR = str(PROJECT_ROOT / "data" / "input")
OUTPUT_DIR = str(PROJECT_ROOT / "data" / "output")
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB
