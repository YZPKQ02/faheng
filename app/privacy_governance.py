from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AuditEvent, CasePseudonym, ModelDataConsent
from app.privacy import ModelCallAuthorization, PseudonymRule


def build_model_authorization(
    db: Session,
    *,
    case_id: str,
    tenant_id: str,
    purpose: str,
    settings: Settings,
) -> ModelCallAuthorization | None:
    if not settings.model_consent_required:
        return ModelCallAuthorization(
            consent_id="consent-not-required",
            consent_version=0,
            case_id=case_id,
            tenant_id=tenant_id,
            purpose=purpose,
        )
    consents = list(
        db.scalars(
            select(ModelDataConsent)
            .where(
                ModelDataConsent.case_id == case_id,
                ModelDataConsent.tenant_id == tenant_id,
                ModelDataConsent.provider == settings.model_provider,
                ModelDataConsent.status == "active",
            )
            .order_by(ModelDataConsent.version.desc())
        ).all()
    )
    consent = next((item for item in consents if purpose in item.purposes), None)
    if consent is None:
        return None
    mappings = list(
        db.scalars(
            select(CasePseudonym).where(
                CasePseudonym.case_id == case_id,
                CasePseudonym.tenant_id == tenant_id,
            )
        ).all()
    )
    authorization = ModelCallAuthorization(
        consent_id=consent.id,
        consent_version=consent.version,
        case_id=case_id,
        tenant_id=tenant_id,
        purpose=purpose,
        pseudonyms=tuple(
            PseudonymRule(item.entity_fingerprint, item.source_length, item.pseudonym)
            for item in mappings
        ),
    )
    db.add(
        AuditEvent(
            case_id=case_id,
            event_type="model_data_access_authorized",
            agent="privacy_gateway",
            payload={
                "consent_id": consent.id,
                "consent_version": consent.version,
                "provider": settings.model_provider,
                "purpose": purpose,
                "data_categories": consent.data_categories,
                "pseudonym_count": len(mappings),
            },
        )
    )
    return authorization
