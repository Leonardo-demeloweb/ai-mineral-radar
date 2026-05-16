"""
API Dependencies
================

FastAPI dependencies for injection into route handlers.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.core.logging import get_logger
from app.db.mongodb import get_database
from app.db.opensearch import get_opensearch_client
from app.db.redis import get_redis_client
from app.schemas.common import PaginationParams, TokenPayload

logger = get_logger(__name__)

# Security scheme
bearer_scheme = HTTPBearer(auto_error=False)

_DEV_BEARER = "dev-token"


def _mock_dev_user() -> TokenPayload:
    from datetime import datetime, timedelta

    now = datetime.utcnow()
    return TokenPayload(
        sub="dev-user-001",
        exp=now + timedelta(hours=24),
        iat=now,
        email="dev@mineralradar.com.br",
        name="Developer",
        roles=["admin"],
        empresa_id="dev-empresa-001",
    )


async def get_current_user(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, 
        Depends(bearer_scheme)
    ] = None,
) -> TokenPayload | None:
    """
    Dependency to get current authenticated user from JWT token.
    
    Returns None for unauthenticated requests (use require_auth for protected routes).
    In development mode with no Azure AD configured, returns a mock user automatically.
    """
    # Sem tenant real (vazio ou placeholder do env.example) → usuário dev automático
    if not settings.azure_ad_tenant_effectively_set:
        return _mock_dev_user()

    # Tenant configurado, mas desenvolvimento com Bearer dev-token explicitamente permitido
    if (
        settings.environment == "development"
        and settings.azure_ad_allow_dev_bearer
        and credentials
        and (credentials.credentials or "").strip() == _DEV_BEARER
    ):
        return _mock_dev_user()

    if not credentials:
        return None

    try:
        from datetime import datetime, timezone

        from app.core.azure_ad_auth import validate_azure_ad_token

        token = (credentials.credentials or "").strip()
        claims = await validate_azure_ad_token(token)

        def _claim_ts(name: str) -> datetime:
            raw = claims.get(name)
            if raw is None:
                return datetime.now(timezone.utc)
            return datetime.fromtimestamp(int(raw), tz=timezone.utc)

        roles = claims.get("roles")
        if isinstance(roles, str):
            roles = [roles]
        if not isinstance(roles, list):
            roles = []

        return TokenPayload(
            sub=str(claims.get("oid") or claims.get("sub") or ""),
            exp=_claim_ts("exp"),
            iat=_claim_ts("iat"),
            iss=claims.get("iss"),
            aud=claims.get("aud"),
            email=claims.get("preferred_username") or claims.get("email") or claims.get("upn"),
            name=claims.get("name"),
            roles=roles,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Token validation failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_auth(
    user: Annotated[TokenPayload | None, Depends(get_current_user)]
) -> TokenPayload:
    """
    Dependency that requires authentication.
    
    Use this for protected routes.
    """
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_roles(*required_roles: str):
    """
    Dependency factory that requires specific roles.
    
    Usage:
        @router.get("/admin")
        async def admin_route(user: Annotated[TokenPayload, Depends(require_roles("admin"))]):
            ...
    """
    async def check_roles(
        user: Annotated[TokenPayload, Depends(require_auth)]
    ) -> TokenPayload:
        if not any(role in user.roles for role in required_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Required roles: {', '.join(required_roles)}"
            )
        return user
    
    return check_roles


def pagination_params(
    page: int = 1,
    page_size: int = 20,
) -> PaginationParams:
    """Dependency for pagination parameters."""
    return PaginationParams(page=page, page_size=page_size)


# Type aliases for cleaner route signatures
CurrentUser = Annotated[TokenPayload | None, Depends(get_current_user)]
AuthenticatedUser = Annotated[TokenPayload, Depends(require_auth)]
Pagination = Annotated[PaginationParams, Depends(pagination_params)]
Database = Annotated[any, Depends(get_database)]
Redis = Annotated[any, Depends(get_redis_client)]
OpenSearch = Annotated[any, Depends(get_opensearch_client)]
