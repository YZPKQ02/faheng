from app.database import SessionLocal
from app.models import AuditEvent
from app.observability import query_fingerprint


def test_query_fingerprint_is_stable_and_tenant_scoped():
    first = query_fingerprint("违法解除 赔偿", secret="metric-secret", tenant_id="tenant-a")
    repeated = query_fingerprint(
        "  违法解除   赔偿 ", secret="metric-secret", tenant_id="tenant-a"
    )
    other_tenant = query_fingerprint(
        "违法解除 赔偿", secret="metric-secret", tenant_id="tenant-b"
    )

    assert first == repeated
    assert first != other_tenant
    assert query_fingerprint("敏感查询", secret=None, tenant_id="tenant-a") is None


def test_internal_metrics_aggregate_without_storing_query_or_prompt(client):
    case = client.post("/cases", json={"title": "指标测试"}).json()
    content = "公司突然解除劳动合同，我想知道是否可以要求赔偿。"
    response = client.post(f"/cases/{case['id']}/messages", json={"content": content})
    assert response.status_code == 200

    metrics = client.get("/internal/metrics?hours=24")

    assert metrics.status_code == 200
    payload = metrics.json()
    assert payload["model"]["calls"] == 1
    assert payload["model"]["fallbacks"] == 1
    assert payload["retrieval"]["calls"] >= 1
    with SessionLocal() as db:
        events = db.query(AuditEvent).filter(
            AuditEvent.case_id == case["id"],
            AuditEvent.event_type.in_(("model_call_metric", "authority_retrieval_metric")),
        )
        assert content not in str([item.payload for item in events])
