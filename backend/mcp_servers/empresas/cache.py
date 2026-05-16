"""
Empresas Cache Helpers
=======================

Specialized cache patterns for the Empresas MCP Server.
Built on top of RedisCache (mcp_servers.common.redis_cache).

Patterns:
    - Paginated search results (buscar_empresas, buscar_por_socio)
    - Company detail cache (detalhes_empresa)
    - Cache key generation with parameter hashing

The pagination strategy:
    1. First request: execute full query, store ALL results in Redis
    2. Subsequent "next page" requests: read from Redis, zero OpenSearch calls
    3. TTL: 1h for searches, 24h for company details
"""

import json
import hashlib
import logging
import math
from typing import Any

from mcp_servers.common.redis_cache import RedisCache
from mcp_servers.common.config import mcp_settings
from mcp_servers.common.schemas import SearchResultMeta

logger = logging.getLogger("mcp.empresas.cache")


class EmpresasCache:
    """
    Cache helpers for Empresas-specific data patterns.

    Usage:
        cache = EmpresasCache(redis_cache)

        # Store full search results
        cache_id = await cache.store_search("search", params, all_results)

        # Get a page from cached results
        page_data, meta = await cache.get_page(cache_id, pagina=2, por_pagina=10)
    """

    # Cache key prefixes
    PREFIX_SEARCH = "empresas:search"
    PREFIX_DETALHE = "empresas:detalhe"
    PREFIX_SOCIO = "empresas:socio"

    def __init__(self, redis_cache: RedisCache):
        self.redis = redis_cache

    # ==================================================================
    # SEARCH RESULTS (paginated)
    # ==================================================================

    @staticmethod
    def _hash_params(params: dict) -> str:
        """Generate a short hash from search parameters."""
        content = json.dumps(params, sort_keys=True, default=str)
        return hashlib.sha256(content.encode()).hexdigest()[:12]

    def _search_key(self, prefix: str, params: dict) -> str:
        """Generate cache key for a search query."""
        return f"{prefix}:{self._hash_params(params)}"

    async def store_search(
        self,
        prefix: str,
        params: dict,
        results: list[dict],
        ttl: int | None = None,
    ) -> str:
        """
        Store full search results in Redis for pagination.

        Args:
            prefix: Cache key prefix (e.g., PREFIX_SEARCH)
            params: Search parameters (used for key generation)
            results: Complete list of result dicts
            ttl: Time-to-live in seconds (default: cache_search_ttl from config)

        Returns:
            cache_id: The Redis key for retrieving pages
        """
        cache_id = self._search_key(prefix, params)
        ttl = ttl or mcp_settings.cache_search_ttl

        payload = json.dumps(
            {"total": len(results), "dados": results},
            default=str,
            ensure_ascii=False,
        )

        stored = await self.redis.set(cache_id, payload, ttl=ttl)
        if stored:
            logger.debug(f"Cached {len(results)} results → {cache_id} (TTL={ttl}s)")
        else:
            logger.warning(f"Failed to cache results → {cache_id}")

        return cache_id

    async def get_page(
        self,
        cache_id: str,
        pagina: int = 1,
        por_pagina: int = 10,
    ) -> tuple[list[dict], SearchResultMeta] | None:
        """
        Retrieve a page from cached search results.

        Args:
            cache_id: Redis key returned by store_search
            pagina: Page number (1-based)
            por_pagina: Items per page

        Returns:
            Tuple of (page_items, meta) or None if not cached
        """
        raw = await self.redis.get(cache_id)
        if not raw:
            return None

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in cache key {cache_id}")
            return None

        total = data.get("total", 0)
        all_results = data.get("dados", [])

        # Calculate pagination
        por_pagina = min(por_pagina, mcp_settings.max_page_size)
        total_paginas = max(1, math.ceil(total / por_pagina))
        pagina = min(pagina, total_paginas)

        offset = (pagina - 1) * por_pagina
        page_items = all_results[offset : offset + por_pagina]

        meta = SearchResultMeta(
            total=total,
            pagina=pagina,
            por_pagina=por_pagina,
            total_paginas=total_paginas,
        )

        logger.debug(
            f"Cache page {pagina}/{total_paginas} from {cache_id} "
            f"({len(page_items)} items)"
        )

        return page_items, meta

    async def get_or_none(self, cache_id: str) -> list[dict] | None:
        """
        Retrieve ALL cached results (no pagination).

        Args:
            cache_id: Redis key

        Returns:
            List of all result dicts or None if not cached
        """
        raw = await self.redis.get(cache_id)
        if not raw:
            return None

        try:
            data = json.loads(raw)
            return data.get("dados", [])
        except json.JSONDecodeError:
            return None

    # ==================================================================
    # COMPANY DETAILS (single document cache)
    # ==================================================================

    def _empresa_key(self, cnpj_basico: str) -> str:
        """Generate cache key for a specific company."""
        normalized = cnpj_basico.strip().replace(".", "").replace("/", "").replace("-", "")
        return f"{self.PREFIX_DETALHE}:{normalized}"

    async def get_empresa(self, cnpj_basico: str) -> dict | None:
        """
        Retrieve cached company details.

        Args:
            cnpj_basico: CNPJ básico (8 dígitos)

        Returns:
            Company data dict or None if not cached
        """
        key = self._empresa_key(cnpj_basico)
        raw = await self.redis.get(key)
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return None
        return None

    async def store_empresa(self, cnpj_basico: str, data: dict) -> bool:
        """
        Cache company details.

        Args:
            cnpj_basico: CNPJ básico (8 dígitos)
            data: Complete company data dict

        Returns:
            True if cached successfully
        """
        key = self._empresa_key(cnpj_basico)
        payload = json.dumps(data, default=str, ensure_ascii=False)
        ttl = mcp_settings.cache_empresa_ttl  # 24h
        stored = await self.redis.set(key, payload, ttl=ttl)
        if stored:
            logger.debug(f"Cached empresa {cnpj_basico} → {key}")
        return stored

    # ==================================================================
    # UTILITIES
    # ==================================================================

    async def store_mapa(self, cache_id: str, mapa: dict, ttl: int | None = None) -> None:
        """Cache mapa data alongside search results (key = {cache_id}:mapa)."""
        ttl = ttl or mcp_settings.cache_search_ttl
        payload = json.dumps(mapa, default=str, ensure_ascii=False)
        stored = await self.redis.set(f"{cache_id}:mapa", payload, ttl=ttl)
        if not stored:
            logger.warning(f"Failed to cache mapa → {cache_id}:mapa")

    async def get_mapa(self, cache_id: str) -> dict | None:
        """Retrieve cached mapa data for a search, or None."""
        raw = await self.redis.get(f"{cache_id}:mapa")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def invalidate_search(self, prefix: str, params: dict) -> bool:
        """Invalidate a specific cached search."""
        key = self._search_key(prefix, params)
        return await self.redis.delete(key)

    async def invalidate_empresa(self, cnpj_basico: str) -> bool:
        """Invalidate a specific cached company."""
        key = self._empresa_key(cnpj_basico)
        return await self.redis.delete(key)

    def build_paginated_response(
        self,
        page_items: list[dict],
        meta: SearchResultMeta,
        cache_id: str,
    ) -> dict:
        """
        Build a standardized paginated response dict.

        Args:
            page_items: Items for the current page
            meta: Pagination metadata
            cache_id: Cache key for subsequent page requests

        Returns:
            Standardized response dict with: sucesso, meta, dados, cache_id.
            Caller adds extra fields (mapa, resolucao, etc.) as needed.
        """
        return {
            "sucesso": True,
            "meta": meta.model_dump(),
            "dados": page_items,
            "cache_id": cache_id,
        }
