"""
Unit tests for MSGraphClient.

MSTokenManager is replaced with an AsyncMock so the token lifecycle
is not re-tested here.  The httpx client is mocked to verify correct
request construction and error handling.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.adapters.outbound.ms_graph.client import MSGraphClient
from app.core.errors import ProviderUnavailableError


# ── Helpers ───────────────────────────────────────────────────────────────────

TEAM_ID = "team-abc"
CHANNEL_ID = "channel-xyz"
CONNECTION_ID = "ms-default"
FAKE_TOKEN = "fake_bearer_token"
MESSAGE_ID = "msg-001"


def _success_response(body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.is_success = True
    resp.status_code = 200
    resp.json.return_value = body or {"id": MESSAGE_ID}
    return resp


def _error_response(status_code: int) -> MagicMock:
    resp = MagicMock()
    resp.is_success = False
    resp.status_code = status_code
    resp.text = "error detail"
    return resp


@pytest.fixture
def mock_token_manager():
    manager = AsyncMock()
    manager.get_token = AsyncMock(return_value=FAKE_TOKEN)
    return manager


@pytest.fixture
def mock_http():
    client = AsyncMock()
    client.post = AsyncMock(return_value=_success_response())
    return client


@pytest.fixture
def graph_client(mock_token_manager, mock_http):
    return MSGraphClient(token_manager=mock_token_manager, http_client=mock_http)


# ── send_message ──────────────────────────────────────────────────────────────

async def test_send_message_calls_correct_endpoint(graph_client, mock_http):
    await graph_client.send_message(
        CONNECTION_ID, TEAM_ID, CHANNEL_ID, "Hello", "text"
    )
    call_args = mock_http.post.call_args
    assert f"/teams/{TEAM_ID}/channels/{CHANNEL_ID}/messages" in call_args.args[0]


async def test_send_message_passes_correct_body(graph_client, mock_http):
    await graph_client.send_message(
        CONNECTION_ID, TEAM_ID, CHANNEL_ID, "<b>Hi</b>", "html"
    )
    payload = mock_http.post.call_args.kwargs["json"]
    assert payload["body"]["contentType"] == "html"
    assert payload["body"]["content"] == "<b>Hi</b>"


async def test_send_message_includes_bearer_auth(graph_client, mock_http):
    await graph_client.send_message(
        CONNECTION_ID, TEAM_ID, CHANNEL_ID, "Hello", "text"
    )
    headers = mock_http.post.call_args.kwargs["headers"]
    assert headers["Authorization"] == f"Bearer {FAKE_TOKEN}"


async def test_send_message_returns_response_body(graph_client):
    result = await graph_client.send_message(
        CONNECTION_ID, TEAM_ID, CHANNEL_ID, "Hello", "text"
    )
    assert result["id"] == MESSAGE_ID


# ── send_adaptive_card ────────────────────────────────────────────────────────

async def test_send_adaptive_card_body_has_attachment_placeholder(graph_client, mock_http):
    card = {"type": "AdaptiveCard", "version": "1.4", "body": []}
    await graph_client.send_adaptive_card(
        CONNECTION_ID, TEAM_ID, CHANNEL_ID, card
    )
    payload = mock_http.post.call_args.kwargs["json"]
    assert payload["body"]["contentType"] == "html"
    assert "<attachment id=" in payload["body"]["content"]


async def test_send_adaptive_card_has_correct_attachment_structure(graph_client, mock_http):
    card = {"type": "AdaptiveCard", "version": "1.4", "body": []}
    await graph_client.send_adaptive_card(
        CONNECTION_ID, TEAM_ID, CHANNEL_ID, card
    )
    payload = mock_http.post.call_args.kwargs["json"]
    assert len(payload["attachments"]) == 1

    attachment = payload["attachments"][0]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    # Extract the UUID from the HTML attribute: <attachment id="{uuid}"></attachment>
    placeholder_id = payload["body"]["content"].split('id="')[1].split('"')[0]
    assert attachment["id"] == placeholder_id


async def test_send_adaptive_card_serialises_content_as_string(graph_client, mock_http):
    """Graph requires card content as a JSON string, not a nested dict."""
    card = {"type": "AdaptiveCard", "version": "1.4", "body": [{"type": "TextBlock", "text": "Hi"}]}
    await graph_client.send_adaptive_card(
        CONNECTION_ID, TEAM_ID, CHANNEL_ID, card
    )
    payload = mock_http.post.call_args.kwargs["json"]
    content = payload["attachments"][0]["content"]
    assert isinstance(content, str)
    assert json.loads(content)["type"] == "AdaptiveCard"


# ── 401 retry logic ───────────────────────────────────────────────────────────

async def test_graph_401_triggers_force_refresh_and_retry(
    graph_client, mock_http, mock_token_manager
):
    """On a 401 response, get_token must be called again with force=True,
    and the request retried once.
    """
    mock_http.post.side_effect = [
        _error_response(401),   # first attempt
        _success_response(),    # retry after force refresh
    ]

    result = await graph_client.send_message(
        CONNECTION_ID, TEAM_ID, CHANNEL_ID, "Hello", "text"
    )

    assert result["id"] == MESSAGE_ID
    assert mock_http.post.call_count == 2
    # First call: force=False, second call: force=True
    calls = mock_token_manager.get_token.call_args_list
    assert calls[0] == call(CONNECTION_ID, force=False)
    assert calls[1] == call(CONNECTION_ID, force=True)


async def test_graph_401_on_retry_raises_provider_unavailable(graph_client, mock_http):
    """If the retry also returns 401, ProviderUnavailableError must be raised."""
    mock_http.post.side_effect = [
        _error_response(401),
        _error_response(401),
    ]

    with pytest.raises(ProviderUnavailableError):
        await graph_client.send_message(
            CONNECTION_ID, TEAM_ID, CHANNEL_ID, "Hello", "text"
        )


async def test_graph_5xx_raises_provider_unavailable(graph_client, mock_http):
    """A 5xx response on the first attempt raises ProviderUnavailableError
    without retrying (only 401 triggers the retry path).
    """
    mock_http.post.return_value = _error_response(503)

    with pytest.raises(ProviderUnavailableError):
        await graph_client.send_message(
            CONNECTION_ID, TEAM_ID, CHANNEL_ID, "Hello", "text"
        )

    assert mock_http.post.call_count == 1


async def test_graph_403_raises_provider_unavailable_without_retry(graph_client, mock_http):
    mock_http.post.return_value = _error_response(403)

    with pytest.raises(ProviderUnavailableError):
        await graph_client.send_message(
            CONNECTION_ID, TEAM_ID, CHANNEL_ID, "Hello", "text"
        )

    assert mock_http.post.call_count == 1
