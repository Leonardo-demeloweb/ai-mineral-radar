"""
Redis Cache for MCP Servers
============================

Caching layer for embeddings and search results.
Graceful degradation — if Redis is unavailable, operations return None/False.
"""

import json
import hashlib
import logging
from typing import Any

import redis.asyncio as redis
from redis.asyncio import Redis

from mcp_servers.common.config import mcp_settings

logger = logging.getLogger("mcp.redis")


class RedisCache:
    """
    Redis cache with typed helpers for embeddings and search results.
    
    Key patterns:
        emb:{hash}       → cached embedding vector (JSON list of floats)
        search:{hash}    → cached search result (JSON)
        sub:{id}         → cached substance lookup
    """

    def __init__(self):
        self.client: Redis | None = None

    async def connect(self) -> None:
        """Establish connection to Redis."""
        try:
            self.client = redis.Redis(
                host=mcp_settings.redis_host,
                port=mcp_settings.redis_port,
                username=mcp_settings.redis_username,
                password=mcp_settings.redis_password or None,
                db=mcp_settings.redis_db,
                ssl=mcp_settings.redis_ssl,
                encoding="utf-8",
                decode_responses=True,
            )
            await self.client.ping()
            logger.info(f"Redis connected: {mcp_settings.redis_host}:{mcp_settings.redis_port}")
        except Exception as e:
            logger.warning(f"Redis unavailable ({e}) — caching disabled")
            self.client = None

    async def disconnect(self) -> None:
        """Close Redis connection."""
        if self.client:
            await self.client.close()
            self.client = None

    @property
    def available(self) -> bool:
        """Check if Redis is available."""
        return self.client is not None

    # ==================== Generic Operations ====================

    async def get(self, key: str) -> str | None:
        """Get a string value by key."""
        if not self.client:
            return None
        try:
            return await self.client.get(key)
        except Exception as e:
            logger.warning(f"Redis GET error: {e}")
            return None

    async def set(self, key: str, value: str, ttl: int | None = None) -> bool:
        """Set a string value with optional TTL (seconds)."""
        if not self.client:
            return False
        try:
            await self.client.set(key, value, ex=ttl)
            return True
        except Exception as e:
            logger.warning(f"Redis SET error: {e}")
            return False

    async def delete(self, key: str) -> bool:
        """Delete a key."""
        if not self.client:
            return False
        try:
            await self.client.delete(key)
            return True
        except Exception as e:
            logger.warning(f"Redis DEL error: {e}")
            return False

    # ==================== Embedding Cache ====================

    @staticmethod
    def _embedding_key(text: str, model: str) -> str:
        """Generate cache key for an embedding."""
        content = f"{model}:{text}"
        digest = hashlib.sha256(content.encode()).hexdigest()[:16]
        return f"emb:{digest}"

    async def get_embedding(self, text: str, model: str) -> list[float] | None:
        """Get a cached embedding vector."""
        key = self._embedding_key(text, model)
        raw = await self.get(key)
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return None
        return None

    async def set_embedding(self, text: str, model: str, vector: list[float]) -> bool:
        """Cache an embedding vector."""
        key = self._embedding_key(text, model)
        return await self.set(key, json.dumps(vector), ttl=mcp_settings.cache_embedding_ttl)

    # ==================== Search Result Cache ====================

    @staticmethod
    def _search_key(index: str, query_body: dict) -> str:
        """Generate cache key for a search query."""
        content = f"{index}:{json.dumps(query_body, sort_keys=True)}"
        digest = hashlib.sha256(content.encode()).hexdigest()[:16]
        return f"search:{digest}"

    async def get_search(self, index: str, query_body: dict) -> dict | None:
        """Get cached search results."""
        key = self._search_key(index, query_body)
        raw = await self.get(key)
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return None
        return None

    async def set_search(self, index: str, query_body: dict, result: dict) -> bool:
        """Cache search results."""
        key = self._search_key(index, query_body)
        return await self.set(key, json.dumps(result, default=str), ttl=mcp_settings.cache_search_ttl)

    # ==================== Substance Cache ====================

    async def get_substance_ids(self, termo: str) -> list[int] | None:
        """Get cached substance IDs for a search term."""
        key = f"sub:{termo.lower().strip()}"
        raw = await self.get(key)
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return None
        return None

    async def set_substance_ids(self, termo: str, ids: list[int]) -> bool:
        """Cache substance IDs for a search term."""
        key = f"sub:{termo.lower().strip()}"
        return await self.set(key, json.dumps(ids), ttl=mcp_settings.cache_substancia_ttl)


# Singleton
_redis_cache: RedisCache | None = None


async def get_redis_cache() -> RedisCache:
    """Get or create the Redis cache singleton."""
    global _redis_cache
    if _redis_cache is None:
        _redis_cache = RedisCache()
        await _redis_cache.connect()
    return _redis_cache
