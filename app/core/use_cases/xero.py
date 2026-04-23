"""
Xero use cases: invoice lifecycle operations.

Token acquisition and tenant ID resolution are fully handled by the
XeroHttpClient adapter.  These use cases do not interact with the token
store directly.

Idempotency keys are required for all write operations to prevent
duplicate invoices on OpenClaw retries.

Xero API notes
--------------
* ``Status: DRAFT`` — invoice created but not yet approved.
* ``Status: AUTHORISED`` — Xero's term for a submitted/approved invoice.
* ``Status: VOIDED`` — invoice has been voided.
* The response body for invoice operations always contains an ``Invoices``
  list; we take the first element.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from app.core.domain.xero import XeroInvoice
from app.core.errors import ProviderUnavailableError
from app.core.ports.idempotency_store import AbstractIdempotencyStore
from app.core.ports.xero_client import AbstractXeroClient
from app.core.use_cases.results import (
    XeroAccountResult,
    XeroContactResult,
    XeroInvoiceResult,
    XeroTaxRateResult,
)

logger = logging.getLogger(__name__)

# Stable operation name prefixes used as part of the idempotency Redis key.
_OP_CREATE_INVOICE = "create_xero_invoice"
_OP_SUBMIT_INVOICE = "submit_xero_invoice"
_OP_VOID_INVOICE = "void_xero_invoice"

# Invoice statuses that mean the invoice no longer exists in a usable form.
# If a cached invoice_id resolves to one of these, the cache entry is stale.
_INVALID_INVOICE_STATUSES = frozenset({"DELETED", "VOIDED"})


_INVOICE_TYPE = "ACCPAY"  # Bills / Purchases only. ACCREC (Sales) is never created here.


def _invoice_payload(invoice: XeroInvoice) -> dict:
    """Convert a XeroInvoice domain object to the Xero API request body.

    Always produces Type=ACCPAY (Bill/Purchase). ACCREC is never valid here.
    Raises ValueError if the assembled payload somehow carries the wrong type.
    """
    payload = {
        "Type": _INVOICE_TYPE,
        "Contact": {"ContactID": invoice.contact_id},
        "DueDate": invoice.due_date.isoformat(),
        "CurrencyCode": invoice.currency_code,
        "Status": "DRAFT",
        **({"Reference": invoice.reference} if invoice.reference else {}),
        "LineItems": [
            {
                "Description": li.description,
                "Quantity": str(li.quantity),
                "UnitAmount": str(li.unit_amount),
                "AccountCode": li.account_code,
                **({"TaxType": li.tax_type} if li.tax_type else {}),
            }
            for li in invoice.line_items
        ],
    }
    if payload["Type"] != _INVOICE_TYPE:
        raise ValueError(
            f"Invoice payload type must be {_INVOICE_TYPE!r}, got {payload['Type']!r}. "
            "ACCREC (Sales invoices) must never be created through this service."
        )
    return payload


def _extract_invoice_result(response: dict) -> XeroInvoiceResult:
    """Pull invoice_id and status from a Xero Invoices API response."""
    inv = response["Invoices"][0]
    return XeroInvoiceResult(invoice_id=inv["InvoiceID"], status=inv["Status"])


class CreateXeroDraftInvoice:
    """Create a new DRAFT invoice in Xero.

    idempotency_key is mandatory — Xero has no built-in deduplication for
    POST /Invoices, so the service must guard against duplicate creation on
    OpenClaw retries.
    """

    def __init__(
        self,
        xero_client: AbstractXeroClient,
        idempotency_store: AbstractIdempotencyStore,
        idempotency_ttl_seconds: int,
    ) -> None:
        self._xero_client = xero_client
        self._idempotency_store = idempotency_store
        self._idempotency_ttl_seconds = idempotency_ttl_seconds

    async def execute(
        self,
        connection_id: str,
        invoice: XeroInvoice,
        idempotency_key: str,
    ) -> XeroInvoiceResult:
        idem_key = f"idempotency:{_OP_CREATE_INVOICE}:{idempotency_key}"

        cached = await self._idempotency_store.get(idem_key)
        if cached:
            cached_invoice_id = cached["invoice_id"]
            try:
                verification = await self._xero_client.get_invoice(
                    connection_id=connection_id,
                    invoice_id=cached_invoice_id,
                )
                live_status: str | None = verification["Invoices"][0]["Status"]
            except ProviderUnavailableError:
                live_status = None

            if live_status is not None and live_status not in _INVALID_INVOICE_STATUSES:
                logger.debug("Idempotency cache hit for key %r", idempotency_key)
                return XeroInvoiceResult(
                    invoice_id=cached_invoice_id,
                    status=cached["status"],
                )

            logger.warning(
                "Idempotency cache hit for %r but invoice %r has status %r — "
                "invalidating cache entry and re-creating invoice",
                idempotency_key,
                cached_invoice_id,
                live_status,
            )
            # Fall through: create a new invoice and overwrite the stale cache entry.

        payload = {"Invoices": [_invoice_payload(invoice)]}
        response = await self._xero_client.create_invoice(
            connection_id=connection_id,
            payload=payload,
        )
        result = _extract_invoice_result(response)
        returned_type = response["Invoices"][0].get("Type")
        if returned_type != _INVOICE_TYPE:
            raise ProviderUnavailableError(
                f"Xero created invoice with type {returned_type!r} instead of "
                f"{_INVOICE_TYPE!r}. Manual review required — invoice_id={result.invoice_id}"
            )

        await self._idempotency_store.set(
            idem_key,
            {"invoice_id": result.invoice_id, "status": result.status},
            self._idempotency_ttl_seconds,
        )
        return result


class SubmitXeroInvoice:
    """Transition a Xero invoice from DRAFT to AUTHORISED (submitted).

    Xero uses the status name ``AUTHORISED`` for a submitted/approved invoice.
    We expose this as "submit" in the API to match OpenClaw's workflow vocabulary.
    """

    def __init__(
        self,
        xero_client: AbstractXeroClient,
        idempotency_store: AbstractIdempotencyStore,
        idempotency_ttl_seconds: int,
    ) -> None:
        self._xero_client = xero_client
        self._idempotency_store = idempotency_store
        self._idempotency_ttl_seconds = idempotency_ttl_seconds

    async def execute(
        self,
        connection_id: str,
        invoice_id: str,
        idempotency_key: str,
    ) -> XeroInvoiceResult:
        idem_key = f"idempotency:{_OP_SUBMIT_INVOICE}:{idempotency_key}"

        cached = await self._idempotency_store.get(idem_key)
        if cached:
            logger.debug("Idempotency cache hit for key %r", idempotency_key)
            return XeroInvoiceResult(
                invoice_id=cached["invoice_id"],
                status=cached["status"],
            )

        response = await self._xero_client.update_invoice_status(
            connection_id=connection_id,
            invoice_id=invoice_id,
            status="AUTHORISED",
        )
        result = _extract_invoice_result(response)

        await self._idempotency_store.set(
            idem_key,
            {"invoice_id": result.invoice_id, "status": result.status},
            self._idempotency_ttl_seconds,
        )
        return result


class GetXeroInvoice:
    """Retrieve the current state of a Xero invoice by its InvoiceID.

    Read-only; no idempotency key required.
    """

    def __init__(
        self,
        xero_client: AbstractXeroClient,
    ) -> None:
        self._xero_client = xero_client

    async def execute(
        self,
        connection_id: str,
        invoice_id: str,
    ) -> XeroInvoiceResult:
        response = await self._xero_client.get_invoice(
            connection_id=connection_id,
            invoice_id=invoice_id,
        )
        return _extract_invoice_result(response)


class ListXeroContacts:
    """List contacts from Xero, optionally filtered by name."""

    def __init__(self, xero_client: AbstractXeroClient) -> None:
        self._xero_client = xero_client

    async def execute(
        self,
        connection_id: str,
        search: str | None = None,
    ) -> list[XeroContactResult]:
        response = await self._xero_client.list_contacts(
            connection_id=connection_id,
            search=search,
        )
        contacts = response.get("Contacts", [])
        return [
            XeroContactResult(
                contact_id=c["ContactID"],
                name=c.get("Name", ""),
                email=c.get("EmailAddress") or None,
            )
            for c in contacts
        ]


class ListXeroAccounts:
    """List accounts (chart of accounts) from Xero, optionally filtered by status."""

    def __init__(self, xero_client: AbstractXeroClient) -> None:
        self._xero_client = xero_client

    async def execute(
        self,
        connection_id: str,
        status: str | None = None,
    ) -> list[XeroAccountResult]:
        response = await self._xero_client.list_accounts(
            connection_id=connection_id,
            status=status,
        )
        accounts = response.get("Accounts", [])
        return [
            XeroAccountResult(
                account_id=a["AccountID"],
                code=a.get("Code", ""),
                name=a.get("Name", ""),
                type=a.get("Type", ""),
                status=a.get("Status", ""),
            )
            for a in accounts
        ]


class ListXeroTaxRates:
    """List tax rates from Xero, optionally filtered by status."""

    def __init__(self, xero_client: AbstractXeroClient) -> None:
        self._xero_client = xero_client

    async def execute(
        self,
        connection_id: str,
        status: str | None = None,
    ) -> list[XeroTaxRateResult]:
        response = await self._xero_client.list_tax_rates(
            connection_id=connection_id,
            status=status,
        )
        tax_rates = response.get("TaxRates", [])
        return [
            XeroTaxRateResult(
                name=t.get("Name", ""),
                tax_type=t.get("TaxType", ""),
                status=t.get("Status", ""),
                effective_rate=t.get("EffectiveRate"),
            )
            for t in tax_rates
        ]


class VoidXeroInvoice:
    """Void a Xero invoice, marking it as VOIDED."""

    def __init__(
        self,
        xero_client: AbstractXeroClient,
        idempotency_store: AbstractIdempotencyStore,
        idempotency_ttl_seconds: int,
    ) -> None:
        self._xero_client = xero_client
        self._idempotency_store = idempotency_store
        self._idempotency_ttl_seconds = idempotency_ttl_seconds

    async def execute(
        self,
        connection_id: str,
        invoice_id: str,
        idempotency_key: str,
    ) -> XeroInvoiceResult:
        idem_key = f"idempotency:{_OP_VOID_INVOICE}:{idempotency_key}"

        cached = await self._idempotency_store.get(idem_key)
        if cached:
            logger.debug("Idempotency cache hit for key %r", idempotency_key)
            return XeroInvoiceResult(
                invoice_id=cached["invoice_id"],
                status=cached["status"],
            )

        response = await self._xero_client.update_invoice_status(
            connection_id=connection_id,
            invoice_id=invoice_id,
            status="VOIDED",
        )
        result = _extract_invoice_result(response)

        await self._idempotency_store.set(
            idem_key,
            {"invoice_id": result.invoice_id, "status": result.status},
            self._idempotency_ttl_seconds,
        )
        return result
