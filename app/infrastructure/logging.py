"""
Logging configuration.

Provides:
  - configure_logging(settings): called once at startup; sets up the root
    logger for console output and, when SEQ_ENABLED=true, adds a Seq handler.
  - Request-ID context helpers used by RequestIdMiddleware and the logging
    filter so that every log line emitted during a request carries the same
    correlation ID.

Usage
-----
Call ``configure_logging(settings)`` exactly once, inside the FastAPI lifespan,
before any log output is expected.

All existing code that uses ``logging.getLogger(__name__)`` continues to work
unchanged.
"""

import logging
from contextvars import ContextVar

# ---------------------------------------------------------------------------
# Request-ID context variable
# ---------------------------------------------------------------------------

# ContextVar is per-async-task; asyncio propagates it correctly when a new
# task is spawned, so the ID set by RequestIdMiddleware stays in scope for
# the entire request-handling chain without any extra effort.
_request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def get_request_id() -> str:
    """Return the request ID bound to the current async task, or ''."""
    return _request_id_var.get()


def set_request_id(value: str) -> None:
    """Bind a request ID to the current async task."""
    _request_id_var.set(value)


# ---------------------------------------------------------------------------
# Logging filter
# ---------------------------------------------------------------------------


class _RequestIdFilter(logging.Filter):
    """Inject the current request_id into every log record.

    Attaching this filter to the root logger means every handler — console
    and Seq alike — receives records with a ``request_id`` attribute.  Seq
    (via seqlog's ``support_extra_properties=True``) exposes it as a
    searchable property; the console formatter ignores it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get("")
        return True


# ---------------------------------------------------------------------------
# Public configuration entry point
# ---------------------------------------------------------------------------


def configure_logging(settings) -> None:
    """Configure the root logger.

    Always sets up a console (stdout) handler with a human-readable format.
    When ``SEQ_ENABLED=true`` and ``SEQ_URL`` is non-empty, also adds a Seq
    handler that ships structured log events (including ``request_id``) to
    the configured Seq instance.

    Parameters
    ----------
    settings:
        The application ``Settings`` object; accessed for ``log_level``,
        ``seq_enabled``, ``seq_url``, ``seq_api_key``, and ``seq_min_level``.
    """
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(settings.log_level)

    # Console handler – always present.
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root.addHandler(console_handler)

    # Attach the request-ID filter to the root logger so every record –
    # from any logger, going to any handler – carries the current request_id.
    root.addFilter(_RequestIdFilter())

    if settings.seq_enabled and settings.seq_url:
        import seqlog

        seqlog.log_to_seq(
            server_url=settings.seq_url,
            api_key=settings.seq_api_key or None,
            level=getattr(logging, settings.seq_min_level, logging.INFO),
            # Respect the level already applied to the root logger above.
            override_root_level=False,
            # Send non-standard record attributes (e.g. request_id) as Seq
            # structured properties so they are searchable and filterable.
            support_extra_properties=True,
        )
        logging.getLogger(__name__).info(
            "Seq logging enabled",
            extra={"seq_url": settings.seq_url},
        )
