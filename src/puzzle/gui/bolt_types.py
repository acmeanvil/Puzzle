"""Bolt types in the GUI (ADR 0050): New bolt type… in one dialog.

The standard and size come from `puzzle.bolts` (ISO metric coarse and fine, UNC,
UNF, or a custom diameter); the grade is a project material or one of the shipped
bolting grades, copied in with the type (one command batch, one undo); the preload is
none, a force, a share of the yield load (70 % by default) or a torque with a nut
factor. The name follows the size and grade until typed.
"""

from __future__ import annotations

from puzzle.bolts import STANDARDS, sizes
from puzzle.gui.choices import label
from puzzle.gui.quick_form import Ask
from puzzle.model.schema import Material

PRELOADS = [
    ("Share of the yield load", "yield_percent"),
    ("A force", "force"),
    ("A torque", "torque"),
    ("None", "none"),
]
JOINTS = [(label(k), k) for k in ("hole_walls", "through_nut", "tapped", "stud_nuts",
                                   "stud_tapped")]  # fmt: skip
FORMULATIONS = [("Beam (no mesh)", "beam"), ("Solid (a meshed bolt)", "solid")]
ENDS = [("Distributing (spreads the load, like RBE3)", "distributing"),
        ("Rigid (stiffens the hole, like RBE2)", "rigid")]  # fmt: skip


def _grades(project) -> list[tuple[str, str]]:
    """The project's materials, then the shipped bolting grades not yet in it."""
    from puzzle.materials import library_entry, library_keys

    have = sorted(project.of_type(Material), key=lambda m: m.name)
    names = {m.name for m in have}
    out = [(m.name, str(m.id)) for m in have]
    for key in library_keys():
        entry = library_entry(key)
        if entry["info"].get("group") == "Fastener" and entry["name"] not in names:
            out.append((f"{entry['name']} (from the library)", f"library:{key}"))
    return out


def _default_grade(choices) -> str | None:
    for text, value in choices:
        if "8.8" in text:
            return value
    return choices[0][1] if choices else None


def asks(project) -> list[Ask]:
    from puzzle.model.units import pretty
    from puzzle.settings import current, length_unit

    grades = _grades(project)
    units = current().display_units()
    length, _ = length_unit()
    return [
        Ask("standard", "Standard", choices=[(v, k) for k, v in STANDARDS.items()],
            default="iso_coarse"),
        Ask("size", "Size", choices=lambda v: [(s.name, s.name) for s in sizes(v["standard"])],
            default="M10", shown=lambda v: v["standard"] != "custom"),
        Ask("diameter", f"Diameter ({pretty(length)})", kind="text",
            shown=lambda v: v["standard"] == "custom"),
        Ask("material", "Grade", choices=grades, default=_default_grade(grades),
            help="the material's yield and proof strengths are what the checks use"),
        Ask("preload", "Preload", choices=PRELOADS, default="yield_percent"),
        Ask("percent", "Share of yield (%)", kind="text", default="70",
            shown=lambda v: v["preload"] == "yield_percent"),
        Ask("force", f"Force ({pretty(units['force'])})", kind="text",
            shown=lambda v: v["preload"] == "force"),
        Ask("torque", "Torque (N·m)", kind="text", shown=lambda v: v["preload"] == "torque"),
        Ask("nut_factor", "Nut factor K", kind="text", default="0.2",
            shown=lambda v: v["preload"] == "torque",
            help="0.2 for plain steel, about 0.15 lubricated"),
        Ask("joint", "Joint", choices=JOINTS, default="hole_walls",
            help="what the head, nut or thread bears on"),
        Ask("formulation", "Formulation", choices=FORMULATIONS, default="beam"),
        Ask("ends", "Ends", choices=ENDS, default="distributing"),
        Ask("name", "Name", kind="text", follow=lambda v: _name(v, grades)),
    ]  # fmt: skip


def _name(values: dict, grades) -> str:
    size = values.get("size") if values.get("standard") != "custom" else "Custom bolt"
    grade = dict((v, t) for t, v in grades).get(values.get("material"), "")
    grade = grade.replace(" (from the library)", "")
    short = grade.split("Class ")[-1].split("Grade ")[-1] if grade else ""
    return f"{size} {short}".strip()


def _number(text, what: str) -> float:
    try:
        return float(str(text).strip())
    except ValueError:
        raise ValueError(f"type a number for the {what}") from None


def check(values: dict) -> str | None:
    try:
        commands(values, check_only=True)
    except ValueError as exc:
        return str(exc)[:1].upper() + str(exc)[1:]
    return None


def commands(values: dict, check_only: bool = False) -> list:
    """The batch: the grade copied from the library if it isn't in the project yet,
    then the bolt type."""
    from puzzle.commands.model import Create
    from puzzle.materials import library_material
    from puzzle.model.units import plain
    from puzzle.settings import current, length_unit

    units = current().display_units()
    data: dict = {"name": (values.get("name") or "").strip() or "Bolt type",
                  "standard": values["standard"], "preload": values["preload"],
                  "formulation": values["formulation"], "ends": values["ends"],
                  "joint": values.get("joint", "hole_walls")}  # fmt: skip
    if values["standard"] == "custom":
        data["diameter"] = {"value": _number(values.get("diameter"), "diameter"),
                            "unit": plain(length_unit()[0])}  # fmt: skip
    else:
        data["size"] = values["size"]
    if values["preload"] == "yield_percent":
        percent = _number(values.get("percent"), "share of yield")
        if not 0 < percent <= 100:
            raise ValueError("the share of yield is a percentage, above 0 and at most 100")
        data["preload_percent"] = percent
    elif values["preload"] == "force":
        data["preload_force"] = {"value": _number(values.get("force"), "preload force"),
                                 "unit": plain(units["force"])}  # fmt: skip
    elif values["preload"] == "torque":
        data["torque"] = {"value": _number(values.get("torque"), "torque"), "unit": "N*m"}
        data["nut_factor"] = _number(values.get("nut_factor"), "nut factor")
    out = []
    grade = values.get("material")
    if not grade:
        raise ValueError("choose a grade")
    if str(grade).startswith("library:"):
        material = library_material(str(grade).split(":", 1)[1])
        out.append(Create(kind="material", data=material.model_dump(mode="json")))
        data["material"] = str(material.id)
    else:
        data["material"] = grade
    if check_only:
        return out
    out.append(Create(kind="bolt_type", data=data))
    return out


def new_bolt_type(window):
    """New bolt type…: everything in one dialog, made as one batch (one undo)."""
    project = window.session.project
    values = window.ask_form(
        "New bolt type",
        asks(project),
        intro="A kind of bolt, given once: every bolt of this type takes its size, grade "
        "and preload unless it sets its own.",
        check=check,
    )
    if values is None:
        return None
    try:
        batch = commands(values)
    except ValueError as exc:
        window.statusBar().showMessage(str(exc), 8000)
        return None
    return window.submit_batch(batch)
