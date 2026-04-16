"""
Integration tests: /health endpoint.

Real Redis, mock HTTP transport.

The health endpoint now probes the Azure OpenID Connect discovery document
(GET) instead of the token endpoint, since the service no longer holds a
client secret.
"""

import httpx
import pytest

from tests.integration.conftest import TEST_MS_TENANT

_DISCOVERY_URL_PATTERN = (
    r"https://login\.microsoftonline\.com/.+/v2\.0/\.well-known/openid-configuration"
)


def _mock_discovery_ok(mock_router):
    mock_router.get(
        url__regex=_DISCOVERY_URL_PATTERN,
    ).mock(return_value=httpx.Response(200, json={"issuer": "https://login.microsoftonline.com/"}))


async def test_health_all_ok(app_client, mock_router):
    """Both Redis (real) and MS Graph discovery endpoint (mocked 200) healthy → ok."""
    _mock_discovery_ok(mock_router)

    resp = await app_client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["redis"] == "ok"
    assert body["ms_graph"] == "ok"


async def test_health_ms_graph_error_causes_degraded(app_client, mock_router):
    """Discovery endpoint returning 404 → ms_graph error → status degraded."""
    mock_router.get(
        url__regex=_DISCOVERY_URL_PATTERN,
    ).mock(return_value=httpx.Response(404, json={"error": "tenant_not_found"}))

    resp = await app_client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["redis"] == "ok"
    assert body["ms_graph"] == "error"


async def test_health_response_has_required_keys(app_client, mock_router):
    """Response always contains status, redis, and ms_graph."""
    _mock_discovery_ok(mock_router)

    body = (await app_client.get("/health")).json()

    assert set(body.keys()) == {"status", "redis", "ms_graph"}


async def test_health_no_auth_required(app_client, mock_router):
    """/health must be accessible without the Authorization header."""
    _mock_discovery_ok(mock_router)

    resp = await app_client.get(
        "/health",
        headers={"Authorization": ""},
    )

    assert resp.status_code == 200
