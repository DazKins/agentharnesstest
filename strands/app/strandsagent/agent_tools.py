"""Custom tools. Strands builds the tool spec from the signature and docstring."""

from datetime import datetime
from zoneinfo import ZoneInfo

from strands import tool


@tool
def current_time(timezone: str = "UTC") -> str:
    """Get the current date and time in an IANA timezone.

    Args:
        timezone: IANA timezone, e.g. Asia/Tokyo.
    """
    # An exception becomes an error result for the model.
    return datetime.now(ZoneInfo(timezone)).isoformat()
