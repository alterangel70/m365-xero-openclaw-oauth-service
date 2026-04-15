from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class XeroLineItem:
    """A single line item on a Xero invoice."""

    description: str
    quantity: Decimal
    unit_amount: Decimal
    account_code: str
    tax_type: str | None = None


@dataclass(frozen=True)
class XeroInvoice:
    """The data required to create or represent a Xero invoice (type ACCREC).

    This value object is passed from OpenClaw to the use case layer.
    The use case translates it into the Xero REST API payload format
    before calling the adapter.
    """

    contact_id: str
    line_items: tuple[XeroLineItem, ...]
    due_date: date
    currency_code: str
    reference: str | None = None
