"""
Microsoft Graph token manager.

Acquires and caches a Microsoft access token using the client_credentials
(app-only) grant.  There is no user principal, no browser flow, and no
refresh token — the token is simply re-acquired from the Azure token
endpoint when it expires.

Why httpx directly instead of Authlib here:
  The client_credentials grant reduces to a single POST with four fields.
  Authlib's value is in managing auth_code flows, PKCE, refresh rotation,
  and CSRF state — none of which apply here.  Using httpx directly keeps
  the implementation transparent, easy to test, and dependency-light.
  Authlib is used in the Xero adapter (Phase 7) where it earns its place.

Token lifecycle:
  1. Load from Redis.  If valid (not within refresh_buffer_seconds of expiry),
     return immediately.
  2. Acquire the distributed refresh lock.
  3. Double-check Redis inside the lock — another worker may have refreshed
     while we waited to acquire.
  4. POST to the Azure token endpoint and store the resulting token.
  5. Release the lock and return the access token string.

On lock contention:
  If the lock is held by another worker, wait 100 ms and re-check Redis.
  Repeat up to 5 times before raising LockTimeoutError.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.core.domain.token import TokenSet
from app.core.errors import LockTimeoutError, ProviderUnavailableError
from app.core.ports.lock_manager import AbstractLockManager
from app.core.ports.token_store import AbstractTokenStore

logger = logging.getLogger(__name__)

_LOCK_TTL_SECONDS = 30
_CONTENTION_RETRIES = 5
_CONTENTION_SLEEP_SECONDS = 0.1


class MSTokenManager:
    """Manages Microsoft client_credentials access tokens.

    All dependencies are injected; no FastAPI imports, no app.state.
    The http_client lifetime is managed by the caller (DI layer).
    """

    def __init__(
        self,
        token_store: AbstractTokenStore,
        lock_manager: AbstractLockManager,
        http_client: httpx.AsyncClient,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        scopes: str,
        refresh_buffer_seconds: int,
    ) -> None:
        self._token_store = token_store
        self._lock_manager = lock_manager
        self._http_client = http_client
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._scopes = scopes
        self._refresh_buffer_seconds = refresh_buffer_seconds

    async def get_token(self, connection_id: str, force: bool = False) -> str:
        """Return a valid access token for the given connection.

        Parameters
        ----------
        connection_id:
            The Redis key namespace for this MS connection (e.g. "ms-default").
        force:
            When True, bypass the cache check and unconditionally re-acquire.
            Used by MSGraphClient after receiving a 401 from Graph.
        """
        if not force:
            cached = await self._token_store.load(connection_id)
            if cached and not cached.is_expired_or_near(self._refresh_buffer_seconds):
                return cached.access_token

        lock_key = f"lock:refresh:{connection_id}"

        for _attempt in range(_CONTENTION_RETRIES):
            async with self._lock_manager.acquire(lock_key, _LOCK_TTL_SECONDS) as acquired:
                if acquired:
                    # Double-check: another worker may have refreshed while
                    # we were waiting for this lock.
                    if not force:
                        cached = await self._token_store.load(connection_id)
                        if cached and not cached.is_expired_or_near(self._refresh_buffer_seconds):
                            return cached.access_token

                    new_token = await self._acquire_from_provider()
                    await self._token_store.store(connection_id, new_token)
                    logger.debug(
                        "MS token acquired for %s, expires %s",
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
            f"Could not acquire refresh lock for {connection_id!r} "
            f"after {_CONTENTION_RETRIES} attempts"
        )

    async def _acquire_from_provider(self) -> TokenSet:
        """POST to the Azure token endpoint and return a fresh TokenSet."""
        url = (
            f"https://login.microsoftonline.com/{self._tenant_id}"
            "/oauth2/v2.0/token"
        )
        data = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "scope": self._scopes,
        }
        response = await self._http_client.post(url, data=data)
        if not response.is_success:
            raise ProviderUnavailableError(
                f"Microsoft token endpoint returned {response.status_code}: "
                f"{response.text[:300]}"
            )
        body = response.json()
        expires_at = datetime.now(tz=timezone.utc) + timedelta(
            seconds=int(body["expires_in"])
        )
        return TokenSet(
            access_token=body["access_token"],
            expires_at=expires_at,
            token_type=body.get("token_type", "Bearer"),
            scope=body.get("scope"),
        )
