"""
Projetos Schemas
================

Pydantic models for Projetos (mineral / supply-chain projects).
"""

from datetime import datetime
from enum import Enum
from typing import Any

from bson import ObjectId
from pydantic import Field, field_validator

from app.schemas.common import BaseSchema, GeoPoint


class ProjetoStatus(str, Enum):
    """Status of a projeto."""

    PLANEJAMENTO = "planejamento"
    EM_ANDAMENTO = "em_andamento"
    PAUSADO = "pausado"
    CONCLUIDO = "concluido"
    CANCELADO = "cancelado"


class ProjetoType(str, Enum):
    """Type of mineral / supply-chain project."""

    MINERACAO = "mineracao"
    PESQUISA_MINERAL = "pesquisa_mineral"
    LICENCIAMENTO = "licenciamento"
    LAVRA = "lavra"
    BENEFICIAMENTO = "beneficiamento"
    INFRAESTRUTURA = "infraestrutura"
    LOGISTICA = "logistica"
    AMBIENTAL = "ambiental"
    INDUSTRIAL = "industrial"
    OUTRO = "outro"


class PyObjectId(str):
    """Custom type for MongoDB ObjectId."""

    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v, info):
        if isinstance(v, ObjectId):
            return str(v)
        if isinstance(v, str) and ObjectId.is_valid(v):
            return v
        raise ValueError("Invalid ObjectId")


# =============================================================================
# Base Schemas
# =============================================================================

class ProjetoBase(BaseSchema):
    """Base schema with common projeto fields."""

    nome: str = Field(
        min_length=3,
        max_length=200,
        description="Nome do projeto",
        examples=["Pesquisa de Areia Lavada — Rio Paraguaçu"]
    )
    descricao: str | None = Field(
        default=None,
        max_length=2000,
        description="Descrição detalhada do projeto"
    )
    tipo: ProjetoType = Field(
        default=ProjetoType.OUTRO,
        description="Tipo de projeto"
    )
    status: ProjetoStatus = Field(
        default=ProjetoStatus.PLANEJAMENTO,
        description="Status atual do projeto"
    )

    # Location
    localizacao: GeoPoint | None = Field(
        default=None,
        description="Coordenadas de referência do projeto"
    )
    endereco: str | None = Field(
        default=None,
        max_length=500,
        description="Endereço completo"
    )
    municipio: str | None = Field(
        default=None,
        max_length=100,
        description="Município"
    )
    uf: str | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        description="Unidade Federativa (UF)"
    )

    # Project info
    codigo_interno: str | None = Field(
        default=None,
        max_length=50,
        description="Código interno do projeto"
    )
    contrato: str | None = Field(
        default=None,
        max_length=100,
        description="Número do contrato"
    )
    cliente: str | None = Field(
        default=None,
        max_length=200,
        description="Nome do cliente"
    )

    # Dates
    data_inicio_prevista: datetime | None = Field(
        default=None,
        description="Data prevista de início"
    )
    data_fim_prevista: datetime | None = Field(
        default=None,
        description="Data prevista de conclusão"
    )

    # Search radius for suppliers
    raio_busca_km: float = Field(
        default=50.0,
        ge=1.0,
        le=500.0,
        description="Raio de busca de fornecedores em km"
    )

    # Tags for organization
    tags: list[str] = Field(
        default_factory=list,
        description="Tags para organização"
    )


# =============================================================================
# Request Schemas
# =============================================================================

class ProjetoCreate(ProjetoBase):
    """Schema for creating a new projeto."""
    pass


class ProjetoUpdate(BaseSchema):
    """Schema for updating an existing projeto (all fields optional)."""

    nome: str | None = Field(default=None, min_length=3, max_length=200)
    descricao: str | None = None
    tipo: ProjetoType | None = None
    status: ProjetoStatus | None = None
    localizacao: GeoPoint | None = None
    endereco: str | None = None
    municipio: str | None = None
    uf: str | None = None
    codigo_interno: str | None = None
    contrato: str | None = None
    cliente: str | None = None
    data_inicio_prevista: datetime | None = None
    data_fim_prevista: datetime | None = None
    raio_busca_km: float | None = Field(default=None, ge=1.0, le=500.0)
    tags: list[str] | None = None


# =============================================================================
# Response Schemas
# =============================================================================

class ProjetoResponse(ProjetoBase):
    """Schema for projeto response (includes metadata)."""

    id: str = Field(alias="_id", description="ID único do projeto")

    # Metadata
    created_by: str | None = Field(default=None, description="ID do usuário que criou")
    created_at: datetime = Field(description="Data de criação")
    updated_at: datetime = Field(description="Data da última atualização")

    # Computed fields
    total_analises: int = Field(default=0, description="Número de análises vinculadas")

    @field_validator("id", mode="before")
    @classmethod
    def convert_objectid(cls, v):
        if isinstance(v, ObjectId):
            return str(v)
        return v

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "_id": "507f1f77bcf86cd799439011",
                "nome": "Pesquisa de Areia Lavada — Rio Paraguaçu",
                "descricao": "Mapeamento de jazidas de areia lavada no entorno do rio",
                "tipo": "pesquisa_mineral",
                "status": "em_andamento",
                "localizacao": {"lat": -12.9714, "lon": -38.5014},
                "municipio": "Salvador",
                "uf": "BA",
                "codigo_interno": "PRJ-2024-001",
                "raio_busca_km": 100.0,
                "tags": ["areia", "bahia", "rio-paraguacu"],
                "created_by": "user-001",
                "created_at": "2024-01-15T10:30:00Z",
                "updated_at": "2024-01-20T14:45:00Z",
                "total_analises": 3,
            }
        },
    }


class ProjetoListResponse(BaseSchema):
    """Schema for projeto list item (lighter version)."""

    id: str = Field(alias="_id")
    nome: str
    tipo: ProjetoType
    status: ProjetoStatus
    municipio: str | None = None
    uf: str | None = None
    localizacao: GeoPoint | None = None
    total_analises: int = 0
    created_at: datetime
    updated_at: datetime

    @field_validator("id", mode="before")
    @classmethod
    def convert_objectid(cls, v):
        if isinstance(v, ObjectId):
            return str(v)
        return v

    model_config = {"populate_by_name": True}
