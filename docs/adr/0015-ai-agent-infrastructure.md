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
