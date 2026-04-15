import pytest
import fakeredis

from app.adapters.outbound.token_store.redis_oauth_state_store import RedisOAuthStateStore


@pytest.fixture
def redis_client():
    return fakeredis.FakeAsyncRedis(decode_responses=True)


@pytest.fixture
def store(redis_client):
    return RedisOAuthStateStore(redis_client)


async def test_save_and_pop_returns_connection_id(store):
    await store.save("state-abc123", "xero-acme", ttl_seconds=600)
    connection_id = await store.pop("state-abc123")
    assert connection_id == "xero-acme"


async def test_pop_deletes_state_atomically(store):
    """A second pop of the same state must return None (key was consumed)."""
    await store.save("state-def456", "xero-acme", ttl_seconds=600)
    first = await store.pop("state-def456")
    second = await store.pop("state-def456")
    assert first == "xero-acme"
    assert second is None


async def test_pop_missing_state_returns_none(store):
    result = await store.pop("state-nonexistent")
    assert result is None


async def test_separate_states_do_not_interfere(store):
    """Two concurrent OAuth flows must not cross-contaminate each other."""
    await store.save("state-flow-1", "xero-org-1", ttl_seconds=600)
    await store.save("state-flow-2", "xero-org-2", ttl_seconds=600)
    assert await store.pop("state-flow-1") == "xero-org-1"
    assert await store.pop("state-flow-2") == "xero-org-2"
