"""
Unit tests for XeroTokenManager.

AbstractTokenStore, AbstractLockManager, and AbstractOAuthClient are all
replaced by AsyncMocks so the tests exercise only the token-manager logic:
cache hits, stale-token refresh, rotation-safe locking, and error propagation.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.adapters.outbound.xero.token_manager import XeroTokenManager
from app.core.domain.token import TokenSet
from app.core.errors import ConnectionExpiredError, ConnectionMissingError, LockTimeoutError

# ── Helpers ────────────────────────────────────────────────────────────────────

CONNECTION_ID = "xero-acme"
BUFFER = 300  # seconds


def _fresh_token(offset_seconds: int = 3600) -> TokenSet:
    """Return a TokenSet that expires in offset_seconds from now."""
    return TokenSet(
        access_token="access-fresh",
        refresh_token="refresh-fresh",
        expires_at=datetime.now(tz=timezone.utc) + timedelta(seconds=offset_seconds),
        xero_tenant_id="tenant-1",
    )


def _stale_token() -> TokenSet:
    """Return a TokenSet that is already expired."""
    return TokenSet(
        access_token="access-stale",
        refresh_token="refresh-stale",
        expires_at=datetime.now(tz=timezone.utc) - timedelta(seconds=10),
        xero_tenant_id="tenant-1",
    )


def _refreshed_token() -> TokenSet:
    return TokenSet(
        access_token="access-new",
        refresh_token="refresh-new",
        expires_at=datetime.now(tz=timezone.utc) + timedelta(seconds=3600),
        xero_tenant_id="tenant-1",
    )


def _make_lock_manager(acquired: bool = True):
    """Return a mock LockManager.  acquire() yields acquired."""
    manager = MagicMock()

    @asynccontextmanager
    async def _acquire(key, ttl):
        yield acquired

    manager.acquire = _acquire
    return manager


@pytest.fixture
def token_store() -> AsyncMock:
    store = AsyncMock()
    store.load = AsyncMock(return_value=None)
    store.store = AsyncMock()
    store.delete = AsyncMock()
    return store


@pytest.fixture
def lock_manager_acquired():
    return _make_lock_manager(acquired=True)


@pytest.fixture
def lock_manager_not_acquired():
    return _make_lock_manager(acquired=False)


@pytest.fixture
def oauth_client() -> AsyncMock:
    client = AsyncMock()
    client.refresh_token = AsyncMock(return_value=_refreshed_token())
    return client


@pytest.fixture
def token_manager(token_store, lock_manager_acquired, oauth_client) -> XeroTokenManager:
    return XeroTokenManager(
        token_store=token_store,
        lock_manager=lock_manager_acquired,
        oauth_client=oauth_client,
        refresh_buffer_seconds=BUFFER,
    )


# ── Cache hit (valid token) ────────────────────────────────────────────────────


async def test_returns_cached_token_when_valid(token_manager, token_store):
    token_store.load.return_value = _fresh_token()

    result = await token_manager.get_valid_token(CONNECTION_ID)

    assert result.access_token == "access-fresh"
    token_store.store.assert_not_awaited()


async def test_does_not_call_oauth_when_token_valid(
    token_manager, token_store, oauth_client
):
    token_store.load.return_value = _fresh_token()

    await token_manager.get_valid_token(CONNECTION_ID)

    oauth_client.refresh_token.assert_not_awaited()


# ── Missing token ──────────────────────────────────────────────────────────────


async def test_missing_token_raises_connection_missing(token_manager, token_store):
    token_store.load.return_value = None

    with pytest.raises(ConnectionMissingError):
        await token_manager.get_valid_token(CONNECTION_ID)


# ── Stale token — refresh path ─────────────────────────────────────────────────


async def test_stale_token_triggers_refresh(token_manager, token_store, oauth_client):
    token_store.load.return_value = _stale_token()

    result = await token_manager.get_valid_token(CONNECTION_ID)

    oauth_client.refresh_token.assert_awaited_once()
    assert result.access_token == "access-new"


async def test_refreshed_token_is_stored(token_manager, token_store):
    token_store.load.return_value = _stale_token()

    await token_manager.get_valid_token(CONNECTION_ID)

    token_store.store.assert_awaited_once()
    stored_conn_id, stored_token = token_store.store.call_args.args
    assert stored_conn_id == CONNECTION_ID
    assert stored_token.access_token == "access-new"
    assert stored_token.refresh_token == "refresh-new"


async def test_double_check_inside_lock_skips_refresh(
    token_store, lock_manager_acquired, oauth_client
):
    """If another worker refreshed while we waited for the lock,
    the double-check should return the fresh token without calling refresh."""
    # First load (before lock): stale
    # Second load (inside lock after double-check): fresh
    token_store.load.side_effect = [_stale_token(), _fresh_token()]

    mgr = XeroTokenManager(
        token_store=token_store,
        lock_manager=lock_manager_acquired,
        oauth_client=oauth_client,
        refresh_buffer_seconds=BUFFER,
    )
    result = await mgr.get_valid_token(CONNECTION_ID)

    assert result.access_token == "access-fresh"
    oauth_client.refresh_token.assert_not_awaited()


# ── Lock contention ────────────────────────────────────────────────────────────


async def test_lock_not_acquired_reads_redis_after_sleep(
    token_store, lock_manager_not_acquired, oauth_client
):
    """When the lock is held by another worker, the manager should poll Redis
    and return the token if it has been refreshed by the lock holder."""
    # initial load: stale; post-sleep load: fresh (written by other worker)
    token_store.load.side_effect = [_stale_token(), _fresh_token()]

    mgr = XeroTokenManager(
        token_store=token_store,
        lock_manager=lock_manager_not_acquired,
        oauth_client=oauth_client,
        refresh_buffer_seconds=BUFFER,
    )

    with patch("app.adapters.outbound.xero.token_manager.asyncio.sleep", new=AsyncMock()):
        result = await mgr.get_valid_token(CONNECTION_ID)

    assert result.access_token == "access-fresh"
    oauth_client.refresh_token.assert_not_awaited()


async def test_lock_contention_exhausted_raises_lock_timeout(
    token_store, lock_manager_not_acquired, oauth_client
):
    """If the lock is never acquired and Redis never returns a fresh token,
    LockTimeoutError must be raised after all retries."""
    # Always stale, never refreshed by another worker
    token_store.load.return_value = _stale_token()

    mgr = XeroTokenManager(
        token_store=token_store,
        lock_manager=lock_manager_not_acquired,
        oauth_client=oauth_client,
        refresh_buffer_seconds=BUFFER,
    )

    with patch("app.adapters.outbound.xero.token_manager.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(LockTimeoutError):
            await mgr.get_valid_token(CONNECTION_ID)


# ── Error propagation ──────────────────────────────────────────────────────────


async def test_connection_expired_propagates_from_oauth(
    token_store, lock_manager_acquired, oauth_client
):
    token_store.load.return_value = _stale_token()
    oauth_client.refresh_token.side_effect = ConnectionExpiredError("revoked")

    mgr = XeroTokenManager(
        token_store=token_store,
        lock_manager=lock_manager_acquired,
        oauth_client=oauth_client,
        refresh_buffer_seconds=BUFFER,
    )

    with pytest.raises(ConnectionExpiredError):
        await mgr.get_valid_token(CONNECTION_ID)
