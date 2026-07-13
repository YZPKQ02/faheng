from app.agent_contracts import ConversationOutput, ConversationPlan
from app.database import SessionLocal
from app.models import AuditEvent, CaseFile
from app.workflow import _format_follow_up_questions, run_intake


class RecordingGateway:
    enabled = True

    def __init__(self):
        self.calls: list[tuple[type, str]] = []

    def structured(self, *, system: str, user: str, schema: type):
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
    assert "1. 1." not in message.content
    assert "1. 是否有解除通知？" in message.content
    assert len(gateway.calls) == 2
    assert "我现在最想知道，公司是否应该赔偿我？" in gateway.calls[0][1]
    assert "我现在最想知道，公司是否应该赔偿我？" in gateway.calls[1][1]
