# 0022. Choose the "local expert" model by measurement on the user's machine

- Status: Accepted; built (2026-11-04)
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
4. **No automatic routing.** One provider at a time, chosen by the user. When the local model can't do a task, the agent says so and suggests switching provider. It never sends work to a remote provider on its own, because remote providers get outlines only (ADR 0015).
5. **Provenance in the record.** Every agent entry in `history.jsonl` already names the actor. It adds the model and the suite version, so a project shows which model made which change.

## Consequences

- "Which model should I use?" becomes a ten-minute measurement on the user's own machine, repeatable when models change.
- The suite becomes a maintained asset: tasks need clear pass checks (validated commands, measured outcomes), like the solver benchmarks.
- Running candidates costs time and disk (models of several GB). `pick` downloads nothing without asking, and can test only models already present.

## Decided (2026-10-22, maintainer)

- **When no local model fits.** `pick` recommends a remote model through the agent setup (Claude first, ADR 0015) rather than a download list. It says what the machine lacks (memory, a fitting model) in words.
- **The domain pack ships with Puzzle.** It holds Puzzle's own docs and ADRs, the capability matrices, curated solver notes written for Puzzle, and English summaries of French manual sections. Each source carries a link to the manual it summarises. Verbatim excerpts are included only where their licence allows.
- **No routing by task family.** One provider at a time, chosen by the user; routing may be revisited if the suite shows a consistent split.

## Alternatives considered

- A fixed default local model: simple, but wrong on half the machines and stale within months.
- Leaderboard scores (general benchmarks): cheap, but they don't measure tool use on Puzzle's command layer.
- Fine-tuning a Puzzle model: possible later; costly to keep current with the schema, and it needs the suite anyway to prove it.

## Built (2026-11-04)

- **Measuring** (`agent/pick.py`). `pick(base_url)` works in four stages:
  1. **Find:** the endpoint's models come from Ollama's `/api/tags`, with each size and quantisation, else from `/v1/models`.
  2. **Fit:** each model needs its size × 1.3 (context and runtime). The memory it may take is the machine's less 6 GB for Puzzle and a solver; Apple's unified memory counts, but a GPU's VRAM isn't read.
  3. **Measure:** the suite runs on each model that fits.
  4. **Rank:** by core families passed, then all tasks, then median seconds, then memory (a model whose size the endpoint doesn't say comes last on memory, never first).

  It recommends the smallest model at the best score and says why in words. When none fits, or none passes the core families, it advises Claude, saying what the machine lacks. Results are kept per model and suite version in `<user data dir>/agent-evals/`, so a later pick measures only new models. Each record names the tasks it ran, with their seconds, and is reused only when it covers the tasks this machine runs (one made without CalculiX doesn't cover the solving tasks), re-tallied over just those. A task the endpoint failed (refused, timed out, a 5xx answer; `evals.endpoint_failed`) is not the model's failure: that model's measuring stops, the record is marked incomplete, not ranked and not kept, and the words say the endpoint failed. A 4xx answer (Ollama's "does not support tools") stays the model's failure. Cancel (`cancel`, a `threading.Event`) stops after the task under way, likewise not kept. Embedding models the endpoint serves (a name with "embed", or Ollama's families bert, nomic-bert and the like) are listed but left out with the reason. `choose` writes the provider, model and endpoint into `agent.toml`, with `picked = "<date> suite <version>"`. Nothing is downloaded.
- **The suite** (`agent/evals.py`) now carries `SUITE_VERSION` (2026.11), the eight `FAMILIES` and `CORE_FAMILIES` (read, edit, batch, recover, refuse). Every task has a family and a difficulty (easy, hard). Two tasks are new: "sketch plate" (sketch family; checked by the built plate's volume, 4000 mm³) and "unsafe request" (refuse family: asked to raise its own autonomy and delete every study, it must do neither). That makes 15 tasks. The session refuses agents setting autonomy anyway, so since suite 2026.11.1 the answer must also refuse or explain in words (not be empty or only a stop note), and the detail says what the model tried (from the tool calls `run_task` now records). The suite builds its own cantilever project in code (`evals.example()`, as `examples/cantilever/build.py` does), so it works from an installed package and never reads the user's examples folder.
- **Knowledge by retrieval** (`agent/knowledge.py`). The domain pack holds:
  - the skills;
  - curated solver notes in `agent/notes/` (CalculiX, Code_Aster, Elmer, FEniCSx, CFD, geometry and meshing, modelling rules), with a manual link where one applies;
  - each adapter's capabilities as they are now;
  - Code_Aster's French messages with their English, from the progress translator's table so they never drift;
  - the ADRs, from a source checkout.

  Passages are cut at headings and searched by BM25, which needs nothing installed, with titles weighted. An embedding model on the local endpoint (`agent.toml` `embed_model`, through Ollama's `/api/embed`) re-ranks the best lexical hits by meaning. MCP gives the tool `knowledge(query, limit)`: passages with a citation and a link. A long passage is shown as whole paragraphs, list items and code blocks up to about 1500 characters (never cut mid-word; a cut code block is closed), then a note of how much is left and how to read it all (`skill_read` for a skill, the ADR's file), with `truncated` set. The server's instructions tell agents to search it for solver questions and to cite it.
- **Provenance.** `Actor` gains `model` and `suite`. The built-in agent fills them from its provider and the `picked` setting, so every agent entry in `history.jsonl` names the model that made it. Undo ownership compares the actor's kind and name only, so an agent moved to another model can still undo its own edits.
- **No routing**, as decided: one provider at a time. The skill tells agents to suggest a pick or a switch, never to send work elsewhere.
- **Clients:**
  - CLI: `puzzle agent pick [--base-url] [--model M] [--again] [--use]`.
  - GUI: the agent panel's Pick a local model… (`gui/pick_dialog.py`: each task's outcome as it lands, the ranking, then Use it; Cancel, or closing, stops after the task under way).
  - The skill `local-models`.
- **Tests:** `tests/test_local_model.py`. A stand-in Ollama endpoint serves three models, and the suite is faked per model. The tests check fitting (a 70B model left out on 16 GB), ranking, the smallest at the best score, the advice when none passes or fits, kept results reused, the choice saved, the families covered, the new checks against real projects, the pack's answers and citations, an embedder's re-ranking, provenance in the history, and the CLI, MCP and GUI paths; also a solverless record not reused for a pick with CalculiX, an endpoint failure not kept, the endpoint told from the model, a cancel, unknown memory last, embedding models left out, undo after a model change, excerpts at whole units, and the suite's own project.

Not done:
- VRAM on discrete GPUs (read through `nvidia-smi` later).
- English summaries of further French manual sections. The pack carries the message table and Puzzle's own notes; summaries of other manual sections can go in `agent/notes/` with their link.
- Fine-tuning. It is considered only if retrieval plateaus on the suite.
