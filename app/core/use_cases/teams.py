"""
Teams use cases: send messages and Adaptive Cards to Microsoft Teams.

Token acquisition is fully handled by the MSGraphClient adapter.
These use cases do not interact with the token store directly.
"""

from __future__ import annotations

import logging

from app.core.domain.teams import TeamsApprovalCard, TeamsMessage
from app.core.ports.idempotency_store import AbstractIdempotencyStore
from app.core.ports.teams_client import AbstractTeamsClient
from app.core.use_cases.results import MessageResult

logger = logging.getLogger(__name__)

# Stable operation name prefixes used as part of the idempotency Redis key.
_OP_SEND_MESSAGE = "send_teams_message"
_OP_SEND_APPROVAL = "send_teams_approval_card"


class SendTeamsMessage:
    """Send a plain text or HTML message to a Teams channel.

    If an idempotency_key is provided and a result is already cached for that
    key, the cached message_id is returned without re-sending to Teams.
    """

    def __init__(
        self,
        teams_client: AbstractTeamsClient,
        idempotency_store: AbstractIdempotencyStore,
        idempotency_ttl_seconds: int,
    ) -> None:
        self._teams_client = teams_client
        self._idempotency_store = idempotency_store
        self._idempotency_ttl_seconds = idempotency_ttl_seconds

    async def execute(
        self,
        connection_id: str,
        message: TeamsMessage,
        idempotency_key: str | None = None,
    ) -> MessageResult:
        idem_key = (
            f"idempotency:{_OP_SEND_MESSAGE}:{idempotency_key}"
            if idempotency_key
            else None
        )

        if idem_key:
            cached = await self._idempotency_store.get(idem_key)
            if cached:
                logger.debug("Idempotency cache hit for key %r", idempotency_key)
                return MessageResult(message_id=cached["message_id"])

        response = await self._teams_client.send_message(
            connection_id=connection_id,
            message=message,
        )
        result = MessageResult(message_id=response["id"])

        if idem_key:
            await self._idempotency_store.set(
                idem_key,
                {"message_id": result.message_id},
                self._idempotency_ttl_seconds,
            )

        return result


class SendTeamsApprovalCard:
    """Send an Adaptive Card with Approve / Reject buttons to a Teams channel.

    The approve_url and reject_url are embedded as Action.OpenUrl targets.
    When a Teams user clicks a button, their browser is directed to that URL.
    This service is not involved in that flow after the card is sent.
    """

    def __init__(
        self,
        teams_client: AbstractTeamsClient,
        idempotency_store: AbstractIdempotencyStore,
        idempotency_ttl_seconds: int,
    ) -> None:
        self._teams_client = teams_client
        self._idempotency_store = idempotency_store
        self._idempotency_ttl_seconds = idempotency_ttl_seconds

    async def execute(
        self,
        connection_id: str,
        card: TeamsApprovalCard,
        idempotency_key: str | None = None,
    ) -> MessageResult:
        idem_key = (
            f"idempotency:{_OP_SEND_APPROVAL}:{idempotency_key}"
            if idempotency_key
            else None
        )

        if idem_key:
            cached = await self._idempotency_store.get(idem_key)
            if cached:
                logger.debug("Idempotency cache hit for key %r", idempotency_key)
                return MessageResult(message_id=cached["message_id"])

        response = await self._teams_client.send_adaptive_card(
            connection_id=connection_id,
            card=card,
        )
        result = MessageResult(message_id=response["id"])

        if idem_key:
            await self._idempotency_store.set(
                idem_key,
                {"message_id": result.message_id},
                self._idempotency_ttl_seconds,
            )

        return result
