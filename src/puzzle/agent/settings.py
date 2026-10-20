"""Which model the built-in agent uses: a user setting, never part of a project.

`<user data dir>/agent.toml` (keys never go in project files):

    provider = "anthropic"          # or "openai" for an OpenAI-compatible endpoint
    model = "claude-sonnet-5-5"     # e.g. "qwen3:8b" on Ollama
    base_url = "http://localhost:11434/v1"   # openai only
    api_key_env = "ANTHROPIC_API_KEY"        # the environment variable holding the key
    send_images = false             # let a remote provider see rendered results

Environment variables PUZZLE_AGENT_PROVIDER, PUZZLE_AGENT_MODEL and
PUZZLE_AGENT_BASE_URL override the file.

API keys: the environment variable first (`api_key_env`, default ANTHROPIC_API_KEY for
Claude), then the operating system's keychain (macOS Keychain, Windows Credential
Locker, Secret Service on Linux) under service "puzzle", account = provider.
`puzzle agent-key set` stores one there without it touching a file or the shell
history.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from puzzle.agent.providers import AnthropicProvider, OpenAIProvider, Provider, ProviderError

DEFAULT_MODELS = {"anthropic": "claude-sonnet-5-5", "openai": "qwen3:8b"}
DEFAULT_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
KEYRING_SERVICE = "puzzle"


def _keyring():
    try:
        import keyring
    except ImportError:
        return None
    return keyring


def stored_key(provider: str) -> str | None:
    kr = _keyring()
    if kr is None:
        return None
    try:
        return kr.get_password(KEYRING_SERVICE, provider)
    except Exception:  # noqa: BLE001 - no usable keychain here: behave as if empty
        return None


def store_key(provider: str, key: str) -> None:
    kr = _keyring()
    if kr is None:
        raise ProviderError("storing keys needs the keyring package: pip install keyring")
    kr.set_password(KEYRING_SERVICE, provider, key)


def delete_key(provider: str) -> bool:
    kr = _keyring()
    if kr is None or stored_key(provider) is None:
        return False
    kr.delete_password(KEYRING_SERVICE, provider)
    return True


@dataclass
class AgentSettings:
    provider: str = "anthropic"
    model: str = ""
    base_url: str = "http://localhost:11434/v1"
    api_key_env: str = ""
    send_images: bool | None = None  # default: local providers yes, remote no

    def build(self) -> Provider:
        model = self.model or DEFAULT_MODELS.get(self.provider, "")
        key = self.api_key()
        if self.provider == "anthropic":
            return AnthropicProvider(model=model, api_key=key)
        if self.provider == "openai":
            return OpenAIProvider(model=model, base_url=self.base_url, api_key=key)
        raise ProviderError(f"unknown agent provider '{self.provider}' (anthropic or openai)")

    def api_key(self) -> str | None:
        """From the environment variable, else the keychain; None when neither has one."""
        env = self.api_key_env or DEFAULT_KEY_ENV.get(self.provider, "")
        return (os.environ.get(env) if env else None) or stored_key(self.provider)

    def key_source(self) -> str:
        env = self.api_key_env or DEFAULT_KEY_ENV.get(self.provider, "")
        if env and os.environ.get(env):
            return f"environment ({env})"
        return "keychain" if stored_key(self.provider) else "none"

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
