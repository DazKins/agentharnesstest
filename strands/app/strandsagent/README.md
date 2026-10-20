# strandsagent

An AgentCore Runtime agent built with AWS's [Strands Agents](https://strandsagents.com)
([`strands-agents`](https://pypi.org/project/strands-agents/)) on Claude via Amazon Bedrock.

Strands is a framework rather than a ready-made coding agent: the agent here is assembled from its
built-in tools. It is the framework AgentCore's own templates use, and its `BedrockModel` calls the
Bedrock Converse API with the AWS credential chain, so no model wrapper is needed.

## Layout

| File | Purpose |
| --- | --- |
| `main.py` | AgentCore entrypoint. Keeps one Strands `Agent` per AgentCore session ID and streams text deltas and tool calls as text chunks. |
| `agent_tools.py` | Example custom tool (`current_time`). |
| `agent/system.md` | System prompt. |
| `Dockerfile` | Image for the `Container` build: Python 3.13, uv, and command-line tools for the shell tool. |

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `BEDROCK_MODEL_ID` | `global.anthropic.claude-opus-5-5` | Bedrock model or inference profile ID. |
| `BEDROCK_REGION` | `AWS_REGION`, else `eu-west-1` | Bedrock region. |
| `CLAUDE_EFFORT` | `medium` | `output_config.effort`: `low`, `medium`, `high`, `xhigh` or `max`. |
| `STRANDS_STATE_DIR` | `/tmp/strandsagent` (`/home/bedrock_agentcore/strandsagent` in the image) | Holds the working directory. |
| `AGENT_WORK_DIR` | `STRANDS_STATE_DIR/work` | Working directory, shared by all sessions (the shell runs in the process's). |

AWS credentials come from the normal credential chain (locally, your SSO profile). Conversations
are kept in memory, so they are lost on restart; Strands' session managers (file, S3, AgentCore
Memory) can persist them.

## Tools

The agent has full access to the machine it runs on: Strands' built-in `shell`, `file_editor`
(view, create, edit; absolute paths), `http_request`, and `subagent`, plus `current_time`. The
built-in tools route through the agent's sandbox, and with none configured they run directly on
the host, without approval prompts. Their reach is whatever the process user can reach.

The agent uses the `Container` build, so it runs in Docker as the unprivileged `bedrock_agentcore`
user, both locally and when deployed (where each session also gets its own microVM). Two things
cross the container boundary locally:

- **AWS credentials.** `agentcore dev` exports your host AWS credentials (`AWS_PROFILE`) into the
  container's environment, so a shell in the container can act as that identity. Use a profile
  that can only call Bedrock if that matters. The exported credentials expire with the SSO session;
  restart the agent after `aws sso login`.
- **Network.** The container has normal outbound network access, and Docker publishes its port
  (8080, or 8082 from the web UI) on all host interfaces, so firewall ports 8080-8099 if the host
  is reachable from other machines.

To add a tool, write a function with `@tool` (see `agent_tools.py`) and add it to `TOOLS` in
`main.py`. Strands builds the tool spec from the signature and docstring.

## Running locally

From the project root. The first run builds the Docker image. Code is copied into the image, not
mounted, so restart `agentcore dev` (or press Start again in the UI) after changing it.

```bash
agentcore dev --skip-deploy --no-browser --logs   # agent only, on http://localhost:8080
agentcore dev "What time is it in Tokyo?"          # invoke the running dev server
agentcore dev --skip-deploy                        # web chat UI on http://localhost:8081
```

The other projects in this repo use the same ports, so run one at a time.

On a headless box, the web UI exits with `spawn xdg-open ENOENT`. Put a no-op `xdg-open` on `PATH`
and give it a TTY:

```bash
mkdir -p /tmp/agentcore-bin && printf '#!/bin/sh\nexit 0\n' > /tmp/agentcore-bin/xdg-open && chmod +x /tmp/agentcore-bin/xdg-open
PATH=/tmp/agentcore-bin:$PATH script -qfec "agentcore dev --skip-deploy" /tmp/strandsagent-ui.log
```

## Serving on the tailnet

The chat UI rejects any `Origin` other than `http://localhost:8081`, so `tailscale serve` can't
point at it directly. `scripts/tailnet-proxy.mjs` sits in between: it accepts only your tailnet
origin and rewrites it to localhost. Because the agent has a shell, the proxy also only lets through
the Tailscale logins passed with `--allow-user`; the tailnet is shared, so don't leave this open.

```bash
HOST=$(tailscale status --json | python3 -c 'import sys,json; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
node scripts/tailnet-proxy.mjs --origin "https://$HOST" --allow-user you@example.com &   # listens on 127.0.0.1:8090
sudo tailscale serve --bg http://127.0.0.1:8090
```

Open `https://$HOST/`, start the `strandsagent` agent in the UI, and chat. Turn it off with
`sudo tailscale serve --https=443 off`.
