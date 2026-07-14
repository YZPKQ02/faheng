import json

from app.privacy import (
    ModelCallAuthorization,
    PseudonymRule,
    apply_case_pseudonyms,
    entity_fingerprint,
    redact_sensitive_text,
)


def test_redaction_removes_high_risk_identifiers_without_breaking_json():
    source = json.dumps(
        {
            "content": (
                "手机号13812345678，身份证11010519900101123X，"
                "邮箱worker@example.com，银行卡6222021234567890123。"
            )
        },
        ensure_ascii=False,
    )

    result = redact_sensitive_text(source)
    payload = json.loads(result.text)

    assert "13812345678" not in result.text
    assert "11010519900101123X" not in result.text
    assert "worker@example.com" not in result.text
    assert "6222021234567890123" not in result.text
    assert "[手机号已脱敏]" in payload["content"]
    assert result.total == 4


def test_redaction_does_not_change_ordinary_dates_or_money():
    source = "2026年7月14日，月工资为12000元。"

    result = redact_sensitive_text(source)

    assert result.text == source
    assert result.total == 0


def test_case_pseudonym_is_stable_and_scoped_to_case():
    secret = "test-pseudonym-secret"
    fingerprint = entity_fingerprint(
        "张三", secret=secret, tenant_id="tenant-1", case_id="case-1"
    )
    authorization = ModelCallAuthorization(
        consent_id="consent-1",
        consent_version=1,
        case_id="case-1",
        tenant_id="tenant-1",
        purpose="intake",
        pseudonyms=(PseudonymRule(fingerprint, 2, "当事人-1"),),
    )

    result = apply_case_pseudonyms(
        "张三称公司拖欠工资，张三已经催讨。",
        authorization=authorization,
        secret=secret,
    )

    assert result.text == "当事人-1称公司拖欠工资，当事人-1已经催讨。"
    assert result.counts == {"case_pseudonym": 2}
    assert entity_fingerprint(
        "张三", secret=secret, tenant_id="tenant-1", case_id="case-2"
    ) != fingerprint
