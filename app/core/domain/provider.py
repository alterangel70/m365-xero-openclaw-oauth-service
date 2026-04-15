from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Provider(str, Enum):
    """Supported external integration providers."""

    MICROSOFT = "microsoft"
    XERO = "xero"


@dataclass(frozen=True)
class Connection:
    """Represents an authorized connection to a provider.

    The connection_id is a stable, caller-assigned identifier
    (e.g. "ms-default" or "xero-acmeltd").  It is the primary key
    used to look up stored tokens throughout the service.
    """

    connection_id: str
    provider: Provider
    created_at: datetime
