import json
import time
from collections.abc import Callable
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.agent_contracts import JudicialAssessment, PartyArgument, SafetyReview
from app.coordinator import record_agent_task
from app.model_gateway import ModelGateway, ModelGatewayError, ModelRequestBudget
from app.observability import ModelCallTelemetry
from app.privacy import ModelCallAuthorization
from app.privacy_governance import build_model_authorization
from app.models import CaseFile, LegalAuthority


class StrategyState(TypedDict, total=False):
    case_id: str
    facts: list[dict]
    evidence: list[dict]
    authorities: list[dict]
    worker_argument: dict
    worker_execution: dict
    employer_argument: dict
    employer_execution: dict
    assessment: dict
    safety_review: dict
    protocol_version: str
    round_number: int


MAX_STRATEGY_FACTS = 30
MAX_STRATEGY_EVIDENCE = 20
MAX_STRATEGY_AUTHORITIES = 10


def _clip(value: str, limit: int) -> str:
    value = value.strip()
    return value if len(value) <= limit else f"{value[:limit]}…"


def _select_strategy_facts(case: CaseFile) -> list[dict]:
    status_priority = {
        "confirmed": 0,
        "evidence_supported": 1,
        "disputed": 2,
        "user_stated": 3,
        "unknown": 4,
        "inferred": 5,
    }
    ranked = sorted(case.facts, key=lambda fact: (status_priority.get(fact.status, 9), fact.id))
    return [
        {
            "id": fact.id,
            "content": _clip(fact.content, 600),
            "status": fact.status,
            "source": fact.source,
        }
        for fact in ranked[:MAX_STRATEGY_FACTS]
    ]


def _context(state: StrategyState) -> str:
    return json.dumps(
        {"facts": state["facts"], "evidence": state["evidence"], "authorities": state["authorities"]},
        ensure_ascii=False,
    )


def _allowed_ids(state: StrategyState) -> set[str]:
    return {item["id"] for item in state["authorities"]}


def _filter_ids(ids: list[str], state: StrategyState) -> list[str]:
    allowed = _allowed_ids(state)
    return [item for item in ids if item in allowed]


def build_strategy_workflow(
    db: Session,
    gateway_factory: Callable[[], ModelGateway] | None = None,
    authorization: ModelCallAuthorization | None = None,
):
    gateway_factory = gateway_factory or ModelGateway

    def execution_result(
        *,
        started: float,
        telemetry: ModelCallTelemetry | None,
        fallback_used: bool,
    ) -> dict:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "status": "fallback" if fallback_used else "completed",
            "duration_ms": telemetry.duration_ms if telemetry else elapsed_ms,
            "model_enabled": telemetry is None or telemetry.outcome != "disabled",
            "fallback_used": fallback_used,
            "error_type": telemetry.error_type if telemetry else None,
        }

    def audit(case_id: str, agent: str, duration_ms: float, payload: dict) -> None:
        objectives = {
            "worker_advocate": "基于事实、证据和候选法条形成劳动者主张",
            "employer_advocate": "基于事实、证据和候选法条独立形成单位抗辩",
            "neutral_adjudicator": "逐项评估双方主张并形成中立判断",
            "safety_reviewer": "执行引用、事实边界和人工接管的确定性发布安全门",
        }
        record_agent_task(
            db,
            case_id=case_id,
            agent=agent,
            task_type="legal_strategy",
            objective=objectives[agent],
            input_refs={"case_id": case_id},
            output={"strategy_protocol": "legal-debate-v2", **payload},
            duration_ms=duration_ms,
            constraints={"max_attempts": 2, "timeout_seconds": 60, "max_rounds": 1},
        )
        db.commit()

    def worker_agent(state: StrategyState) -> StrategyState:
        started = time.perf_counter()
        gateway = gateway_factory()
        fallback_used = False
        try:
            result = gateway.structured(
                system="你是中国大陆劳动争议中的劳动者代理人。仅基于给定事实、证据和候选依据形成最有力但审慎的论证。",
                user=_context(state),
                schema=PartyArgument,
                authorization=authorization,
            )
        except ModelGatewayError:
            fallback_used = True
            result = PartyArgument(
                position="现有陈述显示劳动者可能具有可主张的劳动权益，但需补强证据。",
                arguments=["围绕劳动关系、争议行为、损失及请求计算逐项举证。"],
                evidence_needed=["劳动合同或工作管理记录", "工资流水", "争议行为相关通知或聊天记录"],
                authority_ids=list(_allowed_ids(state)),
            )
        data = result.model_dump()
        data["authority_ids"] = _filter_ids(data["authority_ids"], state)
        return {
            "worker_argument": data,
            "worker_execution": execution_result(
                started=started,
                telemetry=gateway.last_telemetry,
                fallback_used=fallback_used,
            ),
        }

    def employer_agent(state: StrategyState) -> StrategyState:
        started = time.perf_counter()
        gateway = gateway_factory()
        fallback_used = False
        try:
            result = gateway.structured(
                system=(
                    "你是中国大陆劳动争议中的用人单位代理人。仅基于给定事实、证据和"
                    "候选依据，独立识别事实、证据、计算、程序和时效方面最可能成立的抗辩。"
                    "不得假设或读取劳动者代理的观点，不得捏造新事实。"
                ),
                user=_context(state),
                schema=PartyArgument,
                authorization=authorization,
            )
        except ModelGatewayError:
            fallback_used = True
            result = PartyArgument(
                position="用人单位可能从劳动关系、行为合法性、金额计算和仲裁时效提出抗辩。",
                arguments=["要求劳动者对每项构成要件承担相应举证责任。"],
                evidence_needed=["单位规章制度及送达记录", "工资和考勤原始记录"],
                authority_ids=list(_allowed_ids(state)),
            )
        data = result.model_dump()
        data["authority_ids"] = _filter_ids(data["authority_ids"], state)
        return {
            "employer_argument": data,
            "employer_execution": execution_result(
                started=started,
                telemetry=gateway.last_telemetry,
                fallback_used=fallback_used,
            ),
        }

    def record_party_tasks(state: StrategyState) -> StrategyState:
        for agent, output_key, execution_key in (
            ("worker_advocate", "worker_argument", "worker_execution"),
            ("employer_advocate", "employer_argument", "employer_execution"),
        ):
            execution = state[execution_key]
            audit(
                state["case_id"],
                agent,
                execution["duration_ms"],
                {"output": state[output_key], "execution": execution},
            )
        return {}

    def judge_agent(state: StrategyState) -> StrategyState:
        started = time.perf_counter()
        gateway = gateway_factory()
        fallback_used = False
        prompt = _context(state) + "\n双方观点：" + json.dumps(
            {"worker": state["worker_argument"], "employer": state["employer_argument"]}, ensure_ascii=False
        )
        try:
            result = gateway.structured(
                system="你是中立的劳动争议裁判评估者。逐项列出争点、举证责任、双方强弱与待核实事项。不得给出伪精确胜诉率。",
                user=prompt,
                schema=JudicialAssessment,
                authorization=authorization,
            )
        except ModelGatewayError:
            fallback_used = True
            result = JudicialAssessment(
                issues=["劳动关系是否成立", "争议行为是否合法", "请求金额与时效是否成立"],
                assessment="现有信息只能支持初步分析；关键原始证据和完整时间线尚未经过核验。",
                likely_outcome="信息不足，可能支持、部分支持或不支持，取决于补充证据。",
                confidence=min(0.8, 0.35 + len(state["facts"]) * 0.06 + len(state["evidence"]) * 0.08),
                uncertainties=["事实尚属用户陈述", "需核对证据原件及当地裁审口径"],
                authority_ids=list(_allowed_ids(state)),
            )
        data = result.model_dump()
        data["authority_ids"] = _filter_ids(data["authority_ids"], state)
        execution = execution_result(
            started=started,
            telemetry=gateway.last_telemetry,
            fallback_used=fallback_used,
        )
        audit(
            state["case_id"],
            "neutral_adjudicator",
            execution["duration_ms"],
            {"output": data, "execution": execution},
        )
        return {"assessment": data}

    def safety_gate(state: StrategyState) -> StrategyState:
        started = time.perf_counter()
        assessment_ids = set(state["assessment"].get("authority_ids", []))
        allowed_ids = _allowed_ids(state)
        invalid_citations = not assessment_ids or not assessment_ids <= allowed_ids
        inferred_as_fact = any(fact.get("status") == "inferred" for fact in state["facts"])
        low_confidence = state["assessment"]["confidence"] < 0.45
        party_fallback = any(
            state.get(key, {}).get("fallback_used", False)
            for key in ("worker_execution", "employer_execution")
        )
        summary = state["assessment"]["assessment"]
        problems: list[str] = []
        if invalid_citations:
            problems.append("裁判评估缺少可核验依据")
        if inferred_as_fact:
            problems.append("存在模型推断事实，必须由用户确认")
        if low_confidence:
            problems.append("裁判评估置信度过低，必须人工复核")
        if party_fallback:
            problems.append("一方或双方观点使用了确定性备用回答")
        if invalid_citations:
            decision = "block"
            corrected_summary = "当前分析未通过引用安全校验，暂不提供可依赖的法律结论，需补充依据并由人工复核。"
        elif problems:
            decision = "escalate"
            corrected_summary = summary
        else:
            decision = "pass"
            corrected_summary = summary
        review = SafetyReview(
            decision=decision,
            approved=decision == "pass",
            problems=problems,
            corrected_summary=corrected_summary,
            requires_human_lawyer=decision != "pass",
            publishable_authority_ids=(
                [item for item in state["assessment"].get("authority_ids", []) if item in allowed_ids]
                if decision != "block"
                else []
            ),
        ).model_dump()
        audit(
            state["case_id"],
            "safety_reviewer",
            round((time.perf_counter() - started) * 1000, 2),
            review,
        )
        return {"safety_review": review}

    graph = StateGraph(StrategyState)
    graph.add_node("worker_advocate", worker_agent)
    graph.add_node("employer_advocate", employer_agent)
    graph.add_node("record_party_tasks", record_party_tasks)
    graph.add_node("neutral_adjudicator", judge_agent)
    graph.add_node("safety_reviewer", safety_gate)
    graph.add_edge(START, "worker_advocate")
    graph.add_edge(START, "employer_advocate")
    graph.add_edge(["worker_advocate", "employer_advocate"], "record_party_tasks")
    graph.add_edge("record_party_tasks", "neutral_adjudicator")
    graph.add_edge("neutral_adjudicator", "safety_reviewer")
    graph.add_edge("safety_reviewer", END)
    return graph.compile()


def run_strategy(db: Session, case: CaseFile, authorities: list[LegalAuthority]) -> StrategyState:
    gateway_settings = ModelGateway().settings
    logical_calls = gateway_settings.analysis_model_call_budget
    request_budget = ModelRequestBudget(
        max_logical_calls=logical_calls,
        max_http_requests=logical_calls * gateway_settings.model_http_request_multiplier,
    )
    authorization = build_model_authorization(
        db,
        case_id=case.id,
        tenant_id=case.tenant_id,
        purpose="analysis",
        settings=gateway_settings,
    )
    db.commit()
    initial: StrategyState = {
        "case_id": case.id,
        "protocol_version": "legal-debate-v2",
        "round_number": 1,
        "facts": _select_strategy_facts(case),
        "evidence": [
            {
                "id": item.id,
                "name": _clip(item.name, 200),
                "purpose": _clip(item.purpose, 500),
                "authenticity": item.authenticity,
            }
            for item in case.evidence[:MAX_STRATEGY_EVIDENCE]
        ],
        "authorities": [
            {
                "id": item.id,
                "title": _clip(item.title, 300),
                "article": item.article,
                "content": _clip(item.content, 1200),
            }
            for item in authorities[:MAX_STRATEGY_AUTHORITIES]
        ],
    }
    return build_strategy_workflow(
        db,
        gateway_factory=lambda: ModelGateway(
            gateway_settings,
            request_budget=request_budget,
        ),
        authorization=authorization,
    ).invoke(initial)
