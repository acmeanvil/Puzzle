""" "Written or refused" (ADR 0090 pattern 2, work package 5): every adapter either
writes a thing it is given or refuses it with a coded `PuzzleError`, never accepts it and
drops it.

The check is differential and needs no solver. A minimal valid case for (adapter,
physics, step kind) is written through `run_study` up to the solver call (the job is
stopped before it starts) on a tiny mesh made once; then one thing is added (a feature
with distinctive values, a material model, a contact, a fastener, a weld, a setting, a
coupling...) and the case is written again. The cell is

- "refused" when that raises a `PuzzleError` whose code is in `errors.CODES`;
- "written" when the solver's input files (the mesh files aside) differ from the base's;
- "silent" when it is accepted and the input is the base's byte for byte: dropped;
- "error" when anything else is raised (a KeyError, a pydantic error...).

Nothing here keeps a list of what each cell should contain: the base and the added case
are both written by the adapter itself. What by design changes nothing in the input is
in `EXEMPT` and `MODAL_LOADS`, each with its reason. `tests/test_written_or_refused.py`
asserts no cell is silent or an error and that the cells agree with each adapter's
`Capabilities`; `scripts/written_matrix.py` prints the matrix as a table.
"""

from __future__ import annotations

import copy
import shutil
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

from puzzle.errors import CODES, PuzzleError
from puzzle.model.catalog import CATALOG, STEP_TYPES

ADAPTERS = ("calculix", "code_aster", "elmer", "fenicsx", "code_saturne", "openfoam",
            "opencourant", "pynite")  # fmt: skip
PHYSICS = tuple(CATALOG)  # every physics the catalog offers features for
KINDS = ("body", "boundary", "edge", "point")
# The bar: 200 x 10 x 10 mm in two bodies (x < L/2, x > L/2) of one part, so a section
# face lies inside it for pretension; coarse, so a write takes milliseconds.
L, B, H = 0.2, 0.01, 0.01
# Files that hold the mesh, not the model: a difference there alone says nothing.
MESH_FILES = (".med", ".msh", ".npy", "mesh.nodes", "mesh.elements", "mesh.boundary",
              "mesh.header", "node-ids.txt", "puzzle-node-ids.txt", ".meshnodes",
              "polyMesh")  # fmt: skip
GAP = 1e-3  # between the split bar's halves
DISTINCT = 1.2345  # the factor every value gets, so an added thing can't equal a default

# What changes nothing in the input by design, (adapter or "*", physics, step kind or
# "*", feature): kept tiny, each with its reason.
EXEMPT: dict[tuple[str, str, str, str], str] = {
    ("*", "heat_transfer", "*", "symmetry"): "zero flux is the natural condition",
    ("*", "electrostatics", "*", "symmetry"): "zero normal field is the natural condition",
    # common.pretension_states: a section is closed in a modal or harmonic step without a
    # preload before it; Code_Aster runs that step as its own job, so closed is the
    # section left uncut (the bolt whole); CalculiX ties the cut shut instead.
    **{
        ("code_aster", "solid_mechanics", step, kind): "the section is closed (left uncut) "
        "in a modal or harmonic job: common.pretension_states"
        for step in ("modal", "frequency_domain")
        for kind in ("bolt_pretension", "bolt_adjustment")
    },
}
# Loads in a modal step: a linear eigenproblem has no right-hand side, so a load has
# nothing to act on (CalculiX's test_modal_step_writes_frequency_and_drops_loads).
# Stiffness (supports, springs, held values, pretension) is never in this list.
MODAL_LOADS = frozenset({
    "force", "pressure", "gravity", "acceleration", "rotation", "body_force", "traction",
    "line_load", "remote_force", "heat_flux", "convection", "heat_generation",
    "neumann", "velocity", "initial_velocity",
})  # fmt: skip


def exempt(solver: str, physics: str, step: str, item: str) -> str | None:
    """Why this cell changes nothing by design, or None."""
    if step == "modal" and item in MODAL_LOADS:
        return "a load has nothing to act on in a modal (eigenvalue) step"
    for key in ((solver, physics, step, item), ("*", physics, step, item),
                (solver, physics, "*", item), ("*", physics, "*", item)):  # fmt: skip
        if key in EXEMPT:
            return EXEMPT[key]
    return None


@dataclass
class Outcome:
    kind: str  # written, refused, silent, error
    message: str = ""
    code: str | None = None
    files: dict[str, bytes] = field(default_factory=dict, repr=False)

    def __str__(self) -> str:
        what = self.kind + (f" ({self.code})" if self.code else "")
        return what + (f": {self.message[:400]}" if self.message else "")


class _Stopped(Exception):
    """The job was about to start: everything before it was written."""


def _collect(folder: Path) -> dict[str, bytes]:
    out = {}
    for p in sorted(folder.rglob("*")):
        if not p.is_file() or p.name == "progress.jsonl":
            continue
        text = p.read_bytes()
        for form in (str(folder), folder.as_posix(), str(folder).replace("\\", "\\\\")):
            text = text.replace(form.encode(), b"<work>")  # the folder's own path
        out[p.relative_to(folder).as_posix()] = text
    return out


def input_files(files: dict[str, bytes]) -> dict[str, bytes]:
    """The solver input proper: the files that are not the mesh."""
    return {k: v for k, v in files.items() if not any(m in k for m in MESH_FILES)}


@contextmanager
def no_solver():
    """`run_study` up to the solver call: the job is stopped before it starts."""

    from puzzle.jobs.runtime import NativeRuntime

    class Here(NativeRuntime):
        """No solver here (asking Docker takes 50 ms a call), and a fixed memory, so an
        input that reads the runtime is the same in every write."""

        def available(self, executable: str) -> bool:
            return False

        def memory(self):
            return 8 * 2**30

    def stop(*args, **kwargs):
        raise _Stopped

    with (
        mock.patch("puzzle.solve.run_job", stop),
        mock.patch("puzzle.solve.runtime_for", lambda solver: Here()),
        mock.patch("puzzle.jobs.runtime.runtime_for", lambda solver: Here()),
    ):
        yield


def write(project, study_id, root: Path, meshed) -> Outcome:
    """Write `study_id` as `run_study` does, without running anything."""
    from puzzle.solve import run_study

    work = Path(tempfile.mkdtemp(prefix="wor-"))
    try:
        with no_solver():
            run_study(project, study_id, root, work, meshed=meshed)
        return Outcome("error", "run_study returned without starting a job")
    except _Stopped:
        return Outcome("written", files=_collect(work))
    except PuzzleError as exc:
        code = getattr(exc, "code", None)
        if code in CODES:
            return Outcome("refused", getattr(exc, "message", str(exc)), code)
        return Outcome("error", f"{type(exc).__name__} with code {code!r}: {exc}")
    except Exception as exc:  # noqa: BLE001 - every other exception is the finding
        return Outcome("error", f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def compare(base: Outcome, added: Outcome) -> Outcome:
    """The cell's outcome: the added case's, "written" turned "silent" when its input is
    the base's."""
    if added.kind != "written":
        return added
    if base.kind == "refused":  # the thing added is what made the case writable
        return Outcome("written", f"the base alone is refused ({base.message[:120]})")
    if base.kind != "written":
        return Outcome("error", f"the base case was not written ({base}); the added one was")
    a, b = input_files(added.files), input_files(base.files)
    if a == b:
        return Outcome("silent", "accepted, and the input is the base's byte for byte")
    changed = sorted(k for k in a.keys() | b.keys() if a.get(k) != b.get(k))
    return Outcome("written", "changed: " + ", ".join(changed[:6]))


def q(value, unit=""):
    from puzzle.model import Quantity

    return Quantity(value=value, unit=unit)


# -- worlds: geometry, a mesh and a project, made once each ---------------------------


@dataclass
class Place:
    """A world: its project and component (without physics), its mesh, and which
    selection stands for each kind of thing added."""

    project: object
    component: object
    meshed: object
    added: dict[str, str]  # catalog entity (body, face, edge, vertex) -> selection
    kinds: dict[str, str]  # selection kind -> selection
    base: dict[str, list] = field(default_factory=dict)  # physics -> base features


def _box_step(path: Path, boxes) -> Path:
    import gmsh

    from puzzle.geometry import gmsh_session

    with gmsh_session("box"):
        tags = [(3, gmsh.model.occ.addBox(*b)) for b in boxes]
        if len(tags) > 1:
            gmsh.model.occ.fragment(tags[:1], tags[1:])
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    return path


def _bar(root: Path, order: int, shape: str = "tetrahedra") -> Place:
    from puzzle.geometry import import_step
    from puzzle.materials import add_library_material
    from puzzle.mesh import generate_mesh
    from puzzle.model import (
        Component,
        Feature,
        Geometry,
        MaterialAssignment,
        MeshRecipe,
        NamedSelection,
        PartInstance,
        Project,
    )

    step = root / "bar.step"
    if not step.exists():
        _box_step(step, [(0, 0, 0, L / 2, B, H), (L / 2, 0, 0, L / 2, B, H)])
    m = import_step(step)
    edge = next(e.name for e in m.of_dim(1)
                if abs(e.bbox[0] - L) < 1e-6 and abs(e.bbox[3] - L) < 1e-6)  # fmt: skip
    corner = next(p.name for p in m.of_dim(0) if abs(p.bbox[0] - L) < 1e-6)
    walls = [f for axis, at in (("y", B), ("z", 0), ("z", H)) for f in m.faces_on_plane(axis, at)]
    p = Project(name="bar")
    geo = p.add(Geometry(name="Bar", source=step.name, selections=[
        NamedSelection(name="Body", entity="body", members=[b.name for b in m.of_dim(3)]),
        NamedSelection(name="Root", entity="face", members=m.faces_on_plane("x", 0.0)),
        NamedSelection(name="Tip", entity="face", members=m.faces_on_plane("x", L)),
        NamedSelection(name="Section", entity="face", members=m.faces_on_plane("x", L / 2)),
        NamedSelection(name="Side", entity="face", members=m.faces_on_plane("y", 0.0)),
        NamedSelection(name="Walls", entity="face", members=walls),
        NamedSelection(name="TipEdge", entity="edge", members=[edge]),
        NamedSelection(name="TipCorner", entity="vertex", members=[corner]),
    ]))  # fmt: skip
    steel = add_library_material(p, "structural_steel")
    recipe = p.add(MeshRecipe(name="Mesh", global_size=q(10, "mm"), element_order=order,
                              element_shape=shape))  # fmt: skip
    comp = p.add(Component(name="Bar", parts=[PartInstance(name="Bar", geometry=geo.id)],
                           mesh=recipe.id,
                           materials=[MaterialAssignment(material=steel.id,
                                                         selection="Bar/Body")]))  # fmt: skip
    meshed = generate_mesh(p, comp.id, root, root / f"mesh-bar-{order}-{shape}")
    f = Feature
    base = {
        "solid_mechanics": [f(type="fixed", selection="Bar/Root"),
                            f(type="force", selection="Bar/Tip", values={"x": q(-1, "N")})],
        "heat_transfer": [f(type="temperature", selection="Bar/Root", values={"T": q(300, "K")})],
        "electrostatics": [f(type="electric_potential", selection="Bar/Root",
                             values={"V": q(10, "V")}), f(type="ground", selection="Bar/Tip")],
        "magnetic_fields": [f(type="coil_current", selection="Bar/Body", values={"I": q(10, "A")}),
                            f(type="coil_start", selection="Bar/Root"),
                            f(type="coil_end", selection="Bar/Tip")],
        "fluid_flow": [f(type="velocity_inlet", selection="Bar/Root", values={"U": q(0.01, "m/s")}),
                       f(type="pressure_outlet", selection="Bar/Tip", values={"p": q(0, "Pa")}),
                       f(type="wall", selection="Bar/Walls")],
        "custom_pde": [f(type="dirichlet", selection="Bar/Root",
                         values={"value": q(1.0, "dimensionless")})],
    }  # fmt: skip
    return Place(p, comp, meshed,
                 added={"body": "Bar/Body", "face": "Bar/Side", "edge": "Bar/TipEdge",
                        "vertex": "Bar/TipCorner", "section": "Bar/Section",
                        "walls": "Bar/Walls"},
                 kinds={"body": "Bar/Body", "boundary": "Bar/Side", "edge": "Bar/TipEdge",
                        "point": "Bar/TipCorner"}, base=base)  # fmt: skip


def _split(root: Path, order: int) -> Place:
    """Two half bars end to end, 1 mm apart, Left and Right, meshed apart: each held at
    its far end, so a contact, a fastener or a weld between them is the one thing added."""
    from puzzle.geometry import import_step
    from puzzle.materials import add_library_material
    from puzzle.mesh import generate_mesh
    from puzzle.model import (
        Component,
        Feature,
        Geometry,
        MaterialAssignment,
        MeshRecipe,
        NamedSelection,
        PartInstance,
        Project,
    )

    half = L / 2
    step = root / "half.step"
    if not step.exists():
        _box_step(step, [(0, 0, 0, half, B, H)])
    m = import_step(step)
    edge = next(e.name for e in m.of_dim(1) if abs(e.bbox[0] - half) < 1e-6
                and abs(e.bbox[3] - half) < 1e-6 and abs(e.bbox[2] - H) < 1e-6
                and abs(e.bbox[5] - H) < 1e-6)  # fmt: skip
    p = Project(name="split")
    geo = p.add(Geometry(name="Half", source=step.name, selections=[
        NamedSelection(name="Body", entity="body", members=["body-1"]),
        NamedSelection(name="Start", entity="face", members=m.faces_on_plane("x", 0.0)),
        NamedSelection(name="End", entity="face", members=m.faces_on_plane("x", half)),
        NamedSelection(name="Top", entity="face", members=m.faces_on_plane("z", H)),
        NamedSelection(name="Side", entity="face", members=m.faces_on_plane("y", 0.0)),
        NamedSelection(name="EndTop", entity="edge", members=[edge]),
    ]))  # fmt: skip
    steel = add_library_material(p, "structural_steel")
    recipe = p.add(MeshRecipe(name="Mesh", global_size=q(10, "mm"), element_order=order))
    comp = p.add(Component(
        name="Split", mesh=recipe.id,
        parts=[PartInstance(name="Left", geometry=geo.id),
               PartInstance(name="Right", geometry=geo.id,
                            translation=(half + GAP, 0.0, 0.0))],
        materials=[MaterialAssignment(material=steel.id, selection="Left/Body"),
                   MaterialAssignment(material=steel.id, selection="Right/Body")]))  # fmt: skip
    meshed = generate_mesh(p, comp.id, root, root / f"mesh-split-{order}")
    f = Feature
    base = {
        "solid_mechanics": [f(type="fixed", selection="Left/Start"),
                            f(type="fixed", selection="Right/End"),
                            f(type="force", selection="Right/Top", values={"z": q(-1, "N")})],
        "heat_transfer": [f(type="temperature", selection="Left/Start", values={"T": q(300, "K")}),
                          f(type="temperature", selection="Right/End", values={"T": q(350, "K")})],
    }  # fmt: skip
    return Place(p, comp, meshed, added={}, kinds={}, base=base)


def _frame(root: Path) -> Place:
    """A two-member frame A-B-C along x (needs OCP: a frame is a history step)."""
    from puzzle import frames
    from puzzle.materials import add_library_material
    from puzzle.mesh import generate_mesh
    from puzzle.model import (
        Component,
        Feature,
        MaterialAssignment,
        MeshRecipe,
        PartInstance,
        Project,
    )
    from puzzle.model.schema import BeamSection

    p = Project(name="frame")
    geo = p.add(frames.geometry("Frame", {"A": [0, 0, 0], "B": [2, 0, 0], "C": [4, 0, 0]},
                                [["AB", "A", "B"], ["BC", "B", "C"]]))  # fmt: skip
    steel = add_library_material(p, "structural_steel")
    recipe = p.add(MeshRecipe(name="M", global_size=q(500, "mm")))
    comp = p.add(Component(
        name="Frame", parts=[PartInstance(name="F", geometry=geo.id)], mesh=recipe.id,
        materials=[MaterialAssignment(material=steel.id, selection="F")],
        beam_sections=[BeamSection(selection="F", shape="rectangle", width=q(20, "mm"),
                                   height=q(40, "mm"))]))  # fmt: skip
    meshed = generate_mesh(p, comp.id, root, root / "mesh-frame")
    f = Feature
    base = {
        "solid_mechanics": [
            f(type="fixed", selection="F/A"),
            f(type="force", selection="F/C", values={"z": q(-1, "N")}),
        ]
    }
    return Place(p, comp, meshed,
                 added={"body": "F", "face": "F/B", "edge": "F/BC", "vertex": "F/B"},
                 kinds={"body": "F", "edge": "F/BC", "point": "F/B"}, base=base)  # fmt: skip


def _planar(root: Path, shape: str = "tetrahedra", order: int = 2) -> Place:
    """A 40 x 20 mm plate in plane strain (needs OCP: a face is a history step)."""
    from puzzle.materials import add_library_material
    from puzzle.mesh import generate_mesh
    from puzzle.model import (
        Component,
        Feature,
        Geometry,
        MaterialAssignment,
        MeshRecipe,
        NamedSelection,
        PartInstance,
        Project,
    )
    from puzzle.model.schema import HistoryStep

    w, h = 0.04, 0.02
    p = Project(name="plate")
    geo = p.add(Geometry(name="Plate", history=[
        HistoryStep(kind="face", name="Plate",
                    args={"profile": {"points": [[0, 0], [w, 0], [w, h], [0, h]]}}),
    ], selections=[
        NamedSelection(name="Region", entity="face", members=["Plate/face"]),
        NamedSelection(name="Bottom", entity="edge", members=["Plate/side-1"]),
        NamedSelection(name="Right", entity="edge", members=["Plate/side-2"]),
        NamedSelection(name="Top", entity="edge", members=["Plate/side-3"]),
        NamedSelection(name="Left", entity="edge", members=["Plate/side-4"]),
    ]))  # fmt: skip
    steel = add_library_material(p, "structural_steel")
    recipe = p.add(MeshRecipe(name="Mesh", global_size=q(5, "mm"), element_shape=shape,
                              element_order=order))  # fmt: skip
    comp = p.add(Component(name="Plate", dimension="2d_planar", planar_model="plane_strain",
                           mesh=recipe.id,
                           parts=[PartInstance(name="Plate", geometry=geo.id)],
                           materials=[MaterialAssignment(material=steel.id,
                                                         selection="Plate/Region")]))  # fmt: skip
    meshed = generate_mesh(p, comp.id, root, root / f"mesh-plate-{shape}-{order}")
    f = Feature
    base = {
        "solid_mechanics": [f(type="fixed", selection="Plate/Bottom"),
                            f(type="force", selection="Plate/Top", values={"y": q(1, "N")})],
        "heat_transfer": [f(type="temperature", selection="Plate/Bottom",
                            values={"T": q(300, "K")})],
        "magnetic_fields": [f(type="coil_current", selection="Plate/Region",
                              values={"I": q(10, "A")})],
    }  # fmt: skip
    return Place(
        p,
        comp,
        meshed,
        added={"body": "Plate/Region", "face": "Plate/Right", "edge": "Plate/Right"},
        kinds={"body": "Plate/Region", "boundary": "Plate/Right"},
        base=base,
    )


class World:
    """The places, each made on first use (a missing optional package fails only the
    cells that need it)."""

    BUILDERS: dict[str, Callable] = {
        "tet10": lambda r: _bar(r, 2),
        "tet4": lambda r: _bar(r, 1),
        "hex8": lambda r: _bar(r, 1, "hexahedra"),
        "split10": lambda r: _split(r, 2),
        "split4": lambda r: _split(r, 1),
        "frame": _frame,
        "planar": _planar,
        "planar_quad": lambda r: _planar(r, "hexahedra", 1),
    }

    def __init__(self, root: Path):
        self.root = Path(root)
        self.places: dict[str, Place] = {}
        self.bases: dict[tuple, Outcome] = {}

    def place(self, name: str) -> Place:
        if name not in self.places:
            self.places[name] = self.BUILDERS[name](self.root)
        return self.places[name]


# -- cases -------------------------------------------------------------------------------

STEP_SETTINGS = {
    "transient": {"time_step": 1e-3, "end_time": 2e-3},
    "frequency_domain": {"frequencies": "100, 200"},
    "modal": {"modes": 2},
    "buckling": {"modes": 2},
    "explicit": {"end_time": 1e-5, "frames": 2},
}
CFD = ("code_saturne", "openfoam")


@dataclass(frozen=True)
class Case:
    """A base case: (adapter, physics, step kind) on a place; `variant` for a modal
    transient or a coupled thermal-structural step."""

    solver: str
    physics: str
    step: str
    place: str = ""
    variant: str = ""

    @property
    def where(self) -> str:
        return self.place or default_place(self.solver)

    def ident(self) -> str:
        step = self.step + (f"+{self.variant}" if self.variant else "")
        place = f"@{self.place}" if self.place else ""
        return f"{self.solver}/{self.physics}/{step}{place}"


def default_place(solver: str) -> str:
    """First-order meshes for the finite-volume solvers and the explicit one (pressure on
    first-order faces only), a frame for PyNite, second order for the others."""
    if solver == "pynite":
        return "frame"
    return "tet4" if solver in (*CFD, "opencourant") else "tet10"


def _physics_list(place: Place, case: Case) -> list:
    from puzzle.model import CustomEquation, Feature, Physics, Unknown

    def one(kind: str) -> Physics:
        settings: dict = {}
        if kind == "heat_transfer":
            settings = {"temperature_dependent_materials": case.variant == "nonlinear"}
            if case.step == "transient":
                settings["initial_temperature"] = q(300, "K")
        equation = None
        if kind == "custom_pde":
            equation = CustomEquation(unknowns=[Unknown(name="u")],
                                      weak_form="inner(grad(u), grad(v_u)) * dx")  # fmt: skip
        return Physics(type=kind, features=list(place.base.get(kind, [])), settings=settings,
                       equation=equation)  # fmt: skip

    if case.solver in CFD and case.physics == "heat_transfer":
        # The finite-volume solvers solve heat in the flow: the flow, and the inlet's
        # temperature.
        flow, heat = one("fluid_flow"), one("heat_transfer")
        heat.features = [Feature(type="temperature", selection="Bar/Root",
                                 values={"T": q(300, "K")})]  # fmt: skip
        return [heat, flow]
    out = [one(case.physics)]
    if case.variant == "coupled":
        out.append(one("heat_transfer" if case.physics == "solid_mechanics" else "solid_mechanics"))
    return out


_MATERIAL = {"fluid_flow": "air", "magnetic_fields": "copper", "electrostatics": "water"}


def make(world: World, case: Case, change: Callable | None = None):
    """The base project for `case`, then `change(project, component, study)`: the
    project and the study's id."""
    from puzzle.materials import add_library_material
    from puzzle.model import SolveStep, Study

    place = world.place(case.where)
    project = copy.deepcopy(place.project)
    comp = project.objects[place.component.id]
    comp.physics = _physics_list(place, case)
    if case.physics in _MATERIAL or (case.solver in CFD):
        m = add_library_material(project, _MATERIAL.get(case.physics, "air"))
        comp.materials = [a.model_copy(update={"material": m.id}) for a in comp.materials]
    if case.variant == "coupled":
        comp.couplings = ["thermal_expansion"]
    if case.variant == "nonlinear" and case.physics == "solid_mechanics":
        _material_with(project, comp, _model("plasticity"))
    settings = dict(STEP_SETTINGS.get(case.step, {}))
    if case.variant == "modal":
        settings["method"] = "modal"
    study = project.add(Study(name="s", component=comp.id, solver=case.solver,
                              steps=[SolveStep(type=case.step, settings=settings)]))  # fmt: skip
    if change is not None:
        change(project, comp, study)
    return project, study.id


def base(world: World, case: Case) -> Outcome:
    if case not in world.bases:
        project, sid = make(world, case)
        world.bases[case] = write(project, sid, world.root, world.place(case.where).meshed)
    return world.bases[case]


def cell(world: World, case: Case, change: Callable, against: Callable | None = None) -> Outcome:
    """Adding `change` to `case`'s base (or to the base with `against` applied)."""
    project, sid = make(world, case, change)
    added = write(project, sid, world.root, world.place(case.where).meshed)
    if against is None:
        return compare(base(world, case), added)
    project, sid = make(world, case, against)
    return compare(write(project, sid, world.root, world.place(case.where).meshed), added)


def distinct_values(spec) -> dict:
    """The catalog's values, each made distinctive (a zero default becomes 1.2345 in its
    unit); directions and fractions keep their defaults."""
    out = {}
    for name, (value, unit) in spec.values.items():
        if name in ("nx", "ny", "nz", "fill", "turns", "phase", "mu"):
            out[name] = q(value, unit)
        else:
            out[name] = q(value * DISTINCT if value else DISTINCT, unit)
    return out


def _physics_of(comp, kind: str):
    return next(p for p in comp.physics if p.type == kind)


def add_feature(physics: str, feature: str, selection: str) -> Callable:
    from puzzle.model import Feature
    from puzzle.model.catalog import spec

    s = spec(physics, feature)

    def change(project, comp, study):
        _physics_of(comp, physics).features.append(
            Feature(type=feature, selection=selection, values=distinct_values(s))
        )

    return change


def catalog_selection(place: Place, physics: str, feature: str, solver: str = "") -> str:
    from puzzle.model.catalog import spec

    if physics == "heat_transfer" and "walls" in place.added and solver in CFD:
        return place.added["walls"]  # heat in a flow goes on its walls
    if feature in ("bolt_pretension", "bolt_adjustment") and "section" in place.added:
        return place.added["section"]  # a section face inside the bar
    return place.added.get(spec(physics, feature).entity, place.added.get("face", ""))


def capabilities(solver: str):
    from puzzle.adapters import available_adapters

    return available_adapters()[solver].capabilities


def catalog_kind(physics: str, feature: str) -> str:
    from puzzle.model.catalog import spec

    return {"body": "body", "face": "boundary", "edge": "edge",
            "vertex": "point"}[spec(physics, feature).entity]  # fmt: skip


# -- the cells ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    """One cell: `what` (base, feature, kind, model, directions, contact, fastener,
    weld, thermal, setting, expert, coupling, weak_springs, shape) and `item` added to
    `case`'s base, on a selection `kind` where that varies."""

    case: Case
    what: str
    item: str = ""
    kind: str = ""

    def ident(self) -> str:
        return "/".join(x for x in (self.case.ident(), self.what, self.item, self.kind) if x)

    def exemption(self) -> str | None:
        case = self.case
        if self.what in ("feature", "kind"):
            return exempt(case.solver, case.physics, case.step, self.item)
        if self.what == "model" and self.item in MODEL_EXEMPT.get(case.step, ()):
            return MODEL_EXEMPT[case.step][self.item]
        if self.what == "setting":
            caps = capabilities(case.solver)
            if f"{self.item}:{SETTING_VALUES[self.item]}" in caps.shared_settings:
                return "the only choice this solver has: what it always does"
            key = (case.solver, case.physics, case.step, case.variant)
            if self.item in SETTING_EXEMPT.get(key, ()):
                return SETTING_EXEMPT[key][self.item]
        return None


# A linear perturbation step solves with the elastic constants by definition: plasticity
# has no load path to follow there (CalculiX writes *PLASTIC and its *FREQUENCY ignores it).
_LINEAR = "a linear perturbation step uses the elastic constants: nothing to yield"
MODEL_EXEMPT = {step: {"plasticity": _LINEAR} for step in ("modal", "buckling",
                                                           "frequency_domain")}  # fmt: skip
# Settings a linear solve has nothing to do with (Code_Aster's MECA_STATIQUE and
# THER_LINEAIRE solve once: no iterations to bound, no increments to step or thin); the
# "nonlinear" variant of the same case shows each is written once the solve iterates.
_ONCE = ("a linear solve is solved once: nothing to iterate, step or thin (written for "
         "the nonlinear variant)")  # fmt: skip
SETTING_EXEMPT = {
    ("code_aster", "solid_mechanics", "stationary", ""): dict.fromkeys(
        ("tolerance", "max_iterations", "output_every", "increments", "min_increment",
         "fixed_increments"), _ONCE),
    **{("code_aster", "heat_transfer", step, ""): dict.fromkeys(
        ("tolerance", "max_iterations"), _ONCE) for step in ("stationary", "transient")},
}  # fmt: skip

SOLID_STEPS = ("stationary", "transient", "modal", "buckling", "frequency_domain", "explicit")


def _steps(solver: str, physics: str, place: str = "") -> list[str]:
    taken = capabilities(solver).physics.get(physics, frozenset())
    return [s for s in STEP_TYPES if s in taken]


def feature_cells() -> list[Cell]:
    """Adapter x physics x step kind (the base), then x each catalog feature on its
    catalog kind, then, in the first step kind each adapter takes, x the other kinds."""
    out = []
    for solver in ADAPTERS:
        caps = capabilities(solver)
        for physics in PHYSICS:
            steps = caps.physics.get(physics)
            if steps is None:
                out.append(Cell(Case(solver, physics, "stationary"), "base"))
                continue
            for step in STEP_TYPES:
                case = Case(solver, physics, step)
                out.append(Cell(case, "base"))
                if step in steps:
                    out += [Cell(case, "feature", s.type) for s in CATALOG[physics]]
            case = Case(solver, physics, next(s for s in STEP_TYPES if s in steps))
            out += [Cell(case, "kind", s.type, k) for s in CATALOG[physics] for k in KINDS
                    if k != catalog_kind(physics, s.type)
                    and (solver != "pynite" or k != "boundary")]  # fmt: skip
    return out


def beam_cells() -> list[Cell]:
    """Frames on the FE solvers that write beams: features x their step kinds."""
    out = []
    for solver in ("calculix", "code_aster"):
        for step in ("stationary", "modal"):
            case = Case(solver, "solid_mechanics", step, "frame")
            out.append(Cell(case, "base"))
            out += [
                Cell(case, "feature", f)
                for f in ("fixed", "displacement", "force", "line_load", "node_spring", "gravity")
            ]
    return out


def planar_cells() -> list[Cell]:
    """2D: each FE solver's structural and heat features on a region and its edges."""
    out = []
    for solver in ("calculix", "code_aster", "elmer", "fenicsx"):
        for physics in ("solid_mechanics", "heat_transfer"):
            case = Case(solver, physics, "stationary", "planar")
            out.append(Cell(case, "base"))
            for s in CATALOG[physics]:
                for k in ("body", "boundary"):
                    out.append(Cell(case, "kind", s.type, k))
    return out


MODELS = ("plasticity", "orthotropic_elasticity", "hyperelasticity", "johnson_cook",
          "failure", "anisotropic_conductivity")  # fmt: skip


def model_cells() -> list[Cell]:
    """Each material model x each step kind of its physics, per adapter; material
    directions (an orthotropic material's axes turned) in 3D and 2D."""
    from puzzle.materials.registry import MODEL_PHYSICS

    out = []
    for solver in ADAPTERS:
        if solver == "pynite":
            continue
        for model in MODELS:
            physics = MODEL_PHYSICS.get(model, "solid_mechanics")
            for step in _steps(solver, physics):
                out.append(Cell(Case(solver, physics, step), "model", model))
        for physics, model in (("solid_mechanics", "orthotropic_elasticity"),
                               ("heat_transfer", "anisotropic_conductivity")):  # fmt: skip
            if model not in capabilities(solver).material_models:
                continue
            out.append(Cell(Case(solver, physics, "stationary"), "directions", model))
            if "2d_planar" in capabilities(solver).dimensions:
                out.append(Cell(Case(solver, physics, "stationary", "planar"), "directions",
                                model))  # fmt: skip
    return out


CONTACTS = ("bonded_nonconformal", "frictionless", "frictional", "no_separation", "rough",
            "self_contact")  # fmt: skip


def contact_cells() -> list[Cell]:
    out = []
    for solver in ADAPTERS:
        if solver in (*CFD, "pynite"):
            continue
        # first order too: Code_Aster's friction takes first-order meshes only
        places = ("split4",) if solver == "opencourant" else ("split10", "split4")
        for physics in ("solid_mechanics", "heat_transfer"):
            for step in _steps(solver, physics):
                for place in places:
                    case = Case(solver, physics, step, place)
                    out.append(Cell(case, "base"))
                    kinds = (CONTACTS if physics == "solid_mechanics"
                             else ("bonded_nonconformal",))  # fmt: skip
                    out += [Cell(case, "contact", c) for c in kinds]
    return out


def fastener_cells() -> list[Cell]:
    """Beam fasteners (rigid and distributing ends, with and without pretension) and a
    line weld, x each structural step kind."""
    out = []
    for solver in ADAPTERS:
        if solver in (*CFD, "pynite"):
            continue
        for step in _steps(solver, "solid_mechanics"):
            case = Case(solver, "solid_mechanics", step,
                        "split4" if solver == "opencourant" else "split10")  # fmt: skip
            out += [Cell(case, "fastener", f) for f in
                    ("rigid", "distributing", "rigid+pretension")]  # fmt: skip
            out.append(Cell(case, "weld", "line"))
    return out


def thermal_cells() -> list[Cell]:
    """A temperature load (a body temperature: a temperature load, as a transfer's) on a
    model with a beam fastener, a pretension section, a weld's springs."""
    out = []
    for solver in ("calculix", "code_aster", "elmer", "fenicsx"):
        for item in ("fastener", "pretension", "weld"):
            place = "tet10" if item == "pretension" else "split10"
            out.append(Cell(Case(solver, "solid_mechanics", "stationary", place), "thermal",
                            item))  # fmt: skip
    return out


SETTING_VALUES = {
    "linear_solver": "iterative", "tolerance": 1.2345e-7, "max_iterations": 37,
    "output_every": 2, "increments": 7, "initial_increment": 0.12345,
    "min_increment": 1.2345e-5, "fixed_increments": True,
}  # fmt: skip


def setting_cells() -> list[Cell]:
    """Each shared setting x each step kind (and a modal transient, a coupled step), and
    CalculiX's pass-through cards after the procedure."""
    from puzzle.adapters.settings import SHARED

    out = []
    for solver in ADAPTERS:
        if solver == "pynite":
            continue
        cases = []
        for physics in ("solid_mechanics", "heat_transfer", "fluid_flow"):
            cases += [Case(solver, physics, s) for s in _steps(solver, physics)]
        if "transient" in _steps(solver, "solid_mechanics"):
            cases.append(Case(solver, "solid_mechanics", "transient", variant="modal"))
        if "thermal_expansion" in capabilities(solver).couplings:
            cases.append(Case(solver, "solid_mechanics", "stationary", variant="coupled"))
        for physics in ("solid_mechanics", "heat_transfer"):
            if "stationary" in _steps(solver, physics) and solver != "opencourant":
                cases.append(Case(solver, physics, "stationary", variant="nonlinear"))
        for case in cases:
            out += [Cell(case, "setting", s.key) for s in SHARED]
            if solver == "calculix":
                out.append(Cell(case, "expert", "after_procedure"))
    return out


COUPLED = {  # coupling -> the physics of its base case, and the one it couples to
    "thermal_expansion": ("solid_mechanics", "heat_transfer"),
    "joule_heating": ("electrostatics", "heat_transfer"),
    "non_isothermal_flow": ("fluid_flow", "heat_transfer"),
    "electromagnetic_force": ("magnetic_fields", "solid_mechanics"),
}


def coupling_cells() -> list[Cell]:
    out = []
    for solver in ADAPTERS:
        caps = capabilities(solver)
        for coupling, (first, second) in COUPLED.items():
            if first not in caps.physics or second not in caps.physics:
                continue
            out.append(Cell(Case(solver, first, "stationary", variant="pair"), "coupling",
                            coupling))  # fmt: skip
    return out


def spring_cells() -> list[Cell]:
    """Weak springs (ADR 0080) asked for always, on a body held only by a frictionless
    contact."""
    return [Cell(Case(s, "solid_mechanics", "stationary", "split10"), "weak_springs", "always")
            for s in ("calculix", "code_aster", "elmer", "fenicsx")]  # fmt: skip


def shape_cells() -> list[Cell]:
    """Magnetics on hexahedra and quadrilaterals (EDDY-1): its element tables are for
    tetrahedra and triangles, so other shapes must be refused."""
    out = []
    for solver in ("elmer", "fenicsx"):
        for step in ("stationary", "frequency_domain"):
            out.append(Cell(Case(solver, "magnetic_fields", step, "hex8"), "shape", "hex8"))
        out.append(Cell(Case(solver, "magnetic_fields", "frequency_domain", "planar_quad"),
                        "shape", "quad"))  # fmt: skip
    return out


def all_cells() -> list[Cell]:
    return (feature_cells() + beam_cells() + planar_cells() + model_cells() + contact_cells()
            + fastener_cells() + thermal_cells() + setting_cells() + coupling_cells()
            + spring_cells() + shape_cells())  # fmt: skip


# -- what each slice adds ----------------------------------------------------------------


def _material_with(project, comp, models: dict, base_material="structural_steel"):
    from puzzle.materials import add_library_material
    from puzzle.model.schema import MaterialModels

    m = add_library_material(project, base_material)
    m.models = MaterialModels(**models)
    comp.materials = [a.model_copy(update={"material": m.id}) for a in comp.materials]
    return m


def _model(name: str, turned: bool = False) -> dict:
    from puzzle.model.schema import (
        AnisotropicConductivity,
        Failure,
        HardeningCurve,
        Hyperelasticity,
        JohnsonCook,
        OrthotropicElasticity,
        Plasticity,
    )

    axis1, axis2 = (
        ((0.0, 1.0, 0.0), (-1.0, 0.0, 0.0)) if turned else ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    )
    return {
        "plasticity": {"plasticity": Plasticity(curves=[HardeningCurve(
            points=[(0.0, 2.5e8), (0.1, 3.0e8)])])},
        "orthotropic_elasticity": {"orthotropic_elasticity": OrthotropicElasticity(
            e1=2e11, e2=1e11, e3=1.5e11, nu12=0.3, nu13=0.25, nu23=0.2, g12=5e10, g13=6e10,
            g23=4e10, axis1=axis1, axis2=axis2)},
        "hyperelasticity": {"hyperelasticity": Hyperelasticity(c10=1e6, d1=1e-8)},
        "johnson_cook": {"johnson_cook": JohnsonCook(a=2.5e8, b=1e8)},
        "failure": {"plasticity": Plasticity(curves=[HardeningCurve(
            points=[(0.0, 2.5e8), (0.1, 3.0e8)])]), "failure": Failure(strain=0.3)},
        "anisotropic_conductivity": {"anisotropic_conductivity": AnisotropicConductivity(
            k1=50, k2=20, k3=10, axis1=axis1, axis2=axis2)},
    }[name]  # fmt: skip


def _contact(name: str):
    from puzzle.model import Contact

    if name == "self_contact":
        return Contact(name="c", type="frictionless", source="Right/Side", target="Right/Side")
    kind = {"bonded_nonconformal": "bonded"}.get(name, name)
    extra = {"friction": 0.2} if kind == "frictional" else {}
    if kind == "bonded":
        extra["meshing"] = "nonconformal"
    return Contact(name="c", type=kind, source="Right/Start", target="Left/End", **extra)


def _fastener(project, comp, item: str):
    from puzzle.model.schema import BeamFastener

    steel = comp.materials[0].material
    comp.connectors.append(BeamFastener(
        name="b", source="Left/End", target="Right/Start", diameter=q(5, "mm"), material=steel,
        ends=item.split("+")[0],
        pretension=q(1234.5, "N") if "pretension" in item else None))  # fmt: skip


def _weld(project, comp):
    from puzzle.model.schema import Weld

    comp.welds.append(Weld(name="w", leg=q(3, "mm"), path="Left/EndTop",
                           faces=["Left/Top", "Right/Top"]))  # fmt: skip


def run_cell(world: World, c: Cell) -> Outcome:
    """The cell's outcome."""
    case = c.case
    if c.what == "base":
        return base(world, case)
    place = world.place(case.where)
    if c.what == "feature":
        return cell(
            world,
            case,
            add_feature(
                case.physics, c.item, catalog_selection(place, case.physics, c.item, case.solver)
            ),
        )
    if c.what == "kind":
        return cell(world, case, add_feature(case.physics, c.item, place.kinds[c.kind]))
    if c.what == "model":
        return cell(world, case, lambda p, comp, s: _material_with(p, comp, _model(c.item)))
    if c.what == "directions":  # the same material, its axes turned
        return cell(
            world,
            case,
            lambda p, comp, s: _material_with(p, comp, _model(c.item, turned=True)),
            lambda p, comp, s: _material_with(p, comp, _model(c.item)),
        )
    if c.what == "contact":
        return cell(world, case, lambda p, comp, s: comp.contacts.append(_contact(c.item)))
    if c.what == "fastener":
        return cell(world, case, lambda p, comp, s: _fastener(p, comp, c.item))
    if c.what == "weld":
        return cell(world, case, lambda p, comp, s: _weld(p, comp))
    if c.what == "thermal":
        from puzzle.model import Feature

        def thing(p, comp, s):
            if c.item == "fastener":
                _fastener(p, comp, "rigid")
            elif c.item == "weld":
                _weld(p, comp)
            else:
                _physics_of(comp, "solid_mechanics").features.append(Feature(
                    type="bolt_pretension", selection="Bar/Section",
                    values={"F": q(1000, "N")}))  # fmt: skip

        def heated(p, comp, s):
            thing(p, comp, s)
            body = "Bar/Body" if case.where == "tet10" else "Left/Body"
            _physics_of(comp, "solid_mechanics").features.append(
                Feature(type="body_temperature", selection=body, values={"T": q(123.45, "degC")})
            )

        return cell(world, case, heated, thing)
    if c.what == "setting":

        def setting(p, comp, s):
            s.steps[0].settings[c.item] = SETTING_VALUES[c.item]

        return cell(world, case, setting)
    if c.what == "expert":

        def expert(p, comp, s):
            s.expert_settings = {"calculix": {"after_procedure": "*CONTROLS, PARAMETERS=LINE "
                                              "SEARCH\n1.2345, 0.25"}}  # fmt: skip

        return cell(world, case, expert)
    if c.what == "coupling":
        first, second = COUPLED[c.item]

        def pair(p, comp, s):
            from puzzle.model import Physics

            if not any(ph.type == second for ph in comp.physics):
                extra = Physics(type=second, features=list(place.base.get(second, [])),
                                settings={"temperature_dependent_materials": False}
                                if second == "heat_transfer" else {})  # fmt: skip
                if second == "heat_transfer" and first == "fluid_flow":
                    from puzzle.model import Feature

                    extra.features = [Feature(type="temperature", selection="Bar/Root",
                                              values={"T": q(300, "K")})]  # fmt: skip
                comp.physics.append(extra)

        def coupled(p, comp, s):
            pair(p, comp, s)
            comp.couplings = [c.item]

        return cell(world, case, coupled, pair)
    if c.what == "weak_springs":
        from puzzle.model import Feature

        def contact_only(p, comp, s):
            # Right held only through a frictionless contact with Left
            ph = _physics_of(comp, "solid_mechanics")
            ph.features = [f for f in ph.features if f.selection != "Right/End"]
            ph.features.append(Feature(type="force", selection="Right/End",
                                       values={"x": q(-1, "N")}))  # fmt: skip
            comp.contacts.append(_contact("frictionless"))

        def springs(p, comp, s):
            contact_only(p, comp, s)
            s.weak_springs = "always"

        def none(p, comp, s):
            contact_only(p, comp, s)
            s.weak_springs = "never"

        return cell(world, case, springs, none)
    if c.what == "shape":
        return base(world, case)
    raise ValueError(c.what)


def supported_base(case: Case) -> bool:
    """Whether the adapter claims the case's physics and step kind."""
    return case.step in capabilities(case.solver).physics.get(case.physics, frozenset())


def table(outcomes: dict) -> str:
    """The matrix as a Markdown table: a row per (adapter, physics, what is added), a
    column per step kind; each cell written, refused, exempt (by design no change), or
    the failure (silent, error)."""
    rows: dict[tuple, dict[str, str]] = {}
    for c, o in outcomes.items():
        if c.what == "base":
            continue
        item = c.item + (f" on {c.kind}" if c.kind else "")
        if c.case.place:
            item += f" ({c.case.place})"
        if c.case.variant:
            item += f" [{c.case.variant}]"
        key = (c.case.solver, c.case.physics, c.what, item)
        word = o.kind
        if word == "silent" and c.exemption():
            word = "exempt"
        rows.setdefault(key, {})[c.case.step] = word
    lines = [
        "| adapter | physics | what | " + " | ".join(STEP_TYPES) + " |",
        "| --- | --- | --- | " + " | ".join("---" for _ in STEP_TYPES) + " |",
    ]
    for (solver, physics, what, item), steps in sorted(rows.items()):
        name = item if what in ("feature", "kind") else f"{what}: {item}"
        lines.append(f"| {solver} | {physics} | {name} | "
                     + " | ".join(steps.get(s, "") for s in STEP_TYPES) + " |")  # fmt: skip
    return "\n".join(lines) + "\n"
