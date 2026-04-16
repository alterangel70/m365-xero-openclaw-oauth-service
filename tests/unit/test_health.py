"""
Unit tests for the enriched /health endpoint.

Calls the route handler function directly with a mock Request so no ASGI
startup is required.  Redis and the httpx client are injected via mocks.
"""

import json

import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.main import health


# ── Helpers ───────────────────────────────────────────────────────────────────


def _mock_settings(
    ms_tenant_id: str = "tenant",
    ms_client_id: str = "client",
    ms_graph_scopes: str = "https://graph.microsoft.com/ChannelMessage.Send",
) -> MagicMock:
    s = MagicMock()
    s.ms_tenant_id = ms_tenant_id
    s.ms_client_id = ms_client_id
    s.ms_graph_scopes = ms_graph_scopes
    return s


def _mock_request(
    redis_ok: bool = True,
    ms_status: int = 200,
    ms_raises: Exception | None = None,
) -> MagicMock:
    """Build a mock FastAPI Request with pre-configured state."""
    redis = AsyncMock()
    if redis_ok:
        redis.ping.return_value = True
    else:
        redis.ping.side_effect = ConnectionError("Redis unavailable")

    ms_resp = MagicMock()
    ms_resp.status_code = ms_status

    http_client = AsyncMock()
    if ms_raises is not None:
        http_client.get.side_effect = ms_raises
    else:
        http_client.get.return_value = ms_resp

    request = MagicMock()
    request.app.state.redis = redis
    request.app.state.http_client = http_client
    return request


def _body(resp) -> dict:
    return json.loads(resp.body)


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_health_all_ok():
    with patch("app.main.get_settings", return_value=_mock_settings()):
        resp = await health(_mock_request())

    body = _body(resp)
    assert body["status"] == "ok"
    assert body["redis"] == "ok"
    assert body["ms_graph"] == "ok"


async def test_health_redis_error_causes_degraded():
    with patch("app.main.get_settings", return_value=_mock_settings()):
        resp = await health(_mock_request(redis_ok=False))

    body = _body(resp)
    assert body["status"] == "degraded"
    assert body["redis"] == "error"
    assert body["ms_graph"] == "ok"


async def test_health_ms_graph_non_200_causes_degraded():
    with patch("app.main.get_settings", return_value=_mock_settings()):
        resp = await health(_mock_request(ms_status=401))

    body = _body(resp)
    assert body["status"] == "degraded"
    assert body["redis"] == "ok"
    assert body["ms_graph"] == "error"


async def test_health_ms_graph_network_error_causes_degraded():
    with patch("app.main.get_settings", return_value=_mock_settings()):
        resp = await health(
            _mock_request(ms_raises=httpx.ConnectError("connection refused"))
        )

    body = _body(resp)
    assert body["status"] == "degraded"
    assert body["redis"] == "ok"
    assert body["ms_graph"] == "error"


async def test_health_both_components_error():
    with patch("app.main.get_settings", return_value=_mock_settings()):
        resp = await health(
            _mock_request(
                redis_ok=False,
                ms_raises=httpx.ConnectError("refused"),
            )
        )

    body = _body(resp)
    assert body["status"] == "degraded"
    assert body["redis"] == "error"
    assert body["ms_graph"] == "error"


async def test_health_response_contains_all_keys():
    with patch("app.main.get_settings", return_value=_mock_settings()):
        resp = await health(_mock_request())

    assert set(_body(resp).keys()) == {"status", "redis", "ms_graph"}


async def test_health_ms_graph_uses_correct_token_url():
    """Verify the MS token endpoint URL is constructed from settings."""
    settings = _mock_settings(ms_tenant_id="my-tenant-id")
    request = _mock_request()

    with patch("app.main.get_settings", return_value=settings):
        await health(request)

    call_args = request.app.state.http_client.get.call_args
    url = call_args[0][0]
    assert "my-tenant-id" in url
    assert "/.well-known/openid-configuration" in url
