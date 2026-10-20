"""AgentCore Runtime entrypoint running the DeepSeek Harness agent on Claude via Amazon Bedrock."""

import asyncio
import json
import os
import re
from pathlib import Path

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from deepseek_harness import DeepSeekHarness, Notification, Session
from deepseek_harness.errors import TransportClosedError

APP_DIR = Path(__file__).parent
STATE_DIR = Path(os.environ.get("DSH_STATE_DIR", "/tmp/deepseekagent"))
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "global.anthropic.claude-opus-5-5")
REGION = os.environ.get("BEDROCK_REGION") or os.environ.get("AWS_REGION") or "eu-west-1"
EFFORT = os.environ.get("CLAUDE_EFFORT", "medium")
# The harness runtime has one working directory, shared by every session.
WORK_DIR = Path(os.environ.get("AGENT_WORK_DIR", STATE_DIR / "work"))


def _runtime_patch() -> Path:
    """Write the profile patch that depends on env and install paths. JSON is valid YAML."""
    patch = [
        # pi-ai's catalog route for Bedrock signs requests with the AWS credential chain. Its
        # installed catalog predates Opus 5.5, so the model is declared here.
        {
            "id": "llm-pi-ai",
            "config": {
                "providers": {
                    "amazon-bedrock": {
                        "baseURL": f"https://bedrock-runtime.{REGION}.amazonaws.com",
                        "models": [
                            {
                                "id": MODEL_ID,
                                "contextWindow": 1_000_000,
                                "maxTokens": 128_000,
                                "input": ["text", "image"],
                                "reasoningEfforts": {e: e for e in ("low", "medium", "high", "xhigh", "max")},
                            }
                        ],
                    }
                }
            },
        },
        {
            "id": "system-prompt",
            "config": {"personaPrefix": (APP_DIR / "agent" / "system.md").read_text(), "personaSuffix": ""},
        },
        {"insert": [{"id": f"tool-{p.stem}", "name": str(p)} for p in sorted((APP_DIR / "tools").glob("*.mjs"))]},
    ]
    path = STATE_DIR / "runtime.patch.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(patch, indent=2))
    return path


app = BedrockAgentCoreApp()
log = app.logger

# One harness runtime (a `dsh` subprocess) serves every AgentCore session; each session ID
# maps to a harness session with its own conversation. Turns within a session are serialized.
_harness: DeepSeekHarness | None = None
_harness_lock = asyncio.Lock()
_session_locks: dict[str, asyncio.Lock] = {}


async def _get_harness() -> DeepSeekHarness:
    global _harness
    async with _harness_lock:
        if _harness is None:
            WORK_DIR.mkdir(parents=True, exist_ok=True)
            harness = DeepSeekHarness(
                provider="amazon-bedrock",
                model=MODEL_ID,
                reasoning_effort=EFFORT,
                cwd=str(WORK_DIR),
                dsh_home=str(STATE_DIR / "dsh"),
                profile="sdk",
                patches=(str(APP_DIR / "agent" / "harness.patch.yml"), str(_runtime_patch())),
                env={"DSH_PERMISSION_MODE": "danger-full-access", "AWS_REGION": REGION},
            )
            await asyncio.to_thread(harness.start)
            _harness = harness
        return _harness


async def _reset_harness() -> None:
    global _harness
    async with _harness_lock:
        if _harness is not None:
            await asyncio.to_thread(_harness.close)
            _harness = None


def _session_id(payload: dict, context) -> str:
    raw = getattr(context, "session_id", None) or payload.get("sessionId") or "default"
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(raw))[:128]


def _format_tool_call(name: str, arguments: str) -> str:
    """Render a tool call for the chat, showing the argument that matters (command, path, ...)."""
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        args = arguments
    if isinstance(args, dict):
        detail = next(
            (args[k] for k in ("command", "file_path", "path", "pattern", "url", "prompt", "description") if k in args),
            args,
        )
    else:
        detail = args
    if not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False)
    if len(detail) > 500:
        detail = detail[:500] + "…"
    return f"\n\n🔧 **{name}**\n```\n{detail}\n```\n\n"


def _render(notification: Notification, session_id: str):
    """Turn one root-session event into chat chunks. Subagent events are not shown."""
    if notification.method != "session.event" or notification.payload.get("sessionId") != session_id:
        return
    event = notification.payload.get("event") or {}
    data = event.get("data") or {}
    match event.get("type"):
        case "assistant/message":
            for block in (data.get("message") or {}).get("content") or []:
                if block.get("type") == "text" and block.get("text"):
                    yield block["text"]
                elif block.get("type") == "tool-call":
                    yield _format_tool_call(block.get("name", "?"), block.get("arguments", ""))
        case "tool/result":
            for result in (data.get("message") or {}).get("content") or []:
                if result.get("isError"):
                    text = " ".join(c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text")
                    yield f"> ⚠️ {text[:500]}\n\n"
        case "turn/end":
            reason = data.get("reason") or {}
            if reason.get("kind") not in ("completed", "aborted"):
                error = reason.get("error") or {}
                detail = error.get("message") or json.dumps(reason, ensure_ascii=False)[:1000]
                yield {"error": f"{reason.get('kind')}: {detail}"}


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
    log.info("Invoking DeepSeek Harness agent, session %s", session_id)

    # Stream plain text chunks: both `agentcore invoke` and the `agentcore dev` chat UI render them.
    async with _session_locks.setdefault(session_id, asyncio.Lock()):
        try:
            harness = await _get_harness()
        except Exception as e:
            log.exception("DeepSeek Harness runtime failed to start")
            yield {"error": f"{type(e).__name__}: {e}"}
            return

        # The SDK is synchronous: run the turn in a thread and relay its notifications here.
        # Both the notifications and the end-of-run sentinel go through the event loop, in order.
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Notification | None] = asyncio.Queue()
        session = Session(harness, session_id)
        run = asyncio.ensure_future(
            asyncio.to_thread(
                session.run, prompt, on_notification=lambda n: loop.call_soon_threadsafe(queue.put_nowait, n)
            )
        )
        run.add_done_callback(lambda _: queue.put_nowait(None))
        while (notification := await queue.get()) is not None:
            for chunk in _render(notification, session_id):
                yield chunk
        try:
            run.result()
        except Exception as e:
            log.exception("DeepSeek Harness run failed")
            if isinstance(e, TransportClosedError):
                await _reset_harness()  # the runtime exited; start a fresh one on the next call
            yield {"error": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    app.run()
