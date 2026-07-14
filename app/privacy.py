from dataclasses import dataclass
from hashlib import sha256
import hmac
import re


@dataclass(frozen=True)
class RedactionResult:
    text: str
    counts: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.counts.values())


@dataclass(frozen=True)
class PseudonymRule:
    fingerprint: str
    source_length: int
    replacement: str


@dataclass(frozen=True)
class ModelCallAuthorization:
    consent_id: str
    consent_version: int
    case_id: str
    tenant_id: str
    purpose: str
    pseudonyms: tuple[PseudonymRule, ...] = ()


PATTERNS = (
    ("email", re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])"), "[邮箱已脱敏]"),
    ("prc_id", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "[身份证号已脱敏]"),
    ("mobile", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[手机号已脱敏]"),
    ("landline", re.compile(r"(?<!\d)0\d{2,3}[- ]?\d{7,8}(?!\d)"), "[电话已脱敏]"),
    (
        "bank_card",
        re.compile(r"(?<!\d)(?:\d[ -]?){15,18}\d(?!\d)"),
        "[银行卡号已脱敏]",
    ),
)


def redact_sensitive_text(value: str) -> RedactionResult:
    """Redact deterministic high-risk identifiers before external model calls."""
    text = value
    counts: dict[str, int] = {}
    for label, pattern, replacement in PATTERNS:
        text, count = pattern.subn(replacement, text)
        if count:
            counts[label] = count
    return RedactionResult(text=text, counts=counts)


def entity_fingerprint(
    value: str, *, secret: str, tenant_id: str, case_id: str
) -> str:
    normalized = value.strip().casefold()
    message = f"{tenant_id}\x1f{case_id}\x1f{normalized}".encode()
    return hmac.new(secret.encode(), message, sha256).hexdigest()


def apply_case_pseudonyms(
    value: str, *, authorization: ModelCallAuthorization, secret: str
) -> RedactionResult:
    text = value
    replaced = 0
    for rule in sorted(authorization.pseudonyms, key=lambda item: item.source_length, reverse=True):
        index = 0
        parts: list[str] = []
        while index <= len(text) - rule.source_length:
            candidate = text[index : index + rule.source_length]
            fingerprint = entity_fingerprint(
                candidate,
                secret=secret,
                tenant_id=authorization.tenant_id,
                case_id=authorization.case_id,
            )
            if fingerprint == rule.fingerprint:
                parts.append(rule.replacement)
                index += rule.source_length
                replaced += 1
            else:
                parts.append(text[index])
                index += 1
        parts.append(text[index:])
        text = "".join(parts)
    return RedactionResult(text=text, counts={"case_pseudonym": replaced} if replaced else {})
