from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import time
from collections.abc import Iterator

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, inspect, select, text
from sqlalchemy.orm import Session, selectinload

from app.authorities import seed_authorities
from app.config import get_settings
from app.database import Base, SessionLocal, engine, get_db
from app.models import (
    AgentTask,
    AnalysisConclusion,
    AuditEvent,
    CaseFile,
    EvidenceItem,
    Fact,
    Feedback,
    GeneratedDocument,
    HumanReviewTask,
    LegalAuthority,
    LegalCase,
    Message,
    SimulationSession,
)
from app.schemas import (
    AgentTaskRead,
    AnalysisRequest,
    AnalysisResponse,
    AuthorityRead,
    CaseCreate,
    CaseRead,
    DocumentCreate,
    DocumentRead,
    EvidenceCreate,
    EvidenceRead,
    FeedbackCreate,
    FactRead,
    FactUpdate,
    HumanReviewDecision,
    HumanReviewRead,
    MessageCreate,
    MessageResponse,
    KnowledgeStats,
    SimulationCreate,
    SimulationMessageCreate,
    SimulationRead,
)
from app.analysis_lifecycle import invalidate_case_analyses
from app.coordinator import ensure_human_review, reconcile_case_stage
from app.reasoning import decision_gate
from app.services import (
    DISCLAIMER,
    analyze_case,
    continue_simulation,
    create_document,
    create_simulation,
    get_case_authorities,
)
from app.workflow import run_intake
from app.model_gateway import ModelGateway


def load_case(db: Session, case_id: str) -> CaseFile:
    stmt = (
        select(CaseFile)
        .where(CaseFile.id == case_id)
        .options(
            selectinload(CaseFile.facts),
            selectinload(CaseFile.evidence),
            selectinload(CaseFile.messages),
            selectinload(CaseFile.analyses),
        )
    )
    case = db.scalar(stmt)
    if not case:
        raise HTTPException(status_code=404, detail="案件不存在")
    return case


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    # Lightweight compatibility migration for the SQLite MVP. Production uses Alembic.
    if engine.dialect.name == "sqlite":
        columns = {item["name"] for item in inspect(engine).get_columns("analysis_conclusions")}
        with engine.begin() as connection:
            if "reasoning_trace" not in columns:
                connection.execute(text("ALTER TABLE analysis_conclusions ADD COLUMN reasoning_trace JSON DEFAULT '[]'"))
            if "quality_metrics" not in columns:
                connection.execute(text("ALTER TABLE analysis_conclusions ADD COLUMN quality_metrics JSON DEFAULT '{}'"))
            if "is_current" not in columns:
                connection.execute(text("ALTER TABLE analysis_conclusions ADD COLUMN is_current BOOLEAN DEFAULT 1"))
            if "invalidated_reason" not in columns:
                connection.execute(text("ALTER TABLE analysis_conclusions ADD COLUMN invalidated_reason VARCHAR(200)"))
    with SessionLocal() as db:
        seed_authorities(db)
    yield


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/knowledge/stats", response_model=KnowledgeStats)
def knowledge_stats(db: Session = Depends(get_db)) -> KnowledgeStats:
    gateway = ModelGateway()
    return KnowledgeStats(
        authorities=db.scalar(select(func.count()).select_from(LegalAuthority)) or 0,
        cases=db.scalar(select(func.count()).select_from(LegalCase)) or 0,
        pending_review=db.scalar(
            select(func.count()).select_from(LegalCase).where(LegalCase.review_status == "pending")
        )
        or 0,
        approved_cases=db.scalar(
            select(func.count()).select_from(LegalCase).where(LegalCase.review_status == "approved")
        )
        or 0,
        model_provider=settings.model_provider,
        model_configured=gateway.enabled,
    )


@app.post("/cases", response_model=CaseRead, status_code=status.HTTP_201_CREATED)
def create_case(payload: CaseCreate, db: Session = Depends(get_db)) -> CaseFile:
    case = CaseFile(**payload.model_dump())
    db.add(case)
    db.commit()
    return load_case(db, case.id)


@app.get("/cases", response_model=list[CaseRead])
def list_cases(db: Session = Depends(get_db)) -> list[CaseFile]:
    stmt = (
        select(CaseFile)
        .options(
            selectinload(CaseFile.facts),
            selectinload(CaseFile.evidence),
            selectinload(CaseFile.messages),
            selectinload(CaseFile.analyses),
        )
        .order_by(CaseFile.updated_at.desc())
    )
    return list(db.scalars(stmt).unique().all())


@app.get("/cases/{case_id}", response_model=CaseRead)
def read_case(case_id: str, db: Session = Depends(get_db)) -> CaseFile:
    return load_case(db, case_id)


@app.delete("/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_case(case_id: str, db: Session = Depends(get_db)) -> None:
    case = load_case(db, case_id)
    title = case.title
    for model in (
        HumanReviewTask,
        AgentTask,
        Fact,
        EvidenceItem,
        Message,
        AnalysisConclusion,
        SimulationSession,
        GeneratedDocument,
        AuditEvent,
    ):
        db.execute(delete(model).where(model.case_id == case_id))
    db.delete(case)
    db.flush()
    db.add(
        AuditEvent(
            event_type="case_deleted",
            payload={"deleted_case_title": title, "content_retained": False},
        )
    )
    db.commit()


@app.post("/cases/{case_id}/messages", response_model=MessageResponse)
def post_message(case_id: str, payload: MessageCreate, db: Session = Depends(get_db)) -> MessageResponse:
    case = load_case(db, case_id)
    message, state = run_intake(db, case, payload.content)
    reconcile_case_stage(db, case)
    db.commit()
    authorities = [db.get(LegalAuthority, aid) for aid in state.get("authority_ids", [])]
    return MessageResponse(message=message, stage="fact_gathering", missing_information=state.get("missing_information", []), authorities=[a for a in authorities if a])


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/cases/{case_id}/messages/stream")
def stream_message(
    case_id: str, payload: MessageCreate, db: Session = Depends(get_db)
) -> StreamingResponse:
    case = load_case(db, case_id)

    def generate() -> Iterator[str]:
        yield sse("status", {"stage": "observe", "label": "正在回顾本案与当前问题"})
        time.sleep(0.05)
        yield sse("status", {"stage": "plan", "label": "正在生成可控的分步计划"})
        time.sleep(0.05)
        yield sse("status", {"stage": "execute", "label": "正在按计划检索并核验法律依据"})
        try:
            message, state = run_intake(db, case, payload.content)
            reconcile_case_stage(db, case)
            db.commit()
            yield sse("status", {"stage": "respond", "label": "正在围绕当前问题组织答复"})
            content = message.content
            chunk_size = 5
            for index in range(0, len(content), chunk_size):
                yield sse("token", {"content": content[index : index + chunk_size]})
                time.sleep(0.018)
            yield sse(
                "complete",
                {
                    "message_id": message.id,
                    "stage": "fact_gathering",
                    "missing_information": state.get("missing_information", []),
                    "model_provider": settings.model_provider,
                },
            )
        except Exception as exc:
            db.rollback()
            yield sse("error", {"message": f"回答生成失败：{type(exc).__name__}"})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/cases/{case_id}/evidence", response_model=EvidenceRead, status_code=201)
def add_evidence(case_id: str, payload: EvidenceCreate, db: Session = Depends(get_db)) -> EvidenceItem:
    case = load_case(db, case_id)
    item = EvidenceItem(case_id=case_id, **payload.model_dump())
    db.add(item)
    invalidate_case_analyses(db, case, "新增证据后需要重新分析")
    evidence_text = f"{payload.name}{payload.purpose}"
    for fact in case.facts:
        terms = [term for term in fact.content.replace("，", " ").split() if len(term) >= 2]
        if any(term in evidence_text for term in terms):
            fact.status = "evidence_supported"
    reconcile_case_stage(db, case)
    db.commit()
    db.refresh(item)
    return item


@app.patch("/cases/{case_id}/facts/{fact_id}", response_model=FactRead)
def update_fact(
    case_id: str, fact_id: str, payload: FactUpdate, db: Session = Depends(get_db)
) -> Fact:
    case = load_case(db, case_id)
    fact = db.get(Fact, fact_id)
    if not fact or fact.case_id != case_id:
        raise HTTPException(status_code=404, detail="事实不存在")
    previous = {"status": fact.status, "occurred_on": str(fact.occurred_on) if fact.occurred_on else None}
    fact.status = payload.status
    fact.occurred_on = payload.occurred_on
    invalidate_case_analyses(db, case, "事实状态或日期发生变化")
    reconcile_case_stage(db, case)
    db.add(
        AuditEvent(
            case_id=case.id,
            event_type="fact_reviewed",
            agent="user",
            payload={"fact_id": fact.id, "previous": previous, "current": payload.model_dump(mode="json")},
        )
    )
    db.commit()
    db.refresh(fact)
    return fact


@app.post("/cases/{case_id}/analysis", response_model=AnalysisResponse)
def run_analysis(case_id: str, payload: AnalysisRequest, db: Session = Depends(get_db)) -> AnalysisResponse:
    case = load_case(db, case_id)
    conclusions, authorities, gaps, next_steps = analyze_case(db, case, payload.as_of)
    blocked_reasons = decision_gate(
        conclusions[0].quality_metrics, conclusions[0].authority_ids
    )
    if blocked_reasons:
        ensure_human_review(db, case, conclusions[0], blocked_reasons)
    reconcile_case_stage(db, case)
    db.commit()
    return AnalysisResponse(
        conclusions=conclusions,
        authorities=authorities,
        evidence_gaps=gaps,
        next_steps=next_steps,
        disclaimer=DISCLAIMER,
        requires_human_review=bool(blocked_reasons),
        blocked_reasons=blocked_reasons,
    )


@app.get("/cases/{case_id}/agent-tasks", response_model=list[AgentTaskRead])
def list_agent_tasks(case_id: str, db: Session = Depends(get_db)) -> list[AgentTask]:
    load_case(db, case_id)
    return list(
        db.scalars(
            select(AgentTask)
            .where(AgentTask.case_id == case_id)
            .order_by(AgentTask.created_at)
        ).all()
    )


@app.get("/cases/{case_id}/reviews", response_model=list[HumanReviewRead])
def list_human_reviews(case_id: str, db: Session = Depends(get_db)) -> list[HumanReviewTask]:
    load_case(db, case_id)
    return list(
        db.scalars(
            select(HumanReviewTask)
            .where(HumanReviewTask.case_id == case_id)
            .order_by(HumanReviewTask.created_at)
        ).all()
    )


@app.post("/reviews/{review_id}/decision", response_model=HumanReviewRead)
def decide_human_review(
    review_id: str,
    payload: HumanReviewDecision,
    db: Session = Depends(get_db),
) -> HumanReviewTask:
    review = db.get(HumanReviewTask, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="人工审核任务不存在")
    if review.status != "pending":
        raise HTTPException(status_code=409, detail="人工审核任务已处理")
    case = load_case(db, review.case_id)
    conclusion = db.get(AnalysisConclusion, review.analysis_id)
    review.status = "completed"
    review.decision = payload.decision
    review.reviewer = payload.reviewer
    review.notes = payload.notes
    review.reviewed_at = datetime.now(timezone.utc)
    if payload.decision in ("rejected", "changes_requested") and conclusion:
        conclusion.is_current = False
        conclusion.invalidated_reason = (
            "人工审核驳回" if payload.decision == "rejected" else "人工审核要求修改"
        )
    db.add(
        AuditEvent(
            case_id=case.id,
            event_type="human_review_completed",
            agent=payload.reviewer,
            payload={
                "review_id": review.id,
                "analysis_id": review.analysis_id,
                "decision": payload.decision,
            },
        )
    )
    db.flush()
    reconcile_case_stage(db, case)
    db.commit()
    db.refresh(review)
    return review


@app.post("/cases/{case_id}/simulations", response_model=SimulationRead, status_code=201)
def start_simulation(case_id: str, payload: SimulationCreate, db: Session = Depends(get_db)):
    return create_simulation(db, load_case(db, case_id), payload.scenario, payload.user_role)


@app.post("/simulations/{session_id}/messages", response_model=SimulationRead)
def simulation_message(
    session_id: str, payload: SimulationMessageCreate, db: Session = Depends(get_db)
):
    session = db.get(SimulationSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="模拟会话不存在")
    case = load_case(db, session.case_id)
    return continue_simulation(db, session, case, payload.content)


@app.post("/cases/{case_id}/documents", response_model=DocumentRead, status_code=201)
def generate_document(case_id: str, payload: DocumentCreate, db: Session = Depends(get_db)):
    return create_document(db, load_case(db, case_id), payload.document_type)


@app.get("/cases/{case_id}/authorities", response_model=list[AuthorityRead])
def authorities(case_id: str, db: Session = Depends(get_db)):
    load_case(db, case_id)
    return get_case_authorities(db, case_id)


@app.post("/feedback", status_code=201)
def add_feedback(payload: FeedbackCreate, db: Session = Depends(get_db)) -> dict:
    if payload.case_id:
        load_case(db, payload.case_id)
    item = Feedback(**payload.model_dump())
    db.add(item)
    db.commit()
    return {"id": item.id, "accepted": True}
