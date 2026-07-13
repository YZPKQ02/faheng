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
    """A concise auditable ReAct plan, not a hidden chain-of-thought transcript."""

    question_focus: str
    user_intent: str
    relevant_fact_ids: list[str]
    information_gaps: list[str]
    action: Literal["retrieve_authorities", "clarify", "escalate"]
    retrieval_query: str


class SimulationTurnOutput(BaseModel):
    speaker: str
    reply: str
    coaching_feedback: list[str]
    next_question: str
