from app.agent_contracts import (
    SimulationAgentReply,
    SimulationArbitratorReply,
    SimulationRouterOutput,
)
from app.database import SessionLocal
from app.models import AuditEvent, Feedback
from app.main import settings
from app.model_gateway import ModelGateway
from app.observability import ModelCallTelemetry


def create_case(client):
    response = client.post("/cases", json={"title": "违法解除咨询"})
    assert response.status_code == 201
    return response.json()


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_intake_keeps_user_statements_unconfirmed(client):
    case = create_case(client)
    response = client.post(f"/cases/{case['id']}/messages", json={"content": "公司昨天突然辞退我，没有书面通知。我工作了三年。"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["stage"] == "fact_gathering"
    assert payload["authorities"]
    saved = client.get(f"/cases/{case['id']}").json()
    assert saved["facts"]
    assert all(fact["status"] == "user_stated" for fact in saved["facts"])


def test_intake_records_model_fallback_without_breaking_conversation(client):
    case = create_case(client)
    response = client.post(
        f"/cases/{case['id']}/messages",
        json={"content": "我想了解劳动仲裁需要准备什么。"},
    )
    assert response.status_code == 200
    assert response.json()["message"]["content"]


def test_streaming_intake_emits_status_tokens_and_completion(client):
    case = create_case(client)
    with client.stream(
        "POST",
        f"/cases/{case['id']}/messages/stream",
        json={"content": "公司拖欠工资，我需要准备什么？"},
    ) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200
    assert "event: status" in body
    assert "event: token" in body
    assert "event: complete" in body
    assert "text/event-stream" in response.headers["content-type"]


def test_analysis_has_citations_and_uncertainty(client):
    case = create_case(client)
    client.post(f"/cases/{case['id']}/messages", json={"content": "公司违法解除劳动合同，我想要赔偿金。"})
    response = client.post(f"/cases/{case['id']}/analysis", json={})
    assert response.status_code == 200
    data = response.json()
    assert data["authorities"]
    assert data["conclusions"][0]["authority_ids"]
    assert data["conclusions"][0]["uncertainties"]
    assert data["conclusions"][0]["reasoning_trace"]
    assert data["conclusions"][0]["quality_metrics"]["citation_coverage"] > 0
    assert "不承诺" in data["disclaimer"]


def test_fact_review_and_new_evidence_invalidate_previous_analysis(client):
    case = create_case(client)
    client.post(
        f"/cases/{case['id']}/messages",
        json={"content": "公司突然辞退我，没有书面通知，我有三年工资流水。"},
    )
    analysis = client.post(f"/cases/{case['id']}/analysis", json={}).json()
    conclusion_id = analysis["conclusions"][0]["id"]
    saved = client.get(f"/cases/{case['id']}").json()
    fact_id = saved["facts"][0]["id"]
    reviewed = client.patch(
        f"/cases/{case['id']}/facts/{fact_id}",
        json={"status": "confirmed", "occurred_on": "2026-01-10"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "confirmed"
    stale = client.get(f"/cases/{case['id']}").json()
    old = next(item for item in stale["analyses"] if item["id"] == conclusion_id)
    assert old["is_current"] is False
    assert old["invalidated_reason"]
    assert stale["stage"] == "evidence_review"

    evidence = client.post(
        f"/cases/{case['id']}/evidence",
        json={"name": "解除通知", "evidence_type": "document", "purpose": "证明公司解除"},
    )
    assert evidence.status_code == 201
    after_evidence = client.get(f"/cases/{case['id']}").json()
    reviewed_fact = next(item for item in after_evidence["facts"] if item["id"] == fact_id)
    assert reviewed_fact["status"] == "confirmed"


def test_evidence_match_does_not_promote_user_statement(client):
    case = create_case(client)
    client.post(
        f"/cases/{case['id']}/messages",
        json={"content": "公司解除劳动合同，但我没有收到解除通知。"},
    )
    before = client.get(f"/cases/{case['id']}").json()
    assert before["facts"]
    assert all(item["status"] == "user_stated" for item in before["facts"])

    response = client.post(
        f"/cases/{case['id']}/evidence",
        json={"name": "解除通知", "evidence_type": "document", "purpose": "核对解除理由"},
    )

    assert response.status_code == 201
    after = client.get(f"/cases/{case['id']}").json()
    assert all(item["status"] == "user_stated" for item in after["facts"])


def test_analysis_gate_requires_review_when_evidence_is_insufficient(client):
    case = create_case(client)
    client.post(
        f"/cases/{case['id']}/messages",
        json={"content": "公司突然辞退我，我认为属于违法解除。"},
    )
    response = client.post(f"/cases/{case['id']}/analysis", json={})
    assert response.status_code == 200
    payload = response.json()
    assert payload["requires_human_review"] is True
    assert payload["blocked_reasons"]
    assert payload["conclusions"][0]["confidence"] <= 0.49
    saved = client.get(f"/cases/{case['id']}").json()
    assert saved["stage"] == "human_review"
    tasks = client.get(f"/cases/{case['id']}/agent-tasks").json()
    assert len(tasks) == 4
    assert {task["agent"] for task in tasks} == {
        "worker_advocate",
        "employer_advocate",
        "neutral_adjudicator",
        "safety_reviewer",
    }
    assert all(task["protocol_version"] == "agent-task-v1" for task in tasks)
    assert all(task["status"] == "completed" for task in tasks)

    reviews = client.get(f"/cases/{case['id']}/reviews").json()
    assert len(reviews) == 1
    assert reviews[0]["status"] == "pending"
    decision = client.post(
        f"/reviews/{reviews[0]['id']}/decision",
        json={
            "decision": "approved",
            "reviewer": "测试审核员",
            "notes": "已核对当前分析仅作为附条件风险提示。",
        },
    )
    assert decision.status_code == 200
    assert decision.json()["decision"] == "approved"
    assert client.get(f"/cases/{case['id']}").json()["stage"] == "strategy_ready"
    duplicate = client.post(
        f"/reviews/{reviews[0]['id']}/decision",
        json={"decision": "rejected", "reviewer": "测试审核员", "notes": "重复处理"},
    )
    assert duplicate.status_code == 409


def test_rejected_review_invalidates_analysis(client):
    case = create_case(client)
    client.post(
        f"/cases/{case['id']}/messages",
        json={"content": "公司突然辞退我，我认为属于违法解除。"},
    )
    analysis = client.post(f"/cases/{case['id']}/analysis", json={}).json()
    review = client.get(f"/cases/{case['id']}/reviews").json()[0]
    response = client.post(
        f"/reviews/{review['id']}/decision",
        json={"decision": "rejected", "reviewer": "审核律师", "notes": "关键事实无法核实"},
    )
    assert response.status_code == 200
    saved = client.get(f"/cases/{case['id']}").json()
    conclusion = next(item for item in saved["analyses"] if item["id"] == analysis["conclusions"][0]["id"])
    assert conclusion["is_current"] is False
    assert conclusion["invalidated_reason"] == "人工审核驳回"
    assert saved["stage"] == "fact_review"


def test_evidence_simulation_document_and_feedback(client):
    case = create_case(client)
    evidence = client.post(f"/cases/{case['id']}/evidence", json={"name": "解除通知", "evidence_type": "document", "purpose": "证明解除时间及理由"})
    assert evidence.status_code == 201
    simulation = client.post(f"/cases/{case['id']}/simulations", json={"scenario": "arbitration", "user_role": "worker"})
    assert simulation.status_code == 201
    assert simulation.json()["feedback"]
    assert simulation.json()["transcript"][0]["mode"] == "rule"
    assert simulation.json()["transcript"][0]["mode_reason"] == "model_disabled"
    simulation_turn = client.post(
        f"/simulations/{simulation.json()['id']}/messages",
        json={"content": "我请求公司支付违法解除赔偿金。"},
    )
    assert simulation_turn.status_code == 200
    assert any(
        item["role"] == "劳动者（你）" for item in simulation_turn.json()["transcript"]
    )
    turn_transcript = simulation_turn.json()["transcript"]
    assert turn_transcript[0]["last_execution"] == [
        "employer_advocate",
        "arbitrator",
        "worker_coach",
    ]
    employer_index = next(
        index for index, item in enumerate(turn_transcript) if item.get("agent_id") == "employer_advocate"
    )
    arbitrator_indices = [
        index for index, item in enumerate(turn_transcript) if item.get("agent_id") == "arbitrator"
    ]
    assert employer_index < arbitrator_indices[-1]
    with SessionLocal() as db:
        agent_calls = [
            event
            for event in db.query(AuditEvent)
            .filter(AuditEvent.event_type == "simulation_agent_call")
            .all()
            if event.payload["session_id"] == simulation.json()["id"]
        ]
        assert [event.agent for event in agent_calls] == [
            "employer_advocate",
            "arbitrator",
            "worker_coach",
        ]
        assert len(agent_calls) <= 3
    document = client.post(f"/cases/{case['id']}/documents", json={"document_type": "arbitration_application"})
    assert document.status_code == 201
    assert "仲裁申请书" in document.json()["content"]
    feedback = client.post("/feedback", json={"case_id": case["id"], "category": "usability", "content": "测试反馈"})
    assert feedback.status_code == 201


def test_simulation_rule_mode_answers_role_question(client):
    case = create_case(client)
    simulation = client.post(
        f"/cases/{case['id']}/simulations",
        json={"scenario": "arbitration", "user_role": "worker"},
    ).json()

    response = client.post(
        f"/simulations/{simulation['id']}/messages",
        json={"content": "你们都是谁？"},
    )

    assert response.status_code == 200
    transcript = response.json()["transcript"]
    assert transcript[0]["mode"] == "rule"
    assert transcript[0]["last_execution"] == ["arbitrator"]
    assert transcript[-1]["agent_id"] == "arbitrator"
    assert "用人单位代理人" in transcript[-1]["content"]
    assert "仲裁助手" in transcript[-1]["content"]
    assert "你本人扮演劳动者" in transcript[-1]["content"]


def test_simulation_routes_named_employer_question_to_employer_only(client):
    case = create_case(client)
    simulation = client.post(
        f"/cases/{case['id']}/simulations",
        json={"scenario": "arbitration", "user_role": "worker"},
    ).json()

    response = client.post(
        f"/simulations/{simulation['id']}/messages",
        json={"content": "单位代表是谁？"},
    )

    assert response.status_code == 200
    transcript = response.json()["transcript"]
    metadata = transcript[0]
    assert metadata["stage"] == "orientation"
    assert metadata["last_execution"] == ["employer_advocate"]
    assert metadata["last_user_act"] == "role_identity"
    assert metadata["last_response_plan"] == ["employer_advocate"]
    assert transcript[-1]["agent_id"] == "employer_advocate"
    assert "用人单位代理人" in transcript[-1]["content"]
    with SessionLocal() as db:
        decision = (
            db.query(AuditEvent)
            .filter(AuditEvent.event_type == "simulation_turn_decision")
            .order_by(AuditEvent.created_at.desc())
            .first()
        )
        assert decision.payload["addressed_to"] == "employer_advocate"
        assert decision.payload["response_plan"] == ["employer_advocate"]


def test_simulation_routes_coaching_request_to_sidebar_only(client):
    case = create_case(client)
    simulation = client.post(
        f"/cases/{case['id']}/simulations",
        json={"scenario": "arbitration", "user_role": "worker"},
    ).json()
    previous_feedback = simulation["feedback"]

    response = client.post(
        f"/simulations/{simulation['id']}/messages",
        json={"content": "我不知道怎么回答，请给我建议。"},
    )

    assert response.status_code == 200
    payload = response.json()
    metadata = payload["transcript"][0]
    assert metadata["stage"] == "orientation"
    assert metadata["last_execution"] == ["worker_coach"]
    assert metadata["last_user_act"] == "coaching"
    assert payload["transcript"][-1]["agent_id"] == "worker"
    assert payload["feedback"] != previous_feedback
    assert any("直接回答仲裁员" in item for item in payload["feedback"])


def test_simulation_procedure_question_does_not_advance_stage(client):
    case = create_case(client)
    simulation = client.post(
        f"/cases/{case['id']}/simulations",
        json={"scenario": "arbitration", "user_role": "worker"},
    ).json()
    substantive = client.post(
        f"/simulations/{simulation['id']}/messages",
        json={"content": "我请求公司支付违法解除赔偿金。"},
    ).json()
    current_stage = substantive["transcript"][0]["stage"]

    response = client.post(
        f"/simulations/{simulation['id']}/messages",
        json={"content": "现在是什么流程？"},
    )

    assert response.status_code == 200
    metadata = response.json()["transcript"][0]
    assert metadata["stage"] == current_stage
    assert metadata["last_execution"] == ["arbitrator"]
    assert metadata["last_user_act"] == "procedure"


def test_simulation_semantic_router_understands_natural_role_reference(client, monkeypatch):
    case = create_case(client)
    simulation = client.post(
        f"/cases/{case['id']}/simulations",
        json={"scenario": "arbitration", "user_role": "worker"},
    ).json()

    def fake_structured(self, *, schema, **kwargs):
        self.last_telemetry = ModelCallTelemetry("success", 1, 1, 0)
        if schema is SimulationRouterOutput:
            return SimulationRouterOutput(
                speech_act="role_identity",
                addressed_to="employer_advocate",
                response_plan=["employer_advocate"],
                confidence=0.94,
                needs_clarification=False,
            )
        if schema is SimulationAgentReply:
            return SimulationAgentReply(reply="我是代表用人单位参加本次模拟的代理人。")
        raise AssertionError(f"unexpected schema: {schema}")

    monkeypatch.setattr(ModelGateway, "structured", fake_structured)
    response = client.post(
        f"/simulations/{simulation['id']}/messages",
        json={"content": "酒红色头像那位是做什么的？"},
    )

    assert response.status_code == 200
    transcript = response.json()["transcript"]
    metadata = transcript[0]
    assert metadata["last_execution"] == ["employer_advocate"]
    assert metadata["last_response_plan"] == ["employer_advocate"]
    assert transcript[-1]["agent_id"] == "employer_advocate"
    with SessionLocal() as db:
        router_event = next(
            event
            for event in db.query(AuditEvent)
            .filter(AuditEvent.event_type == "simulation_router_call")
            .all()
            if event.payload["session_id"] == simulation["id"]
        )
        assert router_event.payload["outcome"] == "success"
        assert router_event.payload["selected_source"] == "semantic_router"


def test_simulation_semantic_router_low_confidence_only_clarifies(client, monkeypatch):
    case = create_case(client)
    simulation = client.post(
        f"/cases/{case['id']}/simulations",
        json={"scenario": "arbitration", "user_role": "worker"},
    ).json()

    def fake_structured(self, *, schema, **kwargs):
        self.last_telemetry = ModelCallTelemetry("success", 1, 1, 0)
        if schema is SimulationRouterOutput:
            return SimulationRouterOutput(
                speech_act="direct_question",
                addressed_to="employer_advocate",
                response_plan=["employer_advocate"],
                confidence=0.51,
                needs_clarification=False,
            )
        if schema is SimulationArbitratorReply:
            return SimulationArbitratorReply(
                reply="请说明你希望哪一方回答，以及具体想询问什么。",
                next_stage="orientation",
            )
        raise AssertionError(f"unexpected schema: {schema}")

    monkeypatch.setattr(ModelGateway, "structured", fake_structured)
    response = client.post(
        f"/simulations/{simulation['id']}/messages",
        json={"content": "刚才那个到底怎么算？"},
    )

    assert response.status_code == 200
    metadata = response.json()["transcript"][0]
    assert metadata["stage"] == "orientation"
    assert metadata["last_user_act"] == "clarify"
    assert metadata["last_execution"] == ["arbitrator"]


def test_semantically_routed_full_turn_keeps_three_model_node_limit(client, monkeypatch):
    case = create_case(client)
    simulation = client.post(
        f"/cases/{case['id']}/simulations",
        json={"scenario": "arbitration", "user_role": "worker"},
    ).json()
    called_schemas = []

    def fake_structured(self, *, schema, **kwargs):
        called_schemas.append(schema)
        self.last_telemetry = ModelCallTelemetry("success", 1, 1, 0)
        if schema is SimulationRouterOutput:
            return SimulationRouterOutput(
                speech_act="answer_or_substantive",
                addressed_to="unspecified",
                response_plan=["employer_advocate", "arbitrator", "worker_coach"],
                confidence=0.9,
                needs_clarification=False,
            )
        if schema is SimulationAgentReply:
            return SimulationAgentReply(reply="单位方不同意该项陈述。")
        if schema is SimulationArbitratorReply:
            return SimulationArbitratorReply(
                reply="双方对该项事实存在争议。",
                next_question="请说明能够支持该陈述的材料。",
                next_stage="fact_investigation",
            )
        raise AssertionError(f"unexpected schema: {schema}")

    monkeypatch.setattr(ModelGateway, "structured", fake_structured)
    response = client.post(
        f"/simulations/{simulation['id']}/messages",
        json={"content": "对方刚才的说法与实际情况不一致。"},
    )

    assert response.status_code == 200
    metadata = response.json()["transcript"][0]
    assert called_schemas == [
        SimulationRouterOutput,
        SimulationAgentReply,
        SimulationArbitratorReply,
    ]
    assert metadata["last_execution"] == ["employer_advocate", "arbitrator"]
    assert metadata["fallback_agents"] == ["worker_coach"]
    with SessionLocal() as db:
        turn_event = next(
            event
            for event in db.query(AuditEvent)
            .filter(AuditEvent.event_type == "simulation_turn")
            .all()
            if event.payload["session_id"] == simulation["id"]
        )
        assert turn_event.payload["model_node_count"] == 3
        assert turn_event.payload["max_model_nodes"] == 3


def test_active_simulation_is_resumed_until_explicitly_completed(client):
    case = create_case(client)
    opened = client.put(
        f"/cases/{case['id']}/simulations/active",
        json={"scenario": "arbitration", "user_role": "worker"},
    )
    assert opened.status_code == 200
    first = opened.json()
    assert first["status"] == "active"
    assert first["created_at"]
    assert first["updated_at"]

    continued = client.post(
        f"/simulations/{first['id']}/messages",
        json={"content": "我请求公司支付违法解除赔偿金。"},
    )
    assert continued.status_code == 200
    continued_payload = continued.json()

    resumed = client.put(
        f"/cases/{case['id']}/simulations/active",
        json={"scenario": "arbitration", "user_role": "worker"},
    )
    assert resumed.status_code == 200
    assert resumed.json()["id"] == first["id"]
    assert resumed.json()["transcript"] == continued_payload["transcript"]

    completed = client.post(f"/simulations/{first['id']}/complete")
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    rejected = client.post(
        f"/simulations/{first['id']}/messages",
        json={"content": "继续回答"},
    )
    assert rejected.status_code == 409

    restarted = client.put(
        f"/cases/{case['id']}/simulations/active",
        json={"scenario": "arbitration", "user_role": "worker"},
    )
    assert restarted.status_code == 200
    assert restarted.json()["id"] != first["id"]
    assert restarted.json()["status"] == "active"


def test_active_simulation_never_crosses_case_boundary(client):
    first_case = create_case(client)
    second_case = create_case(client)
    first = client.put(
        f"/cases/{first_case['id']}/simulations/active",
        json={"scenario": "arbitration", "user_role": "worker"},
    ).json()
    second = client.put(
        f"/cases/{second_case['id']}/simulations/active",
        json={"scenario": "arbitration", "user_role": "worker"},
    ).json()

    assert first["id"] != second["id"]


def test_unknown_case_is_isolated(client):
    assert client.get("/cases/not-found").status_code == 404


def test_model_consent_and_pseudonym_lifecycle_does_not_store_raw_entity(client):
    case = create_case(client)
    previous_secret = settings.pseudonym_hmac_secret
    settings.pseudonym_hmac_secret = "test-case-pseudonym-secret"
    try:
        pseudonym = client.post(
            f"/cases/{case['id']}/pseudonyms",
            json={"entity_value": "张三", "entity_type": "person"},
        )
        assert pseudonym.status_code == 201
        assert pseudonym.json()["pseudonym"] == "当事人-1"
        assert "entity_value" not in pseudonym.json()
        duplicate = client.post(
            f"/cases/{case['id']}/pseudonyms",
            json={"entity_value": "张三", "entity_type": "person"},
        )
        assert duplicate.json()["id"] == pseudonym.json()["id"]

        consent = client.post(
            f"/cases/{case['id']}/model-consents",
            json={
                "provider": "deepseek",
                "purposes": ["intake", "analysis"],
                "data_categories": ["conversation", "facts"],
            },
        )
        assert consent.status_code == 201
        consent_id = consent.json()["id"]
        assert consent.json()["status"] == "active"
        assert client.delete(
            f"/cases/{case['id']}/model-consents/{consent_id}"
        ).status_code == 204
        listed = client.get(f"/cases/{case['id']}/model-consents").json()
        assert listed[0]["status"] == "revoked"

        with SessionLocal() as db:
            events = db.query(AuditEvent).filter(AuditEvent.case_id == case["id"]).all()
            assert "张三" not in str([event.payload for event in events])
    finally:
        settings.pseudonym_hmac_secret = previous_secret


def test_list_and_permanently_delete_case(client):
    first = create_case(client)
    second = create_case(client)
    feedback = client.post(
        "/feedback",
        json={"case_id": first["id"], "category": "other", "content": "包含案件信息的反馈"},
    )
    assert feedback.status_code == 201
    cases = client.get("/cases").json()
    assert {item["id"] for item in cases} == {first["id"], second["id"]}
    response = client.delete(f"/cases/{first['id']}")
    assert response.status_code == 204
    assert client.get(f"/cases/{first['id']}").status_code == 404
    remaining = client.get("/cases").json()
    assert [item["id"] for item in remaining] == [second["id"]]

    with SessionLocal() as db:
        assert db.query(Feedback).filter(Feedback.case_id == first["id"]).count() == 0
        deletion = (
            db.query(AuditEvent)
            .filter(AuditEvent.event_type == "case_deleted")
            .order_by(AuditEvent.created_at.desc())
            .first()
        )
        assert deletion is not None
        assert "deleted_case_title" not in deletion.payload
        assert deletion.payload["content_retained"] is False


def test_cors_preflight_allows_local_frontend_origins(client):
    for origin in ("http://localhost:3001", "http://127.0.0.1:3001"):
        response = client.options(
            "/cases",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin


def test_cors_preflight_rejects_unknown_origin(client):
    response = client.options(
        "/cases",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
