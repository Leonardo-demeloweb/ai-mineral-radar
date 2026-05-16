#!/usr/bin/env python3
"""
Limpa caches Redis do MCP Geo (rotas, geocode, isócronas).

Uso (na pasta backend, com Redis a correr):
    python scripts/flush_geo_cache.py
    python scripts/flush_geo_cache.py --only rotas
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_servers.common.config import mcp_settings
from mcp_servers.common.redis_cache import RedisCache


async def main() -> None:
    parser = argparse.ArgumentParser(description="Flush MineralRadar geo Redis keys")
    parser.add_argument(
        "--only",
        choices=("rotas", "geocode", "all"),
        default="all",
        help="rotas = geo:rota:* ; geocode = geo:geocode:* ; all = geo:*",
    )
    args = parser.parse_args()

    patterns = {
        "rotas": ["geo:rota:*"],
        "geocode": ["geo:geocode:*"],
        "all": ["geo:*"],
    }
    pats = patterns[args.only]

    cache = RedisCache()
    await cache.connect()
    if cache.client is None:
        print(f"Redis indisponível em {mcp_settings.redis_host}:{mcp_settings.redis_port}")
        sys.exit(1)

    total = 0
    for pattern in pats:
        keys: list[bytes] = []
        async for key in cache.client.scan_iter(match=pattern):
            keys.append(key)
        if keys:
            deleted = await cache.client.delete(*keys)
            total += int(deleted)
            print(f"  {pattern}: {deleted} chave(s) removida(s)")

    await cache.disconnect()
    print(f"Total: {total} chave(s). Reinicie o MCP Geo: ./scripts/start_mcp.sh")


if __name__ == "__main__":
    asyncio.run(main())
