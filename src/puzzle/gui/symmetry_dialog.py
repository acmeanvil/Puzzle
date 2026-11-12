"""Model → Symmetry (ADR 0081): symmetry and antisymmetry planes, and the model's type
(3D, 2D planar, 2D axisymmetric), each one form and one Edit.

A symmetry plane goes on every physics of the component that has one: structural and
heat and electrostatic `symmetry`, fluid `symmetry`, magnetic `magnetic_symmetry`; an
antisymmetry plane on solid mechanics only."""

from __future__ import annotations

from puzzle.commands.model import Edit, EditOp
from puzzle.gui.quick_form import Ask
from puzzle.model.schema import Component
from puzzle.symmetry import plane_features, plane_ops

DIMENSIONS = [
    ("3D", "3d"),
    ("2D planar: plane stress", "2d_planar:plane_stress"),
    ("2D planar: plane strain", "2d_planar:plane_strain"),
    ("2D axisymmetric (Y the axis, X the radius)", "2d_axisymmetric"),
]


def add_plane(window, comp: Component, kind: str = "symmetry"):
    """One form: the flat face (a straight edge in 2D) to put the plane on."""
    from puzzle.gui.menus import selections_of

    targets = plane_features(comp, kind)
    if not targets:
        what = "solid mechanics" if kind == "antisymmetry" else "a physics"
        window.statusBar().showMessage(
            f"{comp.name} has no {what} to put a {kind} plane on: add one first", 8000)  # fmt: skip
        return None
    project = window.session.project
    entities = ("face",) if comp.dimension == "3d" else ("edge", "face")
    choices = [s for e in entities for s in selections_of(project, comp, e)]
    if not choices:
        window.statusBar().showMessage(
            f"No face selections on {comp.name} yet: pick the cut face in the view first", 8000
        )
        return None
    names = ", ".join(p.type.replace("_", " ") for p in (comp.physics[i] for i, _ in targets))
    intro = (
        "The model continues mirrored across this face: nothing moves through it."
        if kind == "symmetry"
        else "The model continues mirrored with the load reversed: nothing moves along it."
    ) + f" Added to {names}. The face must be flat (a straight edge in 2D)."
    got = window.ask_form(
        f"{'Symmetry' if kind == 'symmetry' else 'Antisymmetry'} plane on {comp.name}",
        [Ask("selection", "On the face", choices=[(c, c) for c in choices])],
        intro=intro,
    )
    if got is None:
        return None
    return window.submit(Edit(object=str(comp.id), ops=plane_ops(comp, kind, got["selection"])))


def set_model_type(window, comp: Component):
    """One form: 3D, 2D planar (plane stress or strain) or 2D axisymmetric (ADR 0048)."""
    now = comp.dimension if comp.dimension != "2d_planar" else f"2d_planar:{comp.planar_model}"
    got = window.ask_form(
        f"Model type of {comp.name}",
        [Ask("dimension", "Model", choices=DIMENSIONS, default=now)],
        intro="A 2D model's parts are faces at z = 0 (Geometry → a face from a profile, or "
        "a section of a 3D part); axisymmetric models turn about Y.",
    )
    if got is None or got["dimension"] == now:
        return None
    dimension, _, planar = got["dimension"].partition(":")
    ops = [EditOp(op="set", path="dimension", value=dimension)]
    if planar:
        ops.append(EditOp(op="set", path="planar_model", value=planar))
    return window.submit(Edit(object=str(comp.id), ops=ops))


AXES = [("Z", "0,0,1"), ("Y", "0,1,0"), ("X", "1,0,0")]


def _faces(window, comp: Component) -> list[str]:
    from puzzle.gui.menus import selections_of

    entities = ("face",) if comp.dimension == "3d" else ("edge", "face")
    return [s for e in entities for s in selections_of(window.session.project, comp, e)]


def set_cyclic(window, comp: Component):
    """One form: the two cut faces, how many sectors, the axis (ADR 0081 step 3)."""
    faces = _faces(window, comp)
    if len(faces) < 2:
        window.statusBar().showMessage(
            f"{comp.name} needs its two cut faces as selections: pick them first", 8000
        )
        return None
    now = comp.cyclic
    asks = [
        Ask("source", "First cut face", choices=[(f, f) for f in faces],
            default=now.source if now else faces[0]),
        Ask("target", "Second cut face (the first, turned)", choices=[(f, f) for f in faces],
            default=now.target if now else faces[1]),
        Ask("sectors", "Sectors in the whole", kind="text",
            default=str(now.sectors) if now else "12"),
        Ask("axis", "Axis (through the origin)", choices=AXES,
            default=",".join(f"{v:g}" for v in now.axis) if now else "0,0,1"),
    ]  # fmt: skip
    got = window.ask_form(
        f"Cyclic symmetry of {comp.name}", asks,
        intro="The component is one sector; the second cut face is the first turned by "
        "360°/sectors about the axis (right-handed). Each node there moves as its twin on "
        "the first, turned. Loads repeat in every sector: the whole carries sectors times "
        "them. Static, transient and heat studies.",
        check=lambda v: None if v["sectors"].strip().isdigit() and int(v["sectors"]) >= 2
        else "sectors is a whole number, 2 or more",
    )  # fmt: skip
    if got is None:
        return None
    value = {
        "source": got["source"],
        "target": got["target"],
        "sectors": int(got["sectors"]),
        "origin": (0.0, 0.0, 0.0),
        "axis": tuple(float(v) for v in got["axis"].split(",")),
    }
    return window.submit(Edit(object=str(comp.id),
                              ops=[EditOp(op="set", path="cyclic", value=value)]))  # fmt: skip


def set_periodic(window, comp: Component):
    """One form: the two cut faces and the step between them (ADR 0081 step 4)."""
    from puzzle.settings import length_unit

    faces = _faces(window, comp)
    if len(faces) < 2:
        window.statusBar().showMessage(
            f"{comp.name} needs its two cut faces as selections: pick them first", 8000
        )
        return None
    unit, per_metre = length_unit()
    now = comp.periodic
    asks = [
        Ask("source", "First cut face", choices=[(f, f) for f in faces],
            default=now.source if now else faces[0]),
        Ask("target", "Second cut face (the first, moved)", choices=[(f, f) for f in faces],
            default=now.target if now else faces[1]),
        *[Ask(a, f"Step along {a} ({unit})", kind="text",
              default=f"{now.translation[i] * per_metre:g}" if now else "0")
          for i, a in enumerate("xyz")],
    ]  # fmt: skip

    def check(v):
        try:
            step = [float(v[a]) for a in "xyz"]
        except ValueError:
            return "the step is three numbers"
        return None if any(step) else "the step between the faces can't be zero"

    got = window.ask_form(
        f"Periodic symmetry of {comp.name}", asks,
        intro="The component is one slice of a whole that repeats along a line: the second "
        "cut face is the first moved by the step, and each node there moves as its twin "
        "(no stretch along the line overall).", check=check)  # fmt: skip
    if got is None:
        return None
    step = tuple(float(got[a]) / per_metre for a in "xyz")
    value = {"source": got["source"], "target": got["target"], "translation": step}
    return window.submit(Edit(object=str(comp.id),
                              ops=[EditOp(op="set", path="periodic", value=value)]))  # fmt: skip


def clear_repeats(window, comp: Component):
    """Remove cyclic and periodic symmetry: the component is the whole again."""
    ops = [EditOp(op="set", path=k, value=None) for k in ("cyclic", "periodic")
           if getattr(comp, k) is not None]  # fmt: skip
    return window.submit(Edit(object=str(comp.id), ops=ops)) if ops else None
