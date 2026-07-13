import json
import time
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings


T = TypeVar("T", bound=BaseModel)


class ModelGatewayError(RuntimeError):
    """Raised when a configured model cannot return a validated response."""


class ModelGateway:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @property
    def enabled(self) -> bool:
        return self.settings.model_provider == "deepseek" and bool(self.settings.deepseek_api_key)

    def structured(self, *, system: str, user: str, schema: type[T]) -> T:
        if not self.enabled:
            raise ModelGatewayError("DeepSeek 未配置；使用确定性回退")
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        messages = [
            {
                "role": "system",
                "content": (
                    system
                    + "\n只输出一个合法 JSON 对象，不要输出 Markdown。不得编造事实、法条、案例或引用。"
                    + f"\n输出必须符合 JSON Schema：{schema_json}"
                ),
            },
            {"role": "user", "content": user},
        ]
        payload = {
            "model": self.settings.deepseek_model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }
        last_error: Exception | None = None
        for attempt in range(self.settings.deepseek_max_retries + 1):
            try:
                with httpx.Client(timeout=self.settings.deepseek_timeout_seconds) as client:
                    response = client.post(
                        f"{self.settings.deepseek_base_url.rstrip('/')}/chat/completions",
                        headers={"Authorization": f"Bearer {self.settings.deepseek_api_key}"},
                        json=payload,
                    )
                    response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                return schema.model_validate_json(content)
            except (httpx.HTTPError, KeyError, TypeError, ValidationError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < self.settings.deepseek_max_retries:
                    time.sleep(0.25 * (attempt + 1))
        raise ModelGatewayError(f"模型调用或结构校验失败：{last_error}") from last_error

