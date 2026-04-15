"""
Unit tests for SendTeamsMessage and SendTeamsApprovalCard use cases.

Both adapters (teams_client and idempotency_store) are replaced by AsyncMock
instances so the tests exercise only the use-case logic.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.domain.teams import TeamsApprovalCard, TeamsMessage
from app.core.use_cases.results import MessageResult
from app.core.use_cases.teams import SendTeamsApprovalCard, SendTeamsMessage

# ── Fixtures ──────────────────────────────────────────────────────────────────

CONNECTION_ID = "ms-default"
MESSAGE_ID = "msg-1234"
IDEM_KEY = "req-abc"
TTL = 86400


def _make_message() -> TeamsMessage:
    return TeamsMessage(
        team_id="T1",
        channel_id="C1",
        body_content="Hello",
        content_type="text",
    )


def _make_card() -> TeamsApprovalCard:
    return TeamsApprovalCard(
        team_id="T1",
        channel_id="C1",
        title="Approve invoice?",
        description="Invoice #42 for $100.",
        approve_url="https://openclaw.example.com/approve/42",
        reject_url="https://openclaw.example.com/reject/42",
        metadata={"Invoice": "#42", "Amount": "$100"},
    )


@pytest.fixture
def mock_teams_client() -> AsyncMock:
    client = AsyncMock()
    client.send_message = AsyncMock(return_value={"id": MESSAGE_ID})
    client.send_adaptive_card = AsyncMock(return_value={"id": MESSAGE_ID})
    return client


@pytest.fixture
def mock_idempotency_store() -> AsyncMock:
    store = AsyncMock()
    store.get = AsyncMock(return_value=None)   # cache miss by default
    store.set = AsyncMock()
    return store


@pytest.fixture
def send_message_use_case(mock_teams_client, mock_idempotency_store) -> SendTeamsMessage:
    return SendTeamsMessage(
        teams_client=mock_teams_client,
        idempotency_store=mock_idempotency_store,
        idempotency_ttl_seconds=TTL,
    )


@pytest.fixture
def send_approval_use_case(mock_teams_client, mock_idempotency_store) -> SendTeamsApprovalCard:
    return SendTeamsApprovalCard(
        teams_client=mock_teams_client,
        idempotency_store=mock_idempotency_store,
        idempotency_ttl_seconds=TTL,
    )


# ── SendTeamsMessage ──────────────────────────────────────────────────────────


async def test_send_message_calls_client_and_returns_result(
    send_message_use_case, mock_teams_client
):
    result = await send_message_use_case.execute(
        connection_id=CONNECTION_ID,
        message=_make_message(),
    )

    mock_teams_client.send_message.assert_awaited_once()
    assert isinstance(result, MessageResult)
    assert result.message_id == MESSAGE_ID


async def test_send_message_without_idempotency_key_does_not_cache(
    send_message_use_case, mock_idempotency_store
):
    await send_message_use_case.execute(
        connection_id=CONNECTION_ID,
        message=_make_message(),
        idempotency_key=None,
    )

    mock_idempotency_store.get.assert_not_awaited()
    mock_idempotency_store.set.assert_not_awaited()


async def test_send_message_with_idempotency_key_stores_result(
    send_message_use_case, mock_idempotency_store
):
    await send_message_use_case.execute(
        connection_id=CONNECTION_ID,
        message=_make_message(),
        idempotency_key=IDEM_KEY,
    )

    mock_idempotency_store.set.assert_awaited_once()
    call_args = mock_idempotency_store.set.call_args
    key, payload, ttl = call_args.args
    assert IDEM_KEY in key
    assert payload["message_id"] == MESSAGE_ID
    assert ttl == TTL


async def test_send_message_cache_hit_returns_cached_result(
    send_message_use_case, mock_idempotency_store, mock_teams_client
):
    mock_idempotency_store.get.return_value = {"message_id": "cached-id"}

    result = await send_message_use_case.execute(
        connection_id=CONNECTION_ID,
        message=_make_message(),
        idempotency_key=IDEM_KEY,
    )

    assert result.message_id == "cached-id"
    mock_teams_client.send_message.assert_not_awaited()
    mock_idempotency_store.set.assert_not_awaited()


# ── SendTeamsApprovalCard ─────────────────────────────────────────────────────


async def test_send_approval_calls_client_and_returns_result(
    send_approval_use_case, mock_teams_client
):
    result = await send_approval_use_case.execute(
        connection_id=CONNECTION_ID,
        card=_make_card(),
    )

    mock_teams_client.send_adaptive_card.assert_awaited_once()
    assert isinstance(result, MessageResult)
    assert result.message_id == MESSAGE_ID


async def test_send_approval_client_receives_domain_object(
    send_approval_use_case, mock_teams_client
):
    """The use case must pass the TeamsApprovalCard domain object to the port."""
    card = _make_card()
    await send_approval_use_case.execute(
        connection_id=CONNECTION_ID,
        card=card,
    )

    _, kwargs = mock_teams_client.send_adaptive_card.call_args
    assert kwargs["card"] is card


async def test_send_approval_without_idempotency_key_does_not_cache(
    send_approval_use_case, mock_idempotency_store
):
    await send_approval_use_case.execute(
        connection_id=CONNECTION_ID,
        card=_make_card(),
        idempotency_key=None,
    )

    mock_idempotency_store.get.assert_not_awaited()
    mock_idempotency_store.set.assert_not_awaited()


async def test_send_approval_cache_hit_returns_cached_result(
    send_approval_use_case, mock_idempotency_store, mock_teams_client
):
    mock_idempotency_store.get.return_value = {"message_id": "cached-card-id"}

    result = await send_approval_use_case.execute(
        connection_id=CONNECTION_ID,
        card=_make_card(),
        idempotency_key=IDEM_KEY,
    )

    assert result.message_id == "cached-card-id"
    mock_teams_client.send_adaptive_card.assert_not_awaited()


async def test_send_approval_with_idempotency_key_stores_result(
    send_approval_use_case, mock_idempotency_store
):
    await send_approval_use_case.execute(
        connection_id=CONNECTION_ID,
        card=_make_card(),
        idempotency_key=IDEM_KEY,
    )

    mock_idempotency_store.set.assert_awaited_once()
    call_args = mock_idempotency_store.set.call_args
    key, payload, ttl = call_args.args
    assert IDEM_KEY in key
    assert payload["message_id"] == MESSAGE_ID
    assert ttl == TTL
