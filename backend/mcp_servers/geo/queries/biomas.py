"""
Biomas Query Builders
=====================

OpenSearch query builders e formatadores de resultado para mr_biomas_v001.

Used by:
    - Tool: bioma_por_coordenada  (geo_shape contains — ponto dentro do polígono do bioma)
"""

import logging
from typing import Any

from mcp_servers.common.opensearch_client import OpenSearchService

logger = logging.getLogger("mcp.geo.queries.biomas")

INDEX_BIOMAS = "mr_biomas_v001"

FIELDS_BASE = ["slug", "nome", "nome_normalizado", "codigo", "area_km2", "centroide"]
FIELDS_COM_POLIGONO = [*FIELDS_BASE, "poligono"]

# Descrições curtas de cada bioma para enriquecer a resposta ao LLM
DESCRICOES: dict[str, str] = {
    "amazonia": (
        "Maior floresta tropical do mundo, cobre ~49% do Brasil. "
        "Alta biodiversidade, rios amazônicos, povos indígenas e reservas naturais."
    ),
    "caatinga": (
        "Único bioma exclusivamente brasileiro. Vegetação xerófila adaptada à seca, "
        "clima semiárido. Ocorre no Nordeste e norte de Minas Gerais."
    ),
    "cerrado": (
        "Segunda maior formação vegetal da América do Sul. "
        "Principal berço de água do Brasil (nasce aqui o São Francisco, o Tocantins e o Araguaia). "
        "Maior fronteira agrícola do país e hotspot de biodiversidade."
    ),
    "mata_atlantica": (
        "Um dos biomas mais ameaçados do planeta — restam ~12% da cobertura original. "
        "Ocorre na costa leste, Sul e Sudeste. Alta densidade populacional humana."
    ),
    "pampa": (
        "Campos sulinos do Rio Grande do Sul. Vegetação herbácea (gramíneas), "
        "clima subtropical. Importante para pecuária e geração de energia eólica."
    ),
    "pantanal": (
        "Maior planície alagável do mundo. Concentrado no Mato Grosso e Mato Grosso do Sul, "
        "com extensões no Paraguai e Bolívia. Megadiversidade aquática e avifauna."
    ),
}


def _format_bioma(source: dict, incluir_descricao: bool = True) -> dict:
    """Formata _source do OpenSearch para resposta estruturada."""
    slug = source.get("slug", "")
    centroide = source.get("centroide") or {}

    result: dict[str, Any] = {
        "slug":       slug,
        "nome":       source.get("nome", ""),
        "codigo":     source.get("codigo"),
        "area_km2":   source.get("area_km2"),
        "centroide":  {
            "lat": centroide.get("lat"),
            "lon": centroide.get("lon"),
        } if centroide else None,
    }

    if incluir_descricao:
        result["descricao"] = DESCRICOES.get(slug, "")

    return result


# ======================================================================
# bioma_por_coordenada — geo_shape contains (point-in-polygon)
# ======================================================================


async def executar_bioma_por_coordenada(
    os_service: OpenSearchService,
    latitude: float,
    longitude: float,
    incluir_poligono: bool = False,
) -> dict[str, Any]:
    """
    Identifica em qual bioma brasileiro está uma coordenada.

    Usa geo_shape query com relation=contains no campo `poligono` de mr_biomas_v001.
    Os 6 polígonos cobrem praticamente todo o território continental do Brasil;
    pontos em regiões de fronteira entre biomas ou em áreas offshore podem não ter match.

    Nota: coordenadas OpenSearch usam [lon, lat] (GeoJSON padrão).

    Args:
        os_service: OpenSearch async client
        latitude: Latitude do ponto
        longitude: Longitude do ponto
        incluir_poligono: Incluir polígono GeoJSON completo do bioma na resposta

    Returns:
        Dict com ``encontrado``, ``bioma`` e (se incluir_poligono) ``feature``.
    """
    fields = FIELDS_COM_POLIGONO if incluir_poligono else FIELDS_BASE

    query: dict[str, Any] = {
        "size": 1,
        "query": {
            "geo_shape": {
                "poligono": {
                    "shape": {
                        "type": "point",
                        "coordinates": [longitude, latitude],
                    },
                    "relation": "contains",
                }
            }
        },
        "_source": fields,
    }

    logger.debug(
        f"bioma_por_coordenada: geo_shape contains ({latitude}, {longitude})"
    )

    result = await os_service.search_with_meta(INDEX_BIOMAS, query)
    hits = result.get("hits", [])

    if not hits:
        logger.info(
            f"bioma_por_coordenada: nenhum bioma encontrado para "
            f"({latitude}, {longitude}) — ponto pode estar offshore ou em fronteira"
        )
        return {"encontrado": False, "bioma": None}

    source = hits[0].get("_source", {})
    bioma = _format_bioma(source)

    response: dict[str, Any] = {
        "encontrado": True,
        "bioma": bioma,
    }

    if incluir_poligono:
        poligono = source.get("poligono")
        if poligono:
            response["feature"] = {
                "type": "Feature",
                "geometry": poligono,
                "properties": {
                    "camada": "bioma",
                    "slug":   bioma["slug"],
                    "nome":   bioma["nome"],
                },
            }

    logger.info(
        f"bioma_por_coordenada: ({latitude}, {longitude}) → {bioma['nome']}"
    )

    return response
