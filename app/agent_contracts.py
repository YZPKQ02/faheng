from typing import Literal

from pydantic import BaseModel, Field


class FactCandidate(BaseModel):
    content: str
    source: str = "user"
    status: str = "user_stated"


class InvestigationOutput(BaseModel):
    category: str
    urgency: str
    facts: list[FactCandidate]
    missing_information: list[str]
    escalation_reason: str | None = None


class PartyArgument(BaseModel):
    position: str
    arguments: list[str]
    evidence_needed: list[str]
    authority_ids: list[str] = Field(description="只能填写上下文提供的 authority id")


class JudicialAssessment(BaseModel):
    issues: list[str]
    assessment: str
    likely_outcome: str
    confidence: float = Field(ge=0, le=1)
    uncertainties: list[str]
    authority_ids: list[str]


class SafetyReview(BaseModel):
    approved: bool
    problems: list[str]
    corrected_summary: str
    requires_human_lawyer: bool


class ConversationOutput(BaseModel):
    answer: str
    follow_up_questions: list[str]
    should_escalate: bool = False
    escalation_reason: str | None = None


class ConversationPlan(BaseModel):
    """A concise auditable coarse plan, not a hidden chain-of-thought transcript."""

    question_focus: str
    user_intent: str
    relevant_fact_ids: list[str]
    information_gaps: list[str]
    action: Literal["retrieve_authorities", "clarify", "escalate"]
    retrieval_query: str


class ExecutionStep(BaseModel):
    """System-owned execution contract for one bounded plan step."""

    step_id: Literal["persist_facts", "retrieve_authorities", "compose_response"]
    objective: str
    executor: Literal["deterministic", "bounded_react", "structured_model"]
    allowed_tools: list[Literal["search_authorities"]] = Field(default_factory=list)
    max_tool_calls: int = Field(default=0, ge=0, le=2)
    success_condition: str


class ConversationExecutionPlan(BaseModel):
    """Validated outer Plan-and-Execute plan compiled by the application."""

    protocol: Literal["plan-execute-react-v1"] = "plan-execute-react-v1"
    goal: str
    steps: list[ExecutionStep]
    max_replans: int = Field(default=0, ge=0, le=1)


class SimulationTurnOutput(BaseModel):
    speaker: Literal["arbitrator", "employer_advocate"]
    reply: str
    coaching_feedback: list[str]
    next_question: str | None = None


class SimulationAgentReply(BaseModel):
    reply: str


class SimulationArbitratorReply(BaseModel):
    reply: str
    next_question: str | None = None
    next_stage: Literal[
        "orientation",
        "claims",
        "fact_investigation",
        "evidence_examination",
        "debate",
        "closing_or_mediation",
    ]


class SimulationCoachReply(BaseModel):
    feedback: list[str] = Field(min_length=1, max_length=3)


class SimulationTurnDecision(BaseModel):
    """Application-owned floor-control decision for one simulation turn."""

    speech_act: Literal[
        "role_identity",
        "procedure",
        "coaching",
        "direct_question",
        "answer_or_substantive",
        "clarify",
    ]
    addressed_to: Literal[
        "arbitrator",
        "employer_advocate",
        "worker_coach",
        "all",
        "unspecified",
    ]
    response_plan: list[Literal["arbitrator", "employer_advocate", "worker_coach"]]
    route_source: Literal[
        "explicit_address",
        "speech_act",
        "pending_question",
        "semantic_router",
        "fallback",
    ]


class SimulationRouterOutput(BaseModel):
    """Strict semantic-router output; the application still validates the plan."""

    speech_act: Literal[
        "role_identity",
        "procedure",
        "coaching",
        "direct_question",
        "answer_or_substantive",
        "clarify",
    ]
    addressed_to: Literal[
        "arbitrator",
        "employer_advocate",
        "worker_coach",
        "all",
        "unspecified",
    ]
    response_plan: list[Literal["arbitrator", "employer_advocate", "worker_coach"]]
    confidence: float = Field(ge=0, le=1)
    needs_clarification: bool
