"""Solver settings… (ADR 0023): one form for a study's settings.

- **Shared settings** (`adapters.settings.SHARED`), written on every solve step and
  translated by each solver: blank or "each solver's default" leaves a setting unset,
  so the solver keeps its own default;
- **each solver's own**, for the study's solver and its shoot-out solvers, named in
  English with the solver's own word beside it; power-user settings only at that
  level (Settings > Solvers);
- at the power-user level, **the expert pass-through**, one box per solver, checked by
  the adapter before OK.

OK is one Edit command (one undo).
"""

from __future__ import annotations

from puzzle.adapters.settings import SHARED
from puzzle.gui.quick_form import Ask

UNSET = ""


def parse_expert(text: str) -> dict[str, str]:
    """A pass-through box's entries: `PATH = value` lines, or `[place]` headers each
    followed by its card lines (CalculiX)."""
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if any(line.startswith("[") for line in lines):
        out: dict[str, list[str]] = {}
        place = None
        for line in lines:
            if line.startswith("[") and line.endswith("]"):
                place = line[1:-1].strip()
                out.setdefault(place, [])
            elif place is not None:
                out[place].append(line)
        return {k: "\n".join(v) for k, v in out.items()}
    pairs = {}
    for line in lines:
        path, _, value = line.partition("=")
        pairs[path.strip()] = value.strip()
    return pairs


def show_expert(entries: dict[str, str]) -> str:
    if any("\n" in v or v.startswith("*") for v in entries.values()):
        return "\n".join(f"[{k}]\n{v}" for k, v in entries.items())
    return "\n".join(f"{k} = {v}" for k, v in entries.items())


def _field(setting, key: str, label: str, current) -> Ask:
    if setting.kind == "choice":
        default = dict((v, t) for t, v in setting.choices).get(setting.default, setting.default)
        choices = [(f"Default ({default})", UNSET), *setting.choices]
        return Ask(key, label, choices=choices, default=current if current is not None else UNSET,
                   help=setting.description)  # fmt: skip
    if setting.kind == "bool":
        choices = [(f"Default ({'yes' if setting.default else 'no'})", UNSET),
                   ("Yes", True), ("No", False)]  # fmt: skip
        return Ask(key, label, choices=choices, default=current if current is not None else UNSET,
                   help=setting.description)  # fmt: skip
    unit = f" ({setting.unit})" if setting.unit else ""
    return Ask(key, f"{label}{unit}", kind="text", default="" if current is None else current,
               help=f"{setting.description} Blank: the default, {setting.default:g}.")  # fmt: skip


def edit_solver_settings(window, study):
    from puzzle.adapters import available_adapters
    from puzzle.adapters.common import InputError
    from puzzle.commands.model import Edit, EditOp
    from puzzle.gui.choices import solver_label
    from puzzle.settings import current

    power = current().get("solvers.level") == "power"
    adapters = {name: cls() for name, cls in available_adapters().items()}
    solvers = [s for s in study.all_solvers() if s in adapters]
    steps = study.solve_steps()
    given = steps[0].settings if steps else {}
    asks = [
        _field(s, f"shared:{s.key}", f"All solvers: {s.label}", given.get(s.key)) for s in SHARED
    ]
    schemas = {}
    for solver in solvers:
        name = solver_label(solver)
        own = study.solver_settings.get(solver, {})
        for s in adapters[solver].settings_schema():
            if s.power and not power and s.key not in own:
                continue
            schemas[f"{solver}:{s.key}"] = s
            label = f"{name}: {s.label}" + (f" ({s.native})" if s.native else "")
            asks.append(_field(s, f"{solver}:{s.key}", label, own.get(s.key)))
        if power and adapters[solver].expert_help():
            asks.append(Ask(f"expert:{solver}", f"{name}: expert pass-through", kind="multiline",
                            default=show_expert(study.expert_settings.get(solver, {})),
                            help=adapters[solver].expert_help()))  # fmt: skip

    def value(key: str, text):
        setting = schemas.get(key) or next(s for s in SHARED if f"shared:{s.key}" == key)
        if text in (UNSET, None):
            return None
        return setting.check(text)

    def check(v) -> str | None:
        try:
            for key, text in v.items():
                if key.startswith("expert:"):
                    solver = key.split(":", 1)[1]
                    problems = adapters[solver].check_expert(parse_expert(text))
                    if problems:
                        return problems[0]
                else:
                    value(key, text)
        except InputError as exc:
            return str(exc)
        return None

    intro = (
        "Blank or Default leaves a setting to the solver. Settings change how a solver "
        "works; a shoot-out lists each solver's settings beside its results."
        + ("" if power else " Settings > Solvers shows every option and the expert pass-through.")
    )
    got = window.ask_form(f"Solver settings: {study.name}", asks, intro=intro, check=check)
    if got is None:
        return None
    ops = []
    solver_settings = {s: dict(study.solver_settings.get(s, {})) for s in solvers}
    expert = {s: dict(study.expert_settings.get(s, {})) for s in solvers}
    for key, text in got.items():
        where, name = key.split(":", 1)
        if where == "expert":
            expert[name] = parse_expert(text)
        elif where == "shared":
            chosen = value(key, text)
            for i, step in enumerate(study.steps):
                if step not in steps:
                    continue
                if chosen is None:
                    if name in step.settings:
                        ops.append(EditOp(op="remove", path=f"steps.{i}.settings.{name}"))
                else:
                    ops.append(EditOp(op="set", path=f"steps.{i}.settings.{name}", value=chosen))
        else:
            chosen = value(key, text)
            if chosen is None:
                solver_settings[where].pop(name, None)
            else:
                solver_settings[where][name] = chosen
    ops.append(EditOp(op="set", path="solver_settings",
                      value={k: v for k, v in solver_settings.items() if v}))  # fmt: skip
    ops.append(EditOp(op="set", path="expert_settings",
                      value={k: v for k, v in expert.items() if v}))  # fmt: skip
    return window.submit(Edit(object=str(study.id), ops=ops))


# --- presets -------------------------------------------------------------------------


def save_preset(window, study):
    """The study's settings (shared, each solver's own, pass-through) as a preset."""
    from puzzle.adapters.settings import SHARED_KEYS
    from puzzle.commands.model import Create

    name = window.ask_text("Save as preset", "Preset name:", f"{study.name} settings")
    if not name:
        return None
    steps = study.solve_steps()
    shared = {k: v for k, v in (steps[0].settings if steps else {}).items() if k in SHARED_KEYS}
    data = {
        "name": name,
        "shared": shared,
        "solver_settings": study.solver_settings,
        "expert_settings": study.expert_settings,
    }
    return window.submit(Create(kind="preset", data=data))


def apply_preset(window, study, preset):
    """The preset on the study, one Edit: its shared settings on every solve step (the
    others removed), and for each solver it names, its own block and pass-through."""
    from puzzle.adapters.settings import SHARED_KEYS
    from puzzle.commands.model import Edit, EditOp

    ops = []
    for i, step in enumerate(study.steps):
        if step not in study.solve_steps():
            continue
        for key in SHARED_KEYS:
            if key in preset.shared:
                ops.append(EditOp(op="set", path=f"steps.{i}.settings.{key}",
                                  value=preset.shared[key]))  # fmt: skip
            elif key in step.settings:
                ops.append(EditOp(op="remove", path=f"steps.{i}.settings.{key}"))
    own = {**study.solver_settings, **preset.solver_settings}
    expert = {**study.expert_settings, **preset.expert_settings}
    for solver in preset.solver_settings.keys() | preset.expert_settings.keys():
        own[solver] = preset.solver_settings.get(solver, {})
        expert[solver] = preset.expert_settings.get(solver, {})
    ops.append(EditOp(op="set", path="solver_settings", value={k: v for k, v in own.items() if v}))
    ops.append(
        EditOp(op="set", path="expert_settings", value={k: v for k, v in expert.items() if v})
    )
    return window.submit(Edit(object=str(study.id), ops=ops))
