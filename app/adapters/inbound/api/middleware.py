"""
Internal API key authentication dependency.

Used as a router-level dependency on all routes that must be protected.
Routes excluded from auth: GET /health, GET /v1/oauth/xero/callback.
"""

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.infrastructure.config import get_settings

# auto_error=False so that a missing Authorization header does not produce
# a 403 from FastAPI's internals before our handler can run.  We produce a
# consistent 401 ourselves in verify_api_key below.
_http_bearer = HTTPBearer(auto_error=False)


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
            detail={"error": "unauthorized", "detail": "Missing or invalid API key"},
        )
