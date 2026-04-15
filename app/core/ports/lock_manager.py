from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager


class AbstractLockManager(ABC):
    """Port for acquiring distributed locks.

    Used to serialize token refresh operations across concurrent workers,
    preventing multiple simultaneous refresh calls with the same credentials.

    The Redis implementation uses SET NX EX and a Lua safe-release script.
    Key pattern: lock:refresh:{connection_id}
    """

    @abstractmethod
    def acquire(self, key: str, ttl_seconds: int) -> AbstractAsyncContextManager[bool]:
        """Return an async context manager that attempts to acquire the named lock.

        The context manager yields True if the lock was acquired by this caller,
        or False if it was already held.  The lock is automatically released on
        context manager exit if it was acquired.

        Example
        -------
        async with lock_manager.acquire("lock:refresh:xero-acme", ttl_seconds=30) as acquired:
            if acquired:
                # This worker owns the lock; perform the refresh.
                ...
            else:
                # Another worker holds the lock; read the freshly refreshed token.
                ...
        """
