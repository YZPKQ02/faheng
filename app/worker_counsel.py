import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AnalysisConclusion,
    AuditEvent,
    CaseFile,
    EvidenceItem,
    Fact,
    GeneratedDocument,
    Message,
    WorkerCounselMemory,
)


AGENT_ID = "worker_counsel"
MEMORY_SCHEMA_VERSION = "worker-counsel-memory-v1"


def _clip(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return f"{value[: limit * 3 // 5]}…{value[-limit * 2 // 5 :]}"


def _content_hash(snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_worker_counsel_snapshot(
    db: Session,
    case: CaseFile,
    *,
    pending_questions: list[str] | None = None,
    previous_snapshot: dict | None = None,
) -> dict[str, Any]:
    messages = list(
        db.scalars(
            select(Message)
            .where(Message.case_id == case.id)
            .order_by(Message.created_at.asc())
        ).all()
    )
    facts = list(
        db.scalars(
            select(Fact).where(Fact.case_id == case.id).order_by(Fact.id).limit(60)
        ).all()
    )
    evidence = list(
        db.scalars(
            select(EvidenceItem)
            .where(EvidenceItem.case_id == case.id)
            .order_by(EvidenceItem.id)
            .limit(40)
        ).all()
    )
    analysis = db.scalar(
        select(AnalysisConclusion)
        .where(AnalysisConclusion.case_id == case.id, AnalysisConclusion.is_current.is_(True))
        .order_by(AnalysisConclusion.created_at.desc())
        .limit(1)
    )
    documents = list(
        db.scalars(
            select(GeneratedDocument)
            .where(GeneratedDocument.case_id == case.id)
            .order_by(GeneratedDocument.created_at.desc())
            .limit(12)
        ).all()
    )
    user_messages = [item for item in messages if item.role == "user"]
    assistant_messages = [item for item in messages if item.role == "assistant"]
    retained_questions = (
        pending_questions
        if pending_questions is not None
        else list((previous_snapshot or {}).get("pending_questions", []))
    )
    return {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "identity": {
            "agent_id": AGENT_ID,
            "role": "劳动者代理",
            "relationship": "从诉求收集到仲裁模拟持续服务同一案件",
        },
        "case": {
            "case_id": case.id,
            "goal": _clip(case.goal, 800),
            "initial_issue": _clip(user_messages[0].content, 2400) if user_messages else "",
            "stage": case.stage,
            "region": case.region,
        },
        "claims": [
            {
                "content": _clip(case.goal, 800),
                "status": "user_goal",
                "confirmation_required": True,
                "source": "case.goal",
            }
        ],
        "facts": [
            {
                "id": item.id,
                "content": _clip(item.content, 1000),
                "occurred_on": item.occurred_on.isoformat() if item.occurred_on else None,
                "status": item.status,
                "source": item.source,
            }
            for item in facts
        ],
        "evidence": [
            {
                "id": item.id,
                "name": _clip(item.name, 240),
                "purpose": _clip(item.purpose, 800),
                "authenticity": item.authenticity,
                "gap": _clip(item.gap, 500) if item.gap else None,
            }
            for item in evidence
        ],
        "legal_strategy": (
            {
                "analysis_id": analysis.id,
                "worker_position": _clip(analysis.viewpoint, 3000),
                "expected_opposition": _clip(analysis.counterargument, 2000),
                "uncertainties": [_clip(item, 500) for item in analysis.uncertainties[:12]],
                "authority_ids": analysis.authority_ids[:20],
                "confidence": analysis.confidence,
            }
            if analysis
            else None
        ),
        "pending_questions": list(dict.fromkeys(retained_questions))[:12],
        "recent_counsel_advice": [
            {
                "message_id": item.id,
                "content": _clip(item.content, 1200),
            }
            for item in assistant_messages[-3:]
        ],
        "generated_documents": [
            {"id": item.id, "document_type": item.document_type} for item in documents
        ],
        "source_refs": {
            "message_ids": [item.id for item in messages[-12:]],
            "fact_ids": [item.id for item in facts],
            "evidence_ids": [item.id for item in evidence],
        },
        "fact_boundary": {
            "may_use_user_stated_facts": True,
            "must_label_inferences": True,
            "may_invent_or_confirm_new_facts": False,
        },
    }


def refresh_worker_counsel_memory(
    db: Session,
    case: CaseFile,
    *,
    trigger: str,
    pending_questions: list[str] | None = None,
) -> WorkerCounselMemory:
    memory = db.scalar(
        select(WorkerCounselMemory).where(WorkerCounselMemory.case_id == case.id)
    )
    previous_snapshot = memory.snapshot if memory else None
    snapshot = build_worker_counsel_snapshot(
        db,
        case,
        pending_questions=pending_questions,
        previous_snapshot=previous_snapshot,
    )
    digest = _content_hash(snapshot)
    changed = memory is None or memory.content_hash != digest
    if memory is None:
        memory = WorkerCounselMemory(
            case_id=case.id,
            agent_id=AGENT_ID,
            version=1,
            snapshot=snapshot,
            content_hash=digest,
        )
        db.add(memory)
    elif changed:
        memory.version += 1
        memory.snapshot = snapshot
        memory.content_hash = digest
    if changed:
        db.flush()
        db.add(
            AuditEvent(
                case_id=case.id,
                event_type="worker_counsel_memory_updated",
                agent=AGENT_ID,
                payload={
                    "memory_id": memory.id,
                    "version": memory.version,
                    "content_hash": digest,
                    "trigger": trigger,
                    "source_refs": snapshot["source_refs"],
                },
            )
        )
    return memory
