"""
Helpers para construção de filtros geoespaciais no OpenSearch.

Usado por buscas que aceitam um polígono GeoJSON (Polygon / MultiPolygon)
para filtrar documentos com campo ``geo_point`` (ex.: ``localizacao`` em
``rfb_cnpj_v003`` e ``anm_v003``).

Estratégia de filtragem por polígono — two-stage
-------------------------------------------------
1. ``geo_bounding_box`` no OpenSearch (Estágio 1 — broad filter)
   Filtra pelo retângulo delimitador do polígono. Suportado em TODAS as
   versões do OpenSearch/ES sobre ``geo_point``, zero falso-negativo.
   Pode devolver documentos nos "cantos" do bounding box que estão FORA
   do polígono real (falso-positivo).

2. Ray-casting PIP em Python (Estágio 2 — exact filter)
   ``filter_hits_by_polygon`` aplica o algoritmo ponto-no-polígono sobre
   os hits retornados pelo Estágio 1, eliminando os falsos-positivos.
   Roda em memória sobre ≤ MAX_RESULTS=200 hits — O(N·P) com N≈200,
   P≈400 vértices ≈ 80 k operações < 1 ms por chamada.

Por que não usar outras abordagens:
  - ``geo_polygon``: deprecated + limite de 1024 pts (isócronas Azure Maps
    têm 200-600 vértices; falha silenciosamente quando excede).
  - ``geo_shape/within``: só funciona em campos ``geo_shape``, não ``geo_point``.

Centralizar aqui evita duplicação entre ``mcp_servers/empresas`` e
``mcp_servers/jazidas`` e garante semântica consistente.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("mcp.geo_filters")


def geojson_to_geo_polygon_clauses(
    geometry: dict[str, Any],
    field: str = "localizacao",
) -> list[dict[str, Any]]:
    """
    Converte um GeoJSON Polygon/MultiPolygon em cláusulas ``geo_bounding_box``
    (uma por sub-polígono) prontas para usar em ``query.bool.filter``.

    Para ``geo_point`` fields, usamos ``geo_bounding_box`` (retângulo
    delimitador do polígono) porque:
      • Funciona em todas as versões do OpenSearch/ES — sem deprecated
      • Sem limite de vértices (``geo_polygon`` limita a 1024)
      • ``geo_shape/within`` só funciona em campos ``geo_shape``, não ``geo_point``
      • Garante zero falso-negativo: todo ponto dentro do polígono está
        dentro do bounding box; só pode sobre-selecionar levemente nas quinas

    Para MultiPolygon, retorna N bounding boxes (um por sub-polígono); o
    chamador normalmente agrupa em ``bool.should`` para semantica de OR.

    Args:
        geometry: dicionário GeoJSON com ``type`` em (Polygon, MultiPolygon)
                  e ``coordinates`` no formato GeoJSON ([lon, lat] pairs).
        field: Nome do campo geo_point indexado a filtrar.

    Returns:
        Lista de cláusulas ``geo_bounding_box`` (1+ elementos).

    Raises:
        ValueError: ``geometry`` inválido ou sem coordenadas válidas.
    """
    if not isinstance(geometry, dict):
        raise ValueError("geometry deve ser um dict GeoJSON")

    geom_type = geometry.get("type")
    coords = geometry.get("coordinates")
    if geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError(
            f"Tipo GeoJSON não suportado: '{geom_type}'. "
            "Use Polygon ou MultiPolygon."
        )
    if not coords:
        raise ValueError("Campo 'coordinates' vazio ou ausente.")

    if geom_type == "Polygon":
        rings: list[list[list[float]]] = [coords[0]]
    else:
        rings = [poly[0] for poly in coords if poly]

    clauses: list[dict[str, Any]] = []
    for ring in rings:
        if not ring or len(ring) < 3:
            continue

        valid_pts = [
            pt for pt in ring
            if isinstance(pt, (list, tuple)) and len(pt) >= 2
        ]
        if len(valid_pts) < 3:
            continue

        lons = [float(pt[0]) for pt in valid_pts]
        lats = [float(pt[1]) for pt in valid_pts]

        clauses.append({
            "geo_bounding_box": {
                field: {
                    "top_left":     {"lat": max(lats), "lon": min(lons)},
                    "bottom_right": {"lat": min(lats), "lon": max(lons)},
                }
            }
        })

    if not clauses:
        raise ValueError("Polígono sem vértices válidos para geo_bounding_box.")
    return clauses


def geojson_to_geo_polygon_filter(
    geometry: dict[str, Any],
    field: str = "localizacao",
) -> dict[str, Any]:
    """
    Wrapper de conveniência: retorna UMA cláusula pronta para
    ``query.bool.filter[]``. Se for MultiPolygon, embrulha as N cláusulas
    em ``bool.should`` com ``minimum_should_match=1``.
    """
    clauses = geojson_to_geo_polygon_clauses(geometry, field=field)
    if len(clauses) == 1:
        return clauses[0]
    return {
        "bool": {
            "should": clauses,
            "minimum_should_match": 1,
        }
    }


# ── Estágio 2: ray-casting PIP (pós-filtro exato) ───────────────────────────


def _point_in_ring(lat: float, lon: float, ring: list) -> bool:
    """
    Ray-casting ponto-no-polígono sobre um anel GeoJSON ([lon, lat] pairs).

    Implementação pura Python sem dependências externas — suficientemente
    rápida para ≤ 200 hits × ≤ 600 vértices por chamada de ferramenta.
    """
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi = float(ring[i][0])  # lon
        yi = float(ring[i][1])  # lat
        xj = float(ring[j][0])
        yj = float(ring[j][1])
        # Evita divisão por zero com denominador ≈ 0
        dy = yj - yi
        if dy == 0.0:
            j = i
            continue
        if (yi > lat) != (yj > lat):
            x_intersect = xi + (xj - xi) * (lat - yi) / dy
            if lon < x_intersect:
                inside = not inside
        j = i
    return inside


def _point_in_geometry(lat: float, lon: float, geometry: dict) -> bool:
    """
    True se (lat, lon) está dentro de qualquer anel exterior do GeoJSON.

    Suporta Polygon e MultiPolygon. Para MultiPolygon, basta que o ponto
    esteja em um dos sub-polígonos (semântica OR).
    """
    geom_type = geometry.get("type")
    coords = geometry.get("coordinates", [])
    if geom_type == "Polygon":
        rings = [coords[0]] if coords else []
    elif geom_type == "MultiPolygon":
        rings = [poly[0] for poly in coords if poly]
    else:
        return True  # tipo desconhecido → não filtrar
    return any(_point_in_ring(lat, lon, ring) for ring in rings)


def filter_hits_by_polygon(
    hits: list[dict[str, Any]],
    geometry: dict[str, Any],
    localizacao_field: str = "localizacao",
) -> list[dict[str, Any]]:
    """
    Pós-filtra hits do OpenSearch pelo polígono exato (ray-casting PIP).

    Elimina os falsos-positivos gerados pelo ``geo_bounding_box`` do
    Estágio 1 (pontos nos "cantos" do retângulo delimitador que estão
    fora do polígono real da isócrona).

    Args:
        hits: lista de hits brutos do OpenSearch (``result["hits"]``).
        geometry: GeoJSON Polygon/MultiPolygon da isócrona ou área.
        localizacao_field: chave do campo ``geo_point`` em ``_source``.
            Deve ter formato ``{"lat": float, "lon": float}``.

    Returns:
        Sublista dos hits cujo ponto está dentro do polígono.
        Hits sem localização válida são DESCARTADOS (comportamento
        conservador — sem localização não podemos confirmar a inclusão).
    """
    if not hits:
        return hits

    filtered: list[dict[str, Any]] = []
    skipped_no_loc = 0
    for hit in hits:
        source = hit.get("_source", {})
        loc = source.get(localizacao_field)
        if not isinstance(loc, dict):
            skipped_no_loc += 1
            continue
        lat = loc.get("lat")
        lon = loc.get("lon")
        if lat is None or lon is None:
            skipped_no_loc += 1
            continue
        if _point_in_geometry(float(lat), float(lon), geometry):
            filtered.append(hit)

    logger.info(
        "filter_hits_by_polygon: %d/%d hits inside polygon "
        "(%d sem localização descartados)",
        len(filtered), len(hits), skipped_no_loc,
    )
    return filtered
