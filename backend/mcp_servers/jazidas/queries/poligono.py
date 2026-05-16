"""
Polígono Intersection Module
==============================

Query builder + orchestrator for **jazidas_por_poligono** (Tool 4).

Suporta dois modos espaciais:
  • **Concessão ∩ polígono** (default): ``geom`` geo_shape ``intersects`` —
    processos cuja área da concessão cruza o polígono (ex.: limite municipal).
  • **Pin dentro do polígono** (``localizacao_dentro_poligono``): campo
    ``location`` com bounding box + PIP em Python — alinhado à isócrona no mapa.

Index: mr_jazidas_v001
    - geom              (geo_shape, root-level)
    - location          (geo_point, root-level centroid)
    - substancias_desc  (keyword[] — substance names)
    - fase              (keyword)
    - ativo             (boolean)
    - numero_processo   (keyword)

Performance:
    - Modo concessão: geo_shape em ``geom`` — ~20–100 ms
    - Modo pin: até ``MAX_RESULTS`` hits no bbox + PIP em memória (igual fornecedores)
    - Polígono município (ibge_municipio_v001): ~5 ms extra se resolver por nome
"""

import logging
from typing import Any

from mcp_servers.common.geo_filters import (
    filter_hits_by_polygon,
    geojson_to_geo_polygon_filter,
)
from mcp_servers.common.opensearch_client import OpenSearchService
from mcp_servers.jazidas.queries.geo import (
    SHAPES_SOURCE_FULL,
    build_mapa_response_with_municipios,
)

logger = logging.getLogger("mcp.jazidas.poligono")

# ==================== Constants ====================

INDEX_ANM = "mr_jazidas_v001"

# Max results per query (geo_shape can be expensive)
MAX_RESULTS = 200

# Source fields for mr_jazidas_v001 flat schema
SOURCE_FIELDS = [
    "numero_processo",
    "ativo",
    "area_ha",
    "dt_requerimento",
    "fase",
    "situacao",
    "substancias_desc",
    "uf",
    "municipio",
    "location",
    "titular",
] + SHAPES_SOURCE_FULL


# ==================== Query Builder ====================


def build_poligono_query(
    geometry: dict,
    substancia: str | None = None,
    fase: str | None = None,
    apenas_ativos: bool = False,
    size: int = 50,
    from_offset: int = 0,
    localizacao_dentro_poligono: bool = False,
) -> dict[str, Any]:
    """
    Build a root-level query on mr_jazidas_v001 for a user GeoJSON polygon.

    Modos:
      • ``localizacao_dentro_poligono=False`` (default): ``geo_shape`` em
        ``geom`` com ``intersects`` — processos cuja **concessão** cruza o
        polígono (ex.: município). O pin ``location`` pode ficar fora.
      • ``localizacao_dentro_poligono=True``: ``geo_bounding_box`` sobre o
        campo ``location`` (estágio 1) + PIP em Python em
        ``executar_busca_por_poligono`` — só processos cujo **ponto do mapa**
        cai dentro do polígono (ex.: isócrona).

    Args:
        geometry: GeoJSON geometry (Polygon or MultiPolygon)
        substancia: Filter by substance name (substancias_desc field)
        fase: Filter by process phase
        apenas_ativos: Filter for active processes only
        size: Number of results to return (cap em ``MAX_RESULTS``)
        from_offset: Pagination offset (ignorado no OS quando PIP; aplicado
            em memória após PIP)
        localizacao_dentro_poligono: se True, filtra pelo centróide ``location``.

    Returns:
        OpenSearch query body
    """
    must: list[dict] = []
    filter_clauses: list[dict] = []

    if localizacao_dentro_poligono:
        filter_clauses.append(
            geojson_to_geo_polygon_filter(geometry, field="location")
        )
    else:
        # ── Core: root-level geo_shape intersection (concessão ∩ polígono) ──
        must.append({
            "geo_shape": {
                "geom": {
                    "shape": geometry,
                    "relation": "intersects",
                }
            }
        })

    # ── Optional filters ──
    if substancia:
        filter_clauses.append({
            "bool": {
                "should": [
                    {"term": {"substancias_desc.keyword": substancia.upper()}},
                    {"match": {"substancias_desc": {"query": substancia, "fuzziness": "AUTO"}}},
                ],
                "minimum_should_match": 1,
            }
        })

    if fase:
        filter_clauses.append({
            "match": {
                "fase": {
                    "query": fase,
                    "fuzziness": "AUTO",
                }
            }
        })

    if apenas_ativos:
        filter_clauses.append({"term": {"ativo": True}})

    # ── Assemble bool query ──
    bool_query: dict[str, Any] = {}
    if must:
        bool_query["must"] = must
    if filter_clauses:
        bool_query["filter"] = filter_clauses
    if not bool_query:
        bool_query = {"must": {"match_all": {}}}

    return {
        "size": min(size, MAX_RESULTS),
        "from": from_offset,
        "query": {"bool": bool_query},
        "_source": SOURCE_FIELDS,
    }


# ==================== Result Formatter ====================


def format_poligono_result(hit: dict) -> dict[str, Any]:
    """
    Format a single mr_jazidas_v001 hit for jazidas_por_poligono.

    Flat schema — no inner_hits since geom is root-level (not nested).
    """
    source = hit.get("_source", {})
    titular = source.get("titular") or {}
    loc = source.get("location") or {}

    return {
        "numero_processo": source.get("numero_processo", ""),
        "fase": source.get("fase"),
        "situacao": source.get("situacao"),
        "area_ha": source.get("area_ha"),
        "ativo": source.get("ativo", True),
        "substancias": _ensure_list(source.get("substancias_desc")),
        "municipio": source.get("municipio"),
        "uf": source.get("uf"),
        "localizacao": {"lat": loc.get("lat"), "lon": loc.get("lon")} if loc else None,
        "titular": titular.get("nome") or titular.get("razao_social"),
        "cnpj_titular": titular.get("cnpj"),
        "dt_requerimento": source.get("dt_requerimento"),
    }


# ==================== Orchestrator ====================


async def executar_busca_por_poligono(
    os_service: OpenSearchService,
    geometry: dict,
    substancia: str | None = None,
    fase: str | None = None,
    apenas_ativos: bool = False,
    size: int = 50,
    from_offset: int = 0,
    localizacao_dentro_poligono: bool = False,
) -> dict[str, Any]:
    """
    Orchestrates polygon search on mr_jazidas_v001.

    When ``localizacao_dentro_poligono`` is True, uses bounding-box prefilter
    on ``location`` then exact point-in-polygon in Python (same strategy as
    ``fornecedores_por_poligono`` / ``empresas_por_poligono``), then applies
    pagination in memory so totals match pins inside the polygon.

    Args:
        os_service: OpenSearch async client
        geometry: GeoJSON geometry (Polygon or MultiPolygon)
        substancia: Filter by substance name (optional)
        fase: Filter by process phase (optional)
        apenas_ativos: Filter for active processes only
        size: Results per page
        from_offset: Pagination offset
        localizacao_dentro_poligono: pin ``location`` must lie inside geometry

    Returns:
        Dict with total, results, map data with geometry.
    """
    page_size = min(size, MAX_RESULTS)

    if localizacao_dentro_poligono:
        query = build_poligono_query(
            geometry=geometry,
            substancia=substancia,
            fase=fase,
            apenas_ativos=apenas_ativos,
            size=MAX_RESULTS,
            from_offset=0,
            localizacao_dentro_poligono=True,
        )
    else:
        query = build_poligono_query(
            geometry=geometry,
            substancia=substancia,
            fase=fase,
            apenas_ativos=apenas_ativos,
            size=page_size,
            from_offset=from_offset,
            localizacao_dentro_poligono=False,
        )

    raw_result = await os_service.search(INDEX_ANM, query)
    hits_wrapper = raw_result.get("hits", {})
    hits = hits_wrapper.get("hits", [])

    if localizacao_dentro_poligono:
        hits = filter_hits_by_polygon(hits, geometry, localizacao_field="location")
        total = len(hits)
        hits = hits[from_offset : from_offset + page_size]
    else:
        total_value = hits_wrapper.get("total", {})
        total = (
            total_value.get("value", 0)
            if isinstance(total_value, dict)
            else total_value
        )

    # ── Step 3: Format results ──
    resultados = [format_poligono_result(hit) for hit in hits]

    # ── Step 4: Map data (always include geometry — this tool is about polygons) ──
    mapa = await build_mapa_response_with_municipios(
        os_service=os_service,
        hits=hits,
        incluir_geometria=True,  # Always for this tool
    )

    return {
        "total": total,
        "resultados": resultados,
        "mapa": mapa,
    }


# ==================== Municipality Helper ====================


async def resolver_poligono_municipio(
    os_service: OpenSearchService,
    nome_municipio: str,
    uf: str | None = None,
) -> dict | None:
    """
    Resolve a municipality name to its GeoJSON polygon.

    Convenience method for when the user says "processos dentro de Guarulhos"
    instead of providing raw GeoJSON coordinates.

    Args:
        os_service: OpenSearch async client
        nome_municipio: Municipality name (e.g., "Guarulhos")
        uf: Optional UF to disambiguate (e.g., "SP")

    Returns:
        GeoJSON geometry dict (Polygon/MultiPolygon) or None if not found.
    """
    from mcp_servers.jazidas.queries.municipio import (
        INDEX_MUNICIPIO,
        MUNICIPIO_SOURCE_FIELDS,
    )

    must_clauses: list[dict] = [
        # match padrão (case-insensitive, sem analyzer customizado para garantir compatibilidade)
        {"match": {"nome": {"query": nome_municipio, "fuzziness": "AUTO"}}},
    ]

    filter_clauses: list[dict] = []
    if uf:
        u = uf.strip().upper()
        # Suporta "uf" (novo ETL bot_municipios) e "siglaUF" (schema legado)
        filter_clauses.append(
            {
                "bool": {
                    "should": [
                        {"term": {"uf": u}},
                        {"term": {"siglaUF": u}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        )

    query = {
        "size": 1,
        "query": {
            "bool": {
                "must": must_clauses,
                **({"filter": filter_clauses} if filter_clauses else {}),
            }
        },
        # Inclui campos de ambos os schemas
        "_source": ["poligono", "nome", "uf", "siglaUF", "codigo_ibge", "idMunicipio"],
    }

    try:
        result = await os_service.search(INDEX_MUNICIPIO, query)
        hits = result.get("hits", {}).get("hits", [])
        if hits:
            source = hits[0].get("_source", {})
            poligono = source.get("poligono")
            if poligono and isinstance(poligono, dict) and "coordinates" in poligono:
                uf_log = source.get("uf") or source.get("siglaUF", "?")
                logger.info(
                    f"Resolved municipality '{nome_municipio}' "
                    f"({uf_log}) to polygon"
                )
                return poligono
        logger.warning(f"Municipality '{nome_municipio}' not found or has no polygon")
        return None
    except Exception as e:
        logger.warning(f"Failed to resolve municipality polygon: {e}")
        return None


# ==================== Validation ====================


def validate_geometry(geometry: dict) -> str | None:
    """
    Validate that the provided geometry is a valid GeoJSON shape.

    Returns:
        None if valid, error message string if invalid.
    """
    if not isinstance(geometry, dict):
        return "geometry deve ser um objeto dict"

    geo_type = geometry.get("type")
    if not geo_type:
        return "geometry deve conter o campo 'type'"

    valid_types = {"Polygon", "MultiPolygon", "Envelope"}
    if geo_type not in valid_types:
        return f"geometry.type deve ser um de {valid_types}, recebido: '{geo_type}'"

    coordinates = geometry.get("coordinates")
    if coordinates is None:
        return "geometry deve conter o campo 'coordinates'"

    if not isinstance(coordinates, list):
        return "geometry.coordinates deve ser uma lista"

    if not coordinates:
        return "geometry.coordinates não pode ser vazio"

    return None


# ==================== Helpers ====================


def _ensure_list(val: Any) -> list:
    """Ensure a value is a list."""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]
