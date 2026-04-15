from abc import ABC, abstractmethod

from app.core.domain.token import TokenSet


class AbstractTokenStore(ABC):
    """Port for persisting and retrieving provider token sets.

    Implementations store token data keyed by connection_id.
    The Redis implementation uses the key pattern: token:{connection_id}
    """

    @abstractmethod
    async def store(self, connection_id: str, token_set: TokenSet) -> None:
        """Persist a token set, replacing any existing value atomically."""

    @abstractmethod
    async def load(self, connection_id: str) -> TokenSet | None:
        """Return the stored token set, or None if not found."""

    @abstractmethod
    async def delete(self, connection_id: str) -> None:
        """Delete the stored token set.  No-op if the key does not exist."""
