import pytest
import fakeredis
from datetime import datetime, timezone

from app.core.domain.token import TokenSet
from app.adapters.outbound.token_store.redis_token_store import RedisTokenStore

_EXPIRES_AT = datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def redis_client():
    return fakeredis.FakeAsyncRedis(decode_responses=True)


@pytest.fixture
def store(redis_client):
    return RedisTokenStore(redis_client)


async def test_store_and_load_full_token_set(store):
    """All optional fields populated (Xero-style token)."""
    token = TokenSet(
        access_token="acc_token_123",
        refresh_token="ref_token_456",
        expires_at=_EXPIRES_AT,
        token_type="Bearer",
        scope="openid profile accounting.transactions",
        xero_tenant_id="tenant-abc-123",
    )
    await store.store("xero-acme", token)
    loaded = await store.load("xero-acme")
    assert loaded == token


async def test_store_and_load_minimal_token_set(store):
    """No refresh_token or xero_tenant_id — MS client_credentials style."""
    token = TokenSet(
        access_token="ms_acc_token",
        expires_at=_EXPIRES_AT,
    )
    await store.store("ms-default", token)
    loaded = await store.load("ms-default")
    assert loaded is not None
    assert loaded.access_token == "ms_acc_token"
    assert loaded.refresh_token is None
    assert loaded.xero_tenant_id is None
    assert loaded.scope is None


async def test_store_overwrites_existing_token(store):
    """A second store for the same connection_id replaces the previous token."""
    old_token = TokenSet(access_token="old_acc", expires_at=_EXPIRES_AT)
    new_token = TokenSet(
        access_token="new_acc",
        refresh_token="new_ref",
        expires_at=_EXPIRES_AT,
    )
    await store.store("conn-1", old_token)
    await store.store("conn-1", new_token)
    loaded = await store.load("conn-1")
    assert loaded == new_token


async def test_load_missing_key_returns_none(store):
    result = await store.load("nonexistent-connection")
    assert result is None


async def test_delete_removes_token(store):
    token = TokenSet(access_token="acc", expires_at=_EXPIRES_AT)
    await store.store("conn-del", token)
    await store.delete("conn-del")
    assert await store.load("conn-del") is None


async def test_delete_nonexistent_is_noop(store):
    """Deleting a key that does not exist must not raise."""
    await store.delete("does-not-exist")


async def test_expires_at_roundtrip_preserves_timezone(store):
    """Timezone-aware datetime must survive Redis serialisation unchanged."""
    token = TokenSet(access_token="t", expires_at=_EXPIRES_AT)
    await store.store("tz-conn", token)
    loaded = await store.load("tz-conn")
    assert loaded.expires_at.tzinfo is not None
    assert loaded.expires_at == _EXPIRES_AT
