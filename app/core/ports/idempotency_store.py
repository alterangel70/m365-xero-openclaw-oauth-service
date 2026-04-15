from abc import ABC, abstractmethod


class AbstractIdempotencyStore(ABC):
    """Port for caching the results of side-effecting operations.

    Prevents duplicate provider calls when OpenClaw retries a request
    that already succeeded.

    Key pattern: idempotency:{operation}:{idempotency_key}
    """

    @abstractmethod
    async def get(self, key: str) -> dict | None:
        """Return the cached result dict, or None if not found / expired."""

    @abstractmethod
    async def set(self, key: str, result: dict, ttl_seconds: int) -> None:
        """Store a result dict under the given key with an expiry."""
