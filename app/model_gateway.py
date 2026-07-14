import json
import logging
import time
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings
from app.privacy import ModelCallAuthorization, apply_case_pseudonyms, redact_sensitive_text
from app.observability import ModelCallTelemetry


T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)


class ModelGatewayError(RuntimeError):
    """Raised when a configured model cannot return a validated response."""


class ModelGateway:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.last_telemetry: ModelCallTelemetry | None = None

    @property
    def enabled(self) -> bool:
        return self.settings.model_provider == "deepseek" and bool(self.settings.deepseek_api_key)

    def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        authorization: ModelCallAuthorization | None = None,
    ) -> T:
        started = time.perf_counter()
        redaction_count = 0
        pseudonym_count = 0
        if not self.enabled:
            self.last_telemetry = ModelCallTelemetry(
                "disabled", 0, 0, 0, error_type="model_disabled"
            )
            raise ModelGatewayError("DeepSeek 未配置；使用确定性回退")
        if self.settings.model_consent_required and authorization is None:
            self.last_telemetry = ModelCallTelemetry(
                "rejected", 0, 0, 0, error_type="consent_missing"
            )
            raise ModelGatewayError("当前案件未授权向外部模型发送数据")
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        if authorization and authorization.pseudonyms:
            if not self.settings.pseudonym_hmac_secret:
                raise ModelGatewayError("案件假名密钥未配置")
            pseudonymized = apply_case_pseudonyms(
                user,
                authorization=authorization,
                secret=self.settings.pseudonym_hmac_secret,
            )
            user = pseudonymized.text
            pseudonym_count = pseudonymized.total
            if pseudonymized.total:
                logger.info(
                    "Applied %s case pseudonyms before model call for consent %s",
                    pseudonymized.total,
                    authorization.consent_id,
                )
        if self.settings.model_redaction_enabled:
            redacted = redact_sensitive_text(user)
            user = redacted.text
            redaction_count = redacted.total
            if redacted.total:
                logger.info(
                    "Redacted %s sensitive identifiers before model call: %s",
                    redacted.total,
                    redacted.counts,
                )
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
        status_code: int | None = None
        for attempt in range(self.settings.deepseek_max_retries + 1):
            try:
                with httpx.Client(timeout=self.settings.deepseek_timeout_seconds) as client:
                    response = client.post(
                        f"{self.settings.deepseek_base_url.rstrip('/')}/chat/completions",
                        headers={"Authorization": f"Bearer {self.settings.deepseek_api_key}"},
                        json=payload,
                    )
                    status_code = response.status_code
                    response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                result = schema.model_validate_json(content)
                self.last_telemetry = ModelCallTelemetry(
                    outcome="success",
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                    attempts=attempt + 1,
                    retries=attempt,
                    status_code=status_code,
                    redaction_count=redaction_count,
                    pseudonym_count=pseudonym_count,
                )
                return result
            except (httpx.HTTPError, KeyError, TypeError, ValidationError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < self.settings.deepseek_max_retries:
                    time.sleep(0.25 * (attempt + 1))
        attempts = self.settings.deepseek_max_retries + 1
        self.last_telemetry = ModelCallTelemetry(
            outcome="error",
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            attempts=attempts,
            retries=max(0, attempts - 1),
            status_code=status_code,
            error_type=type(last_error).__name__ if last_error else "unknown",
            redaction_count=redaction_count,
            pseudonym_count=pseudonym_count,
        )
        raise ModelGatewayError(f"模型调用或结构校验失败：{last_error}") from last_error
