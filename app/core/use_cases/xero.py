"""
Xero use cases: invoice lifecycle operations.

Token acquisition and tenant ID resolution are fully handled by the
XeroHttpClient adapter.  These use cases do not interact with the token
store directly.

Idempotency keys are required for all write operations to prevent
duplicate invoices on OpenClaw retries.
"""

from __future__ import annotations

import logging

from app.core.domain.xero import XeroInvoice
from app.core.ports.idempotency_store import AbstractIdempotencyStore
from app.core.ports.xero_client import AbstractXeroClient
from app.core.use_cases.results import XeroInvoiceResult

logger = logging.getLogger(__name__)


class CreateXeroDraftInvoice:
    """Create a new DRAFT invoice in Xero."""

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
        raise NotImplementedError


class SubmitXeroInvoice:
    """Transition a Xero invoice from DRAFT to SUBMITTED (AUTHORISED in Xero terms)."""

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
        raise NotImplementedError


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
        raise NotImplementedError


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
        raise NotImplementedError
