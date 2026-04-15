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

from app.core.ports.oauth_client import AbstractOAuthClient
from app.core.ports.oauth_state_store import AbstractOAuthStateStore
from app.core.ports.token_store import AbstractTokenStore
from app.core.use_cases.results import AuthUrlResult, ConnectionStatus

logger = logging.getLogger(__name__)


class BuildXeroAuthorizationUrl:
    """Generate a Xero OAuth authorization URL for the one-time setup flow.

    The caller (typically an admin or setup script) opens the returned URL
    in a browser.  Xero redirects back to the callback endpoint after consent.
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
        raise NotImplementedError


class HandleXeroOAuthCallback:
    """Exchange the authorization code received from Xero for a token set.

    Validates the state parameter, exchanges the code, and persists the
    resulting TokenSet (including xero_tenant_id) in the token store.
    The concrete XeroAuthlibOAuthClient.exchange_code() handles the
    Xero /connections call to populate xero_tenant_id transparently.
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

    async def execute(self, code: str, state: str) -> None:
        raise NotImplementedError


class GetConnectionStatus:
    """Return the validity state of a stored provider connection.

    Possible statuses:
      valid   — a token exists and has not expired (within buffer)
      expired — a token exists but is past its expiry
      missing — no token is stored for this connection_id

    For Microsoft, "missing" effectively means the first client_credentials
    acquisition has not yet succeeded.  Under normal operation this resolves
    automatically on the next Teams call.

    The refresh_buffer_seconds is used to determine if a token is
    "effectively expired" even if technically still within its lifetime.
    """

    def __init__(
        self,
        token_store: AbstractTokenStore,
        refresh_buffer_seconds: int,
    ) -> None:
        self._token_store = token_store
        self._refresh_buffer_seconds = refresh_buffer_seconds

    async def execute(self, connection_id: str) -> ConnectionStatus:
        raise NotImplementedError


class RevokeConnection:
    """Delete a stored connection, optionally revoking it at the provider.

    For Xero, if an oauth_client is provided, the refresh token is revoked
    at the Xero token revocation endpoint before local deletion.
    Revocation failure is logged but does not prevent local token deletion.

    For Microsoft, there is no token to revoke at the provider level
    (client_credentials tokens are not user-bound).  Pass oauth_client=None.
    """

    def __init__(
        self,
        token_store: AbstractTokenStore,
        oauth_client: AbstractOAuthClient | None = None,
    ) -> None:
        self._token_store = token_store
        self._oauth_client = oauth_client

    async def execute(self, connection_id: str) -> None:
        raise NotImplementedError
