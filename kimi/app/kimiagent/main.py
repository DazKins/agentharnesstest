"""AgentCore Runtime entrypoint running the Kimi agent harness on Claude via Amazon Bedrock."""

import asyncio
import json
import os
import re
from collections import OrderedDict
from pathlib import Path

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from kaos.path import KaosPath
from kimi_agent_sdk import ApprovalRequest, Config, Session, TextPart, ToolCall, ToolCallPart, ToolResult
from kimi_cli.config import LLMModel, LLMProvider
from pydantic import SecretStr

import bedrock_claude

APP_DIR = Path(__file__).parent
STATE_DIR = Path(os.environ.get("KIMI_STATE_DIR", "/tmp/kimiagent"))
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "global.anthropic.claude-opus-5-5")
# Shared working directory for every session. Unset: each session gets its own under STATE_DIR.
WORK_DIR = os.environ.get("AGENT_WORK_DIR")
MAX_SESSIONS = 32

# Kimi persists session history under KIMI_SHARE_DIR (default ~/.kimi).
os.environ.setdefault("KIMI_SHARE_DIR", str(STATE_DIR / "kimi"))
bedrock_claude.install()

KIMI_CONFIG = Config(
    default_model="claude",
    models={
        "claude": LLMModel(
            provider="bedrock",
            model=MODEL_ID,
            max_context_size=1_000_000,
            capabilities={"image_in"},
        )
    },
    # base_url and api_key are required by kimi's schema but unused: bedrock_claude
    # authenticates with the AWS credential chain.
    providers={"bedrock": LLMProvider(type="anthropic", base_url="bedrock", api_key=SecretStr("unused"))},
)

app = BedrockAgentCoreApp()
log = app.logger

# One Kimi session per AgentCore session, so each keeps its own conversation.
# Bounded LRU: the least recently used session is closed when the cap is hit.
_sessions: OrderedDict[str, tuple[Session, asyncio.Lock]] = OrderedDict()
_sessions_lock = asyncio.Lock()


async def _get_session(session_id: str) -> tuple[Session, asyncio.Lock]:
    async with _sessions_lock:
        if session_id in _sessions:
            _sessions.move_to_end(session_id)
            return _sessions[session_id]
        work_dir = Path(WORK_DIR) if WORK_DIR else STATE_DIR / "work" / session_id
        work_dir.mkdir(parents=True, exist_ok=True)
        session = await Session.create(
            KaosPath.unsafe_from_local_path(work_dir),
            session_id=session_id,
            config=KIMI_CONFIG,
            agent_file=APP_DIR / "agent" / "agent.yaml",
            yolo=True,
        )
        _sessions[session_id] = (session, asyncio.Lock())
        if len(_sessions) > MAX_SESSIONS:
            _, (evicted, _) = _sessions.popitem(last=False)
            await evicted.close()
        return _sessions[session_id]


def _session_id(payload: dict, context) -> str:
    raw = getattr(context, "session_id", None) or payload.get("sessionId") or "default"
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(raw))[:128]


def _format_tool_call(call: ToolCall) -> str:
    """Render a tool call for the chat, showing the argument that matters (command, path, ...)."""
    try:
        args = json.loads(call.function.arguments or "{}")
    except json.JSONDecodeError:
        args = call.function.arguments
    if isinstance(args, dict):
        detail = next((args[k] for k in ("command", "path", "pattern", "url", "description") if k in args), args)
    else:
        detail = args
    if not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False)
    if len(detail) > 500:
        detail = detail[:500] + "…"
    return f"\n\n🔧 **{call.function.name}**\n```\n{detail}\n```\n\n"


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
    log.info("Invoking Kimi agent, session %s", session_id)
    session, lock = await _get_session(session_id)

    # Stream plain text chunks: both `agentcore invoke` and the `agentcore dev` chat UI render them.
    async with lock:
        # A tool call's arguments stream in as ToolCallParts for the latest ToolCall, possibly
        # interleaved with results of earlier calls. Show a call once its arguments are complete:
        # when the next call or text starts, or when its own result arrives.
        current_call: ToolCall | None = None
        try:
            async for msg in session.prompt(prompt):
                if isinstance(msg, ToolCallPart):
                    if current_call is not None:
                        current_call.merge_in_place(msg)
                    continue
                if current_call is not None and (
                    isinstance(msg, (ToolCall, TextPart))
                    or (isinstance(msg, ToolResult) and msg.tool_call_id == current_call.id)
                ):
                    yield _format_tool_call(current_call)
                    current_call = None
                match msg:
                    case TextPart(text=text) if text:
                        yield text
                    case ToolCall():
                        current_call = msg.model_copy(deep=True)
                    case ToolResult() if msg.return_value.is_error:
                        yield f"> ⚠️ {msg.return_value.message}\n\n"
                    case ApprovalRequest():
                        msg.resolve("reject")  # unreachable with yolo=True; never leave a request hanging
        except Exception as e:
            log.exception("Kimi agent run failed")
            yield {"error": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    app.run()
