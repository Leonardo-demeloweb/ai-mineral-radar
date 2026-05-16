"""
MongoDB Geocode Cache (L2)
===========================

Persistent second-level cache for Azure Maps geocoding results.
Complements the Redis L1 cache (7-day TTL) with a long-lived MongoDB
collection that survives Redis evictions and restarts.

Collection: ``geocode_cache``
    _id          — sha256[:16] hash of normalized address (same key as Redis)
    address      — original address string (lowercase, stripped)
    lat, lon     — geocoded coordinates
    source       — always "azure_maps" for now
    created_at   — first time this address was geocoded
    last_seen_at — updated on every cache hit (useful for TTL index)
    hits         — counter incremented on every read

Usage from geocoder.py:
    cache = await get_mongo_geocache()
    coords = await cache.get("cd60a030bea347c6")
    await cache.set("cd60a030bea347c6", "rua x, bh, mg", -19.9, -43.9)
"""

import logging
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection

from mcp_servers.common.config import mcp_settings

logger = logging.getLogger("mcp.common.mongo_geocache")

COLLECTION_NAME = "geocode_cache"

_client: AsyncIOMotorClient | None = None
_collection: AsyncIOMotorCollection | None = None


async def get_mongo_geocache() -> "MongoGeocache":
    """Lazy singleton — creates the client and index on first call."""
    global _client, _collection

    if _collection is not None:
        return MongoGeocache(_collection)

    conn_str = mcp_settings.mongodb_connection_string
    db_name = mcp_settings.mongodb_database

    _client = AsyncIOMotorClient(conn_str, serverSelectionTimeoutMS=5000)
    db = _client[db_name]
    _collection = db[COLLECTION_NAME]

    await _collection.create_index("last_seen_at", expireAfterSeconds=63_072_000)
    logger.info("MongoGeocache connected — collection=%s, db=%s", COLLECTION_NAME, db_name)

    return MongoGeocache(_collection)


class MongoGeocache:
    """Thin async wrapper around the geocode_cache collection."""

    def __init__(self, collection: AsyncIOMotorCollection):
        self._col = collection

    async def get(self, cache_key: str) -> tuple[float, float] | None:
        """Fetch coords by cache_key (_id). Returns (lat, lon) or None."""
        doc = await self._col.find_one_and_update(
            {"_id": cache_key},
            {
                "$set": {"last_seen_at": datetime.now(timezone.utc)},
                "$inc": {"hits": 1},
            },
        )
        if doc and doc.get("lat") is not None and doc.get("lon") is not None:
            return (doc["lat"], doc["lon"])
        return None

    async def get_many(self, cache_keys: list[str]) -> dict[str, tuple[float, float]]:
        """Batch fetch. Returns {cache_key: (lat, lon)} for found entries."""
        if not cache_keys:
            return {}
        results: dict[str, tuple[float, float]] = {}
        now = datetime.now(timezone.utc)
        cursor = self._col.find({"_id": {"$in": cache_keys}})
        async for doc in cursor:
            if doc.get("lat") is not None and doc.get("lon") is not None:
                results[doc["_id"]] = (doc["lat"], doc["lon"])
        if results:
            await self._col.update_many(
                {"_id": {"$in": list(results.keys())}},
                {"$set": {"last_seen_at": now}, "$inc": {"hits": 1}},
            )
        return results

    async def set(
        self,
        cache_key: str,
        address: str,
        lat: float,
        lon: float,
        source: str = "azure_maps",
    ) -> None:
        """Upsert a geocoding result."""
        now = datetime.now(timezone.utc)
        await self._col.update_one(
            {"_id": cache_key},
            {
                "$set": {
                    "address": address.strip().lower(),
                    "lat": lat,
                    "lon": lon,
                    "source": source,
                    "last_seen_at": now,
                },
                "$setOnInsert": {"created_at": now, "hits": 0},
            },
            upsert=True,
        )

    async def set_many(
        self,
        entries: list[dict],
        source: str = "azure_maps",
    ) -> int:
        """
        Bulk upsert geocoding results.

        entries: list of {"key": str, "address": str, "lat": float, "lon": float}
        Returns number of upserted/updated docs.
        """
        if not entries:
            return 0
        from pymongo import UpdateOne

        now = datetime.now(timezone.utc)
        ops = [
            UpdateOne(
                {"_id": e["key"]},
                {
                    "$set": {
                        "address": e["address"].strip().lower(),
                        "lat": e["lat"],
                        "lon": e["lon"],
                        "source": source,
                        "last_seen_at": now,
                    },
                    "$setOnInsert": {"created_at": now, "hits": 0},
                },
                upsert=True,
            )
            for e in entries
        ]
        result = await self._col.bulk_write(ops, ordered=False)
        return result.upserted_count + result.modified_count
