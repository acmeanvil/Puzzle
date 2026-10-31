# 0057. A skill for agents with every finished feature

- Status: Accepted (2026-10-30, at the maintainer's request)
- Date: 2026-10-30

## Context

Puzzle's built-in agent (ADR 0015) runs on Claude or on a local model (Ollama, llama.cpp, LM Studio), with the same MCP tools outside agents get. Its prompt says how to use the tools in general; nothing tells it how a particular feature is meant to be used: that a bolt takes its size from a bolt type, which joint style bears where, that CalculiX refuses an I-beam's table section. A capable model works this out from `schema` and errors; a small local one mostly does not, and either spends tool rounds and context on it. The maintainer asked that, as each ADR is completed, what an agent needs is added to a skills file, so the local AI can use the feature once configured.

## Decision

1. **Skills** are Markdown files in `src/puzzle/agent/skills/` (shipped with Puzzle): a header with `name`, `description` (one line: when to read it) and `adr`, then the steps with the tools and their arguments, the units, and the mistakes to avoid. Short: a page, not a manual.
2. **The built-in agent's prompt lists the catalogue only** (each skill's name and one line), so a small model's context holds what exists, not every body; it reads a skill with `skill_read` before a task in its area. **MCP** gives outside agents the same: `skills_list` and `skill_read`, and the server's instructions say to read the skill first.
3. **Every ADR whose finished work an agent uses gets a skill as part of finishing it**, or has its skill updated, in the same change as the code. `tests/test_agent_skills.py` checks that each skill parses, names a real ADR and only tools the MCP server has, and that the ADRs listed there (`WITH_SKILLS`) have one. Where a skill needs a tool that doesn't exist, the tool is added (ADR 0050's `bolt_results`, ADR 0055's `sections_find`).
4. **First skills:** `bolts` (ADR 0050) and `beam-sections` (ADR 0055). Earlier finished ADRs get theirs as they are next touched, the most used first: contacts (0039), result tools and probes (0042), solver settings (0023), materials (0020), meshing (0019), sweeps (0018), derived results and fatigue (0025, 0026).

## Consequences

- A local model can use a new feature the day it lands, from a page written for it, at the cost of one tool call when the task needs it.
- Skills can drift from the code; the test catches renamed tools and missing ADRs, not wrong advice, so a skill is reviewed with its feature.
- The prompt grows by one line per skill; past a few dozen they would be grouped.

## Alternatives considered

- **Everything in the system prompt:** simplest, but every request then carries every feature's notes; a small model's context fills and its attention spreads.
- **Rely on `schema` and error messages:** works for strong models, slowly; small ones loop on refusals.
- **Claude Code skills in `.claude/skills/`:** serve Claude Code sessions only, not Puzzle's own agent or other MCP clients; the MCP tools serve all three.
