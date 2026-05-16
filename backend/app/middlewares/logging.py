"""
Logging Middleware
==================

Logs all HTTP requests with timing information.
"""

import time
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import clear_log_context, get_logger, log_context

logger = get_logger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware that logs all incoming HTTP requests.
    
    Logs:
    - Request method, path, and query params
    - Client IP address
    - Response status code
    - Request duration in milliseconds
    """

    # Paths to exclude from logging (health checks, etc.)
    EXCLUDE_PATHS = {"/health", "/healthz", "/ready", "/metrics", "/favicon.ico"}

    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
        # Skip logging for excluded paths
        if request.url.path in self.EXCLUDE_PATHS:
            return await call_next(request)

        # Extract request info
        method = request.method
        path = request.url.path
        query = str(request.query_params) if request.query_params else ""
        client_ip = self._get_client_ip(request)
        user_agent = request.headers.get("user-agent", "")

        # Add to logging context
        log_context(
            method=method,
            path=path,
            client_ip=client_ip,
        )

        # Log request start
        logger.info(
            "Request started",
            query=query,
            user_agent=user_agent[:100] if user_agent else "",
        )

        # Process request and measure time
        start_time = time.perf_counter()
        
        try:
            response = await call_next(request)
        except Exception as e:
            # Log unhandled exceptions
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.exception(
                "Request failed with exception",
                duration_ms=round(duration_ms, 2),
                error=str(e),
            )
            raise
        finally:
            # Clear logging context after request
            clear_log_context()

        # Calculate duration
        duration_ms = (time.perf_counter() - start_time) * 1000

        # Log request completion
        log_level = "warning" if response.status_code >= 400 else "info"
        getattr(logger, log_level)(
            "Request completed",
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
        )

        # Add timing header
        response.headers["X-Response-Time"] = f"{duration_ms:.2f}ms"

        return response

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP, considering proxies."""
        # Check X-Forwarded-For header (from reverse proxy)
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            # First IP in the list is the original client
            return forwarded_for.split(",")[0].strip()
        
        # Check X-Real-IP header
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip

        # Fall back to direct client IP
        if request.client:
            return request.client.host
        
        return "unknown"
