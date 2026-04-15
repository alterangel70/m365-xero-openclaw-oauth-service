"""
Xero HTTP client adapter.

Implements AbstractXeroClient by calling the Xero Accounting API
(https://api.xero.com/api.xro/2.0/).

Token acquisition is fully delegated to XeroTokenManager — this class
never touches Redis or OAuth credentials directly.

On receiving a 401 from Xero, the client forces a token refresh and
retries once (identical strategy to MSGraphClient).  Any other non-2xx
response raises ProviderUnavailableError.

The Xero-Tenant-ID header is required on every Xero API call and is
sourced from the stored TokenSet.xero_tenant_id.
"""

from __future__ import annotations

import logging

import httpx

from app.core.errors import ProviderUnavailableError
from app.core.ports.xero_client import AbstractXeroClient

from .token_manager import XeroTokenManager

logger = logging.getLogger(__name__)

_XERO_API_BASE = "https://api.xero.com/api.xro/2.0"


class XeroHttpClient(AbstractXeroClient):
    """Sends Xero Accounting API requests with automatic token management.

    All dependencies are injected; no FastAPI imports, no app.state.
    """

    def __init__(
        self,
        token_manager: XeroTokenManager,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._token_manager = token_manager
        self._http_client = http_client

    # ── AbstractXeroClient ─────────────────────────────────────────────────────

    async def create_invoice(
        self,
        connection_id: str,
        payload: dict,
    ) -> dict:
        """POST a new invoice to Xero."""
        return await self._request(
            "POST",
            f"{_XERO_API_BASE}/Invoices",
            connection_id=connection_id,
            json=payload,
        )

    async def update_invoice_status(
        self,
        connection_id: str,
        invoice_id: str,
        status: str,
    ) -> dict:
        """POST an invoice status update to Xero."""
        return await self._request(
            "POST",
            f"{_XERO_API_BASE}/Invoices/{invoice_id}",
            connection_id=connection_id,
            json={"Invoices": [{"InvoiceID": invoice_id, "Status": status}]},
        )

    async def get_invoice(
        self,
        connection_id: str,
        invoice_id: str,
    ) -> dict:
        """GET a single invoice from Xero."""
        return await self._request(
            "GET",
            f"{_XERO_API_BASE}/Invoices/{invoice_id}",
            connection_id=connection_id,
        )

    # ── Private ────────────────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        url: str,
        connection_id: str,
        **kwargs,
    ) -> dict:
        """Execute a Xero API request with 401-once retry.

        Builds Authorization and Xero-tenant-ID headers from the current
        token.  On a 401 response, forces a token refresh and retries once.
        Any other non-2xx response raises ProviderUnavailableError.
        """
        for attempt in range(2):
            token = await self._token_manager.get_valid_token(connection_id)

            if token.xero_tenant_id is None:
                raise ProviderUnavailableError(
                    f"Xero tenant ID is not set for connection {connection_id!r}. "
                    "Re-run the OAuth flow to populate it."
                )

            headers = {
                "Authorization": f"Bearer {token.access_token}",
                "Xero-tenant-ID": token.xero_tenant_id,
                "Accept": "application/json",
            }

            response = await self._http_client.request(
                method,
                url,
                headers=headers,
                **kwargs,
            )

            if response.status_code == 401 and attempt == 0:
                logger.warning(
                    "Xero returned 401 for connection %r — forcing token refresh",
                    connection_id,
                )
                # Mark the token stale by deleting from the store so that
                # XeroTokenManager will perform a fresh refresh on the next call.
                await self._token_manager._token_store.delete(connection_id)
                continue  # retry with forced token re-acquisition

            if not response.is_success:
                raise ProviderUnavailableError(
                    f"Xero API returned {response.status_code}: "
                    f"{response.text[:300]}"
                )

            logger.debug(
                "Xero %s %s → %s (connection=%r)",
                method,
                url,
                response.status_code,
                connection_id,
            )
            return response.json()

        # If we exit the loop without returning, both attempts returned 401.
        raise ProviderUnavailableError(
            f"Xero API returned 401 after token refresh "
            f"(connection={connection_id!r}, url={url!r})"
        )
