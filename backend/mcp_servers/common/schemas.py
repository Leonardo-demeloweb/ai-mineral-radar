"""
Shared Schemas for MCP Servers
================================

Common Pydantic models used across all MCP tools.
"""

from pydantic import BaseModel, Field


class GeoPoint(BaseModel):
    """Geographic coordinate (WGS84)."""
    lat: float = Field(description="Latitude (-90 to 90)")
    lon: float = Field(description="Longitude (-180 to 180)")


class PaginationParams(BaseModel):
    """Pagination parameters for search results."""
    pagina: int = Field(default=1, ge=1, description="Número da página (1-based)")
    por_pagina: int = Field(default=20, ge=1, le=50, description="Resultados por página")

    @property
    def offset(self) -> int:
        return (self.pagina - 1) * self.por_pagina


class SearchResultMeta(BaseModel):
    """Metadata for paginated search results."""
    total: int = Field(description="Total de resultados encontrados")
    pagina: int = Field(description="Página atual")
    por_pagina: int = Field(description="Resultados por página")
    total_paginas: int = Field(description="Total de páginas")


class ToolResponse(BaseModel):
    """Standard response wrapper for MCP tools."""
    sucesso: bool = Field(default=True)
    mensagem: str | None = Field(default=None)
    dados: dict | list | None = Field(default=None)
    meta: SearchResultMeta | None = Field(default=None)
