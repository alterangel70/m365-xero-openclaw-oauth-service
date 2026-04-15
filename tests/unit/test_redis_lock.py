import pytest
import fakeredis

from app.adapters.outbound.lock.redis_lock import RedisLockManager


@pytest.fixture
def redis_client():
    return fakeredis.FakeAsyncRedis(decode_responses=True)


@pytest.fixture
def lock(redis_client):
    return RedisLockManager(redis_client)


async def test_acquire_succeeds_on_free_key(lock):
    async with lock.acquire("lock:test:conn1", ttl_seconds=30) as acquired:
        assert acquired is True


async def test_lock_is_released_after_context_exits(redis_client, lock):
    """After the context manager exits, the key must be gone."""
    async with lock.acquire("lock:test:conn2", ttl_seconds=30) as acquired:
        assert acquired is True
        # Confirm the key exists while held.
        assert await redis_client.exists("lock:test:conn2") == 1

    # Key must be deleted after normal exit.
    assert await redis_client.exists("lock:test:conn2") == 0


async def test_lock_released_after_exception_in_body(redis_client, lock):
    """Lock must be released even if an exception is raised inside the block."""
    with pytest.raises(RuntimeError):
        async with lock.acquire("lock:test:conn3", ttl_seconds=30):
            raise RuntimeError("simulated failure")

    assert await redis_client.exists("lock:test:conn3") == 0


async def test_second_caller_cannot_acquire_held_lock(redis_client):
    """Two separate lock manager instances share the same Redis client.
    The second caller must receive False while the first holds the lock.
    """
    lock_a = RedisLockManager(redis_client)
    lock_b = RedisLockManager(redis_client)

    async with lock_a.acquire("lock:test:conn4", ttl_seconds=30) as first:
        assert first is True
        async with lock_b.acquire("lock:test:conn4", ttl_seconds=30) as second:
            assert second is False


async def test_lock_can_be_reacquired_after_release(redis_client):
    """Once the first caller releases, a second acquire must succeed."""
    lock_a = RedisLockManager(redis_client)
    lock_b = RedisLockManager(redis_client)

    async with lock_a.acquire("lock:test:conn5", ttl_seconds=30):
        pass  # Lock is released on exit.

    async with lock_b.acquire("lock:test:conn5", ttl_seconds=30) as reacquired:
        assert reacquired is True


async def test_non_owner_cannot_release_lock(redis_client):
    """A second manager that did not acquire the lock must not delete it."""
    lock_a = RedisLockManager(redis_client)
    lock_b = RedisLockManager(redis_client)

    async with lock_a.acquire("lock:test:conn6", ttl_seconds=30) as first:
        assert first is True
        # lock_b fails to acquire (second is False), so its __aexit__
        # will not call the Lua release script for this key.
        async with lock_b.acquire("lock:test:conn6", ttl_seconds=30) as second:
            assert second is False

        # After lock_b's context exits, lock_a's key must still exist.
        assert await redis_client.exists("lock:test:conn6") == 1
