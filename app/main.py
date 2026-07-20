from contextlib import asynccontextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import time
from collections.abc import Iterator

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, inspect, select, text
from sqlalchemy.orm import Session, selectinload

from app.authorities import seed_authorities
from app.auth import Principal, current_principal, require_reviewer
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
    ModelDataConsent,
    CasePseudonym,
    SimulationSession,
    WorkerCounselMemory,
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
    ModelConsentCreate,
    ModelConsentRead,
    KnowledgeStats,
    PseudonymCreate,
    PseudonymRead,
    SimulationCreate,
    SimulationMessageCreate,
    SimulationRead,
    WorkerCounselMemoryRead,
)
from app.analysis_lifecycle import invalidate_case_analyses
from app.coordinator import ensure_human_review, reconcile_case_stage
from app.reasoning import decision_gate
from app.services import (
    DISCLAIMER,
    analyze_case,
    continue_simulation,
    complete_simulation,
    create_document,
    create_simulation,
    get_case_authorities,
    open_simulation,
)
from app.workflow import run_intake
from app.model_gateway import ModelGateway
from app.observability import aggregate_tenant_metrics
from app.privacy import entity_fingerprint
from app.worker_counsel import refresh_worker_counsel_memory


def load_case(db: Session, case_id: str, principal: Principal) -> CaseFile:
    stmt = (
        select(CaseFile)
        .where(CaseFile.id == case_id, CaseFile.tenant_id == principal.tenant_id)
        .options(
            selectinload(CaseFile.facts),
            selectinload(CaseFile.evidence),
            selectinload(CaseFile.messages),
            selectinload(CaseFile.analyses),
        )
    )
    if not principal.can_access_tenant_cases:
        stmt = stmt.where(CaseFile.owner_id == principal.actor_id)
    case = db.scalar(stmt)
    if not case:
        raise HTTPException(status_code=404, detail="案件不存在")
    return case


@asynccontextmanager
async def lifespan(_: FastAPI):
    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(bind=engine)
        # Lightweight compatibility migration for the SQLite MVP. Production uses Alembic.
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
        case_columns = {item["name"] for item in inspect(engine).get_columns("case_files")}
        with engine.begin() as connection:
            if "tenant_id" not in case_columns:
                connection.execute(
                    text("ALTER TABLE case_files ADD COLUMN tenant_id VARCHAR(100) DEFAULT 'local'")
                )
            if "owner_id" not in case_columns:
                connection.execute(
                    text("ALTER TABLE case_files ADD COLUMN owner_id VARCHAR(200) DEFAULT 'local'")
                )
        simulation_columns = {
            item["name"] for item in inspect(engine).get_columns("simulation_sessions")
        }
        with engine.begin() as connection:
            if "status" not in simulation_columns:
                connection.execute(
                    text("ALTER TABLE simulation_sessions ADD COLUMN status VARCHAR(20) DEFAULT 'active'")
                )
            if "created_at" not in simulation_columns:
                connection.execute(
                    text("ALTER TABLE simulation_sessions ADD COLUMN created_at DATETIME")
                )
            if "updated_at" not in simulation_columns:
                connection.execute(
                    text("ALTER TABLE simulation_sessions ADD COLUMN updated_at DATETIME")
                )
            if "assistance_mode" not in simulation_columns:
                connection.execute(
                    text(
                        "ALTER TABLE simulation_sessions ADD COLUMN "
                        "assistance_mode VARCHAR(30) DEFAULT 'coach'"
                    )
                )
            if "suggested_answers" not in simulation_columns:
                connection.execute(
                    text(
                        "ALTER TABLE simulation_sessions ADD COLUMN "
                        "suggested_answers JSON DEFAULT '[]'"
                    )
                )
            if "counsel_agent_id" not in simulation_columns:
                connection.execute(
                    text(
                        "ALTER TABLE simulation_sessions ADD COLUMN "
                        "counsel_agent_id VARCHAR(50) DEFAULT 'worker_counsel'"
                    )
                )
            if "counsel_memory_version" not in simulation_columns:
                connection.execute(
                    text(
                        "ALTER TABLE simulation_sessions ADD COLUMN "
                        "counsel_memory_version INTEGER DEFAULT 0"
                    )
                )
            if "counsel_memory_snapshot" not in simulation_columns:
                connection.execute(
                    text(
                        "ALTER TABLE simulation_sessions ADD COLUMN "
                        "counsel_memory_snapshot JSON DEFAULT '{}'"
                    )
                )
            connection.execute(
                text(
                    "UPDATE simulation_sessions "
                    "SET status = COALESCE(status, 'active'), "
                    "assistance_mode = COALESCE(assistance_mode, 'coach'), "
                    "suggested_answers = COALESCE(suggested_answers, '[]'), "
                    "counsel_agent_id = COALESCE(counsel_agent_id, 'worker_counsel'), "
                    "counsel_memory_version = COALESCE(counsel_memory_version, 0), "
                    "counsel_memory_snapshot = COALESCE(counsel_memory_snapshot, '{}'), "
                    "created_at = COALESCE(created_at, CURRENT_TIMESTAMP), "
                    "updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)"
                )
            )
    with SessionLocal() as db:
        seed_authorities(db)
    yield


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/knowledge/stats", response_model=KnowledgeStats)
def knowledge_stats(
    db: Session = Depends(get_db), _: Principal = Depends(current_principal)
) -> KnowledgeStats:
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


@app.get("/internal/metrics")
def internal_metrics(
    hours: int = Query(default=24, ge=1, le=168),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_reviewer),
) -> dict:
    return aggregate_tenant_metrics(db, tenant_id=principal.tenant_id, hours=hours)


@app.post("/cases", response_model=CaseRead, status_code=status.HTTP_201_CREATED)
def create_case(
    payload: CaseCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
) -> CaseFile:
    case = CaseFile(
        **payload.model_dump(), tenant_id=principal.tenant_id, owner_id=principal.actor_id
    )
    db.add(case)
    db.flush()
    refresh_worker_counsel_memory(db, case, trigger="case_created")
    db.commit()
    return load_case(db, case.id, principal)


@app.get("/cases", response_model=list[CaseRead])
def list_cases(
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
) -> list[CaseFile]:
    stmt = (
        select(CaseFile)
        .options(
            selectinload(CaseFile.facts),
            selectinload(CaseFile.evidence),
            selectinload(CaseFile.messages),
            selectinload(CaseFile.analyses),
        )
        .order_by(CaseFile.updated_at.desc())
        .where(CaseFile.tenant_id == principal.tenant_id)
    )
    if not principal.can_access_tenant_cases:
        stmt = stmt.where(CaseFile.owner_id == principal.actor_id)
    return list(db.scalars(stmt).unique().all())


@app.get("/cases/{case_id}", response_model=CaseRead)
def read_case(
    case_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
) -> CaseFile:
    return load_case(db, case_id, principal)


@app.get(
    "/cases/{case_id}/worker-counsel-memory",
    response_model=WorkerCounselMemoryRead,
)
def read_worker_counsel_memory(
    case_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
) -> WorkerCounselMemory:
    case = load_case(db, case_id, principal)
    memory = refresh_worker_counsel_memory(db, case, trigger="memory_read")
    db.commit()
    db.refresh(memory)
    return memory


@app.delete("/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_case(
    case_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
) -> None:
    case = load_case(db, case_id, principal)
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
        Feedback,
        ModelDataConsent,
        CasePseudonym,
        WorkerCounselMemory,
    ):
        db.execute(delete(model).where(model.case_id == case_id))
    db.delete(case)
    db.flush()
    db.add(
        AuditEvent(
            event_type="case_deleted",
            payload={
                "deleted_case_fingerprint": sha256(f"{case_id}:{title}".encode()).hexdigest(),
                "content_retained": False,
            },
        )
    )
    db.commit()


@app.post("/cases/{case_id}/messages", response_model=MessageResponse)
def post_message(
    case_id: str,
    payload: MessageCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
) -> MessageResponse:
    case = load_case(db, case_id, principal)
    message, state = run_intake(db, case, payload.content)
    reconcile_case_stage(db, case)
    db.commit()
    authorities = [db.get(LegalAuthority, aid) for aid in state.get("authority_ids", [])]
    return MessageResponse(message=message, stage="fact_gathering", missing_information=state.get("missing_information", []), authorities=[a for a in authorities if a])


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/cases/{case_id}/messages/stream")
def stream_message(
    case_id: str,
    payload: MessageCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
) -> StreamingResponse:
    case = load_case(db, case_id, principal)

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
def add_evidence(
    case_id: str,
    payload: EvidenceCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
) -> EvidenceItem:
    case = load_case(db, case_id, principal)
    item = EvidenceItem(case_id=case_id, **payload.model_dump())
    db.add(item)
    db.flush()
    invalidate_case_analyses(db, case, "新增证据后需要重新分析")
    evidence_text = f"{payload.name}{payload.purpose}"
    candidate_fact_ids: list[str] = []
    for fact in case.facts:
        terms = [term for term in fact.content.replace("，", " ").split() if len(term) >= 2]
        if any(term in evidence_text for term in terms):
            candidate_fact_ids.append(fact.id)
    db.add(
        AuditEvent(
            case_id=case.id,
            event_type="evidence_link_candidates_identified",
            agent="deterministic_matcher",
            payload={
                "evidence_id": item.id,
                "candidate_fact_ids": candidate_fact_ids,
                "fact_status_changed": False,
                "review_required": bool(candidate_fact_ids),
            },
        )
    )
    reconcile_case_stage(db, case)
    refresh_worker_counsel_memory(db, case, trigger="evidence_added")
    db.commit()
    db.refresh(item)
    return item


@app.patch("/cases/{case_id}/facts/{fact_id}", response_model=FactRead)
def update_fact(
    case_id: str,
    fact_id: str,
    payload: FactUpdate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
) -> Fact:
    case = load_case(db, case_id, principal)
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
    refresh_worker_counsel_memory(db, case, trigger="fact_reviewed")
    db.commit()
    db.refresh(fact)
    return fact


@app.post("/cases/{case_id}/analysis", response_model=AnalysisResponse)
def run_analysis(
    case_id: str,
    payload: AnalysisRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
) -> AnalysisResponse:
    case = load_case(db, case_id, principal)
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
def list_agent_tasks(
    case_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
) -> list[AgentTask]:
    load_case(db, case_id, principal)
    return list(
        db.scalars(
            select(AgentTask)
            .where(AgentTask.case_id == case_id)
            .order_by(AgentTask.created_at)
        ).all()
    )


@app.get("/cases/{case_id}/reviews", response_model=list[HumanReviewRead])
def list_human_reviews(
    case_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
) -> list[HumanReviewTask]:
    load_case(db, case_id, principal)
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
    principal: Principal = Depends(require_reviewer),
) -> HumanReviewTask:
    review = db.get(HumanReviewTask, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="人工审核任务不存在")
    if review.status != "pending":
        raise HTTPException(status_code=409, detail="人工审核任务已处理")
    case = load_case(db, review.case_id, principal)
    conclusion = db.get(AnalysisConclusion, review.analysis_id)
    review.status = "completed"
    review.decision = payload.decision
    review.reviewer = principal.actor_id
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
            agent=principal.actor_id,
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
def start_simulation(
    case_id: str,
    payload: SimulationCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
):
    return create_simulation(
        db, load_case(db, case_id, principal), payload.scenario, payload.user_role
    )


@app.put("/cases/{case_id}/simulations/active", response_model=SimulationRead)
def open_active_simulation(
    case_id: str,
    payload: SimulationCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
):
    return open_simulation(
        db, load_case(db, case_id, principal), payload.scenario, payload.user_role
    )


@app.post("/simulations/{session_id}/messages", response_model=SimulationRead)
def simulation_message(
    session_id: str,
    payload: SimulationMessageCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
):
    session = db.get(SimulationSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="模拟会话不存在")
    case = load_case(db, session.case_id, principal)
    if session.status != "active":
        raise HTTPException(status_code=409, detail="本次模拟已结束，请重新开始一场模拟")
    return continue_simulation(db, session, case, payload.content)


@app.post("/simulations/{session_id}/messages/stream")
def stream_simulation_message(
    session_id: str,
    payload: SimulationMessageCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
) -> StreamingResponse:
    session = db.get(SimulationSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="模拟会话不存在")
    case = load_case(db, session.case_id, principal)
    if session.status != "active":
        raise HTTPException(status_code=409, detail="本次模拟已结束，请重新开始一场模拟")
    previous_count = len(session.transcript)

    def generate() -> Iterator[str]:
        yield sse("status", {"label": "正在分析你的回答并分配本轮发言顺序"})
        try:
            updated = continue_simulation(db, session, case, payload.content)
            new_lines = [
                line
                for line in updated.transcript[previous_count:]
                if line.get("agent_id") not in ("worker", "system")
            ]
            for index, line in enumerate(new_lines):
                stream_id = f"{updated.id}:{len(updated.transcript)}:{index}"
                shell = {
                    "stream_id": stream_id,
                    "role": line.get("role", "系统"),
                    "agent_id": line.get("agent_id"),
                    "kind": line.get("kind"),
                    "content": "",
                }
                yield sse("agent_start", shell)
                label = {
                    "employer_advocate": "用人单位代理正在回应",
                    "arbitrator": "仲裁员正在归纳和提问",
                }.get(line.get("agent_id"), "劳动者代理正在整理建议")
                yield sse("status", {"label": label})
                content = str(line.get("content", ""))
                for offset in range(0, len(content), 5):
                    yield sse(
                        "agent_token",
                        {
                            "stream_id": stream_id,
                            "content": content[offset : offset + 5],
                        },
                    )
                    time.sleep(0.018)
                yield sse("agent_complete", {**shell, "content": content})
            streamed_feedback: list[str] = []
            streamed_answers: list[str] = []
            if updated.feedback or updated.suggested_answers:
                yield sse("status", {"label": "劳动者代理正在整理重点和建议回答"})
            for item in updated.feedback[:4]:
                streamed_feedback.append("")
                for offset in range(0, len(item), 5):
                    streamed_feedback[-1] += item[offset : offset + 5]
                    yield sse(
                        "counsel",
                        {
                            "feedback": streamed_feedback,
                            "suggested_answers": streamed_answers,
                        },
                    )
                    time.sleep(0.018)
            for item in updated.suggested_answers[:4]:
                streamed_answers.append("")
                for offset in range(0, len(item), 5):
                    streamed_answers[-1] += item[offset : offset + 5]
                    yield sse(
                        "counsel",
                        {
                            "feedback": streamed_feedback,
                            "suggested_answers": streamed_answers,
                        },
                    )
                    time.sleep(0.018)
            yield sse(
                "counsel",
                {
                    "feedback": updated.feedback[:4],
                    "suggested_answers": updated.suggested_answers[:4],
                },
            )
            serialized = SimulationRead.model_validate(updated).model_dump(mode="json")
            yield sse("complete", {"session": serialized})
        except Exception as exc:
            db.rollback()
            yield sse(
                "error",
                {"message": f"模拟回应生成失败：{type(exc).__name__}"},
            )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/simulations/{session_id}/complete", response_model=SimulationRead)
def finish_simulation(
    session_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
):
    session = db.get(SimulationSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="模拟会话不存在")
    load_case(db, session.case_id, principal)
    return complete_simulation(db, session)


@app.post("/cases/{case_id}/documents", response_model=DocumentRead, status_code=201)
def generate_document(
    case_id: str,
    payload: DocumentCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
):
    return create_document(db, load_case(db, case_id, principal), payload.document_type)


@app.get("/cases/{case_id}/authorities", response_model=list[AuthorityRead])
def authorities(
    case_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
):
    load_case(db, case_id, principal)
    return get_case_authorities(db, case_id)


@app.post("/feedback", status_code=201)
def add_feedback(
    payload: FeedbackCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
) -> dict:
    if payload.case_id:
        load_case(db, payload.case_id, principal)
    item = Feedback(**payload.model_dump())
    db.add(item)
    db.commit()
    return {"id": item.id, "accepted": True}


def require_case_privacy_manager(case: CaseFile, principal: Principal) -> None:
    if principal.actor_id != case.owner_id and "admin" not in principal.roles:
        raise HTTPException(status_code=403, detail="只有案件所有者或管理员可以管理模型授权")


@app.get("/cases/{case_id}/model-consents", response_model=list[ModelConsentRead])
def list_model_consents(
    case_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
) -> list[ModelDataConsent]:
    case = load_case(db, case_id, principal)
    require_case_privacy_manager(case, principal)
    return list(
        db.scalars(
            select(ModelDataConsent)
            .where(ModelDataConsent.case_id == case.id)
            .order_by(ModelDataConsent.version.desc())
        ).all()
    )


@app.post("/cases/{case_id}/model-consents", response_model=ModelConsentRead, status_code=201)
def grant_model_consent(
    case_id: str,
    payload: ModelConsentCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
) -> ModelDataConsent:
    case = load_case(db, case_id, principal)
    require_case_privacy_manager(case, principal)
    existing = list(
        db.scalars(
            select(ModelDataConsent)
            .where(
                ModelDataConsent.case_id == case.id,
                ModelDataConsent.provider == payload.provider,
            )
            .order_by(ModelDataConsent.version.desc())
        ).all()
    )
    now_value = datetime.now(timezone.utc)
    for item in existing:
        if item.status == "active":
            item.status = "revoked"
            item.revoked_at = now_value
    consent = ModelDataConsent(
        case_id=case.id,
        tenant_id=case.tenant_id,
        provider=payload.provider,
        purposes=list(dict.fromkeys(payload.purposes)),
        data_categories=list(dict.fromkeys(payload.data_categories)),
        version=max((item.version for item in existing), default=0) + 1,
        granted_by=principal.actor_id,
    )
    db.add(consent)
    db.flush()
    db.add(
        AuditEvent(
            case_id=case.id,
            event_type="model_consent_granted",
            agent=principal.actor_id,
            payload={
                "consent_id": consent.id,
                "version": consent.version,
                "provider": consent.provider,
                "purposes": consent.purposes,
                "data_categories": consent.data_categories,
            },
        )
    )
    db.commit()
    db.refresh(consent)
    return consent


@app.delete("/cases/{case_id}/model-consents/{consent_id}", status_code=204)
def revoke_model_consent(
    case_id: str,
    consent_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
) -> None:
    case = load_case(db, case_id, principal)
    require_case_privacy_manager(case, principal)
    consent = db.get(ModelDataConsent, consent_id)
    if not consent or consent.case_id != case.id:
        raise HTTPException(status_code=404, detail="模型授权不存在")
    if consent.status != "active":
        raise HTTPException(status_code=409, detail="模型授权已撤销")
    consent.status = "revoked"
    consent.revoked_at = datetime.now(timezone.utc)
    db.add(
        AuditEvent(
            case_id=case.id,
            event_type="model_consent_revoked",
            agent=principal.actor_id,
            payload={"consent_id": consent.id, "version": consent.version},
        )
    )
    db.commit()


@app.get("/cases/{case_id}/pseudonyms", response_model=list[PseudonymRead])
def list_case_pseudonyms(
    case_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
) -> list[CasePseudonym]:
    case = load_case(db, case_id, principal)
    require_case_privacy_manager(case, principal)
    return list(
        db.scalars(
            select(CasePseudonym)
            .where(CasePseudonym.case_id == case.id)
            .order_by(CasePseudonym.created_at)
        ).all()
    )


@app.post("/cases/{case_id}/pseudonyms", response_model=PseudonymRead, status_code=201)
def create_case_pseudonym(
    case_id: str,
    payload: PseudonymCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
) -> CasePseudonym:
    case = load_case(db, case_id, principal)
    require_case_privacy_manager(case, principal)
    if not settings.pseudonym_hmac_secret:
        raise HTTPException(status_code=503, detail="案件假名密钥未配置")
    entity_value = payload.entity_value.strip()
    fingerprint = entity_fingerprint(
        entity_value,
        secret=settings.pseudonym_hmac_secret,
        tenant_id=case.tenant_id,
        case_id=case.id,
    )
    existing = db.scalar(
        select(CasePseudonym).where(
            CasePseudonym.case_id == case.id,
            CasePseudonym.entity_fingerprint == fingerprint,
        )
    )
    if existing:
        return existing
    count = db.scalar(
        select(func.count())
        .select_from(CasePseudonym)
        .where(
            CasePseudonym.case_id == case.id,
            CasePseudonym.entity_type == payload.entity_type,
        )
    ) or 0
    labels = {"person": "当事人", "organization": "机构", "address": "地址", "other": "实体"}
    item = CasePseudonym(
        case_id=case.id,
        tenant_id=case.tenant_id,
        entity_fingerprint=fingerprint,
        source_length=len(entity_value),
        entity_type=payload.entity_type,
        pseudonym=f"{labels[payload.entity_type]}-{count + 1}",
        created_by=principal.actor_id,
    )
    db.add(item)
    db.flush()
    db.add(
        AuditEvent(
            case_id=case.id,
            event_type="case_pseudonym_created",
            agent=principal.actor_id,
            payload={
                "pseudonym_id": item.id,
                "entity_type": item.entity_type,
                "pseudonym": item.pseudonym,
                "source_length": item.source_length,
            },
        )
    )
    db.commit()
    db.refresh(item)
    return item
