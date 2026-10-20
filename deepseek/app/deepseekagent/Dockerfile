FROM public.ecr.aws/docker/library/python:3.13-slim-trixie

# Command-line tools for the agent's bash tool. (The harness runtime bundles its own ripgrep.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git jq less procps ripgrep \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

ARG UV_DEFAULT_INDEX
ARG UV_INDEX

WORKDIR /app

ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_PROGRESS=1 \
    PYTHONUNBUFFERED=1 \
    DOCKER_CONTAINER=1 \
    UV_DEFAULT_INDEX=${UV_DEFAULT_INDEX} \
    UV_INDEX=${UV_INDEX} \
    PATH="/app/.venv/bin:$PATH"

RUN useradd -m -u 1000 bedrock_agentcore

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

# The agent runs shell commands as this unprivileged user. It owns its state
# directory and home, but not the app code or system paths.
USER bedrock_agentcore
ENV DSH_STATE_DIR=/home/bedrock_agentcore/deepseekagent

# AgentCore Runtime service contract ports
# https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-service-contract.html
# 8080: HTTP Mode
# 8000: MCP Mode
# 9000: A2A Mode
EXPOSE 8080 8000 9000

CMD ["opentelemetry-instrument", "python", "-m", "main"]
