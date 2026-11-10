"""Modelled bolts meshed as bodies of revolution (ADR 0077 decision 3, `mesh.revolved`).

An ISO 4014 M16 × 50 hex bolt for a 30 mm grip (its pretension section at mid-grip),
drawn by `solid_bolts` as one `bolt` history step, placed tilted and moved. The mesher
revolves its meridian profile in 24 sectors: hexahedra, prisms only on the axis, one
conformal mesh (every free element face lies on the bolt's named faces), its volume
the OCC solid's within 0.5 %, and every named face found as its quarters.

Pulled by a pressure on its bearing face with its end held, the revolved mesh and a
tetrahedral one give the same axial stress away from the ends (within 2 %), and both
F / A of the thread's core within 1 %.
"""

import math

import numpy as np
import pytest
from solvers import over

from puzzle import solid_bolts
from puzzle.materials import add_library_material
from puzzle.model import Feature, Physics, Quantity, SolveStep, Study
from puzzle.model.schema import (
    Component,
    Geometry,
    HistoryStep,
    MaterialAssignment,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Project,
)

pytest.importorskip("OCP")

GRIP = 0.03
FACES = {"Bearing": "bearing", "Fillet": "fillet", "Shank": "shank", "Runout": "runout",
         "Thread": "thread", "End": "end", "Top": "head_top", "Side": "head_side",
         "Section": "section"}  # fmt: skip
PRESSURE = 50e6  # Pa on the bearing face, pushing the head away from the end


def _dimensions():
    return solid_bolts.dimensions("M16", "hex", length=0.05, grip=GRIP)


def _project(*, revolved=True, translation=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0),
             order=2, size=2.0, fine_thread=True):  # fmt: skip
    p = Project(name="bolt")
    dim = _dimensions()
    geo = p.add(Geometry(
        name="M16", history=[HistoryStep(kind="bolt", name="B", args=dim.args(grip=GRIP))],
        selections=[NamedSelection(name=n, entity="face", members=[f"B/{f}"])
                    for n, f in FACES.items()],
    ))  # fmt: skip
    controls = [{"kind": "size", "selection": "Bolt/Fillet",
                 "size": {"value": dim.fillet / 3 * 1e3, "unit": "mm"}}]  # fmt: skip
    if fine_thread:  # four elements a pitch, as `solid_bolts.make_commands` sets
        controls.append({"kind": "size", "selection": "Bolt/Thread",
                         "size": {"value": dim.pitch / 4 * 1e3, "unit": "mm"}})  # fmt: skip
    recipe = p.add(MeshRecipe(name="Mesh", element_order=order,
                              global_size=Quantity(value=size, unit="mm"),
                              revolved_bolts=revolved, controls=controls))  # fmt: skip
    part = PartInstance(name="Bolt", geometry=geo.id, translation=translation, rotation=rotation)
    comp = p.add(Component(name="Bolt", mesh=recipe.id, parts=[part]))
    return p, comp, geo


def _solid_volume(dim) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    from puzzle.geometry.history import Op, regenerate

    shape = regenerate([Op("bolt", "B", dim.args(grip=GRIP))]).shape
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


def _mesh_volume(path) -> float:
    """The volume of the written mesh by Gauss quadrature on its curved elements."""
    import gmsh

    from puzzle.geometry import gmsh_session

    total = 0.0
    with gmsh_session("volume"):
        gmsh.open(str(path))
        for t in gmsh.model.mesh.getElementTypes(3):
            local, weights = gmsh.model.mesh.getIntegrationPoints(t, "Gauss4")
            _, det, _ = gmsh.model.mesh.getJacobians(t, local)
            total += float((np.asarray(det).reshape(-1, len(weights)) @ weights).sum())
    return total


CORNERS = {"hex20": [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6),
                     (3, 0, 4, 7)],
           "wedge15": [(0, 1, 2), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (2, 0, 3, 5)]}  # fmt: skip


def test_a_bolt_meshes_as_a_body_of_revolution(tmp_path):
    from puzzle.mesh import generate_mesh

    p, comp, _ = _project(translation=(0.01, -0.02, 0.03), rotation=(30.0, 40.0, 10.0))
    result = generate_mesh(p, comp.id, tmp_path, tmp_path / "mesh")
    mesh = result.mesh
    assert not result.warnings
    solids = {b.element for b in mesh.blocks if b.dim == 3}
    assert solids == {"hex20", "wedge15"}
    counts = mesh.element_counts()
    assert counts["wedge15"] < 0.1 * counts["hex20"]  # prisms on the axis only
    assert counts["hex20"] % 24 == 0 and counts["wedge15"] % 24 == 0  # 24 sectors
    # Every named face has its group, and each holds surface elements.
    for name in FACES:
        group = mesh.groups[f"Bolt/{name}"]
        assert group.dim == 2 and len(group.blocks(mesh)), name
    # One conformal mesh: the faces used by one element only are exactly the elements
    # on the bolt's outer faces (all but the section, which lies inside).
    seen: dict[frozenset, int] = {}
    for b in mesh.blocks:
        for corners in CORNERS.get(b.element, []):
            for row in b.connectivity[:, corners]:
                key = frozenset(row.tolist())
                seen[key] = seen.get(key, 0) + 1
    free = {k for k, n in seen.items() if n == 1}
    outer = set()
    for name in FACES.keys() - {"Section"}:
        for b in mesh.groups[f"Bolt/{name}"].blocks(mesh):
            corners = 3 if b.element.startswith("tri") else 4
            outer |= {frozenset(row[:corners].tolist()) for row in b.connectivity}
    assert free == outer
    assert _mesh_volume(result.path) == pytest.approx(_solid_volume(_dimensions()), rel=5e-3)


def test_turned_off_it_meshes_tetrahedra(tmp_path):
    from puzzle.mesh import generate_mesh

    p, comp, _ = _project(revolved=False, order=1, size=4.0)
    mesh = generate_mesh(p, comp.id, tmp_path, tmp_path / "mesh").mesh
    assert {b.element for b in mesh.blocks if b.dim == 3} == {"tet4"}
    assert all(f"Bolt/{n}" in mesh.groups for n in FACES)


def _axial_stress(run, lo, hi):
    """Mean σzz over the nodes at lo < z < hi (m, the bolt's own frame: unplaced)."""
    field = run.results.last("stress")
    stress = field.values
    z = run.mesh.node_coords(field.ids)[:, 2]
    band = (z > lo) & (z < hi)
    return float(stress[band, 2].mean())


@over("calculix", "code_aster")
def test_revolved_and_tetrahedral_meshes_agree(tmp_path, solver):
    from puzzle.solve import run_study

    dim = _dimensions()
    out = {}
    for revolved in (True, False):
        # Tetrahedra at a quarter pitch along the thread would be a 47 MB deck.
        p, comp, _ = _project(revolved=revolved, fine_thread=revolved)
        steel = add_library_material(p, "structural_steel")
        comp.materials = [MaterialAssignment(material=steel.id, selection="Bolt")]
        comp.physics = [Physics(type="solid_mechanics", features=[
            Feature(type="fixed", selection="Bolt/End"),
            Feature(type="pressure", selection="Bolt/Bearing",
                    values={"p": Quantity(value=PRESSURE, unit="Pa")})])]  # fmt: skip
        study = p.add(Study(name="Pull", component=comp.id, solver=solver,
                            steps=[SolveStep(type="stationary")]))  # fmt: skip
        root = tmp_path / ("revolved" if revolved else "tets")
        run = run_study(p, study.id, root, root / "run", timeout_s=900)
        # The thread's core between the section and the held end, away from both.
        out[revolved] = _axial_stress(run, -0.040, -0.020)
    bearing = math.pi * (dim.head_radius**2 - (dim.shank_radius + dim.fillet) ** 2)
    core = PRESSURE * bearing / (math.pi * dim.minor_radius**2)
    assert out[True] == pytest.approx(out[False], rel=0.02)
    assert out[True] == pytest.approx(core, rel=0.01)
    assert out[False] == pytest.approx(core, rel=0.01)


# CalculiX's (and VTK's) edges of the 20-node hexahedron and the 15-node prism, after
# the corners, as corner pairs.
CCX_EDGES = {
    "hex20": [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5),
              (2, 6), (3, 7)],
    "wedge15": [(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3), (0, 3), (1, 4), (2, 5)],
}  # fmt: skip


@pytest.mark.parametrize("element", ["hex20", "wedge15"])
def test_second_order_node_orders(element):
    """The reorders from Gmsh's node order put each mid-edge node where CalculiX and VTK
    expect it: on the middle of the edge they number it by (Gmsh's reference element)."""
    import gmsh

    from puzzle.adapters.calculix.inp import ELEMENTS
    from puzzle.geometry import gmsh_session
    from puzzle.mesh.mesh import ELEMENT_NAMES
    from puzzle.post.vtk import VTK_CELLS

    code = next(k for k, v in ELEMENT_NAMES.items() if v == element)
    with gmsh_session("orders"):
        props = gmsh.model.mesh.getElementProperties(code)
    nodes = np.asarray(props[4]).reshape(-1, 3)
    for order in (ELEMENTS[element][1], VTK_CELLS[element][1]):
        ordered = nodes[order]
        for k, (a, b) in enumerate(CCX_EDGES[element]):
            corners = 8 if element == "hex20" else 6
            assert np.allclose(ordered[corners + k], (ordered[a] + ordered[b]) / 2)


@over("calculix", "code_aster")
def test_first_order_hexahedra_and_prisms_solve(tmp_path, solver):
    """First order: hexahedra and prisms (C3D8 and C3D6, HEXA8 and PENTA6) on the
    revolved bolt carry F / A in the thread's core within 3 %."""
    from puzzle.solve import run_study

    dim = _dimensions()
    p, comp, _ = _project(order=1, size=1.5)
    steel = add_library_material(p, "structural_steel")
    comp.materials = [MaterialAssignment(material=steel.id, selection="Bolt")]
    comp.physics = [Physics(type="solid_mechanics", features=[
        Feature(type="fixed", selection="Bolt/End"),
        Feature(type="pressure", selection="Bolt/Bearing",
                values={"p": Quantity(value=PRESSURE, unit="Pa")})])]  # fmt: skip
    study = p.add(Study(name="Pull", component=comp.id, solver=solver,
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    run = run_study(p, study.id, tmp_path, tmp_path / "run", timeout_s=900)
    assert {b.element for b in run.mesh.blocks if b.dim == 3} == {"hex8", "wedge6"}
    bearing = math.pi * (dim.head_radius**2 - (dim.shank_radius + dim.fillet) ** 2)
    core = PRESSURE * bearing / (math.pi * dim.minor_radius**2)
    assert _axial_stress(run, -0.040, -0.020) == pytest.approx(core, rel=0.03)


def _tipton_kt(big: float, small: float, r: float) -> float:
    """The stress concentration factor of a shouldered round bar in tension, on the
    maximum principal stress over the smaller bar's nominal stress: Tipton, Sorem and
    Rolovic (1996)'s fit to finite element results, Peterson's Stress Concentration
    Factors 3rd ed. Eq. (3.5), for 0.002 <= r/d <= 0.3 and 1.01 <= D/d <= 6. Peterson's
    Chart 3.4 underestimates sharp fillets (2.40 here, by its text up to 40 % low)."""
    q, t = big / small, r / small
    root = math.sqrt((3.43 - 3.41 * q**2 + 0.0232 * q**4) / (1 - 8.85 * q**2 - 0.078 * q**4))
    return 0.493 + 0.48 * q**-2.43 + t**-0.48 * root


@over("calculix")
def test_the_head_fillet_raises_the_stress_as_peterson_says(tmp_path, solver):
    """The under-head fillet's peak stress (ADR 0077: stress risers in the solid). The
    bolt is a shouldered bar here: its head's top held, its end pulled. Tipton's factor
    for the head's bearing diameter over the shank with the ISO 885 minimum fillet
    (M16: D/d 1.41, r/d 0.0375) is 2.82; the revolved mesh at r/3 along the fillet gives
    2.80 (2.86 at r/6; a head four times as tall 2.94), checked within 4 %."""
    from puzzle.solve import run_study

    dim = _dimensions()
    p, comp, _ = _project(fine_thread=False)
    steel = add_library_material(p, "structural_steel")
    comp.materials = [MaterialAssignment(material=steel.id, selection="Bolt")]
    comp.physics = [Physics(type="solid_mechanics", features=[
        Feature(type="fixed", selection="Bolt/Top"),
        Feature(type="pressure", selection="Bolt/End",
                values={"p": Quantity(value=-PRESSURE, unit="Pa")})])]  # fmt: skip
    study = p.add(Study(name="Pull", component=comp.id, solver=solver,
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    run = run_study(p, study.id, tmp_path, tmp_path / "run", timeout_s=900)
    field = run.results.last("stress")
    rows = {int(i): k for k, i in enumerate(field.ids)}
    fillet = run.mesh.groups["Bolt/Fillet"].node_ids
    s = np.asarray(field.values)[[rows[int(i)] for i in fillet if int(i) in rows]]
    xx, yy, zz, xy, yz, zx = s.T
    tensors = np.stack([np.stack([xx, xy, zx], -1), np.stack([xy, yy, yz], -1),
                        np.stack([zx, yz, zz], -1)], -2)  # fmt: skip
    peak = float(np.linalg.eigvalsh(tensors)[:, -1].max())
    force = PRESSURE * math.pi * dim.minor_radius**2
    nominal = force / (math.pi * dim.shank_radius**2)
    kt = _tipton_kt(2 * dim.head_radius, 2 * dim.shank_radius, dim.fillet)
    print(f"fillet peak/nominal {peak / nominal:.3f}, Tipton {kt:.3f}")
    assert peak / nominal == pytest.approx(kt, rel=0.04)
