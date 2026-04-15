"""
Microsoft Graph Teams client.

Implements AbstractTeamsClient by sending channel messages and Adaptive Cards
via the Microsoft Graph API.  Token acquisition is fully delegated to
MSTokenManager — this class never touches Redis or OAuth credentials.

On receiving a 401 (invalid_token) from Graph, the client requests a forced
token re-acquisition and retries exactly once.  Any other non-2xx response
raises ProviderUnavailableError.
"""

from __future__ import annotations

import json
import logging
import uuid

import httpx

from app.core.domain.teams import TeamsApprovalCard, TeamsMessage
from app.core.errors import ProviderUnavailableError
from app.core.ports.teams_client import AbstractTeamsClient

from .card_builder import build_approval_card
from .token_manager import MSTokenManager

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class MSGraphClient(AbstractTeamsClient):
    """Sends Teams messages and Adaptive Cards via Microsoft Graph.

    All dependencies are injected; no FastAPI imports, no app.state.
    """

    def __init__(
        self,
        token_manager: MSTokenManager,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._token_manager = token_manager
        self._http_client = http_client

    async def send_message(
        self,
        connection_id: str,
        message: TeamsMessage,
    ) -> dict:
        """Send a plain text or HTML message to a Teams channel."""
        payload = {
            "body": {
                "contentType": message.content_type,
                "content": message.body_content,
            }
        }
        return await self._post_to_channel(
            connection_id, message.team_id, message.channel_id, payload
        )

    async def send_adaptive_card(
        self,
        connection_id: str,
        card: TeamsApprovalCard,
    ) -> dict:
        """Send an Adaptive Card to a Teams channel.

        The card domain object is serialized to Adaptive Card JSON by
        card_builder.build_approval_card(), then wrapped in the Graph
        chatMessage attachment structure required by the API.

        A unique attachment ID ties the HTML placeholder in the message body
        to the actual card content in the attachments list.
        """
        attachment_id = str(uuid.uuid4())
        card_json = build_approval_card(card)
        payload = {
            "body": {
                "contentType": "html",
                # Graph requires this specific HTML placeholder syntax.
                "content": f'<attachment id="{attachment_id}"></attachment>',
            },
            "attachments": [
                {
                    "id": attachment_id,
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    # Graph expects the Adaptive Card JSON as a string, not a dict.
                    "content": json.dumps(card_json),
                }
            ],
        }
        return await self._post_to_channel(
            connection_id, card.team_id, card.channel_id, payload
        )

    async def _post_to_channel(
        self,
        connection_id: str,
        team_id: str,
        channel_id: str,
        payload: dict,
    ) -> dict:
        """POST a message payload to the Graph channel messages endpoint.

        Retries exactly once if Graph returns 401, forcing a token
        re-acquisition before the retry attempt.
        """
        url = f"{_GRAPH_BASE}/teams/{team_id}/channels/{channel_id}/messages"

        for attempt in range(2):
            token = await self._token_manager.get_token(
                connection_id,
                force=(attempt > 0),  # force re-acquire on the retry pass
            )
            response = await self._http_client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )

            if response.status_code == 401 and attempt == 0:
                logger.warning(
                    "Graph returned 401 for connection %r — forcing token refresh",
                    connection_id,
                )
                continue  # retry with forced token re-acquisition

            if not response.is_success:
                raise ProviderUnavailableError(
                    f"Microsoft Graph returned {response.status_code}: "
                    f"{response.text[:300]}"
                )

            logger.debug(
                "Teams message sent to team=%s channel=%s", team_id, channel_id
            )
            return response.json()

        # Reached only if both attempts returned 401.
        raise ProviderUnavailableError(
            f"Microsoft Graph returned 401 after token re-acquisition "
            f"(connection={connection_id!r}, team={team_id!r})"
        )
