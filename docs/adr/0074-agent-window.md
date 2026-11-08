# 0074. The agent window: a composer, live status, rich replies, attachments

- Status: Accepted
- Date: 2026-11-08

## Context

The agent panel (ADR 0015) is a transcript of escaped text over a prompt box, with the
model named in a grey line and its own buttons for the key and for picking a local model.
The maintainer's testing (2026-11-08) asked for:

1. **The model in the composer:** pick the model where one types (as Claude's own
   composer does), see where it runs (this computer or a provider's servers), and switch
   between local and remote when both are set up.
2. **Status as it works:** what the agent is doing now (thinking, reading a skill,
   calling a tool), not only the finished reply.
3. **Rich replies:** replies are Markdown already and hard to read raw; equations should
   show as equations; long parts (a Python snippet it means to put in an operator, with
   its explanation) should fold away.
4. **A window that stays usable** while the agent works: the whole UI froze until the
   answer came.
5. **Attachments:** a file chosen or dropped, or pasted (an image, text), passed to the
   model with the message.

What was measured on the Windows machine:

- A question to a provider that takes 3 s a reply did not hold the window: the ask
  already runs on a worker thread.
- What does hold it: every refresh asked the solver adapters for their checks, and
  listing them read every installed package's metadata, ~300 ms a selection in the
  installed app (fixed with this ADR: read once). Each change an agent makes reloads
  the project and redraws what is shown, seconds on a large model, so an agent making
  several changes keeps the window busy for most of its turn. The first message also
  built the agent (its tools, the skills catalogue) on the GUI thread.

## Decision

1. **Responsiveness first.**
   - The agent is built on the worker, with its first question.
   - While an agent turn runs, reloads from its own changes are held and done once
     when the turn ends (others' changes still reload as now); the view redraws only
     when what is shown changed.
   - The adapters are listed once per process.
   - A turn reports its timings (model, tools) in the log, so a slow one says why.
2. **The composer.** The prompt box gets a bar under it, as Claude's: an attach button,
   the model chip, the send button.
   - The chip names the model and where it runs ("Claude Sonnet 5.5 · Anthropic" or
     "qwen3:8b · this computer"); its menu lists the models each configured provider
     offers (Anthropic's model list with a stored key; an OpenAI-compatible endpoint's
     `/models`), so local and remote can be switched when both are set up.
   - The choice is the user's setting (`agent.toml`), never the project's, and starts
     a fresh chat with that model.
   - "Pick a local model…" and "API key…" move into the chip's menu.
3. **Status.** A line over the composer shows what the agent is doing now: "Thinking…",
   "Reading the skill contact-tool", "Calling result_stats", with the time so far and a
   Stop that ends the turn after the current call. The tool calls of a reply fold into
   one "3 tools used" line under it, opened on a click.
4. **Rich replies.**
   - Each reply is drawn as Markdown (headings, lists, tables, code).
   - TeX between `$...$` or `$$...$$` is drawn as an equation (matplotlib's mathtext;
     no browser engine).
   - A fenced code block is a folded box with its language and line count, opened on
     a click and copyable, so a Python snippet's explanation reads first and the code
     is a click away.
   - The skill prompt asks the model for Markdown, TeX for equations, and code in
     fenced blocks.
5. **Attachments.**
   - The attach button, a drop on the panel, or a paste into the prompt add files to
     the next message, shown as removable chips.
   - Images go to the model as images (PNG, JPEG, GIF, WebP).
   - PDFs go as documents where the provider reads them (Anthropic), else as their
     text.
   - Text files (CSV, logs, .inp, .comm, .py) go as text, cut at 200 kB with a note.
   - Images and documents go to a remote provider only with `send_images` (ADR 0015's
     rule, now "send files"); the chip says when one will be held back. Pasted text
     longer than a few lines becomes a text attachment.
   - Attachments are kept with the chat's messages, not in the project.
6. **Agents through MCP** are unchanged: these are the built-in agent's window.

## Consequences

- The window stays usable during long agent turns, and the cost of every selection drops
  (the adapter scan was paid on each one).
- Replies read as the model meant them; equations and code are legible.
- Providers must handle content blocks (text, image, document) in user messages; the
  scripted test provider records them.
- Rendering TeX needs matplotlib in the GUI environment (already a ParaView and pyvista
  dependency); what mathtext can't draw is shown as the TeX text.

## Alternatives considered

- **A web view (QtWebEngine with KaTeX) for the transcript:** full Markdown and TeX,
  but a large dependency, a second rendering engine and another process; Qt's own
  Markdown plus mathtext covers what the agent writes.
- **The agent in its own process:** isolation from any GIL-heavy tool, but the in-process
  tools share the session cache; the measured holds were on the GUI thread, not the
  worker. Revisit if a tool is found to starve the GUI.
- **Model choice per project:** a model is the user's (keys, local hardware), never the
  project's (ADR 0015).
