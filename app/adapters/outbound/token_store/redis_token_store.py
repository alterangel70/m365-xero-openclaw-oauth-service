from __future__ import annotations

from datetime import datetime
from typing import Optional

import redis.asyncio as aioredis

from app.core.domain.token import TokenSet
from app.core.ports.token_store import AbstractTokenStore

_KEY_PREFIX = "token"

# Sentinel stored in Redis for optional string fields that are absent.
# Redis hashes cannot represent None natively; an empty string is used instead.
_ABSENT = ""


def _key(connection_id: str) -> str:
    return f"{_KEY_PREFIX}:{connection_id}"


def _to_mapping(token_set: TokenSet) -> dict[str, str]:
    """Serialize a TokenSet to a flat dict suitable for Redis HSET."""
    return {
        "access_token": token_set.access_token,
        "refresh_token": token_set.refresh_token or _ABSENT,
        # Store expires_at as an ISO-8601 UTC string so it survives
        # serialization without precision loss or timezone ambiguity.
        "expires_at": token_set.expires_at.isoformat(),
        "token_type": token_set.token_type,
        "scope": token_set.scope or _ABSENT,
        "xero_tenant_id": token_set.xero_tenant_id or _ABSENT,
    }


def _from_mapping(data: dict[str, str]) -> TokenSet:
    """Deserialize a Redis hash back into an immutable TokenSet."""
    return TokenSet(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"] or None,
        expires_at=datetime.fromisoformat(data["expires_at"]),
        token_type=data.get("token_type", "Bearer"),
        scope=data["scope"] or None,
        xero_tenant_id=data["xero_tenant_id"] or None,
    )


class RedisTokenStore(AbstractTokenStore):
    """Stores provider TokenSets in Redis hashes.

    Each connection occupies one key: token:{connection_id}
    No Redis TTL is set; tokens are managed by the application layer
    (the token manager checks expires_at and refreshes on demand).
    """

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def store(self, connection_id: str, token_set: TokenSet) -> None:
        """Atomically replace any existing token for this connection."""
        await self._redis.hset(_key(connection_id), mapping=_to_mapping(token_set))

    async def load(self, connection_id: str) -> TokenSet | None:
        """Return the stored token, or None if no token exists."""
        data = await self._redis.hgetall(_key(connection_id))
        if not data:
            return None
        return _from_mapping(data)

    async def delete(self, connection_id: str) -> None:
        """Delete the stored token.  No-op if the key does not exist."""
        await self._redis.delete(_key(connection_id))
