"""
Xero token manager (rotation-safe).

Manages loading, validating, refreshing, and storing Xero OAuth tokens.
The critical complexity here is Xero's refresh-token rotation policy:
every successful refresh invalidates the old refresh_token immediately.
This means a naive concurrent refresh — two workers both attempting a
refresh at the same time with the same refresh_token — would leave one
worker holding an already-invalidated token.

Rotation-safe locking strategy
-------------------------------
1. Load the token from Redis.
2. If valid (not within refresh_buffer_seconds of expiry), return as-is.
3. Acquire the distributed refresh lock for this connection_id.
   - Only one worker may hold this lock at a time.
4. Double-check inside the lock: another worker may have refreshed while
   we were waiting, producing a new (valid) token that is now in Redis.
5. If still stale, call the OAuth client to refresh.  Store the NEW
   token set (new access_token + new refresh_token) atomically in Redis
   BEFORE releasing the lock.  Any worker that arrives after the lock is
   released will find the new token in step 4.
6. Release the lock.

If the lock cannot be acquired after all retries, raise LockTimeoutError.
If the provider rejects the refresh token, raise ConnectionExpiredError
(the connection must be re-authorized via the Xero OAuth flow).
"""

from __future__ import annotations

import asyncio
import logging

from app.core.domain.token import TokenSet
from app.core.errors import ConnectionMissingError, LockTimeoutError
from app.core.ports.lock_manager import AbstractLockManager
from app.core.ports.oauth_client import AbstractOAuthClient
from app.core.ports.token_store import AbstractTokenStore

logger = logging.getLogger(__name__)

_LOCK_TTL_SECONDS = 30
_CONTENTION_RETRIES = 5
_CONTENTION_SLEEP_SECONDS = 0.1


class XeroTokenManager:
    """Manages Xero token lifecycle with rotation-safe refresh serialization.

    All dependencies are injected; no FastAPI imports, no app.state.
    """

    def __init__(
        self,
        token_store: AbstractTokenStore,
        lock_manager: AbstractLockManager,
        oauth_client: AbstractOAuthClient,
        refresh_buffer_seconds: int,
    ) -> None:
        self._token_store = token_store
        self._lock_manager = lock_manager
        self._oauth_client = oauth_client
        self._refresh_buffer_seconds = refresh_buffer_seconds

    async def get_valid_token(self, connection_id: str) -> TokenSet:
        """Return a valid, non-expired TokenSet for the given Xero connection.

        If the stored token is close to expiry, a refresh is performed under
        a distributed lock to prevent multiple concurrent refreshes from
        racing with the same refresh_token.

        Raises
        ------
        ConnectionMissingError
            No token found in the store — the Xero OAuth flow has not been
            completed for this connection_id.
        ConnectionExpiredError
            The provider rejected the refresh token — the user must re-authorize.
        LockTimeoutError
            The distributed refresh lock could not be acquired within the
            configured retry window.
        """
        token = await self._token_store.load(connection_id)
        if token is None:
            raise ConnectionMissingError(
                f"No Xero token found for connection {connection_id!r}. "
                "The OAuth authorization flow must be completed first."
            )

        if not token.is_expired_or_near(self._refresh_buffer_seconds):
            return token

        # Token is stale — must refresh under lock.
        return await self._refresh_under_lock(connection_id)

    # ── Private ────────────────────────────────────────────────────────────────

    async def _refresh_under_lock(self, connection_id: str) -> TokenSet:
        """Acquire the distributed lock and perform a rotation-safe refresh."""
        lock_key = f"lock:refresh:{connection_id}"

        for _attempt in range(_CONTENTION_RETRIES):
            async with self._lock_manager.acquire(lock_key, _LOCK_TTL_SECONDS) as acquired:
                if acquired:
                    # Double-check: another worker may have refreshed while
                    # we were queued for the lock.
                    token = await self._token_store.load(connection_id)
                    if token and not token.is_expired_or_near(self._refresh_buffer_seconds):
                        logger.debug(
                            "Xero token for %r refreshed by another worker (double-check hit)",
                            connection_id,
                        )
                        return token

                    # We own the lock — perform the refresh.
                    # ConnectionExpiredError propagates up to the caller directly.
                    new_token = await self._oauth_client.refresh_token(token)  # type: ignore[arg-type]
                    await self._token_store.store(connection_id, new_token)
                    logger.info(
                        "Xero token refreshed for %r, expires %s",
                        connection_id,
                        new_token.expires_at.isoformat(),
                    )
                    return new_token

            # Lock held elsewhere — wait briefly then re-read Redis.
            await asyncio.sleep(_CONTENTION_SLEEP_SECONDS)

            token = await self._token_store.load(connection_id)
            if token and not token.is_expired_or_near(self._refresh_buffer_seconds):
                return token

        raise LockTimeoutError(
            f"Could not acquire Xero refresh lock for connection {connection_id!r} "
            f"after {_CONTENTION_RETRIES} attempts."
        )
