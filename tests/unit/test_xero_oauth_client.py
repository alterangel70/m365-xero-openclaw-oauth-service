"""
Unit tests for XeroAuthlibOAuthClient.

Authlib's AsyncOAuth2Client is replaced by a mock so these tests exercise
only the adapter logic: URL construction, response parsing, error mapping,
and the /connections tenant-ID fetch.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.adapters.outbound.xero.oauth_client import (
    XeroAuthlibOAuthClient,
    _raw_to_token_set,
)
from app.core.errors import ConnectionExpiredError, ProviderUnavailableError

# ── Constants ──────────────────────────────────────────────────────────────────

CLIENT_ID = "xero-client-id"
CLIENT_SECRET = "xero-client-secret"
REDIRECT_URI = "https://app.example.com/oauth/xero/callback"
SCOPES = "openid profile accounting.transactions"
TENANT_ID = "tenant-abc-123"
ACCESS_TOKEN = "access-tok"
REFRESH_TOKEN = "refresh-tok"
EXPIRES_AT_UNIX = 1800.0  # relative epoch value, doesn't need to be real time


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_raw_token(
    access_token: str = ACCESS_TOKEN,
    refresh_token: str = REFRESH_TOKEN,
    expires_at: float = EXPIRES_AT_UNIX,
) -> dict:
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_at": expires_at,
        "scope": SCOPES,
    }


def _connections_response(tenant_id: str = TENANT_ID) -> MagicMock:
    resp = MagicMock()
    resp.is_success = True
    resp.json.return_value = [{"tenantId": tenant_id}]
    return resp


def _empty_connections_response() -> MagicMock:
    resp = MagicMock()
    resp.is_success = True
    resp.json.return_value = []
    return resp


def _error_response(status_code: int = 500) -> MagicMock:
    resp = MagicMock()
    resp.is_success = False
    resp.status_code = status_code
    resp.text = "error"
    return resp


@pytest.fixture
def mock_http() -> AsyncMock:
    client = AsyncMock()
    client.get = AsyncMock(return_value=_connections_response())
    client.post = AsyncMock()
    return client


@pytest.fixture
def xero_client(mock_http) -> XeroAuthlibOAuthClient:
    return XeroAuthlibOAuthClient(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scopes=SCOPES,
        http_client=mock_http,
    )


# ── build_authorization_url ────────────────────────────────────────────────────


def test_build_authorization_url_contains_client_id(xero_client):
    url = xero_client.build_authorization_url(state="random-state")
    assert CLIENT_ID in url


def test_build_authorization_url_contains_state(xero_client):
    url = xero_client.build_authorization_url(state="my-csrf-token")
    assert "my-csrf-token" in url


def test_build_authorization_url_points_to_xero(xero_client):
    url = xero_client.build_authorization_url(state="s")
    assert "xero.com" in url or "login.xero" in url


# ── exchange_code ──────────────────────────────────────────────────────────────


async def test_exchange_code_returns_token_set_with_tenant(xero_client, mock_http):
    with patch(
        "app.adapters.outbound.xero.oauth_client.AsyncOAuth2Client"
    ) as MockClient:
        mock_session = AsyncMock()
        mock_session.fetch_token = AsyncMock(return_value=_make_raw_token())
        MockClient.return_value = mock_session

        token = await xero_client.exchange_code(code="auth-code", state="s")

    assert token.access_token == ACCESS_TOKEN
    assert token.refresh_token == REFRESH_TOKEN
    assert token.xero_tenant_id == TENANT_ID


async def test_exchange_code_fetches_tenant_id(xero_client, mock_http):
    with patch(
        "app.adapters.outbound.xero.oauth_client.AsyncOAuth2Client"
    ) as MockClient:
        mock_session = AsyncMock()
        mock_session.fetch_token = AsyncMock(return_value=_make_raw_token())
        MockClient.return_value = mock_session

        await xero_client.exchange_code(code="auth-code", state="s")

    mock_http.get.assert_awaited_once()
    call_url = mock_http.get.call_args.args[0]
    assert "connections" in call_url


async def test_exchange_code_sets_none_tenant_when_connections_empty(
    xero_client, mock_http
):
    mock_http.get.return_value = _empty_connections_response()
    with patch(
        "app.adapters.outbound.xero.oauth_client.AsyncOAuth2Client"
    ) as MockClient:
        mock_session = AsyncMock()
        mock_session.fetch_token = AsyncMock(return_value=_make_raw_token())
        MockClient.return_value = mock_session

        token = await xero_client.exchange_code(code="c", state="s")

    assert token.xero_tenant_id is None


async def test_exchange_code_provider_error_raises_provider_unavailable(
    xero_client, mock_http
):
    with patch(
        "app.adapters.outbound.xero.oauth_client.AsyncOAuth2Client"
    ) as MockClient:
        mock_session = AsyncMock()
        mock_session.fetch_token = AsyncMock(
            side_effect=Exception("invalid_client")
        )
        MockClient.return_value = mock_session

        with pytest.raises(ProviderUnavailableError):
            await xero_client.exchange_code(code="bad", state="s")


# ── refresh_token ──────────────────────────────────────────────────────────────


async def test_refresh_token_returns_new_token_set(xero_client):
    from app.core.domain.token import TokenSet
    from datetime import timedelta

    old = TokenSet(
        access_token="old-access",
        refresh_token="old-refresh",
        expires_at=datetime.now(tz=timezone.utc) - timedelta(seconds=10),
        xero_tenant_id=TENANT_ID,
    )

    with patch(
        "app.adapters.outbound.xero.oauth_client.AsyncOAuth2Client"
    ) as MockClient:
        mock_session = AsyncMock()
        mock_session.refresh_token = AsyncMock(
            return_value=_make_raw_token(
                access_token="new-access", refresh_token="new-refresh"
            )
        )
        MockClient.return_value = mock_session

        new_token = await xero_client.refresh_token(old)

    assert new_token.access_token == "new-access"
    assert new_token.refresh_token == "new-refresh"
    # Tenant ID preserved from the original token set
    assert new_token.xero_tenant_id == TENANT_ID


async def test_refresh_token_invalid_grant_raises_connection_expired(xero_client):
    from app.core.domain.token import TokenSet
    from datetime import timedelta

    old = TokenSet(
        access_token="a",
        refresh_token="stale-refresh",
        expires_at=datetime.now(tz=timezone.utc) - timedelta(seconds=10),
    )

    with patch(
        "app.adapters.outbound.xero.oauth_client.AsyncOAuth2Client"
    ) as MockClient:
        mock_session = AsyncMock()
        mock_session.refresh_token = AsyncMock(
            side_effect=Exception("invalid_grant: token_revoked")
        )
        MockClient.return_value = mock_session

        with pytest.raises(ConnectionExpiredError):
            await xero_client.refresh_token(old)


async def test_refresh_token_no_refresh_token_raises_connection_expired(xero_client):
    from app.core.domain.token import TokenSet

    no_refresh = TokenSet(
        access_token="a",
        expires_at=datetime.now(tz=timezone.utc),
    )

    with pytest.raises(ConnectionExpiredError):
        await xero_client.refresh_token(no_refresh)


async def test_refresh_token_other_error_raises_provider_unavailable(xero_client):
    from app.core.domain.token import TokenSet
    from datetime import timedelta

    old = TokenSet(
        access_token="a",
        refresh_token="r",
        expires_at=datetime.now(tz=timezone.utc) - timedelta(seconds=10),
    )

    with patch(
        "app.adapters.outbound.xero.oauth_client.AsyncOAuth2Client"
    ) as MockClient:
        mock_session = AsyncMock()
        mock_session.refresh_token = AsyncMock(
            side_effect=Exception("network timeout")
        )
        MockClient.return_value = mock_session

        with pytest.raises(ProviderUnavailableError):
            await xero_client.refresh_token(old)


# ── revoke_token ───────────────────────────────────────────────────────────────


async def test_revoke_token_posts_to_revocation_endpoint(xero_client, mock_http):
    from app.core.domain.token import TokenSet

    token = TokenSet(access_token="tok", expires_at=datetime.now(tz=timezone.utc))
    mock_http.post.return_value = MagicMock(is_success=True)

    await xero_client.revoke_token(token)

    mock_http.post.assert_awaited_once()
    call_url = mock_http.post.call_args.args[0]
    assert "revoc" in call_url


async def test_revoke_token_failure_does_not_raise(xero_client, mock_http):
    from app.core.domain.token import TokenSet

    token = TokenSet(access_token="tok", expires_at=datetime.now(tz=timezone.utc))
    mock_http.post.side_effect = Exception("network error")

    # Should NOT raise
    await xero_client.revoke_token(token)


# ── _raw_to_token_set helper ───────────────────────────────────────────────────


def test_raw_to_token_set_uses_expires_at():
    now_unix = datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp()
    raw = {"access_token": "a", "refresh_token": "r", "expires_at": now_unix}
    ts = _raw_to_token_set(raw, "tenant-1")
    assert ts.expires_at == datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert ts.xero_tenant_id == "tenant-1"


def test_raw_to_token_set_falls_back_to_expires_in():
    raw = {"access_token": "a", "expires_in": 3600}
    ts = _raw_to_token_set(raw, None)
    assert ts.expires_at > datetime.now(tz=timezone.utc)
    assert ts.xero_tenant_id is None
