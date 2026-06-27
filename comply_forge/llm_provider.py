"""
Swappable LLM provider seam.

THE point of this module: the rest of ComplyForge talks to `LLMProvider`, never
to a vendor SDK directly. That single seam is what lets you develop on the public
Claude API with synthetic data, then flip to Claude on AWS Bedrock GovCloud
(FedRAMP High / IL4-5) before any real CUI is processed -- with zero changes to
control_responder / test_plan.

Providers:
  * FakeProvider     -- deterministic, no network, no key. Default for tests/dev.
  * AnthropicProvider-- public Claude API (claude-opus-4-8, adaptive thinking).
  * BedrockProvider  -- Claude on AWS Bedrock GovCloud. THE CUI path.

Selection (get_provider): COMPLYFORGE_LLM_PROVIDER env = fake|anthropic|bedrock.
Default: anthropic if ANTHROPIC_API_KEY is set, else fake.

Model default is claude-opus-4-8 with adaptive thinking + configurable effort
(per the current Anthropic SDK guidance: adaptive thinking only, no budget_tokens,
no temperature).
"""

from __future__ import annotations

import os
import textwrap
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

DEFAULT_MODEL = "claude-opus-4-8"          # public API id
BEDROCK_MODEL = "anthropic.claude-opus-4-8"  # Bedrock carries the anthropic. prefix


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    usage: dict[str, Any] = field(default_factory=dict)

    def provenance(self) -> dict[str, Any]:
        """Audit metadata to stamp onto any LLM-derived artifact."""
        return {"llm_provider": self.provider, "llm_model": self.model, "usage": self.usage}


class LLMProvider(ABC):
    name: str = "base"
    model: str = DEFAULT_MODEL

    @abstractmethod
    def complete(self, *, system: str, prompt: str,
                 max_tokens: int = 4000, effort: str = "high") -> LLMResult:
        ...


class FakeProvider(LLMProvider):
    """Deterministic, offline. Echoes a structured stub so pipelines + tests run
    end-to-end without a key. NEVER use for real artifacts -- it does not reason."""
    name = "fake"
    model = "fake-deterministic"

    def complete(self, *, system, prompt, max_tokens=4000, effort="high") -> LLMResult:
        head = prompt.strip().splitlines()[0] if prompt.strip() else ""
        text = textwrap.dedent(f"""\
            [SYNTHETIC DRAFT -- fake provider, not real analysis]
            {head[:200]}
            This is a placeholder implementation statement generated offline for
            development/testing. Replace the provider (set COMPLYFORGE_LLM_PROVIDER
            or ANTHROPIC_API_KEY) to produce real drafts. A human must review.""")
        return LLMResult(text=text, provider=self.name, model=self.model,
                         usage={"synthetic": True})


class AnthropicProvider(LLMProvider):
    """Public Claude API. For development on SYNTHETIC data only -- do not send CUI."""
    name = "anthropic"

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None):
        try:
            import anthropic  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("pip install anthropic to use AnthropicProvider") from e
        import anthropic
        self.model = model
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def complete(self, *, system, prompt, max_tokens=4000, effort="high") -> LLMResult:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        usage = {"input_tokens": resp.usage.input_tokens,
                 "output_tokens": resp.usage.output_tokens}
        return LLMResult(text=text, provider=self.name, model=self.model, usage=usage)


class BedrockProvider(LLMProvider):
    """Claude on AWS Bedrock -- the path for CUI / FedRAMP / GovCloud (IL4-5).

    Use a GovCloud region and a FedRAMP-authorized account. Same prompt contract
    as AnthropicProvider, so control_responder/test_plan don't change."""
    name = "bedrock"

    def __init__(self, model: str = BEDROCK_MODEL, region: str | None = None):
        try:
            from anthropic import AnthropicBedrock  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise RuntimeError('pip install "anthropic[bedrock]" to use BedrockProvider') from e
        from anthropic import AnthropicBedrock
        self.model = model
        region = region or os.environ.get("AWS_REGION", "us-gov-west-1")
        self._client = AnthropicBedrock(aws_region=region)

    def complete(self, *, system, prompt, max_tokens=4000, effort="high") -> LLMResult:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        usage = {"input_tokens": resp.usage.input_tokens,
                 "output_tokens": resp.usage.output_tokens}
        return LLMResult(text=text, provider=self.name, model=self.model, usage=usage)


def get_provider(name: str | None = None) -> LLMProvider:
    """Resolve a provider. Explicit name > COMPLYFORGE_LLM_PROVIDER env > auto."""
    name = name or os.environ.get("COMPLYFORGE_LLM_PROVIDER")
    if name == "fake":
        return FakeProvider()
    if name == "anthropic":
        return AnthropicProvider()
    if name == "bedrock":
        return BedrockProvider()
    # auto
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicProvider()
    return FakeProvider()
