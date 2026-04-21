"""
Domain exceptions used by the application layer.

These are framework-agnostic.  The inbound API adapter maps them to
HTTP status codes and the standard error envelope.
"""


class IntegrationError(Exception):
    """Base class for all domain-level errors in this service."""


class ConnectionMissingError(IntegrationError):
    """No token was found in the store for the requested connection_id.

    For Xero this means the OAuth authorization flow has not been completed.
    For Microsoft this should never occur under normal operation because
    client_credentials tokens are acquired automatically, but it is raised
    if the initial acquisition itself fails.
    """


class ConnectionExpiredError(IntegrationError):
    """A token is present but the refresh attempt was rejected by the provider.

    The connection must be re-authorized before further calls can be made.
    Only applicable to Xero (refresh-token-based flow).
    """


class ProviderUnavailableError(IntegrationError):
    """The external provider returned an unexpected error after all retries.

    Callers should treat this as a transient upstream failure and retry later.
    """


class LockTimeoutError(IntegrationError):
    """The distributed refresh lock could not be acquired within the wait window.

    Indicates an unexpectedly slow token refresh by another worker.
    """


class IdempotencyConflictError(IntegrationError):
    """A request with the same idempotency key but different parameters was received.

    The original request is still in progress or has already completed with
    different inputs, so the new request cannot be safely replayed.
    Not expected at the current call volume; included for correctness.
    """


class ApprovalNotFoundError(IntegrationError):
    """No approval request was found for the given approvalId."""


class DuplicateApprovalError(IntegrationError):
    """An approval with the same approvalId but a different payload was already registered.

    Callers must use a new, unique approvalId for each distinct invoice approval
    request.  A retry with the *identical* payload is accepted idempotently.
    """


class InvalidDecisionError(IntegrationError):
    """The supplied decision value or its accompanying data is invalid.

    Raised when:
    - decision is not one of approved / needs_changes / rejected.
    - decision is needs_changes but no note was provided.
    """
