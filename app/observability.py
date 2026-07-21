from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent, CaseFile


@dataclass(frozen=True)
class ModelCallTelemetry:
    outcome: str
    duration_ms: float
    attempts: int
    retries: int
    status_code: int | None = None
    error_type: str | None = None
    redaction_count: int = 0
    pseudonym_count: int = 0
    provider: str | None = None
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


def query_fingerprint(query: str, *, secret: str | None, tenant_id: str) -> str | None:
    if not secret:
        return None
    normalized = " ".join(query.casefold().split())
    return hmac.new(
        secret.encode(), f"{tenant_id}\x1f{normalized}".encode(), sha256
    ).hexdigest()


def record_model_call_metric(
    db: Session, *, case_id: str, phase: str, telemetry: ModelCallTelemetry | None
) -> None:
    if telemetry is None:
        return
    db.add(
        AuditEvent(
            case_id=case_id,
            event_type="model_call_metric",
            agent="observability",
            duration_ms=telemetry.duration_ms,
            payload={"phase": phase, **asdict(telemetry)},
        )
    )


def aggregate_tenant_metrics(
    db: Session, *, tenant_id: str, hours: int
) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    events = list(
        db.scalars(
            select(AuditEvent)
            .join(CaseFile, AuditEvent.case_id == CaseFile.id)
            .where(
                CaseFile.tenant_id == tenant_id,
                AuditEvent.created_at >= since,
                AuditEvent.event_type.in_(("model_call_metric", "authority_retrieval_metric")),
            )
        ).all()
    )
    model_events = [item for item in events if item.event_type == "model_call_metric"]
    retrieval_events = [
        item for item in events if item.event_type == "authority_retrieval_metric"
    ]
    fingerprints = [
        item.payload.get("query_fingerprint")
        for item in retrieval_events
        if item.payload.get("query_fingerprint")
    ]
    return {
        "window_hours": hours,
        "model": {
            "calls": len(model_events),
            "successes": sum(item.payload.get("outcome") == "success" for item in model_events),
            "fallbacks": sum(item.payload.get("outcome") != "success" for item in model_events),
            "average_duration_ms": round(
                sum(item.duration_ms or 0 for item in model_events) / max(1, len(model_events)), 2
            ),
            "retries": sum(int(item.payload.get("retries", 0)) for item in model_events),
        },
        "retrieval": {
            "calls": len(retrieval_events),
            "empty_results": sum(
                int(item.payload.get("result_count", 0)) == 0 for item in retrieval_events
            ),
            "average_duration_ms": round(
                sum(item.duration_ms or 0 for item in retrieval_events)
                / max(1, len(retrieval_events)),
                2,
            ),
            "repeat_query_rate": round(
                (len(fingerprints) - len(set(fingerprints))) / max(1, len(fingerprints)), 3
            ),
        },
    }
