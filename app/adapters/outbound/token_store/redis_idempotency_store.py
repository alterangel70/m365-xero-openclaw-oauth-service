from __future__ import annotations

import json

import redis.asyncio as aioredis

from app.core.ports.idempotency_store import AbstractIdempotencyStore


class RedisIdempotencyStore(AbstractIdempotencyStore):
    """Caches use-case results in Redis to make side-effecting operations idempotent.

    The caller is responsible for constructing a fully-qualified key that
    includes the operation name, e.g.:
        "idempotency:create_invoice:abc-123"
        "idempotency:send_teams_message:xyz-456"

    Results are stored as JSON strings with a caller-supplied TTL.
    """

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def get(self, key: str) -> dict | None:
        """Return the cached result, or None if not found or expired."""
        raw = await self._redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    async def set(self, key: str, result: dict, ttl_seconds: int) -> None:
        """Persist a result with an expiry.  Overwrites any existing value."""
        await self._redis.set(key, json.dumps(result), ex=ttl_seconds)
