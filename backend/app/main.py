"""
MineralRadar API
=============================

Main FastAPI application entry point.
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.routes import analises, chat, geo, health, projetos
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.mongodb import mongodb_client
from app.db.opensearch import opensearch_client
from app.db.redis import redis_client
from app.middlewares.logging import LoggingMiddleware
from app.middlewares.rate_limit import limiter
from app.middlewares.request_id import RequestIDMiddleware

# Configure logging first
configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan handler.
    
    Manages startup and shutdown events for database connections.
    """
    # ==================== Startup ====================
    logger.info(
        "Starting MineralRadar API",
        version=settings.app_version,
        environment=settings.environment,
    )

    # Connect to databases
    try:
        await mongodb_client.connect()
        from app.memory.long_term import ensure_indexes
        await ensure_indexes(mongodb_client.database)
    except Exception as e:
        logger.warning(
            "MongoDB not fully ready at startup — will reconnect on first use",
            error=str(e),
        )

    try:
        await redis_client.connect()
    except Exception as e:
        logger.warning("Failed to connect to Redis", error=str(e))

    try:
        await opensearch_client.connect()
    except Exception as e:
        logger.warning("Failed to connect to OpenSearch", error=str(e))

    # ── LangGraph Agent (MCP provider + compiled graph) ──────────────────
    try:
        from app.langgraph.graph import build_graph
        from mcp_servers.common.unified_mcp_provider import UnifiedMCPProvider

        mcp_provider = UnifiedMCPProvider()
        await mcp_provider.connect()

        connected_count = sum(
            1 for s in mcp_provider._servers.values() if s.connected
        )
        total_tools = sum(
            len(s.tools) for s in mcp_provider._servers.values() if s.connected
        )

        if connected_count == 0:
            raise RuntimeError(
                "0 MCP servers connected — "
                "start jazidas(:8110), empresas(:8111), geo(:8112) BEFORE the API"
            )

        app.state.mcp_provider = mcp_provider
        app.state.agent_graph = build_graph(mcp_provider)
        logger.info(
            "LangGraph agent ready",
            extra={
                "mcp_servers_connected": connected_count,
                "total_tools": total_tools,
                "mcp_status": mcp_provider.status(),
            },
        )
    except (Exception, asyncio.CancelledError) as e:
        logger.warning(
            "LangGraph agent unavailable — chat endpoint will return 503",
            error=str(e),
        )
        app.state.mcp_provider = None
        app.state.agent_graph = None

    logger.info("Application startup complete")

    yield  # Application is running

    # ==================== Shutdown ====================
    logger.info("Shutting down MineralRadar API")

    # OpenSearch native MCP client (streamable HTTP) — optional singleton
    try:
        from mcp_servers.common.opensearch_mcp_client import shutdown_opensearch_mcp_singleton

        await shutdown_opensearch_mcp_singleton()
    except Exception as e:
        logger.debug("OpenSearch MCP singleton shutdown", error=str(e))

    # Disconnect MCP provider before other services (httpx JSON-RPC)
    mcp_provider = getattr(app.state, "mcp_provider", None)
    if mcp_provider is not None:
        try:
            await mcp_provider.disconnect()
        except BaseExceptionGroup as eg:
            logger.warning(
                "MCP provider disconnect (exception group)",
                errors=[repr(x) for x in eg.exceptions],
            )
        except (RuntimeError, asyncio.CancelledError) as e:
            # Uvicorn --reload / SIGINT can cancel lifespan while httpx closes
            logger.debug("MCP provider disconnect", error=repr(e))
        except Exception as e:
            logger.warning("Error disconnecting MCP provider", error=str(e))

    await mongodb_client.disconnect()
    await redis_client.disconnect()
    await opensearch_client.disconnect()

    logger.info("Application shutdown complete")


def create_application() -> FastAPI:
    """
    Application factory function.
    
    Creates and configures the FastAPI application.
    """
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="""
        MineralRadar API - Inteligência Mineral
        
        API Gateway for mineral supply intelligence platform.
        Provides search, analysis, and AI-powered insights for
        mining operations and construction projects.
        
        ## Features
        - 🔍 Hybrid search (text + vector + geo)
        - 🗺️ Geospatial queries
        - 🤖 AI-powered agents via LangGraph
        - 📊 Analytics and reporting
        """,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url="/redoc" if settings.environment != "production" else None,
        openapi_url="/openapi.json" if settings.environment != "production" else None,
        lifespan=lifespan,
    )

    # ==================== Middlewares ====================
    
    # CORS - must be first
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
    )

    # Request ID tracking
    app.add_middleware(RequestIDMiddleware)

    # Request logging
    app.add_middleware(LoggingMiddleware)

    # Rate limiting
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # ==================== Exception Handlers ====================

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """Handle all unhandled exceptions."""
        logger.exception(
            "Unhandled exception",
            error=str(exc),
            path=request.url.path,
        )
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "Internal server error",
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    # ==================== Routes ====================

    # Health check routes (no prefix)
    app.include_router(health.router)

    # API v1 routes
    # app.include_router(
    #     auth.router,
    #     prefix=f"{settings.api_prefix}/auth",
    #     tags=["Authentication"],
    # )
    # app.include_router(
    #     jazidas.router,
    #     prefix=f"{settings.api_prefix}/jazidas",
    #     tags=["Jazidas"],
    # )
    # app.include_router(
    #     empresas.router,
    #     prefix=f"{settings.api_prefix}/empresas",
    #     tags=["Empresas"],
    # )
    app.include_router(
        projetos.router,
        prefix=f"{settings.api_prefix}/projetos",
        tags=["Projetos"],
    )
    app.include_router(
        analises.router,
        prefix=f"{settings.api_prefix}/analises",
        tags=["Análises"],
    )
    app.include_router(
        chat.router,
        prefix=f"{settings.api_prefix}/chat",
        tags=["AI Chat"],
    )
    app.include_router(
        geo.router,
        prefix=f"{settings.api_prefix}/geo",
        tags=["Geo"],
    )

    # ==================== Root Endpoint ====================

    @app.get("/", include_in_schema=False)
    async def root():
        """Root endpoint - API information."""
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "environment": settings.environment,
            "docs": "/docs" if settings.environment != "production" else None,
        }

    return app


# Create application instance
app = create_application()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.environment == "development",
        workers=settings.workers if settings.environment == "production" else 1,
        log_level=settings.log_level.lower(),
    )
