"""
OpenClaw agent webhook client.

Notifies the local OpenClaw process to resume an invoice pipeline after a
human has approved or rejected a draft bill.

POST {openclaw_webhook_base_url}/hooks/agent
Authorization: Bearer <token>
"""

from __future__ import annotations

import logging

import httpx

from app.core.domain.approval import ApprovalRequest
from app.core.ports.openclaw_webhook_client import AbstractOpenClawWebhookClient

logger = logging.getLogger(__name__)

_WEBHOOK_PATH = "/hooks/agent"


class OpenClawWebhookClient(AbstractOpenClawWebhookClient):
    """Calls POST /hooks/agent on the OpenClaw process to resume the agent.

    This client is intentionally failure-tolerant: HTTP errors and network
    exceptions are caught, logged, and returned as short error strings so the
    approval decision is always persisted even when OpenClaw is temporarily
    unreachable.

    Parameters
    ----------
    http_client:
        Shared ``httpx.AsyncClient`` from app.state.
    webhook_base_url:
        Base URL of the OpenClaw process, e.g. ``http://127.0.0.1:18789``.
        The path ``/hooks/agent`` is appended automatically.
    token:
        Bearer token for the Authorization header (INTERNAL_API_KEY).
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        webhook_base_url: str,
        token: str,
    ) -> None:
        self._http = http_client
        self._base_url = webhook_base_url.rstrip("/")
        self._token = token

    async def notify_decision(
        self,
        approval: ApprovalRequest,
        decision: str,
        note: str | None = None,
    ) -> str:
        """POST the decision to OpenClaw and return ``"ok"`` or an error string.

        Never raises.  Any failure is captured in the return value so the
        caller can store it alongside the approval record.
        """
        url = f"{self._base_url}{_WEBHOOK_PATH}"
        payload = {
            "agentId": "main",
            "sessionKey": f"hook:invoice:{approval.invoice_case_id}",
            "message": (
                "🔁 Resume invoice from hook\n"
                f"approvalId={approval.approval_id}\n"
                f"invoiceCaseId={approval.invoice_case_id}\n"
                f"pdf_path={approval.pdf_path}\n"
                f"action={decision}\n"
                f"note={note or ''}"
            ),
            "name": "Invoice approval callback",
            "wakeMode": "now",
            "deliver": False,
            "timeoutSeconds": 120,
        }
        try:
            response = await self._http.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
            response.raise_for_status()
            logger.info(
                "OpenClaw webhook notified for approval %r (decision=%r, status=%d)",
                approval.approval_id,
                decision,
                response.status_code,
            )
            return "ok"
        except httpx.HTTPStatusError as exc:
            msg = f"HTTP {exc.response.status_code}"
            logger.warning(
                "OpenClaw webhook returned an error for approval %r: %s",
                approval.approval_id,
                msg,
            )
            return msg
        except Exception as exc:
            msg = f"error: {exc}"
            logger.warning(
                "OpenClaw webhook call failed for approval %r: %s",
                approval.approval_id,
                exc,
            )
            return msg
