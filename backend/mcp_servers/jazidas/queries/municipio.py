"""
Município Lookup Module
========================

Fetches municipality boundary polygons from ``ibge_municipio_v001``
for map overlay display.

Index: ibge_municipio_v001 (5.631 docs — all Brazilian municipalities)
    - poligono       (geo_shape)  — municipality boundary polygon
    - localizacao    (geo_point)  — geographic center
    - nome           (text)       — municipality name
    - siglaUF        (keyword)    — state code
    - idMunicipio    (keyword)    — IBGE code (7 digits)

Used by:
    - buscar_fornecedores  (contextual municipality overlay)
    - buscar_jazidas       (same)
    - jazidas_por_poligono (municipality identification)

Performance:
    - Single msearch batch: ~20ms for up to 20 municipalities
    - Polygon sizes: ~2-200 KB per municipality (varies by complexity)
"""

import logging
from typing import Any

from mcp_servers.common.opensearch_client import OpenSearchService

logger = logging.getLogger("mcp.jazidas.municipio")

# ==================== Constants ====================

INDEX_MUNICIPIO = "mr_municipios_v001"

# Fields to retrieve from mr_municipios_v001
# Inclui tanto campos do novo ETL (bot_municipios) quanto legados para compatibilidade
MUNICIPIO_SOURCE_FIELDS = [
    "codigo_ibge",      # novo ETL
    "idMunicipio",      # legado
    "nome",
    "uf",               # novo ETL
    "siglaUF",          # legado
    "uf_nome",          # novo ETL
    "nomeUF",           # legado
    "centroide",        # novo ETL
    "localizacao",      # legado
    "poligono",
]

# Max municipalities to fetch per request (safety)
MAX_MUNICIPIOS_BATCH = 20


# ==================== Query Builders ====================


def build_municipios_query(
    nomes: list[str],
    ufs: list[str] | None = None,
) -> dict[str, Any]:
    """
    Build a query to fetch municipalities by name + optional UF.

    Usa ``should`` com match em nome (text, case-insensitive via standard analyzer)
    e filtro opcional de UF — suporta tanto o novo ETL quanto o schema legado.

    Args:
        nomes: List of municipality names (from mr_jazidas_v001.municipios)
        ufs: Optional list of UFs to narrow down (avoids homonyms)

    Returns:
        OpenSearch query body
    """
    nomes_clean = [n.strip() for n in nomes[:MAX_MUNICIPIOS_BATCH] if n]
    if not nomes_clean:
        return {"size": 0, "query": {"match_none": {}}}

    # match em campo text (case-insensitive, analyzer padrão) para cada nome
    should_clauses: list[dict] = [
        {"match": {"nome": {"query": n, "operator": "and"}}}
        for n in nomes_clean
    ]

    filter_clauses: list[dict] = []
    if ufs:
        ufs_upper = [uf.strip().upper() for uf in ufs if uf]
        if ufs_upper:
            # Suporta campo "uf" (novo ETL) e "siglaUF" (legado)
            filter_clauses.append(
                {
                    "bool": {
                        "should": [
                            {"terms": {"uf": ufs_upper}},
                            {"terms": {"siglaUF": ufs_upper}},
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )

    query_clause: dict = {
        "bool": {
            "should": should_clauses,
            "minimum_should_match": 1,
        }
    }
    if filter_clauses:
        query_clause["bool"]["filter"] = filter_clauses

    return {
        "size": MAX_MUNICIPIOS_BATCH,
        "query": query_clause,
        "_source": MUNICIPIO_SOURCE_FIELDS,
    }


def build_municipios_msearch(
    municipio_uf_pairs: list[tuple[str, str]],
) -> list[dict]:
    """
    Build msearch body for precise municipality lookup (name + UF pairs).

    Each pair is a (nome_municipio, sigla_uf) tuple.
    This avoids homonym issues (e.g., "São Paulo" in SP vs "São Paulo" in other states).

    Returns:
        List of alternating header/body dicts for OpenSearch msearch API.
    """
    body: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for nome, uf in municipio_uf_pairs[:MAX_MUNICIPIOS_BATCH]:
        key = (nome.strip(), uf.strip().upper())
        dedup_key = (key[0].lower(), key[1].lower())
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        body.append({"index": INDEX_MUNICIPIO})
        body.append(
            {
                "size": 1,
                "query": {
                    "bool": {
                        "must": [
                            {"match": {"nome": {"query": key[0], "operator": "and"}}},
                        ],
                        # Suporta campo "uf" (novo ETL) e "siglaUF" (legado)
                        "filter": [
                            {
                                "bool": {
                                    "should": [
                                        {"term": {"uf": key[1]}},
                                        {"term": {"siglaUF": key[1]}},
                                    ],
                                    "minimum_should_match": 1,
                                }
                            }
                        ],
                    }
                },
                "_source": MUNICIPIO_SOURCE_FIELDS,
            }
        )

    return body


# ==================== Fetch + Extract ====================


async def fetch_municipios_boundaries(
    os_service: OpenSearchService,
    nomes: list[str],
    ufs: list[str] | None = None,
) -> list[dict]:
    """
    Fetch municipality boundary polygons from ibge_municipio_v001.

    Simple approach: single query with terms filter on nome.keyword.

    Args:
        os_service: OpenSearch async client
        nomes: Municipality names (from anm_v003 nomesMunicipios)
        ufs: Optional UF filter (from anm_v003 siglasUF)

    Returns:
        List of GeoJSON Feature dicts (municipality boundaries)
    """
    if not nomes:
        return []

    query = build_municipios_query(nomes, ufs)
    if query.get("size") == 0:
        return []

    try:
        result = await os_service.search(INDEX_MUNICIPIO, query)
        hits = result.get("hits", {}).get("hits", [])
        return _extract_municipio_features(hits)
    except Exception as e:
        logger.warning(f"Failed to fetch municipality boundaries: {e}")
        return []


async def fetch_municipios_precise(
    os_service: OpenSearchService,
    municipio_uf_pairs: list[tuple[str, str]],
) -> list[dict]:
    """
    Fetch municipality boundaries using precise name+UF pairs (msearch).

    More precise than the simple query — avoids homonym issues.

    Args:
        os_service: OpenSearch async client
        municipio_uf_pairs: List of (nome_municipio, sigla_uf) tuples

    Returns:
        List of GeoJSON Feature dicts (municipality boundaries)
    """
    if not municipio_uf_pairs:
        return []

    msearch_body = build_municipios_msearch(municipio_uf_pairs)
    if not msearch_body:
        return []

    try:
        msearch_response = await os_service.msearch(msearch_body)
        return _parse_municipios_msearch(msearch_response)
    except Exception as e:
        logger.warning(f"Failed to fetch municipality boundaries (msearch): {e}")
        return []


# ==================== Extractors ====================


def _extract_municipio_features(hits: list[dict]) -> list[dict]:
    """Convert ibge_municipio_v001 hits to GeoJSON Features."""
    features: list[dict] = []
    for hit in hits:
        feature = _municipio_to_geojson_feature(hit.get("_source", {}))
        if feature:
            features.append(feature)
    return features


def _parse_municipios_msearch(msearch_response: dict) -> list[dict]:
    """Parse msearch response and extract GeoJSON Features."""
    features: list[dict] = []
    for resp in msearch_response.get("responses", []):
        hits = resp.get("hits", {}).get("hits", [])
        if hits:
            feature = _municipio_to_geojson_feature(hits[0].get("_source", {}))
            if feature:
                features.append(feature)
    return features


def _municipio_to_geojson_feature(source: dict) -> dict | None:
    """
    Convert a single mr_municipios_v001 source to a GeoJSON Feature.

    Supports both schemas:
    - Novo ETL (bot_municipios): codigo_ibge, uf, uf_nome, centroide
    - Schema legado: idMunicipio, siglaUF, nomeUF, localizacao

    Returns None if the municipality has no polygon geometry.

    Output example::

        {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [...]},
            "properties": {
                "camada": "municipio",
                "nome": "Campinas",
                "uf": "SP",
                "id_ibge": "3509502",
                "centro": {"lat": -22.90, "lon": -47.06}
            }
        }
    """
    from mcp_servers.jazidas.queries.geo import normalize_geojson_geometry

    geometry = normalize_geojson_geometry(source.get("poligono"))
    if geometry is None:
        return None

    # Novo ETL usa "centroide"; legado usa "localizacao"
    loc = source.get("centroide") or source.get("localizacao") or {}

    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "camada": "municipio",
            "nome": source.get("nome", ""),
            # Novo ETL usa "uf"; legado usa "siglaUF"
            "uf": source.get("uf") or source.get("siglaUF", ""),
            # Novo ETL usa "uf_nome"; legado usa "nomeUF"
            "nome_uf": source.get("uf_nome") or source.get("nomeUF", ""),
            # Novo ETL usa "codigo_ibge"; legado usa "idMunicipio"
            "id_ibge": source.get("codigo_ibge") or source.get("idMunicipio"),
            "centro": {"lat": loc.get("lat"), "lon": loc.get("lon")} if loc else None,
        },
    }


# ==================== Extract Pairs from ANM Hits ====================


def extract_municipio_uf_pairs(hits: list[dict]) -> list[tuple[str, str]]:
    """
    Extract unique (nome_municipio, sigla_uf) pairs from anm_v003 hits.

    The fields ``nomesMunicipios`` and ``siglasUF`` in anm_v003 are flat
    arrays at root level. Each process can span multiple municipalities.

    Strategy:
        - If both nomesMunicipios and siglasUF have 1 element → direct pair
        - If nomesMunicipios has N and siglasUF has 1 → all in same UF
        - Otherwise → pair each municipio with each UF (cross product,
          the query itself will filter non-existent combinations)

    Returns:
        List of unique (nome, uf) tuples
    """
    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str]] = []

    for hit in hits:
        source = hit.get("_source", {})
        nomes = _ensure_list(source.get("nomesMunicipios"))
        ufs = _ensure_list(source.get("siglasUF"))

        hit_pairs = _build_pairs_for_hit(nomes, ufs)
        for pair in hit_pairs:
            if pair not in seen:
                seen.add(pair)
                pairs.append(pair)

    return pairs[:MAX_MUNICIPIOS_BATCH]


def _build_pairs_for_hit(
    nomes: list[str], ufs: list[str]
) -> list[tuple[str, str]]:
    """Build (nome, uf) pairs for a single ANM hit."""
    if not nomes or not ufs:
        return []

    # Most common: single municipality, single UF
    if len(ufs) == 1:
        return [(n, ufs[0]) for n in nomes if n]

    # Same count → zip
    if len(nomes) == len(ufs):
        return [(n, u) for n, u in zip(nomes, ufs) if n and u]

    # Fallback: cross product (rare, but safe)
    return [(n, u) for n in nomes for u in ufs if n and u]


# ==================== Helpers ====================


def _ensure_list(val: Any) -> list:
    """Ensure a value is a list."""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]
