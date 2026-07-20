from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException

from app.auth import Principal, current_principal, decode_principal
from app.config import Settings
from app.main import app


def _token_settings() -> Settings:
    return Settings(
        auth_enabled=True,
        oidc_issuer="https://identity.example.test",
        oidc_audience="legal-advisor-api",
        oidc_signing_key="test-signing-secret-with-sufficient-length",
        oidc_algorithms="HS256",
    )


def test_decode_principal_validates_identity_tenant_and_roles():
    settings = _token_settings()
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": "lawyer-1",
            "tenant_id": "firm-1",
            "roles": ["lawyer", "reviewer"],
            "iss": settings.oidc_issuer,
            "aud": settings.oidc_audience,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        settings.oidc_signing_key,
        algorithm="HS256",
    )

    principal = decode_principal(token, settings)

    assert principal.actor_id == "lawyer-1"
    assert principal.tenant_id == "firm-1"
    assert principal.can_review is True


def test_decode_principal_rejects_token_without_tenant():
    settings = _token_settings()
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": "user-1",
            "iss": settings.oidc_issuer,
            "aud": settings.oidc_audience,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        settings.oidc_signing_key,
        algorithm="HS256",
    )

    with pytest.raises(HTTPException) as exc_info:
        decode_principal(token, settings)

    assert exc_info.value.status_code == 403


def test_case_access_is_isolated_by_tenant_and_owner(client):
    alice = Principal("alice", "tenant-a", frozenset())
    bob = Principal("bob", "tenant-b", frozenset())
    reviewer = Principal("reviewer", "tenant-a", frozenset({"reviewer"}))
    try:
        app.dependency_overrides[current_principal] = lambda: alice
        created = client.post("/cases", json={}).json()

        app.dependency_overrides[current_principal] = lambda: bob
        assert client.get(f"/cases/{created['id']}").status_code == 404
        assert (
            client.get(f"/cases/{created['id']}/worker-counsel-memory").status_code == 404
        )

        app.dependency_overrides[current_principal] = lambda: Principal(
            "other-user", "tenant-a", frozenset()
        )
        assert client.get(f"/cases/{created['id']}").status_code == 404

        app.dependency_overrides[current_principal] = lambda: reviewer
        assert client.get(f"/cases/{created['id']}").status_code == 200
    finally:
        app.dependency_overrides.pop(current_principal, None)


def test_internal_metrics_requires_review_role(client):
    try:
        app.dependency_overrides[current_principal] = lambda: Principal(
            "ordinary-user", "tenant-a", frozenset()
        )
        assert client.get("/internal/metrics").status_code == 403
    finally:
        app.dependency_overrides.pop(current_principal, None)
