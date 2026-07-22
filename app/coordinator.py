from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AgentTask,
    AnalysisConclusion,
    AuditEvent,
    CaseFile,
    FactStatus,
    HumanReviewTask,
)


def reconcile_case_stage(db: Session, case: CaseFile) -> str:
    db.flush()
    pending_review = db.scalar(
        select(HumanReviewTask.id).where(
            HumanReviewTask.case_id == case.id,
            HumanReviewTask.status == "pending",
        )
    )
    current_analysis = db.scalar(
        select(AnalysisConclusion.id).where(
            AnalysisConclusion.case_id == case.id,
            AnalysisConclusion.is_current.is_(True),
            AnalysisConclusion.publication_status == "published",
        )
    )
    if pending_review:
        stage = "human_review"
    elif current_analysis:
        stage = "strategy_ready"
    elif not case.facts:
        stage = "intake"
    elif not any(
        fact.status in (FactStatus.CONFIRMED, FactStatus.EVIDENCE_SUPPORTED)
        for fact in case.facts
    ):
        stage = "fact_review"
    elif not case.evidence:
        stage = "evidence_review"
    else:
        stage = "issue_identification"
    if case.stage != stage:
        previous = case.stage
        case.stage = stage
        db.add(
            AuditEvent(
                case_id=case.id,
                event_type="case_stage_changed",
                agent="case_coordinator",
                payload={"from": previous, "to": stage},
            )
        )
    return stage


def record_agent_task(
    db: Session,
    *,
    case_id: str,
    agent: str,
    task_type: str,
    objective: str,
    input_refs: dict,
    output: dict,
    duration_ms: float,
    constraints: dict | None = None,
) -> AgentTask:
    task = AgentTask(
        case_id=case_id,
        agent=agent,
        task_type=task_type,
        objective=objective,
        input_refs=input_refs,
        constraints=constraints or {"max_attempts": 2, "timeout_seconds": 60},
        output=output,
        status="completed",
        completed_at=datetime.now(timezone.utc),
    )
    db.add(task)
    db.flush()
    db.add(
        AuditEvent(
            case_id=case_id,
            event_type="agent_task_completed",
            agent=agent,
            duration_ms=duration_ms,
            payload={"task_id": task.id, "task_type": task_type, "protocol_version": task.protocol_version},
        )
    )
    return task


def ensure_human_review(
    db: Session,
    case: CaseFile,
    conclusion: AnalysisConclusion,
    reasons: list[str],
) -> HumanReviewTask:
    existing = db.scalar(
        select(HumanReviewTask).where(
            HumanReviewTask.analysis_id == conclusion.id,
            HumanReviewTask.status == "pending",
        )
    )
    if existing:
        return existing
    task = HumanReviewTask(
        case_id=case.id,
        analysis_id=conclusion.id,
        risk_level="high" if conclusion.confidence < 0.5 else "medium",
        reasons=reasons,
    )
    db.add(task)
    db.flush()
    reconcile_case_stage(db, case)
    return task
