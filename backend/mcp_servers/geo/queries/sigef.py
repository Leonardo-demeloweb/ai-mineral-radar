"""
SIGEF Query Builders
====================

OpenSearch query builders e formatadores de resultado para mr_sigef_v001.

Used by:
    - Tool: imoveis_rurais_em_area   (geo_distance — parcelas próximas a um ponto)
    - Tool: imoveis_rurais_no_poligono (geo_shape intersects — parcelas dentro de polígono)
"""

import logging
from typing import Any

from mcp_servers.common.opensearch_client import OpenSearchService

logger = logging.getLogger("mcp.geo.queries.sigef")

INDEX_SIGEF = "mr_sigef_v001"

FIELDS_BASE = [
    "parcela_codigo",
    "codigo_imovel",
    "nome_area",
    "uf",
    "codigo_municipio",
    "status",
    "situacao_informada",
    "area_ha",
    "centroide",
    "dt_aprovacao",
    "dt_submissao",
    "sobreposicao_area_anm",
    "sobreposicao_ti",
    "sobreposicao_uc",
]


def _format_imovel(hit: dict) -> dict[str, Any]:
    """Formata um hit OpenSearch em estrutura limpa para o LLM."""
    src = hit.get("_source", {})
    centroide = src.get("centroide") or {}
    return {
        "parcela_codigo":        src.get("parcela_codigo"),
        "codigo_imovel":         src.get("codigo_imovel"),
        "nome_area":             src.get("nome_area"),
        "uf":                    src.get("uf"),
        "codigo_municipio":      src.get("codigo_municipio"),
        "status":                src.get("status"),
        "situacao_informada":    src.get("situacao_informada"),
        "area_ha":               src.get("area_ha"),
        "centroide": {
            "lat": centroide.get("lat"),
            "lon": centroide.get("lon"),
        } if centroide else None,
        "dt_aprovacao":          src.get("dt_aprovacao"),
        "dt_submissao":          src.get("dt_submissao"),
        "sobreposicao_area_anm": src.get("sobreposicao_area_anm", False),
        "sobreposicao_ti":       src.get("sobreposicao_ti", False),
        "sobreposicao_uc":       src.get("sobreposicao_uc", False),
    }


# ======================================================================
# imoveis_rurais_em_area — geo_distance (parcelas próximas a um ponto)
# ======================================================================

def build_imoveis_em_area_query(
    latitude: float,
    longitude: float,
    raio_km: float = 50.0,
    apenas_certificadas: bool = True,
    uf: str | None = None,
    codigo_municipio: str | None = None,
    area_min_ha: float | None = None,
    area_max_ha: float | None = None,
    limite: int = 20,
) -> dict[str, Any]:
    """Monta query geo_distance para parcelas SIGEF próximas a um ponto."""
    filters: list[dict] = [
        {"geo_distance": {
            "distance": f"{raio_km}km",
            "centroide": {"lat": latitude, "lon": longitude},
        }}
    ]

    if apenas_certificadas:
        filters.append({"term": {"status": "CERTIFICADA"}})

    if uf:
        filters.append({"term": {"uf": uf.upper()}})

    if codigo_municipio:
        filters.append({"term": {"codigo_municipio": codigo_municipio}})

    if area_min_ha is not None or area_max_ha is not None:
        range_filter: dict = {}
        if area_min_ha is not None:
            range_filter["gte"] = area_min_ha
        if area_max_ha is not None:
            range_filter["lte"] = area_max_ha
        filters.append({"range": {"area_ha": range_filter}})

    return {
        "size": limite,
        "_source": FIELDS_BASE,
        "query": {
            "bool": {"filter": filters}
        },
        "sort": [
            {"_geo_distance": {
                "centroide": {"lat": latitude, "lon": longitude},
                "order":     "asc",
                "unit":      "km",
            }}
        ],
    }


async def executar_imoveis_rurais_em_area(
    os_service: OpenSearchService,
    latitude: float,
    longitude: float,
    raio_km: float = 50.0,
    apenas_certificadas: bool = True,
    uf: str | None = None,
    codigo_municipio: str | None = None,
    area_min_ha: float | None = None,
    area_max_ha: float | None = None,
    limite: int = 20,
) -> dict[str, Any]:
    """
    Busca imóveis rurais certificados (SIGEF/INCRA) próximos a uma coordenada.

    Útil para:
    - Identificar proprietários de terras adjacentes a um processo minerário
    - Verificar se um processo ANM sobrepõe imóvel rural certificado
    - Avaliar conflitos fundiários em área de interesse mineral

    Args:
        os_service: OpenSearch async client
        latitude: Latitude do ponto central
        longitude: Longitude do ponto central
        raio_km: Raio de busca em km (padrão: 50km)
        apenas_certificadas: Filtrar apenas parcelas com status=CERTIFICADA
        uf: Filtrar por UF (ex: "MG")
        codigo_municipio: Filtrar por código IBGE do município
        area_min_ha: Área mínima da parcela em hectares
        area_max_ha: Área máxima da parcela em hectares
        limite: Número máximo de resultados (padrão: 20)

    Returns:
        Dict com ``total``, ``retornados``, ``raio_km`` e lista ``imoveis``.
    """
    query = build_imoveis_em_area_query(
        latitude=latitude,
        longitude=longitude,
        raio_km=raio_km,
        apenas_certificadas=apenas_certificadas,
        uf=uf,
        codigo_municipio=codigo_municipio,
        area_min_ha=area_min_ha,
        area_max_ha=area_max_ha,
        limite=limite,
    )

    result = await os_service.search_with_meta(INDEX_SIGEF, query)
    hits  = result.get("hits", [])
    total = result.get("total", 0)

    imoveis = [_format_imovel(h) for h in hits]

    # Estatísticas agregadas
    com_sobreposicao_anm = sum(1 for im in imoveis if im.get("sobreposicao_area_anm"))
    area_total = sum(im.get("area_ha") or 0.0 for im in imoveis)

    logger.info(
        f"imoveis_rurais_em_area: {len(imoveis)} retornados / {total} total "
        f"(lat={latitude}, lon={longitude}, raio={raio_km}km)"
    )

    return {
        "total":                    total,
        "retornados":               len(imoveis),
        "raio_km":                  raio_km,
        "com_sobreposicao_anm":     com_sobreposicao_anm,
        "area_total_ha":            round(area_total, 2),
        "imoveis":                  imoveis,
    }
