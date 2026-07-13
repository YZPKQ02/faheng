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
    simulation_turn = client.post(
        f"/simulations/{simulation.json()['id']}/messages",
        json={"content": "我请求公司支付违法解除赔偿金。"},
    )
    assert simulation_turn.status_code == 200
    assert any(
        item["role"] == "劳动者（你）" for item in simulation_turn.json()["transcript"]
    )
    document = client.post(f"/cases/{case['id']}/documents", json={"document_type": "arbitration_application"})
    assert document.status_code == 201
    assert "仲裁申请书" in document.json()["content"]
    feedback = client.post("/feedback", json={"case_id": case["id"], "category": "usability", "content": "测试反馈"})
    assert feedback.status_code == 201


def test_unknown_case_is_isolated(client):
    assert client.get("/cases/not-found").status_code == 404


def test_list_and_permanently_delete_case(client):
    first = create_case(client)
    second = create_case(client)
    cases = client.get("/cases").json()
    assert {item["id"] for item in cases} == {first["id"], second["id"]}
    response = client.delete(f"/cases/{first['id']}")
    assert response.status_code == 204
    assert client.get(f"/cases/{first['id']}").status_code == 404
    remaining = client.get("/cases").json()
    assert [item["id"] for item in remaining] == [second["id"]]
