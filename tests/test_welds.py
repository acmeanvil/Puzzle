"""The Weld object (ADR 0078): its checks, and that a component without welds keeps
its content hash."""

import pytest
from pydantic import ValidationError

from puzzle.model.schema import Component, MeshRecipe, PartInstance, Project, Quantity, Weld
from puzzle.project.graph import _own

q = lambda v, u: Quantity(value=v, unit=u)  # noqa: E731


def test_a_weld_says_what_it_lacks():
    Weld(name="W", leg=q(6, "mm"), path="A/E", faces=["A/x", "B/y"])
    with pytest.raises(ValidationError, match="segment length and pitch"):
        Weld(name="W", leg=q(6, "mm"), path="A/E", faces=["A/x", "B/y"],
             pattern="intermittent")  # fmt: skip
    with pytest.raises(ValidationError, match="CJP or PJP"):
        Weld(name="W", type="groove", leg=q(6, "mm"), path="A/E", faces=["A/x", "B/y"])
    with pytest.raises(ValidationError):
        Weld(name="W", leg=q(6, "mm"), path="A/E", faces=["A/x"])  # one face


def test_no_welds_keeps_the_hash():
    p = Project(name="p")
    mesh = p.add(MeshRecipe(name="M"))
    from uuid import uuid4

    comp = Component(name="C", mesh=mesh.id, parts=[PartInstance(name="A", geometry=uuid4())])
    assert "welds" not in _own(comp)
    comp.welds = [Weld(name="W", leg=q(6, "mm"), path="A/E", faces=["A/x", "B/y"])]
    assert _own(comp)["welds"][0]["name"] == "W"


def test_other_solvers_refuse_a_weld():
    from puzzle.adapters import available_adapters

    adapters = available_adapters()
    caps = {name: adapters[name]().capabilities.welds for name in adapters}
    writers = {"calculix", "code_aster", "fenicsx"}  # ADR 0078 step 3
    assert all(caps[k] == {"line"} for k in writers if k in caps)
    assert all(not v for k, v in caps.items() if k not in writers)


def test_a_legs_fit_follows_a_rigid_motion():
    """ADR 0078 (2026-11-14): a station's springs act on each leg's rigid fit, so a rigid
    motion of both legs stretches none of them (force and moment pass exactly), and the
    fit's turn is the motion's."""
    import numpy as np

    from puzzle.adapters.weld_pairs import _fit, _hat

    rng = np.random.default_rng(3)
    xyz = rng.random((12, 3)) * [0.01, 0.006, 0.0]  # a leg's nodes on a face
    w = rng.random(12) + 0.5
    point = np.array([0.004, 0.001, 0.002])
    A, B = _fit(xyz, w, point)
    a, omega = np.array([1e-4, -2e-4, 3e-5]), np.array([0.01, -0.02, 0.005])
    u = a + np.cross(omega, xyz)
    assert np.einsum("nij,nj->i", B, u) == pytest.approx(omega, rel=1e-9)
    assert np.einsum("nij,nj->i", A, u) == pytest.approx(a + np.cross(omega, point), rel=1e-9)
    assert _fit(np.column_stack([np.linspace(0, 1, 5), np.zeros(5), np.zeros(5)]),
                np.ones(5), point) is None  # nodes on a line: no turn about it  # fmt: skip
    s = np.array([0.0, 0.5, 1.0, 1.5, 2.0])
    assert _hat(s, np.array([0.0, 1.0, 2.0]), 1, False, 2.0) == pytest.approx([0, 0.5, 1, 0.5, 0])
    assert _hat(s, np.array([0.0, 1.0, 2.0]), 0, False, 2.0) == pytest.approx([1, 0.5, 0, 0, 0])
