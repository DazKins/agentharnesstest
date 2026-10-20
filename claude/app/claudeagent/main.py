"""AgentCore Runtime entrypoint running the Claude Agent SDK (Claude Code) on Claude via Amazon Bedrock."""

import asyncio
import json
import os
import re
import uuid
from pathlib import Path

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    StreamEvent,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

import agent_tools

APP_DIR = Path(__file__).parent
STATE_DIR = Path(os.environ.get("CLAUDE_STATE_DIR", "/tmp/claudeagent"))
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "global.anthropic.claude-opus-5-5")
# Claude Code uses a small model for background work such as titles and summaries.
SMALL_MODEL_ID = os.environ.get("BEDROCK_SMALL_MODEL_ID", "global.anthropic.claude-haiku-5-5")
REGION = os.environ.get("BEDROCK_REGION") or os.environ.get("AWS_REGION") or "eu-west-1"
EFFORT = os.environ.get("CLAUDE_EFFORT", "medium")
# Shared working directory for every session. Unset: each session gets its own under STATE_DIR.
WORK_DIR = os.environ.get("AGENT_WORK_DIR")
CONFIG_DIR = STATE_DIR / "claude"
SYSTEM_PROMPT = (APP_DIR / "agent" / "system.md").read_text()
SESSION_NAMESPACE = uuid.UUID("5d1f6c1e-8a43-4f0e-9d55-3b8f2a7c9e10")

# Environment for the Claude Code CLI subprocess, on top of this process's environment.
CLI_ENV = {
    "CLAUDE_CODE_USE_BEDROCK": "1",
    "AWS_REGION": REGION,
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": SMALL_MODEL_ID,
    # Keep Claude Code's state (sessions, settings) out of the user's ~/.claude.
    "CLAUDE_CONFIG_DIR": str(CONFIG_DIR),
    # No telemetry, error reporting, or update checks.
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
}

app = BedrockAgentCoreApp()
log = app.logger

# Each query() runs one turn in a fresh Claude Code process. Conversations persist as
# Claude Code session transcripts under CONFIG_DIR, keyed by a UUID derived from the
# AgentCore session ID, so a session resumes even after a restart.
_session_locks: dict[str, asyncio.Lock] = {}


def _session_id(payload: dict, context) -> str:
    raw = getattr(context, "session_id", None) or payload.get("sessionId") or "default"
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(raw))[:128]


def _options(session_id: str) -> ClaudeAgentOptions:
    work_dir = Path(WORK_DIR) if WORK_DIR else STATE_DIR / "work" / session_id
    work_dir.mkdir(parents=True, exist_ok=True)
    claude_session = str(uuid.uuid5(SESSION_NAMESPACE, session_id))
    started = any(CONFIG_DIR.glob(f"projects/*/{claude_session}.jsonl"))
    return ClaudeAgentOptions(
        # Claude Code's own system prompt (tool guidance, environment) plus ours.
        system_prompt={"type": "preset", "preset": "claude_code", "append": SYSTEM_PROMPT},
        model=MODEL_ID,
        effort=EFFORT,
        cwd=work_dir,
        # Full access: every built-in tool (Bash, Read, Write, Edit, Glob, Grep, WebFetch,
        # Task subagents, ...) plus ours, with no permission prompts.
        permission_mode="bypassPermissions",
        mcp_servers={"agent": agent_tools.SERVER},
        # Don't load settings, CLAUDE.md, or hooks from the machine.
        setting_sources=[],
        resume=claude_session if started else None,
        session_id=None if started else claude_session,
        include_partial_messages=True,
        env=CLI_ENV,
    )


def _format_tool_call(block: ToolUseBlock) -> str:
    """Render a tool call for the chat, showing the argument that matters (command, path, ...)."""
    args = block.input
    detail = next(
        (args[k] for k in ("command", "file_path", "path", "pattern", "url", "description") if k in args),
        args,
    )
    if not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False)
    if len(detail) > 500:
        detail = detail[:500] + "…"
    name = block.name.removeprefix("mcp__agent__")
    return f"\n\n🔧 **{name}**\n```\n{detail}\n```\n\n"


def _result_text(block: ToolResultBlock) -> str:
    if isinstance(block.content, list):
        return " ".join(c.get("text", "") for c in block.content if c.get("type") == "text")
    return block.content or ""


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
    log.info("Invoking Claude agent, session %s", session_id)

    # Stream plain text chunks: both `agentcore invoke` and the `agentcore dev` chat UI render them.
    # Text streams as deltas; tool calls are shown once complete. Subagent output is not shown.
    async with _session_locks.setdefault(session_id, asyncio.Lock()):
        try:
            async for msg in query(prompt=prompt, options=_options(session_id)):
                match msg:
                    case StreamEvent(parent_tool_use_id=None, event=event):
                        delta = event.get("delta") or {}
                        if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                            yield delta["text"]
                    case AssistantMessage(parent_tool_use_id=None):
                        for block in msg.content:
                            # ToolSearch is Claude Code loading deferred tool definitions.
                            if isinstance(block, ToolUseBlock) and block.name != "ToolSearch":
                                yield _format_tool_call(block)
                    case UserMessage(parent_tool_use_id=None) if isinstance(msg.content, list):
                        for block in msg.content:
                            if isinstance(block, ToolResultBlock) and block.is_error:
                                yield f"> ⚠️ {_result_text(block)[:500]}\n\n"
                    case ResultMessage(is_error=True):
                        yield {"error": f"{msg.subtype}: {msg.result or '; '.join(msg.errors or [])}"}
        except Exception as e:
            log.exception("Claude agent run failed")
            yield {"error": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    app.run()
