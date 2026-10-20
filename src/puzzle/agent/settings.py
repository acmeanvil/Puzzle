"""Which model the built-in agent uses: a user setting, never part of a project.

`<user data dir>/agent.toml` (keys never go in project files):

    provider = "anthropic"          # or "openai" for an OpenAI-compatible endpoint
    model = "claude-sonnet-5-5"     # e.g. "qwen3:8b" on Ollama
    base_url = "http://localhost:11434/v1"   # openai only
    api_key_env = "ANTHROPIC_API_KEY"        # the environment variable holding the key
    send_images = false             # let a remote provider see rendered results

Environment variables PUZZLE_AGENT_PROVIDER, PUZZLE_AGENT_MODEL and
PUZZLE_AGENT_BASE_URL override the file.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from puzzle.agent.providers import AnthropicProvider, OpenAIProvider, Provider, ProviderError

DEFAULT_MODELS = {"anthropic": "claude-sonnet-5-5", "openai": "qwen3:8b"}


@dataclass
class AgentSettings:
    provider: str = "anthropic"
    model: str = ""
    base_url: str = "http://localhost:11434/v1"
    api_key_env: str = ""
    send_images: bool | None = None  # default: local providers yes, remote no

    def build(self) -> Provider:
        model = self.model or DEFAULT_MODELS.get(self.provider, "")
        key = os.environ.get(self.api_key_env) if self.api_key_env else None
        if self.provider == "anthropic":
            return AnthropicProvider(model=model, api_key=key)
        if self.provider == "openai":
            return OpenAIProvider(model=model, base_url=self.base_url, api_key=key)
        raise ProviderError(f"unknown agent provider '{self.provider}' (anthropic or openai)")

    def images_allowed(self, provider: Provider) -> bool:
        """ADR 0015: by default only outlines and summaries go to a remote provider."""
        return (not provider.remote) if self.send_images is None else self.send_images


def settings_path() -> Path:
    from puzzle.materials import user_data_dir

    return user_data_dir() / "agent.toml"


def load_settings(path: Path | None = None) -> AgentSettings:
    path = path or settings_path()
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    s = AgentSettings(**{k: v for k, v in data.items() if k in AgentSettings.__dataclass_fields__})
    s.provider = os.environ.get("PUZZLE_AGENT_PROVIDER", s.provider)
    s.model = os.environ.get("PUZZLE_AGENT_MODEL", s.model)
    s.base_url = os.environ.get("PUZZLE_AGENT_BASE_URL", s.base_url)
    return s
