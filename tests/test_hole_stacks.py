"""Fasteners from holes and the bolt generator on stacks of more than two layers
(ADR 0027, 0050): coaxial holes in separate parts, or in several bodies of one part (a
STEP assembly imported as one part), become one fastener from the first hole to the
last, the holes between passed through."""

from __future__ import annotations

import pytest

pytest.importorskip("OCP")

from puzzle import bolt_generator as gen  # noqa: E402
from puzzle.commands import Session  # noqa: E402
from puzzle.geometry.holes import fastener_commands, find_hole_pairs  # noqa: E402
from puzzle.materials import add_library_material  # noqa: E402
from puzzle.model.schema import (  # noqa: E402
    BoltRule,
    Component,
    Geometry,
    HistoryStep,
    MaterialAssignment,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Project,
    Quantity,
)
from puzzle.project import save_project  # noqa: E402

T = 0.01  # each plate's thickness, m
R = 0.0033  # the holes' radius (6.6 mm M6 clearance holes)


def _parts_project(tmp_path, layers: int):
    """`layers` plates of one history geometry stacked as parts P1 (bottom) .. Pn."""
    p = Project(name="stack")
    steel = add_library_material(p, "structural_steel")
    plate = p.add(
        Geometry(
            name="Plate",
            history=[
                HistoryStep(kind="box", name="P", args={"dx": 0.05, "dy": 0.05, "dz": T}),
                HistoryStep(kind="hole", name="H", args={"r": R, "x": 0.025, "y": 0.025}),
            ],
            selections=[NamedSelection(name="Body", entity="body", members=["Body"])],
        )
    )
    mesh = p.add(MeshRecipe(name="M", global_size=Quantity(value=5, unit="mm")))
    names = [f"P{i}" for i in range(1, layers + 1)]
    p.add(
        Component(
            name="Stack",
            parts=[
                PartInstance(name=n, geometry=plate.id, translation=(0.0, 0.0, i * T))
                for i, n in enumerate(names)
            ],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=steel.id, selection=f"{n}/Body") for n in names],
        )
    )
    root = tmp_path / f"parts{layers}"
    save_project(p, root)
    return root, steel


def _bodies_project(tmp_path, gaps: list[float]):
    """One STEP part of plates with a coaxial hole each, as separate solids (bodies)
    stacked along z; `gaps[k]` is the clearance between plate k and plate k + 1."""
    import gmsh

    from puzzle.geometry import gmsh_session

    root = tmp_path / f"bodies{len(gaps) + 1}"
    root.mkdir()
    with gmsh_session("stack"):
        z = 0.0
        for k in range(len(gaps) + 1):
            box = gmsh.model.occ.addBox(0, 0, z, 0.05, 0.05, T)
            pin = gmsh.model.occ.addCylinder(0.025, 0.025, z - T, 0, 0, 3 * T, R)
            gmsh.model.occ.cut([(3, box)], [(3, pin)])
            z += T + (gaps[k] if k < len(gaps) else 0.0)
        gmsh.model.occ.synchronize()
        gmsh.write(str(root / "stack.step"))
    p = Project(name="assembly")
    steel = add_library_material(p, "structural_steel")
    bodies = [f"body-{i}" for i in range(1, len(gaps) + 2)]
    geo = p.add(
        Geometry(
            name="Assembly",
            source="stack.step",
            selections=[NamedSelection(name="Body", entity="body", members=bodies)],
        )
    )
    mesh = p.add(MeshRecipe(name="M", global_size=Quantity(value=5, unit="mm")))
    p.add(
        Component(
            name="Assembly",
            parts=[PartInstance(name="Assembly", geometry=geo.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=steel.id, selection="Assembly/Body")],
        )
    )
    save_project(p, root)
    return root, steel


def _comp(session):
    return session.project.of_type(Component)[0]


@pytest.mark.parametrize("layers", [3, 4])
def test_a_stack_of_parts_is_one_fastener_through_the_middle_holes(tmp_path, layers):
    root, steel = _parts_project(tmp_path, layers)
    s = Session(root)
    comp = _comp(s)
    (one,) = find_hole_pairs(s.project, comp, root)
    assert (one.source_part, one.target_part) == ("P1", f"P{layers}")
    assert one.through == [(f"P{i}", "H/wall") for i in range(2, layers)]
    assert one.centre_distance == pytest.approx((layers - 1) * T)
    assert one.diameter == pytest.approx(2 * R)
    s.submit_batch(fastener_commands(s.project, comp, [one], str(steel.id)))
    (bolt,) = _comp(s).connectors
    assert (bolt.source, bolt.target) == ("P1/Hole 1", f"P{layers}/Hole 1")
    # One geometry shared by the parts: each middle part's hole the same face, one selection.
    assert bolt.through == [f"P{i}/Stack hole 1" for i in range(2, layers)]


@pytest.mark.parametrize("layers", [3, 4])
def test_a_stack_of_bodies_in_one_part_is_one_fastener(tmp_path, layers):
    """The maintainer's case: a pressure vessel assembly imported as one STEP part."""
    root, steel = _bodies_project(tmp_path, [0.0] * (layers - 1))
    s = Session(root)
    comp = _comp(s)
    (one,) = find_hole_pairs(s.project, comp, root)
    assert one.source_part == one.target_part == "Assembly"
    assert len(one.through) == layers - 2
    assert one.centre_distance == pytest.approx((layers - 1) * T)
    s.submit_batch(fastener_commands(s.project, comp, [one], str(steel.id)))
    (bolt,) = _comp(s).connectors
    assert (bolt.source, bolt.target) == ("Assembly/Hole 1", "Assembly/Hole 2")
    assert bolt.through == [f"Assembly/Stack hole {n}" for n in range(1, layers - 1)]
    geometry = s.project.of_type(Geometry)[0]
    faces = {sel.name: sel.members for sel in geometry.selections}
    ends = {faces["Hole 1"][0], faces["Hole 2"][0]}
    assert len(ends) == 2 and not ends & {faces[f"Stack hole {n}"][0] for n in range(1, layers - 1)}
    # The bolt generator finds the same stack (no `same_part` needed: different bodies).
    (stack,) = gen.by_rule(s.project, comp, root, BoltRule(name="All"))
    assert len(stack.through) == layers - 2


def test_two_joints_on_one_axis_stay_two_fasteners(tmp_path):
    """Plates 1-2 and 3-4 on one axis, 30 mm apart (more than a hole diameter): two."""
    root, _ = _bodies_project(tmp_path, [0.0, 0.03, 0.0])
    s = Session(root)
    pairs = find_hole_pairs(s.project, _comp(s), root)
    assert len(pairs) == 2 and all(p.through == [] for p in pairs)
    assert all(p.centre_distance == pytest.approx(T) for p in pairs)


def test_a_small_gap_between_layers_still_stacks(tmp_path):
    """A 2 mm gap (a gasket left out of the model) is less than the hole: one stack."""
    root, _ = _bodies_project(tmp_path, [0.002, 0.0])
    s = Session(root)
    (one,) = find_hole_pairs(s.project, _comp(s), root)
    assert len(one.through) == 1
