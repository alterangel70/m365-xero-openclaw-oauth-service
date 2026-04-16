"""
Integration test fixtures.

These tests run against a real Redis instance (provided by docker-compose.test.yml)
and intercept all outbound HTTP calls (MS Graph, Xero, Azure token endpoint) via
the respx mock transport injected into the shared httpx.AsyncClient.

Strategy
--------
- The FastAPI app is started via httpx.ASGITransport so no TCP port is needed.
- The real Redis client is wired by overriding app.state after lifespan runs.
- All outbound HTTP calls go through a single respx.MockRouter bound to the
  shared httpx.AsyncClient so no real provider credentials are required.
- Each test gets a clean Redis (FLUSHDB) to prevent state leaking between tests.

Environment
-----------
The REDIS_URL environment variable must point to a reachable Redis server.
When run via docker-compose.test.yml, this is set to redis://redis-test:6379/0.
"""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
import redis.asyncio as aioredis
import respx

from app.infrastructure.config import get_settings

# ---------------------------------------------------------------------------
# Redis fixture — real server, flushed before each test
# ---------------------------------------------------------------------------

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture
async def redis_client():
    """Return a connected async Redis client; flush the DB before yielding."""
    client = aioredis.from_url(
        REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
    )
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


# ---------------------------------------------------------------------------
# respx router — intercepts all outbound HTTP calls
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_router():
    """A respx MockRouter for registering mock routes per test."""
    yield respx.MockRouter(assert_all_mocked=False, assert_all_called=False)


# ---------------------------------------------------------------------------
# Settings override — dummy provider credentials accepted by the app
# ---------------------------------------------------------------------------

TEST_API_KEY = "integration-test-key"
TEST_MS_TENANT = "test-tenant-id"
TEST_MS_CLIENT_ID = "test-ms-client-id"
TEST_MS_TOKEN = "ms-access-token-integration"
TEST_XERO_TENANT_ID = "xero-tenant-uuid"
TEST_XERO_ACCESS_TOKEN = "xero-access-token-integration"


def _make_settings():
    """Return a Settings-like object with dummy provider credentials."""
    settings = get_settings()
    # pydantic-settings objects are frozen; patch individual fields via
    # object.__setattr__ (works on the Pydantic model instance).
    object.__setattr__(settings, "internal_api_key", TEST_API_KEY)
    object.__setattr__(settings, "ms_tenant_id", TEST_MS_TENANT)
    object.__setattr__(settings, "ms_client_id", TEST_MS_CLIENT_ID)
    object.__setattr__(settings, "seq_enabled", False)
    return settings


# ---------------------------------------------------------------------------
# App client — real ASGI stack wired to real Redis + mock HTTP transport
# ---------------------------------------------------------------------------


@pytest.fixture
async def app_client(redis_client, mock_router):
    """
    Return an httpx.AsyncClient pointing at the full FastAPI application.

    httpx.ASGITransport does not trigger ASGI lifespan events, so we seed
    app.state directly with the real Redis client and the mock HTTP client.
    """
    from app.main import app

    # Wrap the MockRouter with httpx.MockTransport so it implements handle_async_request.
    mock_http = httpx.AsyncClient(
        transport=httpx.MockTransport(mock_router.handler),
        base_url="https://mock",
    )

    # ASGITransport does not fire lifespan events; seed state directly.
    app.state.redis = redis_client
    app.state.http_client = mock_http

    with patch("app.main.get_settings", return_value=_make_settings()), \
         patch("app.adapters.inbound.api.middleware.get_settings",
               return_value=_make_settings()):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        ) as client:
            yield client

    await mock_http.aclose()


# ---------------------------------------------------------------------------
# Helpers — register standard mock routes
# ---------------------------------------------------------------------------


def register_ms_refresh_route(router: respx.MockRouter) -> None:
    """Mock the Azure AD refresh_token grant endpoint."""
    router.post(
        url__regex=r"https://login\.microsoftonline\.com/.+/oauth2/v2\.0/token",
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": TEST_MS_TOKEN,
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "new-ms-refresh-token",
                "scope": "https://graph.microsoft.com/ChannelMessage.Send",
            },
        )
    )


# Keep the old name as an alias so existing tests do not break.
register_ms_token_route = register_ms_refresh_route


def register_ms_token_in_redis(redis, connection_id: str) -> None:
    """Pre-seed a valid delegated MS token in Redis.

    Returns the coroutine from redis.hset — the caller must await it.
    """
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=1)
    ).isoformat()
    return redis.hset(
        f"token:{connection_id}",
        mapping={
            "access_token": TEST_MS_TOKEN,
            "expires_at": expires_at,
            "token_type": "Bearer",
            "refresh_token": "ms-refresh-token",
            "scope": "https://graph.microsoft.com/ChannelMessage.Send",
            "xero_tenant_id": "",
        },
    )


def register_xero_token_in_redis(redis, connection_id: str) -> None:
    """Helper coroutine — pre-seed a valid Xero token in Redis."""
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=1)
    ).isoformat()
    return redis.hset(
        f"token:{connection_id}",
        mapping={
            "access_token": TEST_XERO_ACCESS_TOKEN,
            "expires_at": expires_at,
            "token_type": "Bearer",
            "refresh_token": "xero-refresh-token",
            "scope": "accounting.transactions",
            "xero_tenant_id": TEST_XERO_TENANT_ID,
        },
    )
