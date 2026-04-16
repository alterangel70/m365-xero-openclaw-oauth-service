"""
OAuth and connection management use cases.

BuildXeroAuthorizationUrl and HandleXeroOAuthCallback cover the one-time
Xero authorization setup.  GetConnectionStatus and RevokeConnection apply
to both Microsoft and Xero connections.

Microsoft has no OAuth use cases: its client_credentials token is acquired
automatically by the MSGraphClient adapter on demand.
"""

from __future__ import annotations

import logging
import secrets

from app.core.errors import ConnectionMissingError
from app.core.ports.oauth_client import AbstractOAuthClient
from app.core.ports.oauth_state_store import AbstractOAuthStateStore
from app.core.ports.token_store import AbstractTokenStore
from app.core.use_cases.results import AuthUrlResult, ConnectionStatus

logger = logging.getLogger(__name__)


class BuildXeroAuthorizationUrl:
    """Generate a Xero OAuth authorization URL for the one-time setup flow.

    A cryptographically random state value is generated, stored in the
    OAuth state store (keyed to connection_id), and embedded in the URL.
    The callback handler validates this state before accepting the code.
    """

    def __init__(
        self,
        oauth_client: AbstractOAuthClient,
        state_store: AbstractOAuthStateStore,
        oauth_state_ttl_seconds: int,
    ) -> None:
        self._oauth_client = oauth_client
        self._state_store = state_store
        self._oauth_state_ttl_seconds = oauth_state_ttl_seconds

    async def execute(self, connection_id: str) -> AuthUrlResult:
        state = secrets.token_urlsafe(32)
        await self._state_store.save(
            state=state,
            connection_id=connection_id,
            ttl_seconds=self._oauth_state_ttl_seconds,
        )
        url = self._oauth_client.build_authorization_url(state=state)
        logger.info(
            "Built Xero authorization URL for connection %r (state=%s…)",
            connection_id,
            state[:8],
        )
        return AuthUrlResult(authorization_url=url, state=state)


class HandleXeroOAuthCallback:
    """Exchange the authorization code received from Xero for a token set.

    Validates the state parameter against the state store, exchanges the code
    for an access + refresh token pair, fetches the Xero tenant ID, and
    persists the full TokenSet in the token store.

    Raises ConnectionMissingError if the state is unknown or expired (prevents
    CSRF and replay attacks).
    """

    def __init__(
        self,
        oauth_client: AbstractOAuthClient,
        state_store: AbstractOAuthStateStore,
        token_store: AbstractTokenStore,
    ) -> None:
        self._oauth_client = oauth_client
        self._state_store = state_store
        self._token_store = token_store

    async def execute(self, code: str, state: str) -> str:
        """Exchange the code and store the resulting token.

        Returns the connection_id the token was stored under.
        Raises ConnectionMissingError if the state is invalid or expired.
        """
        connection_id = await self._state_store.pop(state)
        if connection_id is None:
            raise ConnectionMissingError(
                "OAuth state is invalid or expired. "
                "Please restart the authorization flow."
            )

        token_set = await self._oauth_client.exchange_code(code=code, state=state)
        await self._token_store.store(connection_id, token_set)
        logger.info(
            "Xero token stored for connection %r (tenant=%s)",
            connection_id,
            token_set.xero_tenant_id,
        )
        return connection_id


class GetConnectionStatus:
    """Return the validity state of a stored provider connection.

    Possible statuses (see ConnectionStatus):
      valid   — a token exists and is not within refresh_buffer_seconds of expiry
      expired — a token exists but is at or past the refresh threshold
      missing — no token is stored for this connection_id
    """

    def __init__(
        self,
        token_store: AbstractTokenStore,
        refresh_buffer_seconds: int,
    ) -> None:
        self._token_store = token_store
        self._refresh_buffer_seconds = refresh_buffer_seconds

    async def execute(self, connection_id: str) -> ConnectionStatus:
        token = await self._token_store.load(connection_id)
        if token is None:
            return ConnectionStatus(status="missing")
        if token.is_expired_or_near(self._refresh_buffer_seconds):
            return ConnectionStatus(status="expired")
        return ConnectionStatus(status="valid")


class RevokeConnection:
    """Delete a stored connection, optionally revoking it at the provider.

    For Xero, if an oauth_client is provided, the access token is revoked at
    the Xero revocation endpoint before local deletion.  Revocation failure
    is logged but does not prevent local token deletion.

    For Microsoft, there is no user-bound token to revoke.  Pass oauth_client=None.
    """

    def __init__(
        self,
        token_store: AbstractTokenStore,
        oauth_client: AbstractOAuthClient | None = None,
    ) -> None:
        self._token_store = token_store
        self._oauth_client = oauth_client

    async def execute(self, connection_id: str) -> None:
        token = await self._token_store.load(connection_id)

        if token is not None and self._oauth_client is not None:
            # Best-effort revocation; errors are caught inside revoke_token()
            await self._oauth_client.revoke_token(token)

        await self._token_store.delete(connection_id)
        logger.info("Connection %r revoked and token deleted.", connection_id)
