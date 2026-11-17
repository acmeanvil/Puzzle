# Puzzle's agent interface, and whether Looking Glass fits

Reviewer: agent-interface part of the design review, 2026-11-16. Both repositories were read only.
Scratch scripts and their output are in `scratchpad\review\agent\` (`measure.py`, `probe.py`).

How things were checked:

- **Read:** the ADRs (0015, 0022, 0040, 0057, 0072 section 9 and the forms plan, 0074, 0084), the guide `agent-and-advisor.md`, `agent/agent.py`, `providers.py`, `http.py`, `attachments.py`, `gui/agent_panel.py`, `agent_view.py`, `agent_prefill.py` in full; `mcp.py`, `settings.py`, `evals.py`, `commands/session.py` in the parts cited.
- **Measured:** the tool list and prompt size (`measure.py`), and four behaviours on a scratch copy of `examples/cantilever` with the scripted provider (`probe.py`). No model provider was called.
- **Looking Glass:** read only (README, CLAUDE.md, both ADRs, manifests, `lg-acp/src/model.rs`, parts of `lib.rs` and `session.rs`). It was not built or run: it is macOS-only today and this machine is Windows.

---

## 1. Puzzle's agent interface

### 1.1 Strengths

- **One command layer with real gates.** Every agent change goes through `Session` as `Actor("agent", …)`; `_authorize` (session.py:571) refuses, proposes or applies by level. Agents cannot set autonomy, accept proposals or undo others' changes. "Act" needs a recorded double sign-off. This is a stronger base than most in-app assistants have.
- **The same tools inside and out.** The built-in agent calls the MCP server in-process, so outside agents (Claude Code and others) get exactly what it gets, with coded errors (`PuzzleError.to_dict`).
- **Provider work is careful.** Four wire formats, a per-provider `carry` for signed thinking and encrypted reasoning, one error vocabulary, `Retry-After`, a fallback for proxies that don't stream.
- **Keys are handled well.** Environment variable or OS keychain only; never in a project, never through a tool; a generic user agent.
- **Privacy default is right.** Files and rendered images stay local unless `send_images` is on, and the transcript says when a file was held back.
- **Skills and the knowledge pack** (43 skills, BM25 with optional embedding re-rank) keep domain knowledge out of the prompt until needed.
- **Measurement over guessing.** `puzzle agent pick` and the eval suite are unusual and valuable.
- **The window stays responsive** (ADR 0074): the agent is built and run on a worker, with a status line and elapsed time.

### 1.2 Measured facts

| What | Value | Source |
| --- | --- | --- |
| MCP tools | 110 (not ~150) | `measure.py` |
| Tool definitions sent with every request | 86,600 characters, about 22k tokens | `measure.py` (chars ÷ 4, an estimate) |
| System prompt (instructions + skills catalogue) | 9,393 characters, about 2.3k tokens | `measure.py` |
| Tools with MCP annotations (read-only, destructive) | 0 of 110 | `measure.py` |
| Largest tool definitions | `bolts_generate` 3.0k, `contact_thread` 2.6k, `seismic_design` 2.6k, `bolt_vdi2230` 2.6k, `member_design` 2.3k characters | `measure.py` |
| Four agent edits in one turn | 5 history entries, 3 versions, two labelled "opened" (changed outside Puzzle) | `probe.py` |
| Tool call overhead | about 0.4 s per `submit` on the cantilever; reads about 8 ms | `probe.py` |

### 1.3 Interaction-design problems (UXA)

Effort: S = a day or two, M = about a week, L = several weeks.

| ID | Severity | Problem | Recommendation | Effort |
| --- | --- | --- | --- | --- |
| UXA-1 | High | **The user cannot follow the agent in the model.** `MainWindow.agent_turn` (window.py:3686) holds every reload until the turn ends, so the tree and 3D view stand still for the whole turn, then jump. Nothing marks what was touched. | Refresh the tree per change without redrawing the view; mark objects touched this turn; make each tool line in the transcript select its object. | M |
| UXA-2 | High | **Proposals are a detached one-line list.** `_proposal_text` gives "Changed Tets 2.5 mm (global size)" (the shipped screenshot): the old value, not the new one. No before/after table, no preview in the view, whole-accept only (ADR 0015 lists per-change acceptance as open). | A proposal card inline in the transcript: old → new per field from `Session.preview`, problems before and after, a "Show in view" button using the existing `MainWindow.pending` preview, a tick box per command. | M–L |
| UXA-3 | High | **No context awareness.** Nothing about the selection, the shown result, the step or the active tab reaches the model (no reference in `agent.py` or `agent_panel.py`). The user cannot point at a face or a hot spot and ask. | A context chip in the composer ("Bracket/Hole 3 · von Mises, step 2") sent as a structured block with the message; "Ask the agent about this" on tree and view right-clicks; an "attach the view" button that respects `send_images`. | S–M |
| UXA-4 | High | **Conversations are not kept.** `Agent.messages` lives in memory; opening a project, switching model or closing Puzzle loses the chat. No list of past chats, no export. | Store chats per project in the user data dir (not the project, per ADR 0074), reopen the last one, export as Markdown. | M |
| UXA-5 | Medium–High | **A turn is not a unit.** Each `submit` is its own history entry; there is no "undo what the agent just did" and no checkpoint. | Save a version at the start of each agent turn (the machinery exists) and tag the turn's history entries; an "Undo this turn" button on the reply. | S–M |
| UXA-6 | Medium | **Evidence is thin.** Tool lines show `→ result_stats {"study": …}`: internal names and raw JSON, against the project's own "names, never keys" rule. Results are cut to 160 characters. A picture the agent rendered is never shown (`_event` drops `e.data["image"]`, agent_panel.py:398). | Readable verbs per tool (extend `_doing`), values as a small table, thumbnails inline, a "checks run" footer per reply (problems, `check_study`). | M |
| UXA-7 | Medium | **Whole-task delegation stalls.** A 16-round cap ends "set up and check this bracket" with "ask again to continue". There is no plan or task list; the Advisor (which already knows the workflow steps) sits in another dock. | A plan tool whose items show above the composer with their state; a Continue button; seed the plan from `next_steps`; a higher cap for capable models. | M |
| UXA-8 | Medium | **Long solves block the turn.** A `run_study` through `submit` runs inside the tool call: no progress in the chat, Stop does not reach it (AGENT-2), and nothing notifies when it ends. | Agent-started runs go to the job runner (ADR 0067) and return a job id; the agent reads `run_progress`; a toast on finish; the turn may end and pick up later. | L |
| UXA-9 | Medium | **The prompt is too big for the stated goal.** About 24k tokens before the first word, every request, against ADR 0015's "lightest local model". Many local servers default to smaller contexts than that, and nothing sets or checks the context size. | A core set of about 25 tools plus groups switched on by area (tied to skills); trim the twelve largest descriptions into their skills; record the prompt size in `pick`'s results. | M |
| UXA-10 | Medium | **Context is managed only after it overflows.** `_cut_oldest` runs on a context error; there is no meter, images stay in history, nothing is summarised. | A context-in-use pill; drop old tool results and images after N rounds before the error; a short summary when cutting. | M |
| UXA-11 | Medium | **Prompt injection is not addressed.** Solver logs (`run_log`), imported file headers, object names, attachments and knowledge passages reach the model as plain text. At the two "act" levels, text in a log can ask for edits or runs. | Wrap untrusted tool output in marked blocks and say so in the system prompt; after reading such content at an "act" level, make the next change a proposal; add an injection task to the eval suite. | S–M |
| UXA-12 | Medium | **The MCP surface is tools only.** No annotations, so clients cannot tell reads from writes for their own permission prompts; no resources or prompts (skills are a natural fit for MCP prompts); no notifications; stdio only ("network" is unbuilt). Names mix orders (`bolts_generate`, `weld_add`, `result_summary`), and returns mix text, lists and objects. | Add read-only/destructive hints (cheap); expose skills as prompts; a naming pass before more clients depend on it. | S–M |
| UXA-13 | Low–Medium | **Streaming is plain text, swapped for Markdown at the end**, so the reply jumps. Thinking is not shown. One `QTextBrowser` per segment with no virtualisation; no selection across messages or "copy all". | Re-render Markdown in place a few times a second; a folded "Thinking" line; copy and export for the transcript. | S |
| UXA-14 | Low–Medium | **Nothing is discoverable.** 43 skills exist but the user never sees them; no slash commands, no @-mentions of objects, an empty panel on first open. | `/` lists skills as tasks; `@` completes object names; three suggested requests from the Advisor on an empty chat. | S–M |
| UXA-15 | Medium | **The eval suite is narrow.** 15 tasks, all on the cantilever, single-turn. Nothing covers multi-turn memory, skill use, injection, images, contacts or bolts, long contexts, or the panel itself. | Add a second project (the bracket assembly) and one task per gap above. | M |

### 1.4 Bugs (AGENT)

| ID | Severity | Where | Scenario | Fix | Test |
| --- | --- | --- | --- | --- | --- |
| AGENT-1 | High | `mcp.py:1975` `optimisation_run`, `:1852` `mesh_adapt(solve=True)`, `:1912` `mesh_convergence(solve=True)` | At "act within the budget" these call `optimise.run`/`adapt.run`/`convergence.run` directly. They write no history entry, so `budget_used` (session.py:550) never counts them and `_run_timeout` never runs. An agent with 5 runs left can solve hundreds of points. | Route them through a session method that checks and records against the budget, as sweeps do. | Budget of 2 runs; `optimisation_run` with 5 points must raise `budget_exceeded`. |
| AGENT-2 | Medium–High | `agent.py:134,158,190` | Stop is checked only between steps. It does not close a streaming reply (ADR 0084 decision 6 says it does) and does not reach a solve in a tool call. The button says "Stopping after this step…" for up to the provider's 600 s timeout or the solve's length. | Raise from `on_delta` when `stop` is set; pass `stop` as the run's `cancel`. | A provider that streams slowly; set stop; the turn ends within one delta. |
| AGENT-3 | Medium | `agent_panel.py:405` `new_chat`, `:301` `project_opened`, `:556` `switch` | All three act while a turn is running. Confirmed: `reset()` mid-turn leaves messages `[assistant, tool, assistant]`, with no user message first, which providers refuse on the next send. After `project_opened` the old turn keeps editing the previous project and writes into the new transcript. | Disable these while `busy`, or stop the turn and wait for it first. | Call `new_chat` from inside a scripted reply; the next ask must still start with a user message. |
| AGENT-4 | Medium | `agent.py:145`, `providers.py:240` | A reply with no text and no tool calls is stored as an assistant turn with empty content (confirmed: `{'role': 'assistant', 'content': []}`). Anthropic refuses an empty non-final assistant message, so every later message in that chat fails until New chat. | Don't store an empty turn (or store a placeholder), and tell the user the model returned nothing. | Scripted empty reply, then a second ask; `to_anthropic` has no empty content. |
| AGENT-5 | Medium | `session.py:587` `_writes_code` | It checks an edit's path, not its value. Confirmed: appending a physics with an `equation` to `physics` returns False, so at "act within the budget" a custom PDE's weak form (evaluated with `eval` in a trusted project) is applied without the proposal ADR 0062 promises. | Also search the op's value for `equation`, `weak_form`, `code` and `type: script`. | The probe's command must return True. |
| AGENT-6 | Medium | `agent.py:104,236`; `providers.py:419`; `agent_panel.py:351,398` | `render` reaches almost no one. Remote providers lose the tool by default; local models keep it but go through `ChatProvider`, whose `to_openai` replaces the image with "this provider cannot show it"; on macOS it is off in the GUI; and the panel never shows the picture to the user. | Send tool images to local vision models as a following user image part; show the image in the transcript regardless. | A scripted `render` call puts an image widget in the transcript. |
| AGENT-7 | Medium | `mcp.py:73`, `session.py:490` | Each tool call builds a new `Session`, and each new session's first change runs the "opened" check. Confirmed: four agent edits made three versions, two saying the project "changed outside Puzzle". Version lists fill with false entries. | Keep one session per agent turn, or skip the "opened" check for agent actors. | Four scripted edits make at most one version. |
| AGENT-8 | Low–Medium | `providers.py:135–141,163` | Anthropic usage counts `input_tokens` only, which excludes cached tokens, so the panel under-reports input once caching works. Separately, cache breakpoints sit on tools and system only: the growing message history is sent uncached on every tool round. | Add cache-read and cache-write tokens to the totals; put a breakpoint on the last message block. | Recorded response with cache fields; totals include them. |
| AGENT-9 | Medium, not verified | `providers.py:142–145` | With `reasoning` set, the request sends `thinking: {type: "enabled", budget_tokens}`. Newer Claude models take adaptive thinking only and refuse a fixed budget, which would surface as an unclassified 400. I did not call the API to confirm for the catalogued models. | Choose the thinking form per model from the catalog. | Recorded 400; error kind and a plain sentence. |
| AGENT-10 | Low | `agent_view.py` `stream`/`_end_live`; `agent_panel.py:327` | A provider error after some text has streamed leaves the partial text in the transcript as if said; it is in neither `texts` nor the conversation. | Call `_end_live` (or mark it "interrupted") on failure. | Stream, then raise; no live widget remains. |
| AGENT-11 | Low | `agent_panel.py:645` | Accept with nothing selected accepts the first proposal. | Require a selection, or disable the buttons without one. | No selection: nothing is accepted. |
| AGENT-12 | Low | `mcp.py:2926`, `:1132`, `:1164`, `:2038` | `render` leaves a temp folder per call. `pardiso_build` (starts a compile), `host_check` (opens SSH) and `operator_export` (writes files) run at "advise". | Clean the folder; gate the three by level. | At advise, each raises `not_allowed`. |
| AGENT-13 | Low | `http.py` `post_stream`; `providers.py:362` | A connection dropped mid-stream raises a raw `OSError`, not a `ProviderError`, so the user sees Python's message. A streaming server that omits `index` on tool-call fragments gets each fragment as a separate call. 5xx and overloaded answers are not retried. | Wrap the read loop; key fragments by id when index is absent; retry 5xx once or twice. | A server that closes mid-stream gives kind `network`. |

### 1.5 Against the state of the art

Comparison from general knowledge of these products, not from testing them here.

| Pattern | Where it is common | Puzzle today |
| --- | --- | --- |
| Plan first, then act; a visible task list | Claude Code, Cursor, Copilot agents | Missing (UXA-7) |
| Checkpoints and rewind per turn | Claude Code, Cursor | Missing; versions exist but are not tied to turns (UXA-5) |
| Diff review inline, accept per hunk | Cursor, Claude Code, Copilot | One-line list, whole-accept (UXA-2) |
| Ghost preview of a change in the canvas | CAD assistants, Copilot in Office | Missing, though `MainWindow.pending` could carry it |
| Context from the selection; @-mentions | Cursor, Copilot, Office | Missing (UXA-3, UXA-14) |
| Slash commands and skills shown to users | Claude Code, Cursor | Skills are agent-only (UXA-14) |
| Background agents, notifications on finish | Claude Code, Cursor | Missing (UXA-8) |
| Sub-agents for parallel work (sweeps) | Claude Code | Missing; lower priority, the DOE runner already batches |
| Persistent, shareable transcripts | All chat products | Missing (UXA-4) |
| Reports as artifacts or canvases | Claude, ChatGPT | Partly: the Report operator writes HTML, not tied to the chat |
| Voice | Some assistants | Missing; low value for this work |
| Autonomy levels with a compute budget; a signed history | Rare | **Ahead** of most |
| Local-model measurement on the user's machine | Rare | **Ahead** |
| Documentation chat (AnsysGPT, COMSOL's chatbot) | Simulation vendors | Covered by `knowledge` and `help`, with citations |

Puzzle's governance (levels, budget, history, keys) is better than most. Its conversation surface is a generation behind: it is a chat log beside the model, not an assistant working in the model.

---

## 2. Looking Glass

### 2.1 What it is

A GPU-rendered native desktop app for working with **coding agents**: "a pretty terminal frontend for agent interaction" (`README.md`). It is a client of the open **Agent Client Protocol (ACP)**, so it fronts Claude Code, Codex, Gemini CLI, Qwen Code and its own small agent.

| Aspect | Finding | Source |
| --- | --- | --- |
| Language and toolkit | Rust, GPUI 0.2.2 (Zed's UI toolkit) | `crates/looking-glass/Cargo.toml` |
| Crates | `looking-glass` (app), `lg-acp` (ACP client, file and terminal services), `lg-render` (Markdown model, highlighting), `lg-agent` (built-in agent) | `README.md` Layout |
| Protocol to agents | ACP v1 over stdin/stdout; the UI sees only neutral `Event`/`Command` types | `lg-acp/src/model.rs` |
| MCP | None. Sessions are created with a folder only; "MCP server configuration per session" is listed as not yet | `lg-acp/src/lib.rs:369`, README Notes |
| Built-in agent | Four tools: read file, list directory, write file, run command; 40 steps a turn; Claude's Messages API natively, Chat Completions for the rest | `lg-agent/src/tools.rs`, `anthropic.rs:27` |
| Interaction | Tabs of sessions; streamed Markdown; collapsible Thinking and tool cards with status; a plan panel; permission prompts with diffs (once, always, reject); `@file` mentions from a file sidebar; a context-use pill and token ledger; a terminal panel; saved and resumed sessions; agent and model pickers in the header | README; `session.rs` (`render_plan`, `render_permission`, `render_tool`) |
| Input modalities | Keyboard and mouse. No voice, no vision; image attachments are "not yet" | README Notes |
| Memory | None beyond saved transcripts and ACP `session/load` | `store.rs` |
| Keys | OS keychain via `keyring`, passed in the agent's environment at launch, never to remote hosts | `secrets.rs`, README |
| Remote | Agents over SSH | `lg-acp/src/remote.rs` |
| Platforms | **macOS only**, developed on an Intel Mac. Linux and Windows are an accepted plan "on hold" | `docs/adr/0001` |
| Theme and accessibility | One dark theme. No accessibility work found in the repo | `theme.rs` |
| Tests | 40 unit and end-to-end tests; no GUI tests | CLAUDE.md, grep |
| Maturity | 25 commits over two days (2026-11-14 to 11-15), about 15k lines of Rust, "early prototype" | git log, README |
| Dependencies | 800 packages in `Cargo.lock` | `Cargo.lock` |
| Licence | MIT, same owner as Puzzle. The bundled Nerd Font symbols are MIT | `LICENSE`, `assets/fonts/SymbolsNerdFont-LICENSE.txt` |

**Licence fit.** MIT code can go into a GPL-3.0-or-later project. Its main dependencies are, to my knowledge, MIT or Apache-2.0 (GPUI, alacritty_terminal, the ACP crate, syntect, pulldown-cmark, ureq, keyring); Apache-2.0 is compatible with GPL version 3. I did not check all 800 transitive packages: run `cargo deny` or `cargo license` before shipping any binary.

### 2.2 What is good in it

Its transcript is better than Puzzle's in several concrete ways: a virtualised list, selection across messages, tool cards with live status and output, a plan panel, permission prompts that show a diff, a context meter with cache tokens, saved sessions that resume, `@` mentions, and model and agent switching in the header. Most of the UXA items above have a working example in `session.rs`.

### 2.3 What does not fit

- **It is built for code.** Its services to agents are files and a terminal; its diffs are text diffs; its sidebar is a file tree. Puzzle's agent works on model objects through commands and needs the 3D view, the tree and results.
- **Its tools would bypass the command layer.** An agent with `write_file` and `run_command` in a project folder can edit object files directly, which ADR 0015 forbids ("no client writes project files itself").
- **It does not run where Puzzle ships.** Windows needs Job Objects, shell selection and shortcut changes (`lg-acp/terminal.rs` uses process groups and `/bin/sh`); nothing has been compiled off macOS.

---

## 3. Fit and embedding options

### 3.1 Can its window be embedded in the PySide6 app?

Not realistically. This is from knowledge of GPUI and Qt, not from a test.

- **In-process:** GPUI owns the main-thread event loop (`Application::run`); so does Qt. Two UI loops in one process on macOS is not workable, and GPUI 0.2.2 has no public way to render into a host's view or hand out a texture.
- **Cross-process child window:** Windows allows re-parenting another process's window, with known focus, DPI and input problems. macOS does not allow it at all. X11 can; Wayland cannot. So `QWindow.fromWinId` gives one fragile platform out of three.
- **Web view:** Looking Glass has no web front end, and ADR 0074 already rejected a web engine for the transcript.
- Puzzle draws its own 3D view off screen because on-screen GPU surfaces misbehaved on the maintainer's Mac. A second GPU surface inside the same window adds that risk again.

### 3.2 Options compared

| Option | What it is | Effort | User gains | User loses or risks | Verdict |
| --- | --- | --- | --- | --- | --- |
| 1. Embed the GPUI window | A native child window or shared texture inside the Qt main window | L, with no clear path on macOS or Wayland | Its transcript as is | Two toolkits, two themes, no accessibility parity, a Rust build on five platforms, an app that is macOS-only today | **No** |
| 2a. Companion: Looking Glass → an ACP coding agent → `puzzle mcp` | The user runs Looking Glass beside Puzzle; Claude Code (or another agent) inside it connects to Puzzle's MCP server from its own configuration | None in Puzzle; works today on a Mac | A capable outside agent with a good transcript | No link to the selection or view; the agent also has file and shell tools in the project folder; proposals are reviewed back in Puzzle | **Document it**, for users who already have it |
| 2b. Companion: Puzzle's agent as an ACP agent (`puzzle acp PROJECT`) | Puzzle's built-in loop speaks ACP on stdio. Tool calls become ACP tool calls, proposals become permission requests with a before/after diff of the object, plans become ACP plans. Any ACP client (Looking Glass, Zed and others) can front it | M for a first version | Puzzle's agent in any ACP client, on a second screen; a second, protocol-level test of the agent | Another protocol to keep current; needs a decision record under the ADR freeze; keys arrive in the environment, which Puzzle already reads first | **Prototype later**, after option 3 |
| 3. Port the ideas into the Qt panel | Build the patterns in section 2.2 in `agent_panel.py`/`agent_view.py` | M per group, phased | Everything in one window, themed, accessible, on all five platforms, offline, with the view and tree linked | Nothing; it is the most work in Puzzle itself | **Yes** |
| 4. Do nothing | Keep the panel | None | Nothing | The High items in 1.3 and 1.4 stay | **No** |

What option 2 would need from Puzzle to feel integrated, if pursued: the current selection and shown result as a readable tool or resource; a viewport image tool gated by `send_images`; change notifications so the client need not poll; proposals answerable from the client (today only a user in Puzzle may accept, which is the right rule and would have to map onto the client's permission prompt).

### 3.3 Other considerations

- **Packaging.** Puzzle ships conda packages and installers from `pixi.lock`. A Rust binary means a recipe and a build per platform, plus Vulkan on Linux and DirectX on Windows at run time.
- **Offline and local models (ADR 0022).** Unaffected by option 3. Looking Glass's own agent also talks to local servers, but it would be a second place to configure models.
- **Keys.** Both projects use the OS keychain and neither passes keys through tools. Under 2b the client supplies the key in the environment, which fits Puzzle's order (environment first).
- **Accessibility.** Qt gives Puzzle screen-reader support, keyboard order and three themes with checked contrast (ADR 0045). Looking Glass has one dark theme and no accessibility work.
- **Maintenance.** Two contributors, two languages, two UI toolkits and two agent loops is a heavy load for a chat panel.

---

## 4. Recommendation

**Do not embed Looking Glass; port its interaction patterns into Puzzle's Qt agent panel.** Its stack cannot be embedded on macOS or Wayland, it runs on one of Puzzle's five platforms, and it is a two-day-old prototype built around files and terminals rather than a model, a view and results. What it gets right is the transcript, and those patterns can be built in Qt on top of a command layer that is already stronger than what most assistants have.

### Phased plan

| Phase | Work | Items |
| --- | --- | --- |
| 0. Fix | The budget bypass, Stop, New chat during a turn, the empty reply, the code-writing check, version spam | AGENT-1 to 5, AGENT-7 |
| 1. Follow and trust | Live tree updates and touched marks; proposal cards with before/after and "Show in view"; undo a turn; pictures and readable tool lines | UXA-1, 2, 5, 6; AGENT-6 |
| 2. Context and continuity | Selection and view context; kept chats; a context meter and proactive trimming; a smaller tool set by area | UXA-3, 4, 9, 10 |
| 3. Delegation | A plan list; background runs with notifications; slash skills and @-mentions; injection marking; wider evals | UXA-7, 8, 11, 14, 15 |
| 4. Optional | `puzzle acp` so ACP clients, Looking Glass among them, can front Puzzle's agent; MCP annotations and prompts | Option 2b, UXA-12 |

Phase 4's `puzzle acp` would need a decision record, which the current ADR freeze routes to the maintainer first.

### What to prototype first

1. **A proposal card with "Show in view"** on the scripted provider, using the bracket example: one card, old → new values from `Session.preview`, the change previewed through `MainWindow.pending`, Accept on the card. This tests the hardest part of option 3 (linking the chat to the view) in a few days.
2. **A context chip**: send the selection and shown result with one message and see whether answers improve on three hand-written questions. This is cheap and decides how much of UXA-3 is worth building.
3. Only if a second-screen client is still wanted afterwards: a two-day `puzzle acp` spike, checked headless on the Mac with Looking Glass's own `cargo run -p lg-acp --example smoke`.

---

## 5. Not determined

- Looking Glass was not built or run; its behaviour is taken from its README, CLAUDE.md and source. The repository has no screenshots.
- GPUI embedding limits are from knowledge of the toolkit, not a test.
- Licences of Looking Glass's 800 transitive dependencies were not checked.
- AGENT-9 (the thinking parameter on current Claude models) was not confirmed against the API.
- Token counts are characters ÷ 4, not a tokenizer's count.
- Whether the GUI's Solution panel follows a solve the agent started was not checked.
- AGENT-5 was confirmed at the predicate (`_writes_code` returns False); I did not push a full custom-PDE edit through to a solve.
- The eval suite was read, not run against any model. Not all 43 skills were read.
- The comparison in 1.5 is from general knowledge of those products.
- `CLAUDE.md` in the Puzzle working tree changed during the review (the ADR-freeze line now refers to ADR 0090). That edit was not mine.
