from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CaseFile, EvidenceItem, Fact, Message


class ConversationMemory(TypedDict):
    case_goal: str
    initial_issue: str
    current_user_message: str
    conversation_history: list[dict]
    known_facts: list[dict]
    evidence_summary: list[dict]


def _clip_middle(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    head = limit * 3 // 5
    tail = limit - head
    return f"{value[:head]}…{value[-tail:]}"


def build_conversation_memory(
    db: Session,
    *,
    case_id: str,
    current_message_id: str,
    current_user_message: str,
    history_limit: int = 12,
) -> ConversationMemory:
    """Build bounded memory while always pinning the current message and initial issue."""
    case = db.get(CaseFile, case_id)
    initial_message = db.scalar(
        select(Message)
        .where(Message.case_id == case_id, Message.role == "user")
        .order_by(Message.created_at.asc())
        .limit(1)
    )
    recent = list(
        db.scalars(
            select(Message)
            .where(Message.case_id == case_id, Message.id != current_message_id)
            .order_by(Message.created_at.desc())
            .limit(history_limit)
        ).all()
    )
    recent.reverse()
    facts = list(
        db.scalars(
            select(Fact)
            .where(Fact.case_id == case_id)
            .order_by(Fact.id)
            .limit(30)
        ).all()
    )
    evidence = list(
        db.scalars(
            select(EvidenceItem)
            .where(EvidenceItem.case_id == case_id)
            .order_by(EvidenceItem.id)
            .limit(20)
        ).all()
    )
    return {
        "case_goal": _clip_middle(case.goal if case else "", 500),
        "initial_issue": _clip_middle(
            initial_message.content if initial_message else current_user_message, 1800
        ),
        "current_user_message": _clip_middle(current_user_message, 6000),
        "conversation_history": [
            {
                "id": item.id,
                "role": item.role,
                "content": _clip_middle(item.content, 1400),
            }
            for item in recent
        ],
        "known_facts": [
            {
                "id": item.id,
                "content": _clip_middle(item.content, 600),
                "status": item.status,
                "source": item.source,
            }
            for item in facts
        ],
        "evidence_summary": [
            {
                "id": item.id,
                "name": _clip_middle(item.name, 200),
                "purpose": _clip_middle(item.purpose, 500),
                "authenticity": item.authenticity,
            }
            for item in evidence
        ],
    }
