"""
Common Schemas
==============

Shared Pydantic models used across the application.
"""

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

# Generic type for paginated data
T = TypeVar("T")


class BaseSchema(BaseModel):
    """Base schema with common configurations."""

    model_config = ConfigDict(
        from_attributes=True,  # Enable ORM mode
        populate_by_name=True,  # Allow population by field name or alias
        str_strip_whitespace=True,  # Strip whitespace from strings
    )


class SuccessResponse(BaseSchema):
    """Standard success response."""

    success: bool = True
    message: str = "Operation completed successfully"
    data: Any | None = None


class ErrorResponse(BaseSchema):
    """Standard error response."""

    success: bool = False
    error: str
    error_code: str | None = None
    details: dict[str, Any] | None = None
    request_id: str | None = None


class PaginatedResponse(BaseSchema, Generic[T]):
    """Paginated response wrapper."""

    items: list[T]
    total: int = Field(description="Total number of items")
    page: int = Field(ge=1, description="Current page number")
    page_size: int = Field(ge=1, le=100, description="Items per page")
    pages: int = Field(description="Total number of pages")

    @property
    def has_next(self) -> bool:
        """Check if there's a next page."""
        return self.page < self.pages

    @property
    def has_previous(self) -> bool:
        """Check if there's a previous page."""
        return self.page > 1


class HealthResponse(BaseSchema):
    """Health check response."""

    status: str = Field(description="Overall health status")
    version: str = Field(description="API version")
    environment: str = Field(description="Current environment")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    # Service health checks
    services: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Health status of dependent services"
    )


class GeoPoint(BaseSchema):
    """Geographic point (latitude/longitude)."""

    lat: float = Field(ge=-90, le=90, description="Latitude")
    lon: float = Field(ge=-180, le=180, description="Longitude")


class GeoDistance(BaseSchema):
    """Geographic distance query parameters."""

    center: GeoPoint
    radius_km: float = Field(gt=0, le=500, description="Search radius in kilometers")


class PaginationParams(BaseSchema):
    """Pagination query parameters."""

    page: int = Field(default=1, ge=1, description="Page number")
    page_size: int = Field(default=20, ge=1, le=100, description="Items per page")

    @property
    def skip(self) -> int:
        """Calculate offset for database query."""
        return (self.page - 1) * self.page_size


class SortParams(BaseSchema):
    """Sorting query parameters."""

    sort_by: str = Field(default="created_at", description="Field to sort by")
    sort_order: str = Field(
        default="desc",
        pattern="^(asc|desc)$",
        description="Sort order (asc or desc)"
    )


class TokenPayload(BaseSchema):
    """JWT token payload."""

    sub: str = Field(description="Subject (user ID)")
    exp: datetime = Field(description="Expiration time")
    iat: datetime = Field(description="Issued at time")
    iss: str | None = Field(default=None, description="Issuer")
    aud: str | list[str] | None = Field(default=None, description="Audience")
    
    # Custom claims
    email: str | None = None
    name: str | None = None
    roles: list[str] = Field(default_factory=list)
    empresa_id: str | None = None
