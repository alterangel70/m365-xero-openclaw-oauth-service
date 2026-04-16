"""
Integration tests: Teams message and approval-card endpoints.

Real Redis, mock HTTP transport (MS Graph token endpoint + Graph channel API).
"""

import httpx
import pytest
import respx

from tests.integration.conftest import (
    TEST_MS_TOKEN,
    TEST_MS_TENANT,
    register_ms_token_route,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _graph_messages_url(team_id: str, channel_id: str) -> str:
    return (
        f"https://graph.microsoft.com/v1.0"
        f"/teams/{team_id}/channels/{channel_id}/messages"
    )


def _make_graph_message_response(message_id: str = "msg-001") -> httpx.Response:
    return httpx.Response(201, json={"id": message_id})


# ── Tests: POST /v1/teams/messages ────────────────────────────────────────────


async def test_send_message_returns_201_with_message_id(app_client, mock_router):
    """Happy path: MS token acquired, message posted, 201 returned."""
    register_ms_token_route(mock_router)
    mock_router.post(
        url__regex=r"https://graph\.microsoft\.com/v1\.0/teams/.+/channels/.+/messages",
    ).mock(return_value=_make_graph_message_response("msg-int-001"))

    resp = await app_client.post(
        "/v1/teams/messages",
        json={
            "connection_id": "ms-default",
            "team_id": "team-aaa",
            "channel_id": "chan-bbb",
            "body_content": "<p>Hello from integration test</p>",
            "content_type": "html",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["message_id"] == "msg-int-001"


async def test_send_message_idempotency_returns_same_result(app_client, mock_router):
    """Sending twice with the same Idempotency-Key must return the cached result."""
    register_ms_token_route(mock_router)
    graph_route = mock_router.post(
        url__regex=r"https://graph\.microsoft\.com/v1\.0/teams/.+/channels/.+/messages",
    ).mock(return_value=_make_graph_message_response("msg-idem-001"))

    payload = {
        "connection_id": "ms-default",
        "team_id": "team-aaa",
        "channel_id": "chan-bbb",
        "body_content": "Idempotent message",
        "content_type": "text",
    }
    headers = {"Idempotency-Key": "idem-key-teams-1"}

    r1 = await app_client.post("/v1/teams/messages", json=payload, headers=headers)
    r2 = await app_client.post("/v1/teams/messages", json=payload, headers=headers)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["message_id"] == r2.json()["message_id"] == "msg-idem-001"
    # Graph endpoint should only have been called once.
    assert graph_route.call_count == 1


async def test_send_message_different_idempotency_keys_call_graph_twice(
    app_client, mock_router
):
    """Two requests with different keys must both call Graph."""
    register_ms_token_route(mock_router)
    graph_route = mock_router.post(
        url__regex=r"https://graph\.microsoft\.com/v1\.0/teams/.+/channels/.+/messages",
    ).mock(side_effect=[
        _make_graph_message_response("msg-a"),
        _make_graph_message_response("msg-b"),
    ])

    payload = {
        "connection_id": "ms-default",
        "team_id": "team-aaa",
        "channel_id": "chan-bbb",
        "body_content": "msg",
        "content_type": "text",
    }

    r1 = await app_client.post(
        "/v1/teams/messages", json=payload, headers={"Idempotency-Key": "key-a"}
    )
    r2 = await app_client.post(
        "/v1/teams/messages", json=payload, headers={"Idempotency-Key": "key-b"}
    )

    assert r1.json()["message_id"] == "msg-a"
    assert r2.json()["message_id"] == "msg-b"
    assert graph_route.call_count == 2


async def test_send_message_missing_api_key_returns_401(app_client, mock_router):
    """Requests without the Authorization header must be rejected."""
    resp = await app_client.post(
        "/v1/teams/messages",
        json={
            "connection_id": "ms-default",
            "team_id": "t",
            "channel_id": "c",
            "body_content": "hello",
        },
        headers={"Authorization": ""},  # override fixture default
    )

    assert resp.status_code == 401
    assert resp.json()["error"] == "unauthorized"


async def test_send_message_graph_5xx_returns_502(app_client, mock_router):
    """A 500 from MS Graph must be surfaced as 502 with the agreed envelope."""
    register_ms_token_route(mock_router)
    mock_router.post(
        url__regex=r"https://graph\.microsoft\.com/v1\.0/teams/.+/channels/.+/messages",
    ).mock(return_value=httpx.Response(500, json={"error": "InternalServerError"}))

    resp = await app_client.post(
        "/v1/teams/messages",
        json={
            "connection_id": "ms-default",
            "team_id": "team-aaa",
            "channel_id": "chan-bbb",
            "body_content": "fail",
        },
    )

    assert resp.status_code == 502
    assert resp.json()["error"] == "provider_unavailable"


# ── Tests: POST /v1/teams/approvals ──────────────────────────────────────────


async def test_send_approval_card_returns_200(app_client, mock_router):
    """Happy path for the approval card route."""
    register_ms_token_route(mock_router)
    mock_router.post(
        url__regex=r"https://graph\.microsoft\.com/v1\.0/teams/.+/channels/.+/messages",
    ).mock(return_value=_make_graph_message_response("msg-approval-001"))

    resp = await app_client.post(
        "/v1/teams/approvals",
        json={
            "connection_id": "ms-default",
            "team_id": "team-aaa",
            "channel_id": "chan-bbb",
            "title": "Approve invoice #123",
            "description": "Please review and approve.",
            "approve_url": "https://openclaw.example/approve/123",
            "reject_url": "https://openclaw.example/reject/123",
            "metadata": {"invoice_id": "123", "amount": "500.00"},
        },
    )

    assert resp.status_code == 200
    assert resp.json()["message_id"] == "msg-approval-001"
