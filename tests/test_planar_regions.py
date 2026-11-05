"""ADR 0048: a 2D part's touching faces as separate regions (history mode "separate"),
each its own mesh region, sharing the nodes of the edge between them."""

import pytest

from puzzle.materials import add_library_material
from puzzle.mesh import generate_mesh
from puzzle.model import (
    Component,
    Geometry,
    MaterialAssignment,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Project,
    Quantity,
)
from puzzle.model.schema import HistoryStep

pytest.importorskip("OCP")


def _rect(name, x0, x1, mode=None):
    args = {"profile": {"points": [[x0, 0], [x1, 0], [x1, 0.01], [x0, 0.01]]}}
    if mode:
        args["mode"] = mode
    return HistoryStep(kind="face", name=name, args=args)


@pytest.mark.parametrize("mode", ["separate", "add"])
def test_separate_faces_stay_regions(tmp_path, mode):
    p = Project(name="regions")
    geo = p.add(Geometry(name="Slice", history=[_rect("A", 0, 0.01), _rect("B", 0.01, 0.03, mode)],
                         selections=[NamedSelection(name="Left", entity="face", members=["A/face"]),
                                     NamedSelection(name="Right", entity="face",
                                                    members=["B/face"])]))  # fmt: skip
    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="M", global_size=Quantity(value=2, unit="mm")))
    comp = p.add(Component(name="C", dimension="2d_planar", mesh=mesh.id,
                           parts=[PartInstance(name="Slice", geometry=geo.id)],
                           materials=[MaterialAssignment(material=steel.id,
                                                         selection="Slice/Left"),
                                      MaterialAssignment(material=steel.id,
                                                         selection="Slice/Right")]))  # fmt: skip
    m = generate_mesh(p, comp.id, tmp_path, tmp_path / "m").mesh
    left, right = (m.groups[f"Slice/{n}"] for n in ("Left", "Right"))
    if mode == "add":  # fused into one face: both names are the whole of it
        assert left.entities == right.entities
        return
    assert set(left.entities).isdisjoint(right.entities)
    shared = set(left.node_ids) & set(right.node_ids)
    assert len(shared) >= 6  # the edge x = 10 mm, meshed once for both
    xs = m.node_coords(sorted(shared))[:, 0]
    assert abs(xs - 0.01).max() < 1e-9
