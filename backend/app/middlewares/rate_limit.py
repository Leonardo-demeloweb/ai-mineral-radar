"""
Rate Limiting Configuration
===========================

Uses slowapi for rate limiting based on client IP.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings


def get_rate_limit_key(request) -> str:
    """
    Get the key for rate limiting.
    
    Uses client IP by default. Can be extended to use
    user ID for authenticated requests.
    """
    # For authenticated requests, could use user ID
    # if hasattr(request.state, "user"):
    #     return f"user:{request.state.user.id}"
    
    return get_remote_address(request)


# Create limiter instance
limiter = Limiter(
    key_func=get_rate_limit_key,
    default_limits=[
        f"{settings.rate_limit_requests}/minute"
    ] if settings.rate_limit_enabled else [],
    enabled=settings.rate_limit_enabled,
    storage_uri=settings.redis_url if settings.redis_password else None,
)


# Common rate limit decorators for routes
def rate_limit_standard(func):
    """Standard rate limit: 100 requests per minute."""
    return limiter.limit("100/minute")(func)


def rate_limit_strict(func):
    """Strict rate limit: 10 requests per minute (for expensive operations)."""
    return limiter.limit("10/minute")(func)


def rate_limit_auth(func):
    """Auth rate limit: 5 requests per minute (for login attempts)."""
    return limiter.limit("5/minute")(func)
