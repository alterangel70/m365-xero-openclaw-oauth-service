"""
Unit tests for XeroHttpClient.

XeroTokenManager is replaced by an AsyncMock.  The httpx client is mocked
to verify correct request construction, header injection, and error handling.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.adapters.outbound.xero.client import XeroHttpClient
from app.core.domain.token import TokenSet
from app.core.errors import ProviderUnavailableError

# ── Helpers ────────────────────────────────────────────────────────────────────

CONNECTION_ID = "xero-acme"
TENANT_ID = "tenant-abc"
ACCESS_TOKEN = "xero-access-token"
INVOICE_ID = "inv-001"


def _valid_token(tenant_id: str = TENANT_ID) -> TokenSet:
    return TokenSet(
        access_token=ACCESS_TOKEN,
        expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        xero_tenant_id=tenant_id,
    )


def _success_response(body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.is_success = True
    resp.status_code = 200
    resp.json.return_value = body or {"Invoices": [{"InvoiceID": INVOICE_ID}]}
    return resp


def _error_response(status_code: int) -> MagicMock:
    resp = MagicMock()
    resp.is_success = False
    resp.status_code = status_code
    resp.text = "Xero error detail"
    return resp


@pytest.fixture
def mock_token_manager() -> AsyncMock:
    manager = AsyncMock()
    manager.get_valid_token = AsyncMock(return_value=_valid_token())
    # Expose _token_store so the 401-retry path can call delete()
    mock_store = AsyncMock()
    mock_store.delete = AsyncMock()
    manager._token_store = mock_store
    return manager


@pytest.fixture
def mock_http() -> AsyncMock:
    client = AsyncMock()
    client.request = AsyncMock(return_value=_success_response())
    return client


@pytest.fixture
def xero_client(mock_token_manager, mock_http) -> XeroHttpClient:
    return XeroHttpClient(
        token_manager=mock_token_manager,
        http_client=mock_http,
    )


# ── create_invoice ─────────────────────────────────────────────────────────────


async def test_create_invoice_posts_to_correct_url(xero_client, mock_http):
    await xero_client.create_invoice(CONNECTION_ID, payload={"Type": "ACCREC"})

    url = mock_http.request.call_args.args[1]
    assert "/Invoices" in url
    assert mock_http.request.call_args.args[0] == "POST"


async def test_create_invoice_includes_auth_and_tenant_headers(xero_client, mock_http):
    await xero_client.create_invoice(CONNECTION_ID, payload={})

    headers = mock_http.request.call_args.kwargs["headers"]
    assert headers["Authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert headers["Xero-tenant-ID"] == TENANT_ID


async def test_create_invoice_returns_response_body(xero_client):
    result = await xero_client.create_invoice(CONNECTION_ID, payload={})
    assert result["Invoices"][0]["InvoiceID"] == INVOICE_ID


# ── update_invoice_status ──────────────────────────────────────────────────────


async def test_update_invoice_status_posts_correct_payload(xero_client, mock_http):
    await xero_client.update_invoice_status(CONNECTION_ID, INVOICE_ID, "SUBMITTED")

    payload = mock_http.request.call_args.kwargs["json"]
    invoice = payload["Invoices"][0]
    assert invoice["InvoiceID"] == INVOICE_ID
    assert invoice["Status"] == "SUBMITTED"


async def test_update_invoice_status_url_contains_invoice_id(xero_client, mock_http):
    await xero_client.update_invoice_status(CONNECTION_ID, INVOICE_ID, "VOIDED")

    url = mock_http.request.call_args.args[1]
    assert INVOICE_ID in url


# ── get_invoice ────────────────────────────────────────────────────────────────


async def test_get_invoice_uses_get_method(xero_client, mock_http):
    await xero_client.get_invoice(CONNECTION_ID, INVOICE_ID)

    assert mock_http.request.call_args.args[0] == "GET"


async def test_get_invoice_url_contains_invoice_id(xero_client, mock_http):
    await xero_client.get_invoice(CONNECTION_ID, INVOICE_ID)

    url = mock_http.request.call_args.args[1]
    assert INVOICE_ID in url


# ── 401 retry logic ────────────────────────────────────────────────────────────


async def test_xero_401_deletes_token_and_retries(
    xero_client, mock_http, mock_token_manager
):
    mock_http.request.side_effect = [
        _error_response(401),
        _success_response(),
    ]

    result = await xero_client.create_invoice(CONNECTION_ID, payload={})

    assert result["Invoices"][0]["InvoiceID"] == INVOICE_ID
    assert mock_http.request.call_count == 2
    mock_token_manager._token_store.delete.assert_awaited_once_with(CONNECTION_ID)


async def test_xero_401_on_retry_raises_provider_unavailable(
    xero_client, mock_http
):
    mock_http.request.side_effect = [
        _error_response(401),
        _error_response(401),
    ]

    with pytest.raises(ProviderUnavailableError):
        await xero_client.create_invoice(CONNECTION_ID, payload={})


async def test_xero_5xx_raises_provider_unavailable_without_retry(
    xero_client, mock_http
):
    mock_http.request.return_value = _error_response(503)

    with pytest.raises(ProviderUnavailableError):
        await xero_client.create_invoice(CONNECTION_ID, payload={})

    assert mock_http.request.call_count == 1


# ── Missing tenant ID ──────────────────────────────────────────────────────────


async def test_missing_tenant_id_raises_provider_unavailable(
    mock_token_manager, mock_http
):
    mock_token_manager.get_valid_token.return_value = _valid_token(tenant_id=None)
    client = XeroHttpClient(token_manager=mock_token_manager, http_client=mock_http)

    with pytest.raises(ProviderUnavailableError, match="tenant"):
        await client.create_invoice(CONNECTION_ID, payload={})
