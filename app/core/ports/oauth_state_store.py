from abc import ABC, abstractmethod


class AbstractOAuthStateStore(ABC):
    """Port for temporarily holding OAuth state values during the Xero authorization dance.

    When the user is redirected to Xero, a random state value is generated and
    saved here.  On callback, the state is retrieved and deleted atomically to
    confirm the response is genuine and to get back the connection_id.

    Key pattern: oauth:state:{state}
    """

    @abstractmethod
    async def save(
        self,
        state: str,
        connection_id: str,
        ttl_seconds: int,
    ) -> None:
        """Persist the state → connection_id mapping with an expiry."""

    @abstractmethod
    async def pop(self, state: str) -> str | None:
        """Return the connection_id for the given state and delete it atomically.

        Returns None if the state has expired or was never stored.
        The atomic delete prevents replay of the same state value.
        """
