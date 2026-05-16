"""
MCP Server: Empresas
=====================

Expõe tools de busca de empresas (CNPJ — Receita Federal) via protocolo MCP.

Transport: Streamable HTTP (POST /mcp) — recomendado pela spec MCP v1.26+
    - Stateless (cada POST independente)
    - Modo JSON puro para request-response (sem overhead SSE)
    - Single endpoint (/mcp) em vez de 2 rotas (GET /sse + POST /messages)

Porta: 8111
Índice principal: mr_empresas_v001 (~69M estabelecimentos, dynamic:strict, pt_brazilian)

Tools custom (8):
    - buscar_empresas           → Cross 2 índices (CNAE semântico → CNPJ)
    - detalhes_empresa          → Cross 3 índices (CNPJ + CNAE hierarquia + ANM)
    - buscar_por_socio          → Nested socios + inner_hits
    - empresas_por_poligono     → Busca por CNAE filtrada por polígono GeoJSON
    - risco_ambiental_empresa   → Autuações IBAMA por CNPJ
    - autuacoes_por_area        → Autuações IBAMA em raio geográfico
    - alertas_monitoramento     → Alertas de prazo/status/DOU para processos ANM
    - resumo_carteira_alertas   → Resumo executivo de alertas por carteira

QPT cobre (~50%) via OpenSearch MCP Nativo:
    - Queries flat ad-hoc (por razão social, UF, CNPJ, capital, data abertura)
    - Aggregations via PPLTool
    - Lookups de catálogo CNAE por código
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

logger = logging.getLogger("mcp.empresas")

# ==================== Constants ====================
INDEX_CNPJ       = "mr_empresas_v001"
INDEX_CNAE       = "mr_cnae_v001"
INDEX_ANM        = "mr_jazidas_v001"
INDEX_MUNICIPIO  = "mr_municipios_v001"
INDEX_AUTUACOES  = "mr_autuacoes_v001"
INDEX_MONITORING = "mr_monitoring_v001"

# ==================== Services ====================
os_service = OpenSearchService()
redis_cache = RedisCache()
embedding_service = EmbeddingService()

# ==================== MCP Server ====================
mcp = FastMCP("mr-empresas", stateless_http=True)

# Initializes mcp._session_manager at module level (required before lifespan)
_mcp_http_app = mcp.streamable_http_app()


# ==================== Health Check ====================
def health_check(request: Request):
    """Health check endpoint for monitoring."""
    checks = {
        "server": "empresas",
        "status": "healthy",
        "transport": "streamable-http",
        "endpoint": "/mcp",
        "opensearch": os_service.client is not None,
        "redis": redis_cache.available,
        "indices": {
            "principal":  INDEX_CNPJ,
            "cnae":       INDEX_CNAE,
            "anm":        INDEX_ANM,
            "municipio":  INDEX_MUNICIPIO,
            "autuacoes":  INDEX_AUTUACOES,
            "monitoring": INDEX_MONITORING,
        },
        "tools": [
            "buscar_empresas",
            "detalhes_empresa",
            "buscar_por_socio",
            "risco_ambiental_empresa",
            "autuacoes_por_area",
            "empresas_por_poligono",
            "alertas_monitoramento",
            "resumo_carteira_alertas",
        ],
    }
    return JSONResponse(checks)


# ==================== Lifecycle ====================
@asynccontextmanager
async def lifespan(app):
    """Manage service lifecycle and start the FastMCP session manager."""
    logger.info("MCP Empresas starting (Streamable HTTP)...")

    await os_service.connect()
    logger.info(f"OpenSearch connected — principal index: {INDEX_CNPJ}")

    await redis_cache.connect()

    try:
        count = await os_service.count(INDEX_CNPJ)
        logger.info(f"Index {INDEX_CNPJ}: {count:,} documents")
    except Exception as e:
        logger.warning(f"Could not verify index {INDEX_CNPJ}: {e}")

    from mcp_servers.empresas.tools import register_tools
    register_tools(mcp, os_service, redis_cache, embedding_service)

    # Start FastMCP session manager — run() is an async context manager
    async with mcp._session_manager.run():
        logger.info(
            f"MCP Empresas ready — Streamable HTTP on "
            f":{mcp_settings.mcp_empresas_port}/mcp | 8 custom tools"
        )
        yield

    await os_service.disconnect()
    await redis_cache.disconnect()
    logger.info("MCP Empresas stopped")


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
        port=mcp_settings.mcp_empresas_port,
    )
