from collections.abc import Collection

from sqlalchemy.orm import Session

from app.models import AuditEvent, LegalDocumentVersion, LegalVersionReviewStatus


REVIEW_ROLES = frozenset({"admin", "reviewer", "lawyer"})
PUBLISH_ROLES = frozenset({"admin", "lawyer"})


def transition_legal_version(
    db: Session,
    version: LegalDocumentVersion,
    *,
    action: str,
    actor_id: str,
    roles: Collection[str],
    notes: str,
) -> LegalDocumentVersion:
    role_set = frozenset(roles)
    if not role_set & REVIEW_ROLES:
        raise PermissionError("需要法律语料审核权限")

    current = version.review_status
    if action == "approve":
        if current != LegalVersionReviewStatus.PENDING:
            raise ValueError("只有待审核版本可以批准")
        target = LegalVersionReviewStatus.APPROVED
    elif action == "publish":
        if not role_set & PUBLISH_ROLES:
            raise PermissionError("发布法律语料需要管理员或律师权限")
        if current != LegalVersionReviewStatus.APPROVED:
            raise ValueError("只有已批准版本可以发布")
        target = LegalVersionReviewStatus.PUBLISHED
    elif action == "reject":
        if current not in {
            LegalVersionReviewStatus.PENDING,
            LegalVersionReviewStatus.APPROVED,
        }:
            raise ValueError("当前版本不能驳回")
        target = LegalVersionReviewStatus.REJECTED
    else:
        raise ValueError("不支持的法律语料审核动作")

    version.review_status = target
    db.add(version)
    db.add(
        AuditEvent(
            event_type="legal_version_review_transition",
            agent=actor_id,
            payload={
                "version_id": version.id,
                "from": str(current),
                "to": str(target),
                "action": action,
                "notes": notes,
            },
        )
    )
    db.flush()
    return version
