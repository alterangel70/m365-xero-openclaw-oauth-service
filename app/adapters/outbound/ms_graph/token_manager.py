"""
Microsoft Graph token manager (delegated, device-code flow).

Manages loading, validating, and refreshing delegated Microsoft access tokens.
The initial token is obtained once via the device code flow
(MSDeviceCodeClient.start_device_code_flow + poll_device_code).
Subsequent calls use the stored refresh_token to silently obtain new
access tokens without operator involvement.

Token lifecycle:
  1. Load from Redis.  If valid (not within refresh_buffer_seconds of expiry),
     return immediately.
  2. Acquire the distributed refresh lock.
  3. Double-check Redis inside the lock — another worker may have refreshed
     while we waited to acquire.
  4. Call MSDeviceCodeClient.refresh_token() and store the new token.
  5. Release the lock and return the access token string.

On lock contention:
  If the lock is held by another worker, wait 100 ms and re-check Redis.
  Repeat up to 5 times before raising LockTimeoutError.

On expired / missing token:
  If no token is present in Redis (flow not yet completed) or the refresh
  token is rejected by Azure, ConnectionMissingError or ConnectionExpiredError
  is raised.  The operator must re-run the device code flow.
"""

from __future__ import annotations

import asyncio
import logging

from app.core.errors import ConnectionExpiredError, ConnectionMissingError, LockTimeoutError
from app.core.ports.lock_manager import AbstractLockManager
from app.core.ports.token_store import AbstractTokenStore

from .device_code_client import MSDeviceCodeClient

logger = logging.getLogger(__name__)

_LOCK_TTL_SECONDS = 30
_CONTENTION_RETRIES = 5
_CONTENTION_SLEEP_SECONDS = 0.1


class MSTokenManager:
    """Manages delegated Microsoft access tokens with rotation-safe refresh.

    All dependencies are injected; no FastAPI imports, no app.state.
    """

    def __init__(
        self,
        token_store: AbstractTokenStore,
        lock_manager: AbstractLockManager,
        device_code_client: MSDeviceCodeClient,
        refresh_buffer_seconds: int,
    ) -> None:
        self._token_store = token_store
        self._lock_manager = lock_manager
        self._device_code_client = device_code_client
        self._refresh_buffer_seconds = refresh_buffer_seconds

    async def get_token(self, connection_id: str, force: bool = False) -> str:
        """Return a valid delegated access token for the given connection.

        Parameters
        ----------
        connection_id:
            The Redis key namespace for this MS connection (e.g. "ms-default").
        force:
            When True, bypass the cache check and force a refresh.
            Used by MSGraphClient after receiving a 401 from Graph.

        Raises
        ------
        ConnectionMissingError
            No token found — device code flow has not been completed.
        ConnectionExpiredError
            Azure rejected the refresh token — re-authorization required.
        LockTimeoutError
            Distributed refresh lock could not be acquired.
        """
        if not force:
            cached = await self._token_store.load(connection_id)
            if cached is None:
                raise ConnectionMissingError(
                    f"No Microsoft token found for connection {connection_id!r}. "
                    "Complete the device code authorization flow first."
                )
            if not cached.is_expired_or_near(self._refresh_buffer_seconds):
                return cached.access_token

        # Token is stale or force-refresh requested — refresh under lock.
        return await self._refresh_under_lock(connection_id, force=force)

    # ── Private ────────────────────────────────────────────────────────────────

    async def _refresh_under_lock(self, connection_id: str, force: bool) -> str:
        lock_key = f"lock:refresh:{connection_id}"

        for _attempt in range(_CONTENTION_RETRIES):
            async with self._lock_manager.acquire(lock_key, _LOCK_TTL_SECONDS) as acquired:
                if acquired:
                    # Double-check: another worker may have refreshed while we waited.
                    if not force:
                        cached = await self._token_store.load(connection_id)
                        if cached and not cached.is_expired_or_near(self._refresh_buffer_seconds):
                            return cached.access_token

                    # Load the current token (needed for its refresh_token field).
                    current = await self._token_store.load(connection_id)
                    if current is None:
                        raise ConnectionMissingError(
                            f"No Microsoft token found for connection {connection_id!r}. "
                            "Complete the device code authorization flow first."
                        )

                    # ConnectionExpiredError propagates to the caller if Azure
                    # rejects the refresh token.
                    new_token = await self._device_code_client.refresh_token(current)
                    await self._token_store.store(connection_id, new_token)
                    logger.info(
                        "MS token refreshed for %r, expires %s",
                        connection_id,
                        new_token.expires_at.isoformat(),
                    )
                    return new_token.access_token

            # Lock held by another worker — wait briefly then re-check Redis.
            await asyncio.sleep(_CONTENTION_SLEEP_SECONDS)

            if not force:
                cached = await self._token_store.load(connection_id)
                if cached and not cached.is_expired_or_near(self._refresh_buffer_seconds):
                    return cached.access_token

        raise LockTimeoutError(
            f"Could not acquire MS refresh lock for {connection_id!r} "
            f"after {_CONTENTION_RETRIES} attempts"
        )
