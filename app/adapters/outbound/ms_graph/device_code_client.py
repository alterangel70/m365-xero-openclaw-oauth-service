"""
Microsoft OAuth 2.0 Device Code Flow client.

Handles the one-time device-code authorization and ongoing token refresh
for delegated (on-behalf-of-user) Microsoft Graph access.

Why device code flow for this service:
  The service is headless/backend-oriented.  The device code flow lets an
  operator authorize once using any browser (including on a different machine),
  after which the service holds a refresh token that it uses autonomously.
  This grants the delegated permissions (e.g. ChannelMessage.Send) that
  client_credentials cannot provide for Teams message posting.

Protocol overview:
  1. POST /oauth2/v2.0/devicecode  →  device_code, user_code, verification_uri
  2. Operator visits verification_uri, enters user_code, and logs in.
  3. Service polls  POST /oauth2/v2.0/token with device_code until the user
     completes the browser step (or the code expires after 15 minutes).
  4. On success the token endpoint returns access_token + refresh_token.
  5. Future calls use standard refresh_token grant (same as Xero's pattern).

Token refresh follows the same rotation-safe locking pattern used by
XeroTokenManager — see token_manager.py.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.core.domain.token import TokenSet
from app.core.errors import ConnectionExpiredError, ProviderUnavailableError

logger = logging.getLogger(__name__)

_AZURE_DEVICE_CODE_URL = (
    "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/devicecode"
)
_AZURE_TOKEN_URL = (
    "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
)

# Status values returned by the Azure polling endpoint.
_PENDING = "authorization_pending"
_SLOW_DOWN = "slow_down"


class DeviceCodePending(Exception):
    """Raised by poll_device_code when the user has not yet completed auth."""


class DeviceCodeExpired(Exception):
    """Raised by poll_device_code when the device code has expired."""


class MSDeviceCodeClient:
    """Manages Microsoft device-code authorization and delegated token refresh.

    This class is not a port implementation — it is an outbound infrastructure
    adapter used directly by MSTokenManager.  All dependencies are injected;
    no FastAPI imports, no app.state.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        tenant_id: str,
        client_id: str,
        scopes: str,
    ) -> None:
        self._http_client = http_client
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._scopes = scopes

    # ── Device code initiation ─────────────────────────────────────────────────

    async def start_device_code_flow(self) -> dict:
        """Request a device code from Azure and return the full response body.

        The caller (use case) surfaces ``user_code`` and ``verification_uri``
        to the operator.  ``device_code`` and ``interval`` are stored for the
        subsequent polling step.

        Returns a dict with at minimum:
          device_code, user_code, verification_uri, expires_in, interval, message
        """
        url = _AZURE_DEVICE_CODE_URL.format(tenant_id=self._tenant_id)
        response = await self._http_client.post(
            url,
            data={"client_id": self._client_id, "scope": self._scopes},
        )
        if not response.is_success:
            raise ProviderUnavailableError(
                f"Azure device code endpoint returned {response.status_code}: "
                f"{response.text[:300]}"
            )
        return response.json()

    # ── Polling ────────────────────────────────────────────────────────────────

    async def poll_device_code(self, device_code: str) -> TokenSet:
        """Poll the Azure token endpoint once for a completed device code.

        Call this in a loop with the interval recommended by Azure (typically
        5 seconds).  The caller controls the loop and sleep so that this method
        stays unit-testable without asyncio.sleep.

        Raises
        ------
        DeviceCodePending
            The user has not yet completed authorization.  Try again after
            ``interval`` seconds.
        DeviceCodeExpired
            The device code has expired (15-minute window elapsed).
        ProviderUnavailableError
            The token endpoint returned an unexpected error.
        """
        url = _AZURE_TOKEN_URL.format(tenant_id=self._tenant_id)
        response = await self._http_client.post(
            url,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": self._client_id,
                "device_code": device_code,
            },
        )
        body = response.json()

        if response.is_success:
            return self._parse_token_set(body)

        error = body.get("error", "")
        if error in (_PENDING, _SLOW_DOWN):
            raise DeviceCodePending(error)
        if error == "expired_token":
            raise DeviceCodeExpired("Device code has expired. Start a new flow.")
        if error == "authorization_declined":
            raise ConnectionExpiredError(
                "User declined the Microsoft authorization request."
            )

        raise ProviderUnavailableError(
            f"Azure token endpoint returned {response.status_code}: "
            f"{response.text[:300]}"
        )

    # ── Token refresh ──────────────────────────────────────────────────────────

    async def refresh_token(self, token_set: TokenSet) -> TokenSet:
        """Refresh a delegated Microsoft token using the stored refresh_token.

        Microsoft does NOT rotate refresh tokens on every refresh call
        (unlike Xero), but we always persist the response in case it does
        return a new refresh_token.

        Raises
        ------
        ConnectionExpiredError
            Azure rejected the refresh token — the operator must re-authorize
            via the device code flow.
        ProviderUnavailableError
            The token endpoint returned an unexpected error.
        """
        if token_set.refresh_token is None:
            raise ConnectionExpiredError(
                "No Microsoft refresh token stored. "
                "The device code authorization flow must be completed first."
            )

        url = _AZURE_TOKEN_URL.format(tenant_id=self._tenant_id)
        response = await self._http_client.post(
            url,
            data={
                "grant_type": "refresh_token",
                "client_id": self._client_id,
                "refresh_token": token_set.refresh_token,
                "scope": self._scopes,
            },
        )

        if not response.is_success:
            body = response.json()
            error = body.get("error", "")
            if error in ("invalid_grant", "interaction_required"):
                raise ConnectionExpiredError(
                    f"Microsoft refresh token rejected ({error}). "
                    "The operator must re-authorize via device code flow."
                )
            raise ProviderUnavailableError(
                f"Microsoft token refresh returned {response.status_code}: "
                f"{response.text[:300]}"
            )

        return self._parse_token_set(response.json())

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_token_set(body: dict) -> TokenSet:
        """Convert an Azure token response body into a domain TokenSet."""
        expires_at = datetime.now(tz=timezone.utc) + timedelta(
            seconds=int(body["expires_in"])
        )
        return TokenSet(
            access_token=body["access_token"],
            expires_at=expires_at,
            token_type=body.get("token_type", "Bearer"),
            refresh_token=body.get("refresh_token"),
            scope=body.get("scope"),
        )
