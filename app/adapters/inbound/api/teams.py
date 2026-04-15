"""
Teams routes: POST /v1/teams/messages and POST /v1/teams/approvals.

These are the only HTTP routes for Phase 6.  Both routes require the
INTERNAL_API_KEY header (enforced at router level via verify_api_key).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from app.adapters.inbound.api.dependencies import (
    get_send_teams_approval_card,
    get_send_teams_message,
)
from app.adapters.inbound.api.middleware import verify_api_key
from app.core.domain.teams import TeamsApprovalCard, TeamsMessage
from app.core.use_cases.teams import SendTeamsApprovalCard, SendTeamsMessage

router = APIRouter(
    prefix="/v1/teams",
    tags=["teams"],
    dependencies=[Depends(verify_api_key)],
)


# ── Request / Response models ─────────────────────────────────────────────────


class SendMessageRequest(BaseModel):
    connection_id: str
    team_id: str
    channel_id: str
    body_content: str
    content_type: str = "html"


class SendApprovalCardRequest(BaseModel):
    connection_id: str
    team_id: str
    channel_id: str
    title: str
    description: str
    approve_url: str
    reject_url: str
    metadata: dict[str, str] = Field(default_factory=dict)


class MessageResponse(BaseModel):
    message_id: str


# ── Routes ────────────────────────────────────────────────────────────────────


@router.post("/messages", response_model=MessageResponse)
async def send_message(
    body: SendMessageRequest,
    use_case: SendTeamsMessage = Depends(get_send_teams_message),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> MessageResponse:
    """Send a plain text or HTML message to a Teams channel.

    An optional Idempotency-Key header prevents duplicate messages when
    OpenClaw retries a request that already succeeded.
    """
    message = TeamsMessage(
        team_id=body.team_id,
        channel_id=body.channel_id,
        body_content=body.body_content,
        content_type=body.content_type,
    )
    result = await use_case.execute(
        connection_id=body.connection_id,
        message=message,
        idempotency_key=idempotency_key,
    )
    return MessageResponse(message_id=result.message_id)


@router.post("/approvals", response_model=MessageResponse)
async def send_approval(
    body: SendApprovalCardRequest,
    use_case: SendTeamsApprovalCard = Depends(get_send_teams_approval_card),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> MessageResponse:
    """Send an Adaptive Card with Approve / Reject buttons to a Teams channel.

    The approve_url and reject_url are opaque to this service.  They are
    embedded as Action.OpenUrl targets; clicking them opens the URL in the
    user's browser.  OpenClaw owns what those URLs do.

    An optional Idempotency-Key header prevents duplicate cards on retries.
    """
    card = TeamsApprovalCard(
        team_id=body.team_id,
        channel_id=body.channel_id,
        title=body.title,
        description=body.description,
        approve_url=body.approve_url,
        reject_url=body.reject_url,
        metadata=body.metadata,
    )
    result = await use_case.execute(
        connection_id=body.connection_id,
        card=card,
        idempotency_key=idempotency_key,
    )
    return MessageResponse(message_id=result.message_id)
