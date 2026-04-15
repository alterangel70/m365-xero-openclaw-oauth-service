import pytest
import fakeredis

from app.adapters.outbound.token_store.redis_idempotency_store import RedisIdempotencyStore


@pytest.fixture
def redis_client():
    return fakeredis.FakeAsyncRedis(decode_responses=True)


@pytest.fixture
def store(redis_client):
    return RedisIdempotencyStore(redis_client)


async def test_set_and_get_returns_stored_result(store):
    result = {"invoice_id": "INV-001", "status": "DRAFT"}
    await store.set("idempotency:create_invoice:key-abc", result, ttl_seconds=3600)
    loaded = await store.get("idempotency:create_invoice:key-abc")
    assert loaded == result


async def test_get_missing_key_returns_none(store):
    result = await store.get("idempotency:create_invoice:missing")
    assert result is None


async def test_set_overwrites_existing_result(store):
    key = "idempotency:submit_invoice:key-xyz"
    await store.set(key, {"status": "DRAFT"}, ttl_seconds=3600)
    await store.set(key, {"status": "AUTHORISED"}, ttl_seconds=3600)
    loaded = await store.get(key)
    assert loaded == {"status": "AUTHORISED"}


async def test_complex_nested_result_roundtrips(store):
    """Verify JSON serialisation handles nested structures."""
    result = {
        "invoice_id": "INV-002",
        "status": "DRAFT",
        "line_items": [{"description": "Consulting", "amount": "1000.00"}],
    }
    await store.set("idempotency:create_invoice:nested", result, ttl_seconds=3600)
    loaded = await store.get("idempotency:create_invoice:nested")
    assert loaded == result
