"""
Logging configuration.

Provides:
  - configure_logging(settings): called once at startup; sets up the root
    logger for console output and, when SEQ_ENABLED=true, adds a Seq handler.
  - flush_seq_handler(): flush any buffered Seq records; call on shutdown.
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
    picks it up via ``record.log_props`` (populated below) and exposes it as
    a searchable property; the console formatter ignores it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        rid = _request_id_var.get("")
        record.request_id = rid
        # seqlog reads structured properties from record.log_props; inject
        # request_id there so it appears as a Seq property.
        if rid:
            if not hasattr(record, "log_props"):
                record.log_props = {}  # type: ignore[attr-defined]
            record.log_props["request_id"] = rid  # type: ignore[attr-defined]
        return True


# ---------------------------------------------------------------------------
# Seq handler reference (for flush-on-shutdown)
# ---------------------------------------------------------------------------

_seq_handler: logging.Handler | None = None


def flush_seq_handler() -> None:
    """Flush any buffered Seq log records and close the handler.

    Call this once during application shutdown to ensure no records are lost
    in the ``SeqLogHandler``'s internal queue.
    """
    if _seq_handler is not None:
        try:
            _seq_handler.flush()
        except Exception:
            pass  # best-effort; do not raise during shutdown


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
    global _seq_handler

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
        # Import SeqLogHandler directly to add it via root.addHandler().
        #
        # We must NOT use seqlog.log_to_seq() here because it calls
        # logging.basicConfig() internally, which is a no-op once any
        # handler has been added to the root logger (Python standard
        # behaviour).  The SeqLogHandler would be silently discarded.
        from seqlog.structured_logging import SeqLogHandler  # type: ignore[import-untyped]

        seq_handler = SeqLogHandler(
            server_url=settings.seq_url,
            api_key=settings.seq_api_key or None,
            # Flush after this many records are buffered.  10 is the default;
            # records also flush via the auto_flush_timeout below.
            batch_size=10,
            # Flush any buffered records to Seq every 2 seconds regardless of
            # batch_size.  Without this, startup logs (< 10 records) would
            # never be sent.
            auto_flush_timeout=2,
        )
        seq_handler.setLevel(getattr(logging, settings.seq_min_level, logging.INFO))
        root.addHandler(seq_handler)
        _seq_handler = seq_handler

        logging.getLogger(__name__).info(
            "Seq logging enabled",
            extra={"seq_url": settings.seq_url},
        )
