"""
Unit tests for OAuth and connection-management use cases:
  - BuildXeroAuthorizationUrl
  - HandleXeroOAuthCallback
  - GetConnectionStatus
  - RevokeConnection
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.domain.token import TokenSet
from app.core.errors import ConnectionMissingError
from app.core.use_cases.oauth import (
    BuildXeroAuthorizationUrl,
    GetConnectionStatus,
    HandleXeroOAuthCallback,
    RevokeConnection,
)
from app.core.use_cases.results import AuthUrlResult, ConnectionStatus

# ── Helpers ────────────────────────────────────────────────────────────────────

CONNECTION_ID = "xero-acme"
FAKE_URL = "https://login.xero.com/identity/connect/authorize?client_id=x&state=abc"
FAKE_STATE = "abc123"
FAKE_CODE = "auth-code-xyz"
TTL = 600
BUFFER = 300


def _fresh_token(seconds_ahead: int = 3600) -> TokenSet:
    return TokenSet(
        access_token="access",
        refresh_token="refresh",
        expires_at=datetime.now(tz=timezone.utc) + timedelta(seconds=seconds_ahead),
        xero_tenant_id="tenant-1",
    )


def _stale_token(seconds_behind: int = 10) -> TokenSet:
    return TokenSet(
        access_token="old-access",
        refresh_token="old-refresh",
        expires_at=datetime.now(tz=timezone.utc) - timedelta(seconds=seconds_behind),
    )


def _near_expiry_token() -> TokenSet:
    # Within the refresh buffer but not yet technically expired
    return TokenSet(
        access_token="near",
        refresh_token="near-refresh",
        expires_at=datetime.now(tz=timezone.utc) + timedelta(seconds=BUFFER - 1),
    )


# ── BuildXeroAuthorizationUrl ──────────────────────────────────────────────────


@pytest.fixture
def mock_oauth_client() -> MagicMock:
    client = MagicMock()
    client.build_authorization_url = MagicMock(return_value=FAKE_URL)
    client.exchange_code = AsyncMock(return_value=_fresh_token())
    client.revoke_token = AsyncMock()
    return client


@pytest.fixture
def mock_state_store() -> AsyncMock:
    store = AsyncMock()
    store.save = AsyncMock()
    store.pop = AsyncMock(return_value=CONNECTION_ID)
    return store


@pytest.fixture
def mock_token_store() -> AsyncMock:
    store = AsyncMock()
    store.load = AsyncMock(return_value=None)
    store.store = AsyncMock()
    store.delete = AsyncMock()
    return store


async def test_build_url_generates_random_state(mock_oauth_client, mock_state_store):
    use_case = BuildXeroAuthorizationUrl(
        oauth_client=mock_oauth_client,
        state_store=mock_state_store,
        oauth_state_ttl_seconds=TTL,
    )

    with patch("app.core.use_cases.oauth.secrets.token_urlsafe", return_value=FAKE_STATE):
        result = await use_case.execute(connection_id=CONNECTION_ID)

    assert isinstance(result, AuthUrlResult)
    assert result.state == FAKE_STATE
    assert result.authorization_url == FAKE_URL


async def test_build_url_saves_state_to_store(mock_oauth_client, mock_state_store):
    use_case = BuildXeroAuthorizationUrl(
        oauth_client=mock_oauth_client,
        state_store=mock_state_store,
        oauth_state_ttl_seconds=TTL,
    )

    with patch("app.core.use_cases.oauth.secrets.token_urlsafe", return_value=FAKE_STATE):
        await use_case.execute(connection_id=CONNECTION_ID)

    mock_state_store.save.assert_awaited_once_with(
        state=FAKE_STATE,
        connection_id=CONNECTION_ID,
        ttl_seconds=TTL,
    )


async def test_build_url_passes_state_to_oauth_client(mock_oauth_client, mock_state_store):
    use_case = BuildXeroAuthorizationUrl(
        oauth_client=mock_oauth_client,
        state_store=mock_state_store,
        oauth_state_ttl_seconds=TTL,
    )

    with patch("app.core.use_cases.oauth.secrets.token_urlsafe", return_value=FAKE_STATE):
        await use_case.execute(connection_id=CONNECTION_ID)

    mock_oauth_client.build_authorization_url.assert_called_once_with(state=FAKE_STATE)


# ── HandleXeroOAuthCallback ────────────────────────────────────────────────────


async def test_callback_pops_state_and_stores_token(
    mock_oauth_client, mock_state_store, mock_token_store
):
    use_case = HandleXeroOAuthCallback(
        oauth_client=mock_oauth_client,
        state_store=mock_state_store,
        token_store=mock_token_store,
    )

    conn_id = await use_case.execute(code=FAKE_CODE, state=FAKE_STATE)

    mock_state_store.pop.assert_awaited_once_with(FAKE_STATE)
    mock_oauth_client.exchange_code.assert_awaited_once_with(
        code=FAKE_CODE, state=FAKE_STATE
    )
    mock_token_store.store.assert_awaited_once()
    assert conn_id == CONNECTION_ID


async def test_callback_invalid_state_raises_connection_missing(
    mock_oauth_client, mock_state_store, mock_token_store
):
    mock_state_store.pop.return_value = None  # state not found / expired

    use_case = HandleXeroOAuthCallback(
        oauth_client=mock_oauth_client,
        state_store=mock_state_store,
        token_store=mock_token_store,
    )

    with pytest.raises(ConnectionMissingError):
        await use_case.execute(code=FAKE_CODE, state="unknown-state")

    mock_oauth_client.exchange_code.assert_not_awaited()
    mock_token_store.store.assert_not_awaited()


async def test_callback_stores_token_under_correct_connection_id(
    mock_oauth_client, mock_state_store, mock_token_store
):
    use_case = HandleXeroOAuthCallback(
        oauth_client=mock_oauth_client,
        state_store=mock_state_store,
        token_store=mock_token_store,
    )

    await use_case.execute(code=FAKE_CODE, state=FAKE_STATE)

    call_args = mock_token_store.store.call_args
    stored_conn_id = call_args.args[0]
    assert stored_conn_id == CONNECTION_ID


# ── GetConnectionStatus ────────────────────────────────────────────────────────


async def test_status_missing_when_no_token(mock_token_store):
    mock_token_store.load.return_value = None
    use_case = GetConnectionStatus(
        token_store=mock_token_store,
        refresh_buffer_seconds=BUFFER,
    )

    result = await use_case.execute(CONNECTION_ID)

    assert isinstance(result, ConnectionStatus)
    assert result.status == "missing"


async def test_status_valid_when_token_fresh(mock_token_store):
    mock_token_store.load.return_value = _fresh_token()
    use_case = GetConnectionStatus(
        token_store=mock_token_store,
        refresh_buffer_seconds=BUFFER,
    )

    result = await use_case.execute(CONNECTION_ID)

    assert result.status == "valid"


async def test_status_expired_when_token_stale(mock_token_store):
    mock_token_store.load.return_value = _stale_token()
    use_case = GetConnectionStatus(
        token_store=mock_token_store,
        refresh_buffer_seconds=BUFFER,
    )

    result = await use_case.execute(CONNECTION_ID)

    assert result.status == "expired"


async def test_status_expired_when_token_within_buffer(mock_token_store):
    """A token within the refresh buffer window is reported as expired."""
    mock_token_store.load.return_value = _near_expiry_token()
    use_case = GetConnectionStatus(
        token_store=mock_token_store,
        refresh_buffer_seconds=BUFFER,
    )

    result = await use_case.execute(CONNECTION_ID)

    assert result.status == "expired"


# ── RevokeConnection ───────────────────────────────────────────────────────────


async def test_revoke_deletes_token(mock_token_store, mock_oauth_client):
    mock_token_store.load.return_value = _fresh_token()
    use_case = RevokeConnection(
        token_store=mock_token_store,
        oauth_client=mock_oauth_client,
    )

    await use_case.execute(CONNECTION_ID)

    mock_token_store.delete.assert_awaited_once_with(CONNECTION_ID)


async def test_revoke_calls_provider_revocation_for_xero(
    mock_token_store, mock_oauth_client
):
    token = _fresh_token()
    mock_token_store.load.return_value = token
    use_case = RevokeConnection(
        token_store=mock_token_store,
        oauth_client=mock_oauth_client,
    )

    await use_case.execute(CONNECTION_ID)

    mock_oauth_client.revoke_token.assert_awaited_once_with(token)


async def test_revoke_skips_provider_revocation_for_ms(mock_token_store):
    """When oauth_client=None (MS), only local deletion is performed."""
    mock_token_store.load.return_value = _fresh_token()
    use_case = RevokeConnection(
        token_store=mock_token_store,
        oauth_client=None,
    )

    await use_case.execute(CONNECTION_ID)

    mock_token_store.delete.assert_awaited_once_with(CONNECTION_ID)


async def test_revoke_skips_provider_call_when_no_token(
    mock_token_store, mock_oauth_client
):
    """If no token is stored, revoke_token must not be called."""
    mock_token_store.load.return_value = None
    use_case = RevokeConnection(
        token_store=mock_token_store,
        oauth_client=mock_oauth_client,
    )

    await use_case.execute(CONNECTION_ID)

    mock_oauth_client.revoke_token.assert_not_awaited()
    mock_token_store.delete.assert_awaited_once_with(CONNECTION_ID)
