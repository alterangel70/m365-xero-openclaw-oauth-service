from abc import ABC, abstractmethod

from app.core.domain.approval import ApprovalRequest


class AbstractOpenClawWebhookClient(ABC):
    """Port for notifying the OpenClaw agent of an approval decision.

    The concrete implementation (OpenClawWebhookClient) lives in the outbound
    adapter layer and POSTs to POST /hooks/agent on the local OpenClaw process.

    The method never raises; errors are returned as short strings so they can
    be stored alongside the approval record for observability and recovery.
    """

    @abstractmethod
    async def notify_decision(
        self,
        approval: ApprovalRequest,
        decision: str,
        note: str | None = None,
    ) -> str:
        """Send the decision to OpenClaw and return "ok" or an error string.

        Parameters
        ----------
        approval:
            The decided approval record (read-only; the record is already
            saved before this call is made).
        decision:
            "approved", "needs_changes", or "rejected".
        note:
            Reviewer note; required when decision is "needs_changes".

        Returns
        -------
        str
            "ok" on HTTP 2xx; a short description on any failure.
        """
