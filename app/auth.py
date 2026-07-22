from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings


@dataclass(frozen=True)
class Principal:
    actor_id: str
    tenant_id: str
    roles: frozenset[str]

    @property
    def can_review(self) -> bool:
        return bool(self.roles & {"admin", "reviewer", "lawyer"})

    @property
    def can_access_tenant_cases(self) -> bool:
        return bool(self.roles & {"admin", "reviewer"})

    @property
    def can_publish_legal_versions(self) -> bool:
        return bool(self.roles & {"admin", "lawyer"})


bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=8)
def _jwk_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, timeout=10, lifespan=300)


def _claim_roles(value: object) -> frozenset[str]:
    if isinstance(value, str):
        return frozenset(item for item in value.replace(",", " ").split() if item)
    if isinstance(value, list):
        return frozenset(str(item) for item in value if item)
    return frozenset()


def decode_principal(token: str, settings: Settings) -> Principal:
    algorithms = [item.strip() for item in settings.oidc_algorithms.split(",") if item.strip()]
    if not algorithms or not settings.oidc_issuer or not settings.oidc_audience:
        raise HTTPException(status_code=500, detail="OIDC 配置不完整")
    try:
        if settings.oidc_jwks_url:
            key = _jwk_client(settings.oidc_jwks_url).get_signing_key_from_jwt(token)
        elif settings.oidc_signing_key:
            key = settings.oidc_signing_key
        else:
            raise HTTPException(status_code=500, detail="OIDC 验签密钥未配置")
        claims = jwt.decode(
            token,
            key=key,
            algorithms=algorithms,
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
            options={"require": ["exp", "iat", "sub"]},
        )
    except HTTPException:
        raise
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="访问令牌无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    tenant = claims.get(settings.oidc_tenant_claim)
    if not isinstance(tenant, str) or not tenant.strip():
        raise HTTPException(status_code=403, detail="访问令牌缺少租户标识")
    return Principal(
        actor_id=str(claims["sub"]),
        tenant_id=tenant.strip(),
        roles=_claim_roles(claims.get(settings.oidc_roles_claim)),
    )


def current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> Principal:
    settings = get_settings()
    if not settings.auth_enabled:
        return Principal("local", "local", frozenset({"admin", "reviewer"}))
    if not credentials or credentials.scheme.casefold() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要 Bearer 访问令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_principal(credentials.credentials, settings)


def require_reviewer(principal: Principal = Depends(current_principal)) -> Principal:
    if not principal.can_review:
        raise HTTPException(status_code=403, detail="需要人工复核权限")
    return principal
