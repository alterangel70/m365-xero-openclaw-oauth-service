"""
Integration tests: OAuth / connection management endpoints.

Real Redis, mock HTTP transport (Xero token + revocation endpoints).
Covers:
- /v1/oauth/xero/authorize — state saved to Redis
- /v1/oauth/xero/callback — state popped, token stored, CSRF enforced
- /v1/connections/{id}/status — valid / expired / missing
- DELETE /v1/connections/{id}/xero — revoke + delete
- DELETE /v1/connections/{id}/ms — local delete only
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.adapters.outbound.xero.oauth_client import XeroAuthlibOAuthClient
from app.core.domain.token import TokenSet
from tests.integration.conftest import (
    TEST_XERO_ACCESS_TOKEN,
    TEST_XERO_TENANT_ID,
    register_xero_token_in_redis,
)

_XERO_REVOKE_URL = "https://identity.xero.com/connect/revocation"


def _fake_token_set() -> TokenSet:
    """Return a realistic TokenSet for patching exchange_code."""
    return TokenSet(
        access_token=TEST_XERO_ACCESS_TOKEN,
        refresh_token="xero-refresh",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scope="accounting.transactions",
        xero_tenant_id=TEST_XERO_TENANT_ID,
    )


# ── /v1/oauth/xero/authorize ──────────────────────────────────────────────────


async def test_authorize_returns_url_and_state(app_client, mock_router, redis_client):
    """Authorize endpoint must return an authorization_url and a state."""
    resp = await app_client.get(
        "/v1/oauth/xero/authorize",
        params={"connection_id": "xero-test"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert "authorization_url" in body
    assert "state" in body
    assert body["state"]
    assert "login.xero.com" in body["authorization_url"]


async def test_authorize_state_saved_to_redis(app_client, mock_router, redis_client):
    """The generated state must be persisted in Redis."""
    resp = await app_client.get(
        "/v1/oauth/xero/authorize",
        params={"connection_id": "xero-test"},
    )
    state = resp.json()["state"]

    stored = await redis_client.get(f"oauth:state:{state}")
    assert stored == "xero-test"


async def test_authorize_different_calls_produce_different_states(
    app_client, mock_router
):
    """Each authorize call must produce a unique state value."""
    r1 = await app_client.get(
        "/v1/oauth/xero/authorize", params={"connection_id": "xero-a"}
    )
    r2 = await app_client.get(
        "/v1/oauth/xero/authorize", params={"connection_id": "xero-b"}
    )

    assert r1.json()["state"] != r2.json()["state"]


# ── /v1/oauth/xero/callback ───────────────────────────────────────────────────


async def _do_authorize(app_client, connection_id: str = "xero-test") -> str:
    """Helper: run the authorize step and return the generated state."""
    resp = await app_client.get(
        "/v1/oauth/xero/authorize", params={"connection_id": connection_id}
    )
    return resp.json()["state"]


async def test_callback_exchanges_code_and_stores_token(
    app_client, mock_router, redis_client
):
    """Valid code + state → token stored in Redis, 200 returned.

    Authlib's AsyncOAuth2Client creates its own internal httpx transport for
    the code-exchange POST, so we patch exchange_code at the class level to
    return a pre-built TokenSet.  The rest of the flow (state lookup, token
    persistence, response shaping) executes against real Redis.
    """
    state = await _do_authorize(app_client)

    with patch.object(
        XeroAuthlibOAuthClient,
        "exchange_code",
        new_callable=AsyncMock,
        return_value=_fake_token_set(),
    ):
        resp = await app_client.get(
            "/v1/oauth/xero/callback",
            params={"code": "valid-code", "state": state},
            headers={"Authorization": ""},  # callback has no API key
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    conn_id = body["connection_id"]

    # Token must be in Redis now.
    token_hash = await redis_client.hgetall(f"token:{conn_id}")
    assert token_hash["access_token"] == TEST_XERO_ACCESS_TOKEN
    assert token_hash["xero_tenant_id"] == TEST_XERO_TENANT_ID


async def test_callback_invalid_state_returns_404(app_client, mock_router):
    """Unknown state → ConnectionMissingError → 404."""
    resp = await app_client.get(
        "/v1/oauth/xero/callback",
        params={"code": "some-code", "state": "does-not-exist"},
        headers={"Authorization": ""},
    )

    assert resp.status_code == 404
    assert resp.json()["error"] == "connection_missing"


async def test_callback_state_is_consumed_exactly_once(
    app_client, mock_router, redis_client
):
    """State must be deleted from Redis after the first successful callback."""
    state = await _do_authorize(app_client)

    with patch.object(
        XeroAuthlibOAuthClient,
        "exchange_code",
        new_callable=AsyncMock,
        return_value=_fake_token_set(),
    ):
        await app_client.get(
            "/v1/oauth/xero/callback",
            params={"code": "code-1", "state": state},
            headers={"Authorization": ""},
        )

    # Second callback with the same state must fail (state consumed).
    resp2 = await app_client.get(
        "/v1/oauth/xero/callback",
        params={"code": "code-2", "state": state},
        headers={"Authorization": ""},
    )
    assert resp2.status_code == 404


# ── /v1/connections/{id}/status ───────────────────────────────────────────────


async def test_connection_status_missing(app_client, mock_router):
    """No token in Redis → status missing."""
    resp = await app_client.get("/v1/connections/no-such-conn/status")

    assert resp.status_code == 200
    assert resp.json()["status"] == "missing"


async def test_connection_status_valid(app_client, mock_router, redis_client):
    """Valid unexpired token → status valid."""
    await register_xero_token_in_redis(redis_client, "xero-valid")

    resp = await app_client.get("/v1/connections/xero-valid/status")

    assert resp.status_code == 200
    assert resp.json()["status"] == "valid"


# ── DELETE /v1/connections/{id}/xero ─────────────────────────────────────────


async def test_revoke_xero_connection_returns_204(
    app_client, mock_router, redis_client
):
    """Revoke endpoint must return 204 and remove the token from Redis."""
    await register_xero_token_in_redis(redis_client, "xero-to-revoke")

    mock_router.post(_XERO_REVOKE_URL).mock(return_value=httpx.Response(200))

    resp = await app_client.delete("/v1/connections/xero-to-revoke/xero")

    assert resp.status_code == 204
    # Token must be gone from Redis.
    remaining = await redis_client.hgetall("token:xero-to-revoke")
    assert remaining == {}


async def test_revoke_xero_connection_no_token_still_returns_204(
    app_client, mock_router
):
    """Revoking a non-existent connection must still succeed (idempotent)."""
    resp = await app_client.delete("/v1/connections/ghost-conn/xero")

    assert resp.status_code == 204


# ── DELETE /v1/connections/{id}/ms ───────────────────────────────────────────


async def test_revoke_ms_connection_returns_204(
    app_client, mock_router, redis_client
):
    """MS revocation deletes the local token only (no provider call)."""
    # Pre-seed a minimal token for an MS connection.
    from datetime import datetime, timedelta, timezone
    await redis_client.hset(
        "token:ms-default",
        mapping={
            "access_token": "ms-token",
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat(),
            "token_type": "Bearer",
            "refresh_token": "",
            "scope": "",
            "xero_tenant_id": "",
        },
    )

    resp = await app_client.delete("/v1/connections/ms-default/ms")

    assert resp.status_code == 204
    remaining = await redis_client.hgetall("token:ms-default")
    assert remaining == {}
