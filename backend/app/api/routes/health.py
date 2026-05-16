"""
Health Check Routes
===================

Endpoints for monitoring application health and dependencies.
"""

from datetime import datetime

from fastapi import APIRouter, status

from app.core.config import settings
from app.core.logging import get_logger
from app.db.mongodb import mongodb_client
from app.db.opensearch import opensearch_client
from app.db.redis import redis_client
from app.schemas.common import HealthResponse

router = APIRouter(tags=["Health"])
logger = get_logger(__name__)


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Health Check",
    description="Returns the health status of the API and its dependencies.",
)
async def health_check() -> HealthResponse:
    """
    Check the health of all services.
    
    Returns OK if the API is running. Also checks connectivity
    to MongoDB, Redis, and OpenSearch.
    """
    services = {}
    overall_status = "healthy"

    # Check MongoDB
    try:
        if mongodb_client.client:
            await mongodb_client.client.admin.command("ping")
            services["mongodb"] = {"status": "healthy", "latency_ms": None}
        else:
            services["mongodb"] = {"status": "not_connected"}
            overall_status = "degraded"
    except Exception as e:
        logger.warning("MongoDB health check failed", error=str(e))
        services["mongodb"] = {"status": "unhealthy", "error": str(e)}
        overall_status = "degraded"

    # Check Redis
    try:
        if redis_client.client:
            await redis_client.client.ping()
            services["redis"] = {"status": "healthy"}
        else:
            services["redis"] = {"status": "not_connected"}
            overall_status = "degraded"
    except Exception as e:
        logger.warning("Redis health check failed", error=str(e))
        services["redis"] = {"status": "unhealthy", "error": str(e)}
        overall_status = "degraded"

    # Check OpenSearch
    try:
        if opensearch_client.client:
            info = await opensearch_client.client.info()
            services["opensearch"] = {
                "status": "healthy",
                "cluster_name": info.get("cluster_name"),
                "version": info.get("version", {}).get("number"),
            }
        else:
            services["opensearch"] = {"status": "not_connected"}
            overall_status = "degraded"
    except Exception as e:
        logger.warning("OpenSearch health check failed", error=str(e))
        services["opensearch"] = {"status": "unhealthy", "error": str(e)}
        overall_status = "degraded"

    return HealthResponse(
        status=overall_status,
        version=settings.app_version,
        environment=settings.environment,
        timestamp=datetime.utcnow(),
        services=services,
    )


@router.get(
    "/ready",
    status_code=status.HTTP_200_OK,
    summary="Readiness Check",
    description="Kubernetes readiness probe endpoint.",
)
async def readiness_check() -> dict:
    """
    Readiness probe for Kubernetes.
    
    Returns 200 if the service is ready to accept traffic.
    """
    # Check if essential services are connected
    ready = True
    
    try:
        if mongodb_client.client:
            await mongodb_client.client.admin.command("ping")
        else:
            ready = False
    except Exception:
        ready = False

    if not ready:
        return {"ready": False}
    
    return {"ready": True}


@router.get(
    "/live",
    status_code=status.HTTP_200_OK,
    summary="Liveness Check",
    description="Kubernetes liveness probe endpoint.",
)
async def liveness_check() -> dict:
    """
    Liveness probe for Kubernetes.
    
    Returns 200 if the service is alive.
    """
    return {"alive": True}
