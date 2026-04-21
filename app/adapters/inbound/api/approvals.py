"""
Approval flow routes.

Internal (protected by INTERNAL_API_KEY):
    POST /internal/approvals/register
        Called by the OpenClaw agent to register a new invoice approval.

Public (browser-facing — no auth beyond the opaque approvalId in the URL):
    GET  /approvals/{approvalId}/approve
    GET  /approvals/{approvalId}/reject
        Render a confirmation page with the invoice summary and a single
        action button.

    POST /approvals/{approvalId}/decision
        Receives the HTML form submission (decision=approved|rejected).
        Records the decision idempotently, calls the OpenClaw webhook, and
        renders a final status page.

Adding authentication to the public routes later
-------------------------------------------------
The ``public_router`` is currently defined without any dependencies.  To add
authentication (e.g. a signed URL token or SSO middleware), inject a
dependency into ``public_router`` or add Starlette middleware scoped to the
``/approvals`` prefix in ``main.py``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.adapters.inbound.api.dependencies import (
    get_get_approval,
    get_record_decision,
    get_register_approval,
)
from app.adapters.inbound.api.middleware import verify_api_key
from app.core.errors import ApprovalNotFoundError, DuplicateApprovalError, InvalidDecisionError
from app.core.use_cases.approval import GetApproval, RecordDecision, RegisterApproval

logger = logging.getLogger(__name__)

# ── Jinja2 template loader ────────────────────────────────────────────────────

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# ── Routers ───────────────────────────────────────────────────────────────────

# Internal: key-protected; only the OpenClaw agent calls this.
internal_router = APIRouter(
    prefix="/internal",
    tags=["approvals-internal"],
    dependencies=[Depends(verify_api_key)],
)

# Public: served to human browsers that clicked an approve/reject link.
public_router = APIRouter(
    prefix="/approvals",
    tags=["approvals-public"],
)


# ── Request / response models ─────────────────────────────────────────────────


class RegisterApprovalRequest(BaseModel):
    approvalId: str = Field(min_length=1)
    invoiceCaseId: str = Field(min_length=1)
    pdfPath: str = Field(min_length=1)
    invoiceNumber: str = Field(min_length=1)
    supplierName: str = Field(min_length=1)
    approveUrl: str = Field(min_length=1)
    rejectUrl: str = Field(min_length=1)


class ApprovalRegisteredResponse(BaseModel):
    approvalId: str
    status: str
    invoiceCaseId: str
    invoiceNumber: str
    supplierName: str
    createdAt: str  # ISO-8601 UTC


# ── Internal endpoint ─────────────────────────────────────────────────────────


@internal_router.post(
    "/approvals/register",
    response_model=ApprovalRegisteredResponse,
    status_code=201,
    summary="Register a new invoice approval request",
)
async def register_approval(
    body: RegisterApprovalRequest,
    use_case: RegisterApproval = Depends(get_register_approval),
) -> ApprovalRegisteredResponse:
    """Register a new invoice approval request from the OpenClaw agent.

    Idempotent: a second call with the **identical** payload is accepted and
    returns the stored record with ``status 200`` (the first call returns
    ``201``).  A second call with the same ``approvalId`` but a different
    payload is rejected with ``409 Conflict``.
    """
    try:
        approval = await use_case.execute(
            approval_id=body.approvalId,
            invoice_case_id=body.invoiceCaseId,
            pdf_path=body.pdfPath,
            invoice_number=body.invoiceNumber,
            supplier_name=body.supplierName,
            approve_url=body.approveUrl,
            reject_url=body.rejectUrl,
        )
    except DuplicateApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return ApprovalRegisteredResponse(
        approvalId=approval.approval_id,
        status=approval.status,
        invoiceCaseId=approval.invoice_case_id,
        invoiceNumber=approval.invoice_number,
        supplierName=approval.supplier_name,
        createdAt=approval.created_at.isoformat(),
    )


# ── Public browser endpoints ──────────────────────────────────────────────────


@public_router.get(
    "/{approval_id}/approve",
    response_class=HTMLResponse,
    summary="Render the approve confirmation page",
    include_in_schema=False,
)
async def show_approve_page(
    request: Request,
    approval_id: str,
    use_case: GetApproval = Depends(get_get_approval),
) -> HTMLResponse:
    """Render the approval confirmation page for a human to review and confirm."""
    return await _render_decision_page(
        request=request,
        approval_id=approval_id,
        action="approve",
        use_case=use_case,
    )


@public_router.get(
    "/{approval_id}/reject",
    response_class=HTMLResponse,
    summary="Render the reject confirmation page",
    include_in_schema=False,
)
async def show_reject_page(
    request: Request,
    approval_id: str,
    use_case: GetApproval = Depends(get_get_approval),
) -> HTMLResponse:
    """Render the rejection confirmation page for a human to review and confirm."""
    return await _render_decision_page(
        request=request,
        approval_id=approval_id,
        action="reject",
        use_case=use_case,
    )


async def _render_decision_page(
    request: Request,
    approval_id: str,
    action: str,
    use_case: GetApproval,
) -> HTMLResponse:
    """Shared helper that renders either the confirm or the already-decided page."""
    try:
        approval = await use_case.execute(approval_id)
    except ApprovalNotFoundError:
        logger.warning("Approval not found for GET page: %r", approval_id)
        return _templates.TemplateResponse(
            request,
            "approval_error.html",
            {"message": "Approval request not found."},
            status_code=404,
        )

    # Already decided — show the final status page immediately.
    if approval.status != "pending":
        return _templates.TemplateResponse(
            request,
            "approval_decided.html",
            {"approval": approval, "just_decided": False},
        )

    return _templates.TemplateResponse(
        request,
        "approval_confirm.html",
        {"approval": approval, "action": action},
    )


@public_router.post(
    "/{approval_id}/decision",
    response_class=HTMLResponse,
    summary="Record an approve/reject/needs-changes decision",
    include_in_schema=False,
)
async def record_decision(
    request: Request,
    approval_id: str,
    decision: str = Form(...),
    note: str | None = Form(None),
    use_case: RecordDecision = Depends(get_record_decision),
) -> HTMLResponse:
    """Receive the HTML form submission and persist the decision.

    The ``decision`` field must be ``"approved"``, ``"needs_changes"``, or
    ``"rejected"``.
    When ``decision`` is ``"needs_changes"`` the ``note`` field is required.
    After persisting, the OpenClaw webhook is called to resume the pipeline,
    and a final status page is rendered.

    Idempotent: re-submitting the same form returns the stored final page
    without re-calling the webhook.
    """
    if decision not in ("approved", "needs_changes", "rejected"):
        return _templates.TemplateResponse(
            request,
            "approval_error.html",
            {"message": "Invalid decision value."},
            status_code=400,
        )

    # Backend validation mirrors what the frontend enforces.
    clean_note = note.strip() if note else None
    if decision == "needs_changes" and not clean_note:
        return _templates.TemplateResponse(
            request,
            "approval_error.html",
            {"message": "A note is required when requesting changes."},
            status_code=400,
        )

    try:
        approval = await use_case.execute(
            approval_id=approval_id,
            decision=decision,
            note=clean_note,
            decision_source="web_form",
        )
    except ApprovalNotFoundError:
        logger.warning("Approval not found for POST decision: %r", approval_id)
        return _templates.TemplateResponse(
            request,
            "approval_error.html",
            {"message": "Approval request not found."},
            status_code=404,
        )
    except InvalidDecisionError as exc:
        return _templates.TemplateResponse(
            request,
            "approval_error.html",
            {"message": str(exc)},
            status_code=400,
        )

    return _templates.TemplateResponse(
        request,
        "approval_decided.html",
        {"approval": approval, "just_decided": True},
    )
