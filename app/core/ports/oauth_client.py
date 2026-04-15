from abc import ABC, abstractmethod

from app.core.domain.token import TokenSet


class AbstractOAuthClient(ABC):
    """Port for Xero OAuth 2.0 authorization_code flow operations.

    This port has no Microsoft implementation.  MS token acquisition is
    handled internally by MSTokenManager (an adapter-layer concern) using
    the client_credentials grant, which requires no user interaction and
    no authorization URL.

    The concrete implementation (XeroAuthlibOAuthClient) holds provider
    credentials (client_id, client_secret, redirect_uri, scopes) and is
    responsible for building correct request parameters.
    """

    @abstractmethod
    def build_authorization_url(self, state: str) -> str:
        """Return the provider's authorization URL for the user to visit.

        state is a cryptographically random, opaque string that must be
        verified on callback to prevent CSRF.
        """

    @abstractmethod
    async def exchange_code(self, code: str, state: str) -> TokenSet:
        """Exchange an authorization code for a token set.

        For Xero, the implementation also calls the Xero /connections endpoint
        to discover the authorized tenant ID and stores it in the returned
        TokenSet.xero_tenant_id.  The use case does not need to handle this
        step separately.

        Raises ProviderUnavailableError if the provider rejects the exchange.
        """

    @abstractmethod
    async def refresh_token(self, token_set: TokenSet) -> TokenSet:
        """Request a new token set using the stored refresh token.

        Xero rotates refresh tokens on every use.  The returned TokenSet
        contains both a new access_token and a new refresh_token; the old
        refresh_token is invalid immediately after this call.

        Raises ConnectionExpiredError if the provider rejects the refresh token.
        """

    @abstractmethod
    async def revoke_token(self, token_set: TokenSet) -> None:
        """Revoke the token at the provider, invalidating the session.

        Best-effort; failures are logged but do not prevent local token deletion.
        """
