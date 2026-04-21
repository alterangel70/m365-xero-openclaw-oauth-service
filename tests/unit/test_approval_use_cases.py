"""
Unit tests for approval use cases: RegisterApproval, GetApproval, RecordDecision.

All external collaborators (approval store, webhook client) are replaced with
AsyncMocks so no real Redis or HTTP connection is required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.core.domain.approval import ApprovalRequest
from app.core.errors import ApprovalNotFoundError, DuplicateApprovalError
from app.core.use_cases.approval import GetApproval, RecordDecision, RegisterApproval

# ── Shared fixtures & constants ───────────────────────────────────────────────

APPROVAL_ID = "approval-abc-001"
INVOICE_CASE_ID = "case-xyz-123"
PDF_PATH = "/storage/invoices/case-xyz-123.pdf"
INVOICE_NUMBER = "INV-001"
SUPPLIER_NAME = "Acme Corp"
APPROVE_URL = "http://localhost:8080/approvals/approval-abc-001/approve"
REJECT_URL = "http://localhost:8080/approvals/approval-abc-001/reject"
_CREATED_AT = datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)
_DECIDED_AT = datetime(2026, 4, 20, 13, 0, 0, tzinfo=timezone.utc)


def _make_pending() -> ApprovalRequest:
    return ApprovalRequest(
        approval_id=APPROVAL_ID,
        invoice_case_id=INVOICE_CASE_ID,
        pdf_path=PDF_PATH,
        invoice_number=INVOICE_NUMBER,
        supplier_name=SUPPLIER_NAME,
        approve_url=APPROVE_URL,
        reject_url=REJECT_URL,
        status="pending",
        created_at=_CREATED_AT,
    )


def _make_decided(status: str) -> ApprovalRequest:
    return ApprovalRequest(
        approval_id=APPROVAL_ID,
        invoice_case_id=INVOICE_CASE_ID,
        pdf_path=PDF_PATH,
        invoice_number=INVOICE_NUMBER,
        supplier_name=SUPPLIER_NAME,
        approve_url=APPROVE_URL,
        reject_url=REJECT_URL,
        status=status,  # type: ignore[arg-type]
        created_at=_CREATED_AT,
        decided_at=_DECIDED_AT,
        decision_source="web_form",
        webhook_result="ok",
    )


@pytest.fixture
def mock_store() -> AsyncMock:
    store = AsyncMock()
    store.save = AsyncMock()
    store.load = AsyncMock(return_value=None)
    return store


@pytest.fixture
def mock_webhook() -> AsyncMock:
    client = AsyncMock()
    client.notify_decision = AsyncMock(return_value="ok")
    return client


# ── RegisterApproval ──────────────────────────────────────────────────────────


class TestRegisterApproval:
    @pytest.fixture
    def use_case(self, mock_store) -> RegisterApproval:
        return RegisterApproval(approval_store=mock_store)

    async def test_creates_new_approval(self, use_case, mock_store):
        result = await use_case.execute(
            approval_id=APPROVAL_ID,
            invoice_case_id=INVOICE_CASE_ID,
            pdf_path=PDF_PATH,
            invoice_number=INVOICE_NUMBER,
            supplier_name=SUPPLIER_NAME,
            approve_url=APPROVE_URL,
            reject_url=REJECT_URL,
        )

        assert result.approval_id == APPROVAL_ID
        assert result.status == "pending"
        assert result.invoice_number == INVOICE_NUMBER
        assert result.supplier_name == SUPPLIER_NAME
        assert result.decided_at is None
        assert result.created_at is not None
        mock_store.save.assert_awaited_once_with(result)

    async def test_idempotent_retry_returns_existing_without_save(
        self, use_case, mock_store
    ):
        existing = _make_pending()
        mock_store.load.return_value = existing

        result = await use_case.execute(
            approval_id=APPROVAL_ID,
            invoice_case_id=INVOICE_CASE_ID,
            pdf_path=PDF_PATH,
            invoice_number=INVOICE_NUMBER,
            supplier_name=SUPPLIER_NAME,
            approve_url=APPROVE_URL,
            reject_url=REJECT_URL,
        )

        assert result is existing
        mock_store.save.assert_not_awaited()

    async def test_duplicate_different_invoice_number_raises(
        self, use_case, mock_store
    ):
        mock_store.load.return_value = _make_pending()

        with pytest.raises(DuplicateApprovalError, match="already exists"):
            await use_case.execute(
                approval_id=APPROVAL_ID,
                invoice_case_id=INVOICE_CASE_ID,
                pdf_path=PDF_PATH,
                invoice_number="INV-DIFFERENT",
                supplier_name=SUPPLIER_NAME,
                approve_url=APPROVE_URL,
                reject_url=REJECT_URL,
            )

    async def test_duplicate_different_supplier_raises(self, use_case, mock_store):
        mock_store.load.return_value = _make_pending()

        with pytest.raises(DuplicateApprovalError):
            await use_case.execute(
                approval_id=APPROVAL_ID,
                invoice_case_id=INVOICE_CASE_ID,
                pdf_path=PDF_PATH,
                invoice_number=INVOICE_NUMBER,
                supplier_name="OtherCorp",
                approve_url=APPROVE_URL,
                reject_url=REJECT_URL,
            )


# ── GetApproval ───────────────────────────────────────────────────────────────


class TestGetApproval:
    @pytest.fixture
    def use_case(self, mock_store) -> GetApproval:
        return GetApproval(approval_store=mock_store)

    async def test_returns_existing_approval(self, use_case, mock_store):
        approval = _make_pending()
        mock_store.load.return_value = approval

        result = await use_case.execute(APPROVAL_ID)

        assert result is approval
        mock_store.load.assert_awaited_once_with(APPROVAL_ID)

    async def test_missing_id_raises(self, use_case, mock_store):
        mock_store.load.return_value = None

        with pytest.raises(ApprovalNotFoundError, match="approval-missing"):
            await use_case.execute("approval-missing")


# ── RecordDecision ────────────────────────────────────────────────────────────


class TestRecordDecision:
    @pytest.fixture
    def use_case(self, mock_store, mock_webhook) -> RecordDecision:
        return RecordDecision(
            approval_store=mock_store,
            webhook_client=mock_webhook,
        )

    async def test_approved_decision_persisted(
        self, use_case, mock_store, mock_webhook
    ):
        mock_store.load.return_value = _make_pending()

        result = await use_case.execute(APPROVAL_ID, "approved")

        assert result.status == "approved"
        assert result.decided_at is not None
        assert result.decision_source == "web_form"
        assert result.webhook_result == "ok"
        mock_store.save.assert_awaited_once()
        mock_webhook.notify_decision.assert_awaited_once()

    async def test_rejected_decision_persisted(
        self, use_case, mock_store, mock_webhook
    ):
        mock_store.load.return_value = _make_pending()

        result = await use_case.execute(APPROVAL_ID, "rejected")

        assert result.status == "rejected"
        assert result.webhook_result == "ok"

    async def test_custom_decision_source_stored(
        self, use_case, mock_store, mock_webhook
    ):
        mock_store.load.return_value = _make_pending()

        result = await use_case.execute(APPROVAL_ID, "approved", decision_source="api")

        assert result.decision_source == "api"

    async def test_already_decided_returns_stored_without_webhook(
        self, use_case, mock_store, mock_webhook
    ):
        decided = _make_decided("approved")
        mock_store.load.return_value = decided

        result = await use_case.execute(APPROVAL_ID, "approved")

        assert result is decided
        mock_store.save.assert_not_awaited()
        mock_webhook.notify_decision.assert_not_awaited()

    async def test_already_rejected_returns_stored_without_webhook(
        self, use_case, mock_store, mock_webhook
    ):
        decided = _make_decided("rejected")
        mock_store.load.return_value = decided

        result = await use_case.execute(APPROVAL_ID, "rejected")

        assert result is decided
        mock_store.save.assert_not_awaited()

    async def test_not_found_raises(self, use_case, mock_store):
        mock_store.load.return_value = None

        with pytest.raises(ApprovalNotFoundError):
            await use_case.execute("missing-id", "approved")

    async def test_webhook_error_string_stored_decision_still_saved(
        self, use_case, mock_store, mock_webhook
    ):
        mock_store.load.return_value = _make_pending()
        mock_webhook.notify_decision.return_value = "HTTP 503"

        result = await use_case.execute(APPROVAL_ID, "approved")

        # The decision is still recorded even when the webhook fails.
        assert result.status == "approved"
        assert result.webhook_result == "HTTP 503"
        mock_store.save.assert_awaited_once()

    async def test_webhook_notify_called_with_correct_approval_and_decision(
        self, use_case, mock_store, mock_webhook
    ):
        pending = _make_pending()
        mock_store.load.return_value = pending

        await use_case.execute(APPROVAL_ID, "rejected")

        mock_webhook.notify_decision.assert_awaited_once_with(
            approval=pending,
            decision="rejected",
        )
