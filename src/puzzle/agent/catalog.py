"""The model providers the built-in agent can talk to, as data (ADR 0084 decision 1).

Each entry says which wire format it speaks (`messages`: Anthropic's Messages,
`responses`: OpenAI's Responses, `gemini`: Gemini's generateContent, `chat`: Chat
Completions), where, how its key is sent and how its models are listed. An endpoint
that moves is a one-line change here (with `tests/test_agent_providers.py`), not code;
a user's `base_url` in agent.toml overrides the entry's.

`local` is an OpenAI-compatible server on this computer (Ollama, llama.cpp, LM
Studio); `compatible` is the template for any other Chat Completions endpoint the user
names (self-hosted NIM, OpenRouter, Groq, Azure OpenAI), several allowed, each kept as
`compatible:<name>`.

What a model can do comes from the provider's list where it says; else from
`TOOL_PATTERNS` (names known to call tools, or known not to: embedding, speech and
image models); else it is probed on first use (`agent/capabilities.py`).
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

WIRES = ("messages", "responses", "gemini", "chat")
KEY_HEADERS = ("bearer", "x-api-key", "x-goog-api-key", "none")


@dataclass(frozen=True)
class ProviderEntry:
    id: str
    name: str
    wire: str
    base_url: str
    key_env: str  # the environment variable holding the key ("" when none is needed)
    key_hint: str  # how a key starts, for the paste check (never a reason to refuse)
    key_header: str  # how the key is sent (KEY_HEADERS)
    models: str  # "list": the provider lists them; "typed": the user types one
    default_model: str = ""  # only where the provider names a stable alias
    docs: str = ""
    data_terms: str = ""  # how the provider treats what is sent
    remote: bool = True  # whether data leaves this computer
    reasoning: str = ""  # the wire's reasoning setting, if any (`settings.REASONING`)


PROVIDERS: dict[str, ProviderEntry] = {
    e.id: e
    for e in [
        ProviderEntry(
            "anthropic", "Anthropic (Claude)", "messages", "https://api.anthropic.com",
            "ANTHROPIC_API_KEY", "sk-ant-", "x-api-key", "list",
            default_model="claude-sonnet-5-5",
            docs="https://docs.anthropic.com/en/api/overview",
            data_terms="https://www.anthropic.com/legal/commercial-terms",
            reasoning="thinking"),
        ProviderEntry(
            "openai", "OpenAI", "responses", "https://api.openai.com/v1",
            "OPENAI_API_KEY", "sk-", "bearer", "list",
            docs="https://platform.openai.com/docs/api-reference/responses",
            data_terms="https://openai.com/enterprise-privacy/",
            reasoning="effort"),
        ProviderEntry(
            "google", "Google (Gemini)", "gemini",
            "https://generativelanguage.googleapis.com/v1beta",
            "GEMINI_API_KEY", "AIza", "x-goog-api-key", "list",
            docs="https://ai.google.dev/gemini-api/docs",
            data_terms="https://ai.google.dev/gemini-api/terms",
            reasoning="thinking_level"),
        ProviderEntry(
            "huggingface", "Hugging Face (Inference Providers)", "chat",
            "https://router.huggingface.co/v1", "HF_TOKEN", "hf_", "bearer", "list",
            docs="https://huggingface.co/docs/inference-providers",
            data_terms="https://huggingface.co/terms-of-service",
            reasoning="effort"),
        ProviderEntry(
            "thinking_machines", "Thinking Machines (Tinker)", "chat",
            "https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1",
            "TINKER_API_KEY", "", "bearer", "typed",
            docs="https://tinker-docs.thinkingmachines.ai/compatible-apis/openai"),
        ProviderEntry(
            "meta", "Meta (Llama API)", "chat", "https://api.llama.com/compat/v1",
            "LLAMA_API_KEY", "LLM|", "bearer", "list",
            docs="https://llama.developer.meta.com/docs/"),
        ProviderEntry(
            "nvidia", "NVIDIA (API catalog)", "chat", "https://integrate.api.nvidia.com/v1",
            "NVIDIA_API_KEY", "nvapi-", "bearer", "list",
            docs="https://docs.api.nvidia.com/",
            data_terms="https://www.nvidia.com/en-us/about-nvidia/terms-of-service/",
            reasoning="effort"),
        ProviderEntry(
            "local", "This computer (Ollama, llama.cpp, LM Studio)", "chat",
            "http://localhost:11434/v1", "", "", "bearer", "list", remote=False),
        ProviderEntry(
            "compatible", "Another Chat Completions endpoint", "chat", "", "", "", "bearer",
            "list"),
    ]
}  # fmt: skip

# Model names known to call tools (True) or known not to (False), per provider; the
# first match wins, "*" for every provider. Unknown models are probed when chosen.
TOOL_PATTERNS: list[tuple[str, str, bool]] = [
    ("*", "*embed*", False),
    ("*", "*rerank*", False),
    ("*", "*whisper*", False),
    ("*", "*tts*", False),
    ("*", "*transcribe*", False),
    ("*", "*moderation*", False),
    ("*", "*guard*", False),
    ("*", "dall-e*", False),
    ("*", "*image*", False),
    ("*", "*realtime*", False),
    ("*", "*audio*", False),
    ("anthropic", "claude-*", True),
    ("openai", "gpt-4*", True),
    ("openai", "gpt-5*", True),
    ("openai", "o[0-9]*", True),
    ("google", "gemini-*", True),
    ("meta", "llama-4*", True),
    ("meta", "llama-3.3*", True),
    ("*", "*qwen3*", True),
    ("*", "*qwen2.5*", True),
    ("*", "*llama-3.1*", True),
    ("*", "*llama3.1*", True),
    ("*", "*llama-3.3*", True),
    ("*", "*llama3.3*", True),
    ("*", "*llama-4*", True),
    ("*", "*mistral-large*", True),
    ("*", "*mistral-small*", True),
    ("*", "*nemotron*", True),
    ("*", "*gpt-oss*", True),
    ("*", "*deepseek-v3*", True),
    ("*", "*kimi-k2*", True),
    ("*", "*glm-4*", True),
]


def entry(provider: str) -> ProviderEntry:
    """The catalog entry for a provider id; `compatible:<name>` uses `compatible`'s."""
    base = provider.split(":", 1)[0]
    if base not in PROVIDERS:
        from puzzle.agent.providers import ProviderError

        raise ProviderError(f"unknown agent provider '{provider}' (one of {', '.join(PROVIDERS)})")
    return PROVIDERS[base]


def calls_tools(provider: str, model: str) -> bool | None:
    """Whether a model is known to call tools (None: not known; probe it)."""
    base = provider.split(":", 1)[0]
    name = model.lower().rsplit("/", 1)[-1]
    for who, pattern, tools in TOOL_PATTERNS:
        if who in ("*", base) and fnmatch.fnmatch(name, pattern):
            return tools
    return None


def key_problem(provider: str, key: str) -> str | None:
    """A paste that can't be one key (None if it might be): pasted twice, stray text,
    not the provider's usual start. Only a warning: the provider decides."""
    e = entry(provider)
    if any(c.isspace() for c in key):
        return "it contains spaces or line breaks"
    if e.key_hint and key.count(e.key_hint) > 1:
        return "it contains more than one key; paste a single key"
    if e.key_hint and not key.startswith(e.key_hint):
        return f"{e.name} keys usually start with '{e.key_hint}'; this does not"
    return None
