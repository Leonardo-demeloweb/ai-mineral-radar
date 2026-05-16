"""
Municipality Query Builders
============================

OpenSearch query builders and result formatters for mr_municipios_v001.

Documentos são indexados pelo ETL (``bots.bot_municipios``) com:
``codigo_ibge``, ``nome``, ``uf``, ``centroide`` (+ ``poligono``).
Campos legados ``idMunicipio`` / ``siglaUF`` / ``localizacao`` ainda são
lidos no ``_source`` quando presentes.

Used by:
    - Tool 1: buscar_municipio (match nome / term codigo_ibge / term UF)
    - Tool 2: municipio_por_coordenada (geo_shape contains)
    - Tool 3: obter_poligono (term idMunicipio → GeoJSON Feature)
    - Tool 4: municipios_em_raio (geo_distance + _geo_distance sort)
"""

import logging
from typing import Any

from mcp_servers.common.opensearch_client import OpenSearchService
from mcp_servers.geo.schemas import MunicipioResult, MunicipioComPoligono

logger = logging.getLogger("mcp.geo.queries.municipios")

INDEX_MUNICIPIO = "mr_municipios_v001"

FIELDS_BASE = [
    "codigo_ibge",
    "idMunicipio",
    "nome",
    "uf",
    "siglaUF",
    "nomeUF",
    "uf_nome",
    "nomeRegiao",
    "regiao",
    "nomeMesorregiao",
    "mesorregiao",
    "nomeMicrorregiao",
    "microrregiao",
    "centroide",
    "localizacao",
    "localizacaoEconomica",
    "amazoniaLegal",
    "capitalUF",
]

FIELDS_COM_POLIGONO = [*FIELDS_BASE, "poligono"]


def _format_municipio(source: dict, distancia_km: float | None = None) -> dict:
    """Convert raw OpenSearch _source into MunicipioResult-compatible dict.

    Suporta dois schemas:
    - Novo ETL (bot_municipios.py): codigo_ibge, uf, uf_nome, regiao, centroide
    - Schema legado: idMunicipio, siglaUF, nomeUF, nomeRegiao, localizacao
    """
    # Centroide: novo ETL usa "centroide", legado usa "localizacao"
    loc = source.get("centroide") or source.get("localizacao") or {}
    loc_eco = source.get("localizacaoEconomica") or {}

    return {
        # Novo ETL usa "codigo_ibge"; legado usa "idMunicipio"
        "id_ibge": str(
            source.get("codigo_ibge") or source.get("idMunicipio") or ""
        ),
        "nome": source.get("nome", ""),
        # Novo ETL usa "uf"; legado usa "siglaUF"
        "uf": source.get("uf") or source.get("siglaUF", ""),
        # Novo ETL usa "uf_nome"; legado usa "nomeUF"
        "nome_uf": source.get("uf_nome") or source.get("nomeUF"),
        # Novo ETL usa "regiao"; legado usa "nomeRegiao"
        "regiao": source.get("regiao") or source.get("nomeRegiao"),
        "mesorregiao": source.get("mesorregiao") or source.get("nomeMesorregiao"),
        "microrregiao": source.get("microrregiao") or source.get("nomeMicrorregiao"),
        "capital": bool(source.get("capitalUF", False)),
        "amazonia_legal": bool(source.get("amazoniaLegal", False)),
        "centro": {"lat": loc.get("lat"), "lon": loc.get("lon")} if loc else None,
        "centro_economico": (
            {"lat": loc_eco.get("lat"), "lon": loc_eco.get("lon")}
            if loc_eco
            else None
        ),
        "distancia_km": distancia_km,
    }


def _format_feature(source: dict) -> dict | None:
    """Build GeoJSON Feature from _source with poligono field."""
    poligono = source.get("poligono")
    if not poligono:
        return None

    loc = source.get("localizacao") or source.get("centroide") or {}
    uf = source.get("uf") or source.get("siglaUF", "")

    return {
        "type": "Feature",
        "geometry": poligono,
        "properties": {
            "camada": "municipio",
            "nome": source.get("nome", ""),
            "uf": uf,
            "id_ibge": str(source.get("codigo_ibge") or source.get("idMunicipio", "")),
            "centro": (
                {"lat": loc.get("lat"), "lon": loc.get("lon")} if loc else None
            ),
        },
    }


def _parse_nome_e_uf(nome: str | None, uf: str | None) -> tuple[str | None, str | None]:
    """
    Extrai UF de strings como \"Sete Lagoas/MG\" ou \"Campinas - SP\" quando
    ``uf`` não foi passado separadamente (comum em prompts do modelo).
    """
    if not nome:
        return nome, uf
    n = nome.strip()
    if uf is not None:
        return n, uf
    if "/" in n:
        left, right = n.split("/", 1)
        tail = right.strip().upper()
        if len(tail) == 2 and tail.isalpha():
            return left.strip(), tail
    if " - " in n:
        left, right = n.split(" - ", 1)
        tail = right.strip().upper()
        if len(tail) == 2 and tail.isalpha():
            return left.strip(), tail
    return n, uf


# ======================================================================
# Tool 1: buscar_municipio — match nome / term codigo_ibge / term UF
# ======================================================================


async def executar_buscar_municipio(
    os_service: OpenSearchService,
    nome: str | None = None,
    codigo_ibge: str | None = None,
    uf: str | None = None,
    incluir_poligono: bool = False,
    limite: int = 10,
) -> dict[str, Any]:
    """
    Search municipalities by name, IBGE code, or UF.

    Query strategy:
        - codigo_ibge provided → exact ``term`` on ``codigo_ibge`` (ou legado ``idMunicipio``)
        - nome provided → ``match`` with fuzziness on ``nome`` + UF em ``uf`` / ``siglaUF``
        - Formato \"Cidade/UF\" no parâmetro ``nome`` é aceito (UF extraído automaticamente)

    Args:
        os_service: OpenSearch async client
        nome: Municipality name (fuzzy match)
        codigo_ibge: IBGE 7-digit code (exact match)
        uf: UF filter (e.g. "SP", "MG")
        incluir_poligono: Include GeoJSON polygon in results
        limite: Max results (default 10, max 50)

    Returns:
        Dict with ``total`` and ``municipios`` list.
    """
    limite = max(1, min(limite, 50))
    fields = FIELDS_COM_POLIGONO if incluir_poligono else FIELDS_BASE

    nome_q: str | None = nome
    uf_q: str | None = uf

    if codigo_ibge:
        query = _build_query_by_codigo(codigo_ibge, fields)
    elif nome:
        nome_q, uf_q = _parse_nome_e_uf(nome, uf)
        if not nome_q:
            return {"total": 0, "municipios": []}
        query = _build_query_by_nome(nome_q, uf_q, limite, fields)
    else:
        return {"total": 0, "municipios": []}

    logger.debug(
        f"buscar_municipio: codigo_ibge={codigo_ibge}, nome={nome_q}, uf={uf_q}, "
        f"limite={limite}"
    )

    result = await os_service.search_with_meta(INDEX_MUNICIPIO, query)
    hits = result.get("hits", [])

    municipios = []
    for hit in hits:
        source = hit.get("_source", {})
        mun = _format_municipio(source)
        if incluir_poligono:
            mun["feature"] = _format_feature(source)
        municipios.append(mun)

    logger.info(
        f"buscar_municipio: {len(municipios)} results "
        f"(nome={nome_q}, codigo={codigo_ibge}, uf={uf_q})"
    )

    return {"total": len(municipios), "municipios": municipios}


def _build_query_by_codigo(
    codigo_ibge: str, fields: list[str]
) -> dict[str, Any]:
    """Build exact term query for IBGE code lookup."""
    code = codigo_ibge.strip()
    return {
        "size": 1,
        "query": {
            "bool": {
                "should": [
                    {"term": {"codigo_ibge": code}},
                    {"term": {"idMunicipio": code}},
                ],
                "minimum_should_match": 1,
            }
        },
        "_source": fields,
    }


def _build_query_by_nome(
    nome: str,
    uf: str | None,
    limite: int,
    fields: list[str],
) -> dict[str, Any]:
    """Build match query for municipality name with optional UF filter."""
    must = [
        {
            "match": {
                "nome": {
                    "query": nome,
                    "fuzziness": "AUTO",
                }
            }
        }
    ]

    filters: list[dict[str, Any]] = []
    if uf:
        u = uf.upper().strip()
        filters.append(
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

    return {
        "size": limite,
        "query": {
            "bool": {
                "must": must,
                "filter": filters,
            }
        },
        "_source": fields,
    }


# ======================================================================
# Tool 3: obter_poligono — GeoJSON Feature export by code or name+UF
# ======================================================================


async def executar_obter_poligono(
    os_service: OpenSearchService,
    codigo_ibge: str | None = None,
    nome: str | None = None,
    uf: str | None = None,
) -> dict[str, Any]:
    """
    Retrieve a municipality's full GeoJSON polygon.

    Resolution strategy:
        - codigo_ibge → exact ``term`` on ``codigo_ibge`` / ``idMunicipio`` (legado)
        - nome + uf → ``match`` em ``nome`` + filtro ``uf`` / ``siglaUF`` (top-1)
        - nome alone → ``match`` on ``nome`` (top-1 — may be ambiguous)
        - ``nome`` no formato \"Cidade/UF\" extrai a UF automaticamente

    Always fetches ``poligono`` field since that's the purpose of this tool.

    Args:
        os_service: OpenSearch async client
        codigo_ibge: IBGE 7-digit code (preferred)
        nome: Municipality name (used when code is unknown)
        uf: UF to disambiguate name matches

    Returns:
        Dict with ``encontrado``, municipality metadata, and ``feature`` (GeoJSON).
    """
    nome_log: str | None = nome
    uf_log: str | None = uf

    if codigo_ibge:
        query = _build_query_by_codigo(codigo_ibge, FIELDS_COM_POLIGONO)
    elif nome:
        nome_q, uf_q = _parse_nome_e_uf(nome, uf)
        if not nome_q:
            return {"encontrado": False, "municipio": None, "feature": None}
        nome_log, uf_log = nome_q, uf_q
        query = _build_query_by_nome(nome_q, uf_q, 1, FIELDS_COM_POLIGONO)
    else:
        return {"encontrado": False, "municipio": None, "feature": None}

    logger.debug(
        f"obter_poligono: codigo_ibge={codigo_ibge}, nome={nome_log}, uf={uf_log}"
    )

    result = await os_service.search_with_meta(INDEX_MUNICIPIO, query)
    hits = result.get("hits", [])

    if not hits:
        logger.info(
            f"obter_poligono: municipality not found "
            f"(codigo={codigo_ibge}, nome={nome_log}, uf={uf_log})"
        )
        return {"encontrado": False, "municipio": None, "feature": None}

    source = hits[0].get("_source", {})
    municipio = _format_municipio(source)
    feature = _format_feature(source)

    logger.info(
        f"obter_poligono: {municipio['nome']}/{municipio['uf']} "
        f"({municipio['id_ibge']}) — "
        f"polygon {'present' if feature else 'missing'}"
    )

    return {
        "encontrado": True,
        "municipio": municipio,
        "feature": feature,
    }


# ======================================================================
# Tool 2: municipio_por_coordenada — geo_shape contains (point-in-polygon)
# ======================================================================


async def executar_municipio_por_coordenada(
    os_service: OpenSearchService,
    latitude: float,
    longitude: float,
    incluir_poligono: bool = False,
) -> dict[str, Any]:
    """
    Identify which municipality contains the given point.

    Uses OpenSearch geo_shape query with relation=contains on the
    ``poligono`` field of ibge_municipio_v001.

    Note: OpenSearch geo_shape coordinates use [lon, lat] order (GeoJSON standard).

    Args:
        os_service: OpenSearch async client
        latitude: Point latitude
        longitude: Point longitude
        incluir_poligono: Whether to include the full GeoJSON polygon

    Returns:
        Dict with ``encontrado``, ``municipio`` (or ``municipio`` + ``feature``).
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
        f"municipio_por_coordenada: geo_shape contains ({latitude}, {longitude})"
    )

    result = await os_service.search_with_meta(INDEX_MUNICIPIO, query)
    hits = result.get("hits", [])

    if not hits:
        logger.info(
            f"municipio_por_coordenada: no municipality found for "
            f"({latitude}, {longitude}) — point may be in ocean/border"
        )
        return {"encontrado": False, "municipio": None}

    source = hits[0].get("_source", {})
    municipio = _format_municipio(source)

    response: dict[str, Any] = {
        "encontrado": True,
        "municipio": municipio,
    }

    if incluir_poligono:
        response["feature"] = _format_feature(source)

    logger.info(
        f"municipio_por_coordenada: ({latitude}, {longitude}) → "
        f"{municipio['nome']}/{municipio['uf']} ({municipio['id_ibge']})"
    )

    return response


# ======================================================================
# Tool 4: municipios_em_raio — geo_distance + _geo_distance sort
# ======================================================================


async def executar_municipios_em_raio(
    os_service: OpenSearchService,
    latitude: float,
    longitude: float,
    raio_km: float = 50.0,
    uf: str | None = None,
    incluir_poligonos: bool = False,
    limite: int = 20,
) -> dict[str, Any]:
    """
    Find municipalities whose geographic center falls within a radius.

    Uses ``geo_distance`` no campo ``centroide`` (indexação ETL ``bot_municipios``;
    legado ``localizacao`` não é enviado pelo bot atual) com ordenação
    ``_geo_distance``.

    Args:
        os_service: OpenSearch async client
        latitude: Center latitude
        longitude: Center longitude
        raio_km: Radius in km (default 50, max 500)
        uf: Optional UF filter
        incluir_poligonos: Include GeoJSON polygons (heavy — default false)
        limite: Max results (default 20, max 100)

    Returns:
        Dict with ``centro``, ``raio_km``, ``total``, ``municipios`` list
        (each with ``distancia_km``).
    """
    limite = max(1, min(limite, 100))
    raio_km = max(1, min(raio_km, 500))
    fields = FIELDS_COM_POLIGONO if incluir_poligonos else FIELDS_BASE

    filters: list[dict[str, Any]] = [
        {
            "geo_distance": {
                "distance": f"{raio_km}km",
                "centroide": {"lat": latitude, "lon": longitude},
            }
        }
    ]

    if uf:
        u = uf.upper().strip()
        filters.append(
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

    query: dict[str, Any] = {
        "size": limite,
        "query": {
            "bool": {
                "filter": filters,
            }
        },
        "_source": fields,
        "sort": [
            {
                "_geo_distance": {
                    "centroide": {"lat": latitude, "lon": longitude},
                    "order": "asc",
                    "unit": "km",
                }
            }
        ],
    }

    logger.debug(
        f"municipios_em_raio: ({latitude}, {longitude}), "
        f"raio={raio_km}km, uf={uf}, limite={limite}"
    )

    result = await os_service.search_with_meta(INDEX_MUNICIPIO, query)
    hits = result.get("hits", [])

    municipios = []
    for hit in hits:
        source = hit.get("_source", {})

        sort_values = hit.get("sort", [])
        distancia = round(sort_values[0], 1) if sort_values else None

        mun = _format_municipio(source, distancia_km=distancia)
        if incluir_poligonos:
            mun["feature"] = _format_feature(source)
        municipios.append(mun)

    logger.info(
        f"municipios_em_raio: {len(municipios)} municipalities within "
        f"{raio_km}km of ({latitude}, {longitude})"
        + (f" [UF={uf}]" if uf else "")
    )

    return {
        "centro": {"lat": latitude, "lon": longitude},
        "raio_km": raio_km,
        "total": len(municipios),
        "municipios": municipios,
    }
