"""Claude on Amazon Bedrock as a Kimi chat provider.

kimi-cli has no Bedrock provider. Its `anthropic` provider type builds kosong's
`Anthropic` chat provider, which talks to the Messages API through a plain
`AsyncAnthropic` client. This module swaps that client for the Anthropic SDK's
Bedrock client (SigV4 auth from the normal AWS credential chain) and adjusts
the request shape for current Claude models:

- Thinking is always adaptive. Claude Opus 5.5 rejects `{"type": "disabled"}`
  and `budget_tokens`, which kosong would otherwise send.
- Depth is controlled with `output_config.effort` (CLAUDE_EFFORT).
- The `interleaved-thinking` beta header is dropped; adaptive thinking
  interleaves without it.

Call `install()` once before creating a Kimi session.
"""

from __future__ import annotations

import os
from typing import Any, Literal

from anthropic import AsyncAnthropicBedrock, AsyncAnthropicBedrockMantle
from kosong.contrib.chat_provider import anthropic as kosong_anthropic

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class BedrockClaude(kosong_anthropic.Anthropic):
    def __init__(self, *, model: str, api_key: str | None = None, base_url: str | None = None, **kwargs: Any):
        # kimi passes the provider's base_url/api_key; Bedrock uses AWS credentials instead.
        super().__init__(model=model, api_key="unused", **kwargs)
        region = os.environ.get("BEDROCK_REGION") or os.environ.get("AWS_REGION") or "eu-west-1"
        if os.environ.get("BEDROCK_API", "invoke") == "mantle":
            # Bedrock's native Messages API endpoint. Not every region serves it yet.
            self._client = AsyncAnthropicBedrockMantle(aws_region=region)
        else:
            self._client = AsyncAnthropicBedrock(aws_region=region)
        effort: Effort = os.environ.get("CLAUDE_EFFORT", "medium")  # type: ignore[assignment]
        self._generation_kwargs["beta_features"] = []
        self._generation_kwargs["thinking"] = {"type": "adaptive"}
        self._generation_kwargs["output_config"] = {"effort": effort}  # type: ignore[typeddict-unknown-key]

    def with_thinking(self, effort):  # noqa: ARG002 - kimi's on/off toggle doesn't apply
        return self


def install() -> None:
    """Route kimi's `anthropic` provider type through Bedrock."""
    kosong_anthropic.Anthropic = BedrockClaude  # type: ignore[misc]
