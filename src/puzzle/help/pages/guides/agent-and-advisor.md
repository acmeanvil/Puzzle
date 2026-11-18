---
title: The agent and the Advisor
section: guide
order: 150
keys: panel:agent, panel:advisor
summary: Ask the built-in agent to set up, check and explain a model, choose how much it may do, and follow the Advisor's next step.
adrs: 0090, 0015, 0022, 0040, 0057, 0084
---

The agent is a built-in expert: ask it in plain words to build, check, run or explain
any part of a model. It works through the same commands you do, so every change it makes
is in the history and can be undone. The Advisor is a checklist that says what the
project needs next.

## How to ask the agent

1. Open the Agent panel with the **Agent** button at the right of the toolbar.
2. Type in the message box and send with Ctrl+Enter (⌘+Enter on a Mac). Settings >
   Agent can make Enter send instead.
3. Replies come as text, equations and folded code, drawn as they are written; a model
   that thinks aloud shows it under a folded **Thinking** line. The tools it used fold
   into one line per reply, each said in words ("Read statistics of von mises in Tip
   load"), with the objects it changed as links: click one to select it in the tree. A
   picture the agent draws (`render`) shows there too, saying whether the model was sent
   it. The status line shows the time taken, and while a run it started solves, how far
   it has got; a toast says when that run ends. **Stop** ends its turn: a reply being
   written stops at once and is dropped, a run it started is stopped, and otherwise it
   stops after the step it is on.
4. **Attach…** sends an image, a PDF or a text file with the next message; you can also
   drop or paste one. A remote provider doesn't get images or files unless you allow it.
5. **New chat** starts again. Pressed while the agent is working, it stops that turn
   first; opening another project or choosing another model does the same.
6. An empty chat offers three requests to start from, taken from the project's state.
   Type `/` at the start of a message for the skills (`/bolts add two M8 bolts` reads
   the bolts skill first) and `@` for the project's objects by name.

## How to review what the agent does

- **The tree follows the turn.** Each change shows in the model tree as it is made; the
  3D view and Properties catch up when the turn ends. What the turn added or changed is
  in bold until the next turn, its tooltip saying so.
- **A proposal is a card in the chat.** It lists each command with a tick box and, under
  it, every value it changes, old → new ("Tip load › Values › Y: -100 N → -250 N"), and
  the project's problems now and after. **Show in view** selects what it changes (a
  geometry's new shape is previewed in the Geometry tab, not applied); **Accept**
  applies the ticked commands (untick one to accept the rest), **Reject** drops it.
  Proposals made elsewhere (an outside agent) are listed under Proposals, where
  **Accept** and **Reject** act on the one picked.
- **Undo this turn** under a turn's reply takes back every change it made, as one step;
  Redo puts them all back. It is refused, naming them, when you have changed the same
  objects since: undo those first. Runs are kept.
- **The plan.** For a longer task the agent may keep a plan, shown above the message
  box, each item to do, being done or done. When a turn stops at its round limit,
  **Continue** lets it carry on.

## What the agent sees

- **Sees: …** beside the model button names what goes with your message: the tree's
  selection (by name and place) and the result shown (study, quantity, step). Click it
  to leave them out. Right-click anything in the tree and choose **Ask the agent about
  this…** to start a message about it.
- Text from outside Puzzle (a solver's log, a file's headers, an attached file) is
  marked as data for the model, and after reading it the agent's changes in that turn
  are proposals even at "act within the budget", so an instruction hidden in a file
  can't make changes on its own.
- The agent starts with a core set of tools and loads others by area as a task needs
  them, so a request stays small for local models.

## Conversations

Each project's conversations are kept on this computer, in your user data folder, never
in the project: opening the project reopens the last one. **Chats** lists the earlier
ones to open, **Export this conversation…** writes one as Markdown, **Copy the whole
conversation** puts it on the clipboard, and **Delete this conversation** removes it.
Save as, archives and versions don't take conversations, and a colleague opening the
project sees none of yours. Pictures and attached images and PDFs are not kept, only
their names.

![The agent window: a question, the tools the agent used folded into one line, its answer with an equation, and the proposal it made waiting below with Accept and Reject](image:screens/agent-window.png)

## How to choose what the agent may do

The **Agents may** dropdown at the top of the panel sets the project's autonomy:

- **only advise**: it reads and explains, and changes nothing.
- **propose changes** (the default): each change waits under Proposals for you to
  **Accept** or **Reject**.
- **act within the budget**: it applies changes and runs studies until it has spent the
  budget. **Budget…** sets fresh solver runs (default 5), solver time (default 10
  minutes) and remote core-hours (default 2). Saving starts the count again. Every
  solve counts, whatever started it: a study, each point of a sweep or an
  optimisation, each mesh of a convergence check, each adaptive pass, both meshes of a
  mesh shoot-out, a failed or stopped run too. A run that would go past the budget
  stops at the solve that would, and says so.
- **act freely**: no budget. It asks you twice, the second time to type "act".

Below "act freely", Python scripts and custom equations the agent writes are always
proposed, however the change is made (a new object, an edit, a copy, a batch); an undo
or a version restore that would bring code back is left to you. At "only advise" the
agent also writes no files (exports, archives), starts no build and reaches no remote
host; building CalculiX with PARDISO or cancelling a solve needs "act within the
budget". Agents can never change the autonomy themselves.

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
   one; its button goes where the fix is made. With a geometry and no component yet,
   the Component step's button is New analysis….
4. It follows what is on screen: the selected study; else the study of the selected run
   or result, or of the results in the view; else the study run last. With a run
   selected the heading reads "Results of …" and Probe, Reaction… and Export… are one
   click away. The project's other studies are listed under it with their state and a
   Show button.
5. In an empty project, **Try the cantilever example** opens a worked model (the start
   page has it too, with the bracket assembly).

![The Advisor on the cantilever example: the next step named at the top, each step ticked when done, and a button beside the ones to do](image:screens/advisor.png)

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
- Each reply's tokens are shown in the status, with about how much of the model's
  context the conversation holds; the provider's console is the bill. The budget counts
  runs and solver time, not tokens. Older tool results and pictures are shortened as a
  conversation grows, before the model's context fills.
- A key in the provider's environment variable (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
  `GEMINI_API_KEY`...) is used before the keychain.

## Mistakes to avoid

- Pasting a key into the chat: it would go into the conversation and the history. Use
  Set key….
- A model that doesn't call tools can't act: it is checked once when chosen and refused
  if it fails.
- An error "auth" means no key or a rejected one; "quota" means the account is out of
  credit; "context" means the chat is too long: start a new chat.
- Undoing a turn's changes one by one with Undo when **Undo this turn** does it in one.
- Expecting the agent to run studies at "propose changes": runs are proposed too.

## For agents and scripts

Agents read the Advisor with [next_steps](help:mcp:next_steps), the domain pack with
[knowledge](help:mcp:knowledge), and proposals with [proposals](help:mcp:proposals);
they keep a plan with [plan](help:mcp:plan) and load tools by area with
[tools_load](help:mcp:tools_load) (the skill `agent-turns`). MCP clients see every
tool marked read-only or not, and the skills as prompts. In
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
