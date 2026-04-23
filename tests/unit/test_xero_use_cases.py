"""
Unit tests for Xero use cases: CreateXeroDraftInvoice, SubmitXeroInvoice,
GetXeroInvoice, and VoidXeroInvoice.

AbstractXeroClient and AbstractIdempotencyStore are replaced by AsyncMocks.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.core.domain.xero import XeroInvoice, XeroLineItem
from app.core.errors import ProviderUnavailableError
from app.core.use_cases.results import XeroInvoiceResult
from app.core.use_cases.xero import (
    CreateXeroDraftInvoice,
    GetXeroInvoice,
    SubmitXeroInvoice,
    VoidXeroInvoice,
)

# ── Shared fixtures & helpers ─────────────────────────────────────────────────

CONNECTION_ID = "xero-acme"
INVOICE_ID = "inv-abc-001"
IDEM_KEY = "req-xyz"
TTL = 86400


def _make_invoice() -> XeroInvoice:
    return XeroInvoice(
        contact_id="contact-1",
        line_items=(
            XeroLineItem(
                description="Consulting",
                quantity=Decimal("1"),
                unit_amount=Decimal("500.00"),
                account_code="200",
            ),
        ),
        due_date=date(2026, 6, 1),
        currency_code="AUD",
        reference="OCL-001",
    )


def _xero_response(
    invoice_id: str = INVOICE_ID, status: str = "DRAFT", type_: str = "ACCPAY"
) -> dict:
    return {"Invoices": [{"InvoiceID": invoice_id, "Status": status, "Type": type_}]}


@pytest.fixture
def mock_xero_client() -> AsyncMock:
    client = AsyncMock()
    client.create_invoice = AsyncMock(return_value=_xero_response(status="DRAFT"))
    client.update_invoice_status = AsyncMock(
        return_value=_xero_response(status="AUTHORISED")
    )
    client.get_invoice = AsyncMock(return_value=_xero_response(status="AUTHORISED"))
    return client


@pytest.fixture
def mock_idempotency_store() -> AsyncMock:
    store = AsyncMock()
    store.get = AsyncMock(return_value=None)  # cache miss by default
    store.set = AsyncMock()
    return store


# ── CreateXeroDraftInvoice ─────────────────────────────────────────────────────


@pytest.fixture
def create_use_case(mock_xero_client, mock_idempotency_store) -> CreateXeroDraftInvoice:
    return CreateXeroDraftInvoice(
        xero_client=mock_xero_client,
        idempotency_store=mock_idempotency_store,
        idempotency_ttl_seconds=TTL,
    )


async def test_create_invoice_calls_client_and_returns_result(
    create_use_case, mock_xero_client
):
    result = await create_use_case.execute(
        connection_id=CONNECTION_ID,
        invoice=_make_invoice(),
        idempotency_key=IDEM_KEY,
    )

    mock_xero_client.create_invoice.assert_awaited_once()
    assert isinstance(result, XeroInvoiceResult)
    assert result.invoice_id == INVOICE_ID
    assert result.status == "DRAFT"


async def test_create_invoice_payload_has_correct_structure(
    create_use_case, mock_xero_client
):
    await create_use_case.execute(
        connection_id=CONNECTION_ID,
        invoice=_make_invoice(),
        idempotency_key=IDEM_KEY,
    )

    payload = mock_xero_client.create_invoice.call_args.kwargs["payload"]
    inv = payload["Invoices"][0]
    assert inv["Type"] == "ACCPAY"
    assert inv["Status"] == "DRAFT"
    assert inv["Contact"]["ContactID"] == "contact-1"
    assert inv["LineItems"][0]["Description"] == "Consulting"
    assert inv["Reference"] == "OCL-001"


async def test_create_invoice_cache_hit_skips_client(
    create_use_case, mock_idempotency_store, mock_xero_client
):
    mock_idempotency_store.get.return_value = {
        "invoice_id": "cached-id",
        "status": "DRAFT",
    }
    mock_xero_client.get_invoice.return_value = _xero_response(
        invoice_id="cached-id", status="DRAFT"
    )

    result = await create_use_case.execute(
        connection_id=CONNECTION_ID,
        invoice=_make_invoice(),
        idempotency_key=IDEM_KEY,
    )

    assert result.invoice_id == "cached-id"
    mock_xero_client.get_invoice.assert_awaited_once_with(
        connection_id=CONNECTION_ID, invoice_id="cached-id"
    )
    mock_xero_client.create_invoice.assert_not_awaited()


@pytest.mark.parametrize("stale_status", ["DELETED", "VOIDED"])
async def test_create_invoice_cache_hit_stale_status_recreates(
    create_use_case, mock_idempotency_store, mock_xero_client, stale_status
):
    """When the cached invoice is DELETED or VOIDED, a new invoice must be created."""
    mock_idempotency_store.get.return_value = {
        "invoice_id": "stale-id",
        "status": stale_status,
    }
    mock_xero_client.get_invoice.return_value = _xero_response(
        invoice_id="stale-id", status=stale_status
    )
    mock_xero_client.create_invoice.return_value = _xero_response(
        invoice_id="new-id", status="DRAFT"
    )

    result = await create_use_case.execute(
        connection_id=CONNECTION_ID,
        invoice=_make_invoice(),
        idempotency_key=IDEM_KEY,
    )

    mock_xero_client.create_invoice.assert_awaited_once()
    assert result.invoice_id == "new-id"
    assert result.status == "DRAFT"


async def test_create_invoice_cache_hit_invoice_not_found_recreates(
    create_use_case, mock_idempotency_store, mock_xero_client
):
    """When Xero returns an error for the cached invoice_id, a new invoice is created."""
    mock_idempotency_store.get.return_value = {
        "invoice_id": "missing-id",
        "status": "DRAFT",
    }
    mock_xero_client.get_invoice.side_effect = ProviderUnavailableError("404")
    mock_xero_client.create_invoice.return_value = _xero_response(
        invoice_id="new-id", status="DRAFT"
    )

    result = await create_use_case.execute(
        connection_id=CONNECTION_ID,
        invoice=_make_invoice(),
        idempotency_key=IDEM_KEY,
    )

    mock_xero_client.create_invoice.assert_awaited_once()
    assert result.invoice_id == "new-id"


async def test_create_invoice_stores_result_under_idempotency_key(
    create_use_case, mock_idempotency_store
):
    await create_use_case.execute(
        connection_id=CONNECTION_ID,
        invoice=_make_invoice(),
        idempotency_key=IDEM_KEY,
    )

    mock_idempotency_store.set.assert_awaited_once()
    key, payload, ttl = mock_idempotency_store.set.call_args.args
    assert IDEM_KEY in key
    assert payload["invoice_id"] == INVOICE_ID
    assert payload["status"] == "DRAFT"
    assert ttl == TTL


async def test_create_invoice_payload_omits_reference_when_none(
    mock_xero_client, mock_idempotency_store
):
    invoice = XeroInvoice(
        contact_id="c1",
        line_items=(
            XeroLineItem(
                description="Item",
                quantity=Decimal("1"),
                unit_amount=Decimal("100"),
                account_code="200",
            ),
        ),
        due_date=date(2026, 6, 1),
        currency_code="AUD",
        reference=None,
    )
    use_case = CreateXeroDraftInvoice(
        xero_client=mock_xero_client,
        idempotency_store=mock_idempotency_store,
        idempotency_ttl_seconds=TTL,
    )
    await use_case.execute(CONNECTION_ID, invoice, IDEM_KEY)

    payload = mock_xero_client.create_invoice.call_args.kwargs["payload"]
    assert "Reference" not in payload["Invoices"][0]


# ── SubmitXeroInvoice ──────────────────────────────────────────────────────────


@pytest.fixture
def submit_use_case(mock_xero_client, mock_idempotency_store) -> SubmitXeroInvoice:
    return SubmitXeroInvoice(
        xero_client=mock_xero_client,
        idempotency_store=mock_idempotency_store,
        idempotency_ttl_seconds=TTL,
    )


async def test_submit_invoice_calls_update_with_authorised(
    submit_use_case, mock_xero_client
):
    mock_xero_client.update_invoice_status.return_value = _xero_response(
        status="AUTHORISED"
    )

    result = await submit_use_case.execute(
        connection_id=CONNECTION_ID,
        invoice_id=INVOICE_ID,
        idempotency_key=IDEM_KEY,
    )

    mock_xero_client.update_invoice_status.assert_awaited_once_with(
        connection_id=CONNECTION_ID,
        invoice_id=INVOICE_ID,
        status="AUTHORISED",
    )
    assert result.status == "AUTHORISED"


async def test_submit_invoice_cache_hit_skips_client(
    submit_use_case, mock_idempotency_store, mock_xero_client
):
    mock_idempotency_store.get.return_value = {
        "invoice_id": INVOICE_ID,
        "status": "AUTHORISED",
    }

    result = await submit_use_case.execute(CONNECTION_ID, INVOICE_ID, IDEM_KEY)

    assert result.status == "AUTHORISED"
    mock_xero_client.update_invoice_status.assert_not_awaited()


async def test_submit_invoice_stores_idempotency_result(
    submit_use_case, mock_idempotency_store
):
    await submit_use_case.execute(CONNECTION_ID, INVOICE_ID, IDEM_KEY)

    mock_idempotency_store.set.assert_awaited_once()
    key, payload, ttl = mock_idempotency_store.set.call_args.args
    assert IDEM_KEY in key
    assert payload["status"] == "AUTHORISED"


# ── GetXeroInvoice ─────────────────────────────────────────────────────────────


@pytest.fixture
def get_use_case(mock_xero_client) -> GetXeroInvoice:
    return GetXeroInvoice(xero_client=mock_xero_client)


async def test_get_invoice_returns_result(get_use_case, mock_xero_client):
    mock_xero_client.get_invoice.return_value = _xero_response(status="AUTHORISED")

    result = await get_use_case.execute(CONNECTION_ID, INVOICE_ID)

    mock_xero_client.get_invoice.assert_awaited_once_with(
        connection_id=CONNECTION_ID,
        invoice_id=INVOICE_ID,
    )
    assert result.invoice_id == INVOICE_ID
    assert result.status == "AUTHORISED"


async def test_get_invoice_does_not_use_idempotency(get_use_case, mock_xero_client):
    """GetXeroInvoice has no idempotency store — verify it's never touched."""
    # The fixture has no idempotency_store, so any attribute access would fail.
    assert not hasattr(get_use_case, "_idempotency_store")
    await get_use_case.execute(CONNECTION_ID, INVOICE_ID)


# ── VoidXeroInvoice ────────────────────────────────────────────────────────────


@pytest.fixture
def void_use_case(mock_xero_client, mock_idempotency_store) -> VoidXeroInvoice:
    return VoidXeroInvoice(
        xero_client=mock_xero_client,
        idempotency_store=mock_idempotency_store,
        idempotency_ttl_seconds=TTL,
    )


async def test_void_invoice_calls_update_with_voided(void_use_case, mock_xero_client):
    mock_xero_client.update_invoice_status.return_value = _xero_response(
        status="VOIDED"
    )

    result = await void_use_case.execute(
        connection_id=CONNECTION_ID,
        invoice_id=INVOICE_ID,
        idempotency_key=IDEM_KEY,
    )

    mock_xero_client.update_invoice_status.assert_awaited_once_with(
        connection_id=CONNECTION_ID,
        invoice_id=INVOICE_ID,
        status="VOIDED",
    )
    assert result.status == "VOIDED"


async def test_void_invoice_cache_hit_skips_client(
    void_use_case, mock_idempotency_store, mock_xero_client
):
    mock_idempotency_store.get.return_value = {
        "invoice_id": INVOICE_ID,
        "status": "VOIDED",
    }

    result = await void_use_case.execute(CONNECTION_ID, INVOICE_ID, IDEM_KEY)

    assert result.status == "VOIDED"
    mock_xero_client.update_invoice_status.assert_not_awaited()


async def test_void_invoice_stores_idempotency_result(
    void_use_case, mock_idempotency_store
):
    mock_xero_client_local = AsyncMock()
    mock_xero_client_local.update_invoice_status = AsyncMock(
        return_value=_xero_response(status="VOIDED")
    )
    use_case = VoidXeroInvoice(
        xero_client=mock_xero_client_local,
        idempotency_store=mock_idempotency_store,
        idempotency_ttl_seconds=TTL,
    )

    await use_case.execute(CONNECTION_ID, INVOICE_ID, IDEM_KEY)

    mock_idempotency_store.set.assert_awaited_once()
    key, payload, ttl = mock_idempotency_store.set.call_args.args
    assert IDEM_KEY in key
    assert payload["status"] == "VOIDED"
