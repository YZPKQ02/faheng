from sqlalchemy import select

from app.database import SessionLocal
from app.models import AuditEvent, CaseFile, SimulationSession, WorkerCounselMemory
from app.worker_counsel import refresh_worker_counsel_memory


def main() -> None:
    created = 0
    updated = 0
    unchanged = 0
    sessions_pinned = 0
    with SessionLocal() as db:
        for case in db.scalars(select(CaseFile).order_by(CaseFile.created_at)).all():
            existing = db.scalar(
                select(WorkerCounselMemory).where(WorkerCounselMemory.case_id == case.id)
            )
            previous_version = existing.version if existing else 0
            memory = refresh_worker_counsel_memory(
                db, case, trigger="worker_counsel_backfill"
            )
            if existing is None:
                created += 1
            elif memory.version > previous_version:
                updated += 1
            else:
                unchanged += 1
        sessions = db.scalars(
            select(SimulationSession).where(
                SimulationSession.status == "active",
                SimulationSession.counsel_memory_version == 0,
            )
        ).all()
        for session in sessions:
            memory = db.scalar(
                select(WorkerCounselMemory).where(
                    WorkerCounselMemory.case_id == session.case_id
                )
            )
            if memory is None:
                continue
            session.assistance_mode = "coach"
            session.counsel_agent_id = "worker_counsel"
            session.counsel_memory_version = memory.version
            session.counsel_memory_snapshot = memory.snapshot
            sessions_pinned += 1
            db.add(
                AuditEvent(
                    case_id=session.case_id,
                    event_type="simulation_counsel_memory_backfilled",
                    agent="worker_counsel",
                    payload={
                        "session_id": session.id,
                        "memory_id": memory.id,
                        "memory_version": memory.version,
                    },
                )
            )
        db.commit()
    print(
        f"created={created} updated={updated} unchanged={unchanged} "
        f"sessions_pinned={sessions_pinned}"
    )


if __name__ == "__main__":
    main()
