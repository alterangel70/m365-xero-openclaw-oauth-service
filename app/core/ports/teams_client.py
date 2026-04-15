from abc import ABC, abstractmethod


class AbstractTeamsClient(ABC):
    """Port for outbound Microsoft Teams / Graph API calls.

    Token acquisition and refresh are fully encapsulated within the adapter
    implementation (MSGraphClient).  Callers pass only the connection_id;
    the adapter resolves and manages the access token internally.
    """

    @abstractmethod
    async def send_message(
        self,
        connection_id: str,
        team_id: str,
        channel_id: str,
        body: str,
        content_type: str,
    ) -> dict:
        """Send a plain text or HTML message to a Teams channel.

        Returns the raw Graph API response body as a dict.
        Raises ProviderUnavailableError on unrecoverable provider errors.
        """

    @abstractmethod
    async def send_adaptive_card(
        self,
        connection_id: str,
        team_id: str,
        channel_id: str,
        card_payload: dict,
    ) -> dict:
        """Send an Adaptive Card to a Teams channel.

        card_payload is the full Adaptive Card JSON as a Python dict.
        Returns the raw Graph API response body as a dict.
        Raises ProviderUnavailableError on unrecoverable provider errors.
        """
