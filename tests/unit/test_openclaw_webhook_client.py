"""
Unit tests for OpenClawWebhookClient.

The httpx.AsyncClient is replaced with an AsyncMock so no real network
connection is required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.adapters.outbound.openclaw.webhook_client import OpenClawWebhookClient
from app.core.domain.approval import ApprovalRequest

# ── Fixtures ──────────────────────────────────────────────────────────────────

_WEBHOOK_URL = "http://127.0.0.1:18789"
_TOKEN = "test-bearer-token"

_APPROVAL = ApprovalRequest(
    approval_id="approval-001",
    invoice_case_id="case-001",
    pdf_path="/invoices/case-001.pdf",
    invoice_number="INV-001",
    supplier_name="Acme Corp",
    approve_url="http://example.com/approve",
    reject_url="http://example.com/reject",
    status="pending",
    created_at=datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc),
)


def _make_client(mock_http: AsyncMock) -> OpenClawWebhookClient:
    return OpenClawWebhookClient(
        http_client=mock_http,
        webhook_base_url=_WEBHOOK_URL,
        token=_TOKEN,
    )


def _ok_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    return resp


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestOpenClawWebhookClient:
    async def test_successful_call_returns_ok(self):
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=_ok_response())

        client = _make_client(mock_http)
        result = await client.notify_decision(_APPROVAL, "approved")

        assert result == "ok"

    async def test_posts_to_correct_url(self):
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=_ok_response())

        await _make_client(mock_http).notify_decision(_APPROVAL, "approved")

        url = mock_http.post.call_args.args[0]
        assert url == f"{_WEBHOOK_URL}/hooks/agent"

    async def test_authorization_header_sent(self):
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=_ok_response())

        await _make_client(mock_http).notify_decision(_APPROVAL, "approved")

        headers = mock_http.post.call_args.kwargs["headers"]
        assert headers["Authorization"] == f"Bearer {_TOKEN}"

    async def test_payload_contains_required_fields(self):
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=_ok_response())

        await _make_client(mock_http).notify_decision(_APPROVAL, "approved")

        payload = mock_http.post.call_args.kwargs["json"]
        assert payload["agentId"] == "main"
        assert payload["sessionKey"] == "hook:invoice:case-001"
        assert payload["wakeMode"] == "now"
        assert payload["deliver"] is False
        assert payload["timeoutSeconds"] == 120
        assert "Invoice approval callback" == payload["name"]

    async def test_payload_message_contains_all_fields(self):
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=_ok_response())

        await _make_client(mock_http).notify_decision(_APPROVAL, "rejected")

        message = mock_http.post.call_args.kwargs["json"]["message"]
        assert "approvalId=approval-001" in message
        assert "invoiceCaseId=case-001" in message
        assert "pdf_path=/invoices/case-001.pdf" in message
        assert "action=rejected" in message

    async def test_http_status_error_returns_error_string(self):
        error_response = MagicMock()
        error_response.status_code = 503
        error_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "503", request=MagicMock(), response=error_response
            )
        )
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=error_response)

        result = await _make_client(mock_http).notify_decision(_APPROVAL, "approved")

        assert "503" in result
        # Never raises — errors become strings.

    async def test_connection_error_returns_error_string(self):
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )

        result = await _make_client(mock_http).notify_decision(_APPROVAL, "approved")

        assert result.startswith("error:")

    async def test_generic_exception_returns_error_string(self):
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(side_effect=RuntimeError("something broke"))

        result = await _make_client(mock_http).notify_decision(_APPROVAL, "rejected")

        assert "something broke" in result

    async def test_trailing_slash_in_base_url_stripped(self):
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=_ok_response())

        client = OpenClawWebhookClient(
            http_client=mock_http,
            webhook_base_url="http://127.0.0.1:18789/",
            token=_TOKEN,
        )
        await client.notify_decision(_APPROVAL, "approved")

        url = mock_http.post.call_args.args[0]
        assert url == "http://127.0.0.1:18789/hooks/agent"
