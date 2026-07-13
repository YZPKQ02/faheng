from app.ingestion.pipeline import RawCase, import_records, redact
from app.models import LegalCase


def test_redaction_removes_common_pii():
    text = "手机号13812345678，身份证110101199001011234，邮箱test@example.com"
    cleaned = redact(text)
    assert "13812345678" not in cleaned
    assert "110101199001011234" not in cleaned
    assert "test@example.com" not in cleaned


def test_import_allowlisted_case_and_deduplicate(client):
    from app.database import SessionLocal

    raw = RawCase(
        source_url="https://www.court.gov.cn/example/labor-1.html",
        title="某劳动争议参考案例",
        facts="劳动者手机号13812345678，主张违法解除。",
        issues=["解除是否合法"],
        outcome="部分支持",
    )
    with SessionLocal() as db:
        first = import_records(db, [raw])
        second = import_records(db, [raw])
        saved = db.query(LegalCase).one()
    assert first.imported == 1
    assert second.duplicates == 1
    assert "13812345678" not in saved.facts


def test_knowledge_stats(client):
    payload = client.get("/knowledge/stats").json()
    assert payload["authorities"] >= 5
    assert payload["model_provider"] in {"deterministic", "deepseek"}
