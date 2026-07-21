from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CaseCreate(BaseModel):
    title: str = "新的劳动争议"
    region: str = "中国大陆"
    goal: str = "了解权利与下一步行动"


class FactRead(ORMModel):
    id: str
    content: str
    occurred_on: date | None
    source: str
    status: str


class FactUpdate(BaseModel):
    status: Literal["user_stated", "evidence_supported", "confirmed", "inferred", "disputed", "unknown"]
    occurred_on: date | None = None


class EvidenceCreate(BaseModel):
    name: str
    evidence_type: str
    purpose: str
    holder: str = "劳动者"
    authenticity: str = "unverified"
    gap: str | None = None


class EvidenceRead(EvidenceCreate, ORMModel):
    id: str


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=10000)


class MessageRead(ORMModel):
    id: str
    role: str
    content: str
    agent: str | None
    created_at: datetime


class AuthorityRead(ORMModel):
    id: str
    title: str
    article: str
    content: str
    level: str
    effective_on: date
    expired_on: date | None
    source_url: str


class ConclusionRead(ORMModel):
    id: str
    viewpoint: str
    counterargument: str
    confidence: float
    uncertainties: list[str]
    authority_ids: list[str]
    reasoning_trace: list[dict] = []
    quality_metrics: dict = {}
    is_current: bool = True
    invalidated_reason: str | None = None


class CaseRead(ORMModel):
    id: str
    title: str
    case_type: str
    region: str
    stage: str
    goal: str
    risk_level: str
    created_at: datetime
    updated_at: datetime
    facts: list[FactRead] = []
    evidence: list[EvidenceRead] = []
    messages: list[MessageRead] = []
    analyses: list[ConclusionRead] = []


class MessageResponse(BaseModel):
    message: MessageRead
    stage: str
    missing_information: list[str]
    authorities: list[AuthorityRead]


class AnalysisRequest(BaseModel):
    as_of: date = Field(default_factory=date.today)


class AnalysisResponse(BaseModel):
    conclusions: list[ConclusionRead]
    authorities: list[AuthorityRead]
    evidence_gaps: list[str]
    next_steps: list[str]
    disclaimer: str
    requires_human_review: bool = False
    blocked_reasons: list[str] = []


class SimulationCreate(BaseModel):
    scenario: Literal["negotiation", "arbitration", "hearing"]
    user_role: Literal["worker", "observer"] = "worker"


class SimulationRead(ORMModel):
    id: str
    scenario: str
    user_role: str
    transcript: list[dict]
    feedback: list[str]
    suggested_answers: list[str]
    assistance_mode: Literal["coach"]
    counsel_agent_id: str
    counsel_memory_version: int
    counsel_memory_snapshot: dict
    status: Literal["active", "completed"]
    completion_reason: Literal["natural_end", "user_ended", "max_rounds", "superseded"] | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SimulationMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=5000)


class WorkerCounselMemoryRead(ORMModel):
    id: str
    case_id: str
    agent_id: Literal["worker_counsel"]
    version: int
    snapshot: dict
    content_hash: str
    created_at: datetime
    updated_at: datetime


class DocumentCreate(BaseModel):
    document_type: Literal["arbitration_application", "evidence_list", "timeline", "hearing_outline"]


class DocumentRead(ORMModel):
    id: str
    document_type: str
    content: str
    created_at: datetime


class FeedbackCreate(BaseModel):
    case_id: str | None = None
    category: Literal["citation_error", "fact_error", "usability", "other"]
    content: str = Field(min_length=1, max_length=5000)


class KnowledgeStats(BaseModel):
    authorities: int
    cases: int
    pending_review: int
    approved_cases: int
    model_provider: str
    model_configured: bool


class AgentTaskRead(ORMModel):
    id: str
    case_id: str
    task_type: str
    agent: str
    objective: str
    protocol_version: str
    input_refs: dict
    constraints: dict
    output: dict
    status: str
    attempt: int
    error: str | None
    created_at: datetime
    completed_at: datetime | None


class HumanReviewRead(ORMModel):
    id: str
    case_id: str
    analysis_id: str
    risk_level: str
    reasons: list[str]
    status: str
    decision: str | None
    reviewer: str | None
    notes: str | None
    created_at: datetime
    reviewed_at: datetime | None


class HumanReviewDecision(BaseModel):
    decision: Literal["approved", "rejected", "changes_requested"]
    reviewer: str = Field(min_length=1, max_length=100)
    notes: str = Field(min_length=1, max_length=5000)


class ModelConsentCreate(BaseModel):
    provider: Literal["deepseek", "embedding"] = "deepseek"
    purposes: list[Literal["intake", "analysis", "simulation"]] = Field(min_length=1)
    data_categories: list[
        Literal["conversation", "facts", "evidence_metadata", "legal_analysis"]
    ] = Field(min_length=1)


class ModelConsentRead(ORMModel):
    id: str
    case_id: str
    provider: str
    purposes: list[str]
    data_categories: list[str]
    status: str
    version: int
    granted_by: str
    granted_at: datetime
    revoked_at: datetime | None


class PseudonymCreate(BaseModel):
    entity_value: str = Field(min_length=2, max_length=200)
    entity_type: Literal["person", "organization", "address", "other"]


class PseudonymRead(ORMModel):
    id: str
    case_id: str
    entity_type: str
    pseudonym: str
    created_by: str
    created_at: datetime
