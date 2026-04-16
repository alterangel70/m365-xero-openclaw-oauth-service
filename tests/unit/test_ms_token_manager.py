"""
Unit tests for MSTokenManager (delegated / device-code flow).

Redis adapters use fakeredis (no running Redis required).
MSDeviceCodeClient is replaced with an AsyncMock; no real network calls.
asyncio.sleep is patched where needed to keep contention tests fast.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import fakeredis
import pytest

from app.adapters.outbound.lock.redis_lock import RedisLockManager
from app.adapters.outbound.ms_graph.token_manager import (
    _CONTENTION_RETRIES,
    MSTokenManager,
)
from app.adapters.outbound.token_store.redis_token_store import RedisTokenStore
from app.core.domain.token import TokenSet
from app.core.errors import ConnectionExpiredError, ConnectionMissingError, LockTimeoutError


# ── Helpers ───────────────────────────────────────────────────────────────────

CONNECTION_ID = "ms-default"


def _make_token(access_token: str = "fresh_token", hours: int = 1) -> TokenSet:
    """Return a valid delegated TokenSet (includes refresh_token)."""
    return TokenSet(
        access_token=access_token,
        expires_at=datetime.now(tz=timezone.utc) + timedelta(hours=hours),
        refresh_token="refresh-abc",
        token_type="Bearer",
        scope="https://graph.microsoft.com/ChannelMessage.Send",
    )


def _stale_token() -> TokenSet:
    """Return a TokenSet that is within the 300-second refresh buffer."""
    return TokenSet(
        access_token="stale_token",
        expires_at=datetime.now(tz=timezone.utc) + timedelta(seconds=60),
        refresh_token="old-refresh",
    )


@pytest.fixture
def redis_client():
    return fakeredis.FakeAsyncRedis(decode_responses=True)


@pytest.fixture
def token_store(redis_client):
    return RedisTokenStore(redis_client)


@pytest.fixture
def lock_manager(redis_client):
    return RedisLockManager(redis_client)


@pytest.fixture
def mock_device_code_client():
    client = AsyncMock()
    client.refresh_token = AsyncMock(return_value=_make_token("fresh_token"))
    return client


@pytest.fixture
def manager(token_store, lock_manager, mock_device_code_client):
    return MSTokenManager(
        token_store=token_store,
        lock_manager=lock_manager,
        device_code_client=mock_device_code_client,
        refresh_buffer_seconds=300,
    )


# ── Missing token ─────────────────────────────────────────────────────────────


async def test_raises_connection_missing_when_no_token(manager, mock_device_code_client):
    """get_token raises ConnectionMissingError when nothing is stored in Redis."""
    with pytest.raises(ConnectionMissingError):
        await manager.get_token(CONNECTION_ID)

    mock_device_code_client.refresh_token.assert_not_called()


# ── Token cache hit ───────────────────────────────────────────────────────────


async def test_returns_cached_token_when_valid(manager, token_store, mock_device_code_client):
    """No refresh call is made when a valid cached token exists."""
    await token_store.store(CONNECTION_ID, _make_token("cached_token"))

    token = await manager.get_token(CONNECTION_ID)

    assert token == "cached_token"
    mock_device_code_client.refresh_token.assert_not_called()


async def test_refreshes_token_when_near_expiry(manager, token_store, mock_device_code_client):
    """Token is refreshed when it falls within the refresh buffer window."""
    await token_store.store(CONNECTION_ID, _stale_token())

    token = await manager.get_token(CONNECTION_ID)

    assert token == "fresh_token"
    mock_device_code_client.refresh_token.assert_called_once()


async def test_new_token_is_stored_in_redis(manager, token_store, mock_device_code_client):
    """After refresh, the new token is persisted in Redis."""
    await token_store.store(CONNECTION_ID, _stale_token())

    await manager.get_token(CONNECTION_ID)

    stored = await token_store.load(CONNECTION_ID)
    assert stored is not None
    assert stored.access_token == "fresh_token"


async def test_force_flag_triggers_refresh_on_valid_token(
    manager, token_store, mock_device_code_client
):
    """force=True causes a refresh even when the cached token is not yet stale."""
    await token_store.store(CONNECTION_ID, _make_token("cached_token"))

    token = await manager.get_token(CONNECTION_ID, force=True)

    assert token == "fresh_token"
    mock_device_code_client.refresh_token.assert_called_once()


async def test_connection_expired_propagates(manager, token_store, mock_device_code_client):
    """ConnectionExpiredError from the device code client propagates to the caller."""
    await token_store.store(CONNECTION_ID, _stale_token())
    mock_device_code_client.refresh_token.side_effect = ConnectionExpiredError("rejected")

    with pytest.raises(ConnectionExpiredError):
        await manager.get_token(CONNECTION_ID)


# ── Lock contention ───────────────────────────────────────────────────────────


async def test_double_check_avoids_redundant_refresh(
    manager, token_store, mock_device_code_client
):
    """A valid token found on the outer check is returned without acquiring the lock."""
    await token_store.store(CONNECTION_ID, _make_token("peer_token"))

    token = await manager.get_token(CONNECTION_ID)

    assert token == "peer_token"
    mock_device_code_client.refresh_token.assert_not_called()


@patch(
    "app.adapters.outbound.ms_graph.token_manager.asyncio.sleep",
    new_callable=AsyncMock,
)
async def test_lock_contention_exhausted_raises_lock_timeout(
    mock_sleep, token_store, lock_manager
):
    """When the refresh lock is held for all retry attempts and the token stays
    stale in Redis, LockTimeoutError must be raised."""
    contending_manager = MSTokenManager(
        token_store=token_store,
        lock_manager=lock_manager,
        device_code_client=AsyncMock(),
        refresh_buffer_seconds=300,
    )

    lock_key = f"lock:refresh:{CONNECTION_ID}"
    redis = token_store._redis
    await redis.set(lock_key, "other-worker-uuid", ex=30)

    # Store a stale token so the outer check does not immediately raise
    # ConnectionMissingError — we want the code to attempt a lock-based refresh.
    await token_store.store(CONNECTION_ID, _stale_token())

    with pytest.raises(LockTimeoutError):
        await contending_manager.get_token(CONNECTION_ID)

    assert mock_sleep.call_count == _CONTENTION_RETRIES
