"""Which model the built-in agent uses: a user setting, never part of a project.

`<user data dir>/agent.toml` (keys never go in project files):

    provider = "anthropic"          # or "openai" for an OpenAI-compatible endpoint
    model = "claude-sonnet-5-5"     # e.g. "qwen3:8b" on Ollama
    base_url = "http://localhost:11434/v1"   # openai only
    api_key_env = "ANTHROPIC_API_KEY"        # the environment variable holding the key
    send_images = false             # let a remote provider see rendered results
    embed_model = "nomic-embed-text"         # optional: knowledge search by meaning
    picked = "2026-11-04 suite 2026.11"      # set by `puzzle agent pick` (ADR 0022)

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


def anthropic_key_problem(key: str) -> str | None:
    """A paste that cannot be one key (None if it might be): pasted twice, stray text.
    Whether Anthropic accepts it is checked by a call, not guessed from its prefix."""
    if key.count("sk-ant-") > 1:
        return "it contains more than one key; paste a single key"
    if not key.startswith("sk-ant-"):
        return "Anthropic keys start with 'sk-ant-'; this does not"
    if any(c.isspace() for c in key):
        return "it contains spaces or line breaks"
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
    embed_model: str = ""  # an embedding model on the local endpoint, for knowledge search
    picked: str = ""  # "2026-11-04 suite 2026.11": when `puzzle agent pick` chose the model

    def build(self) -> Provider:
        model = self.model or DEFAULT_MODELS.get(self.provider, "")
        key = self.api_key()
        if self.provider == "anthropic":
            return AnthropicProvider(model=model, api_key=key, auth=stored_key("anthropic-auth"))
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


@dataclass(frozen=True)
class ModelChoice:
    """A model the agent window's chip offers (ADR 0074)."""

    provider: str  # "anthropic" or "openai"
    model: str
    remote: bool

    def where(self) -> str:
        return "Anthropic" if self.provider == "anthropic" else (
            "a server" if self.remote else "this computer")  # fmt: skip


def available_models(s: AgentSettings | None = None, timeout: float = 5.0):
    """The models each configured provider offers: Anthropic's (with a key), the
    OpenAI-compatible endpoint's (when it answers). Returns (choices, notes): a note
    says why a provider offered none. Network calls: run it off the GUI thread."""
    import json
    import urllib.request

    s = s or load_settings()
    choices: list[ModelChoice] = []
    notes: list[str] = []
    key = os.environ.get("ANTHROPIC_API_KEY") or stored_key("anthropic")
    if key:
        try:
            from puzzle.agent.providers import anthropic_client, key_auth

            client = anthropic_client(key, stored_key("anthropic-auth") or key_auth(key))
            for m in client.models.list(limit=50):
                choices.append(ModelChoice("anthropic", m.id, True))
        except Exception as exc:  # noqa: BLE001 - offline, a bad key: said, not raised
            notes.append(f"Anthropic: {exc}")
    else:
        notes.append("Anthropic: no API key (API key… stores one)")
    url = s.base_url.rstrip("/") + "/models"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            data = json.loads(response.read())
        host = urllib.request.urlparse(s.base_url).hostname or ""
        remote = host not in ("localhost", "127.0.0.1", "::1")
        choices += [ModelChoice("openai", m["id"], remote) for m in data.get("data", [])]
    except Exception:  # noqa: BLE001 - nothing listening is the usual case
        notes.append(f"No local model server at {s.base_url}")
    return choices, notes


def choose_model(choice: ModelChoice, path: Path | None = None) -> Path:
    """Make `choice` the agent's model (a user setting, never the project's)."""
    s = load_settings(path)
    s.provider, s.model = choice.provider, choice.model
    return save_settings(s, path)


def load_settings(path: Path | None = None) -> AgentSettings:
    path = path or settings_path()
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    s = AgentSettings(**{k: v for k, v in data.items() if k in AgentSettings.__dataclass_fields__})
    s.provider = os.environ.get("PUZZLE_AGENT_PROVIDER", s.provider)
    s.model = os.environ.get("PUZZLE_AGENT_MODEL", s.model)
    s.base_url = os.environ.get("PUZZLE_AGENT_BASE_URL", s.base_url)
    return s


def save_settings(s: AgentSettings, path: Path | None = None) -> Path:
    """Write the agent settings (the Settings dialog's Agent page): what differs from
    the defaults, so a later Puzzle's defaults still apply to the rest."""
    import tomli_w

    path = path or settings_path()
    defaults = AgentSettings()
    data = {
        k: getattr(s, k)
        for k in AgentSettings.__dataclass_fields__
        if getattr(s, k) != getattr(defaults, k) and getattr(s, k) is not None
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(data), encoding="utf-8")
    return path
