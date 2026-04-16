"""
Integration tests: Teams message and approval-card endpoints.

Real Redis, mock HTTP transport (MS Graph channel API).

The delegated MS token is pre-seeded in Redis (register_ms_token_in_redis).
The token manager no longer auto-acquires via client_credentials — it loads
the stored token from Redis and only refreshes when near expiry.
"""

import httpx
import pytest
import respx

from tests.integration.conftest import (
    TEST_MS_TOKEN,
    TEST_MS_TENANT,
    register_ms_token_in_redis,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_graph_message_response(message_id: str = "msg-001") -> httpx.Response:
    return httpx.Response(201, json={"id": message_id})


# ── Tests: POST /v1/teams/messages ────────────────────────────────────────────


async def test_send_message_returns_201_with_message_id(app_client, mock_router, redis_client):
    """Happy path: delegated token pre-seeded in Redis, message posted."""
    await register_ms_token_in_redis(redis_client, "ms-default")
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


async def test_send_message_no_token_returns_404(app_client, mock_router):
    """Without a pre-seeded token, the service returns 404 connection_missing."""
    resp = await app_client.post(
        "/v1/teams/messages",
        json={
            "connection_id": "ms-default",
            "team_id": "team-aaa",
            "channel_id": "chan-bbb",
            "body_content": "Hello",
        },
    )

    assert resp.status_code == 404
    assert resp.json()["error"] == "connection_missing"


async def test_send_message_idempotency_returns_same_result(app_client, mock_router, redis_client):
    """Sending twice with the same Idempotency-Key must return the cached result."""
    await register_ms_token_in_redis(redis_client, "ms-default")
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
    assert graph_route.call_count == 1


async def test_send_message_different_idempotency_keys_call_graph_twice(
    app_client, mock_router, redis_client
):
    """Two requests with different keys must both call Graph."""
    await register_ms_token_in_redis(redis_client, "ms-default")
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
        headers={"Authorization": ""},
    )

    assert resp.status_code == 401
    assert resp.json()["error"] == "unauthorized"


async def test_send_message_graph_5xx_returns_502(app_client, mock_router, redis_client):
    """A 500 from MS Graph must be surfaced as 502 with the agreed envelope."""
    await register_ms_token_in_redis(redis_client, "ms-default")
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


async def test_send_approval_card_returns_200(app_client, mock_router, redis_client):
    """Happy path for the approval card route."""
    await register_ms_token_in_redis(redis_client, "ms-default")
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
