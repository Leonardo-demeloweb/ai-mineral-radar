"""
MCP Server: Geo
=================

Expõe tools geoespaciais (municípios, biomas, imóveis rurais, rotas, geocoding) via protocolo MCP.

Transport: Streamable HTTP (POST /mcp) — recomendado pela spec MCP v1.26+
    - Stateless (cada POST independente)
    - Modo JSON puro para request-response tools
    - Single endpoint (/mcp)

Porta: 8112
Índices: mr_municipios_v001 · mr_biomas_v001 · mr_provincias_v001 · mr_sigef_v001
         mr_portos_v001 · mr_ferrovias_v001 (malha ferroviária federal ANTT)
Serviço externo: Azure Maps REST APIs (Route, Search)

Tools custom (18 com Azure / 13 só OpenSearch):
    - buscar_municipio … imoveis_rurais_em_area (1–7)
    - buscar_porto, porto_por_coordenada, obter_poligono_porto (8–10)
    - buscar_ferrovia, ferrovias_proximas, obter_geometria_ferrovia (11–13)
    - calcular_rota … plotar_endereco (14–18)

QPT cobre (~20%) via OpenSearch MCP Nativo:
    - Lookups simples por código IBGE, UF, capital, Amazônia Legal
    - Aggregations via PPLTool
"""

import logging
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_servers.common.config import mcp_settings
from mcp_servers.common.opensearch_client import OpenSearchService
from mcp_servers.common.redis_cache import RedisCache

logger = logging.getLogger("mcp.geo")

# ==================== Constants ====================
INDEX_MUNICIPIO  = "mr_municipios_v001"
INDEX_BIOMAS     = "mr_biomas_v001"
INDEX_PROVINCIAS = "mr_provincias_v001"
INDEX_SIGEF      = "mr_sigef_v001"
INDEX_PORTOS     = "mr_portos_v001"
INDEX_FERROVIAS  = "mr_ferrovias_v001"

# ==================== Services ====================
os_service = OpenSearchService()
redis_cache = RedisCache()

# ==================== MCP Server ====================
mcp = FastMCP("mr-geo", stateless_http=True)

# Initializes mcp._session_manager at module level (required before lifespan)
_mcp_http_app = mcp.streamable_http_app()


# ==================== Health Check ====================
def health_check(request: Request):
    """Health check endpoint for monitoring."""
    azure_maps_configured = bool(mcp_settings.azure_maps_subscription_key)
    checks = {
        "server": "geo",
        "status": "healthy",
        "transport": "streamable-http",
        "endpoint": "/mcp",
        "opensearch": os_service.client is not None,
        "redis": redis_cache.available,
        "azure_maps": azure_maps_configured,
        "indices": {
            "municipios":  INDEX_MUNICIPIO,
            "biomas":      INDEX_BIOMAS,
            "provincias":  INDEX_PROVINCIAS,
            "sigef":       INDEX_SIGEF,
            "portos":      INDEX_PORTOS,
            "ferrovias":   INDEX_FERROVIAS,
        },
        "tools": [
            "buscar_municipio",
            "municipio_por_coordenada",
            "obter_poligono",
            "municipios_em_raio",
            "bioma_por_coordenada",
            "provincia_por_coordenada",
            "imoveis_rurais_em_area",
            "buscar_porto",
            "porto_por_coordenada",
            "obter_poligono_porto",
            "buscar_ferrovia",
            "ferrovias_proximas",
            "obter_geometria_ferrovia",
            "calcular_rota",
            "comparar_rotas",
            "calcular_isocrona",
            "geocodificar",
            "plotar_endereco",
        ],
    }
    return JSONResponse(checks)


# ==================== Lifecycle ====================
@asynccontextmanager
async def lifespan(app):
    """Manage service lifecycle and start the FastMCP session manager."""
    logger.info("MCP Geo starting (Streamable HTTP)...")

    await os_service.connect()
    logger.info(
        f"OpenSearch connected — índices: {INDEX_MUNICIPIO}, {INDEX_BIOMAS}, "
        f"{INDEX_SIGEF}, {INDEX_PORTOS}, {INDEX_FERROVIAS}"
    )

    await redis_cache.connect()

    for idx in (
        INDEX_MUNICIPIO,
        INDEX_BIOMAS,
        INDEX_PROVINCIAS,
        INDEX_SIGEF,
        INDEX_PORTOS,
        INDEX_FERROVIAS,
    ):
        try:
            count = await os_service.count(idx)
            logger.info(f"Index {idx}: {count:,} documents")
        except Exception as e:
            logger.warning(f"Could not verify index {idx}: {e}")

    azure_maps_configured = bool(mcp_settings.azure_maps_subscription_key)
    if not azure_maps_configured:
        logger.warning(
            "Azure Maps subscription key not configured — "
            "tools calcular_rota, calcular_isocrona, geocodificar will be unavailable"
        )

    from mcp_servers.geo.tools import register_tools
    register_tools(mcp, os_service, redis_cache)

    tools_available = 18 if azure_maps_configured else 13

    # Start FastMCP session manager — run() is an async context manager
    async with mcp._session_manager.run():
        logger.info(
            f"MCP Geo ready — Streamable HTTP on "
            f":{mcp_settings.mcp_geo_port}/mcp | "
            f"{tools_available} custom tools | "
            f"Azure Maps: {'✅' if azure_maps_configured else '❌ (not configured)'}"
        )
        yield

    await os_service.disconnect()
    await redis_cache.disconnect()
    logger.info("MCP Geo stopped")


# ==================== ASGI App ====================
# lifespan starts mcp._session_manager.run() — required by FastMCP's StreamableHTTP
app = Starlette(
    routes=[
        Route("/health", endpoint=health_check, methods=["GET"]),
        Mount("/", app=_mcp_http_app),
    ],
    lifespan=lifespan,
)

if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=mcp_settings.mcp_geo_port,
    )
