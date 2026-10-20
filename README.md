# agentharnesstest

The same agent built on four agent harnesses, each running Claude on Amazon Bedrock inside an
AgentCore Runtime container. Each folder is a self-contained AgentCore CLI project; run the
`agentcore` commands from inside it.

| Folder | Harness | Bedrock connection | Streaming | Sessions | Custom tools |
| --- | --- | --- | --- | --- | --- |
| [`kimi/`](kimi/app/kimiagent/README.md) | Moonshot's Kimi agent SDK (`kimi-agent-sdk`) | Wrapper: Kimi has no Bedrock provider, so `bedrock_claude.py` swaps the Anthropic SDK's Bedrock client into its `anthropic` provider. | Token deltas | In process, own work dir each | Python classes |
| [`deepseek/`](deepseek/app/deepseekagent/README.md) | DeepSeek Harness (`deepseek-harness-sdk`, client for a native `dsh` runtime) | Built in (pi-ai's `amazon-bedrock` route); the model is declared in a profile patch. | Whole steps | One runtime, shared work dir | JavaScript (Cordis) plugins |
| [`claude/`](claude/app/claudeagent/README.md) | Claude Agent SDK (`claude-agent-sdk`, bundles the Claude Code CLI) | Built in (`CLAUDE_CODE_USE_BEDROCK=1`). | Token deltas | CLI per turn, transcript resumed, own work dir each | Python, in-process MCP server |
| [`strands/`](strands/app/strandsagent/README.md) | Strands Agents (`strands-agents`), AWS's framework | Built in (`BedrockModel`, Converse API). | Token deltas | In memory, shared work dir | Python `@tool` functions |

All four expose the same full-access agent (shell, file read/write, web or HTTP fetch, subagents,
an example `current_time` tool) and stream text and tool calls to `agentcore dev` and its chat UI.
They use the same local ports, so run one at a time.
