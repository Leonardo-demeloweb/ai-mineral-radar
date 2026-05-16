"""
Jazidas MCP Schemas
====================

Pydantic models specific to the Jazidas MCP Server.

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
_DESC_AREA_HA = "Área em hectares"

# ==================== Substance Resolution ====================


class SubstanciaMatch(BaseModel):
    """Resultado de resolução semântica de substância."""

    id: str = Field(description="Nome normalizado da substância (lowercase, sem acento) — valor de substancias_desc.keyword")
    nome: str = Field(description="Nome da substância (ex: AREIA LAVADA)")
    score: float = Field(description="Score de relevância (k-NN ou BM25)")


class TipoUsoMatch(BaseModel):
    """Resultado de resolução semântica de tipo de uso (fallback)."""

    id: str = Field(description="Descrição do tipo de uso — valor de uso_substancia")
    descricao: str = Field(description="Descrição do tipo de uso (ex: Construção civil)")
    score: float = Field(description="Score de relevância")


class ResolucaoSubstancia(BaseModel):
    """
    Resultado completo da resolução de substância.

    ids_substancia: nomes normalizados (lowercase/sem acento) resolvidos via mr_substancias_v001
                    → filtro por substancias_desc.keyword em mr_jazidas_v001
    ids_tipo_uso:   descrições de tipo de uso resolvidas via mr_tipo_uso_v001
                    → filtro por uso_substancia em mr_jazidas_v001
    """

    ids_substancia: list[str] = Field(
        default_factory=list,
        description="Nomes normalizados de substância (para terms em substancias_desc.keyword)",
    )
    ids_tipo_uso: list[str] = Field(
        default_factory=list,
        description="Descrições de tipo de uso (para terms em uso_substancia)",
    )
    matches_substancia: list[SubstanciaMatch] = Field(
        default_factory=list,
        description="Detalhes dos matches de substância",
    )
    matches_tipo_uso: list[TipoUsoMatch] = Field(
        default_factory=list,
        description="Detalhes dos matches de tipo de uso (se fallback)",
    )
    metodo: str = Field(
        default="none",
        description="Método usado: 'knn', 'match', 'tipo_uso_match', 'none'",
    )
    termo_original: str = Field(
        default="",
        description="Termo original fornecido pelo usuário",
    )

    @property
    def encontrou(self) -> bool:
        """Se a resolução encontrou algum resultado."""
        return bool(self.ids_substancia or self.ids_tipo_uso)

    @property
    def campo_filter(self) -> str:
        """Campo de mr_jazidas_v001 a usar no terms filter."""
        if self.ids_substancia:
            return "substancias_desc.keyword"
        elif self.ids_tipo_uso:
            return "uso_substancia"
        return ""

    @property
    def ids_filter(self) -> list[str]:
        """Valores a usar no terms filter."""
        return self.ids_substancia or self.ids_tipo_uso


# ==================== Empresa / Titular ====================


class ContatoEmpresa(BaseModel):
    """Dados de contato de uma empresa (extraídos do rfb_cnpj_v003)."""

    telefone: str | None = Field(default=None, description="Telefone principal")
    email: str | None = Field(default=None, description="Email de contato")
    endereco: str | None = Field(default=None, description="Endereço completo")


class TitularProcesso(BaseModel):
    """Titular de um processo ANM com dados enriquecidos."""

    cnpj_completo: str | None = Field(
        default=None,
        description="CNPJ completo formatado (XX.XXX.XXX/YYYY-ZZ)",
    )
    nome: str = Field(description="Nome/razão social do titular")
    cnpj_basico: str | None = Field(default=None, description="CNPJ básico (8 dígitos)")
    contato: ContatoEmpresa | None = Field(default=None, description="Dados de contato")
    socios: list[str] | None = Field(default=None, description="Nomes dos sócios")


# ==================== Processo ====================


class ProcessoResumido(BaseModel):
    """Processo ANM em formato resumido (para listagens)."""

    ds_processo: str = Field(description="Código do processo (ex: 832.145/2018)")
    fase: str | None = Field(default=None, description="Fase atual do processo")
    area_ha: float | None = Field(default=None, description=_DESC_AREA_HA)
    ativo: bool = Field(default=True, description="Se o processo está ativo")
    substancias: list[str] = Field(default_factory=list, description="Nomes das substâncias")
    tipos_uso: list[str] = Field(default_factory=list, description="Tipos de uso")
    municipios: list[str] = Field(default_factory=list, description="Municípios (UF)")
    uf: list[str] = Field(default_factory=list, description="UFs")
    localizacao: GeoPoint | None = Field(default=None, description="Centróide do processo")
    distancia_km: float | None = Field(default=None, description="Distância ao ponto de busca")
    titular: TitularProcesso | None = Field(default=None, description="Titular principal")


class FornecedorResult(BaseModel):
    """Resultado combinado: processo + empresa + contato (buscar_fornecedores)."""

    processo: ProcessoResumido
    contato: ContatoEmpresa | None = Field(default=None, description="Contato direto da empresa")
    socios: list[str] | None = Field(default=None, description="Lista de sócios")


class ProcessoDetalhado(BaseModel):
    """Processo ANM com todos os campos expandidos (detalhes_processo)."""

    ds_processo: str = Field(description="Código do processo")
    nr_nup: str | None = Field(default=None, description="Número NUP completo")
    ativo: bool = Field(default=True, description="Se está ativo")
    fase: str | None = Field(default=None, description="Fase atual")
    tipo_requerimento: str | None = Field(default=None, description="Tipo do requerimento")
    area_ha: float | None = Field(default=None, description="Área em hectares")
    dt_protocolo: str | None = Field(default=None, description="Data de protocolo")
    localizacao: GeoPoint | None = Field(default=None, description="Centróide")
    substancias: list[dict] = Field(
        default_factory=list,
        description="Substâncias com detalhes (nome, tipoUso, vigência)",
    )
    municipios: list[dict] = Field(
        default_factory=list,
        description="Municípios com código IBGE",
    )
    pessoas: list[dict] = Field(
        default_factory=list,
        description="Pessoas relacionadas (titular, resp. técnico, etc.)",
    )
    eventos: list[dict] = Field(
        default_factory=list,
        description="Histórico de eventos (se solicitado)",
    )
    titulos: list[dict] = Field(
        default_factory=list,
        description="Títulos/documentos legais (se solicitado)",
    )
    titular_empresa: dict | None = Field(
        default=None,
        description="Dados enriquecidos da empresa titular (rfb_cnpj_v003)",
    )


# ==================== Vigência ====================


class VigenciaSubstancia(BaseModel):
    """Status de vigência de uma substância em um processo."""

    id_substancia: int = Field(description="ID da substância")
    nome: str = Field(description="Nome da substância")
    tipo_uso: str | None = Field(default=None, description="Tipo de uso")
    dt_inicio: str | None = Field(default=None, description="Data início vigência")
    dt_fim: str | None = Field(default=None, description="Data fim vigência (null = vigente)")
    vigente: bool = Field(description="Se a substância ainda está vigente")
    motivo_encerramento: str | None = Field(
        default=None,
        description="Motivo do encerramento (se aplicável)",
    )


# ==================== Mapa ====================


class MapaPonto(BaseModel):
    """Ponto para exibição no mapa do frontend."""

    lat: float
    lon: float
    processo: str = Field(description="dsProcesso para referência")
    substancia: str | None = Field(default=None, description="Substância principal")
    fase: str | None = Field(default=None, description="Fase do processo")
    tipo: str = Field(
        default="centroide",
        description="Tipo do ponto: 'centroide' (processo) ou 'poligono' (shape individual)",
    )
    area_ha: float | None = Field(default=None, description=f"{_DESC_AREA_HA} (se tipo=poligono)")
    ativa: bool | None = Field(default=None, description="Se o polígono está ativo (se tipo=poligono)")


# ==================== GeoJSON ====================


class GeoJSONProperties(BaseModel):
    """
    Properties de um GeoJSON Feature.

    Campo ``camada`` distingue o tipo de feature:
        - ``"jazida"``: polígono de concessão minerária (anm_v003)
        - ``"municipio"``: fronteira de município (ibge_municipio_v001)
    """

    camada: str = Field(
        default="jazida",
        description="Camada do mapa: 'jazida' ou 'municipio'",
    )
    # Campos para jazidas (camada="jazida")
    processo: str | None = Field(default=None, description="dsProcesso para referência")
    shape_id: str | None = Field(default=None, description="ID do shape no anm_v003")
    substancia: str | None = Field(default=None, description="Substância deste polígono")
    area_ha: float | None = Field(default=None, description=_DESC_AREA_HA)
    ativa: bool | None = Field(default=None, description="Se o polígono está ativo")
    titular: str | None = Field(default=None, description="Titular deste polígono")
    # Campos para municípios (camada="municipio")
    nome: str | None = Field(default=None, description="Nome do município")
    uf: str | None = Field(default=None, description="Sigla UF do município")
    nome_uf: str | None = Field(default=None, description="Nome da UF")
    id_ibge: str | None = Field(default=None, description="Código IBGE do município")
    centro: GeoPoint | None = Field(default=None, description="Centro geográfico do município")


class GeoJSONFeature(BaseModel):
    """
    GeoJSON Feature para renderização no mapa frontend.

    Suporta dois tipos de geometria:
        - Polígonos de jazidas (``shapes[].poligono`` de anm_v003)
        - Fronteiras de municípios (``poligono`` de ibge_municipio_v001)

    Ambos já estão em formato GeoJSON no OpenSearch.

    Exemplo jazida::

        {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[lon, lat], ...]]},
            "properties": {"camada": "jazida", "processo": "820.517/2013", "substancia": "AREIA"}
        }

    Exemplo município::

        {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[lon, lat], ...]]},
            "properties": {"camada": "municipio", "nome": "Campinas", "uf": "SP"}
        }
    """

    type: str = Field(default="Feature", description="Sempre 'Feature'")
    geometry: dict = Field(description="GeoJSON geometry (Polygon/MultiPolygon)")
    properties: GeoJSONProperties


class GeoJSONFeatureCollection(BaseModel):
    """
    GeoJSON FeatureCollection — formato padrão, usável diretamente com
    Leaflet, Mapbox, Google Maps ou qualquer lib de mapas.

    Pode conter features de múltiplas camadas (jazidas + municípios).
    O frontend usa ``properties.camada`` para renderizar com estilos distintos.
    """

    type: str = Field(default="FeatureCollection", description="Sempre 'FeatureCollection'")
    features: list[GeoJSONFeature] = Field(
        default_factory=list,
        description="Lista de Features (polígonos de jazidas e/ou municípios)",
    )


class MapaResponse(BaseModel):
    """
    Resposta completa de mapa: pontos + geometrias em camadas separadas.

    Três camadas:
        1. ``pontos``: Centróides de TODOS os resultados (lightweight, pins)
        2. ``geometrias_jazidas``: FeatureCollection com polígonos minerários (heavy)
        3. ``geometrias_municipios``: FeatureCollection com fronteiras dos municípios (heavy)

    Geometrias são opcionais — só retornadas com ``incluir_geometria=true``.
    """

    pontos: list[MapaPonto] = Field(
        default_factory=list,
        description="Centróides para exibição como pins no mapa",
    )
    total_pontos: int = Field(default=0, description="Total de pontos")
    geometrias_jazidas: GeoJSONFeatureCollection | None = Field(
        default=None,
        description="Polígonos GeoJSON das jazidas (apenas se incluir_geometria=true)",
    )
    total_geometrias_jazidas: int = Field(
        default=0,
        description="Total de polígonos de jazidas",
    )
    geometrias_municipios: GeoJSONFeatureCollection | None = Field(
        default=None,
        description="Fronteiras GeoJSON dos municípios envolvidos (apenas se incluir_geometria=true)",
    )
    total_geometrias_municipios: int = Field(
        default=0,
        description="Total de polígonos de municípios",
    )
