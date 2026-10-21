# 0015. Puzzle is AI-aware: one command layer for people and agents

- Status: Accepted
- Date: 2026-10-18

## Context

The intended workflow is hybrid: a person and an AI agent work on the same model. They set up studies, check them, run them and interpret results together. The agent must be built in, and Puzzle must also be drivable by outside agents. Engineering work adds three constraints:
- results must be traceable;
- nothing may change without the user's knowledge;
- model data can be confidential.

Puzzle already has most of what an agent needs:
- a typed model (Pydantic);
- one file per object with content hashes and staleness;
- a capability matrix per solver;
- results in Delta Lake;
- a CLI and headless rendering.

What is missing is a single place where every action is taken.

## Decision

### One command layer

Every action is a typed command: add or edit an object, set a parameter, mesh, run, reattach selections, export. Each command is:
- validated against the schema;
- undoable;
- recorded in a project log with its actor (user, agent, script) and time.

The GUI, the CLI, scripts, the built-in agent and outside agents are all clients of this layer. No client has a private path to the model.

### Agent-facing interfaces

- **MCP server** over the command layer, available from the start, so the built-in agent and outside agents (e.g. Claude Code) use the same tools. Exposure is a setting:
  - off;
  - local only (stdio or a local socket), the default;
  - network, with authentication.
- **Compact reads:**
  - a text outline of the model tree;
  - published JSON schemas;
  - results queries that return summaries (extremes, value at a point, integrals over a selection, comparison with a hand estimate), not node arrays;
  - rendered images.
- **Structured errors:** validation and solver failures carry a code, the object path and the relevant log lines.

### Providers

- **Claude first.** The built-in agent starts on Claude through the API, with a user-supplied key.
- **Pluggable providers.** Remote and local models are configurable per project or user. A local endpoint (OpenAI-compatible or Ollama-style) is a first-class option, so Puzzle can run fully offline.
- **Goal: the lightest local model that does the whole job.** Puzzle is designed so a small local model can do everything listed in Consequences. Deterministic code does the heavy lifting: selection matching, unit checks, hand calculations, log parsing. Tools are narrow, context stays compact, and the model mostly chooses and explains.
- **Agent evaluation suite.** Scripted tasks with checkable outcomes, run against any provider, measure which models suffice. Examples: set up the bracket from a description; diagnose a failed run; repair detached selections; flag a missing constraint.

### Autonomy levels

There are four levels, set per project. The default is Propose.

| Level | The agent may |
| --- | --- |
| Advise | Read the model, results and logs; answer and explain. No changes. |
| Propose (default) | Prepare change sets that the user accepts or rejects, whole or per change. |
| Act in budget | Apply changes and run jobs within a budget (compute time, number of runs, cost), then report. |
| Act | Apply changes and run without a budget. Turning it on needs double sign-off: two separate confirmations, both recorded in the log. |

At every level the agent's changes are attributed in the log and can be undone.

### Guardrails

- **The project log is the record**, not the chat.
- **Result values come only from solver runs.** The agent interprets them and labels its own estimates as estimates.
- **Data leaves the machine only as configured.** By default only outlines and summaries go to a remote provider; sending geometry or full results needs the user's permission.
- **Puzzle ships no model weights.**

### First implementation

- `puzzle.commands`: `Session` with `submit`, `propose`, `preview`, `accept`, `reject` and `undo`, plus a project lock. Commands are create, edit (dotted field paths), delete, add_library_material, copy_component, run_study and set_autonomy. `history.jsonl` stores the before-snapshots that undo uses. Autonomy settings live in `project.toml` under `[agent]`.
- `puzzle.errors`: `PuzzleError` codes; `Project.problems()` returns coded findings.
- `puzzle.results.query`: summary, stats (with locations), value_at, and integrals with shape-function weights.
- `puzzle.agent.mcp`: an MCP 2.x server over stdio (`puzzle mcp PROJECT --name ...`). Its tools are outline, get_object, schema, problems, materials_library, capabilities, check_study, submit, propose, preview, undo, history, proposals, autonomy, runs, result_summary, result_stats, result_value_at, result_integral and render.

### Built-in agent (2026-10-20)

- `puzzle.agent.agent.Agent` calls the MCP server's tools in-process (one tool set for inside and outside agents) as `Actor("agent", "built-in")`; the outside-MCP setting does not switch it off. Tool output is cut at 8,000 characters and a question gets at most 16 tool rounds, for small models.
- Providers (`puzzle.agent.providers`): Claude through `anthropic` (default `claude-sonnet-5-5`, prompt caching on the system prompt and tools), any OpenAI-compatible endpoint through the standard library (Ollama, llama.cpp, LM Studio, vLLM; local when the host is localhost), and a scripted provider for tests and evaluations. The choice is a user setting, `<user data dir>/agent.toml` or `PUZZLE_AGENT_*` variables, never a project file. API keys come from the environment, else the operating system's keychain (`keyring`: macOS Keychain, Windows Credential Locker, Secret Service), stored by `puzzle agent-key set` from a hidden prompt or the agent panel's API key button.
- Rendered images go only to local providers unless `send_images = true`.
- The GUI's agent panel shows the conversation and its tool calls, lists open proposals to accept or reject, and sets the autonomy level ("act" asks twice). `puzzle ask PROJECT "question"` is the same agent in a terminal.
- Evaluation suite (`puzzle.agent.evals`, `scripts/agent_eval.py`): seven tasks on fresh copies of the cantilever example, each checked in the project or against solver output: describe the studies, change a load, refine the mesh, add a library material, find and fix a misspelt selection, respect the advise level, run a study and report its peak displacement (2%). Its own tests show a scripted correct agent passes and a do-nothing agent fails.
- Measurements (2026-10-20, `scripts/agent_eval.py --tasks all`, CalculiX native). Thirteen tasks: the seven above plus a new load case (copy a component, change its load, add a study, keep the original), diagnosing a study that will not run (the new `unconstrained` problem: no support), replacing a force with a 2 MPa pressure, swapping boundary temperatures, reporting the first natural frequency (solver: 209.0 Hz), and a two-mesh convergence check.

  | Model | Passed | Tool calls | Tokens in/out | Time (s) |
  | --- | --- | --- | --- | --- |
  | claude-sonnet-5-5 | 13/13 | 84 | 232,616/8,990 | 256 |
  | claude-haiku-5-5 | 13/13 | 87 | 224,551/13,266 | 261 |

  Before `submit` took a list, Haiku failed the new load case: it put the three related commands in `propose` (the only multi-command tool then), so they waited for review at "act_in_budget". A tool-design gap, not a modelling one; `submit` now applies a list of edits all or none (one history entry, one undo), and the instructions say to propose only what the user did not ask for.

  Also found by the suite: a static solve with no support ran in CalculiX and returned ~9,000 km of displacement. Such studies now report the problem `unconstrained` and are refused before running (free modal analysis is still allowed).
- Still to do: local models, tasks that separate Haiku from smaller models, per-change acceptance in the GUI, streaming replies.

## Consequences

- One mechanism gives undo, provenance, scripting and agent control. The GUI is built on it rather than retrofitted.
- The work the agent is designed for:
  - setup from a description;
  - pre-run checks (units, rigid-body modes, mesh quality, contact gaps);
  - solver choice from the capability matrix;
  - solver-log diagnosis;
  - convergence studies and shoot-outs;
  - bulk selection repair (ADR 0014);
  - Code_Aster and Code_Saturne terminology in English;
  - FEniCSx custom PDEs;
  - reports from the project log.
- Building first:
  1. the command layer with log and undo;
  2. published schemas and error codes;
  3. the results query API;
  4. the MCP server.

  Later: the GUI chat panel, change-set review, provider and autonomy settings, the evaluation suite.
- To watch: commands must stay fine-grained enough to review, yet coarse enough that a small model can use them; the evaluation suite is the check.

## Alternatives considered

- **An agent that edits project files directly:** bypasses validation, undo and provenance.
- **A separate agent API beside the GUI's code paths:** two ways to change the model drift apart.
- **One hosted provider only:** fails offline and confidential work, and the lightest-local-model goal.
- **Act without sign-off:** too easy to enable by accident for unbudgeted compute and unreviewed changes.
