# kimiagent

An AgentCore Runtime agent that runs Moonshot's Kimi agent harness
([`kimi-agent-sdk`](https://pypi.org/project/kimi-agent-sdk/)) on Claude via Amazon Bedrock.

## Layout

| File | Purpose |
| --- | --- |
| `main.py` | AgentCore entrypoint. Keeps one Kimi `Session` per AgentCore session ID and streams the reply as text chunks. |
| `bedrock_claude.py` | Plugs Claude on Bedrock into Kimi. Kimi has no Bedrock provider, so this swaps the client behind its `anthropic` provider type for the Anthropic SDK's Bedrock client and always uses adaptive thinking. |
| `agent/agent.yaml` | Kimi agent spec: system prompt and tool list. |
| `agent/system.md` | System prompt. |
| `kimiagent_tools.py` | Example custom tool (`CurrentTime`). |
| `Dockerfile` | Image for the `Container` build: Python 3.13, uv, and command-line tools for the Shell tool. |

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `BEDROCK_MODEL_ID` | `global.anthropic.claude-opus-5-5` | Bedrock model or inference profile ID. |
| `BEDROCK_REGION` | `AWS_REGION`, else `eu-west-1` | Bedrock region. |
| `BEDROCK_API` | `invoke` | `invoke` uses `bedrock-runtime` InvokeModel. `mantle` uses Bedrock's Messages API endpoint, which serves Claude in `us-east-1` but not yet in `eu-west-1`. |
| `CLAUDE_EFFORT` | `medium` | `output_config.effort`: `low`, `medium`, `high`, `xhigh` or `max`. |
| `KIMI_STATE_DIR` | `/tmp/kimiagent` (`/home/bedrock_agentcore/kimiagent` in the image) | Kimi session history and per-session work directories. |
| `AGENT_WORK_DIR` | per session, under `KIMI_STATE_DIR` | One working directory shared by all sessions. |

AWS credentials come from the normal credential chain (locally, your SSO profile).

## Tools

The agent has full access to the machine it runs on: Kimi's shell, file read/write/search, web
fetch, a `coder` subagent, and `CurrentTime`. Every tool call is auto-approved (`yolo=True`). The
file tools accept absolute paths, so the reach is whatever the process user can reach.

The agent uses the `Container` build, so it runs in Docker as the unprivileged `bedrock_agentcore`
user, both locally and when deployed (where each session also gets its own microVM). Two things
cross the container boundary locally:

- **AWS credentials.** `agentcore dev` exports your host AWS credentials (`AWS_PROFILE`) into the
  container's environment, so a shell in the container can act as that identity. Use a profile
  that can only call Bedrock if that matters. The exported credentials expire with the SSO session;
  restart the agent after `aws sso login`.
- **Network.** The container has normal outbound network access, and Docker publishes its port on
  all host interfaces (see "Locking down the ports" below).

To add a tool, subclass `CallableTool2` (see `kimiagent_tools.py`) and list it in `agent/agent.yaml`
as `"module:Class"`.

## Running locally

From the project root. The first run builds the Docker image. Code is copied into the image, not
mounted, so restart `agentcore dev` (or press Start again in the UI) after changing it.

```bash
agentcore dev --skip-deploy --no-browser --logs   # agent only, on http://localhost:8080
agentcore dev "What time is it in Tokyo?"          # invoke the running dev server
agentcore dev --skip-deploy                        # web chat UI on http://localhost:8081
```

On a headless box, the web UI exits with `spawn xdg-open ENOENT`. Put a no-op `xdg-open` on `PATH`
and give it a TTY:

```bash
mkdir -p /tmp/kimiagent-bin && printf '#!/bin/sh\nexit 0\n' > /tmp/kimiagent-bin/xdg-open && chmod +x /tmp/kimiagent-bin/xdg-open
PATH=/tmp/kimiagent-bin:$PATH script -qfec "agentcore dev --skip-deploy" /tmp/kimiagent-ui.log
```

## Locking down the ports

Docker publishes the agent container on `0.0.0.0` (8080, or 8082 when started from the web UI),
which makes it reachable from the VPC and the tailnet, bypassing the proxy below. Drop external
traffic to ports 8080-8099 (not persistent across reboots):

```bash
for ipt in iptables ip6tables; do for ifc in enX0 tailscale0; do
  sudo $ipt -I DOCKER-USER -i $ifc -p tcp -m conntrack --ctorigdstport 8080:8099 -m comment --comment kimiagent-dev -j DROP
  sudo $ipt -I INPUT -i $ifc -p tcp --dport 8080:8099 -m comment --comment kimiagent-dev -j DROP
done; done
```

Remove them with the same commands using `-D` instead of `-I`.

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

Open `https://$HOST/`, start the `kimiagent` agent in the UI, and chat. Turn it off with
`sudo tailscale serve --https=443 off`.
