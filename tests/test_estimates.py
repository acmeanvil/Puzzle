"""Error estimates (ADR 0066 phase 2) on a unit cube cut into n³ cubes of six
tetrahedra each, with fields known exactly (each a nodal interpolant):

- a linear displacement or temperature: the stress or flux is constant, so the
  estimate and the jumps are zero;
- u = (x², 0, 0) on linear tets: the ZZ estimate against the exact energy-norm error
  ∫ (σ − σ_h)ᵀ C (σ − σ_h) (effectivity near 1), halving with h (order 1);
- a cubic field on quadratic tets: order 2;
- two materials: a jump in conductivity with a matching flux gives no false error at
  the interface, since recovery is per material."""

from __future__ import annotations

import numpy as np
import pytest

from puzzle.mesh.mesh import CellBlock, Mesh
from puzzle.post import estimates as est

KUHN = [(0, 1, 3, 7), (0, 1, 5, 7), (0, 2, 3, 7), (0, 2, 6, 7), (0, 4, 5, 7), (0, 4, 6, 7)]
E, NU, K = 200e9, 0.3, 50.0


def cube(n: int, quadratic: bool = False, split_at: float | None = None) -> Mesh:
    g = np.linspace(0, 1, n + 1)
    pts = np.array([[x, y, z] for z in g for y in g for x in g])

    def node(i, j, k):
        return i + (n + 1) * (j + (n + 1) * k)

    tets = []
    for k in range(n):
        for j in range(n):
            for i in range(n):
                c = [node(i + (b & 1), j + (b >> 1 & 1), k + (b >> 2 & 1)) for b in range(8)]
                for t in KUHN:
                    q = [c[v] for v in t]
                    a = pts[q]
                    if np.linalg.det(a[1:] - a[0]) < 0:
                        q[1], q[2] = q[2], q[1]
                    tets.append(q)
    tets = np.array(tets)
    if quadratic:
        mids: dict[tuple, int] = {}
        extra = []
        out = []
        for t in tets:
            row = list(t)
            for a, b in est.EDGES10:
                key = (min(t[a], t[b]), max(t[a], t[b]))
                if key not in mids:
                    mids[key] = len(pts) + len(extra)
                    extra.append((pts[t[a]] + pts[t[b]]) / 2)
                row.append(mids[key])
            out.append(row)
        pts = np.vstack([pts, extra])
        tets = np.array(out)
    ids = np.arange(1, len(pts) + 1)
    element = "tet10" if quadratic else "tet4"
    if split_at is None:
        blocks = [CellBlock(element, 3, (3, 1), np.arange(1, len(tets) + 1), tets + 1)]
    else:
        left = pts[tets[:, :4]].mean(axis=1)[:, 0] < split_at
        blocks = [CellBlock(element, 3, (3, tag), np.flatnonzero(sel) + 1, tets[sel] + 1)
                  for tag, sel in ((1, left), (2, ~left))]  # fmt: skip
    return Mesh(ids, pts, blocks)


def nodal(mesh, fn):
    vals = np.array([fn(*p) for p in mesh.coords])
    vals = vals.reshape(len(mesh.coords), -1)
    order = np.argsort(mesh.node_ids)
    return lambda ids: vals[order[np.searchsorted(mesh.node_ids, ids, sorter=order)]]


def exact_error(mesh, values_at, exact_grad, physics, mat):
    """∫ (flux − flux_h)ᵀ C (flux − flux_h) over the mesh, by the 32-point rule."""
    pos = {int(n): i for i, n in enumerate(mesh.node_ids)}
    total = 0.0
    for b in mesh.blocks:
        conn = np.vectorize(pos.__getitem__)(b.connectivity)
        x = mesh.coords[conn]
        u = values_at(b.connectivity.reshape(-1)).reshape(*b.connectivity.shape, -1)
        n_q, dn_q = est.shape(b.element, est.GAUSS32)
        g, det = est._gradients(x, dn_q)
        m = np.tile(np.asarray(mat, float), (len(conn), 1))[:, None, :]
        fh = est._flux(physics, np.einsum("enc,eqnj->eqcj", u, g), m)
        xq = np.einsum("qn,eni->eqi", n_q, x)
        ge = np.apply_along_axis(lambda p: exact_grad(*p).reshape(-1), 2, xq)
        fe = est._flux(physics, ge.reshape(*xq.shape[:2], -1, 3), m)
        total += float((est._energy(physics, fe - fh, m) * det * est.W32).sum())
    return np.sqrt(total)


def test_a_linear_field_has_no_error():
    mesh = cube(3)
    got = est.estimate(mesh, nodal(mesh, lambda x, y, z: (1e-3 * x + 2e-3 * y, 0, 3e-4 * z)),
                       (E, NU))  # fmt: skip
    assert got.error.max() < 1e-9 * got.energy.max() and got.jump.max() < 1e-6
    heat = est.estimate(mesh, nodal(mesh, lambda x, y, z: 300 + 20 * x - 5 * z), (K,), "heat")
    assert heat.relative < 1e-9 and heat.order == 1


@pytest.mark.parametrize("n", [4, 8])
def test_zz_tracks_the_exact_error_on_linear_tets(n):
    mesh = cube(n)
    u = nodal(mesh, lambda x, y, z: (1e-3 * x**2, 0, 0))
    got = est.estimate(mesh, u, (E, NU))
    exact = exact_error(mesh, u, lambda x, y, z: np.array(
        [[2e-3 * x, 0, 0], [0, 0, 0], [0, 0, 0]]), "structural", (E, NU))  # fmt: skip
    zz = np.sqrt((got.error**2).sum())
    assert 0.7 < zz / exact < 1.3, zz / exact  # effectivity
    assert got.jump.sum() > 0


def test_orders_of_convergence():
    def rel(n, quadratic):
        mesh = cube(n, quadratic)
        if quadratic:
            fn = lambda x, y, z: (300 + 10 * x**3 + 5 * y**2 * z,)  # noqa: E731
        else:
            fn = lambda x, y, z: (300 + 10 * x**2,)  # noqa: E731
        return np.sqrt((est.estimate(mesh, nodal(mesh, fn), (K,), "heat").error ** 2).sum())

    lin = np.log2(rel(4, False) / rel(8, False))
    quad = np.log2(rel(2, True) / rel(4, True))
    assert lin == pytest.approx(1.0, abs=0.2)
    assert quad == pytest.approx(2.0, abs=0.35)


def test_an_interface_is_not_an_error():
    # k = 50 for x < 0.5, 100 beyond; T piecewise linear with a continuous flux −k dT/dx
    mesh = cube(4, split_at=0.5)

    def temp(x, y, z):
        return (300 + 20 * x,) if x <= 0.5 else (310 + 10 * (x - 0.5),)

    def k_of(block):
        return np.tile([50.0 if block.entity[1] == 1 else 100.0], (len(block.ids), 1))

    got = est.estimate(mesh, nodal(mesh, temp), k_of, "heat")
    assert got.relative < 1e-9 and got.jump.max() < 1e-6


def test_ratio_sizes_and_fields():
    mesh = cube(4)
    got = est.estimate(mesh, nodal(mesh, lambda x, y, z: (1e-3 * x**2, 0, 0)), (E, NU))
    r = got.ratio(got.relative)  # the current error as the target: ratio 1 on average
    assert np.sqrt((r**2).mean()) == pytest.approx(1.0, rel=0.02)
    finer = got.sizes(got.relative / 2)
    assert (finer < got.size).mean() > 0.9  # half the error: smaller everywhere, nearly
    fields = got.fields(0.02)
    assert fields["error_estimate"].location == "element"
    assert fields["error_relative"].values[0, 0] == pytest.approx(got.relative)


def test_any_field_gets_a_relative_indicator():
    mesh = cube(4)
    got = est.estimate(mesh, nodal(mesh, lambda x, y, z: (x**2, y)), (1.0,), "field")
    assert got.relative > 0 and got.jump.sum() > 0
    flat = est.estimate(mesh, nodal(mesh, lambda x, y, z: (x, 2 * y)), (1.0,), "field")
    assert flat.relative < 1e-9


def test_hot_spots_and_words():
    from puzzle.mesh.mesh import Group

    mesh = cube(4)
    far = np.flatnonzero(np.isclose(mesh.coords[:, 0], 1.0))
    mesh.groups["B/End"] = Group("B/End", 2, [(2, 1)], mesh.node_ids[far])
    got = est.estimate(mesh, nodal(mesh, lambda x, y, z: (1e-3 * x**4, 0, 0)), (E, NU))
    spots = est.hot_spots(mesh, got, 3)
    assert spots[0]["error"] == got.error.max() and spots[0]["centre"][0] > 0.7
    assert spots[0]["selections"] == ["B/End"]  # u = x⁴ curves most at x = 1
    text = est.words(mesh, got, 0.02)
    assert text.startswith("error ") and "largest at B/End: refine there" in text
    s = est.summary(mesh, got)
    assert s["too_big"] > 0 and len(s["hot_spots"]) == 5


def test_a_derived_error_estimate_and_the_advisor(tmp_path, monkeypatch):
    import json
    from datetime import datetime

    from test_convergence import Store

    from puzzle import advisor
    from puzzle.materials import add_library_material
    from puzzle.mesh.mesh import Group
    from puzzle.model.schema import (
        Component,
        Geometry,
        MaterialAssignment,
        MeshRecipe,
        PartInstance,
        Project,
        Quantity,
        ResultDefinition,
        Study,
    )  # fmt: skip
    from puzzle.post import derived
    from puzzle.results.model import Field, Results, StepResult
    from puzzle.results.store import RunRecord

    p = Project("p")
    geo = p.add(Geometry(name="G", source="g.step"))
    steel = add_library_material(p, "structural_steel")
    mesh_recipe = p.add(MeshRecipe(name="M", global_size=Quantity(value=6, unit="mm")))
    comp = p.add(Component(name="C", parts=[PartInstance(name="B", geometry=geo.id)],
                           mesh=mesh_recipe.id,
                           materials=[MaterialAssignment(material=steel.id,
                                                         selection="B/Body")]))  # fmt: skip
    study = p.add(Study(name="S", component=comp.id, solver="calculix", steps=[]))
    mesh = cube(4)
    mesh.groups["B/Body"] = Group("B/Body", 3, [(3, 1)], mesh.node_ids)
    far = np.flatnonzero(np.isclose(mesh.coords[:, 0], 1.0))
    mesh.groups["B/End"] = Group("B/End", 2, [(2, 1)], mesh.node_ids[far])
    u = np.zeros((len(mesh.coords), 3))
    u[:, 0] = 1e-3 * mesh.coords[:, 0] ** 4
    store = Store()
    store.meshes["m"] = mesh
    store.results["r1"] = Results(
        "calculix",
        [
            StepResult(
                1,
                "stationary",
                1.0,
                fields={
                    "displacement": Field("displacement", "node", ["x", "y", "z"], mesh.node_ids, u)
                },
            )
        ],
    )
    when = datetime(2026, 11, 4)
    store.records.append(RunRecord("r1", "k", str(study.id), "S", "calculix", "native", "m",
                                   "completed", when, 1.0, 1, when, json.dumps({})))  # fmt: skip
    d = p.add(ResultDefinition(name="Error", method="error_estimate", source=study.id))
    got = derived.evaluate(p, store, d)
    labels = [i.label for i in got.items]
    assert (
        "Error estimate (energy norm)" in labels and "Refinement ratio (above 1: refine)" in labels
    )
    assert any(w.startswith("relative error") for w in got.warnings)
    assert got.results.steps[0].fields["error_estimate"].location == "element"
    monkeypatch.setattr("puzzle.results.store.ResultStore", lambda *_: store)
    text = advisor._estimate(p, study, tmp_path)
    assert text.startswith("Error ") and "largest at B/End" in text
