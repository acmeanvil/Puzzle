"""A section of a 3D part as a 2D part (ADR 0048): the cut follows the 3D part, its
edges are named after the 3D faces they cut, and changing the part stales it."""

import pytest

pytest.importorskip("OCP")

from puzzle.geometry.history import GeometryError, signature  # noqa: E402
from puzzle.geometry.naming import named_geometry, props  # noqa: E402
from puzzle.model import Geometry, Project  # noqa: E402
from puzzle.model.schema import HistoryStep  # noqa: E402


def _tube(r=0.02):
    return [
        HistoryStep(kind="cylinder", name="Tube", args={"r": r, "h": 0.05, "axis": "y"}),
        HistoryStep(kind="hole", name="Bore", args={"r": 0.01, "x": 0, "y": 0, "axis": "y"}),
    ]


def _project(**section):
    p = Project(name="section")
    tube = p.add(Geometry(name="Tube", history=_tube()))
    args = {"part": str(tube.id), "plane": "xy", "half": True} | section
    cut = p.add(Geometry(name="Cut", history=[HistoryStep(kind="section", name="Cut", args=args)]))
    return p, tube, cut


def test_a_half_section_through_the_axis(tmp_path):
    p, _tube_geo, cut = _project()
    named = named_geometry(p, cut, tmp_path)
    [face] = named.of["face"]["Cut/face"]
    assert signature(face)[1] == pytest.approx(0.010 * 0.05, rel=1e-9)
    edges = named.of["edge"]
    for name, x in (("Cut/Tube/wall", 0.02), ("Cut/Bore/wall", 0.01)):
        [e] = edges[name]
        assert props("edge", e)[2][0] == pytest.approx(x)
    assert props("edge", edges["Cut/Tube/end"][0])[2][1] == pytest.approx(0.05)
    assert all(abs(signature(f)[2][2]) < 1e-12 for f in named.of["face"]["Cut/face"])


def test_the_section_follows_the_part_and_is_staled_by_it(tmp_path):
    from puzzle.project.graph import content_hashes

    p, tube, cut = _project()
    before = content_hashes(p)[cut.id]
    tube.history = _tube(r=0.03)
    named = named_geometry(p, cut, tmp_path)
    assert signature(named.of["face"]["Cut/face"][0])[1] == pytest.approx(0.02 * 0.05)
    assert content_hashes(p)[cut.id] != before


def test_a_whole_section_and_another_plane(tmp_path):
    p, _t, cut = _project(half=False)
    area = sum(signature(f)[1] for f in named_geometry(p, cut, tmp_path).of["face"]["Cut/face"])
    assert area == pytest.approx(2 * 0.010 * 0.05)
    # x = 0 holds the tube's axis along its u (y), so it cuts both walls: (y, z) become
    # (x, y), two strips at y = +-15 mm.
    p, _t, cut = _project(plane="yz", half=True)
    faces = named_geometry(p, cut, tmp_path).of["face"]["Cut/face"]
    centres = sorted(signature(f)[2] for f in faces)
    assert [c[0] for c in centres] == pytest.approx([0.025, 0.025])
    assert [c[1] for c in centres] == pytest.approx([-0.015, 0.015])


def test_a_section_of_itself_or_nothing_is_refused(tmp_path):
    p, _t, cut = _project()
    cut.history[0].args["part"] = str(cut.id)
    with pytest.raises(GeometryError, match="section of itself"):
        named_geometry(p, cut, tmp_path)
    cut.history[0].args["part"] = ""
    with pytest.raises(GeometryError, match="names the part"):
        named_geometry(p, cut, tmp_path)
