"""
Request ID Middleware
=====================

Generates and tracks unique request IDs for tracing and debugging.
"""

import uuid
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import log_context

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Middleware that ensures every request has a unique ID.
    
    - Reads X-Request-ID from incoming request headers
    - Generates a new UUID if not present
    - Adds the ID to response headers
    - Binds the ID to the logging context
    """

    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
        # Get existing request ID or generate new one
        request_id = request.headers.get(REQUEST_ID_HEADER)
        if not request_id:
            request_id = str(uuid.uuid4())

        # Store in request state for access in handlers
        request.state.request_id = request_id

        # Add to logging context
        log_context(request_id=request_id)

        # Process request
        response = await call_next(request)

        # Add to response headers
        response.headers[REQUEST_ID_HEADER] = request_id

        return response
