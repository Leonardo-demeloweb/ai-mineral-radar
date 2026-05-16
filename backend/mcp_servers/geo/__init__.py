"""
MCP Server: Geo
=================

Geospatial tools for municipality lookup, routing, and geocoding.

Architecture:
    - 7 custom tools (4 OpenSearch + 3 Azure Maps)
    - QPT handles ~20% of scenarios (simple lookups by code/name/UF)
    - Uses Streamable HTTP for MCP communication

Data Sources:
    - ibge_municipio_v001 (OpenSearch): 5.631 docs — polygons, geo_points,
      IBGE codes for all Brazilian municipalities
    - Azure Maps REST APIs: Route Directions (truck), Route Range (isochrone),
      Search Fuzzy (geocoding), Search Reverse

Tools:
    1. buscar_municipio          — lookup by name/code/UF
    2. municipio_por_coordenada  — geo_shape contains (point-in-polygon)
    3. obter_poligono            — GeoJSON Feature export
    4. municipios_em_raio        — geo_distance + distance sort
    5. calcular_rota             — Azure Maps Route Directions (truck/car)
    6. calcular_isocrona         — Azure Maps Route Range (reachable area)
    7. geocodificar              — Azure Maps Search (forward + reverse)
"""
