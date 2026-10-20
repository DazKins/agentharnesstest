# claudeagent

An AgentCore Runtime agent that runs Anthropic's Claude Agent SDK
([`claude-agent-sdk`](https://pypi.org/project/claude-agent-sdk/)), the harness behind Claude Code,
on Claude via Amazon Bedrock.

The SDK bundles the Claude Code CLI and runs it as a subprocess for each turn. Claude Code supports
Bedrock natively (`CLAUDE_CODE_USE_BEDROCK=1` plus the AWS credential chain), so no model wrapper
is needed.

## Layout

| File | Purpose |
| --- | --- |
| `main.py` | AgentCore entrypoint. Runs one Claude Code turn per invocation, resuming the session's transcript, and streams text deltas and tool calls as text chunks. |
| `agent_tools.py` | Example custom tool (`current_time`), served to Claude Code as an in-process MCP server. |
| `agent/system.md` | Appended to Claude Code's own system prompt. |
| `Dockerfile` | Image for the `Container` build: Python 3.13, uv, and command-line tools for the Bash tool. |

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `BEDROCK_MODEL_ID` | `global.anthropic.claude-opus-5-5` | Bedrock model or inference profile ID. |
| `BEDROCK_SMALL_MODEL_ID` | `global.anthropic.claude-haiku-5-5` | Model Claude Code uses for background work such as titles and summaries. |
| `BEDROCK_REGION` | `AWS_REGION`, else `eu-west-1` | Bedrock region. |
| `CLAUDE_EFFORT` | `medium` | Effort: `low`, `medium`, `high`, `xhigh` or `max`. |
| `CLAUDE_STATE_DIR` | `/tmp/claudeagent` (`/home/bedrock_agentcore/claudeagent` in the image) | Claude Code config dir (session transcripts) and per-session working directories. |
| `AGENT_WORK_DIR` | per session, under `CLAUDE_STATE_DIR` | One working directory shared by all sessions. |

AWS credentials come from the normal credential chain (locally, your SSO profile). Claude Code runs
with its own config dir, loads no settings, `CLAUDE.md` or hooks from the machine, and sends no
telemetry (`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`).

## Sessions

Each AgentCore session ID maps to a fixed Claude Code session UUID. The first turn creates it and
later turns resume its transcript, so a conversation survives a process restart (but not a new
container, since the transcripts live inside it). Each turn starts a fresh CLI process, which adds
a second or two.

## Tools

The agent has full access to the machine it runs on: all of Claude Code's built-in tools (Bash,
Read, Write, Edit, Glob, Grep, WebFetch, Task subagents, todos, ...) plus `current_time`.
`permission_mode="bypassPermissions"` turns off every permission prompt. The file tools accept
absolute paths, so the reach is whatever the process user can reach.

The agent uses the `Container` build, so it runs in Docker as the unprivileged `bedrock_agentcore`
user, both locally and when deployed (where each session also gets its own microVM). Two things
cross the container boundary locally:

- **AWS credentials.** `agentcore dev` exports your host AWS credentials (`AWS_PROFILE`) into the
  container's environment, so a shell in the container can act as that identity. Use a profile
  that can only call Bedrock if that matters. The exported credentials expire with the SSO session;
  restart the agent after `aws sso login`.
- **Network.** The container has normal outbound network access, and Docker publishes its port on
  all host interfaces (see "Locking down the ports" below).

To add a tool, define it with `@tool` in `agent_tools.py` and add it to the `SERVER` list.

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
PATH=/tmp/agentcore-bin:$PATH script -qfec "agentcore dev --skip-deploy" /tmp/claudeagent-ui.log
```

## Locking down the ports

Docker publishes the agent container on `0.0.0.0` (8080, or 8082 when started from the web UI),
which makes it reachable from the VPC and the tailnet, bypassing the proxy below. Drop external
traffic to ports 8080-8099 (not persistent across reboots):

```bash
for ipt in iptables ip6tables; do for ifc in enX0 tailscale0; do
  sudo $ipt -I DOCKER-USER -i $ifc -p tcp -m conntrack --ctorigdstport 8080:8099 -m comment --comment agentcore-dev -j DROP
  sudo $ipt -I INPUT -i $ifc -p tcp --dport 8080:8099 -m comment --comment agentcore-dev -j DROP
done; done
```

Remove them with the same commands using `-D` instead of `-I`. One set of rules covers every
project in this repo.

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

Open `https://$HOST/`, start the `claudeagent` agent in the UI, and chat. Turn it off with
`sudo tailscale serve --https=443 off`.
