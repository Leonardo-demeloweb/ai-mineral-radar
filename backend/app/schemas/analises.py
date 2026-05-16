"""
Análises Schemas
================

Pydantic models for Análises (supply analyses linked to projetos).

IMPORTANTE: O modelo é flexível para suportar:
- Busca de jazidas (materiais de mineração via ANM)
- Busca de empresas (produtos/serviços via CNPJ)
- Busca híbrida (ambos)
"""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from bson import ObjectId
from pydantic import Field, field_validator, model_validator

from app.schemas.common import BaseSchema, GeoPoint


# =============================================================================
# Enums
# =============================================================================

class AnaliseStatus(str, Enum):
    """Status of an análise."""

    RASCUNHO = "rascunho"
    EM_ANALISE = "em_analise"
    CONCLUIDA = "concluida"
    ARQUIVADA = "arquivada"


class CategoriaAnalise(str, Enum):
    """
    Categoria da análise — define a fonte de dados principal.

    - MATERIAL_MINERACAO: Busca em índice ANM (jazidas)
    - PRODUTO_COMERCIAL: Busca em índice CNPJ (empresas/produtos)
    - SERVICO: Busca em índice CNPJ (empresas/serviços)
    - HIBRIDO: Busca em ambos os índices
    """

    MATERIAL_MINERACAO = "material_mineracao"
    PRODUTO_COMERCIAL = "produto_comercial"
    SERVICO = "servico"
    HIBRIDO = "hibrido"


class TipoFonte(str, Enum):
    """
    Tipo de fonte do fornecedor selecionado.

    - ANM: Jazida do índice anm_v003
    - CNPJ: Empresa do índice rfb_cnpj_v003
    - MANUAL: Cadastro manual (não existe nos índices)
    """

    ANM = "anm"
    CNPJ = "cnpj"
    MANUAL = "manual"


class VisibilidadeAnalise(str, Enum):
    """Visibility level of an análise."""

    PRIVADO = "privado"
    EQUIPE = "equipe"
    PROJETO = "projeto"
    PUBLICO = "publico"


# =============================================================================
# Fornecedor Selecionado (Generalizado)
# =============================================================================

class FornecedorSelecionado(BaseSchema):
    """
    Schema genérico para fornecedor selecionado em uma análise.

    Pode representar:
    - Jazida (fonte ANM): fornece materiais de mineração
    - Empresa (fonte CNPJ): fornece produtos ou serviços
    - Manual: cadastro manual pelo usuário
    """

    # =========================================================================
    # Identificação
    # =========================================================================

    id: str = Field(
        description="ID único do fornecedor (dsProcesso para ANM, CNPJ para empresas, UUID para manual)"
    )
    tipo_fonte: TipoFonte = Field(
        description="Origem do fornecedor (anm, cnpj, manual)"
    )

    # =========================================================================
    # Dados Básicos (comuns a todas as fontes)
    # =========================================================================

    nome: str = Field(description="Nome/razão social do fornecedor")
    descricao: str | None = Field(
        default=None,
        max_length=500,
        description="Descrição ou atividade principal"
    )
    localizacao: GeoPoint | None = Field(
        default=None,
        description="Coordenadas geográficas"
    )
    endereco: str | None = Field(
        default=None,
        max_length=500,
        description="Endereço completo"
    )
    municipio: str | None = Field(default=None, description="Município")
    uf: str | None = Field(default=None, min_length=2, max_length=2, description="UF")

    # =========================================================================
    # Dados Específicos por Fonte (opcionais)
    # =========================================================================

    # --- ANM (Jazidas) ---
    processo_anm: str | None = Field(
        default=None,
        description="Número do processo ANM (ex: 820.512/2018)"
    )
    substancia: str | None = Field(
        default=None,
        description="Substância mineral (areia, brita, etc.)"
    )
    fase: str | None = Field(
        default=None,
        description="Fase do processo (lavra, pesquisa, etc.)"
    )
    situacao: str | None = Field(default=None, description="Situação do processo")

    # --- CNPJ (Empresas) ---
    cnpj: str | None = Field(
        default=None,
        description="CNPJ da empresa (14 dígitos)"
    )
    cnae_principal: str | None = Field(
        default=None,
        description="CNAE principal da empresa"
    )
    cnae_descricao: str | None = Field(
        default=None,
        description="Descrição do CNAE"
    )
    porte: str | None = Field(
        default=None,
        description="Porte da empresa (MEI, ME, EPP, etc.)"
    )
    situacao_cadastral: str | None = Field(
        default=None,
        description="Situação cadastral (ATIVA, BAIXADA, etc.)"
    )

    # =========================================================================
    # Anotações do Usuário
    # =========================================================================

    favorito: bool = Field(default=False, description="Marcado como favorito")
    aprovado: bool | None = Field(
        default=None,
        description="Aprovado (True), Reprovado (False), ou Pendente (None)"
    )
    notas: str | None = Field(
        default=None,
        max_length=2000,
        description="Notas e observações do usuário"
    )
    contato_nome: str | None = Field(
        default=None,
        max_length=200,
        description="Nome do contato"
    )
    contato_telefone: str | None = Field(
        default=None,
        max_length=20,
        description="Telefone do contato"
    )
    contato_email: str | None = Field(
        default=None,
        max_length=100,
        description="Email do contato"
    )

    # =========================================================================
    # Dados de Logística (calculados)
    # =========================================================================

    distancia_km: float | None = Field(
        default=None,
        description="Distância até o projeto em km"
    )
    tempo_estimado_min: int | None = Field(
        default=None,
        description="Tempo estimado de deslocamento em minutos"
    )
    custo_frete_estimado: float | None = Field(
        default=None,
        description="Custo estimado de frete"
    )

    # =========================================================================
    # Metadados
    # =========================================================================

    topico: str | None = Field(
        default=None,
        max_length=200,
        description="Tópico/categoria de busca (ex: 'Areia lavada', 'Cimento')"
    )
    adicionado_em: datetime = Field(
        default_factory=datetime.utcnow,
        description="Data em que foi adicionado à análise"
    )
    adicionado_por: str | None = Field(
        default=None,
        description="ID do usuário que adicionou"
    )


# =============================================================================
# Filtros de Busca (Genéricos)
# =============================================================================

class FiltrosANM(BaseSchema):
    """Filtros específicos para busca em índice ANM (jazidas)."""

    substancias: list[str] = Field(default_factory=list, description="Substâncias minerais")
    fases: list[str] = Field(
        default_factory=list,
        description="Fases do processo (lavra, pesquisa, etc.)"
    )
    situacoes: list[str] = Field(default_factory=list, description="Situações do processo")
    tipos_uso: list[str] = Field(default_factory=list, description="Tipos de uso do minério")


class FiltrosCNPJ(BaseSchema):
    """Filtros específicos para busca em índice CNPJ (empresas)."""

    cnaes: list[str] = Field(default_factory=list, description="Códigos CNAE")
    portes: list[str] = Field(
        default_factory=list,
        description="Portes (MEI, ME, EPP, MEDIO, GRANDE)"
    )
    situacoes_cadastrais: list[str] = Field(
        default_factory=list,
        description="Situações cadastrais"
    )
    naturezas_juridicas: list[str] = Field(
        default_factory=list,
        description="Naturezas jurídicas"
    )
    incluir_mei: bool = Field(
        default=True,
        description="Incluir Microempreendedores Individuais (MEI) nos resultados"
    )


class FiltrosBusca(BaseSchema):
    """
    Schema para filtros de busca salvos em uma análise.

    Suporta filtros para ambas as fontes (ANM e CNPJ).
    """

    ufs: list[str] = Field(default_factory=list, description="UFs para filtrar")
    municipios: list[str] = Field(default_factory=list, description="Municípios para filtrar")
    raio_km: float | None = Field(default=None, ge=1, le=500, description="Raio de busca em km")
    centro_busca: GeoPoint | None = Field(
        default=None,
        description="Centro da busca geográfica"
    )
    texto_livre: str | None = Field(
        default=None,
        max_length=500,
        description="Texto livre para busca full-text"
    )
    filtros_anm: FiltrosANM | None = Field(
        default=None,
        description="Filtros específicos para ANM"
    )
    filtros_cnpj: FiltrosCNPJ | None = Field(
        default=None,
        description="Filtros específicos para CNPJ"
    )


# =============================================================================
# Arquivo KML
# =============================================================================

class ArquivoKML(BaseSchema):
    """Schema for uploaded KML/GeoJSON files."""

    id: str = Field(description="ID único do arquivo")
    nome: str = Field(max_length=200, description="Nome do arquivo")
    tipo: str = Field(description="Tipo do arquivo (kml, kmz, geojson)")
    tamanho_bytes: int = Field(description="Tamanho em bytes")
    storage_path: str = Field(description="Caminho no storage")
    descricao: str | None = Field(default=None, max_length=500)
    cor: str | None = Field(default=None, description="Cor de exibição (hex)")
    visivel: bool = Field(default=True, description="Visível no mapa")
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)
    uploaded_by: str | None = None


# =============================================================================
# Base Schemas
# =============================================================================

class AnaliseBase(BaseSchema):
    """Base schema with common análise fields."""

    titulo: str = Field(
        min_length=3,
        max_length=200,
        description="Título da análise",
        examples=["Fornecedores de Areia Lavada", "Parafusos e Fixadores", "Transportadoras"]
    )
    descricao: str | None = Field(
        default=None,
        max_length=2000,
        description="Descrição da análise"
    )

    # Link to projeto
    projeto_id: str = Field(description="ID do projeto vinculado")

    # =========================================================================
    # Configuração da Análise
    # =========================================================================

    categoria: CategoriaAnalise = Field(
        default=CategoriaAnalise.HIBRIDO,
        description="Categoria da análise (define fonte de dados). Default hibrido quando auto-populado via chat."
    )
    termo_busca: str = Field(
        default="",
        max_length=200,
        description="O que está sendo buscado (ex: 'areia lavada', 'parafuso', 'transporte'). Vazio quando auto-populado via chat."
    )

    # =========================================================================
    # Status e Visibilidade
    # =========================================================================

    status: AnaliseStatus = Field(
        default=AnaliseStatus.RASCUNHO,
        description="Status da análise"
    )
    visibilidade: VisibilidadeAnalise = Field(
        default=VisibilidadeAnalise.EQUIPE,
        description="Nível de visibilidade"
    )

    filtros: FiltrosBusca | None = Field(
        default=None,
        description="Filtros de busca salvos"
    )
    tags: list[str] = Field(default_factory=list, description="Tags para organização")


# =============================================================================
# Request Schemas
# =============================================================================

class AnaliseCreate(AnaliseBase):
    """Schema for creating a new análise."""
    pass


class AnaliseUpdate(BaseSchema):
    """Schema for updating an existing análise (all fields optional)."""

    titulo: str | None = Field(default=None, min_length=3, max_length=200)
    descricao: str | None = None
    categoria: CategoriaAnalise | None = None
    termo_busca: str | None = Field(default=None, max_length=200)
    status: AnaliseStatus | None = None
    visibilidade: VisibilidadeAnalise | None = None
    filtros: FiltrosBusca | None = None
    tags: list[str] | None = None


class AddFornecedorRequest(BaseSchema):
    """Schema for adding a fornecedor to an análise."""

    id: str = Field(description="ID do fornecedor (processo ANM ou CNPJ)")
    tipo_fonte: TipoFonte = Field(description="Origem: anm, cnpj ou manual")
    nome: str = Field(description="Nome/razão social")

    descricao: str | None = None
    localizacao: GeoPoint | None = None
    endereco: str | None = None
    municipio: str | None = None
    uf: str | None = None

    # ANM
    processo_anm: str | None = None
    substancia: str | None = None
    fase: str | None = None

    # CNPJ
    cnpj: str | None = None
    cnae_principal: str | None = None
    cnae_descricao: str | None = None
    porte: str | None = None
    situacao_cadastral: str | None = None

    contato_telefone: str | None = None
    contato_email: str | None = None

    distancia_km: float | None = None
    favorito: bool = Field(default=False, description="Marcado como favorito pelo usuário")
    topico: str | None = Field(default=None, max_length=200, description="Tópico/label de busca")
    notas: str | None = Field(default=None, max_length=2000)


class UpdateFornecedorRequest(BaseSchema):
    """Schema for updating a fornecedor in an análise."""

    favorito: bool | None = None
    aprovado: bool | None = None
    notas: str | None = Field(default=None, max_length=2000)
    contato_nome: str | None = None
    contato_telefone: str | None = None
    contato_email: str | None = None


# =============================================================================
# Response Schemas
# =============================================================================

class AnaliseResponse(AnaliseBase):
    """Schema for análise response (includes metadata and selections)."""

    id: str = Field(alias="_id", description="ID único da análise")

    fornecedores: list[FornecedorSelecionado] = Field(
        default_factory=list,
        description="Fornecedores selecionados (jazidas e/ou empresas)"
    )
    arquivos_kml: list[ArquivoKML] = Field(
        default_factory=list,
        description="Arquivos KML/GeoJSON importados"
    )
    compartilhado_com: list[str] = Field(
        default_factory=list,
        description="IDs de usuários com acesso compartilhado"
    )

    created_by: str | None = Field(default=None, description="ID do criador")
    created_at: datetime = Field(description="Data de criação")
    updated_at: datetime = Field(description="Data da última atualização")

    total_fornecedores: int = Field(default=0, description="Total de fornecedores")
    total_jazidas: int = Field(default=0, description="Total de jazidas (ANM)")
    total_empresas: int = Field(default=0, description="Total de empresas (CNPJ)")
    total_favoritos: int = Field(default=0, description="Total de favoritos")
    total_aprovados: int = Field(default=0, description="Total de aprovados")
    total_arquivos: int = Field(default=0, description="Total de arquivos KML")

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
                "_id": "507f1f77bcf86cd799439012",
                "titulo": "Fornecedores de Areia Lavada",
                "descricao": "Levantamento de jazidas de areia lavada no entorno do projeto",
                "projeto_id": "507f1f77bcf86cd799439011",
                "categoria": "material_mineracao",
                "termo_busca": "areia lavada",
                "status": "em_analise",
                "visibilidade": "equipe",
                "total_fornecedores": 8,
                "total_jazidas": 8,
                "total_empresas": 0,
                "total_favoritos": 3,
                "total_aprovados": 2,
            }
        },
    }


class AnaliseListResponse(BaseSchema):
    """Schema for análise list item (lighter version)."""

    id: str = Field(alias="_id")
    titulo: str
    projeto_id: str
    categoria: CategoriaAnalise
    termo_busca: str
    status: AnaliseStatus
    visibilidade: VisibilidadeAnalise
    total_fornecedores: int = 0
    total_favoritos: int = 0
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("id", mode="before")
    @classmethod
    def convert_objectid(cls, v):
        if isinstance(v, ObjectId):
            return str(v)
        return v

    model_config = {"populate_by_name": True}


# =============================================================================
# Backward Compatibility Aliases
# =============================================================================

JazidaSelecionada = FornecedorSelecionado
AddJazidaRequest = AddFornecedorRequest
UpdateJazidaRequest = UpdateFornecedorRequest
