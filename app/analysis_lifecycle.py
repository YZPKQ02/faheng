from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AnalysisConclusion, AuditEvent, CaseFile, HumanReviewTask


def invalidate_case_analyses(db: Session, case: CaseFile, reason: str) -> int:
    current = list(
        db.scalars(
            select(AnalysisConclusion).where(
                AnalysisConclusion.case_id == case.id,
                AnalysisConclusion.is_current.is_(True),
            )
        ).all()
    )
    for conclusion in current:
        conclusion.is_current = False
        conclusion.invalidated_reason = reason
        conclusion.publication_status = "stale"
        reviews = db.scalars(
            select(HumanReviewTask).where(
                HumanReviewTask.analysis_id == conclusion.id,
                HumanReviewTask.status == "pending",
            )
        ).all()
        for review in reviews:
            review.status = "cancelled"
            review.decision = "superseded"
            review.notes = reason
    if current:
        case.stage = "analysis_stale"
        db.add(
            AuditEvent(
                case_id=case.id,
                event_type="analysis_invalidated",
                agent="case_state_manager",
                payload={"reason": reason, "analysis_ids": [item.id for item in current]},
            )
        )
    return len(current)
