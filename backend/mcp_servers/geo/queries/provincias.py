"""
Províncias Geológicas Query Builders
======================================

OpenSearch query builders para mr_provincias_v001.

Used by:
    - Tool: provincia_por_coordenada  (geo_shape contains — ponto na província)
"""

import logging
from typing import Any

from mcp_servers.common.opensearch_client import OpenSearchService

logger = logging.getLogger("mcp.geo.queries.provincias")

INDEX_PROVINCIAS = "mr_provincias_v001"

FIELDS_BASE = [
    "slug", "nome", "nome_normalizado",
    "n_ocorrencias", "area_km2", "centroide",
    "descricao", "minerais_principais", "ufs", "fonte",
]
FIELDS_COM_POLIGONO = [*FIELDS_BASE, "poligono"]


def _format_provincia(source: dict) -> dict[str, Any]:
    """Formata _source do OpenSearch para resposta estruturada."""
    centroide = source.get("centroide") or {}
    return {
        "slug":               source.get("slug", ""),
        "nome":               source.get("nome", ""),
        "n_ocorrencias":      source.get("n_ocorrencias"),
        "area_km2":           source.get("area_km2"),
        "centroide": {
            "lat": centroide.get("lat"),
            "lon": centroide.get("lon"),
        } if centroide else None,
        "descricao":          source.get("descricao", ""),
        "minerais_principais": source.get("minerais_principais", []),
        "ufs":                source.get("ufs", []),
        "fonte":              source.get("fonte", ""),
    }


# ======================================================================
# provincia_por_coordenada — geo_shape contains (point-in-polygon)
# ======================================================================


async def executar_provincia_por_coordenada(
    os_service: OpenSearchService,
    latitude: float,
    longitude: float,
    incluir_poligono: bool = False,
) -> dict[str, Any]:
    """
    Identifica em qual província geológica está uma coordenada.

    Usa geo_shape query com relation=contains no campo `poligono` de
    mr_provincias_v001. Os polígonos são convex hulls aproximados derivados
    das ocorrências CPRM — pontos em fronteiras entre províncias podem
    retornar a província com maior área de cobertura.

    Nota: coordenadas OpenSearch usam [lon, lat] (GeoJSON padrão).

    Args:
        os_service: OpenSearch async client
        latitude: Latitude do ponto
        longitude: Longitude do ponto
        incluir_poligono: Incluir polígono GeoJSON aproximado na resposta

    Returns:
        Dict com ``encontrado``, ``provincia`` e (se incluir_poligono) ``feature``.
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
        f"provincia_por_coordenada: geo_shape contains ({latitude}, {longitude})"
    )

    result = await os_service.search_with_meta(INDEX_PROVINCIAS, query)
    hits = result.get("hits", [])

    if not hits:
        logger.info(
            f"provincia_por_coordenada: nenhuma província encontrada para "
            f"({latitude}, {longitude}) — ponto pode estar em fronteira ou offshore"
        )
        return {"encontrado": False, "provincia": None}

    source = hits[0].get("_source", {})
    provincia = _format_provincia(source)

    response: dict[str, Any] = {
        "encontrado": True,
        "provincia": provincia,
    }

    if incluir_poligono:
        poligono = source.get("poligono")
        if poligono:
            response["feature"] = {
                "type": "Feature",
                "geometry": poligono,
                "properties": {
                    "camada":            "provincia_geologica",
                    "slug":              provincia["slug"],
                    "nome":              provincia["nome"],
                    "n_ocorrencias":     provincia["n_ocorrencias"],
                    "minerais_principais": provincia["minerais_principais"],
                    "nota":              "Polígono aproximado (convex hull + buffer das ocorrências CPRM)",
                },
            }

    logger.info(
        f"provincia_por_coordenada: ({latitude}, {longitude}) → {provincia['nome']}"
    )

    return response
