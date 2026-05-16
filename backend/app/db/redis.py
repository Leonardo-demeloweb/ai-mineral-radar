"""
Redis Connection
================

Async Redis client for caching, sessions, and memory.
"""

import redis.asyncio as redis
from redis.asyncio import Redis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class RedisClient:
    """
    Redis client wrapper with connection lifecycle management.
    
    Supports:
    - Session caching
    - Short-term memory (conversations)
    - Long-term memory (user preferences)
    - Rate limiting storage
    - Pub/Sub events
    
    Usage:
        # At startup
        await redis_client.connect()
        
        # Use client
        await redis_client.client.set("key", "value")
        value = await redis_client.client.get("key")
        
        # At shutdown
        await redis_client.disconnect()
    """

    def __init__(self):
        self.client: Redis | None = None

    async def connect(self) -> None:
        """Establish connection to Redis."""
        try:
            # Use direct connection params for Redis 7+ compatibility
            self.client = redis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                username=settings.redis_username,  # Required for Redis 7+
                password=settings.redis_password or None,
                db=settings.redis_db,
                ssl=settings.redis_ssl,
                encoding="utf-8",
                decode_responses=True,
            )
            
            # Verify connection
            await self.client.ping()
            
            # Get server info
            info = await self.client.info("server")
            redis_version = info.get("redis_version", "unknown")
            
            logger.info(
                "Redis connected",
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                version=redis_version,
            )
        except Exception as e:
            logger.error("Redis connection failed", error=str(e))
            # Redis is optional - don't raise, just log warning
            self.client = None
            logger.warning("Running without Redis - caching disabled")

    async def disconnect(self) -> None:
        """Close Redis connection."""
        if self.client:
            await self.client.close()
            self.client = None
            logger.info("Redis disconnected")

    # ==================== Convenience Methods ====================

    async def get(self, key: str) -> str | None:
        """Get a value by key."""
        if not self.client:
            return None
        return await self.client.get(key)

    async def set(
        self, 
        key: str, 
        value: str, 
        ex: int | None = None,
        px: int | None = None,
    ) -> bool:
        """
        Set a value with optional expiration.
        
        Args:
            key: Cache key
            value: Value to store
            ex: Expiration in seconds
            px: Expiration in milliseconds
        """
        if not self.client:
            return False
        await self.client.set(key, value, ex=ex, px=px)
        return True

    async def delete(self, *keys: str) -> int:
        """Delete one or more keys."""
        if not self.client:
            return 0
        return await self.client.delete(*keys)

    async def exists(self, *keys: str) -> int:
        """Check if keys exist."""
        if not self.client:
            return 0
        return await self.client.exists(*keys)

    async def expire(self, key: str, seconds: int) -> bool:
        """Set expiration on a key."""
        if not self.client:
            return False
        return await self.client.expire(key, seconds)

    # ==================== Hash Operations (for structured data) ====================

    async def hget(self, name: str, key: str) -> str | None:
        """Get a hash field."""
        if not self.client:
            return None
        return await self.client.hget(name, key)

    async def hset(self, name: str, key: str, value: str) -> int:
        """Set a hash field."""
        if not self.client:
            return 0
        return await self.client.hset(name, key, value)

    async def hgetall(self, name: str) -> dict:
        """Get all fields in a hash."""
        if not self.client:
            return {}
        return await self.client.hgetall(name)

    # ==================== List Operations (for queues/history) ====================

    async def lpush(self, name: str, *values: str) -> int:
        """Push values to the head of a list."""
        if not self.client:
            return 0
        return await self.client.lpush(name, *values)

    async def rpush(self, name: str, *values: str) -> int:
        """Push values to the tail of a list."""
        if not self.client:
            return 0
        return await self.client.rpush(name, *values)

    async def lrange(self, name: str, start: int, end: int) -> list:
        """Get a range of elements from a list."""
        if not self.client:
            return []
        return await self.client.lrange(name, start, end)

    async def ltrim(self, name: str, start: int, end: int) -> bool:
        """Trim a list to the specified range."""
        if not self.client:
            return False
        return await self.client.ltrim(name, start, end)


# Singleton instance
redis_client = RedisClient()


async def get_redis_client() -> RedisClient:
    """FastAPI dependency to get Redis client."""
    return redis_client
