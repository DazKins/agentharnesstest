"""AgentCore Runtime entrypoint running a Strands agent on Claude via Amazon Bedrock."""

import asyncio
import json
import os
import re
from collections import OrderedDict
from pathlib import Path

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models import BedrockModel
from strands.vended_tools import file_editor, http_request, make_subagent, shell

import agent_tools

APP_DIR = Path(__file__).parent
STATE_DIR = Path(os.environ.get("STRANDS_STATE_DIR", "/tmp/strandsagent"))
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "global.anthropic.claude-opus-5-5")
REGION = os.environ.get("BEDROCK_REGION") or os.environ.get("AWS_REGION") or "eu-west-1"
EFFORT = os.environ.get("CLAUDE_EFFORT", "medium")
# The shell runs in the process working directory, so every session shares one.
WORK_DIR = Path(os.environ.get("AGENT_WORK_DIR", STATE_DIR / "work"))
SYSTEM_PROMPT = (APP_DIR / "agent" / "system.md").read_text().replace("${WORK_DIR}", str(WORK_DIR))
MAX_SESSIONS = 32

# Full access: Strands' built-in tools run directly on the host when the agent has no
# sandbox configured. Shell, file view/create/edit anywhere the process user can reach,
# HTTP requests, and subagents.
TOOLS = [shell, file_editor, http_request, make_subagent(), agent_tools.current_time]

WORK_DIR.mkdir(parents=True, exist_ok=True)
os.chdir(WORK_DIR)

app = BedrockAgentCoreApp()
log = app.logger

# One Strands agent per AgentCore session, so each keeps its own conversation (in memory).
# Bounded LRU: the least recently used session is dropped when the cap is hit.
_sessions: OrderedDict[str, tuple[Agent, asyncio.Lock]] = OrderedDict()


def _get_session(session_id: str) -> tuple[Agent, asyncio.Lock]:
    if session_id in _sessions:
        _sessions.move_to_end(session_id)
        return _sessions[session_id]
    model = BedrockModel(
        model_id=MODEL_ID,
        region_name=REGION,
        max_tokens=64_000,
        # Passed through to the Anthropic request body. Opus 5.5 takes adaptive thinking only.
        additional_request_fields={"thinking": {"type": "adaptive"}, "output_config": {"effort": EFFORT}},
    )
    agent = Agent(model=model, tools=TOOLS, system_prompt=SYSTEM_PROMPT, callback_handler=None)
    _sessions[session_id] = (agent, asyncio.Lock())
    if len(_sessions) > MAX_SESSIONS:
        _sessions.popitem(last=False)
    return _sessions[session_id]


def _session_id(payload: dict, context) -> str:
    raw = getattr(context, "session_id", None) or payload.get("sessionId") or "default"
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(raw))[:128]


def _format_tool_call(tool_use: dict) -> str:
    """Render a tool call for the chat, showing the argument that matters (command, path, ...)."""
    args = tool_use.get("input") or {}
    if isinstance(args, dict):
        detail = next(
            (args[k] for k in ("path", "command", "url", "task") if k in args),
            args,
        )
    else:
        detail = args
    if not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False)
    if len(detail) > 500:
        detail = detail[:500] + "…"
    return f"\n\n🔧 **{tool_use.get('name', '?')}**\n```\n{detail}\n```\n\n"


def _extract_prompt(payload) -> str:
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    return prompt


@app.entrypoint
async def invoke(payload, context):
    try:
        prompt = _extract_prompt(payload)
    except ValueError as e:
        yield {"error": str(e)}
        return

    session_id = _session_id(payload, context)
    log.info("Invoking Strands agent, session %s", session_id)
    agent, lock = _get_session(session_id)

    # Stream plain text chunks: both `agentcore invoke` and the `agentcore dev` chat UI render
    # them (not Strands' raw events). Text streams as deltas; tool calls show once complete.
    async with lock:
        try:
            async for event in agent.stream_async(prompt):
                if text := event.get("data"):
                    yield text
                elif message := event.get("message"):
                    for block in message.get("content") or []:
                        if "toolUse" in block:
                            yield _format_tool_call(block["toolUse"])
                        elif (result := block.get("toolResult")) and result.get("status") == "error":
                            text = " ".join(c.get("text", "") for c in result.get("content") or [] if "text" in c)
                            yield f"> ⚠️ {text[:500]}\n\n"
        except Exception as e:
            log.exception("Strands agent run failed")
            yield {"error": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    app.run()
