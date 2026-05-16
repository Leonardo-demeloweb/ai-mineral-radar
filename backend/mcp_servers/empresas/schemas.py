"""
Empresas MCP Schemas
=====================

Pydantic models specific to the Empresas MCP Server.

These models define:
    - Input validation for tool parameters
    - Structured output for tool responses
    - Internal data transfer objects

Common schemas (GeoPoint, PaginationParams, SearchResultMeta, ToolResponse)
are imported from mcp_servers.common.schemas.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from mcp_servers.common.schemas import GeoPoint

# ==================== Shared Descriptions ====================
_DESC_CNPJ_BASICO = "CNPJ básico (8 dígitos)"
_DESC_RAZAO_SOCIAL = "Razão social"

# ==================== CNAE Resolution ====================


class CnaeMatch(BaseModel):
    """Resultado de resolução semântica de CNAE."""

    codigo: str = Field(description="Código CNAE (ex: '0810-0/99')")
    nome: str = Field(description="Nome da classe/subclasse")
    score: float = Field(description="Score de relevância (k-NN ou BM25)")


class ResolucaoCnae(BaseModel):
    """
    Resultado completo da resolução semântica de CNAE.

    Gerado pelo CnaeResolver: termo vago → códigos CNAE precisos.
    """

    codigos: list[str] = Field(
        default_factory=list,
        description="Códigos CNAE resolvidos (para terms filter)",
    )
    matches: list[CnaeMatch] = Field(
        default_factory=list,
        description="Detalhes dos matches CNAE",
    )
    metodo: str = Field(
        default="none",
        description="Método: 'knn', 'match', 'direto', 'none'",
    )
    termo_original: str = Field(
        default="",
        description="Termo original fornecido pelo usuário",
    )

    @property
    def encontrou(self) -> bool:
        """Se a resolução encontrou algum resultado."""
        return bool(self.codigos)


# ==================== Contato / Endereço ====================


class ContatoEmpresa(BaseModel):
    """Dados de contato de uma empresa."""

    telefone: str | None = Field(default=None, description="Telefone principal (DDD + número)")
    telefone2: str | None = Field(default=None, description="Segundo telefone")
    email: str | None = Field(default=None, description="Email de contato")
    endereco: str | None = Field(default=None, description="Endereço formatado (uma linha)")


class EnderecoEmpresa(BaseModel):
    """Endereço completo de uma empresa (campos separados)."""

    tipo_logradouro: str | None = Field(default=None, description="Tipo (RUA, AV, etc.)")
    logradouro: str | None = Field(default=None, description="Nome do logradouro")
    numero: str | None = Field(default=None, description="Número")
    complemento: str | None = Field(default=None, description="Complemento")
    bairro: str | None = Field(default=None, description="Bairro")
    cep: str | None = Field(default=None, description="CEP")
    municipio: str | None = Field(default=None, description="Nome do município")
    uf: str | None = Field(default=None, description="Sigla UF")


# ==================== Empresa (buscar_empresas) ====================


class EmpresaResumida(BaseModel):
    """Empresa em formato resumido (para listagens de buscar_empresas)."""

    cnpj_basico: str = Field(description=_DESC_CNPJ_BASICO)
    cnpj_completo: str | None = Field(
        default=None, description="CNPJ completo formatado (XX.XXX.XXX/YYYY-ZZ)"
    )
    razao_social: str = Field(description=_DESC_RAZAO_SOCIAL)
    nome_fantasia: str | None = Field(default=None, description="Nome fantasia")
    cnae_principal: str | None = Field(
        default=None, description="Código CNAE principal"
    )
    cnae_descricao: str | None = Field(
        default=None, description="Descrição CNAE principal"
    )
    situacao: str | None = Field(default=None, description="Situação cadastral")
    uf: str | None = Field(default=None, description="UF")
    municipio: str | None = Field(default=None, description="Nome do município")
    localizacao: GeoPoint | None = Field(default=None, description="Coordenadas")
    distancia_km: float | None = Field(
        default=None, description="Distância ao ponto de busca"
    )
    contato: ContatoEmpresa | None = Field(
        default=None, description="Dados de contato"
    )


# ==================== Sócio ====================


class SocioEmpresa(BaseModel):
    """Sócio de uma empresa (extraído do nested socios)."""

    nome: str = Field(description="Nome completo do sócio")
    cpf_cnpj: str | None = Field(default=None, description="CPF ou CNPJ (parcial)")
    qualificacao: str | None = Field(
        default=None, description="Qualificação no QSA (ex: Sócio-Administrador)"
    )
    data_entrada: str | None = Field(
        default=None, description="Data de entrada na sociedade"
    )


# ==================== CNAE Hierarquia ====================


class CnaeHierarquia(BaseModel):
    """CNAE com hierarquia completa (enriquecido via rfb_cnae_v001)."""

    codigo: str = Field(description="Código CNAE")
    descricao: str = Field(description="Descrição da classe/subclasse")
    secao: str | None = Field(default=None, description="Seção (ex: B)")
    nome_secao: str | None = Field(default=None, description="Nome da seção")
    divisao: str | None = Field(default=None, description="Código divisão")
    nome_divisao: str | None = Field(default=None, description="Nome da divisão")
    grupo: str | None = Field(default=None, description="Código grupo")
    nome_grupo: str | None = Field(default=None, description="Nome do grupo")
    classe: str | None = Field(default=None, description="Código classe")
    nome_classe: str | None = Field(default=None, description="Nome da classe")
    notas_explicativas: str | None = Field(
        default=None, description="Notas explicativas do CNAE"
    )


# ==================== Processos ANM (cross-reference) ====================


class ProcessoAnmResumo(BaseModel):
    """Resumo de processos ANM vinculados a uma empresa."""

    total: int = Field(description="Total de processos minerários")
    por_fase: dict[str, int] = Field(
        default_factory=dict,
        description="Contagem por fase (ex: {'Concessão de Lavra': 2})",
    )
    processos: list[str] = Field(
        default_factory=list,
        description="Lista dos códigos dsProcesso (até 10)",
    )


# ==================== Empresa Detalhada (detalhes_empresa) ====================


class EmpresaDetalhada(BaseModel):
    """Empresa com todos os campos expandidos (detalhes_empresa)."""

    cnpj_basico: str = Field(description=_DESC_CNPJ_BASICO)
    cnpj_completo: str | None = Field(
        default=None, description="CNPJ completo formatado"
    )
    razao_social: str = Field(description=_DESC_RAZAO_SOCIAL)
    nome_fantasia: str | None = Field(default=None, description="Nome fantasia")
    capital_social: float | None = Field(
        default=None, description="Capital social (R$)"
    )
    porte: str | None = Field(default=None, description="Porte da empresa")
    natureza_juridica: str | None = Field(
        default=None, description="Descrição da natureza jurídica"
    )
    situacao: str | None = Field(
        default=None, description="Situação cadastral (ex: Ativa)"
    )
    data_inicio_atividade: str | None = Field(
        default=None, description="Data de início de atividade"
    )
    endereco: EnderecoEmpresa | None = Field(
        default=None, description="Endereço completo"
    )
    contato: ContatoEmpresa | None = Field(
        default=None, description="Dados de contato"
    )
    localizacao: GeoPoint | None = Field(
        default=None, description="Coordenadas geográficas"
    )
    cnae_principal: CnaeHierarquia | None = Field(
        default=None, description="CNAE principal com hierarquia"
    )
    cnaes_secundarios: list[CnaeHierarquia] = Field(
        default_factory=list,
        description="CNAEs secundários com hierarquia",
    )
    socios: list[SocioEmpresa] = Field(
        default_factory=list,
        description="Lista de sócios com qualificação",
    )
    processos_anm: ProcessoAnmResumo | None = Field(
        default=None,
        description="Processos minerários vinculados (cross-ref anm_v003)",
    )
    estabelecimentos: dict | None = Field(
        default=None,
        description="Total de estabelecimentos (matriz + filiais)",
    )


# ==================== Busca por Sócio ====================


class SocioResultado(BaseModel):
    """Empresa encontrada via busca por sócio (buscar_por_socio)."""

    cnpj_basico: str = Field(description=_DESC_CNPJ_BASICO)
    cnpj_completo: str | None = Field(
        default=None, description="CNPJ completo formatado"
    )
    razao_social: str = Field(description=_DESC_RAZAO_SOCIAL)
    situacao: str | None = Field(default=None, description="Situação cadastral")
    uf: str | None = Field(default=None, description="UF")
    municipio: str | None = Field(default=None, description="Município")
    cnae_principal: str | None = Field(
        default=None, description="Código + descrição CNAE principal"
    )
    qualificacao_socio: str | None = Field(
        default=None, description="Qualificação do sócio nesta empresa"
    )
    data_entrada: str | None = Field(
        default=None, description="Data de entrada na sociedade"
    )


# ==================== Mapa (reutiliza GeoJSON do Jazidas) ====================


class MapaPontoEmpresa(BaseModel):
    """Ponto para exibição no mapa (empresa)."""

    lat: float
    lon: float
    tipo: str = Field(default="empresa", description="Tipo: 'empresa'")
    cnpj_basico: str | None = Field(default=None, description="CNPJ básico")
    nome: str | None = Field(default=None, description="Nome fantasia ou razão social")
    cnae: str | None = Field(default=None, description="CNAE principal")
