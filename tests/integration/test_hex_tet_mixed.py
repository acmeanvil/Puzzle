"""Hexahedra and tetrahedra in one recipe (ADR 0019, ADR 0072 section 5): a hexahedral
recipe meshes every body that takes hexahedra with them and the rest with tetrahedra,
the two kinds joined apart and tied where they touch (a face's nodes can't be both:
sharing them would need pyramids, which CalculiX lacks).

A bar of two parts bonded end to end: a block (hexahedra) and a block with a hole
through it near its far end (tetrahedra), held at one end and pulled at the other. The
reaction is the pull, and the pulled end moves as it does with the whole bar in
tetrahedra (the tie is as stiff as shared nodes, to a fraction of a per cent).
"""

import numpy as np
import pytest
from solvers import over

from puzzle.materials import add_library_material
from puzzle.model import (
    Component,
    Contact,
    Feature,
    Geometry,
    MaterialAssignment,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Physics,
    Project,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.model.schema import HistoryStep
from puzzle.project import save_project
from puzzle.results.query import total
from puzzle.solve import run_study

q = lambda v, u: Quantity(value=v, unit=u)  # noqa: E731
P = 20e3  # N
W, L = 0.02, 0.05
FACES = ("-x", "+x", "-y", "+y", "-z", "+z")


def _geometry(p, name, hole):
    history = [HistoryStep(kind="box", name="B", args={"dx": W, "dy": W, "dz": L})]
    if hole:  # a hole across it, near the far end: not block-like, not a revolution
        history.append(HistoryStep(kind="cylinder", name="Hole", args={
            "r": 0.004, "h": 2 * W, "x": -W / 2, "y": W / 2, "z": 0.8 * L,
            "axis": [1, 0, 0], "mode": "cut"}))  # fmt: skip
    return p.add(Geometry(name=name, history=history, selections=[
        NamedSelection(name="Body", entity="body", members=["Body"]),
        *(NamedSelection(name=f, entity="face", members=[f"B/{f}"]) for f in FACES)]))  # fmt: skip


def _project(solver, shape):
    pytest.importorskip("OCP")
    p = Project(name="mixed")
    steel = add_library_material(p, "structural_steel")
    block, holed = _geometry(p, "Block", False), _geometry(p, "Holed", True)
    mesh = p.add(MeshRecipe(name="M", global_size=q(4, "mm"), element_shape=shape,
                            element_order=2))  # fmt: skip
    comp = p.add(Component(
        name="Bar",
        parts=[PartInstance(name="Block", geometry=block.id),
               PartInstance(name="Holed", geometry=holed.id, translation=(0.0, 0.0, L))],
        mesh=mesh.id,
        materials=[MaterialAssignment(material=steel.id, selection=f"{n}/Body")
                   for n in ("Block", "Holed")],
        contacts=[Contact(name="Joint", type="bonded", source="Holed/-z", target="Block/+z")],
        physics=[Physics(type="solid_mechanics",
                         settings={"temperature_dependent_materials": False}, features=[
            Feature(type="fixed", selection="Block/-z"),
            Feature(type="force", selection="Holed/+z", values={"z": q(P, "N")})])],
    ))  # fmt: skip
    study = p.add(Study(name="Pull", component=comp.id, solver=solver,
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    return p, study


def _run(tmp_path, solver, shape):
    p, study = _project(solver, shape)
    root = tmp_path / shape
    save_project(p, root)
    return run_study(p, study.id, root, tmp_path / f"w-{shape}", timeout_s=1800)


def _end(run) -> float:
    u = run.results.last("displacement")
    ids = np.unique(run.mesh.groups["Holed/+z"].node_ids)
    order = np.argsort(u.ids)
    at = order[np.searchsorted(u.ids, ids, sorter=order)]
    return float(np.mean(u.values[at, u.components.index("z")]))


@over("calculix", "code_aster")
def test_hexahedra_and_tetrahedra_tied_in_one_recipe(tmp_path, solver):
    mixed = _run(tmp_path, solver, "hexahedra")
    groups = {g.name: {b.element for b in g.blocks(mixed.mesh) if b.dim == 3}
              for g in mixed.mesh.groups.values() if g.name in ("Block", "Holed")}  # fmt: skip
    assert groups["Block"] == {"hex20"} and groups["Holed"] == {"tet10"}, groups
    assert "Holed/(mesh tie 1)" in mixed.mesh.groups
    assert "Block/(mesh tie 1 target)" in mixed.mesh.groups
    reaction = total(mixed.results, mixed.mesh, "reaction_force", "Block/-z")["total"][2]
    tets = _run(tmp_path, solver, "tetrahedra")
    stretch, reference = _end(mixed), _end(tets)
    off = 100 * (stretch / reference - 1)
    print(f"{solver} mixed bar: reaction {reaction:.6g} N (P {P:g}); end {stretch * 1e6:.5g} "
          f"um, all tetrahedra {reference * 1e6:.5g} um ({off:+.3f} %)")  # fmt: skip
    assert abs(reaction) == pytest.approx(P, rel=1e-4)
    assert stretch == pytest.approx(reference, rel=0.01)
