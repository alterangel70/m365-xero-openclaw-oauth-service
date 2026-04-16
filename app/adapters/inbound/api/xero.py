"""
Xero routes: invoice lifecycle endpoints.

All routes are protected by the INTERNAL_API_KEY header (enforced at
router level via verify_api_key).

Routes
------
POST /v1/xero/invoices
    Create a new DRAFT invoice.  Idempotency-Key header is mandatory.

POST /v1/xero/invoices/{invoice_id}/submit
    Transition an invoice to AUTHORISED.  Idempotency-Key header is mandatory.

POST /v1/xero/invoices/{invoice_id}/void
    Void an invoice.  Idempotency-Key header is mandatory.

GET /v1/xero/contacts
    List contacts, optionally filtered by name.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.adapters.inbound.api.dependencies import (
    get_create_xero_draft_invoice,
    get_get_xero_invoice,
    get_list_xero_contacts,
    get_submit_xero_invoice,
    get_void_xero_invoice,
)
from app.adapters.inbound.api.middleware import verify_api_key
from app.core.domain.xero import XeroInvoice, XeroLineItem
from app.core.use_cases.xero import (
    CreateXeroDraftInvoice,
    GetXeroInvoice,
    ListXeroContacts,
    SubmitXeroInvoice,
    VoidXeroInvoice,
)

router = APIRouter(
    prefix="/v1/xero",
    tags=["xero"],
    dependencies=[Depends(verify_api_key)],
)


# ── Request / Response models ─────────────────────────────────────────────────


class LineItemRequest(BaseModel):
    description: str
    quantity: Decimal
    unit_amount: Decimal
    account_code: str
    tax_type: str | None = None


class CreateInvoiceRequest(BaseModel):
    connection_id: str
    contact_id: str
    line_items: list[LineItemRequest] = Field(min_length=1)
    due_date: date
    currency_code: str
    reference: str | None = None


class InvoiceActionRequest(BaseModel):
    """Body for invoice state-transition endpoints (submit / void)."""

    connection_id: str


class InvoiceResponse(BaseModel):
    invoice_id: str
    status: str


class ContactResponse(BaseModel):
    contact_id: str
    name: str
    email: str | None = None


class ContactListResponse(BaseModel):
    contacts: list[ContactResponse]


# ── Routes ────────────────────────────────────────────────────────────────────


@router.post("/invoices", response_model=InvoiceResponse, status_code=201)
async def create_invoice(
    body: CreateInvoiceRequest,
    use_case: CreateXeroDraftInvoice = Depends(get_create_xero_draft_invoice),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> InvoiceResponse:
    """Create a new DRAFT invoice in Xero.

    An Idempotency-Key header is required to prevent duplicate creation
    on retries.  Returns 400 if the header is absent.
    """
    if not idempotency_key:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key header is required for invoice creation.",
        )

    invoice = XeroInvoice(
        contact_id=body.contact_id,
        line_items=tuple(
            XeroLineItem(
                description=li.description,
                quantity=li.quantity,
                unit_amount=li.unit_amount,
                account_code=li.account_code,
                tax_type=li.tax_type,
            )
            for li in body.line_items
        ),
        due_date=body.due_date,
        currency_code=body.currency_code,
        reference=body.reference,
    )
    result = await use_case.execute(
        connection_id=body.connection_id,
        invoice=invoice,
        idempotency_key=idempotency_key,
    )
    return InvoiceResponse(invoice_id=result.invoice_id, status=result.status)


@router.get("/invoices/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(
    invoice_id: str,
    connection_id: str,
    use_case: GetXeroInvoice = Depends(get_get_xero_invoice),
) -> InvoiceResponse:
    """Retrieve the current state of a Xero invoice.

    connection_id is passed as a query parameter.
    """
    result = await use_case.execute(
        connection_id=connection_id,
        invoice_id=invoice_id,
    )
    return InvoiceResponse(invoice_id=result.invoice_id, status=result.status)


@router.post("/invoices/{invoice_id}/submit", response_model=InvoiceResponse)
async def submit_invoice(
    invoice_id: str,
    body: InvoiceActionRequest,
    use_case: SubmitXeroInvoice = Depends(get_submit_xero_invoice),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> InvoiceResponse:
    """Transition a DRAFT invoice to AUTHORISED (submitted).

    An Idempotency-Key header is required.
    """
    if not idempotency_key:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key header is required for invoice submission.",
        )

    result = await use_case.execute(
        connection_id=body.connection_id,
        invoice_id=invoice_id,
        idempotency_key=idempotency_key,
    )
    return InvoiceResponse(invoice_id=result.invoice_id, status=result.status)


@router.post("/invoices/{invoice_id}/void", response_model=InvoiceResponse)
async def void_invoice(
    invoice_id: str,
    body: InvoiceActionRequest,
    use_case: VoidXeroInvoice = Depends(get_void_xero_invoice),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> InvoiceResponse:
    """Void a Xero invoice.

    An Idempotency-Key header is required.
    """
    if not idempotency_key:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key header is required for invoice voiding.",
        )

    result = await use_case.execute(
        connection_id=body.connection_id,
        invoice_id=invoice_id,
        idempotency_key=idempotency_key,
    )
    return InvoiceResponse(invoice_id=result.invoice_id, status=result.status)


@router.get("/contacts", response_model=ContactListResponse)
async def list_contacts(
    connection_id: str,
    search: str | None = None,
    use_case: ListXeroContacts = Depends(get_list_xero_contacts),
) -> ContactListResponse:
    """List Xero contacts, optionally filtered by name.

    Pass ?search=Acme to filter contacts whose name contains the search term.
    Returns contact_id, name and email for each match.
    """
    results = await use_case.execute(connection_id=connection_id, search=search)
    return ContactListResponse(
        contacts=[
            ContactResponse(contact_id=r.contact_id, name=r.name, email=r.email)
            for r in results
        ]
    )
