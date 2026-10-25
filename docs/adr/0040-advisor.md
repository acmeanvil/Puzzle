# 0040. An Advisor: the next step, from the project's state

- Status: Accepted (2026-10-25, the maintainer); built (2026-10-25)
- Date: 2026-10-23

## Context

Newcomers to FEA tools ask "what do I do next?". Salome-Meca's reviews list the missing overview as a top complaint. PrePoMax's optional Advisor panel (Help → Advisor) walks through a nine-step workflow, and users recommend it as "like having a tutor" (review item 15). Puzzle already knows much of what is missing:

- `Project.problems()` (unknown selections, unconstrained studies, incomplete materials);
- material completeness;
- tree states (ADR 0033).

## Decision

1. **An Advisor dock,** collapsed by default (ADR 0031), lists the workflow for the selected component and study:
   1. geometry;
   2. selections;
   3. materials assigned;
   4. mesh;
   5. physics and its supports and loads;
   6. study and steps;
   7. run;
   8. results.

   Each line has a state (done, needed, optional) and one action button that goes there ("Assign a material…", "Add a fixed support…").
2. **It is computed, not scripted.** The steps come from the project's state and the physics in use: a flow study asks for an inlet and an outlet, a structural one for a support. Each comes with its reason from the same problem codes the Problems tab shows. It never blocks: experts can ignore it.
3. **The text is short and comes from one table** shared with ADR 0036's descriptions and with the agent's outline, so the Advisor, the tooltips and the agent say the same thing.
4. **A first-run choice:** on a new project, the Advisor opens, and the cantilever example is offered as a guided tour (open it, mesh it, run it, look at the result, each step pointing at its button).

## Consequences

- New users get a path without a wizard, and the same states help agents and the tree.
- Each physics needs its "what's required" list, which is mostly the same knowledge as the `unconstrained` and `incomplete_material` checks, written once.
- The Advisor must stay quiet for experts: it is collapsed after the first project, and remembers being closed.

## Built (2026-10-25)

- **The advice** (`puzzle/advisor.py`): `advise(project, runs, component, study, meshed)` returns the steps (geometry, selections, materials, mesh, physics, supports and loads, study, run, results; a component step first when there is none) as `Advice` (state done, needed or optional; a short reason; an action key and a button label). `next_step` is the first needed one.
  - The words are one table, `TEXT`; what each physics needs before a solve is `NEEDS` (structural: a support and a load, except for steps that don't need them, such as a free modal study; heat: a temperature or convection for steady; flow: an inlet and an outlet; electrostatics: a potential and a ground; magnetics: a coil current; custom PDEs: a Dirichlet condition).
  - The study step reports `Project.problems()` and every solver's `check` for the study, so the Advisor, the Problems tab and a refused run say the same; the run step follows the tree states (ADR 0033).
- **The panel** (`gui/advisor_panel.py`): a dock on the right, hidden by default, in Window → Advisor (⌘3) and Help → Advisor; it follows the tree's selection and every project change. Each step shows ✓, ● or ○, its reason and, unless done, one button that does what the menus do (Import geometry…, Pick faces…, Assign a material…, Add fixed support…, New study…, Run, Show results…).
- **First run:** a new project opens the Advisor while Settings > Interface > "Open the Advisor for new projects" is on; closing the Advisor turns it off. An empty project offers "Try the cantilever example", a fresh copy in the user's data folder, opened, where the Advisor then points at Mesh, Run and the results.
- **Twins:** MCP `next_steps(component?, study?)` and `puzzle advise PROJECT [--component --study]`.
- **Checks:** `tests/test_advisor.py` (an empty project, a model built step by step, a free modal study needing no support, the panel and its buttons, closing it once, the agent and the CLI).
- **Not yet:** the guided tour pointing at each button in turn (the Advisor's own next step does that for the example today); tooltips and the form descriptions (ADR 0036) drawing on the same table.

## Alternatives considered

- A wizard per analysis type: it fixes the order and hides the tree, and Puzzle's model is a tree on purpose (ADR 0009).
- Leave guidance to the AI agent: it is useful, but not everyone runs an agent, and the Advisor works offline and instantly.
