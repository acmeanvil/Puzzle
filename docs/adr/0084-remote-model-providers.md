# 0084. The agent on any remote model: providers as data, four wire formats

- Status: Proposed
- Date: 2026-11-13

## Context

The built-in agent (ADR 0015) talks to two kinds of model today (`agent/providers.py`):

- Claude through the `anthropic` package;
- one OpenAI-compatible `/chat/completions` endpoint through the standard library, meant
  for local servers (Ollama, llama.cpp, LM Studio). It is "remote" only when its host isn't
  localhost.

`agent.toml` holds one `provider` ("anthropic" or "openai"), one `model`, one `base_url`
and one key variable. The agent window's model chip (ADR 0074) lists Anthropic's models
and the one endpoint's `/models`. The Settings page has a two-entry provider dropdown.
So a user with an OpenAI or Google key can reach those models only by typing a base URL
over the local endpoint's, losing the local one. The chip can't show both, and nothing
says which models can call tools.

The maintainer wants the agent window to be agnostic about remote models. It should be
configurable for the most used providers: Anthropic, OpenAI, Google, Hugging Face,
Thinking Machines, Meta and NVIDIA.

This ADR and ADR 0085 are the last new records for now (ADR 0072, 2026-11-13). It
must be complete enough to be built in steps without another.

What each provider offers (checked 2026-11-13; endpoints move, so each entry is data, see
decision 1):

| Provider | Endpoint | Wire format | Key | Notes |
| --- | --- | --- | --- | --- |
| Anthropic | `api.anthropic.com` | Messages | `ANTHROPIC_API_KEY` | model list; tools, images, PDFs; prompt caching by `cache_control`; extended thinking blocks carry a signature that must be sent back unchanged with the tool results that follow |
| OpenAI | `api.openai.com/v1` | Responses (`/responses`); Chat Completions still served | `OPENAI_API_KEY` | `/models` lists every model, including non-chat ones. Reasoning models keep their reasoning between tool calls through the Responses API (`store: false` with `reasoning.encrypted_content` included, so nothing is kept server-side); Chat Completions drops it |
| Google (Gemini API) | `generativelanguage.googleapis.com/v1beta` | `generateContent` (native); an OpenAI-compatible layer at `/v1beta/openai/` | `GEMINI_API_KEY` (header `x-goog-api-key`) | Gemini 3 returns a `thoughtSignature` on function calls that must be sent back, or the next turn fails with a 4xx error. The compatible layer puts it in a non-standard `extra_content` that generic clients drop; users report multi-turn tool calls failing there |
| Hugging Face (Inference Providers) | `router.huggingface.co/v1` | Chat Completions (Responses also served) | `HF_TOKEN` | routes to many hosts. A model id may name its host (`model:groq`) or a policy (`:fastest`, `:cheapest`); `/models` lists chat models; tools supported per model |
| Thinking Machines (Tinker) | `tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1` | Chat Completions (beta) | `TINKER_API_KEY` | the model is a sampler checkpoint path (`tinker://…/sampler_weights/…`), typed by the user. No model list; tool calling not documented; reasoning inline unless `separate_reasoning`; an Anthropic-compatible endpoint is also in beta. Meant for the user's own fine-tuned models |
| Meta (Llama API) | `api.llama.com/compat/v1` | Chat Completions (compatibility endpoint; `api.llama.com/v1` is Meta's own shape) | `LLAMA_API_KEY` | tools and images on Llama 4 models; access by account and region |
| NVIDIA (API catalog, NIM) | `integrate.api.nvidia.com/v1` | Chat Completions | `NVIDIA_API_KEY` | many hosts' open models; tool support is per model. A self-hosted NIM container serves the same API at the user's own URL |

So four wire formats cover all seven:

- Anthropic's Messages;
- OpenAI's Responses;
- Gemini's `generateContent`;
- Chat Completions, which serves Hugging Face, Thinking Machines, Meta, NVIDIA, the local
  servers, and the many other compatible hosts (OpenRouter, Groq, Mistral, Together,
  Azure OpenAI with a key, vLLM, self-hosted NIM).

The two things that differ beyond the wire are both state the agent must carry between
turns: what a model sends back about its own reasoning (Anthropic's thinking signatures,
OpenAI's encrypted reasoning, Gemini's thought signatures), and which models can call
tools at all. The agent is useless without tools: every action goes through the command
layer as a tool call (ADR 0015).

ADR 0015's rule stands: a remote provider gets outlines and summaries, and files and
images only with the user's consent. Keys never go in a project.

## Decision

1. **Providers are data** (`agent/catalog.py`, `PROVIDERS`). Each entry has:
   - `id` and `name`;
   - `wire` (`messages`, `responses`, `gemini` or `chat`);
   - `base_url`;
   - `key_env` (the environment variable), `key_hint` (what a key looks like, for the
     paste check `anthropic_key_problem` does now; never a reason to refuse a key the
     provider accepts) and `key_header` (Bearer, `x-api-key` or `x-goog-api-key`);
   - `models` (how its models are listed, or `typed` when they can't be);
   - `default_model` (only where the provider names a stable alias, else none: the
     chip asks);
   - `docs` and `data_terms` (links shown beside the key field);
   - `remote` (always true but for `local`).

   Entries: `anthropic`, `openai`, `google`, `huggingface`, `thinking_machines`,
   `meta`, `nvidia`, plus:
   - `local`: today's OpenAI-compatible endpoint on this computer; its default stays
     Ollama's URL;
   - `compatible`: any other Chat Completions endpoint by URL, several allowed, each
     with a name the user gives, which covers self-hosted NIM, OpenRouter, Groq and
     Azure.

   An endpoint that moves is a one-line change with a test, not code. A user's
   `base_url` overrides the entry's.

2. **Four wire formats, one conversation.**
   - `providers.py` keeps its `Provider` protocol (`complete(system, messages, tools) ->
     Reply`) and gains one class per wire:
     - `MessagesProvider` (today's `AnthropicProvider`, on the `anthropic` package);
     - `ResponsesProvider`, `GeminiProvider` and `ChatProvider` (today's
       `OpenAIProvider`, generalised), on the standard library, as now: no new
       dependencies.
   - The agent's messages stay neutral (text, images, documents, tool calls, tool
     results; ADR 0074).
   - Each assistant turn may hold an opaque `carry` from the provider that made it,
     sent back unchanged on the next request to that provider and dropped for any other:
     - Anthropic's thinking blocks with their signatures;
     - OpenAI's encrypted reasoning items;
     - Gemini's thought signatures, keyed by call id.

     This is what the three providers need for multi-turn tool use with reasoning.
   - Switching models starts a fresh chat (ADR 0074), so a carry never crosses
     providers.

3. **Configuration** (`agent.toml`, the user's, never a project's):
   - `provider` and `model` keep naming the current choice.
   - New `[providers.<id>]` tables hold what differs from the catalog:
     - `base_url` and `key_env`;
     - `enabled`;
     - `favourites` (models pinned to the chip's top);
     - `send_files` (per provider, default off for remote: ADR 0015's `send_images`, now
       per provider, with the old key read as every provider's default);
     - `reasoning` (none, low, medium, high: mapped to each wire's own setting, left
       unset when not given so the provider's default applies);
     - `max_output_tokens`;
     - extra headers a provider requires (OpenAI's organisation and project, never
       anything about the user's project);
     - for `huggingface`, a default host or policy suffix.
   - `[[compatible]]` entries hold a name, URL and key variable.
   - Old files keep working:
     - `provider = "openai"` with a localhost `base_url` reads as `local`;
     - with another host it reads as one `compatible` entry, written back in the new
       form on the next save.
   - `PUZZLE_AGENT_PROVIDER`, `_MODEL` and `_BASE_URL` still override.

4. **Keys.**
   - Each provider's key comes from its environment variable, else the keychain under
     service "puzzle", account = the provider's id (as now).
   - `puzzle agent-key set|check|remove --provider ID` (a hidden prompt; `anthropic`
     when not given, as today).
   - Settings > Agent: a row per provider with:
     - its state (no key, key in the keychain, key from the environment, key rejected);
     - Set key… (a hidden field);
     - Test, one cheap authenticated call (its model list, or a one-token request where
       there is none) saying what came back;
     - Remove;
     - its docs and data-terms links.
   - **Keys never pass through the agent or MCP:** no tool reads, sets or lists them,
     and the agent can only say where to set one.

5. **The model chip lists every enabled provider's models**, grouped by provider, as
   now off the GUI thread:
   - favourites first;
   - then those known to call tools;
   - then the rest, under "may not call tools".
   - Typed-model providers (Thinking Machines, a `compatible` entry without `/models`)
     show "Type a model…".
   - A note per provider says why it offered none (no key, offline, rejected key).

   What a model can do (tools, images, PDFs, context length) comes from:
   - the provider's own list where it says (Gemini's `supportedGenerationMethods`, the
     Hugging Face router's per-model data);
   - else a small table of name patterns in the catalog;
   - else a probe on first use: one request with a trivial tool, the answer cached in
     `<user data dir>/agent-models.json`.

   A model that can't call tools is refused when chosen, with the reason and the
   provider's alternatives, never run tool-less.

6. **Streaming** (ADR 0015's open item, ADR 0072 section 9):
   - every wire streams (server-sent events) and the agent window shows the reply as
     it comes;
   - the status line shows "Thinking…" while reasoning streams and the tool name while
     a tool call's arguments arrive;
   - Stop (ADR 0074) closes the stream;
   - a provider or proxy that won't stream falls back to one response, said once in the
     log.

7. **Errors in one vocabulary.** A `ProviderError` gains a `kind`:
   - `auth` (key missing or rejected: where to set it);
   - `rate_limit` (retried up to three times, honouring `Retry-After`, then said);
   - `quota` (billing: the provider's message);
   - `context` (too long: the agent drops old tool results first and says so);
   - `model` (gone or not allowed: the chip's list);
   - `tools` (refused tool calls);
   - `network`;
   - `server`.

   The agent panel shows the plain sentence, the log the provider's own text.

8. **Privacy and cost.**
   - ADR 0015's rule holds for every remote provider, decided per provider (`send_files`).
   - Requests carry no project names, paths or user details beyond what the outline
     already holds, and no identifying headers; a generic client name is the user agent.
   - Each turn's input and output tokens go in the log and the agent panel's status, per
     provider. The budget (runs, solver minutes, core-hours) is unchanged: Puzzle prices
     no tokens, since prices move; the provider's console is the bill.

9. **Measuring remote models** (ADR 0022):
   - `puzzle agent pick --remote` runs the evaluation suite on the enabled remote
     providers' tool-calling models;
   - it is opt-in, states the number of requests first, and stops at a request budget
     given by `--budget`;
   - scores are kept beside the local ones in `agent-evals/`, so the chip marks a
     measured model's score whichever provider serves it.

10. **Built as ADR 0057 asks:**
    - a skill `agent-providers`: which provider for what (local for confidential work,
      Thinking Machines for one's own fine-tuned model, NVIDIA or Hugging Face for open
      models), where keys go, what `send_files` means, what each error says;
    - Help text from the catalog (ADR 0083's generated reference lists the providers
      from `PROVIDERS`);
    - no MCP tool, since outside agents bring their own model; the `agents` outline
      section names the configured provider and model only.

11. **Tests.**
    - Each wire format against recorded responses (`tests/data/providers/<wire>/`: a
      text reply, a tool call, a reasoning carry round-trip, a stream, each error kind)
      served by a local fake HTTP server, so no key or network is needed.
    - The catalog checked for every entry's fields.
    - Migration of old `agent.toml` files.
    - Live checks behind `PUZZLE_PROVIDER_TESTS=1` with keys from the environment:
      one tool round-trip per enabled provider, run by the maintainer, never in CI.

## Plan

1. The catalog, `ChatProvider` generalised from `OpenAIProvider`, the configuration
   and its migration, keys per provider (CLI and Settings with Test), the chip over
   several providers. Hugging Face, Meta, NVIDIA, Thinking Machines, local and
   compatible entries work at this step, since all are Chat Completions.
2. `ResponsesProvider` (OpenAI) and `GeminiProvider` (Google) with their carries;
   Anthropic's thinking carry; capability detection and the tools refusal.
3. Streaming on all four wires; the error kinds with retries; tokens per turn.
4. `agent pick --remote`; the skill; Help text; live checks run once per provider and
   their results written here.

## Consequences

- A user with any of the seven providers' keys (or a compatible endpoint) gets the agent
  without editing files, and local and remote models stay side by side in the chip.
- Reasoning models keep their reasoning across tool calls on the three providers that
  need it sent back; on Chat Completions hosts, a model's reasoning is whatever the host
  passes (Tinker's inline reasoning shows as text).
- Endpoints and models move. The catalog is data with a test, model lists come from the
  providers, and only Anthropic names a default, so a renamed model shows up in the chip
  rather than breaking a default.
- Four wire formats are four parsers to keep. The recorded responses pin them, and a
  provider change shows as a failed live check, not a silent wrong reply.
- Quality differs widely across models. Tools and the evaluation scores are shown at the
  point of choice, so the user knows before an agent misbehaves.

## Alternatives considered

- **Chat Completions for every provider** (OpenAI, Google and Anthropic all serve a
  compatible layer): one parser, but OpenAI's reasoning models lose their reasoning
  between tool calls, and Gemini 3's thought signatures ride in a field generic clients
  drop (multi-turn tool calls fail). Anthropic's layer isn't meant for production use.
- **LiteLLM or each provider's SDK:** more providers for free, but a large dependency
  with frequent releases in the installer, and a layer between Puzzle and the wire when
  a provider changes. Three standard-library parsers plus the `anthropic` package keep
  the installer small and the failures readable.
- **Provider entries in code, one class per provider:** seven classes for four wires; an
  endpoint change would be a code change.
- **Vertex AI and Amazon Bedrock now:** their authentication (Google OAuth or service
  accounts, AWS SigV4) is a credentials system of its own. Gemini's API key and Azure's
  key cover most users; both stay in ADR 0072's list.
- **Keys settable by the agent:** convenient, but a key would pass through a model's
  context and the history. Keys stay in hidden fields and the keychain.
