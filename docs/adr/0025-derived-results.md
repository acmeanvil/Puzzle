# 0025. Derived results: solution combinations and custom equations

- Status: Accepted
- Date: 2026-10-22

## Context

The maintainer asked for two features familiar from Ansys Mechanical:

- **Solution Combination:** results of several load cases combined, either linearly with coefficients, or as an envelope, SRSS or range (2026-10-22).
- **User-defined results:** a custom equation applied to the result quantities a solver gives (2026-10-22).

Fatigue (ADR 0026) needs the range and the mean of a stress cycle, which are combinations. Results are stored per run (ADR 0008); every quantity a run can show is listed by `post.quantities`.

## Decision

1. **Derived results are project objects, evaluated on demand.** A `ResultDefinition` (kind `result`) names its inputs and how to combine them; it is not a stored result. It is evaluated from the stored runs when shown, queried or exported, and is cached in memory by its inputs' run ids. Editing it is a command (undoable); its value always follows the runs it reads.
2. **Combinations.** The inputs are cases: a study (its latest run, or a sweep point) and a step (an increment, a time, or a mode). Each has a coefficient. Every case must be on the same mesh (or is mapped onto the first case's mesh, as transfers are). The methods are:

   | Method | Value at each node |
   | --- | --- |
   | Linear | sum of coefficient × field, combining vector and tensor fields component-wise, so von Mises and the principal stresses are computed after combining, as Ansys does. Valid for linear analyses; a nonlinear source is warned about. |
   | Envelope | the maximum or minimum over the cases of a chosen quantity, and which case gave it |
   | SRSS | the square root of the sum of the squares over the cases, component-wise |
   | Range | the difference between two cases (or the maximum minus the minimum over several), with the mean, component-wise for tensors: the stress range and mean stress fatigue needs |

   A combination's output fields carry the same names as its inputs, so every quantity in `post.quantities` (total deformation, von Mises, principal stresses, ...) is available on it.
3. **Custom equations.** An expression over the quantities of a run or a combination, evaluated per node:
   - **Names:** they come from the result list, e.g. `von_mises`, `deformation_y`, `temperature`, `plastic_strain_equivalent`, and `stress_xx` for stress components.
   - **Operators and functions:** `+ - * / **`, `abs`, `sqrt`, `exp`, `log`, `min`, `max`, `where(cond, a, b)`, plus the constants `pi` and `e`.
   - **Inputs:** project parameters by name.

   The result has a display unit kind when one is given, else none. Expressions are parsed with Python's `ast` to a small, safe set of nodes: names, numbers, those operators and functions, nothing else. They evaluate on numpy arrays. A typo is reported by name ("no result 'von_mise'; did you mean von_mises?").
4. **In the GUI and for agents.** Derived results sit under Results in the tree. Right-click a run or a study for "New combination…" or "New equation…". Their dialogs list the cases and the quantities by name, and show the result in the view like any result. MCP tools evaluate them (statistics, a value at a point) as for runs.

## Consequences

- Load-case superposition, worst-case envelopes and fatigue ranges are a few clicks, and need no re-solve.
- Derived results cost evaluation time when shown, not storage. Large models may want them cached on disk later.
- Linear combination of nonlinear results is physically wrong. Puzzle warns, as Ansys does, but doesn't refuse.

## Alternatives considered

- Store derived results as new runs: simple to show, but stale when an input is rerun, and a second copy of large data.
- Python scripts as equations: powerful, but unsafe to run from a shared project file; the restricted expression covers the asked-for use.
