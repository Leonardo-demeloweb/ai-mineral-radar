"""
Geo MCP Schemas
================

Pydantic models specific to the Geo MCP Server.

These models define:
    - Structured output for municipality lookups (Tools 1-4)
    - Structured output for Azure Maps responses (Tools 5-7)

Common schemas (GeoPoint, SearchResultMeta, ToolResponse)
are imported from mcp_servers.common.schemas.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ==================== Municipality (Tools 1-4) ====================


class MunicipioResult(BaseModel):
    """Resultado de busca/identificação de município."""

    id_ibge: str = Field(description="Código IBGE 7 dígitos")
    nome: str = Field(description="Nome do município")
    uf: str = Field(description="Sigla UF")
    nome_uf: str | None = Field(default=None, description="Nome do estado")
    regiao: str | None = Field(default=None, description="Região (Sudeste, Sul, etc.)")
    mesorregiao: str | None = Field(default=None, description="Mesorregião IBGE")
    microrregiao: str | None = Field(default=None, description="Microrregião IBGE")
    capital: bool = Field(default=False, description="É capital estadual")
    amazonia_legal: bool = Field(default=False, description="Pertence à Amazônia Legal")
    centro: dict | None = Field(
        default=None, description="Centro geográfico {lat, lon}"
    )
    centro_economico: dict | None = Field(
        default=None, description="Centro econômico {lat, lon}"
    )
    distancia_km: float | None = Field(
        default=None, description="Distância ao ponto de busca (em municipios_em_raio)"
    )


class MunicipioComPoligono(MunicipioResult):
    """Município com GeoJSON Feature (polígono opcional)."""

    feature: dict | None = Field(
        default=None,
        description="GeoJSON Feature com geometry (Polygon/MultiPolygon)",
    )


# ==================== Route (Tool 5) ====================


class RotaResult(BaseModel):
    """Resultado de cálculo de rota (Azure Maps Route Directions)."""

    distancia_km: float = Field(description="Distância total em km")
    duracao_min: float = Field(description="Duração estimada em minutos")
    atraso_trafego_min: float = Field(
        default=0, description="Atraso por tráfego em minutos"
    )
    modo: str = Field(description="Modo de viagem: 'truck' ou 'car'")
    resumo: str = Field(
        description="Resumo legível (ex: '47.3 km • ~58 min')"
    )
    polyline: list[dict] = Field(
        description="Pontos da polyline [{lat, lon}, ...]"
    )
    origem: dict = Field(description="Coordenada de origem {lat, lon}")
    destino: dict = Field(description="Coordenada de destino {lat, lon}")


# ==================== Isochrone (Tool 6) ====================


class IsocronaResult(BaseModel):
    """Resultado de isócrona (Azure Maps Route Range)."""

    centro: dict = Field(description="Centro da isócrona {lat, lon}")
    criterio: str = Field(description="'tempo' ou 'distancia'")
    valor: float = Field(description="Valor do critério (minutos ou km)")
    modo: str = Field(description="Modo de viagem: 'truck' ou 'car'")
    feature: dict = Field(
        description="GeoJSON Feature com polígono da área alcançável"
    )


# ==================== Geocoding (Tool 7) ====================


class GeocodingResultItem(BaseModel):
    """Resultado individual de geocoding."""

    endereco: str = Field(description="Endereço completo formatado")
    coordenadas: dict = Field(description="Coordenadas {lat, lon}")
    tipo: str = Field(
        description="Tipo de resultado (Street, Address, POI, Municipality)"
    )
    confianca: str | None = Field(
        default=None, description="Nível de confiança (High, Medium, Low)"
    )
    municipio: str | None = Field(default=None, description="Município")
    uf: str | None = Field(default=None, description="UF")
    cep: str | None = Field(default=None, description="CEP")


class ReverseGeocodingResult(BaseModel):
    """Resultado de reverse geocoding."""

    endereco: str = Field(description="Endereço completo")
    coordenadas: dict = Field(description="Coordenadas {lat, lon}")
    municipio: str | None = Field(default=None, description="Município")
    uf: str | None = Field(default=None, description="UF")
    cep: str | None = Field(default=None, description="CEP")
