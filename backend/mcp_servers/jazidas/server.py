"""
MCP Server: Jazidas
====================

Expõe tools de busca de processos minerários (ANM) via protocolo MCP.

Transport: Streamable HTTP (POST /mcp) — recomendado pela spec MCP v1.26+
    - Stateless (cada POST independente)
    - Modo JSON puro para request-response (sem overhead SSE)
    - Resumability via Last-Event-Id se conexão cair
    - Single endpoint (/mcp) em vez de 2 rotas (GET /sse + POST /messages)

Porta: 8110
Índice principal: mr_jazidas_v001 (20.4M docs, 14.5 GB)

Tools custom (16):
    - buscar_fornecedores             → Cross 3 índices (substância → ANM → CNPJ)
    - buscar_jazidas                  → Resolução semântica k-NN + geo
    - detalhes_processo               → Cross-index ANM → CNPJ
    - jazidas_por_poligono            → Nested geo_shape
    - verificar_vigencia              → Nested substancias
    - consultar_cfem_processo         → mr_cfem_v001
    - ranking_cfem                    → mr_cfem_v001 aggregations
    - buscar_restricoes_geo           → mr_terras_indigenas_v001 + others
    - ocorrencias_minerais_proximas   → mr_cprm_v001 geo_distance
    - consultar_mercado_mineral       → mr_mercado_v001 tendência anual
    - principais_destinos_mineral     → mr_mercado_v001 ranking países
    - imoveis_car_proximos            → mr_sicar_v001 geo_distance (centróide)
    - imoveis_car_proprietario        → mr_sicar_v001 CPF/CNPJ lookup
    - areas_em_disponibilidade        → mr_jazidas_v001 (fase=disponibilidade)
    - afloramentos_geologicos_proximos → CPRM OGC API Features WFS on-demand
    - consultar_preco_mineral          → Metals-API cotação em tempo real

Resolução de município (nome → codigo_ibge / centroide / polígono) é
responsabilidade exclusiva do MCP Geo (``geo.buscar_municipio``).

QPT cobre (~50%) via OpenSearch MCP Nativo:
    - Queries flat ad-hoc (por UF, fase, titular, substância match direto)
    - Aggregations via PPLTool
    - Listagens de catálogos (substâncias, tipos uso)
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
from mcp_servers.common.embeddings import EmbeddingService

logger = logging.getLogger("mcp.jazidas")

# ==================== Constants ====================
INDEX_ANM       = "mr_jazidas_v001"
INDEX_SUBSTANCIA= "mr_substancias_v001"
INDEX_TIPO_USO  = "mr_tipo_uso_v001"
INDEX_CNPJ      = "mr_empresas_v001"
INDEX_CPRM      = "mr_cprm_v001"
INDEX_MERCADO   = "mr_mercado_v001"
INDEX_SICAR     = "mr_sicar_v001"

# ==================== Services ====================
os_service = OpenSearchService()
redis_cache = RedisCache()
embedding_service = EmbeddingService()

# ==================== MCP Server ====================
mcp = FastMCP("mr-jazidas", stateless_http=True)

# Initializes mcp._session_manager at module level (required before lifespan)
_mcp_http_app = mcp.streamable_http_app()


# ==================== Health Check ====================
def health_check(request: Request):
    """Health check endpoint for monitoring."""
    checks = {
        "server": "jazidas",
        "status": "healthy",
        "transport": "streamable-http",
        "endpoint": "/mcp",
        "opensearch": os_service.client is not None,
        "redis": redis_cache.available,
        "indices": {
            "principal":  INDEX_ANM,
            "substancia": INDEX_SUBSTANCIA,
            "tipo_uso":   INDEX_TIPO_USO,
            "cnpj":       INDEX_CNPJ,
            "cprm":       INDEX_CPRM,
            "mercado":    INDEX_MERCADO,
            "sicar":      INDEX_SICAR,
        },
        "tools": [
            "buscar_fornecedores",
            "buscar_jazidas",
            "detalhes_processo",
            "jazidas_por_poligono",
            "verificar_vigencia_substancia",
            "consultar_cfem_processo",
            "ranking_cfem",
            "buscar_restricoes_geo",
            "ocorrencias_minerais_proximas",
            "consultar_mercado_mineral",
            "principais_destinos_mineral",
            "imoveis_car_proximos",
            "imoveis_car_proprietario",
            "areas_em_disponibilidade",
            "afloramentos_geologicos_proximos",
            "consultar_preco_mineral",
        ],
    }
    return JSONResponse(checks)


# ==================== Lifecycle ====================
@asynccontextmanager
async def lifespan(app):
    """Manage service lifecycle and start the FastMCP session manager."""
    logger.info("MCP Jazidas starting (Streamable HTTP)...")

    await os_service.connect()
    logger.info(f"OpenSearch connected — principal index: {INDEX_ANM}")

    await redis_cache.connect()

    try:
        count = await os_service.count(INDEX_ANM)
        logger.info(f"Index {INDEX_ANM}: {count:,} documents")
    except Exception as e:
        logger.warning(f"Could not verify index {INDEX_ANM}: {e}")

    from mcp_servers.jazidas.tools import register_tools
    register_tools(mcp, os_service, redis_cache, embedding_service)

    # Start FastMCP session manager — run() is an async context manager
    async with mcp._session_manager.run():
        logger.info(
        f"MCP Jazidas ready — Streamable HTTP on "
        f":{mcp_settings.mcp_jazidas_port}/mcp | 16 custom tools"
        )
        yield

    await os_service.disconnect()
    await redis_cache.disconnect()
    logger.info("MCP Jazidas stopped")


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
        port=mcp_settings.mcp_jazidas_port,
    )
