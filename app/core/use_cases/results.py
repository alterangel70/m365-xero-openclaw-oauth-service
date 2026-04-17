"""
Shared result value objects returned by use cases.

These are plain frozen dataclasses — no framework dependencies.
The inbound API adapter maps them to JSON response bodies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class MessageResult:
    """Result of a successful Teams message or Adaptive Card delivery."""

    message_id: str


@dataclass(frozen=True)
class XeroInvoiceResult:
    """Result of any Xero invoice operation."""

    invoice_id: str
    status: str


@dataclass(frozen=True)
class AuthUrlResult:
    """Result of building a Xero OAuth authorization URL."""

    authorization_url: str
    state: str


@dataclass(frozen=True)
class ConnectionStatus:
    """The current validity state of a stored provider connection."""

    status: Literal["valid", "expired", "missing"]


@dataclass(frozen=True)
class DeviceCodeResult:
    """Result of initiating a Microsoft device code flow.

    user_code and verification_uri are surfaced to the operator so they can
    complete the browser authorization step.  device_code and interval are
    retained by the caller for the subsequent polling loop.
    """

    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int
    message: str


@dataclass(frozen=True)
class XeroContactResult:
    """A single Xero contact returned by the list contacts use case."""

    contact_id: str
    name: str
    email: str | None


@dataclass(frozen=True)
class XeroAccountResult:
    """A single Xero account (chart of accounts) entry."""

    account_id: str
    code: str
    name: str
    type: str
    status: str


@dataclass(frozen=True)
class XeroTaxRateResult:
    """A single Xero tax rate entry."""

    name: str
    tax_type: str
    status: str
    effective_rate: float | None
