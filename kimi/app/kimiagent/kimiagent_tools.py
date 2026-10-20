"""Custom tools for the Kimi agent.

Kimi loads tools by import path ("module:Class") from agent/agent.yaml. A tool is a
`CallableTool2` subclass with a pydantic params model.
"""

from datetime import datetime
from typing import override
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from kimi_agent_sdk import CallableTool2, ToolError, ToolOk, ToolReturnValue
from pydantic import BaseModel, Field


class CurrentTimeParams(BaseModel):
    timezone: str = Field(default="UTC", description="IANA timezone name, e.g. 'Europe/London'.")


class CurrentTime(CallableTool2[CurrentTimeParams]):
    name: str = "CurrentTime"
    description: str = "Get the current date and time in a given timezone."
    params: type[CurrentTimeParams] = CurrentTimeParams

    @override
    async def __call__(self, params: CurrentTimeParams) -> ToolReturnValue:
        try:
            now = datetime.now(ZoneInfo(params.timezone))
        except ZoneInfoNotFoundError:
            return ToolError(output="", message=f"Unknown timezone: {params.timezone}", brief="Unknown timezone")
        return ToolOk(output=now.isoformat())
