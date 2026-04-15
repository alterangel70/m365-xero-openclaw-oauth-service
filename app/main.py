import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.adapters.inbound.api.teams import router as teams_router
from app.adapters.inbound.api.xero import router as xero_router
from app.core.errors import (
    ConnectionExpiredError,
    ConnectionMissingError,
    LockTimeoutError,
    ProviderUnavailableError,
)
from app.infrastructure.config import get_settings
from app.infrastructure.redis_client import create_redis_pool

logger = logging.getLogger(__name__)


def _configure_logging(log_level: str) -> None:
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


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
    _configure_logging(settings.log_level)
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


app = FastAPI(
    title="M365 Xero OpenClaw Integration Service",
    version="0.1.0",
    # Disable the interactive docs in production via an env-level reverse proxy rule
    # or by toggling these URLs to None.  Left enabled here for development.
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


# ── Domain error handlers ─────────────────────────────────────────────────────
# Map application-layer exceptions to the agreed error envelope and HTTP status.


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


# ── Health ────────────────────────────────────────────────────────────────────


@app.get("/health", tags=["health"])
async def health(request: Request) -> JSONResponse:
    """
    Shallow health check.  Verifies the app is running and that the Redis
    connection is reachable.  Does not check external providers.
    """
    try:
        await request.app.state.redis.ping()
        redis_status = "ok"
    except Exception:
        logger.exception("Redis ping failed during health check")
        redis_status = "error"

    return JSONResponse({"status": "ok", "redis": redis_status})
