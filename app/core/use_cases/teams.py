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
        raise NotImplementedError


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
        raise NotImplementedError
