from app.agent_contracts import ConversationOutput, ConversationPlan
from app.database import SessionLocal
from app.models import AuditEvent, CaseFile
from app.workflow import _compile_execution_plan, _format_follow_up_questions, run_intake


class RecordingGateway:
    enabled = True

    def __init__(self):
        self.calls: list[tuple[type, str]] = []

    def structured(self, *, system: str, user: str, schema: type, authorization=None):
        self.calls.append((schema, user))
        if schema is ConversationPlan:
            return ConversationPlan(
                question_focus="公司解除劳动关系后是否应当支付赔偿",
                user_intent="确认解除后的可主张权利",
                relevant_fact_ids=["not-a-real-fact-id"],
                information_gaps=["解除通知"],
                action="retrieve_authorities",
                retrieval_query="违法解除 赔偿",
            )
        if schema is ConversationOutput:
            return ConversationOutput(
                answer="公司是否需要承担责任，要结合解除理由、程序和现有证据判断。",
                follow_up_questions=["1. 是否有解除通知？", "2、是否签订劳动合同？"],
            )
        raise AssertionError(f"unexpected schema: {schema}")


def test_follow_up_question_formatter_owns_numbering():
    assert _format_follow_up_questions(
        ["1. 1. 是否有解除通知？", "2、是否签订劳动合同？", "是否签订劳动合同？"]
    ) == "1. 是否有解除通知？\n2. 是否签订劳动合同？"


def test_clarification_plan_skips_react_tool_budget():
    plan = ConversationPlan(
        question_focus="确认用户当前诉求",
        user_intent="补充关键信息",
        relevant_fact_ids=[],
        information_gaps=["争议类型"],
        action="clarify",
        retrieval_query="",
    )

    execution_plan = _compile_execution_plan(plan)
    retrieval = next(
        step for step in execution_plan.steps if step.step_id == "retrieve_authorities"
    )

    assert retrieval.max_tool_calls == 0
    assert retrieval.allowed_tools == []


def test_react_workflow_pins_current_question_and_audits_actions(client):
    case_payload = client.post("/cases", json={"title": "上下文测试"}).json()
    client.post(
        f"/cases/{case_payload['id']}/messages",
        json={"content": "公司通知要解除劳动关系，但没有说明理由。"},
    )
    gateway = RecordingGateway()

    with SessionLocal() as db:
        case = db.get(CaseFile, case_payload["id"])
        message, state = run_intake(
            db,
            case,
            "我现在最想知道，公司是否应该赔偿我？",
            gateway=gateway,
        )
        events = [
            event
            for event in db.query(AuditEvent).filter(AuditEvent.case_id == case.id).all()
            if event.event_type in {"react_plan", "react_action"}
        ]

    assert state["memory"]["current_user_message"] == "我现在最想知道，公司是否应该赔偿我？"
    assert state["memory"]["initial_issue"] == "公司通知要解除劳动关系，但没有说明理由。"
    plan_event = next(event for event in events if event.event_type == "react_plan")
    assert "not-a-real-fact-id" not in plan_event.payload["context_refs"]["fact_ids"]
    assert {event.event_type for event in events} == {"react_plan", "react_action"}
    assert plan_event.payload["protocol"] == "plan-execute-react-v1"
    assert plan_event.payload["budget"]["max_tool_calls"] == 2
    assert [step["step_id"] for step in state["execution_plan"]["steps"]] == [
        "persist_facts",
        "retrieve_authorities",
        "compose_response",
    ]
    assert state["tool_call_count"] <= 2
    assert "1. 1." not in message.content
    assert "1. 是否有解除通知？" in message.content
    assert len(gateway.calls) == 2
    assert "我现在最想知道，公司是否应该赔偿我？" in gateway.calls[0][1]
    assert "我现在最想知道，公司是否应该赔偿我？" in gateway.calls[1][1]


def test_bounded_react_stops_after_retrieval_budget(client, monkeypatch):
    case_payload = client.post("/cases", json={"title": "检索预算测试"}).json()
    calls: list[str] = []

    def empty_search(db, query, **kwargs):
        calls.append(query)
        return []

    monkeypatch.setattr("app.workflow.search_authorities", empty_search)

    with SessionLocal() as db:
        case = db.get(CaseFile, case_payload["id"])
        _, state = run_intake(
            db,
            case,
            "公司拖欠工资，我应该准备哪些材料？",
            gateway=RecordingGateway(),
        )
        validation = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.case_id == case.id,
                AuditEvent.event_type == "execution_validated",
            )
            .one()
        )

    assert len(calls) == 2
    assert state["tool_call_count"] == 2
    assert state["authority_ids"] == []
    assert validation.payload["budget_ok"] is True
    assert validation.payload["replans_used"] == 0
