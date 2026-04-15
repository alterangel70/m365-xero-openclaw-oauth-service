from abc import ABC, abstractmethod


class AbstractXeroClient(ABC):
    """Port for outbound Xero REST API calls.

    Token acquisition and refresh are fully encapsulated within the adapter
    implementation (XeroHttpClient).  Callers pass only the connection_id;
    the adapter resolves the stored token and xero_tenant_id internally.
    """

    @abstractmethod
    async def create_invoice(
        self,
        connection_id: str,
        payload: dict,
    ) -> dict:
        """POST a new invoice to Xero.

        payload is the Xero Invoices API request body as a Python dict.
        Returns the Xero API response body as a dict.
        Raises ConnectionMissingError if no token exists for connection_id.
        Raises ConnectionExpiredError if the refresh token has been revoked.
        Raises ProviderUnavailableError on unrecoverable provider errors.
        """

    @abstractmethod
    async def update_invoice_status(
        self,
        connection_id: str,
        invoice_id: str,
        status: str,
    ) -> dict:
        """POST an invoice status update to Xero (e.g. SUBMITTED or VOIDED).

        Returns the Xero API response body as a dict.
        """

    @abstractmethod
    async def get_invoice(
        self,
        connection_id: str,
        invoice_id: str,
    ) -> dict:
        """GET a single invoice from Xero by its InvoiceID.

        Returns the Xero API response body as a dict.
        """
