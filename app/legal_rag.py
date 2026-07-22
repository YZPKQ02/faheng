"""Build bounded RAG observations and validate citations against versioned law."""

from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import LegalAuthority, LegalChunk, LegalDocument, LegalDocumentVersion


def validate_authority_ids(
    db: Session,
    authority_ids: list[str],
    *,
    as_of: date | None = None,
    region: str = "中国大陆",
) -> list[str]:
    if not authority_ids:
        return []
    as_of = as_of or date.today()
    valid = set(
        db.scalars(
            select(LegalAuthority.id)
            .join(LegalChunk, LegalChunk.authority_id == LegalAuthority.id)
            .join(LegalDocumentVersion, LegalDocumentVersion.id == LegalChunk.version_id)
            .join(LegalDocument, LegalDocument.id == LegalDocumentVersion.document_id)
            .where(
                LegalAuthority.id.in_(authority_ids),
                LegalDocumentVersion.effective_on <= as_of,
                or_(
                    LegalDocumentVersion.expired_on.is_(None),
                    LegalDocumentVersion.expired_on > as_of,
                ),
                LegalDocumentVersion.status.in_(("active", "expired")),
                LegalDocumentVersion.review_status == "published",
                LegalDocument.jurisdiction.in_(("全国", "中国大陆", region)),
            )
        ).all()
    )
    return [authority_id for authority_id in authority_ids if authority_id in valid]


def build_rag_observations(db: Session, authority_ids: list[str]) -> list[dict]:
    if not authority_ids:
        return []
    rows = db.execute(
        select(LegalAuthority, LegalChunk, LegalDocumentVersion, LegalDocument)
        .join(LegalChunk, LegalChunk.authority_id == LegalAuthority.id)
        .join(LegalDocumentVersion, LegalDocumentVersion.id == LegalChunk.version_id)
        .join(LegalDocument, LegalDocument.id == LegalDocumentVersion.document_id)
        .where(LegalDocumentVersion.review_status == "published")
        .where(LegalAuthority.id.in_(authority_ids))
    ).all()
    by_id = {
        authority.id: {
            "authority_id": authority.id,
            "citation": f"《{document.title}》{chunk.locator}",
            "content": chunk.content[:1600],
            "source_url": version.source_url,
            "jurisdiction": document.jurisdiction,
            "level": document.level,
            "version_label": version.version_label,
            "effective_on": version.effective_on.isoformat(),
            "expired_on": version.expired_on.isoformat() if version.expired_on else None,
            "review_status": version.review_status,
        }
        for authority, chunk, version, document in rows
    }
    return [by_id[authority_id] for authority_id in authority_ids if authority_id in by_id]


def render_citations(observations: list[dict]) -> str:
    return "；".join(dict.fromkeys(item["citation"] for item in observations))
