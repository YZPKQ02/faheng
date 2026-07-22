from datetime import date, datetime, timezone
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

from app.database import Base


def uid() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class FactStatus(StrEnum):
    USER_STATED = "user_stated"
    EVIDENCE_SUPPORTED = "evidence_supported"
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    DISPUTED = "disputed"
    UNKNOWN = "unknown"


class LegalVersionReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    PUBLISHED = "published"
    REJECTED = "rejected"


class CaseFile(Base):
    __tablename__ = "case_files"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(100), default="local", index=True)
    owner_id: Mapped[str] = mapped_column(String(200), default="local", index=True)
    title: Mapped[str] = mapped_column(String(200), default="新的劳动争议")
    case_type: Mapped[str] = mapped_column(String(50), default="labor_dispute")
    region: Mapped[str] = mapped_column(String(100), default="中国大陆")
    stage: Mapped[str] = mapped_column(String(50), default="intake")
    goal: Mapped[str] = mapped_column(Text, default="了解权利与下一步行动")
    risk_level: Mapped[str] = mapped_column(String(20), default="medium")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    facts: Mapped[list["Fact"]] = relationship(cascade="all, delete-orphan")
    evidence: Mapped[list["EvidenceItem"]] = relationship(cascade="all, delete-orphan")
    messages: Mapped[list["Message"]] = relationship(cascade="all, delete-orphan")
    analyses: Mapped[list["AnalysisConclusion"]] = relationship(cascade="all, delete-orphan")


class Fact(Base):
    __tablename__ = "facts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    case_id: Mapped[str] = mapped_column(ForeignKey("case_files.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text)
    occurred_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(100), default="user")
    status: Mapped[str] = mapped_column(String(20), default=FactStatus.USER_STATED)


class EvidenceItem(Base):
    __tablename__ = "evidence_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    case_id: Mapped[str] = mapped_column(ForeignKey("case_files.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    evidence_type: Mapped[str] = mapped_column(String(80))
    purpose: Mapped[str] = mapped_column(Text)
    holder: Mapped[str] = mapped_column(String(100), default="劳动者")
    authenticity: Mapped[str] = mapped_column(String(30), default="unverified")
    gap: Mapped[str | None] = mapped_column(Text, nullable=True)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    case_id: Mapped[str] = mapped_column(ForeignKey("case_files.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    agent: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LegalAuthority(Base):
    __tablename__ = "legal_authorities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(String(300))
    article: Mapped[str] = mapped_column(String(80))
    content: Mapped[str] = mapped_column(Text)
    level: Mapped[str] = mapped_column(String(50))
    region: Mapped[str] = mapped_column(String(100), default="全国")
    effective_on: Mapped[date] = mapped_column(Date)
    expired_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_url: Mapped[str] = mapped_column(String(500))
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)


class LegalDocument(Base):
    """Stable identity for an official legal instrument across versions."""

    __tablename__ = "legal_documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    canonical_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    authority_type: Mapped[str] = mapped_column(String(50), default="statute")
    level: Mapped[str] = mapped_column(String(50))
    jurisdiction: Mapped[str] = mapped_column(String(100), default="全国")
    issuing_body: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_url: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LegalDocumentVersion(Base):
    """Effective-dated, content-addressed version of a legal document."""

    __tablename__ = "legal_document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "content_hash", name="uq_legal_version_document_hash"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("legal_documents.id", ondelete="CASCADE"), index=True
    )
    version_label: Mapped[str] = mapped_column(String(100), default="现行版本")
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    promulgated_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_on: Mapped[date] = mapped_column(Date, index=True)
    expired_on: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    source_url: Mapped[str] = mapped_column(String(500))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    review_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    supersedes_id: Mapped[str | None] = mapped_column(
        ForeignKey("legal_document_versions.id", ondelete="SET NULL"), nullable=True
    )
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LegalChunk(Base):
    """Article/clause retrieval unit with a compatibility reference for citations."""

    __tablename__ = "legal_chunks"
    __table_args__ = (
        UniqueConstraint("version_id", "locator", name="uq_legal_chunk_version_locator"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("legal_document_versions.id", ondelete="CASCADE"), index=True
    )
    authority_id: Mapped[str | None] = mapped_column(
        ForeignKey("legal_authorities.id", ondelete="SET NULL"), nullable=True, index=True
    )
    locator: Mapped[str] = mapped_column(String(120))
    sequence: Mapped[int] = mapped_column(default=0)
    heading: Mapped[str | None] = mapped_column(String(300), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)


class LegalChunkEmbedding(Base):
    __tablename__ = "legal_chunk_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "chunk_id",
            "provider",
            "model",
            name="uq_legal_chunk_embedding_model",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    chunk_id: Mapped[str] = mapped_column(
        ForeignKey("legal_chunks.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(50), index=True)
    model: Mapped[str] = mapped_column(String(100), index=True)
    dimensions: Mapped[int]
    embedding: Mapped[list[float]] = mapped_column(
        Vector(1536).with_variant(JSON(), "sqlite")
    )
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


Index(
    "ix_legal_chunk_embeddings_hnsw_cosine",
    LegalChunkEmbedding.embedding,
    postgresql_using="hnsw",
    postgresql_ops={"embedding": "vector_cosine_ops"},
).ddl_if(dialect="postgresql")


class LegalCase(Base):
    __tablename__ = "legal_cases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    source_url: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    source_name: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(500))
    case_number: Mapped[str | None] = mapped_column(String(120), nullable=True)
    case_type: Mapped[str] = mapped_column(String(80), default="劳动争议")
    court: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decision_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    facts: Mapped[str] = mapped_column(Text)
    claims: Mapped[list[str]] = mapped_column(JSON, default=list)
    defenses: Mapped[list[str]] = mapped_column(JSON, default=list)
    issues: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    outcome: Mapped[str] = mapped_column(Text, default="")
    reasoning: Mapped[str] = mapped_column(Text, default="")
    authority_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    review_status: Mapped[str] = mapped_column(String(30), default="pending")
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AnalysisConclusion(Base):
    __tablename__ = "analysis_conclusions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    case_id: Mapped[str] = mapped_column(ForeignKey("case_files.id", ondelete="CASCADE"), index=True)
    viewpoint: Mapped[str] = mapped_column(Text)
    counterargument: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    uncertainties: Mapped[list[str]] = mapped_column(JSON, default=list)
    authority_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    reasoning_trace: Mapped[list[dict]] = mapped_column(JSON, default=list)
    quality_metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    is_current: Mapped[bool] = mapped_column(default=True)
    invalidated_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    publication_status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AgentTask(Base):
    __tablename__ = "agent_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    case_id: Mapped[str] = mapped_column(ForeignKey("case_files.id", ondelete="CASCADE"), index=True)
    task_type: Mapped[str] = mapped_column(String(80))
    agent: Mapped[str] = mapped_column(String(80), index=True)
    objective: Mapped[str] = mapped_column(Text)
    protocol_version: Mapped[str] = mapped_column(String(40), default="agent-task-v1")
    input_refs: Mapped[dict] = mapped_column(JSON, default=dict)
    constraints: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    attempt: Mapped[int] = mapped_column(default=1)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HumanReviewTask(Base):
    __tablename__ = "human_review_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    case_id: Mapped[str] = mapped_column(ForeignKey("case_files.id", ondelete="CASCADE"), index=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analysis_conclusions.id", ondelete="CASCADE"), index=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="high")
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    decision: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reviewer: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ModelDataConsent(Base):
    __tablename__ = "model_data_consents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("case_files.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    purposes: Mapped[list[str]] = mapped_column(JSON, default=list)
    data_categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    version: Mapped[int] = mapped_column(default=1)
    granted_by: Mapped[str] = mapped_column(String(200))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CasePseudonym(Base):
    __tablename__ = "case_pseudonyms"
    __table_args__ = (
        UniqueConstraint("case_id", "entity_fingerprint", name="uq_case_pseudonym_fingerprint"),
        UniqueConstraint("case_id", "pseudonym", name="uq_case_pseudonym_label"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("case_files.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    entity_fingerprint: Mapped[str] = mapped_column(String(64))
    source_length: Mapped[int]
    entity_type: Mapped[str] = mapped_column(String(30))
    pseudonym: Mapped[str] = mapped_column(String(100))
    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WorkerCounselMemory(Base):
    """Current, versioned case memory for the worker's persistent counsel persona."""

    __tablename__ = "worker_counsel_memories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("case_files.id", ondelete="CASCADE"), unique=True, index=True
    )
    agent_id: Mapped[str] = mapped_column(String(50), default="worker_counsel")
    version: Mapped[int] = mapped_column(default=1)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class SimulationSession(Base):
    __tablename__ = "simulation_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    case_id: Mapped[str] = mapped_column(ForeignKey("case_files.id", ondelete="CASCADE"), index=True)
    scenario: Mapped[str] = mapped_column(String(50))
    user_role: Mapped[str] = mapped_column(String(50))
    transcript: Mapped[list[dict]] = mapped_column(JSON, default=list)
    feedback: Mapped[list[str]] = mapped_column(JSON, default=list)
    suggested_answers: Mapped[list[str]] = mapped_column(JSON, default=list)
    assistance_mode: Mapped[str] = mapped_column(String(30), default="coach")
    counsel_agent_id: Mapped[str] = mapped_column(String(50), default="worker_counsel")
    counsel_memory_version: Mapped[int] = mapped_column(default=0)
    counsel_memory_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    completion_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class GeneratedDocument(Base):
    __tablename__ = "generated_documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    case_id: Mapped[str] = mapped_column(ForeignKey("case_files.id", ondelete="CASCADE"), index=True)
    document_type: Mapped[str] = mapped_column(String(50))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    case_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(80))
    agent: Mapped[str | None] = mapped_column(String(50), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    case_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    category: Mapped[str] = mapped_column(String(50))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
