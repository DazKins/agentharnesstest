"""Custom tools, served to Claude Code from this process as an in-process MCP server."""

from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from claude_agent_sdk import create_sdk_mcp_server, tool


@tool(
    "current_time",
    "Get the current date and time in an IANA timezone.",
    {"timezone": Annotated[str, "IANA timezone, e.g. Asia/Tokyo. Use UTC if unsure."]},
)
async def current_time(args: dict) -> dict:
    try:
        now = datetime.now(ZoneInfo(args.get("timezone") or "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        return {"content": [{"type": "text", "text": f"Unknown timezone: {args.get('timezone')}"}], "is_error": True}
    return {"content": [{"type": "text", "text": now.isoformat()}]}


# Tools appear to the model as mcp__agent__<name>.
SERVER = create_sdk_mcp_server("agent", tools=[current_time])
