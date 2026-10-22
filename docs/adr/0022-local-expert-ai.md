# 0022. Choose the "local expert" model by measurement on the user's machine

- Status: Proposed
- Date: 2026-10-22

## Context

The built-in agent (ADR 0015) runs on Claude first, or on any OpenAI-compatible local endpoint (Ollama, llama.cpp, LM Studio). The aim is the lightest local model that is still competent at Puzzle's work.

That work is reading the model outline and making correct, small, validated edits through the command layer: physics features, mesh controls, sketches, studies, and interpreting results. `agent/evals.py` holds 13 tasks (`TASKS`, plus `HARD_TASKS`); Claude Sonnet 5.5 and Haiku 5.5 both pass all 13. Which local model is "good enough" depends on the user's hardware (RAM or VRAM, Apple unified memory) and changes as new open models appear. No default can stay right for long.

"Expert" also means domain knowledge: what a convection coefficient is, why a Code_Aster contact fails to converge, what an unconstrained model means. The solver manuals hold much of it, and some are in French (Code_Aster, Code_Saturne).

## Decision (proposed)

1. **Measure, don't guess.** `puzzle agent pick` (and a GUI button in the agent panel):
   - finds the local models the endpoints serve (`/api/tags` for Ollama, `/v1/models` otherwise);
   - filters them by what fits the machine: model size and quantisation against free RAM or VRAM, with headroom for Puzzle and a solver;
   - runs the evaluation suite on each candidate, in a scratch copy of an example project;
   - ranks them by pass rate, then median latency per task, then memory;
   - recommends the smallest model at the best pass rate, explaining the trade-off in words.

   The choice is saved in `agent.toml`, with the date and the suite's version.
2. **The evaluation suite is the definition of competence.** It grows by task family:
   - read the model;
   - make an edit;
   - make a batched edit;
   - build a sketch (checked with `sketch_check`);
   - set up a mesh;
   - recover from a refused command;
   - interpret a result;
   - refuse an unsafe request.

   Every family has graded tasks (easy, hard). A local expert must pass the core families; others are reported. Suite results are kept so a model update or a new model can be compared like for like.
3. **Knowledge by retrieval, not fine-tuning, at first.** A domain pack is searched locally and cited in answers. It holds Puzzle's docs and ADRs, the capability matrices, and curated solver notes (the CLAUDE.md gotchas, keyword references). French manual excerpts carry English summaries, in line with the GUI translation layer. A small embedding model (run by Ollama) indexes it, and the agent gets a `knowledge` tool. Fine-tuning is considered only if retrieval plateaus on the suite.
4. **Routing by difficulty.** A task the local model fails can be offered to the remote provider. This is never automatic, because remote providers get outlines only (ADR 0015). The routing records which families stay local, so `pick` can recommend per family later.
5. **Provenance in the record.** Every agent entry in `history.jsonl` already names the actor. It adds the model and the suite version, so a project shows which model made which change.

## Consequences

- "Which model should I use?" becomes a ten-minute measurement on the user's own machine, repeatable when models change.
- The suite becomes a maintained asset: tasks need clear pass checks (validated commands, measured outcomes), like the solver benchmarks.
- Running candidates costs time and disk (models of several GB). `pick` downloads nothing without asking, and can test only models already present.

## Open questions

- Which model families to suggest downloading when none fit? A curated list per hardware class, revisited at each release, is one option.
- Should the domain pack ship with Puzzle (licences of excerpts), or be built locally from manuals the user has?
- Is per-task-family routing worth its complexity before the suite shows local models failing some families consistently?

## Alternatives considered

- A fixed default local model: simple, but wrong on half the machines and stale within months.
- Leaderboard scores (general benchmarks): cheap, but they don't measure tool use on Puzzle's command layer.
- Fine-tuning a Puzzle model: possible later; costly to keep current with the schema, and it needs the suite anyway to prove it.
