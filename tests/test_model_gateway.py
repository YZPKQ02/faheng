import httpx
import pytest

from app.agent_contracts import PartyArgument
from app.config import Settings
from app.model_gateway import (
    ModelGateway,
    ModelGatewayError,
    ModelRequestBudget,
)


def test_deepseek_gateway_validates_structured_output(monkeypatch):
    settings = Settings(
        model_provider="deepseek",
        deepseek_api_key="test-key",
        deepseek_base_url="https://example.invalid",
        deepseek_max_retries=0,
        model_consent_required=False,
    )

    def fake_post(self, url, headers, json):
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"position":"观点","arguments":["论据"],"evidence_needed":[],"authority_ids":["a1"]}'
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    result = ModelGateway(settings).structured(system="test", user="test", schema=PartyArgument)
    assert result.position == "观点"
    assert result.authority_ids == ["a1"]


def test_gateway_redacts_sensitive_user_content_before_transport(monkeypatch):
    settings = Settings(
        model_provider="deepseek",
        deepseek_api_key="test-key",
        deepseek_base_url="https://example.invalid",
        deepseek_max_retries=0,
        model_consent_required=False,
    )
    captured = {}

    def fake_post(self, url, headers, json):
        captured["payload"] = json
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"position":"观点","arguments":[],"evidence_needed":[],"authority_ids":[]}'
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    ModelGateway(settings).structured(
        system="test",
        user="联系手机号13812345678，邮箱worker@example.com",
        schema=PartyArgument,
    )

    transported = captured["payload"]["messages"][1]["content"]
    assert "13812345678" not in transported
    assert "worker@example.com" not in transported
    assert "[手机号已脱敏]" in transported


def test_gateway_redaction_can_be_disabled_for_controlled_local_testing(monkeypatch):
    settings = Settings(
        model_provider="deepseek",
        model_redaction_enabled=False,
        deepseek_api_key="test-key",
        deepseek_base_url="https://example.invalid",
        deepseek_max_retries=0,
        model_consent_required=False,
    )
    captured = {}

    def fake_post(self, url, headers, json):
        captured["payload"] = json
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"position":"观点","arguments":[],"evidence_needed":[],"authority_ids":[]}'
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    ModelGateway(settings).structured(
        system="test", user="手机号13812345678", schema=PartyArgument
    )

    assert "13812345678" in captured["payload"]["messages"][1]["content"]


def test_gateway_disabled_without_key():
    assert not ModelGateway(Settings(model_provider="deepseek", deepseek_api_key=None)).enabled


def test_gateway_does_not_send_without_case_consent(monkeypatch):
    settings = Settings(
        model_provider="deepseek",
        deepseek_api_key="test-key",
        deepseek_base_url="https://example.invalid",
        deepseek_max_retries=0,
    )
    transported = False

    def fake_post(self, url, headers, json):
        nonlocal transported
        transported = True

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    try:
        ModelGateway(settings).structured(system="test", user="test", schema=PartyArgument)
    except ModelGatewayError as exc:
        assert "未授权" in str(exc)
    else:
        raise AssertionError("missing consent must fail closed")
    assert transported is False


def test_gateway_records_token_usage_and_enforces_shared_budget(monkeypatch):
    settings = Settings(
        model_provider="deepseek",
        deepseek_api_key="test-key",
        deepseek_base_url="https://example.invalid",
        deepseek_max_retries=0,
        model_consent_required=False,
    )
    transported = 0

    def fake_post(self, url, headers, json):
        nonlocal transported
        transported += 1
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"position":"观点","arguments":[],"evidence_needed":[],"authority_ids":[]}'
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    budget = ModelRequestBudget(max_logical_calls=1, max_http_requests=1)
    gateway = ModelGateway(settings, request_budget=budget)
    gateway.structured(system="test", user="test", schema=PartyArgument)

    assert gateway.last_telemetry.total_tokens == 15
    assert budget.snapshot()["http_requests"] == 1
    with pytest.raises(ModelGatewayError, match="预算"):
        ModelGateway(settings, request_budget=budget).structured(
            system="test", user="test", schema=PartyArgument
        )
    assert transported == 1


def test_gateway_retries_after_rate_limit(monkeypatch):
    settings = Settings(
        model_provider="deepseek",
        deepseek_api_key="test-key",
        deepseek_base_url="https://example.invalid",
        deepseek_max_retries=1,
        model_consent_required=False,
    )
    attempts = 0

    def fake_post(self, url, headers, json):
        nonlocal attempts
        attempts += 1
        request = httpx.Request("POST", url)
        if attempts == 1:
            return httpx.Response(429, request=request)
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"position":"观点","arguments":[],"evidence_needed":[],"authority_ids":[]}'
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    monkeypatch.setattr("app.model_gateway.time.sleep", lambda _: None)
    gateway = ModelGateway(settings)
    gateway.structured(system="test", user="test", schema=PartyArgument)

    assert attempts == 2
    assert gateway.last_telemetry.attempts == 2
    assert gateway.last_telemetry.retries == 1


@pytest.mark.parametrize("failure", ["timeout", "invalid_json"])
def test_gateway_classifies_transport_and_schema_failures(monkeypatch, failure):
    settings = Settings(
        model_provider="deepseek",
        deepseek_api_key="test-key",
        deepseek_base_url="https://example.invalid",
        deepseek_max_retries=0,
        model_consent_required=False,
    )

    def fake_post(self, url, headers, json):
        request = httpx.Request("POST", url)
        if failure == "timeout":
            raise httpx.ReadTimeout("injected timeout", request=request)
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": "not-json"}}]},
        )

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    gateway = ModelGateway(settings)
    with pytest.raises(ModelGatewayError):
        gateway.structured(system="test", user="test", schema=PartyArgument)
    assert gateway.last_telemetry.outcome == "error"
    assert gateway.last_telemetry.error_type in {"ReadTimeout", "ValidationError"}
