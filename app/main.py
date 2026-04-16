import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.adapters.inbound.api.middleware import RequestIdMiddleware
from app.adapters.inbound.api.oauth import router as oauth_router
from app.adapters.inbound.api.teams import router as teams_router
from app.adapters.inbound.api.xero import router as xero_router
from app.core.errors import (
    ConnectionExpiredError,
    ConnectionMissingError,
    LockTimeoutError,
    ProviderUnavailableError,
)
from app.infrastructure.config import get_settings
from app.infrastructure.logging import configure_logging, flush_seq_handler
from app.infrastructure.redis_client import create_redis_pool

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Manage resources that must live for the entire application lifetime:
      - configure logging
      - create the shared Redis connection pool
      - create the shared httpx client (for MS Graph and Xero outbound calls)
      - clean them up on shutdown
    """
    settings = get_settings()
    configure_logging(settings)
    logger.info("Starting M365-Xero-OpenClaw integration service")

    app.state.redis = await create_redis_pool()
    logger.info("Redis connection pool ready")

    # A single shared httpx.AsyncClient is used by all outbound adapters.
    # Sharing the client reuses the underlying TCP connection pool.
    app.state.http_client = httpx.AsyncClient(timeout=30.0)
    logger.info("HTTP client ready")

    yield

    await app.state.http_client.aclose()
    logger.info("HTTP client closed")

    await app.state.redis.aclose()
    logger.info("Redis connection pool closed")

    # Flush any records still buffered in the Seq handler so none are lost
    # on shutdown.  Must be called after the last log statement above so
    # those records are also included.
    flush_seq_handler()


app = FastAPI(
    title="M365 Xero OpenClaw Integration Service",
    version="0.1.0",
    # Disable the interactive docs in production via an env-level reverse proxy rule
    # or by toggling these URLs to None.  Left enabled here for development.
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# RequestIdMiddleware must be registered last so it wraps the entire stack:
# it runs first on every inbound request and last on every outbound response.
app.add_middleware(RequestIdMiddleware)


# ── HTTP error handlers ───────────────────────────────────────────────────────
# Normalize FastAPI / Starlette HTTP errors to the agreed error envelope.

_STATUS_TO_ERROR: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    429: "too_many_requests",
    500: "internal_error",
    502: "bad_gateway",
    503: "service_unavailable",
}


@app.exception_handler(HTTPException)
async def http_exception_handler(
    request: Request, exc: HTTPException
) -> JSONResponse:
    error_code = _STATUS_TO_ERROR.get(exc.status_code, "http_error")
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": error_code, "detail": detail},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = exc.errors()
    detail = errors[0]["msg"] if errors else "Request validation failed"
    return JSONResponse(
        status_code=422,
        content={"error": "validation_error", "detail": detail},
    )


@app.exception_handler(ConnectionMissingError)
async def connection_missing_handler(
    request: Request, exc: ConnectionMissingError
) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "connection_missing", "detail": str(exc)},
    )


@app.exception_handler(ConnectionExpiredError)
async def connection_expired_handler(
    request: Request, exc: ConnectionExpiredError
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"error": "connection_expired", "detail": str(exc)},
    )


@app.exception_handler(ProviderUnavailableError)
async def provider_unavailable_handler(
    request: Request, exc: ProviderUnavailableError
) -> JSONResponse:
    return JSONResponse(
        status_code=502,
        content={"error": "provider_unavailable", "detail": str(exc)},
    )


@app.exception_handler(LockTimeoutError)
async def lock_timeout_handler(
    request: Request, exc: LockTimeoutError
) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"error": "lock_timeout", "detail": str(exc)},
    )


# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(teams_router)
app.include_router(xero_router)
app.include_router(oauth_router)


# ── Health ────────────────────────────────────────────────────────────────────


@app.get("/health", tags=["health"])
async def health(request: Request) -> JSONResponse:
    """
    Liveness + dependency health check.

    Probes:
      * **redis**    – PING to the Redis connection pool.
      * **ms_graph** – client_credentials token request to the MS token
                       endpoint (5-second timeout); validates connectivity
                       and that the app credentials are accepted.

    Response shape::

        {"status": "ok" | "degraded",
         "redis":    "ok" | "error",
         "ms_graph": "ok" | "error"}

    The ms_graph check is a lightweight GET to the Azure OpenID Connect
    discovery endpoint.  It validates network reachability without requiring
    any credentials or stored tokens (the service now uses delegated auth and
    has no client secret to use for a token probe).
    """
    settings = get_settings()

    # ── Redis probe ───────────────────────────────────────────────────────────
    try:
        await request.app.state.redis.ping()
        redis_status = "ok"
    except Exception:
        logger.exception("Redis ping failed during health check")
        redis_status = "error"

    # ── MS Graph probe ────────────────────────────────────────────────────────
    # GET the Azure tenant OpenID discovery document to verify that the
    # configured tenant ID is valid and the Azure endpoint is reachable.
    # This replaces the old client_credentials token probe — the service no
    # longer holds a client secret.
    ms_graph_status: str
    try:
        discovery_url = (
            f"https://login.microsoftonline.com/{settings.ms_tenant_id}"
            "/v2.0/.well-known/openid-configuration"
        )
        resp = await request.app.state.http_client.get(
            discovery_url,
            timeout=5.0,
        )
        ms_graph_status = "ok" if resp.status_code == 200 else "error"
    except Exception:
        logger.exception("MS Graph health check failed")
        ms_graph_status = "error"

    overall = (
        "degraded"
        if any(s == "error" for s in (redis_status, ms_graph_status))
        else "ok"
    )
    return JSONResponse(
        {
            "status": overall,
            "redis": redis_status,
            "ms_graph": ms_graph_status,
        }
    )
