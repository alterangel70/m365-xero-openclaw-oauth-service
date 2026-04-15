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
