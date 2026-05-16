"""Schemas module - Pydantic models for request/response validation."""

from app.schemas.analises import (
    AddFornecedorRequest,
    AnaliseCreate,
    AnaliseListResponse,
    AnaliseResponse,
    AnaliseStatus,
    AnaliseUpdate,
    ArquivoKML,
    CategoriaAnalise,
    FiltrosANM,
    FiltrosBusca,
    FiltrosCNPJ,
    FornecedorSelecionado,
    TipoFonte,
    UpdateFornecedorRequest,
    VisibilidadeAnalise,
)
from app.schemas.common import (
    ErrorResponse,
    GeoPoint,
    HealthResponse,
    PaginatedResponse,
    PaginationParams,
    SuccessResponse,
)
from app.schemas.projetos import (
    ProjetoCreate,
    ProjetoListResponse,
    ProjetoResponse,
    ProjetoStatus,
    ProjetoType,
    ProjetoUpdate,
)

__all__ = [
    # Common
    "ErrorResponse",
    "GeoPoint",
    "HealthResponse",
    "PaginatedResponse",
    "PaginationParams",
    "SuccessResponse",
    # Projetos
    "ProjetoCreate",
    "ProjetoListResponse",
    "ProjetoResponse",
    "ProjetoStatus",
    "ProjetoType",
    "ProjetoUpdate",
    # Analises
    "AddFornecedorRequest",
    "AnaliseCreate",
    "AnaliseListResponse",
    "AnaliseResponse",
    "AnaliseStatus",
    "AnaliseUpdate",
    "ArquivoKML",
    "CategoriaAnalise",
    "FiltrosANM",
    "FiltrosBusca",
    "FiltrosCNPJ",
    "FornecedorSelecionado",
    "TipoFonte",
    "UpdateFornecedorRequest",
    "VisibilidadeAnalise",
]
