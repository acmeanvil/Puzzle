---
name: local-models
description: answering solver and error questions from Puzzle's domain pack (knowledge), and helping the user pick or judge a local model by measurement
adr: 0022
---
Read this when the user asks why a solver did something, what an error or a French
Code_Aster message means, or what a solver supports. Read it too when they ask which
local model to use, or why the agent struggles on their machine.

## Answering from the domain pack

`knowledge(query, limit)` searches what ships with Puzzle:
- the skills;
- curated solver notes (what Puzzle learned while building each adapter);
- each solver's capabilities as they are now;
- Code_Aster's French messages in English;
- the ADRs, when Puzzle runs from source.

Each hit has `cite` ("note code_aster § Contacts"), the `text` and, for a manual
summary, a `link`. A long passage comes as its first paragraphs with `truncated` set
and a note saying how much is left; for a skill, `skill_read(name)` gives it whole.
Read it whole before following steps that the excerpt cuts off.

1. Search with the words the user or the log used, the solver's name included.
   French messages are searched as they are.
2. Answer from the passages and cite them, e.g. "(note code_aster § Contacts)". When
   none answers, say so. Don't guess at a solver's behaviour.
3. For what a study can do on a solver, `check_study` and `capabilities` are the
   authority. The pack says why.

## Choosing a local model

Puzzle doesn't guess which local model is good enough: it measures. The user runs
`puzzle agent pick` (or the agent panel's Pick a local model…). It:
1. lists the models the endpoint already has (it downloads nothing);
2. leaves out those that don't fit the computer's memory, and embedding models;
3. runs the evaluation tasks on the rest;
4. ranks them by the core families passed (read, edit, batch, recover, refuse), then
   all tasks, then speed, then memory;
5. recommends the smallest at the best score.

Results are kept per model and suite version, so a second pick measures only new
models (or a model whose record lacks tasks this machine now runs, such as those
needing CalculiX). When the endpoint fails mid-measure, nothing is kept for that model
and the pick says so: the endpoint failed, not the model. Cancel stops after the task
under way. When no local model passes the core tasks, the advice is Claude.

You don't run the pick yourself: it changes the user's settings. When a task is beyond
the current model (it fails the same way twice), say so and suggest the user run a pick
or switch provider in Settings > Agent. Never send work to another provider yourself.

## Mistakes to avoid

- Citing a passage that doesn't say what you claim. Quote its words if unsure.
- Treating an ADR's "Decision (proposed)" as built. Its "Built" section says what is.
- Raising your own autonomy, or changing the agent settings. These are the user's.
