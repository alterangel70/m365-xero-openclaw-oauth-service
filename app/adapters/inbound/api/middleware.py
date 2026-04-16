"""
Internal API key authentication dependency and request-ID middleware.

Used as a router-level dependency on all routes that must be protected.
Routes excluded from auth: GET /health, GET /v1/oauth/xero/callback.
"""

import uuid

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response

from app.infrastructure.config import get_settings
from app.infrastructure.logging import set_request_id

# auto_error=False so that a missing Authorization header does not produce
# a 403 from FastAPI's internals before our handler can run.  We produce a
# consistent 401 ourselves in verify_api_key below.
_http_bearer = HTTPBearer(auto_error=False)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a correlation ID to every request/response cycle.

    - Reads ``X-Request-ID`` from the incoming request; generates a UUID4 if absent.
    - Stores it on ``request.state.request_id`` for use in route handlers.
    - Binds it into the per-task context variable consumed by the logging filter,
      so every log line emitted during the request carries the same ID.
    - Echoes the final ID back in the ``X-Request-ID`` response header.
    """

    async def dispatch(self, request: StarletteRequest, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        set_request_id(request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


def verify_api_key(
    credentials: HTTPAuthorizationCredentials | None = Security(_http_bearer),
) -> None:
    """FastAPI dependency that enforces the INTERNAL_API_KEY.

    Apply at router level:
        router = APIRouter(dependencies=[Depends(verify_api_key)])
    or at individual route level when finer control is needed.
    """
    if not credentials or credentials.credentials != get_settings().internal_api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid API key",
        )
