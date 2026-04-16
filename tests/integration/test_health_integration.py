"""
Integration tests: /health endpoint.

Real Redis, mock HTTP transport.
"""

import httpx
import pytest

from tests.integration.conftest import TEST_MS_TENANT, register_ms_token_route


async def test_health_all_ok(app_client, mock_router):
    """Both Redis (real) and MS Graph (mocked 200) healthy → status ok."""
    register_ms_token_route(mock_router)

    resp = await app_client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["redis"] == "ok"
    assert body["ms_graph"] == "ok"


async def test_health_ms_graph_error_causes_degraded(app_client, mock_router):
    """MS token endpoint returning 401 → ms_graph error → status degraded."""
    mock_router.post(
        url__regex=r"https://login\.microsoftonline\.com/.+/oauth2/v2\.0/token",
    ).mock(return_value=httpx.Response(401, json={"error": "unauthorized_client"}))

    resp = await app_client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["redis"] == "ok"
    assert body["ms_graph"] == "error"


async def test_health_response_has_required_keys(app_client, mock_router):
    """Response always contains status, redis, and ms_graph."""
    register_ms_token_route(mock_router)

    body = (await app_client.get("/health")).json()

    assert set(body.keys()) == {"status", "redis", "ms_graph"}


async def test_health_no_auth_required(app_client, mock_router):
    """/health must be accessible without the Authorization header."""
    register_ms_token_route(mock_router)

    resp = await app_client.get(
        "/health",
        headers={"Authorization": ""},  # override fixture default
    )

    assert resp.status_code == 200
