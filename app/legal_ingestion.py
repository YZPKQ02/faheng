"""Auditable, idempotent ingestion for official versioned legal materials."""

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.pipeline import validate_source
from app.config import get_settings
from app.models import (
    AuditEvent,
    LegalAuthority,
    LegalChunk,
    LegalDocument,
    LegalDocumentVersion,
)


def canonical_document_key(title: str) -> str:
    normalized = re.sub(r"\s+", "", title).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


class RawLegalChunk(BaseModel):
    locator: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1)
    heading: str | None = Field(default=None, max_length=300)
    keywords: list[str] = Field(default_factory=list)
    sequence: int = Field(default=0, ge=0)


class RawLegalDocument(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    authority_type: str = Field(default="statute", max_length=50)
    level: str = Field(min_length=1, max_length=50)
    jurisdiction: str = Field(default="全国", max_length=100)
    issuing_body: str | None = Field(default=None, max_length=200)
    source_url: HttpUrl
    version_label: str = Field(default="现行版本", max_length=100)
    status: str = Field(default="active", pattern=r"^(active|expired|repealed)$")
    promulgated_on: date | None = None
    effective_on: date
    expired_on: date | None = None
    review_status: str = Field(default="pending", pattern=r"^(pending|approved|rejected)$")
    supersedes_content_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    transition_kind: Literal["correction", "amendment"] | None = None
    chunks: list[RawLegalChunk] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_version(self) -> "RawLegalDocument":
        if self.expired_on is not None and self.expired_on <= self.effective_on:
            raise ValueError("失效日期必须晚于生效日期")
        locators = [chunk.locator for chunk in self.chunks]
        if len(locators) != len(set(locators)):
            raise ValueError("同一版本中的条款定位不得重复")
        if bool(self.supersedes_content_hash) != bool(self.transition_kind):
            raise ValueError("supersedes_content_hash 与 transition_kind 必须同时提供")
        if self.transition_kind and self.status != "active":
            raise ValueError("替换或修订后的新版本必须为 active")
        return self


class LegalVersionTransition(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    supersedes_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    transition_kind: Literal["correction", "amendment"]
    reason: str = Field(min_length=1, max_length=500)


class LegalTransitionManifest(BaseModel):
    corpus_version: str = Field(min_length=1, max_length=100)
    target_corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    predecessor_corpus_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    transitions: list[LegalVersionTransition] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_titles(self) -> "LegalTransitionManifest":
        titles = [transition.title for transition in self.transitions]
        if len(titles) != len(set(titles)):
            raise ValueError("转换清单中的法规标题不得重复")
        return self


class LegalImportResult(BaseModel):
    imported_documents: int = 0
    imported_versions: int = 0
    imported_chunks: int = 0
    duplicates: int = 0
    transitioned_versions: int = 0
    rejected: int = 0
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def _normalized_payload(raw: RawLegalDocument) -> tuple[dict, str]:
    source_url = str(raw.source_url)
    validate_source(source_url)
    data = raw.model_dump(mode="json")
    data["source_url"] = source_url
    data["chunks"] = sorted(data["chunks"], key=lambda item: (item["sequence"], item["locator"]))
    substantive = {
        key: value
        for key, value in data.items()
        if key
        not in {
            "review_status",
            "status",
            "supersedes_content_hash",
            "transition_kind",
        }
    }
    fingerprint = json.dumps(
        substantive,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return data, hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def _resolve_version_transition(
    db: Session,
    *,
    document: LegalDocument,
    raw: RawLegalDocument,
) -> LegalDocumentVersion | None:
    if raw.supersedes_content_hash is None:
        return None
    predecessor = db.scalar(
        select(LegalDocumentVersion).where(
            LegalDocumentVersion.document_id == document.id,
            LegalDocumentVersion.content_hash == raw.supersedes_content_hash,
        )
    )
    if predecessor is None:
        raise ValueError("未找到 supersedes_content_hash 指定的同名法规旧版本")
    if predecessor.status != "active":
        raise ValueError("只能替换当前 active 的旧版本")
    if raw.transition_kind == "correction":
        if raw.effective_on != predecessor.effective_on:
            raise ValueError("勘误替换必须保持旧版本的生效日期")
    elif raw.transition_kind == "amendment":
        if raw.effective_on <= predecessor.effective_on:
            raise ValueError("正式修订的新版本生效日期必须晚于旧版本")
    return predecessor


def _transition_predecessor(
    db: Session,
    *,
    predecessor: LegalDocumentVersion,
    raw: RawLegalDocument,
) -> None:
    predecessor.expired_on = raw.effective_on
    predecessor.status = "superseded" if raw.transition_kind == "correction" else "expired"
    authorities = db.scalars(
        select(LegalAuthority)
        .join(LegalChunk, LegalChunk.authority_id == LegalAuthority.id)
        .where(LegalChunk.version_id == predecessor.id)
    ).all()
    for authority in authorities:
        authority.expired_on = raw.effective_on


def import_legal_records(db: Session, records: list[RawLegalDocument]) -> LegalImportResult:
    result = LegalImportResult()
    for raw in records:
        try:
            with db.begin_nested():
                data, version_hash = _normalized_payload(raw)
                key = canonical_document_key(data["title"])
                document = db.scalar(
                    select(LegalDocument).where(LegalDocument.canonical_key == key)
                )
                if document is None:
                    document = LegalDocument(
                        canonical_key=key,
                        title=data["title"],
                        authority_type=data["authority_type"],
                        level=data["level"],
                        jurisdiction=data["jurisdiction"],
                        issuing_body=data["issuing_body"],
                        source_url=data["source_url"],
                    )
                    db.add(document)
                    db.flush()
                    result.imported_documents += 1
                else:
                    document.authority_type = data["authority_type"]
                    document.level = data["level"]
                    document.jurisdiction = data["jurisdiction"]
                    document.issuing_body = data["issuing_body"]
                    document.source_url = data["source_url"]
                duplicate = db.scalar(
                    select(LegalDocumentVersion.id).where(
                        LegalDocumentVersion.document_id == document.id,
                        LegalDocumentVersion.content_hash == version_hash,
                    )
                )
                if duplicate:
                    result.duplicates += 1
                    continue
                predecessor = _resolve_version_transition(
                    db,
                    document=document,
                    raw=raw,
                )
                overlap_query = select(LegalDocumentVersion.id).where(
                        LegalDocumentVersion.document_id == document.id,
                        LegalDocumentVersion.effective_on <= (raw.expired_on or date.max),
                        (LegalDocumentVersion.expired_on.is_(None))
                        | (LegalDocumentVersion.expired_on > raw.effective_on),
                )
                if predecessor is not None:
                    overlap_query = overlap_query.where(
                        LegalDocumentVersion.id != predecessor.id
                    )
                overlapping = db.scalar(overlap_query)
                if overlapping:
                    raise ValueError(
                        f"《{raw.title}》存在未声明替换关系的生效区间重叠版本"
                    )
                version = LegalDocumentVersion(
                    document_id=document.id,
                    version_label=data["version_label"],
                    status=data["status"],
                    promulgated_on=raw.promulgated_on,
                    effective_on=raw.effective_on,
                    expired_on=raw.expired_on,
                    source_url=data["source_url"],
                    content_hash=version_hash,
                    review_status=data["review_status"],
                    supersedes_id=predecessor.id if predecessor else None,
                )
                db.add(version)
                db.flush()
                result.imported_versions += 1
                for chunk_data in data["chunks"]:
                    authority = LegalAuthority(
                        title=data["title"],
                        article=chunk_data["locator"],
                        content=chunk_data["content"],
                        level=data["level"],
                        region=data["jurisdiction"],
                        effective_on=raw.effective_on,
                        expired_on=raw.expired_on,
                        source_url=data["source_url"],
                        keywords=chunk_data["keywords"],
                    )
                    db.add(authority)
                    db.flush()
                    db.add(
                        LegalChunk(
                            version_id=version.id,
                            authority_id=authority.id,
                            locator=chunk_data["locator"],
                            sequence=chunk_data["sequence"],
                            heading=chunk_data["heading"],
                            content=chunk_data["content"],
                            keywords=chunk_data["keywords"],
                            content_hash=hashlib.sha256(
                                chunk_data["content"].encode("utf-8")
                            ).hexdigest(),
                        )
                    )
                    result.imported_chunks += 1
                if predecessor is not None:
                    _transition_predecessor(db, predecessor=predecessor, raw=raw)
                    result.transitioned_versions += 1
        except Exception as exc:
            result.rejected += 1
            result.errors.append(str(exc))
    if get_settings().embedding_provider == "deterministic":
        from app.embeddings import index_legal_chunks

        index_legal_chunks(db)
    db.add(
        AuditEvent(
            event_type="legal_knowledge_import",
            payload=result.model_dump(),
        )
    )
    db.commit()
    return result


def import_legal_jsonl(
    db: Session,
    path: Path,
    *,
    transition_manifest_path: Path | None = None,
) -> LegalImportResult:
    corpus_bytes = path.read_bytes()
    records: list[RawLegalDocument] = []
    for line_number, line in enumerate(corpus_bytes.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(RawLegalDocument.model_validate_json(line))
        except Exception as exc:
            raise ValueError(f"第 {line_number} 行格式无效：{exc}") from exc
    if transition_manifest_path is not None:
        manifest = LegalTransitionManifest.model_validate_json(
            transition_manifest_path.read_text(encoding="utf-8")
        )
        corpus_hash = hashlib.sha256(corpus_bytes).hexdigest()
        if corpus_hash != manifest.target_corpus_sha256:
            raise ValueError("转换清单与目标语料 SHA-256 不匹配")
        by_title = {record.title: index for index, record in enumerate(records)}
        for transition in manifest.transitions:
            index = by_title.get(transition.title)
            if index is None:
                raise ValueError(f"转换清单中的《{transition.title}》不在目标语料中")
            if records[index].transition_kind is not None:
                raise ValueError(f"《{transition.title}》已在语料中声明版本转换")
            records[index] = records[index].model_copy(
                update={
                    "supersedes_content_hash": transition.supersedes_content_hash,
                    "transition_kind": transition.transition_kind,
                }
            )
    return import_legal_records(db, records)
