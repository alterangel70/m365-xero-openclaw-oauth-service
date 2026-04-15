from abc import ABC, abstractmethod

from app.core.domain.teams import TeamsApprovalCard, TeamsMessage


class AbstractTeamsClient(ABC):
    """Port for outbound Microsoft Teams / Graph API calls.

    Both methods accept domain objects so the serialization to provider wire
    format is fully encapsulated inside the adapter implementation.
    Token acquisition and refresh are also encapsulated; callers never handle
    credentials directly.
    """

    @abstractmethod
    async def send_message(
        self,
        connection_id: str,
        message: TeamsMessage,
    ) -> dict:
        """Send a plain text or HTML message to a Teams channel.

        Returns the raw Graph API response body as a dict.
        Raises ProviderUnavailableError on unrecoverable provider errors.
        """

    @abstractmethod
    async def send_adaptive_card(
        self,
        connection_id: str,
        card: TeamsApprovalCard,
    ) -> dict:
        """Send an Adaptive Card to a Teams channel.

        The adapter is responsible for serializing the domain object to the
        provider's wire format (Graph chatMessage + Adaptive Card JSON).
        Returns the raw Graph API response body as a dict.
        Raises ProviderUnavailableError on unrecoverable provider errors.
        """
