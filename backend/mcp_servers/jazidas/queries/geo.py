"""
Geo Extraction Module
======================

Shared utilities for extracting geographic data from search results.

Used by:
    - buscar_fornecedores (tool 1)
    - buscar_jazidas (tool 2)
    - jazidas_por_poligono (tool 4)

Three map layers:
    1. **Pontos** (lightweight): centroid points for map overview/pins
    2. **Geometrias jazidas** (full): GeoJSON FeatureCollection with mining polygons
    3. **Geometrias municípios** (context): GeoJSON FeatureCollection with municipality boundaries

Data sources:
    - anm_v003:
        shapes[].localizacao  (geo_point — centroid of each polygon)
        shapes[].poligono     (geo_shape — mining concession polygon)
        shapes[].substancia   (object — substance info per polygon)
        shapes[].areaHa       (double — area in hectares)
        shapes[].ativa        (boolean — is this polygon active?)
        shapes[].titular      (text — titleholder per polygon)
    - ibge_municipio_v001:
        poligono              (geo_shape — municipality boundary)
        nome, siglaUF, localizacao, idMunicipio

Performance note:
    - Pontos: ~100 bytes per process (fast, always returned)
    - Geometrias jazidas: ~5-50 KB per polygon (heavy, optional)
    - Geometrias municípios: ~2-200 KB per município (heavy, optional)
"""

import logging
from typing import Any

from mcp_servers.common.opensearch_client import OpenSearchService

logger = logging.getLogger("mcp.jazidas.geo")


# ==================== _source Fields ====================

# Minimal: centroid only (mr_jazidas_v001 uses root "location")
SHAPES_SOURCE_MINIMAL: list[str] = []  # location already in SOURCE_FIELDS

# Full: includes polygon geometry (mr_jazidas_v001 uses root "geom")
SHAPES_SOURCE_FULL = ["geom"]


# ==================== Pontos (lightweight) ====================


def extract_mapa_pontos(hits: list[dict]) -> list[dict]:
    """
    Extract map points from mr_jazidas_v001 hits.

    Supports both new schema (location/numero_processo/substancias_desc/fase)
    and legacy schema (localizacao/dsProcesso/nmSubstancias/faseProcesso).
    """
    pontos: list[dict] = []

    for hit in hits:
        source = hit.get("_source", {})

        # New schema (mr_jazidas_v001)
        processo = source.get("numero_processo") or source.get("dsProcesso", "")
        substancias = _ensure_list(
            source.get("substancias_desc") or source.get("nmSubstancias")
        )
        fase = source.get("fase") or (source.get("faseProcesso") or {}).get("dsFaseProcesso")

        # New schema uses "location", legacy uses "localizacao"
        loc = source.get("location") or source.get("localizacao")
        if loc and isinstance(loc, dict) and loc.get("lat") and loc.get("lon"):
            pontos.append({
                "lat": loc["lat"],
                "lon": loc["lon"],
                "processo": processo,
                "substancia": substancias[0] if substancias else None,
                "fase": fase,
                "tipo": "centroide",
            })

    return pontos


def extract_pontos_shapes(hits: list[dict]) -> list[dict]:
    """
    Extract per-polygon centroid points from shapes nested data.

    Each process can have N shapes (polygons), each with its own centroid.
    Useful for showing individual mining areas on the map.

    Returns:
        List of point dicts with shape-specific info
    """
    pontos: list[dict] = []

    for hit in hits:
        source = hit.get("_source", {})
        ds_processo = source.get("dsProcesso", "")

        for shape in source.get("shapes", []):
            loc = shape.get("localizacao")
            if not loc or not isinstance(loc, dict):
                continue
            if not loc.get("lat") or not loc.get("lon"):
                continue

            sub = shape.get("substancia") or {}
            pontos.append(
                {
                    "lat": loc["lat"],
                    "lon": loc["lon"],
                    "processo": ds_processo,
                    "substancia": sub.get("nmSubstancia"),
                    "area_ha": shape.get("areaHa"),
                    "ativa": shape.get("ativa"),
                    "tipo": "poligono",
                }
            )

    return pontos


# ==================== GeoJSON (full geometry) ====================


def extract_geojson_features(hits: list[dict]) -> list[dict]:
    """
    Extract GeoJSON Feature objects from shapes nested data.

    Each shape with a ``poligono`` field becomes a GeoJSON Feature
    with properties like dsProcesso, substancia, areaHa, etc.

    Returns:
        List of GeoJSON Feature dicts, ready for FeatureCollection wrapping.

    Example output::

        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[lon, lat], ...]]
            },
            "properties": {
                "processo": "820.517/2013",
                "substancia": "AREIA",
                "area_ha": 50.0,
                "ativa": true,
                "titular": "EMPRESA X LTDA"
            }
        }
    """
    features: list[dict] = []

    for hit in hits:
        source = hit.get("_source", {})

        # New schema (mr_jazidas_v001): root-level geom + processo fields
        geom = source.get("geom")
        if geom:
            processo = source.get("numero_processo") or source.get("dsProcesso", "")
            substancias = source.get("substancias_desc") or []
            titular = source.get("titular") or {}
            geometry = normalize_geojson_geometry(geom)
            if geometry:
                # Strip 3D Z coordinates — MapLibre/frontend only handles 2D
                geometry = _strip_z_coords(geometry)
                features.append({
                    "type": "Feature",
                    "geometry": geometry,
                    "properties": {
                        "processo": processo,
                        "substancia": substancias[0] if substancias else None,
                        "substancias": substancias,
                        "area_ha": source.get("area_ha"),
                        "fase": source.get("fase"),
                        "ativa": source.get("ativo", True),
                        "titular": titular.get("nome") or titular.get("razao_social"),
                        "uf": source.get("uf"),
                    },
                })
            continue

        # Legacy schema (anm_v003): nested shapes[]
        ds_processo = source.get("dsProcesso", "")
        for shape in source.get("shapes", []):
            feature = _shape_to_geojson_feature(shape, ds_processo)
            if feature:
                features.append(feature)

    return features


def _strip_z_coords(geometry: dict) -> dict:
    """Remove Z (altitude) from coordinates — ANM SIGMINE uses Measured 3D."""
    def _strip(coords: Any) -> Any:
        if not coords:
            return coords
        if isinstance(coords[0], (int, float)):
            return coords[:2]  # [lon, lat, z?] → [lon, lat]
        return [_strip(c) for c in coords]

    if "coordinates" not in geometry:
        return geometry
    return {**geometry, "coordinates": _strip(geometry["coordinates"])}


def build_feature_collection(features: list[dict]) -> dict:
    """
    Wrap a list of GeoJSON Features into a FeatureCollection.

    Returns:
        GeoJSON FeatureCollection dict
    """
    return {
        "type": "FeatureCollection",
        "features": features,
    }


_GEOJSON_TYPE_NORMALIZE: dict[str, str] = {
    "polygon": "Polygon",
    "multipolygon": "MultiPolygon",
    "point": "Point",
    "multipoint": "MultiPoint",
    "linestring": "LineString",
    "multilinestring": "MultiLineString",
    "geometrycollection": "GeometryCollection",
}


def normalize_geojson_geometry(geometry: Any) -> dict | None:
    """
    Normalize an OpenSearch geo_shape dict to valid GeoJSON.

    OpenSearch may return geometry type names in lowercase (e.g. "polygon")
    which are rejected by MapLibre and other strict GeoJSON consumers.
    This function uppercases the type field to conform to the GeoJSON spec.

    Returns None if the input is not a valid geometry dict.
    """
    if not isinstance(geometry, dict) or "coordinates" not in geometry:
        return None
    raw_type = geometry.get("type", "")
    normalized = _GEOJSON_TYPE_NORMALIZE.get(raw_type.lower(), raw_type)
    if normalized == raw_type:
        return geometry
    return {**geometry, "type": normalized}


def _shape_to_geojson_feature(shape: dict, ds_processo: str) -> dict | None:
    """
    Convert a single anm_v003 shape object to a GeoJSON Feature.

    Returns None if the shape has no polygon geometry.
    """
    poligono = shape.get("poligono")
    if not poligono:
        return None

    # OpenSearch geo_shape may return lowercase type (e.g. "polygon") — normalize.
    geometry = normalize_geojson_geometry(poligono)
    if geometry is None:
        return None

    sub = shape.get("substancia") or {}

    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "processo": ds_processo,
            "shape_id": shape.get("id"),
            "substancia": sub.get("nmSubstancia"),
            "area_ha": shape.get("areaHa"),
            "ativa": shape.get("ativa"),
            "titular": shape.get("titular"),
        },
    }


# ==================== Combined Output ====================


def build_mapa_response(
    hits: list[dict],
    page_hits: list[dict] | None = None,
    incluir_geometria: bool = False,
    municipios_features: list[dict] | None = None,
) -> dict[str, Any]:
    """
    Build complete map response with points + optional jazida/município geometry.

    Args:
        hits: ALL search result hits (for overview points)
        page_hits: Current page hits only (for detailed geometry) —
                   if None, uses all hits
        incluir_geometria: Whether to include polygon GeoJSON (jazidas + municípios)
        municipios_features: Pre-fetched municipality GeoJSON Features
                             (from queries/municipio.py)

    Returns:
        {
            "pontos": [...],              # Centroid points for all results
            "total_pontos": int,
            "geometrias_jazidas": {...},   # GeoJSON FeatureCollection (mining polygons)
            "total_geometrias_jazidas": int,
            "geometrias_municipios": {...}, # GeoJSON FeatureCollection (boundaries)
            "total_geometrias_municipios": int,
        }
    """
    # Points for ALL results (lightweight overview)
    pontos = extract_mapa_pontos(hits)

    response: dict[str, Any] = {
        "pontos": pontos,
        "total_pontos": len(pontos),
    }

    # Polygon geometries (if requested)
    if incluir_geometria:
        # Jazidas (mining concession polygons) — current page only
        target_hits = page_hits if page_hits is not None else hits
        jazidas_features = extract_geojson_features(target_hits)
        response["geometrias_jazidas"] = build_feature_collection(jazidas_features)
        response["total_geometrias_jazidas"] = len(jazidas_features)

        # Municípios (boundary overlays)
        mun_features = municipios_features or []
        response["geometrias_municipios"] = build_feature_collection(mun_features)
        response["total_geometrias_municipios"] = len(mun_features)

    return response


async def build_mapa_response_with_municipios(
    os_service: OpenSearchService,
    hits: list[dict],
    page_hits: list[dict] | None = None,
    incluir_geometria: bool = False,
) -> dict[str, Any]:
    """
    Build complete map response — fetches municipality boundaries automatically.

    Convenience wrapper that extracts município/UF pairs from the ANM hits,
    fetches their boundary polygons from ``ibge_municipio_v001``, and
    includes them in the map response.

    Args:
        os_service: OpenSearch async client (for municipality lookup)
        hits: ALL search result hits
        page_hits: Current page hits only
        incluir_geometria: Whether to include polygon geometry

    Returns:
        Same as build_mapa_response, with municipios populated
    """
    municipios_features: list[dict] | None = None

    if incluir_geometria and hits:
        # Lazy import to avoid circular dependencies
        from mcp_servers.jazidas.queries.municipio import (
            extract_municipio_uf_pairs,
            fetch_municipios_precise,
        )

        pairs = extract_municipio_uf_pairs(hits)
        if pairs:
            municipios_features = await fetch_municipios_precise(os_service, pairs)
            logger.info(
                f"Fetched {len(municipios_features)} municipality boundaries "
                f"from {len(pairs)} unique pairs"
            )

    return build_mapa_response(
        hits=hits,
        page_hits=page_hits,
        incluir_geometria=incluir_geometria,
        municipios_features=municipios_features,
    )


# ==================== Helpers ====================


def _ensure_list(val: Any) -> list:
    """Ensure a value is a list."""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]
