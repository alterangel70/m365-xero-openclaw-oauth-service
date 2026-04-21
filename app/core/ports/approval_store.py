from abc import ABC, abstractmethod

from app.core.domain.approval import ApprovalRequest


class AbstractApprovalStore(ABC):
    """Port for persisting and retrieving invoice approval requests.

    Each approval is addressed by its opaque ``approval_id``.

    Implementations:
        RedisApprovalStore — stores each record as a Redis hash.

    Key pattern: approval:{approvalId}
    """

    @abstractmethod
    async def save(self, approval: ApprovalRequest) -> None:
        """Persist (insert or replace) an approval request."""

    @abstractmethod
    async def load(self, approval_id: str) -> ApprovalRequest | None:
        """Return the approval for the given ID, or None if not found."""
