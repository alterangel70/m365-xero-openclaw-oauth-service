"""
Adaptive Card builder for Teams approval cards.

Converts a TeamsApprovalCard domain object into the Microsoft Adaptive Card
JSON structure (schema 1.4) expected by the Graph API chatMessage endpoint.

This is an adapter-layer concern: the serialization format is specific to
Microsoft Graph and must not leak into the core domain or use cases.
"""

from app.core.domain.teams import TeamsApprovalCard


def build_approval_card(card: TeamsApprovalCard) -> dict:
    """Return a schema 1.4 Adaptive Card payload for an approval request.

    Structure:
      - Title (bold, medium)
      - Description (wrapping text block)
      - FactSet of metadata key/value pairs (omitted when metadata is empty)
      - Two Action.OpenUrl buttons: Approve and Reject

    The approve_url and reject_url are embedded verbatim as action URLs.
    Teams opens them in the user's browser when clicked; this service is
    not involved in what those URLs do.
    """
    body: list[dict] = [
        {
            "type": "TextBlock",
            "text": card.title,
            "weight": "Bolder",
            "size": "Medium",
        },
        {
            "type": "TextBlock",
            "text": card.description,
            "wrap": True,
        },
    ]

    if card.metadata:
        body.append(
            {
                "type": "FactSet",
                "facts": [
                    {"title": k, "value": v} for k, v in card.metadata.items()
                ],
            }
        )

    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
        "actions": [
            {
                "type": "Action.OpenUrl",
                "title": "Approve",
                "url": card.approve_url,
            },
            {
                "type": "Action.OpenUrl",
                "title": "Reject",
                "url": card.reject_url,
            },
        ],
    }
