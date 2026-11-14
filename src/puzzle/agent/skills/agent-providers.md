---
name: agent-providers
description: which model provider to use for the built-in agent, where keys go, what sending files means, what each provider error says, and measuring remote models
adr: 0084
---
Read this when the user asks which model to use, how to use their OpenAI, Google,
Hugging Face, Thinking Machines, Meta or NVIDIA account, why the agent says a key was
rejected or a model can't call tools, or whether their data leaves the computer.

You can't set, read or list keys, and you mustn't ask the user to paste one into the
chat: a key in the chat would go into the conversation and the history. Tell them
where to set it (below).

## Which provider for what

| Want | Provider (id) | Notes |
| --- | --- | --- |
| The best results with Puzzle's tools | Anthropic (`anthropic`) | the default; Claude passes the whole evaluation suite |
| OpenAI's models | OpenAI (`openai`) | reasoning models keep their reasoning between tool calls (nothing is stored at OpenAI) |
| Google's Gemini | Google (`google`) | a Gemini API key from Google AI Studio |
| Open models from many hosts | Hugging Face (`huggingface`) or NVIDIA (`nvidia`) | not every model calls tools; the model button says which are known to |
| Meta's Llama models | Meta (`meta`) | access depends on the account and region |
| The user's own fine-tuned model | Thinking Machines (`thinking_machines`) | the model is the checkpoint's `tinker://…` path, typed in the model button |
| Confidential work, nothing leaving the computer | This computer (`local`) | Ollama, llama.cpp or LM Studio; `puzzle agent pick` measures which local model is good enough (skill local-models) |
| Any other endpoint (OpenRouter, Groq, Azure OpenAI, a NIM of their own) | an endpoint added in Settings > Agent > Add endpoint… (`compatible:<name>`) | any Chat Completions endpoint, by URL |

## Setting a provider up

1. A key: Settings > Agent, the provider's row, **Set key…** (a hidden field; it goes
   to the system keychain, never a file), then **Test** (one cheap call that says
   whether the provider accepts it). From a terminal: `puzzle agent-key set
   --provider ID` (hidden prompt), `puzzle agent-key check --provider ID`,
   `puzzle agent-key remove --provider ID`. A key in the provider's environment
   variable (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `HF_TOKEN`,
   `TINKER_API_KEY`, `LLAMA_API_KEY`, `NVIDIA_API_KEY`) is used before the keychain.
2. A model: the model button above the chat lists every provider that has a key,
   favourites first, then models known to call tools, then "may not call tools".
   Choosing one starts a new chat. A model not known to call tools is asked once with
   a trivial tool; if it doesn't call it, it is refused (the agent acts only through
   tools) and its provider's models that can are named.
3. Options per provider (Settings > Agent > Options): the endpoint, the key's
   variable, whether its models are offered, images and files, reasoning (none, low,
   medium, high, or the provider's default), the most output tokens, favourites, and
   for Hugging Face a host or policy (`:fastest`, `:cheapest`, `:groq`).

## Images and files

By default a remote provider gets outlines and summaries only, never rendered images,
attached pictures or PDFs (ADR 0015); a local model gets them. "Images and files" on
the Agent page sets the default, and each provider's options can override it. Files
held back are named in the chat. Requests carry no project name, path or anything
about the user beyond the outline.

## What the errors say

| Kind | Means | Do |
| --- | --- | --- |
| auth | no key, or the provider rejected it | Set key… and Test in Settings > Agent |
| rate_limit | too many requests (retried three times, waiting as asked) | wait a minute |
| quota | no credit or quota left on the account | the provider's console |
| context | the conversation is too long for the model | the agent cuts the oldest tool results itself and says so; else start a new chat |
| model | the model is gone or not offered to this key | choose another in the model button |
| tools | the provider refused the tool calls | choose a model known to call tools |
| network | the provider couldn't be reached | check the connection or the endpoint's address |
| server | the provider's own error | try again |

## Cost

Each reply's input and output tokens are shown in the agent panel's status and kept in
the log, per provider. Puzzle prices no tokens (prices change): the provider's console
is the bill. The autonomy budget (runs, solver minutes, core-hours) doesn't count
tokens.

## Measuring remote models

`puzzle agent pick --remote --budget N` runs the evaluation suite on the enabled remote
providers' tool-calling models (`--model` names some). It says first how many requests
it could take and stops at N. Scores are kept by model name beside the local ones, so
the model button shows a measured model's score (passed/total) whichever provider
serves it.

## Mistakes to avoid

- Asking for, repeating or storing a key in the chat.
- Promising a model can do the job because it is large: whether it calls tools well
  is measured (`agent pick`), not guessed.
- Telling the user files went to a remote provider: unless they allowed it, they
  didn't.
