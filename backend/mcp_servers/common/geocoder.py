"""
Address Geocoder
=================

Batch forward geocoding using Azure Maps Search Address API.
Converts street addresses into precise (lat, lon) coordinates.

Two-tier cache:
    L1  Redis   — 7-day TTL, sub-ms reads, acts as hot cache
    L2  MongoDB — ~2-year TTL (auto-purge via last_seen_at), survives
                  Redis evictions/restarts

Lookup order:  Redis → MongoDB (re-warms Redis on hit) → Azure Maps API

Features:
    - Per-address caching (Redis L1 + MongoDB L2) to minimize API calls
    - Concurrent requests with semaphore-based rate limiting
    - Graceful degradation when Azure Maps or MongoDB is unavailable
    - Shared across MCP servers (empresas, jazidas)
"""

import asyncio
import hashlib
import json
import logging
import ssl
from typing import Any

import aiohttp
import certifi
from mcp_servers.common.config import mcp_settings
from mcp_servers.common.redis_cache import get_redis_cache

logger = logging.getLogger("mcp.common.geocoder")

GEOCODE_CACHE_PREFIX = "geocode:addr"
GEOCODE_TTL = 86400 * 7  # 7 days — physical addresses rarely change

_session: aiohttp.ClientSession | None = None
MAX_CONCURRENT = 5


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        timeout = aiohttp.ClientTimeout(total=15, connect=5)
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        _session = aiohttp.ClientSession(timeout=timeout, connector=connector)
    return _session


def _cache_key(address: str) -> str:
    normalized = address.strip().lower()
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:16]
    return f"{GEOCODE_CACHE_PREFIX}:{digest}"


def _bare_key(address: str) -> str:
    """Hash-only key (no prefix) used as MongoDB _id."""
    normalized = address.strip().lower()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


# Minimum match-confidence score accepted from Azure Maps (0–1).
# Results below this threshold are discarded so we keep the original
# municipality centroid instead of a low-quality coordinate.
# Azure Maps levels: ~0.9+ = exact address, ~0.5–0.9 = street/postal,
# < 0.2 = country/continent level (useless). Keep the bar low (0.20) so
# partial matches (street name only, no number) still produce a better
# pin than the raw municipality centroid.
MIN_CONFIDENCE = 0.20


async def _geocode_one(address: str) -> tuple[float, float] | None:
    """
    Forward-geocode a single address via Azure Maps Search Address.
    Returns (lat, lon) or None if not found or confidence too low.
    """
    key = mcp_settings.azure_maps_subscription_key
    if not key:
        return None

    base = mcp_settings.azure_maps_base_url.rstrip("/")
    url = f"{base}/search/address/json"

    params: dict[str, str] = {
        "api-version": "1.0",
        "subscription-key": key,
        "query": address,
        "countrySet": "BR",
        "limit": "1",
        "language": "pt-BR",
        # typeahead=false forces full-query geocoding mode (not autocomplete),
        # which returns significantly more accurate coordinates for complete
        # address strings (logradouro + bairro + cidade + UF + CEP).
        "typeahead": "false",
    }

    session = _get_session()
    try:
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            data: dict[str, Any] = await resp.json()

        results = data.get("results", [])
        if not results:
            logger.debug("Geocode no results for '%s'", address[:120])
            return None

        best = results[0]
        confidence = (best.get("matchConfidence") or {}).get("score", 1.0)
        entity_type = best.get("entityType", "unknown")
        result_type = best.get("type", "unknown")

        if confidence < MIN_CONFIDENCE:
            logger.debug(
                "Geocode low confidence (%.2f < %.2f, type=%s/%s) for '%s' — discarded",
                confidence, MIN_CONFIDENCE, result_type, entity_type, address[:120],
            )
            return None

        pos = best.get("position", {})
        lat = pos.get("lat")
        lon = pos.get("lon")
        if lat is not None and lon is not None:
            logger.debug(
                "Geocode OK (confidence=%.2f, type=%s/%s): '%s' → (%.6f, %.6f)",
                confidence, result_type, entity_type, address[:120], lat, lon,
            )
            return (float(lat), float(lon))
        return None
    except Exception as e:
        logger.warning("Geocode failed for '%s': %s", address[:100], e)
        return None


async def batch_geocode(
    addresses: dict[str, str],
    concurrency: int = MAX_CONCURRENT,
) -> dict[str, tuple[float, float]]:
    """
    Geocode multiple addresses with two-tier caching and concurrency control.

    Lookup order per address:
        1. Redis (L1) — fast, 7-day TTL
        2. MongoDB (L2) — persistent, re-warms Redis on hit
        3. Azure Maps API — writes to both Redis and MongoDB

    Args:
        addresses: {identifier: full_address_string}
        concurrency: max parallel Azure Maps requests

    Returns:
        {identifier: (lat, lon)} for successfully geocoded addresses.
        Identifiers with failed/empty geocoding are omitted.
    """
    if not addresses:
        return {}

    if not mcp_settings.azure_maps_subscription_key:
        logger.warning("Azure Maps key not configured — skipping geocoding")
        return {}

    redis = await get_redis_cache()
    results: dict[str, tuple[float, float]] = {}
    after_redis: dict[str, str] = {}  # ident -> addr (missed L1)

    # ── L1: Redis lookup ──
    for ident, addr in addresses.items():
        if not addr or not addr.strip():
            continue
        cached_raw = await redis.get(_cache_key(addr))
        if cached_raw:
            try:
                coords = json.loads(cached_raw)
                results[ident] = (coords["lat"], coords["lon"])
                continue
            except (json.JSONDecodeError, KeyError):
                pass
        after_redis[ident] = addr

    redis_hits = len(results)

    # ── L2: MongoDB lookup (batch) ──
    mongo_hits = 0
    to_geocode: dict[str, str] = {}

    if after_redis:
        try:
            from mcp_servers.common.mongo_geocache import get_mongo_geocache
            mongo_cache = await get_mongo_geocache()

            key_to_ident: dict[str, str] = {}
            key_to_addr: dict[str, str] = {}
            for ident, addr in after_redis.items():
                bk = _bare_key(addr)
                key_to_ident[bk] = ident
                key_to_addr[bk] = addr

            found = await mongo_cache.get_many(list(key_to_ident.keys()))

            for bk, coords in found.items():
                ident = key_to_ident[bk]
                results[ident] = coords
                mongo_hits += 1
                await redis.set(
                    _cache_key(key_to_addr[bk]),
                    json.dumps({"lat": coords[0], "lon": coords[1]}),
                    ttl=GEOCODE_TTL,
                )

            for ident, addr in after_redis.items():
                if ident not in results:
                    to_geocode[ident] = addr
        except Exception as e:
            logger.warning("MongoDB geocache lookup failed (non-blocking): %s", e)
            to_geocode = after_redis

    logger.info(
        "batch_geocode: %d addresses, %d redis, %d mongo, %d to geocode",
        len(addresses), redis_hits, mongo_hits, len(to_geocode),
    )

    if not to_geocode:
        return results

    # ── L3: Azure Maps API ──
    sem = asyncio.Semaphore(concurrency)
    newly_geocoded: list[dict] = []

    async def _geocode_and_cache(ident: str, addr: str) -> None:
        async with sem:
            coords = await _geocode_one(addr)
            if coords:
                results[ident] = coords
                newly_geocoded.append({
                    "key": _bare_key(addr),
                    "address": addr,
                    "lat": coords[0],
                    "lon": coords[1],
                })
                await redis.set(
                    _cache_key(addr),
                    json.dumps({"lat": coords[0], "lon": coords[1]}),
                    ttl=GEOCODE_TTL,
                )

    tasks = [_geocode_and_cache(ident, addr) for ident, addr in to_geocode.items()]
    await asyncio.gather(*tasks)

    # ── Persist new results to MongoDB (bulk, non-blocking) ──
    if newly_geocoded:
        try:
            from mcp_servers.common.mongo_geocache import get_mongo_geocache
            mongo_cache = await get_mongo_geocache()
            written = await mongo_cache.set_many(newly_geocoded)
            logger.info("batch_geocode: persisted %d new entries to MongoDB", written)
        except Exception as e:
            logger.warning("MongoDB geocache write failed (non-blocking): %s", e)

    logger.info(
        "batch_geocode: resolved %d/%d addresses (redis=%d, mongo=%d, api=%d)",
        len(results), len(addresses), redis_hits, mongo_hits, len(newly_geocoded),
    )
    return results
