from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authorities import search_authorities
from app.agent_contracts import SimulationTurnOutput
from app.model_gateway import ModelGateway, ModelGatewayError
from app.models import (
    AnalysisConclusion,
    AuditEvent,
    CaseFile,
    GeneratedDocument,
    LegalAuthority,
    SimulationSession,
)
from app.strategy_workflow import run_strategy
from app.analysis_lifecycle import invalidate_case_analyses
from app.reasoning import (
    build_reasoning_trace,
    calibrated_confidence,
    decision_gate,
    quality_metrics,
    validate_citation_support,
)


DISCLAIMER = "本分析基于当前录入信息，仅供法律信息与决策辅助，不构成律师意见，也不承诺案件结果。"


def analyze_case(db: Session, case: CaseFile, as_of: date) -> tuple[list[AnalysisConclusion], list[LegalAuthority], list[str], list[str]]:
    invalidate_case_analyses(db, case, "已生成更新版本的案件分析")
    fact_text = " ".join(f.content for f in case.facts)
    authorities = search_authorities(db, fact_text or case.title, as_of=as_of, region=case.region)
    evidence_names = " ".join(e.name + e.purpose for e in case.evidence)
    gaps: list[str] = []
    if not case.evidence:
        gaps.append("尚未登记证据；请优先保存劳动合同、工资流水、考勤和解除通知。")
    if not any(k in evidence_names for k in ["工资", "银行", "流水"]):
        gaps.append("缺少工资支付或工资标准证据。")
    if any(k in fact_text for k in ["解除", "辞退", "开除"]) and not any(k in evidence_names for k in ["解除", "通知", "聊天"]):
        gaps.append("缺少解除时间、理由及作出主体的证据。")

    strategy = run_strategy(db, case, authorities)
    worker = strategy["worker_argument"]
    employer = strategy["employer_argument"]
    assessment = strategy["assessment"]
    review = strategy["safety_review"]
    trace = build_reasoning_trace(case, authorities, as_of)
    metrics = quality_metrics(case, trace)
    authority_ids, rejected_ids = validate_citation_support(trace, assessment["authority_ids"])
    gate_reasons = decision_gate(metrics, authority_ids)
    if rejected_ids:
        gate_reasons.append(f"已拒绝{len(rejected_ids)}个不能支持当前争点的引用")
    viewpoint = f"{worker['position']}\n\n中立评估：{review['corrected_summary']}\n可能结果：{assessment['likely_outcome']}"
    counter = employer["position"] + " " + "；".join(employer["arguments"])
    confidence = min(assessment["confidence"], calibrated_confidence(metrics))
    if gate_reasons:
        confidence = min(confidence, 0.49)
    conclusion = AnalysisConclusion(
        case_id=case.id,
        viewpoint=viewpoint,
        counterargument=counter,
        confidence=round(confidence, 2),
        uncertainties=list(
            dict.fromkeys(gaps + assessment["uncertainties"] + review["problems"] + gate_reasons)
        ),
        authority_ids=authority_ids,
        reasoning_trace=trace,
        quality_metrics=metrics,
    )
    db.add(conclusion)
    case.stage = "strategy_ready"
    db.commit()
    db.refresh(conclusion)
    next_steps = ["按时间顺序确认关键事实", "补齐并备份原始证据", "核对仲裁时效和管辖", "形成明确仲裁请求及计算表"]
    return [conclusion], authorities, gaps, next_steps


def create_simulation(db: Session, case: CaseFile, scenario: str, user_role: str) -> SimulationSession:
    labels = {"negotiation": "协商", "arbitration": "劳动仲裁庭审", "hearing": "法庭质询"}
    facts = "；".join(f.content for f in case.facts[:3]) or "尚未确认具体事实"
    transcript = [
        {"role": "主持人", "content": f"现在开始{labels[scenario]}模拟。本次记录的事实为：{facts}。请劳动者陈述请求及依据。"},
        {"role": "用人单位代理人", "content": "我方要求劳动者明确劳动关系、请求金额、计算方法以及每项证据的证明目的。"},
        {"role": "仲裁员/法官", "content": "请先回答争议发生时间，并说明是否存在书面解除通知、工资流水及考勤记录。"},
    ]
    feedback = ["先说结论和请求，再按时间顺序陈述事实。", "对每项关键事实指出对应证据，避免只表达情绪。", "不确定的信息应明确说待核实，不要猜测。"]
    session = SimulationSession(case_id=case.id, scenario=scenario, user_role=user_role, transcript=transcript, feedback=feedback)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def continue_simulation(
    db: Session, session: SimulationSession, case: CaseFile, content: str
) -> SimulationSession:
    transcript = list(session.transcript)
    transcript.append({"role": "劳动者（你）", "content": content})
    facts = [
        {"content": fact.content, "status": fact.status, "source": fact.source}
        for fact in case.facts
    ]
    evidence = [
        {"name": item.name, "purpose": item.purpose, "authenticity": item.authenticity}
        for item in case.evidence
    ]
    gateway = ModelGateway()
    try:
        result = gateway.structured(
            system=(
                "你是中国大陆劳动争议仲裁庭模拟器。根据当前模拟角色，扮演仲裁员或用人单位代理人作出自然回应。"
                "不得把用户在模拟中的话写成已确认案件事实，不得编造证据或法律依据。"
                "每轮指出一到三个表达或举证改进点，并提出一个清晰的下一问题。"
            ),
            user=(
                f"场景：{session.scenario}\n案件已记录事实：{facts}\n案件证据：{evidence}\n"
                f"模拟对话：{transcript[-10:]}"
            ),
            schema=SimulationTurnOutput,
        )
    except ModelGatewayError:
        result = SimulationTurnOutput(
            speaker="仲裁员",
            reply="你的陈述已经记录在本次练习中。仲裁庭需要你把请求、关键时间和对应证据分别说明。",
            coaching_feedback=["先明确具体仲裁请求", "按时间顺序陈述", "说明每项事实对应的证据"],
            next_question="请说明你希望仲裁庭支持的具体请求，以及每项请求的金额或计算方式。",
        )
    transcript.append({"role": result.speaker, "content": result.reply})
    transcript.append({"role": "仲裁员追问", "content": result.next_question})
    session.transcript = transcript
    session.feedback = list(dict.fromkeys([*session.feedback, *result.coaching_feedback]))[-8:]
    db.add(session)
    db.add(
        AuditEvent(
            case_id=case.id,
            event_type="simulation_turn",
            agent="arbitration_simulator",
            payload={"session_id": session.id, "model_enabled": gateway.enabled},
        )
    )
    db.commit()
    db.refresh(session)
    return session


def create_document(db: Session, case: CaseFile, document_type: str) -> GeneratedDocument:
    facts = "\n".join(f"- {f.content}（{f.status}）" for f in case.facts) or "- [待补充并确认事实]"
    evidence = "\n".join(f"- {e.name}：拟证明{e.purpose}；真实性状态：{e.authenticity}" for e in case.evidence) or "- [待登记证据]"
    templates = {
        "arbitration_application": f"""劳动争议仲裁申请书（草稿）

申请人：[待填写]\n被申请人：[待填写]\n管辖仲裁委员会：[待核实]

仲裁请求\n1. [根据计算表填写具体请求与金额]

事实与理由\n{facts}

证据概览\n{evidence}

提示：提交前请核对主体信息、仲裁时效、请求金额和管辖，并由专业人士审阅。""",
        "evidence_list": f"证据目录（草稿）\n\n{evidence}\n\n每份证据应保留原件或原始载体，并注明来源和形成时间。",
        "timeline": f"案件时间线（草稿）\n\n{facts}",
        "hearing_outline": f"庭审提纲（草稿）\n\n一、请求：[待填写]\n二、关键事实\n{facts}\n三、证据对应\n{evidence}\n四、预判抗辩：劳动关系、解除理由、金额计算及仲裁时效。",
    }
    document = GeneratedDocument(case_id=case.id, document_type=document_type, content=templates[document_type])
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def get_case_authorities(db: Session, case_id: str) -> list[LegalAuthority]:
    analyses = db.scalars(select(AnalysisConclusion).where(AnalysisConclusion.case_id == case_id)).all()
    ids = {aid for analysis in analyses for aid in analysis.authority_ids}
    if not ids:
        return []
    return list(db.scalars(select(LegalAuthority).where(LegalAuthority.id.in_(ids))).all())
