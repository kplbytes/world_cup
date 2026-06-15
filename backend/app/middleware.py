"""FastAPI middleware for request ID tracking and access logging."""

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.logging_config import generate_request_id, request_id_var

logger = logging.getLogger(__name__)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a unique request_id to every HTTP request for log tracing."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Use incoming X-Request-ID header or generate a new one
        incoming_id = request.headers.get("X-Request-ID")
        rid = incoming_id[:16] if incoming_id else generate_request_id()
        request_id_var.set(rid)

        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Log HTTP requests with method, path, status, and duration."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = round((time.monotonic() - start) * 1000, 1)

        # Skip health checks to reduce noise
        if request.url.path == "/api/health":
            return response

        logger.info(
            "%s %s -> %s (%.1fms)",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            extra={"duration_ms": duration_ms},
        )
        return response
