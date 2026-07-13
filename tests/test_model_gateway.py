import httpx

from app.agent_contracts import PartyArgument
from app.config import Settings
from app.model_gateway import ModelGateway


def test_deepseek_gateway_validates_structured_output(monkeypatch):
    settings = Settings(
        model_provider="deepseek",
        deepseek_api_key="test-key",
        deepseek_base_url="https://example.invalid",
        deepseek_max_retries=0,
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


def test_gateway_disabled_without_key():
    assert not ModelGateway(Settings(model_provider="deepseek", deepseek_api_key=None)).enabled

