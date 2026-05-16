"""
Geo Cache Helpers
==================

Specialized cache patterns for the Geo MCP Server.
Built on top of RedisCache (mcp_servers.common.redis_cache).

Patterns:
    - Municipality lookups (buscar_municipio, municipio_por_coordenada)
    - Polygon cache (obter_poligono — heavy payload, long TTL)
    - Route/isochrone cache (calcular_rota, calcular_isocrona)
    - Geocoding cache (geocodificar)

Cache strategy:
    - OpenSearch tools (1-4): aggressive TTL (24h-7d) — IBGE data changes ~1x/year
    - Azure Maps tools (5-7): moderate TTL (1h-24h) — routes change with traffic
    - Polygons: 7d TTL — single polygon can be 2-200 KB, caching saves bandwidth
    - Coordinate-based keys: rounded to 6 decimal places (~0.1m precision)
"""

import json
import hashlib
import logging
from typing import Any

from mcp_servers.common.redis_cache import RedisCache
from mcp_servers.common.config import mcp_settings

logger = logging.getLogger("mcp.geo.cache")


class GeoCache:
    """
    Cache helpers for Geo-specific data patterns.

    Usage:
        cache = GeoCache(redis_cache)

        # Municipality by coordinate
        result = await cache.get_municipio_coord(lat, lon)

        # Store polygon (long TTL)
        await cache.store_poligono("3509502", geojson_feature)
    """

    PREFIX_MUNICIPIO = "geo:mun"
    PREFIX_COORD = "geo:coord"
    PREFIX_POLIGONO = "geo:poly"
    PREFIX_RAIO = "geo:raio"
    # v2: invalida rotas cacheadas com geocode desatualizado
    PREFIX_ROTA = "geo:rota:v2"
    PREFIX_ISOCRONA = "geo:iso"
    PREFIX_GEOCODE = "geo:gc"

    def __init__(self, redis_cache: RedisCache):
        self.redis = redis_cache

    # ==================================================================
    # HELPERS
    # ==================================================================

    @staticmethod
    def _hash_params(params: dict) -> str:
        """Generate a short hash from parameters."""
        content = json.dumps(params, sort_keys=True, default=str)
        return hashlib.sha256(content.encode()).hexdigest()[:12]

    @staticmethod
    def _round_coord(val: float, decimals: int = 4) -> str:
        """Round coordinate to N decimal places for cache key stability.

        4 decimals = ~11m precision — good enough for municipality identification.
        """
        return f"{val:.{decimals}f}"

    async def _get_json(self, key: str) -> dict | list | None:
        """Get and parse JSON from cache."""
        raw = await self.redis.get(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in cache key {key}")
            return None

    async def _set_json(self, key: str, data: Any, ttl: int) -> bool:
        """Serialize and store JSON in cache."""
        payload = json.dumps(data, default=str, ensure_ascii=False)
        stored = await self.redis.set(key, payload, ttl=ttl)
        if stored:
            logger.debug(f"Cached → {key} (TTL={ttl}s)")
        return stored

    # ==================================================================
    # MUNICIPALITY BY NAME/CODE (Tool 1)
    # ==================================================================

    async def get_municipio_busca(self, params: dict) -> dict | None:
        """Get cached municipality search results."""
        key = f"{self.PREFIX_MUNICIPIO}:{self._hash_params(params)}"
        return await self._get_json(key)

    async def store_municipio_busca(self, params: dict, data: dict) -> str:
        """Cache municipality search results. Returns cache key."""
        key = f"{self.PREFIX_MUNICIPIO}:{self._hash_params(params)}"
        await self._set_json(key, data, ttl=mcp_settings.cache_geo_municipio_ttl)
        return key

    # ==================================================================
    # MUNICIPALITY BY COORDINATE (Tool 2)
    # ==================================================================

    def _coord_key(self, lat: float, lon: float) -> str:
        return f"{self.PREFIX_COORD}:{self._round_coord(lat)}:{self._round_coord(lon)}"

    async def get_municipio_coord(self, lat: float, lon: float) -> dict | None:
        """Get cached municipality for a coordinate."""
        key = self._coord_key(lat, lon)
        return await self._get_json(key)

    async def store_municipio_coord(
        self, lat: float, lon: float, data: dict
    ) -> str:
        """Cache municipality identified by coordinate."""
        key = self._coord_key(lat, lon)
        await self._set_json(key, data, ttl=mcp_settings.cache_geo_municipio_ttl)
        return key

    # ==================================================================
    # POLYGON (Tool 3)
    # ==================================================================

    def _poligono_key(self, id_ibge: str) -> str:
        return f"{self.PREFIX_POLIGONO}:{id_ibge.strip()}"

    async def get_poligono(self, id_ibge: str) -> dict | None:
        """Get cached municipality polygon (GeoJSON Feature)."""
        key = self._poligono_key(id_ibge)
        return await self._get_json(key)

    async def store_poligono(self, id_ibge: str, feature: dict) -> str:
        """Cache municipality polygon with long TTL (7d)."""
        key = self._poligono_key(id_ibge)
        await self._set_json(key, feature, ttl=mcp_settings.cache_geo_poligono_ttl)
        return key

    # ==================================================================
    # MUNICIPALITIES IN RADIUS (Tool 4)
    # ==================================================================

    async def get_municipios_raio(self, params: dict) -> dict | None:
        """Get cached municipalities-in-radius results."""
        key = f"{self.PREFIX_RAIO}:{self._hash_params(params)}"
        return await self._get_json(key)

    async def store_municipios_raio(self, params: dict, data: dict) -> str:
        """Cache municipalities-in-radius results."""
        key = f"{self.PREFIX_RAIO}:{self._hash_params(params)}"
        await self._set_json(key, data, ttl=mcp_settings.cache_geo_rota_ttl)
        return key

    # ==================================================================
    # ROUTE (Tool 5)
    # ==================================================================

    async def get_rota(self, params: dict) -> dict | None:
        """Get cached route result."""
        key = f"{self.PREFIX_ROTA}:{self._hash_params(params)}"
        return await self._get_json(key)

    async def store_rota(self, params: dict, data: dict) -> str:
        """Cache route result."""
        key = f"{self.PREFIX_ROTA}:{self._hash_params(params)}"
        await self._set_json(key, data, ttl=mcp_settings.cache_geo_rota_ttl)
        return key

    # ==================================================================
    # ISOCHRONE (Tool 6)
    # ==================================================================

    async def get_isocrona(self, params: dict) -> dict | None:
        """Get cached isochrone result."""
        key = f"{self.PREFIX_ISOCRONA}:{self._hash_params(params)}"
        return await self._get_json(key)

    async def store_isocrona(self, params: dict, data: dict) -> str:
        """Cache isochrone result."""
        key = f"{self.PREFIX_ISOCRONA}:{self._hash_params(params)}"
        await self._set_json(key, data, ttl=mcp_settings.cache_geo_rota_ttl)
        return key

    # ==================================================================
    # GEOCODING (Tool 7)
    # ==================================================================

    async def get_geocode(self, query: str) -> dict | None:
        """Get cached geocoding result."""
        key = f"{self.PREFIX_GEOCODE}:{self._hash_params({'q': query})}"
        return await self._get_json(key)

    async def store_geocode(self, query: str, data: dict) -> str:
        """Cache geocoding result."""
        key = f"{self.PREFIX_GEOCODE}:{self._hash_params({'q': query})}"
        await self._set_json(key, data, ttl=mcp_settings.cache_geo_geocode_ttl)
        return key

    async def get_reverse_geocode(self, lat: float, lon: float) -> dict | None:
        """Get cached reverse geocoding result."""
        key = f"{self.PREFIX_GEOCODE}:rev:{self._round_coord(lat)}:{self._round_coord(lon)}"
        return await self._get_json(key)

    async def store_reverse_geocode(
        self, lat: float, lon: float, data: dict
    ) -> str:
        """Cache reverse geocoding result."""
        key = f"{self.PREFIX_GEOCODE}:rev:{self._round_coord(lat)}:{self._round_coord(lon)}"
        await self._set_json(key, data, ttl=mcp_settings.cache_geo_geocode_ttl)
        return key
