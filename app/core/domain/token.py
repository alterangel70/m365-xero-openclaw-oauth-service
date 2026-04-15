from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class TokenSet:
    """An immutable snapshot of an OAuth token for one provider connection.

    This value object is the single token representation used throughout
    the service.  It is never serialized to the external API surface —
    it travels only between the token store, token managers, and adapters.

    Fields
    ------
    access_token:
        The bearer token sent to the provider on every API call.
    refresh_token:
        Present for Xero (authorization_code grant).
        Absent (None) for Microsoft (client_credentials grant).
    expires_at:
        Absolute UTC datetime at which the access_token expires.
        Always stored as a timezone-aware datetime.
    token_type:
        Always "Bearer" in practice; kept for protocol compliance.
    scope:
        Space-separated list of granted scopes, if returned by the provider.
    xero_tenant_id:
        Populated for Xero connections after the OAuth callback.
        None for Microsoft connections.
    """

    access_token: str
    expires_at: datetime  # UTC-aware
    token_type: str = "Bearer"
    refresh_token: str | None = None
    scope: str | None = None
    xero_tenant_id: str | None = None

    def is_expired_or_near(self, buffer_seconds: int) -> bool:
        """Return True if the token has expired or will expire within buffer_seconds.

        Used by token managers to decide whether a proactive refresh is needed
        before making a provider call.
        """
        threshold = datetime.now(tz=timezone.utc) + timedelta(seconds=buffer_seconds)
        return self.expires_at <= threshold
