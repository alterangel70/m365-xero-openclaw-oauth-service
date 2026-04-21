"""
Redis-backed approval store.

Each approval request is persisted as a Redis hash under the key
``approval:{approvalId}``.  No TTL is applied; approval records are retained
indefinitely so historical decisions can always be looked up.
"""

from __future__ import annotations

from datetime import datetime

import redis.asyncio as aioredis

from app.core.domain.approval import ApprovalRequest, ApprovalStatus, ApprovalDecision
from app.core.ports.approval_store import AbstractApprovalStore

_KEY_PREFIX = "approval"

# Sentinel for absent optional fields.  Redis hashes cannot store None; an
# empty string is used as the absent sentinel (mirrors redis_token_store.py).
_ABSENT = ""


def _key(approval_id: str) -> str:
    return f"{_KEY_PREFIX}:{approval_id}"


def _to_mapping(approval: ApprovalRequest) -> dict[str, str]:
    """Serialize an ApprovalRequest to a flat string dict for Redis HSET."""
    return {
        "approval_id": approval.approval_id,
        "invoice_case_id": approval.invoice_case_id,
        "pdf_path": approval.pdf_path,
        "invoice_number": approval.invoice_number,
        "supplier_name": approval.supplier_name,
        "approve_url": approval.approve_url,
        "reject_url": approval.reject_url,
        "status": approval.status,
        "decision": approval.decision or _ABSENT,
        "note": approval.note or _ABSENT,
        "created_at": approval.created_at.isoformat(),
        "decided_at": (
            approval.decided_at.isoformat() if approval.decided_at else _ABSENT
        ),
        "decision_source": approval.decision_source or _ABSENT,
        "webhook_sent_at": (
            approval.webhook_sent_at.isoformat()
            if approval.webhook_sent_at
            else _ABSENT
        ),
        "webhook_result": approval.webhook_result or _ABSENT,
    }


def _from_mapping(data: dict[str, str]) -> ApprovalRequest:
    """Deserialize a Redis hash back into an immutable ApprovalRequest.

    Backward compat: records written before the needs_changes refactor stored
    status as "approved" or "rejected" directly.  These are migrated on read
    to status="resolved" with the old value promoted to decision.
    """
    raw_status = data["status"]
    # Migrate old-style records (status was the decision value).
    if raw_status in ("approved", "rejected"):
        status: ApprovalStatus = "resolved"
        decision: ApprovalDecision | None = raw_status  # type: ignore[assignment]
    else:
        status = raw_status  # type: ignore[assignment]
        decision = data.get("decision") or None  # type: ignore[assignment]
    return ApprovalRequest(
        approval_id=data["approval_id"],
        invoice_case_id=data["invoice_case_id"],
        pdf_path=data["pdf_path"],
        invoice_number=data["invoice_number"],
        supplier_name=data["supplier_name"],
        approve_url=data["approve_url"],
        reject_url=data["reject_url"],
        status=status,
        decision=decision,
        note=data.get("note") or None,
        created_at=datetime.fromisoformat(data["created_at"]),
        decided_at=(
            datetime.fromisoformat(data["decided_at"])
            if data.get("decided_at")
            else None
        ),
        decision_source=data.get("decision_source") or None,
        webhook_sent_at=(
            datetime.fromisoformat(data["webhook_sent_at"])
            if data.get("webhook_sent_at")
            else None
        ),
        webhook_result=data.get("webhook_result") or None,
    )


class RedisApprovalStore(AbstractApprovalStore):
    """Persists approval requests as Redis hashes.

    Key pattern : approval:{approvalId}
    TTL         : none — records are retained indefinitely.

    A save() always replaces the full record atomically via HSET, which
    accepts a mapping and updates all fields in a single round-trip.
    """

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def save(self, approval: ApprovalRequest) -> None:
        """Atomically insert or replace the approval record."""
        await self._redis.hset(_key(approval.approval_id), mapping=_to_mapping(approval))

    async def load(self, approval_id: str) -> ApprovalRequest | None:
        """Return the stored approval, or None if the key does not exist."""
        data = await self._redis.hgetall(_key(approval_id))
        if not data:
            return None
        return _from_mapping(data)
