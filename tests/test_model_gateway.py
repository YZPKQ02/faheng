import httpx

from app.agent_contracts import PartyArgument
from app.config import Settings
from app.model_gateway import ModelGateway, ModelGatewayError


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
