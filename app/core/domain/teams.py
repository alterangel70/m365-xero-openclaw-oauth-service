from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class TeamsMessage:
    """Represents a plain text or HTML message to be sent to a Teams channel."""

    team_id: str
    channel_id: str
    body_content: str
    content_type: Literal["text", "html"] = "html"


@dataclass(frozen=True)
class TeamsApprovalCard:
    """An Adaptive Card sent to a Teams channel that presents an approval decision.

    The approve_url and reject_url are owned entirely by OpenClaw.
    They are embedded as Action.OpenUrl targets; when clicked, Teams opens
    the URL in the user's browser.  This service treats them as opaque strings
    and does not validate or call them.
    """

    team_id: str
    channel_id: str
    title: str
    description: str
    approve_url: str
    reject_url: str
    # Arbitrary key/value pairs rendered inside the card body for context.
    metadata: dict[str, str] = field(default_factory=dict)
