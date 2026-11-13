"""Setting up and reading threaded contacts (ADR 0076 decision 6), for every client.

The MCP tools (`contact_thread`, `thread_forms`, `thread_results`), the GUI's Make it a
thread… form, Find contacts' suggestion and the Advisor's note all go through here, so
an agent and a person get the same checks and the same numbers. Nothing here edits the
project: `thread_command` returns the Edit a client submits through the Session.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from puzzle.errors import PuzzleError

# Contact types a thread may have (Thread's docstring); friction comes later (ADR 0076).
TYPES = ("bonded", "no_separation", "frictionless")
TYPE_NAMES = {"bonded": "Bonded (the bearing flank tied)",
              "no_separation": "No separation (both flanks tied)",
              "frictionless": "Frictionless (flanks bear, may open)"}  # fmt: skip
# The forms whose sizes are in the bolt tables (`bolts.SIZES`); others take a major
# diameter and a pitch.
SIZED = {"iso": ("iso_coarse", "iso_fine"), "un": ("unc", "unf")}
BEARING_NAMES = {"auto": "Automatic: the flank the pull loads",
                 "load": "The form's load flank angle",
                 "other": "The form's other flank angle"}  # fmt: skip
COMPLIANCE_NAMES = {"teeth": "The teeth's (Yamamoto)", "rigid": "Rigid (tied)"}
# ISO 724: the screw's minor diameter d3 = d - 1.226869 P (the radius to model at).
D3 = 1.226869
NEAR = 0.04  # a cylinder's diameter within 4 % of a size's d, d2, D1 or d3 suggests it


def form_choices() -> list[tuple[str, str]]:
    """(readable name, key) of every form, as the GUI's dropdown shows them."""
    from puzzle.threads import FORMS

    out = [(f"{f.name} ({f.standard})" if f.standard != "-" else f.name, k)
           for k, f in FORMS.items()]  # fmt: skip
    return out + [("Custom (major, pitch and flank angles given)", "custom")]


def form_name(key: str) -> str:
    return dict((v, n) for n, v in form_choices()).get(key, key)


def sizes_for(form: str) -> list[str]:
    """The standard sizes a form takes by name (ISO and UN only)."""
    from puzzle.bolts import sizes

    return [z.name for standard in SIZED.get(form, ()) for z in sizes(standard)]


def forms_info() -> list[dict[str, Any]]:
    """Every form: name, standard, flank angles (load, other; degrees from the radial
    plane), taper (diameter per length) and how its size is given."""
    from puzzle.bolts import STANDARDS
    from puzzle.threads import FORMS

    return [
        {
            "form": k,
            "name": f.name,
            "standard": f.standard,
            "flank_angles_deg": list(f.angles),
            "taper": f.taper,
            "sizes": [STANDARDS[s] for s in SIZED.get(k, ())]
            or "give major (and pitch): no size table",
        }
        for k, f in FORMS.items()
    ] + [{"form": "custom", "name": "Custom", "standard": "-",
          "flank_angles_deg": None, "taper": 0.0,
          "sizes": "give major, pitch and flank_angles"}]  # fmt: skip


def suggest_size(diameter: float) -> dict[str, Any] | None:
    """The ISO coarse size a cylinder of `diameter` (m) most likely stands for: the one
    whose major d, pitch diameter d2, nut minor D1 or screw minor d3 is nearest, within
    4 %. A suggestion only: M14 at its major and M16 at its pitch diameter are close."""
    from puzzle.bolts import sizes
    from puzzle.threads import profile

    best = None
    for z in sizes("iso_coarse"):
        p = profile("iso", z.diameter, z.pitch)
        diameters = {"major": z.diameter, "pitch_diameter": p["pitch_diameter"],
                     "minor": p["minor"], "screw_minor": z.diameter - D3 * z.pitch}  # fmt: skip
        for what, d in diameters.items():
            off = abs(diameter - d) / d
            if off <= NEAR and (best is None or off < best[0]):
                best = (off, z.name, what)
    if best is None:
        return None
    return {"form": "iso", "size": best[1], "matches": best[2], "off": best[0]}


def make_thread(**values) -> Any:
    """A `Thread` from plain values (quantities as {"value", "unit"}), or a PuzzleError
    saying what is wrong, with the sizes a form takes."""
    from pydantic import ValidationError

    from puzzle import threads
    from puzzle.model.schema import Thread

    data = {k: v for k, v in values.items() if v is not None}
    try:
        thread = Thread(**data)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise PuzzleError(f"thread: {first['msg']}", code="invalid",
                          details={"field": ".".join(map(str, first["loc"])),
                                   "forms": [k for _, k in form_choices()]}) from None  # fmt: skip
    try:
        threads.spec(thread)
    except ValueError as exc:
        names = sizes_for(thread.form)
        raise PuzzleError(str(exc), code="invalid",
                          details={"sizes": names} if names else {
                              "hint": "this form has no size table: give major and pitch"}
                          ) from None  # fmt: skip
    return thread


def describe(thread) -> dict[str, Any]:
    """A thread's resolved numbers (SI: m, degrees)."""
    from puzzle import threads

    s = threads.spec(thread)
    return {
        "form": thread.form,
        "form_name": form_name(thread.form),
        "size": thread.size,
        "major_m": s.major,
        "pitch_m": s.pitch,
        "lead_m": s.lead,
        "pitch_diameter_m": s.pitch_diameter,
        "minor_m": s.minor,
        "screw_minor_m": s.major - D3 * s.pitch if thread.form in ("iso", "un") else None,
        "flank_angles_deg": list(s.angles),
        "hand": thread.hand,
        "starts": thread.starts,
        "taper": s.taper,
        "clearance_m": s.clearance,
    }


def contact_index(comp, name: str) -> int:
    for i, c in enumerate(comp.contacts):
        if c.name == name:
            return i
    raise PuzzleError(f"no contact '{name}' in {comp.name}", code="not_found",
                      details={"contacts": [c.name for c in comp.contacts]})  # fmt: skip


def thread_command(comp, contact: str, thread, type: str | None = None):
    """The Edit setting (or, with `thread` None, clearing) a contact's thread; a type
    change with it. A conformal bond becomes automatic, since a thread is meshed apart."""
    from puzzle.commands.model import Edit, EditOp

    i = contact_index(comp, contact)
    c = comp.contacts[i]
    kind = type or c.type
    if thread is not None and kind not in TYPES:
        raise PuzzleError(
            f"a thread is {', '.join(TYPES)} (not {kind}): frictional threads come later",
            code="invalid", details={"types": list(TYPES)})  # fmt: skip
    ops = []
    if type and type != c.type:
        ops.append(EditOp(op="set", path=f"contacts.{i}.type", value=type))
        if type != "frictional" and c.friction is not None:
            ops.append(EditOp(op="set", path=f"contacts.{i}.friction", value=None))
    if thread is not None and c.meshing == "conformal":
        ops.append(EditOp(op="set", path=f"contacts.{i}.meshing", value=None))
    if thread is not None and c.interface != "as_is":
        ops.append(EditOp(op="set", path=f"contacts.{i}.interface", value="as_is"))
        ops.append(EditOp(op="set", path=f"contacts.{i}.offset", value=None))
    value = thread.model_dump(mode="json") if thread is not None else None
    ops.append(EditOp(op="set", path=f"contacts.{i}.thread", value=value))
    return Edit(object=str(comp.id), ops=ops)


def writers(project, comp, kind: str) -> list[str]:
    """The component's study solvers that write a threaded `kind` contact (ADR 0076)."""
    from puzzle.adapters import available_adapters
    from puzzle.gui.choices import solver_label
    from puzzle.model.schema import Study

    adapters = available_adapters()
    solvers = sorted({s.solver for s in project.of_type(Study) if s.component == comp.id})
    return [solver_label(s) for s in solvers
            if s in adapters and kind in adapters[s].capabilities.threads]  # fmt: skip


def notes(project, comp, thread, kind: str) -> list[str]:
    """What to know before solving, said once here for every client."""
    from puzzle.model.schema import Study

    out = []
    studies = {s.solver for s in project.of_type(Study) if s.component == comp.id}
    can = writers(project, comp, kind)
    if studies and not can:
        out.append(f"none of this component's solvers write a threaded {kind} contact yet; "
                   "CalculiX does (bonded, no_separation, frictionless)")  # fmt: skip
    if kind == "frictionless":
        out.append("frictionless flanks bear one way only: the step solves nonlinear")
    if kind == "bonded":
        out.append("bonded ties the bearing flank's normal only (the flank slides, as "
                   "real threads); no_separation ties both flanks")  # fmt: skip
    if thread.compliance == "rigid":
        out.append("rigid threads are equations: no thread_force, thread_load or "
                   "thread_wedge results")  # fmt: skip
    out.append("model both cylinders at one radius, best the screw's minor d3/2; radii "
               "more than 2 % of d apart are refused (thread_radii)")  # fmt: skip
    return out


# --- results ---------------------------------------------------------------------------


def results(project, mesh, res, contact: str | None = None, step: int | None = None) -> dict:
    """Each thread's axial load and share, the wedge (total radial force) and the peak
    flank pressure, per threaded contact, in a run's step with thread results."""
    steps = [s for s in res.steps if "thread_load" in s.fields]
    if not steps:
        raise PuzzleError(
            "this run has no thread results: no threaded contact, or only rigid ones "
            "(equations carry no spring force)", code="not_found")  # fmt: skip
    k = len(steps) if step is None else step
    if not 1 <= k <= len(steps):
        raise PuzzleError(f"step {k}: the run has {len(steps)} steps with thread results",
                          code="not_found")  # fmt: skip
    chosen = steps[k - 1]
    load = chosen.fields["thread_load"]
    wedge = chosen.fields.get("thread_wedge")
    pressure = chosen.fields.get("thread_pressure")
    comps = [c for c in _components(project) if c.contacts]
    found = {c.name: c for comp in comps for c in comp.contacts if c.thread is not None}
    names = list(wedge.components) if wedge is not None else sorted(
        {n.rsplit(": ", 1)[0] for n in load.components})  # fmt: skip
    if contact is not None:
        if contact not in names:
            raise PuzzleError(f"no thread results for '{contact}'", code="not_found",
                              details={"threads": names})  # fmt: skip
        names = [contact]
    out = []
    for name in names:
        loads = [float(v) for c, v in zip(load.components, load.values[0], strict=True)
                 if c.rsplit(": ", 1)[0] == name]  # fmt: skip
        total = float(sum(loads))
        shares = [v / total if total else 0.0 for v in loads]
        row = {
            "contact": name,
            "axial_N": total,
            "threads": [
                {"thread": i + 1, "axial_N": v, "share": s}
                for i, (v, s) in enumerate(zip(loads, shares, strict=True))
            ],  # fmt: skip
            "first_thread_share": shares[0] if shares else None,
            "wedge_N": float(wedge.values[0][list(wedge.components).index(name)])
            if wedge is not None and name in wedge.components
            else None,
        }
        c = found.get(name)
        if pressure is not None and c is not None and mesh is not None:
            nodes = [mesh.groups[g].node_ids for g in (c.source, c.target) if g in mesh.groups]
            if nodes:
                held = np.isin(pressure.ids, np.concatenate(nodes))
                if held.any():
                    row["peak_pressure_Pa"] = float(pressure.values[held, 0].max())
        out.append(row)
    return {"step": chosen.label(), "steps": len(steps), "threads": out}


def _components(project):
    from puzzle.model.schema import Component

    return project.of_type(Component)
