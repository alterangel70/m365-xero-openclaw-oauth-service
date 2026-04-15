from __future__ import annotations

import redis.asyncio as aioredis
from redis.exceptions import WatchError

from app.core.ports.oauth_state_store import AbstractOAuthStateStore

_KEY_PREFIX = "oauth:state"


def _key(state: str) -> str:
    return f"{_KEY_PREFIX}:{state}"


class RedisOAuthStateStore(AbstractOAuthStateStore):
    """Persists the OAuth state token for the duration of the Xero authorization dance.

    A random state value is generated before redirecting the user to Xero.
    On callback, the state is atomically read and deleted to validate the
    response and retrieve the associated connection_id.

    Key pattern: oauth:state:{state}
    TTL: OAUTH_STATE_TTL_SECONDS (default 600s)
    """

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def save(
        self,
        state: str,
        connection_id: str,
        ttl_seconds: int,
    ) -> None:
        """Store the state → connection_id mapping with an expiry."""
        await self._redis.set(_key(state), connection_id, ex=ttl_seconds)

    async def pop(self, state: str) -> str | None:
        """Atomically return and delete the connection_id for this state.

        Returns None if the state has expired or was never stored.
        Uses optimistic locking (WATCH/MULTI/EXEC) so each state value can
        only be consumed once even under concurrent callback requests.
        """
        key = _key(state)
        async with self._redis.pipeline() as pipe:
            try:
                await pipe.watch(key)
                value = await pipe.get(key)
                if value is None:
                    return None
                pipe.multi()
                pipe.delete(key)
                await pipe.execute()
                return value
            except WatchError:
                # Key changed between WATCH and EXECUTE; treat as consumed.
                return None
