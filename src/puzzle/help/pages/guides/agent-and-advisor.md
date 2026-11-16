---
title: The agent and the Advisor
section: guide
order: 150
keys: panel:agent, panel:advisor
summary: Ask the built-in agent to set up, check and explain a model, choose how much it may do, and follow the Advisor's next step.
adrs: 0015, 0022, 0040, 0057, 0084
---

The agent is a built-in expert: ask it in plain words to build, check, run or explain
any part of a model. It works through the same commands you do, so every change it makes
is in the history and can be undone. The Advisor is a checklist that says what the
project needs next.

## How to ask the agent

1. Open the Agent panel with the **Agent** button at the right of the toolbar.
2. Type in the message box and send with Ctrl+Enter (⌘+Enter on a Mac). Settings >
   Agent can make Enter send instead.
3. Replies come as text, equations and folded code; the tool calls it made fold into
   one line each. The status line shows the time taken; **Stop** ends its turn after
   the step it is on.
4. **Attach…** sends an image, a PDF or a text file with the next message; you can also
   drop or paste one. A remote provider doesn't get images or files unless you allow it.
5. **New chat** starts again.

## How to choose what the agent may do

The **Agents may** dropdown at the top of the panel sets the project's autonomy:

- **only advise**: it reads and explains, and changes nothing.
- **propose changes** (the default): each change waits under Proposals for you to
  **Accept** or **Reject**.
- **act within the budget**: it applies changes and runs studies until it has spent the
  budget. **Budget…** sets fresh solver runs (default 5), solver time (default 10
  minutes) and remote core-hours (default 2). Saving starts the count again.
- **act freely**: no budget. It asks you twice, the second time to type "act".

Below "act freely", Python scripts and custom equations the agent writes are always
proposed. Agents can never change the autonomy themselves.

## How to choose a model and provider

1. Click the model button beside Attach…: it lists the models of every provider with a
   key, favourites first, then those known to call tools. Choosing one starts a new chat.
2. To add a provider, choose Providers and keys… (or Project → Settings… → Agent), the
   provider's row, **Set key…**, then **Test**. The key goes to the system keychain,
   never a file. From a terminal: `puzzle agent-key set --provider ID`.
3. Anthropic is the default and does best with Puzzle's tools. OpenAI, Google, Hugging
   Face, Meta, NVIDIA and Thinking Machines work too; **Add endpoint…** takes any Chat
   Completions endpoint by URL.
4. For work that must not leave the computer, use a local model (Ollama, llama.cpp, LM
   Studio). Pick a local model… measures the ones you have and recommends the smallest
   that passes.

## How to use the Advisor

1. Window → Advisor (Ctrl+3), also in the Help menu.
2. It lists the workflow's steps for the selected component and study: Geometry,
   Selections, Materials, Component, Mesh, Physics, Supports and loads, Contacts, Study,
   Run, Results, and Mesh convergence, Optimisation, Skin depth or Trust where they apply.
3. Each step is done, needed or optional, with its reason. The heading names the next
   one; its button goes where the fix is made.
4. In an empty project, **Try the cantilever example** opens a worked model.

<!-- key: form:who_may_connect -->
## Outside agents over MCP

Any MCP client (Claude Code, Claude Desktop, an editor) can drive a project with the
same tools the built-in agent has: run `puzzle mcp PROJECT` as its server. It appears in
the history under its `--name`. The same autonomy level and budget apply.

**Who may connect** is the project's: Project → Who may connect… opens Settings > Agent,
whose **This project** box sets **Agents may** and **Who may connect over MCP**: nobody,
programs on this computer (the default) or programs on the network too. Saving sends
one change through the project's history; "act freely" asks twice, as the agent panel
does. On the command line: `puzzle autonomy PROJECT --mcp off|local|network`.

## Options worth knowing

- A remote provider gets outlines and summaries only, never rendered images, attached
  pictures or PDFs, unless "Images and files" is on for it (Settings > Agent). Requests
  carry no project name or path.
- Each reply's tokens are shown in the status; the provider's console is the bill. The
  budget counts runs and solver time, not tokens.
- A key in the provider's environment variable (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
  `GEMINI_API_KEY`...) is used before the keychain.

## Mistakes to avoid

- Pasting a key into the chat: it would go into the conversation and the history. Use
  Set key….
- A model that doesn't call tools can't act: it is checked once when chosen and refused
  if it fails.
- An error "auth" means no key or a rejected one; "quota" means the account is out of
  credit; "context" means the chat is too long: start a new chat.
- Expecting the agent to run studies at "propose changes": runs are proposed too.

## For agents and scripts

Agents read the Advisor with [next_steps](help:mcp:next_steps), the domain pack with
[knowledge](help:mcp:knowledge), and proposals with [proposals](help:mcp:proposals). In
a terminal: [puzzle ask](help:cli:ask) asks the built-in agent,
[puzzle advise](help:cli:advise) prints the Advisor, [puzzle mcp](help:cli:mcp) serves a
project, [puzzle autonomy](help:cli:autonomy) sets the level,
[puzzle agent-key](help:cli:agent-key) stores keys and [puzzle agent](help:cli:agent)
`pick` measures local models.

## See also

- [Getting started](help:start/getting-started)
- [Every agent tool](help:reference/mcp)
- [Versions and projects](help:guides/versions-and-projects)
- [Command line](help:reference/cli)
