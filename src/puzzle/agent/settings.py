"""Which model the built-in agent uses: a user setting, never part of a project.

`<user data dir>/agent.toml` (keys never go in files; ADR 0084 decision 3):

    provider = "anthropic"          # a catalog id (`agent/catalog.py`) or "compatible:<name>"
    model = "claude-sonnet-5-5"
    embed_model = "nomic-embed-text"         # optional: knowledge search by meaning
    picked = "2026-11-04 suite 2026.11 model qwen3:8b"  # set by `puzzle agent pick`
    send_images = false             # every provider's default for sending files

    [providers.openai]              # only what differs from the catalog
    base_url = "..."                # overrides the entry's
    key_env = "MY_OPENAI_KEY"       # the environment variable holding the key
    enabled = true                  # default: when it has a key (local: always)
    favourites = ["gpt-5.1"]        # pinned to the top of the model chip
    send_files = true               # images and PDFs may go to it (ADR 0015)
    reasoning = "medium"            # none, low, medium, high; unset: the provider's own
    max_output_tokens = 8192
    headers = { OpenAI-Organization = "org-..." }
    route = ":fastest"              # Hugging Face: a host or policy after the model id

    [[compatible]]                  # any other Chat Completions endpoint
    name = "openrouter"
    base_url = "https://openrouter.ai/api/v1"
    key_env = "OPENROUTER_API_KEY"

Old files keep working: `provider = "openai"` with a localhost `base_url` reads as
`local`, with another host as one `compatible` entry (written in the new form on the
next save). PUZZLE_AGENT_PROVIDER, PUZZLE_AGENT_MODEL and PUZZLE_AGENT_BASE_URL still
override the file.

Keys: the provider's environment variable first, then the operating system's keychain
(macOS Keychain, Windows Credential Locker, Secret Service on Linux) under service
"puzzle", account = the provider's id. `puzzle agent-key set --provider ID` stores one
there from a hidden prompt. No tool, the agent's or MCP's, reads or sets a key.
"""

from __future__ import annotations

import os
import tomllib
import urllib.request
from dataclasses import dataclass, field, fields
from pathlib import Path

from puzzle.agent.catalog import PROVIDERS, calls_tools, entry, key_problem
from puzzle.agent.providers import (
    ChatProvider,
    GeminiProvider,
    MessagesProvider,
    Provider,
    ProviderError,
    ResponsesProvider,
)

KEYRING_SERVICE = "puzzle"
FORMAT = 2  # agent.toml with provider tables (ADR 0084); older files are migrated
REASONING = ("", "none", "low", "medium", "high")
LOCAL_URL = PROVIDERS["local"].base_url
DEFAULT_MODELS = {pid: e.default_model for pid, e in PROVIDERS.items() if e.default_model}


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
    """A paste that cannot be one Anthropic key (None if it might be). Whether
    Anthropic accepts it is checked by a call, not guessed from its prefix."""
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
class ProviderConfig:
    """What the user changed about one provider (ADR 0084 decision 3)."""

    name: str = ""  # a compatible entry's own name
    base_url: str = ""
    key_env: str = ""
    key_account: str = ""  # a keychain account other than the id (an old file's "openai")
    enabled: bool | None = None
    favourites: list[str] = field(default_factory=list)
    send_files: bool | None = None
    reasoning: str = ""
    max_output_tokens: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    route: str = ""


def _config(data: dict) -> ProviderConfig:
    known = {f.name for f in fields(ProviderConfig)}
    return ProviderConfig(**{k: v for k, v in data.items() if k in known})


def _is_local(url: str) -> bool:
    host = urllib.request.urlparse(url).hostname or ""
    return host in ("localhost", "127.0.0.1", "::1")


@dataclass
class AgentSettings:
    provider: str = "anthropic"
    model: str = ""
    base_url: str = ""  # an old file's endpoint: moved into the provider's table on load
    api_key_env: str = ""  # an old file's key variable: likewise
    send_images: bool | None = None  # every provider's default for send_files
    embed_model: str = ""  # an embedding model on the local endpoint, for knowledge search
    picked: str = ""  # "2026-11-04 suite 2026.11 model M": when `agent pick` chose M
    providers: dict[str, ProviderConfig] = field(default_factory=dict)

    def migrate(self) -> None:
        """An old file's (no `format`) `provider = "openai"` was its one endpoint:
        local, or a compatible entry for another host. Its `base_url` and
        `api_key_env` move into the provider's table."""
        if self.provider == "openai":
            url = self.base_url or LOCAL_URL
            if _is_local(url):
                self.provider = "local"
                if url.rstrip("/") != LOCAL_URL:
                    self.config("local").base_url = url
            else:
                host = urllib.request.urlparse(url).hostname or "endpoint"
                self.provider = f"compatible:{host}"
                cfg = self.config(self.provider)
                cfg.name, cfg.base_url = host, url
                cfg.key_env = self.api_key_env or "OPENAI_API_KEY"
                cfg.key_account = "openai"
            self.base_url = self.api_key_env = ""
        elif self.base_url:  # an endpoint with another provider: the local one's
            if self.base_url.rstrip("/") != LOCAL_URL:
                self.config("local").base_url = self.base_url
            self.base_url = ""
        if self.api_key_env and self.provider in PROVIDERS:
            self.config(self.provider).key_env = self.api_key_env
            self.api_key_env = ""

    # --- each provider ---------------------------------------------------------------

    def config(self, provider: str | None = None) -> ProviderConfig:
        pid = provider or self.provider
        return self.providers.setdefault(pid, ProviderConfig())

    def ids(self) -> list[str]:
        """Every provider the user could use: the catalog's, then their compatible ones."""
        return [p for p in PROVIDERS if p != "compatible"] + sorted(
            p for p in self.providers if p.startswith("compatible:"))  # fmt: skip

    def label(self, provider: str | None = None) -> str:
        pid = provider or self.provider
        if pid.startswith("compatible:"):
            return self.config(pid).name or pid.split(":", 1)[1]
        return entry(pid).name

    def url(self, provider: str | None = None) -> str:
        pid = provider or self.provider
        return (self.providers.get(pid, ProviderConfig()).base_url or entry(pid).base_url).rstrip(
            "/")  # fmt: skip

    def key_env(self, provider: str | None = None) -> str:
        pid = provider or self.provider
        return self.providers.get(pid, ProviderConfig()).key_env or entry(pid).key_env

    def api_key(self, provider: str | None = None) -> str | None:
        """From the environment variable, else the keychain; None when neither has one."""
        pid = provider or self.provider
        env = self.key_env(pid)
        account = self.providers.get(pid, ProviderConfig()).key_account
        return ((os.environ.get(env) if env else None) or stored_key(pid)
                or (stored_key(account) if account else None))  # fmt: skip

    def key_source(self, provider: str | None = None) -> str:
        pid = provider or self.provider
        env = self.key_env(pid)
        if env and os.environ.get(env):
            return f"environment ({env})"
        account = self.providers.get(pid, ProviderConfig()).key_account
        if stored_key(pid) or (account and stored_key(account)):
            return "keychain"
        return "none"

    def needs_key(self, provider: str | None = None) -> bool:
        pid = provider or self.provider
        return entry(pid).remote and bool(self.key_env(pid))

    def enabled(self, provider: str) -> bool:
        cfg = self.providers.get(provider, ProviderConfig())
        if cfg.enabled is not None:
            return cfg.enabled
        if provider == "local" or provider.startswith("compatible:"):
            return True
        return self.api_key(provider) is not None

    def files_allowed(self, provider: str | None = None, remote: bool | None = None) -> bool:
        """ADR 0015: by default only outlines and summaries go to a remote provider."""
        pid = provider or self.provider
        cfg = self.providers.get(pid, ProviderConfig())
        if cfg.send_files is not None:
            return cfg.send_files
        if self.send_images is not None:
            return self.send_images
        return not (entry(pid).remote if remote is None else remote)

    def images_allowed(self, provider: Provider) -> bool:
        return self.files_allowed(getattr(provider, "name", self.provider), provider.remote)

    # --- the model -------------------------------------------------------------------

    def build(self, provider: str | None = None, model: str | None = None) -> Provider:
        pid = provider or self.provider
        e = entry(pid)
        cfg = self.providers.get(pid, ProviderConfig())
        model = model if model is not None else self.model
        model = model or e.default_model
        if not model:
            raise ProviderError(f"choose a model for {self.label(pid)} (the model button)",
                                "model", pid)  # fmt: skip
        key = self.api_key(pid)
        common = dict(max_output_tokens=cfg.max_output_tokens, reasoning=cfg.reasoning)
        if e.wire == "messages":
            return MessagesProvider(model, key, auth=stored_key("anthropic-auth"), name=pid,
                                    base_url=cfg.base_url, **common)  # fmt: skip
        if e.remote and not key and e.key_env and pid != "local":
            raise ProviderError(
                f"no key for {self.label(pid)}: set one in Settings > Agent "
                f"or with `puzzle agent-key set --provider {pid}`",
                "auth",
                pid,
            )
        if e.wire == "responses":
            return ResponsesProvider(model, self.url(pid), key, name=pid, headers=cfg.headers,
                                     **common)  # fmt: skip
        if e.wire == "gemini":
            return GeminiProvider(model, self.url(pid), key, name=pid, headers=cfg.headers,
                                  **common)  # fmt: skip
        if cfg.route and ":" not in model.split("/")[-1]:
            model += cfg.route if cfg.route.startswith(":") else f":{cfg.route}"
        return ChatProvider(model, self.url(pid), key, name=pid, key_header=e.key_header,
                            headers=cfg.headers,
                            remote=None if pid == "local" or pid.startswith("compatible:")
                            else e.remote, **common)  # fmt: skip


def settings_path() -> Path:
    from puzzle.materials import user_data_dir

    return user_data_dir() / "agent.toml"


@dataclass(frozen=True)
class ModelChoice:
    """A model the agent window's chip offers (ADR 0074, ADR 0084 decision 5)."""

    provider: str  # a catalog id or "compatible:<name>"
    model: str
    remote: bool
    tools: bool | None = None  # known to call tools, known not to, or not known
    favourite: bool = False
    label: str = ""  # the provider's name

    def where(self) -> str:
        if self.label:
            return self.label
        if self.provider == "anthropic":
            return "Anthropic"
        return "a server" if self.remote else "this computer"


def _models_of(s: AgentSettings, pid: str, timeout: float):
    """(choices, note) for one provider; network calls."""
    from puzzle.agent import capabilities
    from puzzle.agent.providers import anthropic_client, key_auth, list_models

    e = entry(pid)
    label = s.label(pid)
    if e.models == "typed":
        return [], f"{label}: type a model (its own checkpoint path)"
    key = s.api_key(pid)
    if e.key_env and e.remote and not key:
        return [], f"{label}: no API key (Settings > Agent sets one)"
    try:
        if e.wire == "messages":
            client = anthropic_client(key, stored_key("anthropic-auth") or key_auth(key),
                                      s.config(pid).base_url)  # fmt: skip
            found = [(m.id, True) for m in client.models.list(limit=100)]
        else:
            cfg = s.providers.get(pid, ProviderConfig())
            found = list_models(e.wire, s.url(pid), key, e.key_header, cfg.headers, timeout,
                                label)  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - offline, a bad key: said, not raised
        if pid == "local":
            return [], f"No local model server at {s.url(pid)}"
        said = exc.plain(label) if isinstance(exc, ProviderError) else str(exc)
        return [], f"{label}: {said}"
    favourites = set(s.providers.get(pid, ProviderConfig()).favourites)
    remote = (not _is_local(s.url(pid))) if pid == "local" or pid.startswith(
        "compatible:") else e.remote  # fmt: skip
    out = []
    for model, tools in found:
        known = tools if tools is not None else calls_tools(pid, model)
        if known is None:
            known = capabilities.known(pid, model)
        out.append(ModelChoice(pid, model, remote, known, model in favourites, label))
    return out, ""


def available_models(s: AgentSettings | None = None, timeout: float = 5.0):
    """The models each enabled provider offers, favourites first, then those known to
    call tools, then the rest (ADR 0084 decision 5). Returns (choices, notes): a note
    says why a provider offered none. Network calls, in parallel: run it off the GUI
    thread."""
    from concurrent.futures import ThreadPoolExecutor

    s = s or load_settings()
    ids = [p for p in s.ids() if s.enabled(p)]
    choices: list[ModelChoice] = []
    notes: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, len(ids))) as pool:
        for found, note in pool.map(lambda p: _models_of(s, p, timeout), ids):
            choices += sorted(found, key=lambda c: (not c.favourite, c.tools is not True,
                                                    c.model))  # fmt: skip
            if note:
                notes.append(note)
    return choices, notes


def choose_model(choice: ModelChoice, path: Path | None = None) -> Path:
    """Make `choice` the agent's model (a user setting, never the project's)."""
    s = load_settings(path)
    s.provider, s.model = choice.provider, choice.model
    return save_settings(s, path)


def load_settings(path: Path | None = None) -> AgentSettings:
    path = path or settings_path()
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    tables = {pid: _config(v) for pid, v in (data.pop("providers", None) or {}).items()
              if isinstance(v, dict)}  # fmt: skip
    for c in data.pop("compatible", None) or []:
        if isinstance(c, dict) and c.get("name"):
            tables[f"compatible:{c['name']}"] = _config(c)
    known = {f.name for f in fields(AgentSettings)} - {"providers"}
    s = AgentSettings(**{k: v for k, v in data.items() if k in known}, providers=tables)
    if data.get("format", 1) < FORMAT:
        s.migrate()
    s.provider = os.environ.get("PUZZLE_AGENT_PROVIDER", s.provider)
    s.model = os.environ.get("PUZZLE_AGENT_MODEL", s.model)
    if os.environ.get("PUZZLE_AGENT_BASE_URL"):
        s.config().base_url = os.environ["PUZZLE_AGENT_BASE_URL"]
    return s


def save_settings(s: AgentSettings, path: Path | None = None) -> Path:
    """Write the agent settings (the Settings dialog's Agent page): what differs from
    the defaults, so a later Puzzle's defaults still apply to the rest."""
    import tomli_w

    path = path or settings_path()
    defaults = AgentSettings()
    data: dict = {"format": FORMAT} | {
        k: getattr(s, k)
        for k in ("provider", "model", "send_images", "embed_model", "picked")
        if getattr(s, k) != getattr(defaults, k) and getattr(s, k) is not None
    }
    blank = ProviderConfig()
    tables, compatible = {}, []
    for pid, cfg in sorted(s.providers.items()):
        own = {f.name: getattr(cfg, f.name) for f in fields(ProviderConfig)
               if getattr(cfg, f.name) != getattr(blank, f.name)
               and getattr(cfg, f.name) is not None}  # fmt: skip
        if pid.startswith("compatible:"):
            compatible.append({"name": cfg.name or pid.split(":", 1)[1]} | own)
        elif own:
            tables[pid] = own
    if tables:
        data["providers"] = tables
    if compatible:
        data["compatible"] = compatible
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(data), encoding="utf-8")
    return path


def check_key(provider: str, key: str, s: AgentSettings | None = None) -> str:
    """One cheap authenticated call (Settings > Agent's Test, `agent-key check`): the
    sentence saying what came back. Raises ProviderError when the key is refused."""
    from puzzle.agent.providers import check_anthropic_key, list_models

    s = s or load_settings()
    e = entry(provider)
    if e.wire == "messages":
        auth = check_anthropic_key(key)
        return f"Anthropic accepts the key ({'x-api-key' if auth == 'api_key' else 'bearer'})"
    if e.models == "typed":
        model = s.model if s.provider == provider else ""
        if not model:
            return (f"{s.label(provider)} lists no models, so the key is checked with the "
                    "first message (type a model in the model button)")  # fmt: skip
        s.build(provider, model)  # builds with the stored key; a one-token request:
        from puzzle.agent.http import headers_for, post_json

        post_json(f"{s.url(provider)}/chat/completions",
                  {"model": model, "max_tokens": 1,
                   "messages": [{"role": "user", "content": "ping"}]},
                  headers_for(key, e.key_header, s.config(provider).headers), 30.0,
                  s.label(provider))  # fmt: skip
        return f"{s.label(provider)} accepts the key ({model} answered)"
    found = list_models(e.wire, s.url(provider), key, e.key_header, s.config(provider).headers,
                        15.0, s.label(provider))  # fmt: skip
    return f"{s.label(provider)} accepts the key ({len(found)} models listed)"


def paste_problem(provider: str, key: str) -> str | None:
    """Why a pasted key can't be one (None if it might be), per provider."""
    if entry(provider).wire == "messages":
        return anthropic_key_problem(key)
    return key_problem(provider, key)
