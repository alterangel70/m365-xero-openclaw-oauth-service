from __future__ import annotations

import uuid

import redis.asyncio as aioredis
from redis.exceptions import WatchError

from app.core.ports.lock_manager import AbstractLockManager


class _LockContext:
    """Async context manager for a single non-blocking lock acquisition attempt.

    Yields True if this caller acquired the lock, False if the lock is already
    held by another caller.  On exit, releases the lock only if this caller
    holds it (safe via Lua script).
    """

    __slots__ = ("_redis", "_key", "_ttl_seconds", "_owner_token", "_acquired")

    def __init__(self, redis: aioredis.Redis, key: str, ttl_seconds: int) -> None:
        self._redis = redis
        self._key = key
        self._ttl_seconds = ttl_seconds
        self._owner_token: str | None = None
        self._acquired: bool = False

    async def __aenter__(self) -> bool:
        self._owner_token = str(uuid.uuid4())
        # SET NX EX: returns the string "OK" if the key was set, None if it
        # already exists.  Both results are handled without raising.
        result = await self._redis.set(
            self._key,
            self._owner_token,
            nx=True,
            ex=self._ttl_seconds,
        )
        self._acquired = result is not None
        return self._acquired

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if not self._acquired or self._owner_token is None:
            return
        # Safe release via optimistic locking: only delete the key if the
        # stored token still matches ours.  This prevents a worker from
        # releasing a lock it no longer owns after the TTL expired and
        # another worker re-acquired it.
        async with self._redis.pipeline() as pipe:
            try:
                await pipe.watch(self._key)
                current = await pipe.get(self._key)
                if current == self._owner_token:
                    pipe.multi()
                    pipe.delete(self._key)
                    await pipe.execute()
                # current != owner_token means the TTL fired and another
                # worker re-acquired; do nothing.
            except WatchError:
                pass  # Key changed between WATCH and EXECUTE; already safe.


class RedisLockManager(AbstractLockManager):
    """Distributed lock backed by Redis SET NX EX with WATCH/MULTI/EXEC safe-release.

    Acquisition uses SET NX EX (single atomic command).
    Release uses an optimistic-locking pipeline to ensure only the owner
    can delete the key, preventing accidental release after TTL expiry.

    Key pattern: lock:refresh:{connection_id}
    TTL: 30 seconds (safety net; normal refresh completes in < 2 seconds)

    Usage
    -----
    async with lock_manager.acquire("lock:refresh:xero-acme", ttl_seconds=30) as acquired:
        if acquired:
            # Perform the refresh; we own the lock.
            ...
        else:
            # Another worker holds the lock.
            # Re-read the token from Redis; it was likely just refreshed.
            ...
    """

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    def acquire(self, key: str, ttl_seconds: int) -> _LockContext:
        return _LockContext(self._redis, key, ttl_seconds)
