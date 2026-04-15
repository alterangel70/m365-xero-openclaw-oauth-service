"""
Xero OAuth 2.0 client (authorization_code grant, confidential client).

Uses Authlib's OAuth2Session to handle the authorization URL construction,
code-exchange POST, token refresh, and revocation.  All calls are async
via the httpx integration (authlib.integrations.httpx_client.AsyncOAuth2Client).

Why Authlib here but not for Microsoft:
  The authorization_code flow involves multiple round-trips (auth URL
  construction, code exchange), CSRF state binding, and — for Xero —
  rotating refresh tokens.  Authlib handles all of these correctly and
  its httpx integration gives us non-blocking I/O without extra ceremony.
  For Microsoft's client_credentials grant a plain httpx POST is simpler
  and more transparent.

Xero-specific behaviour:
  * After exchanging the authorization code, Xero's /connections endpoint
    is called to retrieve the authorized tenant ID, which is stored in the
    returned TokenSet.xero_tenant_id.
  * Xero rotates refresh tokens on every refresh call; the old token is
    invalid immediately.  This is handled correctly here — callers must
    persist the new TokenSet returned by refresh_token() before making any
    further Xero API calls with the old refresh token.
  * Revocation POSTs to https://identity.xero.com/connect/revocation with
    the access_token.  Failures are caught and logged; they do not propagate.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client

from app.core.domain.token import TokenSet
from app.core.errors import ConnectionExpiredError, ProviderUnavailableError
from app.core.ports.oauth_client import AbstractOAuthClient

logger = logging.getLogger(__name__)

_XERO_TOKEN_URL = "https://identity.xero.com/connect/token"
_XERO_AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"
_XERO_REVOKE_URL = "https://identity.xero.com/connect/revocation"
_XERO_CONNECTIONS_URL = "https://api.xero.com/connections"


class XeroAuthlibOAuthClient(AbstractOAuthClient):
    """Implements the Xero OAuth 2.0 authorization_code flow.

    All dependencies are injected; no FastAPI imports, no app.state.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scopes: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._scopes = scopes
        # The shared httpx client is used for the /connections call and
        # revocation.  Token exchange and refresh go through Authlib's
        # AsyncOAuth2Client (a thin httpx wrapper).
        self._http_client = http_client

    # ── AbstractOAuthClient ────────────────────────────────────────────────────

    def build_authorization_url(self, state: str) -> str:
        """Return the Xero authorization URL for the user to visit."""
        client = self._make_authlib_client()
        url, _ = client.create_authorization_url(
            _XERO_AUTHORIZE_URL,
            state=state,
        )
        return url

    async def exchange_code(self, code: str, state: str) -> TokenSet:
        """Exchange an authorization code for a Xero TokenSet.

        Also fetches xero_tenant_id from the /connections endpoint so the
        caller does not need to perform this step separately.

        Raises ProviderUnavailableError if the exchange or tenant fetch fails.
        """
        client = self._make_authlib_client()
        try:
            raw = await client.fetch_token(
                _XERO_TOKEN_URL,
                code=code,
                redirect_uri=self._redirect_uri,
            )
        except Exception as exc:
            raise ProviderUnavailableError(
                f"Xero token exchange failed: {exc}"
            ) from exc

        access_token: str = raw["access_token"]
        tenant_id = await self._fetch_tenant_id(access_token)
        return _raw_to_token_set(raw, tenant_id)

    async def refresh_token(self, token_set: TokenSet) -> TokenSet:
        """Refresh the Xero access token using the stored refresh token.

        Xero rotates refresh tokens: the returned TokenSet contains a brand-new
        refresh_token and the old one is no longer valid.

        Raises ConnectionExpiredError if Xero rejects the refresh token (400/401).
        Raises ProviderUnavailableError on other failures.
        """
        if not token_set.refresh_token:
            raise ConnectionExpiredError(
                "Cannot refresh Xero token: no refresh_token stored."
            )

        client = self._make_authlib_client()
        try:
            raw = await client.refresh_token(
                _XERO_TOKEN_URL,
                refresh_token=token_set.refresh_token,
            )
        except Exception as exc:
            # Authlib raises OAuthError (subclass of Exception) when the
            # provider returns an error response.  400/401 === revoked token.
            msg = str(exc).lower()
            if "invalid_grant" in msg or "unauthorized" in msg or "401" in msg:
                raise ConnectionExpiredError(
                    f"Xero refresh token rejected (connection must be re-authorized): {exc}"
                ) from exc
            raise ProviderUnavailableError(
                f"Xero token refresh failed: {exc}"
            ) from exc

        return _raw_to_token_set(raw, token_set.xero_tenant_id)

    async def revoke_token(self, token_set: TokenSet) -> None:
        """Revoke the Xero access token.  Best-effort; errors are only logged."""
        try:
            resp = await self._http_client.post(
                _XERO_REVOKE_URL,
                data={"token": token_set.access_token},
                auth=(self._client_id, self._client_secret),
            )
            if not resp.is_success:
                logger.warning(
                    "Xero token revocation returned %s: %s",
                    resp.status_code,
                    resp.text[:200],
                )
        except Exception as exc:
            logger.warning("Xero token revocation request failed: %s", exc)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _make_authlib_client(self) -> AsyncOAuth2Client:
        """Create a fresh AsyncOAuth2Client for a single operation.

        A new client is created per-call so that token state stored inside
        the Authlib session object does not leak between requests.
        """
        return AsyncOAuth2Client(
            client_id=self._client_id,
            client_secret=self._client_secret,
            scope=self._scopes,
            redirect_uri=self._redirect_uri,
        )

    async def _fetch_tenant_id(self, access_token: str) -> str | None:
        """Call Xero /connections to find the authorized tenant ID.

        Xero returns a list of connections; we take the first one (Xero
        accounts typically have a single accounting tenant per authorization).
        Returns None if the list is empty or the request fails.
        """
        try:
            resp = await self._http_client.get(
                _XERO_CONNECTIONS_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if resp.is_success:
                connections = resp.json()
                if connections:
                    return connections[0].get("tenantId")
            else:
                logger.warning(
                    "Xero /connections returned %s — tenant_id will be None",
                    resp.status_code,
                )
        except Exception as exc:
            logger.warning("Xero /connections request failed: %s", exc)
        return None


# ── Module-level helpers ───────────────────────────────────────────────────────


def _raw_to_token_set(raw: dict, xero_tenant_id: str | None) -> TokenSet:
    """Convert an Authlib token dict to a TokenSet value object.

    Authlib surfaces ``expires_at`` as a UNIX timestamp float when the
    provider returns ``expires_in``.  We convert it to an aware UTC datetime.
    """
    expires_at_unix: float | None = raw.get("expires_at")
    if expires_at_unix is not None:
        expires_at = datetime.fromtimestamp(expires_at_unix, tz=timezone.utc)
    else:
        # Fallback: expires_in may be present without expires_at.
        expires_in = int(raw.get("expires_in", 1800))
        expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=expires_in)

    return TokenSet(
        access_token=raw["access_token"],
        refresh_token=raw.get("refresh_token"),
        expires_at=expires_at,
        token_type=raw.get("token_type", "Bearer"),
        scope=raw.get("scope"),
        xero_tenant_id=xero_tenant_id,
    )
