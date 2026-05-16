"""
Validação de JWT emitido pelo Azure AD (Entra ID) — fluxo MSAL no frontend.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from jose import jwt
from jose.exceptions import JWTError

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_JWKS_CACHE: dict[str, Any] | None = None
_JWKS_CACHE_AT: float = 0.0
_JWKS_TTL_SEC = 3600


def _audience_candidates() -> list[str]:
    candidates: list[str] = []
    if settings.azure_ad_audience:
        candidates.append(settings.azure_ad_audience.strip())
    cid = (settings.azure_ad_client_id or "").strip()
    if cid:
        candidates.append(cid)
        candidates.append(f"api://{cid}")
    scope = (settings.azure_ad_scope or "").strip()
    if scope and scope not in candidates:
        candidates.append(scope)
    return candidates


async def _fetch_jwks() -> dict[str, Any]:
    global _JWKS_CACHE, _JWKS_CACHE_AT
    now = time.time()
    if _JWKS_CACHE and (now - _JWKS_CACHE_AT) < _JWKS_TTL_SEC:
        return _JWKS_CACHE

    url = f"{settings.azure_ad_authority_url.rstrip('/')}/discovery/v2.0/keys"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        _JWKS_CACHE = resp.json()
        _JWKS_CACHE_AT = now
        return _JWKS_CACHE


def _find_rsa_key(jwks: dict[str, Any], kid: str) -> dict[str, Any] | None:
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key
    return None


async def validate_azure_ad_token(token: str) -> dict[str, Any]:
    """
    Valida assinatura, issuer e audience do access token Bearer.
    Devolve claims brutos do JWT.
    """
    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError as e:
        raise ValueError(f"Invalid token header: {e}") from e

    kid = unverified_header.get("kid")
    if not kid:
        raise ValueError("Token missing kid header")

    jwks = await _fetch_jwks()
    key_data = _find_rsa_key(jwks, kid)
    if not key_data:
        _JWKS_CACHE_AT = 0.0
        jwks = await _fetch_jwks()
        key_data = _find_rsa_key(jwks, kid)
    if not key_data:
        raise ValueError("Signing key not found in JWKS")

    tenant = settings.azure_ad_tenant_id.strip()
    issuer_v2 = f"https://login.microsoftonline.com/{tenant}/v2.0"
    issuer_v1 = f"https://sts.windows.net/{tenant}/"

    audiences = _audience_candidates()
    last_err: Exception | None = None
    for aud in audiences:
        try:
            return jwt.decode(
                token,
                key_data,
                algorithms=["RS256"],
                audience=aud,
                issuer=[issuer_v2, issuer_v1],
                options={"verify_at_hash": False},
            )
        except JWTError as e:
            last_err = e
            continue

    if last_err:
        raise ValueError(f"Token validation failed: {last_err}") from last_err
    raise ValueError("No audience configured for token validation")
