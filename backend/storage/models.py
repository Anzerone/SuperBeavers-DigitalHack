"""SQLAlchemy ORM models."""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, Float, DateTime, JSON,
    ForeignKey, LargeBinary, Index, func
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False, default="")
    role = Column(String(20), default="user")  # 'admin' | 'user'
    created_at = Column(DateTime, default=func.now())

    runs = relationship("ProcessingRun", back_populates="user")
    filter_presets = relationship("FilterPreset", back_populates="user", cascade="all, delete-orphan")


class FilterPreset(Base):
    __tablename__ = "filter_presets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(120), nullable=False)
    filters = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="filter_presets")

    __table_args__ = (
        Index("idx_filter_presets_user_name", "user_id", "name", unique=True),
    )


class ChatMemory(Base):
    __tablename__ = "chat_memory"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("processing_runs.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    context = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_chat_memory_run_user", "run_id", "user_id", unique=True),
    )


class ProcessingRun(Base):
    __tablename__ = "processing_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    filename = Column(String(500))
    started_at = Column(DateTime, default=func.now())
    finished_at = Column(DateTime, nullable=True)
    raw_records = Column(Integer, default=0)
    total_records = Column(Integer, default=0)
    dropped_incident_type = Column(Integer, default=0)
    dropped_outcome = Column(Integer, default=0)
    problem_count = Column(Integer, default=0)
    cluster_count = Column(Integer, default=0)
    status = Column(String(20), default="pending")  # pending|running|completed|failed
    progress = Column(Float, default=0.0)
    current_step = Column(String(100), default="")
    error_message = Column(Text, nullable=True)

    user = relationship("User", back_populates="runs")
    appeals = relationship("Appeal", back_populates="run", cascade="all, delete-orphan")
    clusters = relationship("ProblemCluster", back_populates="run", cascade="all, delete-orphan")
    summaries = relationship("Summary", back_populates="run", cascade="all, delete-orphan")


class RunLoadStats(Base):
    """Агрегаты строк, отброшенных при загрузке файла (до сохранения в appeals).

    Закрытые до анализа и нерешаемые обращения не попадают в таблицу appeals,
    но нужны для аналитики «Решённые» — здесь хранятся их разрезы по районам,
    категориям, итогам и месяцам закрытия.
    """
    __tablename__ = "run_load_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("processing_runs.id", ondelete="CASCADE"), nullable=False, unique=True)
    prefiltered = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=func.now())


class Appeal(Base):
    __tablename__ = "appeals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("processing_runs.id", ondelete="CASCADE"), nullable=False)
    date_created = Column(DateTime, nullable=True)
    date_closed = Column(DateTime, nullable=True)
    group_name = Column(String(200))       # Группа тем
    municipality = Column(String(200))     # Муниципалитет
    incident_type = Column(String(200))    # Тип инцидента
    outcome = Column(String(200))          # Итог
    incident_text = Column(Text)           # Текст инцидента
    # Classification results
    is_problem = Column(Boolean, nullable=True)
    classification_method = Column(String(20))  # 'embedding' | 'llm'
    confidence = Column(Float, nullable=True)
    severity = Column(String(20), nullable=True)  # CRITICAL|HIGH|MEDIUM|LOW
    category = Column(String(100), nullable=True)
    sentiment = Column(String(20), nullable=True)  # CALM|NEUTRAL|ANGRY|DESPERATE
    sentiment_score = Column(Float, nullable=True)  # 0..1 интенсивность негатива
    # Embedding cached as bytes (numpy tobytes)
    embedding = Column(LargeBinary, nullable=True)

    run = relationship("ProcessingRun", back_populates="appeals")

    __table_args__ = (
        Index("idx_appeals_municipality", "municipality"),
        Index("idx_appeals_run_id", "run_id"),
        Index("idx_appeals_is_problem", "is_problem"),
        Index("idx_appeals_severity", "severity"),
        Index("idx_appeals_category", "category"),
    )


class ProblemCluster(Base):
    __tablename__ = "problem_clusters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("processing_runs.id", ondelete="CASCADE"), nullable=False)
    municipality = Column(String(200))
    settlement = Column(String(200), nullable=True)
    cluster_name = Column(String(500))         # LLM-generated name
    description = Column(Text, nullable=True)  # LLM-generated description
    category = Column(String(100))
    severity = Column(String(20))              # max severity in cluster
    appeal_count = Column(Integer, default=1)
    rank = Column(Integer, nullable=True)
    rank_score = Column(Float, nullable=True)
    topic_diversity = Column(Integer, nullable=True)
    centroid_text = Column(Text, nullable=True)
    example_texts = Column(JSON, nullable=True)

    run = relationship("ProcessingRun", back_populates="clusters")
    appeal_mappings = relationship("AppealClusterMap", back_populates="cluster", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_clusters_municipality", "municipality"),
        Index("idx_clusters_run_id", "run_id"),
    )


class AppealClusterMap(Base):
    __tablename__ = "appeal_cluster_map"

    id = Column(Integer, primary_key=True, autoincrement=True)
    appeal_id = Column(Integer, ForeignKey("appeals.id", ondelete="CASCADE"), nullable=False)
    cluster_id = Column(Integer, ForeignKey("problem_clusters.id", ondelete="CASCADE"), nullable=False)

    cluster = relationship("ProblemCluster", back_populates="appeal_mappings")

    __table_args__ = (
        Index("idx_acm_appeal", "appeal_id"),
        Index("idx_acm_cluster", "cluster_id"),
    )


class Summary(Base):
    __tablename__ = "summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("processing_runs.id", ondelete="CASCADE"), nullable=False)
    municipality = Column(String(200))
    rank = Column(Integer)
    problem_count = Column(Integer)
    avg_rank = Column(Float, nullable=True)
    top_issues = Column(JSON)         # [{name, count}]
    summary_text = Column(Text)       # LLM-generated summary
    centroid_excerpt = Column(Text, nullable=True)

    run = relationship("ProcessingRun", back_populates="summaries")
