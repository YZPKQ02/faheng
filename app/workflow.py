import json
import re
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.agent_contracts import (
    ConversationExecutionPlan,
    ConversationOutput,
    ConversationPlan,
    ExecutionStep,
)
from app.analysis_lifecycle import invalidate_case_analyses
from app.authorities import search_authorities
from app.config import get_settings
from app.conversation_memory import ConversationMemory, build_conversation_memory
from app.model_gateway import ModelGateway, ModelGatewayError, ModelRequestBudget
from app.observability import record_model_call_metric
from app.legal_rag import build_rag_observations, render_citations, validate_authority_ids
from app.privacy_governance import build_model_authorization
from app.models import AuditEvent, CaseFile, Fact, Message
from app.worker_counsel import refresh_worker_counsel_memory


class AdvisorState(TypedDict, total=False):
    case_id: str
    current_message_id: str
    user_message: str
    category: str
    urgency: str
    extracted_facts: list[str]
    missing_information: list[str]
    memory: ConversationMemory
    plan: dict
    execution_plan: dict
    plan_source: str
    authority_ids: list[str]
    tool_call_count: int
    step_results: dict
    response: str


SUBSCENARIOS = {
    "违法解除": ["辞退", "解除", "开除", "裁员"],
    "拖欠工资": ["欠薪", "拖欠", "工资没发", "工资"],
    "加班费": ["加班", "996", "调休"],
    "未签劳动合同": ["没签合同", "未签", "劳动合同"],
    "经济补偿": ["补偿金", "经济补偿", "赔偿金"],
}

REQUIRED_INFORMATION = {
    "违法解除": [
        ("入职日期", ["入职", "工作了", "工龄"]),
        ("解除日期", ["解除日期", "辞退日期", "昨天", "今天", "通知不用上班"]),
        ("解除理由或通知", ["解除理由", "辞退理由", "通知", "开除原因", "没有书面"]),
        ("解除前十二个月平均工资", ["平均工资", "月工资", "每月", "工资标准"]),
    ],
    "拖欠工资": [
        ("欠薪月份", ["欠薪月份", "拖欠", "个月工资", "工资没发"]),
        ("约定工资", ["约定工资", "月工资", "每月", "工资标准"]),
        ("实际发薪记录", ["工资流水", "发薪记录", "银行流水"]),
        ("是否仍在职", ["仍在职", "已经离职", "还在职", "离职"]),
    ],
    "加班费": [
        ("加班日期和时长", ["加班日期", "加班时长", "小时", "996"]),
        ("考勤记录", ["考勤", "打卡", "排班"]),
        ("工资基数", ["工资基数", "月工资", "每月"]),
        ("是否安排调休", ["调休", "补休"]),
    ],
    "未签劳动合同": [
        ("入职日期", ["入职", "工作了", "一年多"]),
        ("合同签订情况", ["没签合同", "未签", "劳动合同"]),
        ("工资流水", ["工资流水", "银行流水", "转账"]),
        ("工作管理证据", ["考勤", "工牌", "工作群", "管理"]),
    ],
    "经济补偿": [
        ("入职及离职日期", ["入职", "离职日期", "工作了", "工龄"]),
        ("离职原因", ["离职原因", "辞退", "解除", "裁员"]),
        ("平均工资", ["平均工资", "月工资", "每月"]),
        ("离职文件", ["离职证明", "解除通知", "辞退通知"]),
    ],
}

DEFAULT_REQUIRED = [
    ("争议发生时间", ["发生时间", "日期", "昨天", "今天"]),
    ("工作地点", ["工作地点", "办公地点", "城市"]),
    ("核心诉求", ["希望", "要求", "诉求", "怎么办", "可以主张"]),
    ("已有证据", ["证据", "记录", "流水", "通知", "合同"]),
]

LIST_PREFIX = re.compile(r"^\s*(?:(?:\d{1,2})\s*[.、．)]|[（(]\d{1,2}[)）])\s*")


def _strip_list_prefix(value: str) -> str:
    cleaned = value.strip()
    while cleaned and (match := LIST_PREFIX.match(cleaned)):
        cleaned = cleaned[match.end() :].strip()
    return cleaned


def _format_follow_up_questions(items: list[str]) -> str:
    cleaned: list[str] = []
    for item in items:
        question = _strip_list_prefix(item)
        if question and question not in cleaned:
            cleaned.append(question)
    return "\n".join(f"{index}. {item}" for index, item in enumerate(cleaned[:3], start=1))


def _context_text(memory: ConversationMemory) -> str:
    parts = [memory["initial_issue"], memory["current_user_message"]]
    parts.extend(item["content"] for item in memory["conversation_history"])
    parts.extend(item["content"] for item in memory["known_facts"])
    return "\n".join(parts)


def _missing_information(category: str, memory: ConversationMemory) -> list[str]:
    context = _context_text(memory)
    required = REQUIRED_INFORMATION.get(category, DEFAULT_REQUIRED)
    return [label for label, signals in required if not any(signal in context for signal in signals)]


def _default_plan(state: AdvisorState) -> ConversationPlan:
    memory = state["memory"]
    current = memory["current_user_message"]
    history = memory["conversation_history"]
    if len(current) <= 12 and history:
        focus = f"结合上一轮对话理解本次补充“{current}”"
    else:
        focus = current[:160]
    return ConversationPlan(
        question_focus=focus,
        user_intent=f"获得{state['category']}问题的直接答复与下一步建议",
        relevant_fact_ids=[item["id"] for item in memory["known_facts"]],
        information_gaps=state.get("missing_information", []),
        action="retrieve_authorities",
        retrieval_query=f"{state['category']} {memory['initial_issue']} {current}"[:1200],
    )


def _compile_execution_plan(plan: ConversationPlan) -> ConversationExecutionPlan:
    """Compile model suggestions into a fixed, budgeted application-owned plan."""
    should_retrieve = plan.action == "retrieve_authorities"
    return ConversationExecutionPlan(
        goal=plan.user_intent,
        max_replans=0,
        steps=[
            ExecutionStep(
                step_id="persist_facts",
                objective="Persist user-stated facts and invalidate stale analysis when needed",
                executor="deterministic",
                success_condition="Every extracted fact is persisted at most once",
            ),
            ExecutionStep(
                step_id="retrieve_authorities",
                objective=(
                    "Retrieve current authorities relevant to the question focus"
                    if should_retrieve
                    else "Skip retrieval because the coarse plan requires clarification or escalation"
                ),
                executor="bounded_react",
                allowed_tools=["search_authorities"] if should_retrieve else [],
                max_tool_calls=2 if should_retrieve else 0,
                success_condition="Return validated authority IDs or an explicit empty result",
            ),
            ExecutionStep(
                step_id="compose_response",
                objective="Answer the current question using only case context and retrieved authorities",
                executor="structured_model",
                success_condition="Return a schema-valid answer or deterministic fallback",
            ),
        ],
    )


def build_workflow(db: Session, gateway: ModelGateway | None = None):
    if gateway is None:
        settings = get_settings()
        logical_calls = settings.intake_model_call_budget
        gateway = ModelGateway(
            settings,
            request_budget=ModelRequestBudget(
                max_logical_calls=logical_calls,
                max_http_requests=logical_calls * settings.model_http_request_multiplier,
            ),
        )

    def model_authorization(state: AdvisorState):
        if not isinstance(gateway, ModelGateway):
            return None
        case = db.get(CaseFile, state["case_id"])
        return build_model_authorization(
            db,
            case_id=state["case_id"],
            tenant_id=case.tenant_id,
            purpose="intake",
            settings=gateway.settings,
        )

    def observe(state: AdvisorState) -> AdvisorState:
        memory = build_conversation_memory(
            db,
            case_id=state["case_id"],
            current_message_id=state["current_message_id"],
            current_user_message=state["user_message"],
        )
        topic_text = f"{memory['initial_issue']}\n{memory['current_user_message']}"
        category = next(
            (
                name
                for name, words in SUBSCENARIOS.items()
                if any(word in topic_text for word in words)
            ),
            "一般劳动争议",
        )
        urgency = (
            "high"
            if any(
                word in topic_text
                for word in ["明天开庭", "即将过期", "已经收到传票", "自杀", "暴力"]
            )
            else "medium"
        )
        text = state["user_message"].strip()
        facts = [part.strip() for part in re.split(r"[。；\n]", text) if len(part.strip()) >= 4]
        return {
            "memory": memory,
            "category": category,
            "urgency": urgency,
            "extracted_facts": facts,
            "missing_information": _missing_information(category, memory),
        }

    def create_plan(state: AdvisorState) -> AdvisorState:
        plan = _default_plan(state)
        plan_source = "deterministic"
        if getattr(gateway, "enabled", True):
            try:
                candidate = gateway.structured(
                    system=(
                        "你是劳动争议咨询的粗粒度规划器。只定义当前问题的目标、信息缺口和检索意图，"
                        "不执行工具、不输出详细思维链，也不能增加应用未授权的步骤。"
                        "也不回答问题。当前用户消息优先级最高；历史消息只用于消解指代和延续上下文。"
                        "不得发明事实或事实 ID。action 表示下一步应执行的唯一动作。"
                    ),
                    user=json.dumps(
                        {
                            "category": state["category"],
                            "missing_information": state["missing_information"],
                            "memory": state["memory"],
                        },
                        ensure_ascii=False,
                    ),
                    schema=ConversationPlan,
                    authorization=model_authorization(state),
                )
                valid_fact_ids = {item["id"] for item in state["memory"]["known_facts"]}
                candidate.relevant_fact_ids = [
                    fact_id for fact_id in candidate.relevant_fact_ids if fact_id in valid_fact_ids
                ]
                candidate.retrieval_query = (
                    f"{state['category']} {state['memory']['current_user_message']} "
                    f"{candidate.retrieval_query}"
                )[:1200]
                plan = candidate
                plan_source = "model"
            except ModelGatewayError:
                plan_source = "deterministic_fallback"
            finally:
                if isinstance(gateway, ModelGateway):
                    record_model_call_metric(
                        db,
                        case_id=state["case_id"],
                        phase="intake_plan",
                        telemetry=gateway.last_telemetry,
                    )
        execution_plan = _compile_execution_plan(plan)
        db.add(
            AuditEvent(
                case_id=state["case_id"],
                event_type="execution_plan_created",
                agent="intake_coordinator",
                payload={
                    **execution_plan.model_dump(),
                    "source": plan_source,
                    "question_focus": plan.question_focus,
                    "information_gaps": plan.information_gaps,
                },
            )
        )
        db.commit()
        return {
            "plan": plan.model_dump(),
            "execution_plan": execution_plan.model_dump(),
            "plan_source": plan_source,
            "tool_call_count": 0,
            "step_results": {},
        }

    def persist_facts(state: AdvisorState) -> AdvisorState:
        case = db.get(CaseFile, state["case_id"])
        added_fact = False
        for content in state.get("extracted_facts", []):
            if not any(fact.content == content for fact in case.facts):
                db.add(
                    Fact(
                        case_id=case.id,
                        content=content,
                        source="user",
                        status="user_stated",
                    )
                )
                added_fact = True
        if added_fact:
            invalidate_case_analyses(db, case, "用户补充了新的案件事实")
        case.stage = "fact_gathering"
        case.risk_level = state["urgency"]
        result = {"added_fact": added_fact, "status": "completed"}
        db.add(
            AuditEvent(
                case_id=case.id,
                event_type="plan_step_completed",
                agent="intake_coordinator",
                payload={
                    "protocol": "plan-execute-react-v1",
                    "step_id": "persist_facts",
                    "executor": "deterministic",
                    "result": result,
                },
            )
        )
        db.commit()
        return {"step_results": {**state.get("step_results", {}), "persist_facts": result}}

    def retrieve_authorities(state: AdvisorState) -> AdvisorState:
        case = db.get(CaseFile, state["case_id"])
        step = next(
            item
            for item in state["execution_plan"]["steps"]
            if item["step_id"] == "retrieve_authorities"
        )
        queries = [
            (
                f"{state['plan']['retrieval_query']} {state['memory']['initial_issue']} "
                f"{state['user_message']}"
            )[:1600],
            f"{state['category']} {state['plan']['question_focus']}"[:1200],
        ]
        authority_ids: list[str] = []
        attempts: list[dict] = []
        for query in queries[: step["max_tool_calls"]]:
            authorities = search_authorities(
                db,
                query,
                case_id=case.id,
                tenant_id=case.tenant_id,
            )
            authority_ids = list(dict.fromkeys(item.id for item in authorities))
            attempts.append(
                {
                    "action": "retrieve_authorities",
                    "query": query,
                    "authority_ids": authority_ids,
                }
            )
            if authority_ids:
                break
        result = {
            "status": "completed" if authority_ids else "completed_empty",
            "tool_calls": len(attempts),
            "authority_ids": authority_ids,
        }
        db.add_all(
            [
                AuditEvent(
                    case_id=case.id,
                    event_type="react_plan",
                    agent="intake_coordinator",
                    payload={
                        "protocol": "plan-execute-react-v1",
                        "source": state["plan_source"],
                        "question_focus": state["plan"]["question_focus"],
                        "user_intent": state["plan"]["user_intent"],
                        "action": "retrieve_authorities",
                        "retrieval_query": state["plan"]["retrieval_query"],
                        "information_gaps": state["plan"]["information_gaps"],
                        "budget": {"max_tool_calls": step["max_tool_calls"]},
                        "context_refs": {
                            "message_ids": [
                                item["id"] for item in state["memory"]["conversation_history"]
                            ],
                            "fact_ids": state["plan"]["relevant_fact_ids"],
                            "evidence_ids": [
                                item["id"] for item in state["memory"]["evidence_summary"]
                            ],
                        },
                    },
                ),
                AuditEvent(
                    case_id=case.id,
                    event_type="react_action",
                    agent="legal_research",
                    payload={
                        "protocol": "plan-execute-react-v1",
                        "step_id": "retrieve_authorities",
                        "allowed_tools": step["allowed_tools"],
                        "attempts": attempts,
                        **result,
                    },
                ),
                AuditEvent(
                    case_id=case.id,
                    event_type="plan_step_completed",
                    agent="legal_research",
                    payload={
                        "protocol": "plan-execute-react-v1",
                        "step_id": "retrieve_authorities",
                        "executor": "bounded_react",
                        "result": result,
                    },
                ),
            ]
        )
        db.commit()
        return {
            "authority_ids": authority_ids,
            "tool_call_count": len(attempts),
            "step_results": {
                **state.get("step_results", {}),
                "retrieve_authorities": result,
            },
        }

    def validate_execution(state: AdvisorState) -> AdvisorState:
        current_case = db.get(CaseFile, state["case_id"])
        valid_ids = validate_authority_ids(
            db,
            state.get("authority_ids", []),
            region=current_case.region if current_case else "中国大陆",
        )
        step = next(
            item
            for item in state["execution_plan"]["steps"]
            if item["step_id"] == "retrieve_authorities"
        )
        budget_ok = state.get("tool_call_count", 0) <= step["max_tool_calls"]
        db.add(
            AuditEvent(
                case_id=state["case_id"],
                event_type="execution_validated",
                agent="intake_coordinator",
                payload={
                    "protocol": "plan-execute-react-v1",
                    "authority_ids": valid_ids,
                    "tool_call_count": state.get("tool_call_count", 0),
                    "budget_ok": budget_ok,
                    "replans_used": 0,
                },
            )
        )
        db.commit()
        return {"authority_ids": valid_ids}

    def respond(state: AdvisorState) -> AdvisorState:
        current_case = db.get(CaseFile, state["case_id"])
        current_region = current_case.region if current_case else "中国大陆"
        authority_context = build_rag_observations(db, state.get("authority_ids", []))
        citations = render_citations(authority_context)
        missing = "、".join(state.get("missing_information", [])[:4])
        focus = _strip_list_prefix(state["plan"]["question_focus"]).strip("：:。 ")
        try:
            output = gateway.structured(
                system=(
                    "你是贯穿本案诉求收集、案件准备和仲裁训练的劳动者代理。当前处于诉求收集阶段。"
                    "系统已完成粗粒度规划、"
                    "确定性事实处理和有界法律检索。现在只输出最终答复，不展示详细思维链。"
                    "必须先直接回应当前用户消息，不得转而回答更早的问题；历史消息只用于消解指代。"
                    "说明基于哪些用户陈述，并区分陈述与已确认事实。仅使用候选法律依据，不得凭记忆补充法条。"
                    "authority_ids 只能填写检索观察提供的 authority_id；没有可靠依据时返回空列表。"
                    "信息不足时提出最多三个有明确目的的追问，追问文本不要自带序号。"
                    "需要分点时，每一点必须单独占一行，不得把多个编号或项目挤在同一段。"
                    "只用 Markdown 的 **加粗** 适度突出关键结论、金额、期限和下一步动作，"
                    "不要整段加粗，也不要使用复杂表格。"
                    "不得承诺胜诉或使用伪精确概率。高风险时建议尽快咨询真人律师。"
                ),
                user=json.dumps(
                    {
                        "current_user_message_highest_priority": state["memory"][
                            "current_user_message"
                        ],
                        "question_focus": focus,
                        "execution_protocol": "plan-execute-react-v1",
                        "coarse_plan": state["plan"],
                        "step_results": state.get("step_results", {}),
                        "conversation_memory": state["memory"],
                        "retrieval_observation": authority_context,
                        "suggested_information_gaps": state.get("missing_information", []),
                    },
                    ensure_ascii=False,
                ),
                schema=ConversationOutput,
                authorization=model_authorization(state),
            )
            response = (
                f"**问题焦点**：{focus or state['memory']['current_user_message'][:160]}"
                f"\n\n{output.answer.strip()}"
            )
            questions = _format_follow_up_questions(output.follow_up_questions)
            if questions:
                response += f"\n\n**为了进一步判断，请补充**：\n{questions}"
            if output.should_escalate:
                response += (
                    "\n\n**建议尽快咨询真人律师**："
                    f"{output.escalation_reason or '本事项存在较高法律风险'}。"
                )
            requested_ids = output.authority_ids or state.get("authority_ids", [])
            cited_ids = validate_authority_ids(db, requested_ids, region=current_region)
            cited_ids = [
                authority_id
                for authority_id in cited_ids
                if authority_id in state.get("authority_ids", [])
            ]
            rejected_ids = [item for item in output.authority_ids if item not in cited_ids]
            citations = render_citations(build_rag_observations(db, cited_ids))
            if rejected_ids:
                db.add(
                    AuditEvent(
                        case_id=state["case_id"],
                        event_type="citation_validation_rejected",
                        agent="intake_coordinator",
                        payload={"rejected_authority_ids": rejected_ids},
                    )
                )
            response += f"\n\n**可核验依据**：{citations or '当前没有检索到可靠依据'}。"
        except ModelGatewayError as exc:
            response = (
                f"**问题焦点**：{focus or state['memory']['current_user_message'][:160]}\n\n"
                f"初步分诊为“{state['category']}”。目前仅根据你的陈述记录事实，"
                "尚未将任何推测视为已确认事实。\n\n"
                f"**可能相关的依据**：{citations or '暂无可靠依据'}。\n\n"
                f"**下一步需要确认**：{missing or '当前关键信息是否完整'}。\n\n"
                "你可以继续补充，也可以登记证据后生成完整分析。"
                "\n\n**提示**：结果仅供决策辅助，不能替代律师针对原始材料的审查。"
            )
            db.add(
                AuditEvent(
                    case_id=state["case_id"],
                    event_type="model_fallback",
                    agent="intake_coordinator",
                    payload={"reason": str(exc), "protocol": "plan-execute-react-v1"},
                )
            )
            db.commit()
        finally:
            if isinstance(gateway, ModelGateway):
                record_model_call_metric(
                    db,
                    case_id=state["case_id"],
                    phase="intake_response",
                    telemetry=gateway.last_telemetry,
                )
        db.add(
            AuditEvent(
                case_id=state["case_id"],
                event_type="plan_step_completed",
                agent="intake_coordinator",
                payload={
                    "protocol": "plan-execute-react-v1",
                    "step_id": "compose_response",
                    "executor": "structured_model",
                    "result": {"status": "completed"},
                },
            )
        )
        db.commit()
        return {"response": response}

    graph = StateGraph(AdvisorState)
    graph.add_node("observe", observe)
    graph.add_node("create_plan", create_plan)
    graph.add_node("persist_facts", persist_facts)
    graph.add_node("retrieve_authorities", retrieve_authorities)
    graph.add_node("validate_execution", validate_execution)
    graph.add_node("respond", respond)
    graph.add_edge(START, "observe")
    graph.add_edge("observe", "create_plan")
    graph.add_edge("create_plan", "persist_facts")
    graph.add_edge("persist_facts", "retrieve_authorities")
    graph.add_edge("retrieve_authorities", "validate_execution")
    graph.add_edge("validate_execution", "respond")
    graph.add_edge("respond", END)
    return graph.compile()


def run_intake(
    db: Session,
    case: CaseFile,
    content: str,
    gateway: ModelGateway | None = None,
) -> tuple[Message, AdvisorState]:
    user_message = Message(case_id=case.id, role="user", content=content)
    db.add(user_message)
    db.commit()
    db.refresh(user_message)
    result = build_workflow(db, gateway).invoke(
        {
            "case_id": case.id,
            "current_message_id": user_message.id,
            "user_message": content,
        }
    )
    message = Message(
        case_id=case.id,
        role="assistant",
        agent="worker_counsel",
        content=result["response"],
    )
    db.add(message)
    db.flush()
    refresh_worker_counsel_memory(
        db,
        case,
        trigger="intake_turn_completed",
        pending_questions=result.get("missing_information", []),
    )
    db.commit()
    db.refresh(message)
    return message, result
