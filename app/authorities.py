from dataclasses import dataclass
from datetime import date
import hashlib
import math
import time

from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.legal_ingestion import canonical_document_key
from app.legal_text import tokenize
from app.models import (
    AuditEvent,
    LegalAuthority,
    LegalChunk,
    LegalChunkEmbedding,
    LegalDocument,
    LegalDocumentVersion,
    ModelDataConsent,
)
from app.observability import query_fingerprint


SEED_AUTHORITIES = [
    {
        "title": "中华人民共和国劳动合同法",
        "article": "第十条",
        "content": "建立劳动关系，应当订立书面劳动合同。已建立劳动关系，未同时订立书面劳动合同的，应当自用工之日起一个月内订立。",
        "level": "法律",
        "effective_on": date(2008, 1, 1),
        "source_url": "https://flk.npc.gov.cn/",
        "keywords": ["未签合同", "书面劳动合同", "劳动关系", "双倍工资"],
    },
    {
        "title": "中华人民共和国劳动合同法",
        "article": "第四十七条",
        "content": "经济补偿按劳动者在本单位工作的年限，每满一年支付一个月工资的标准向劳动者支付。",
        "level": "法律",
        "effective_on": date(2008, 1, 1),
        "source_url": "https://flk.npc.gov.cn/",
        "keywords": ["经济补偿", "工资", "工作年限", "解除"],
    },
    {
        "title": "中华人民共和国劳动合同法",
        "article": "第八十七条",
        "content": "用人单位违法解除或者终止劳动合同的，应当依照本法第四十七条规定的经济补偿标准的二倍向劳动者支付赔偿金。",
        "level": "法律",
        "effective_on": date(2008, 1, 1),
        "source_url": "https://flk.npc.gov.cn/",
        "keywords": ["违法解除", "赔偿金", "二倍", "辞退"],
    },
    {
        "title": "中华人民共和国劳动争议调解仲裁法",
        "article": "第二十七条",
        "content": "劳动争议申请仲裁的时效期间为一年。仲裁时效期间从当事人知道或者应当知道其权利被侵害之日起计算。",
        "level": "法律",
        "effective_on": date(2008, 5, 1),
        "source_url": "https://flk.npc.gov.cn/",
        "keywords": ["仲裁时效", "一年", "劳动争议", "拖欠工资"],
    },
    {
        "title": "中华人民共和国劳动法",
        "article": "第四十四条",
        "content": "安排劳动者延长工作时间、休息日工作且不能补休、法定休假日工作的，应分别依法支付不低于工资的百分之一百五十、百分之二百、百分之三百的报酬。",
        "level": "法律",
        "effective_on": date(1995, 1, 1),
        "source_url": "https://flk.npc.gov.cn/",
        "keywords": ["加班费", "延长工作时间", "休息日", "法定节假日"],
    },
]


def seed_authorities(db: Session) -> None:
    if not db.scalar(select(LegalAuthority.id).limit(1)):
        db.add_all([LegalAuthority(**item) for item in SEED_AUTHORITIES])
        db.flush()
    _backfill_versioned_knowledge(db)
    db.flush()
    settings = get_settings()
    if settings.embedding_provider == "deterministic":
        from app.embeddings import index_legal_chunks

        index_legal_chunks(db)
    db.commit()


def _backfill_versioned_knowledge(db: Session) -> None:
    """Populate the versioned layer for legacy rows created outside Alembic."""
    linked_ids = set(db.scalars(select(LegalChunk.authority_id)).all())
    authorities = db.scalars(select(LegalAuthority)).all()
    versions: dict[tuple[str, date, date | None], LegalDocumentVersion] = {}
    for authority in authorities:
        if authority.id in linked_ids:
            continue
        key = canonical_document_key(authority.title)
        document = db.scalar(select(LegalDocument).where(LegalDocument.canonical_key == key))
        if document is None:
            document = LegalDocument(
                canonical_key=key,
                title=authority.title,
                level=authority.level,
                jurisdiction=authority.region,
                source_url=authority.source_url,
            )
            db.add(document)
            db.flush()
        version_key = (document.id, authority.effective_on, authority.expired_on)
        version = versions.get(version_key)
        if version is None:
            version = db.scalar(
                select(LegalDocumentVersion).where(
                    LegalDocumentVersion.document_id == document.id,
                    LegalDocumentVersion.effective_on == authority.effective_on,
                    LegalDocumentVersion.expired_on == authority.expired_on,
                )
            )
        if version is None:
            fingerprint = f"{authority.title}|{authority.effective_on}|{authority.expired_on}"
            version = LegalDocumentVersion(
                document_id=document.id,
                version_label="内置演示版本",
                status="expired" if authority.expired_on else "active",
                effective_on=authority.effective_on,
                expired_on=authority.expired_on,
                source_url=authority.source_url,
                content_hash=hashlib.sha256(fingerprint.encode("utf-8")).hexdigest(),
                review_status="pending",
            )
            db.add(version)
            db.flush()
        versions[version_key] = version
        db.add(
            LegalChunk(
                version_id=version.id,
                authority_id=authority.id,
                locator=authority.article,
                content=authority.content,
                keywords=authority.keywords,
                content_hash=hashlib.sha256(authority.content.encode("utf-8")).hexdigest(),
            )
        )


LEVEL_WEIGHT = {"法律": 5, "行政法规": 4, "司法解释": 4, "部门规章": 3, "地方规定": 2}


@dataclass(frozen=True)
class AuthorityHit:
    authority: LegalAuthority
    lexical_score: float
    rank: int
    matched_keywords: tuple[str, ...]
    semantic_score: float | None = None
    fusion_score: float | None = None


def retrieve_authority_hits(
    db: Session,
    query: str,
    as_of: date | None = None,
    limit: int = 10,
    region: str = "中国大陆",
    *,
    case_id: str | None = None,
    tenant_id: str | None = None,
) -> list[AuthorityHit]:
    started = time.perf_counter()
    as_of = as_of or date.today()
    rows = db.execute(
        select(LegalAuthority, LegalChunk, LegalDocumentVersion, LegalDocument)
        .join(LegalChunk, LegalChunk.authority_id == LegalAuthority.id)
        .join(LegalDocumentVersion, LegalDocumentVersion.id == LegalChunk.version_id)
        .join(LegalDocument, LegalDocument.id == LegalDocumentVersion.document_id)
        .where(
            LegalDocumentVersion.effective_on <= as_of,
            or_(
                LegalDocumentVersion.expired_on.is_(None),
                LegalDocumentVersion.expired_on > as_of,
            ),
            LegalDocumentVersion.status.in_(("active", "expired")),
            LegalDocument.jurisdiction.in_(("全国", "中国大陆", region)),
        )
    ).all()
    query_tokens = tokenize(query)

    def relevance(row: tuple) -> tuple[float, tuple[str, ...]]:
        authority, chunk, _version, document = row
        keywords = tuple(keyword for keyword in chunk.keywords if keyword in query)
        haystack = document.title + chunk.locator + chunk.content + " ".join(chunk.keywords)
        keyword_hits = len(keywords) * 8
        overlap = len(query_tokens & tokenize(haystack)) / max(1, len(query_tokens))
        title_locator_bonus = 4 if document.title in query or chunk.locator in query else 0
        return keyword_hits + overlap * 10 + title_locator_bonus, keywords

    scored = []
    for row in rows:
        lexical_score, matched_keywords = relevance(row)
        if lexical_score < 0.5:
            continue
        authority, _chunk, _version, document = row
        jurisdiction_bonus = 2 if document.jurisdiction == region else 1
        final_score = lexical_score + LEVEL_WEIGHT.get(document.level, 1) + jurisdiction_bonus
        scored.append((final_score, authority.id, authority, lexical_score, matched_keywords))

    ranked = sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]
    result = [
        AuthorityHit(
            authority=item[2],
            lexical_score=round(item[3], 4),
            rank=index,
            matched_keywords=item[4],
        )
        for index, item in enumerate(ranked, start=1)
    ]
    if case_id and tenant_id:
        settings = get_settings()
        db.add(
            AuditEvent(
                case_id=case_id,
                event_type="authority_retrieval_metric",
                agent="observability",
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                payload={
                    "candidate_count": len(rows),
                    "valid_count": len(rows),
                    "result_count": len(result),
                    "retriever": "versioned_lexical_v1",
                    "top_hit_ids": [hit.authority.id for hit in result[:5]],
                    "query_fingerprint": query_fingerprint(
                        query,
                        secret=settings.observability_hmac_secret,
                        tenant_id=tenant_id,
                    ),
                },
            )
        )
    return result


def search_authorities(
    db: Session,
    query: str,
    as_of: date | None = None,
    limit: int = 10,
    region: str = "中国大陆",
    *,
    case_id: str | None = None,
    tenant_id: str | None = None,
) -> list[LegalAuthority]:
    return [
        hit.authority
        for hit in retrieve_hybrid_authority_hits(
            db,
            query,
            as_of,
            limit,
            region,
            case_id=case_id,
            tenant_id=tenant_id,
        )
    ]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _external_embedding_allowed(
    db: Session,
    *,
    case_id: str | None,
    tenant_id: str | None,
    consent_provider: str,
) -> bool:
    if case_id is None:
        return True
    if tenant_id is None:
        return False
    consents = db.scalars(
        select(ModelDataConsent).where(
            ModelDataConsent.case_id == case_id,
            ModelDataConsent.tenant_id == tenant_id,
            ModelDataConsent.status == "active",
            ModelDataConsent.provider == consent_provider,
        )
    ).all()
    return any("analysis" in consent.purposes for consent in consents)


def retrieve_hybrid_authority_hits(
    db: Session,
    query: str,
    as_of: date | None = None,
    limit: int = 10,
    region: str = "中国大陆",
    *,
    case_id: str | None = None,
    tenant_id: str | None = None,
    lexical_weight: float = 1.0,
    semantic_weight: float = 1.0,
    rrf_k: int = 60,
    _semantic_only: bool = False,
) -> list[AuthorityHit]:
    """Fuse lexical and semantic ranks while preserving legal scope filters."""
    from app.embeddings import EmbeddingError, embed_query, get_embedding_provider

    started = time.perf_counter()
    as_of = as_of or date.today()
    if lexical_weight < 0 or semantic_weight < 0 or rrf_k < 1:
        raise ValueError("RRF 权重必须非负且 rrf_k 必须大于 0")
    lexical_hits = []
    if not _semantic_only:
        lexical_hits = retrieve_authority_hits(
            db,
            query,
            as_of=as_of,
            limit=max(40, limit),
            region=region,
        )
    provider = None
    fallback_reason = None
    semantic_rows: list[tuple[LegalAuthority, float]] = []
    try:
        provider = get_embedding_provider()
        settings = get_settings()
        if provider.name == "deterministic":
            raise EmbeddingError("deterministic_provider_has_no_semantic_capability")
        if (
            provider.name == "http"
            and settings.embedding_consent_required
            and not _external_embedding_allowed(
                db,
                case_id=case_id,
                tenant_id=tenant_id,
                consent_provider=settings.embedding_consent_provider,
            )
        ):
            raise EmbeddingError("external_embedding_consent_missing")
        query_vector = embed_query(
            provider,
            query,
            instruction=settings.embedding_query_instruction,
        )
        statement = (
            select(LegalAuthority, LegalChunkEmbedding)
            .join(LegalChunk, LegalChunk.authority_id == LegalAuthority.id)
            .join(LegalDocumentVersion, LegalDocumentVersion.id == LegalChunk.version_id)
            .join(LegalDocument, LegalDocument.id == LegalDocumentVersion.document_id)
            .join(LegalChunkEmbedding, LegalChunkEmbedding.chunk_id == LegalChunk.id)
            .where(
                LegalDocumentVersion.effective_on <= as_of,
                or_(
                    LegalDocumentVersion.expired_on.is_(None),
                    LegalDocumentVersion.expired_on > as_of,
                ),
                LegalDocumentVersion.status.in_(("active", "expired")),
                LegalDocument.jurisdiction.in_(("全国", "中国大陆", region)),
                LegalChunkEmbedding.provider == provider.name,
                LegalChunkEmbedding.model == provider.model,
                LegalChunkEmbedding.dimensions == provider.dimensions,
            )
        )
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            distance = LegalChunkEmbedding.embedding.cosine_distance(query_vector)
            rows = db.execute(statement.add_columns(distance).order_by(distance).limit(40)).all()
            semantic_rows = [
                (authority, 1 - float(distance_value))
                for authority, _embedding, distance_value in rows
                if 1 - float(distance_value) >= 0.25
            ]
        else:
            rows = db.execute(statement).all()
            semantic_rows = sorted(
                (
                    (authority, _cosine_similarity(query_vector, list(embedding.embedding)))
                    for authority, embedding in rows
                ),
                key=lambda item: (-item[1], item[0].id),
            )
            semantic_rows = [item for item in semantic_rows if item[1] >= 0.25][:40]
    except (EmbeddingError, SQLAlchemyError, TypeError, ValueError) as exc:
        fallback_reason = type(exc).__name__

    fused: dict[str, dict] = {}
    for rank, hit in enumerate(lexical_hits, start=1):
        fused[hit.authority.id] = {
            "authority": hit.authority,
            "lexical_score": hit.lexical_score,
            "semantic_score": None,
            "matched_keywords": hit.matched_keywords,
            "rrf": lexical_weight / (rrf_k + rank),
        }
    for rank, (authority, similarity) in enumerate(semantic_rows, start=1):
        item = fused.setdefault(
            authority.id,
            {
                "authority": authority,
                "lexical_score": 0.0,
                "semantic_score": None,
                "matched_keywords": (),
                "rrf": 0.0,
            },
        )
        item["semantic_score"] = round(similarity, 4)
        item["rrf"] += semantic_weight / (rrf_k + rank)
    ranked = sorted(
        fused.values(),
        key=lambda item: (-item["rrf"], -item["lexical_score"], item["authority"].id),
    )[:limit]
    result = [
        AuthorityHit(
            authority=item["authority"],
            lexical_score=item["lexical_score"],
            semantic_score=item["semantic_score"],
            fusion_score=round(item["rrf"], 6),
            rank=rank,
            matched_keywords=item["matched_keywords"],
        )
        for rank, item in enumerate(ranked, start=1)
    ]
    if case_id and tenant_id:
        settings = get_settings()
        db.add(
            AuditEvent(
                case_id=case_id,
                event_type="authority_retrieval_metric",
                agent="observability",
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                payload={
                    "candidate_count": len(fused),
                    "valid_count": len(fused),
                    "result_count": len(result),
                    "retriever": "hybrid_rrf_v1",
                    "embedding_provider": provider.name if provider else settings.embedding_provider,
                    "semantic_fallback": fallback_reason,
                    "top_hit_ids": [hit.authority.id for hit in result[:5]],
                    "query_fingerprint": query_fingerprint(
                        query,
                        secret=settings.observability_hmac_secret,
                        tenant_id=tenant_id,
                    ),
                },
            )
        )
    return result


def retrieve_semantic_authority_hits(
    db: Session,
    query: str,
    as_of: date | None = None,
    limit: int = 10,
    region: str = "中国大陆",
) -> list[AuthorityHit]:
    """Return semantic-only ranks for offline evaluation and diagnostics."""
    return retrieve_hybrid_authority_hits(
        db,
        query,
        as_of=as_of,
        limit=limit,
        region=region,
        _semantic_only=True,
    )
