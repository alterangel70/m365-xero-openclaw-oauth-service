"""
Approval flow use cases.

RegisterApproval  – record a new invoice approval request from the agent.
GetApproval       – look up the current state of an approval by its ID.
RecordDecision    – persist a human decision and notify the OpenClaw webhook.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.core.domain.approval import ApprovalRequest
from app.core.errors import ApprovalNotFoundError, DuplicateApprovalError
from app.core.ports.approval_store import AbstractApprovalStore
from app.core.ports.openclaw_webhook_client import AbstractOpenClawWebhookClient

logger = logging.getLogger(__name__)


class RegisterApproval:
    """Register a new invoice approval request.

    Idempotent: a second call with the *identical* payload for the same
    approvalId returns the existing record without mutating it.

    Raises
    ------
    DuplicateApprovalError
        If an approval with the same approvalId already exists but with a
        different invoiceNumber or supplierName.
    """

    def __init__(self, approval_store: AbstractApprovalStore) -> None:
        self._store = approval_store

    async def execute(
        self,
        *,
        approval_id: str,
        invoice_case_id: str,
        pdf_path: str,
        invoice_number: str,
        supplier_name: str,
        approve_url: str,
        reject_url: str,
    ) -> ApprovalRequest:
        existing = await self._store.load(approval_id)
        if existing is not None:
            # Accept an identical retry as an idempotent success.
            if (
                existing.invoice_case_id == invoice_case_id
                and existing.invoice_number == invoice_number
                and existing.supplier_name == supplier_name
            ):
                logger.info(
                    "Idempotent approval registration for %r (status=%r)",
                    approval_id,
                    existing.status,
                )
                return existing
            raise DuplicateApprovalError(
                f"An approval with approvalId={approval_id!r} already exists "
                "with a different payload."
            )

        approval = ApprovalRequest(
            approval_id=approval_id,
            invoice_case_id=invoice_case_id,
            pdf_path=pdf_path,
            invoice_number=invoice_number,
            supplier_name=supplier_name,
            approve_url=approve_url,
            reject_url=reject_url,
            status="pending",
            created_at=datetime.now(tz=timezone.utc),
        )
        await self._store.save(approval)
        logger.info(
            "Registered approval %r for invoice %r (case=%r)",
            approval_id,
            invoice_number,
            invoice_case_id,
        )
        return approval


class GetApproval:
    """Retrieve the current state of an approval request.

    Raises
    ------
    ApprovalNotFoundError
        If no record exists for the given approvalId.
    """

    def __init__(self, approval_store: AbstractApprovalStore) -> None:
        self._store = approval_store

    async def execute(self, approval_id: str) -> ApprovalRequest:
        approval = await self._store.load(approval_id)
        if approval is None:
            raise ApprovalNotFoundError(
                f"No approval found for approvalId={approval_id!r}"
            )
        return approval


class RecordDecision:
    """Persist a human decision and notify the OpenClaw agent via webhook.

    Idempotent: if an approval is already in a final state (approved or
    rejected), the stored record is returned without re-calling the webhook.

    The webhook call is failure-tolerant: if OpenClaw is unreachable, the
    decision is still saved and the error is stored in ``webhook_result`` for
    observability and manual recovery.

    Raises
    ------
    ApprovalNotFoundError
        If no approval exists for the given approvalId.
    """

    def __init__(
        self,
        approval_store: AbstractApprovalStore,
        webhook_client: AbstractOpenClawWebhookClient,
    ) -> None:
        self._store = approval_store
        self._webhook = webhook_client

    async def execute(
        self,
        approval_id: str,
        decision: str,
        decision_source: str = "web_form",
    ) -> ApprovalRequest:
        approval = await self._store.load(approval_id)
        if approval is None:
            raise ApprovalNotFoundError(
                f"No approval found for approvalId={approval_id!r}"
            )

        # Already decided — idempotent: return the stored final state.
        if approval.status != "pending":
            logger.info(
                "Approval %r is already in status %r; returning stored state.",
                approval_id,
                approval.status,
            )
            return approval

        now = datetime.now(tz=timezone.utc)

        # Notify OpenClaw; the client never raises — errors become strings.
        webhook_result = await self._webhook.notify_decision(
            approval=approval,
            decision=decision,
        )

        updated = ApprovalRequest(
            approval_id=approval.approval_id,
            invoice_case_id=approval.invoice_case_id,
            pdf_path=approval.pdf_path,
            invoice_number=approval.invoice_number,
            supplier_name=approval.supplier_name,
            approve_url=approval.approve_url,
            reject_url=approval.reject_url,
            status=decision,  # type: ignore[arg-type]
            created_at=approval.created_at,
            decided_at=now,
            decision_source=decision_source,
            webhook_sent_at=now,
            webhook_result=webhook_result,
        )
        await self._store.save(updated)
        logger.info(
            "Decision %r recorded for approval %r (webhook=%s)",
            decision,
            approval_id,
            webhook_result,
        )
        return updated
