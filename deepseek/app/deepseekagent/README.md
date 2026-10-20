# deepseekagent

An AgentCore Runtime agent that runs DeepSeek's agent harness
([DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness), Python SDK
[`deepseek-harness-sdk`](https://pypi.org/project/deepseek-harness-sdk/)) on Claude via Amazon Bedrock.

The SDK is a thin JSON-RPC client: it starts the bundled native `dsh` runtime as a subprocess and
talks to it over stdio. The agent loop, tools, and model adapters all run in that runtime. Its
pi-ai model adapter calls Bedrock directly with the AWS credential chain, so no model wrapper is
needed. The harness is a developer preview (pre-release, not security audited) and its plugin
contracts may change.

## Layout

| File | Purpose |
| --- | --- |
| `main.py` | AgentCore entrypoint. Runs one harness runtime for the process, one harness session per AgentCore session ID, and streams each step's text and tool calls as text chunks. Also writes the generated profile patch (Bedrock model route, system prompt, tool plugins). |
| `agent/harness.patch.yml` | Profile patch over the bundled `sdk` profile: turns off telemetry, DeepSeek log uploads, and DeepSeek web search. |
| `agent/system.md` | System prompt. The harness prepends its own preamble and tool guidance. |
| `tools/current_time.mjs` | Example custom tool (`current_time`), a Cordis plugin. Every `tools/*.mjs` is loaded. |
| `Dockerfile` | Image for the `Container` build: Python 3.13, uv, and command-line tools for the bash tool. |

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `BEDROCK_MODEL_ID` | `global.anthropic.claude-opus-5-5` | Bedrock model or inference profile ID. |
| `BEDROCK_REGION` | `AWS_REGION`, else `eu-west-1` | Bedrock region. |
| `CLAUDE_EFFORT` | `medium` | Reasoning effort: `low`, `medium`, `high`, `xhigh` or `max`. |
| `DSH_STATE_DIR` | `/tmp/deepseekagent` (`/home/bedrock_agentcore/deepseekagent` in the image) | Harness home (profiles, session logs) and the working directory. |
| `AGENT_WORK_DIR` | `DSH_STATE_DIR/work` | Working directory, shared by all sessions (the runtime has one). |

AWS credentials come from the normal credential chain (locally, your SSO profile).

## Tools

The agent has full access to the machine it runs on: the harness's `sdk` profile tools (`bash`,
`read`, `write`, `edit`, `glob`, `grep`, `read_image`, `web_fetch`, background jobs, todos, goals,
`subagent` and `workflow`) plus `current_time`. `DSH_PERMISSION_MODE=danger-full-access` turns off
the harness's file sandbox and approval prompts. The file tools accept absolute paths, so the reach
is whatever the process user can reach.

The agent uses the `Container` build, so it runs in Docker as the unprivileged `bedrock_agentcore`
user, both locally and when deployed (where each session also gets its own microVM). Two things
cross the container boundary locally:

- **AWS credentials.** `agentcore dev` exports your host AWS credentials (`AWS_PROFILE`) into the
  container's environment, so a shell in the container can act as that identity. Use a profile
  that can only call Bedrock if that matters. The exported credentials expire with the SSO session;
  restart the agent after `aws sso login`.
- **Network.** The container has normal outbound network access, and Docker publishes its port on
  all host interfaces (see "Locking down the ports" below).

To add a tool, drop a plugin in `tools/` (see `current_time.mjs`). Plugins are plain JavaScript:
the runtime's own packages, such as `defineTool` from `@deepseek-ai/dsh-tools`, can't be imported
from outside its bundle.

### Differences from the Kimi version

- Text arrives once per model step rather than token by token: the SDK reports whole assistant
  messages, not deltas.
- All sessions share one working directory.
- Custom tools are JavaScript plugins, not Python classes.

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
PATH=/tmp/agentcore-bin:$PATH script -qfec "agentcore dev --skip-deploy" /tmp/deepseekagent-ui.log
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

Open `https://$HOST/`, start the `deepseekagent` agent in the UI, and chat. Turn it off with
`sudo tailscale serve --https=443 off`.
