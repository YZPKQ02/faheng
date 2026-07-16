import hashlib
import json
import re
from datetime import date
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import AuditEvent, LegalCase


SOURCE_ALLOWLIST = {
    "flk.npc.gov.cn": "国家法律法规数据库",
    "court.gov.cn": "最高人民法院",
    "www.court.gov.cn": "最高人民法院",
    "rmfyalk.court.gov.cn": "人民法院案例库",
    "wenshu.court.gov.cn": "中国裁判文书网",
    "gov.cn": "中国政府网",
    "www.gov.cn": "中国政府网",
    "mohrss.gov.cn": "人力资源和社会保障部",
    "www.mohrss.gov.cn": "人力资源和社会保障部",
    "xzfg.moj.gov.cn": "国家行政法规库（司法部）",
}


class RawCase(BaseModel):
    source_url: HttpUrl
    title: str
    case_number: str | None = None
    case_type: str = "劳动争议"
    court: str | None = None
    decision_date: date | None = None
    facts: str
    claims: list[str] = Field(default_factory=list)
    defenses: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    outcome: str = ""
    reasoning: str = ""
    authority_refs: list[str] = Field(default_factory=list)


class ImportResult(BaseModel):
    imported: int = 0
    duplicates: int = 0
    rejected: int = 0
    errors: list[str] = Field(default_factory=list)


PII_PATTERNS = [
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[手机号已脱敏]"),
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "[身份证号已脱敏]"),
    (re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"), "[邮箱已脱敏]"),
    (re.compile(r"(?:住址|住所地|家庭住址)[：:]?[^，。；\n]{4,80}"), "住址：[详细地址已脱敏]"),
]


def redact(text: str) -> str:
    cleaned = text
    for pattern, replacement in PII_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned.strip()


def validate_source(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host not in SOURCE_ALLOWLIST:
        raise ValueError(f"来源不在白名单：{host}")
    return SOURCE_ALLOWLIST[host]


def normalize(raw: RawCase) -> dict:
    source_url = str(raw.source_url)
    source_name = validate_source(source_url)
    fields = raw.model_dump(mode="json")
    fields["source_url"] = source_url
    fields["source_name"] = source_name
    for key in ("title", "facts", "outcome", "reasoning"):
        fields[key] = redact(fields[key])
    for key in ("claims", "defenses", "issues", "evidence", "authority_refs"):
        fields[key] = [redact(value) for value in fields[key] if value.strip()]
    fingerprint = json.dumps(
        {"title": fields["title"], "facts": fields["facts"], "outcome": fields["outcome"]},
        ensure_ascii=False,
        sort_keys=True,
    )
    fields["content_hash"] = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
    fields["review_status"] = "pending"
    return fields


def import_records(db: Session, records: list[RawCase]) -> ImportResult:
    result = ImportResult()
    for raw in records:
        try:
            with db.begin_nested():
                data = normalize(raw)
                exists = db.scalar(
                    select(LegalCase.id).where(
                        or_(
                            LegalCase.source_url == data["source_url"],
                            LegalCase.content_hash == data["content_hash"],
                        )
                    )
                )
                if exists:
                    result.duplicates += 1
                    continue
                db.add(LegalCase(**data))
                db.flush()
                result.imported += 1
        except Exception as exc:
            result.rejected += 1
            result.errors.append(str(exc))
    db.add(AuditEvent(event_type="knowledge_import", payload=result.model_dump()))
    db.commit()
    return result


def import_json_file(db: Session, path: Path) -> ImportResult:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload if isinstance(payload, list) else payload.get("cases", [])
    records = [RawCase.model_validate(item) for item in items]
    return import_records(db, records)


def fetch_public_page(url: str) -> str:
    """Fetch one allowlisted public page; this deliberately does not crawl links."""
    validate_source(url)
    response = httpx.get(
        url,
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": "AI-Legal-Advisor-Research/0.1 (low-frequency; contact project owner)"},
    )
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type:
        raise ValueError(f"只接受 HTML 公共页面，实际为：{content_type}")
    return response.text


def html_to_text(html: str) -> str:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", "\n", html)
    text = unescape(text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())
