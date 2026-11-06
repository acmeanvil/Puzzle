"""The morphed B-rep deformed shape (ADR 0043 steps 2.2 and 2.3) on CalculiX results.

- the cantilever (100 N at the tip of the 200 x 10 x 10 mm steel bar): the morphed
  solid's lowest point is the stored tip deflection; bending barely changes the volume
  (the axial strain is odd about the neutral axis), and the morphed volume changes as
  the deformed mesh's does; compensated, the top
  rises by the deflection instead; the STEP reads back as one valid solid named Bar;
- a heated plate with a hole (free, held only on three symmetry planes, a uniform
  100 K temperature load): u = α ΔT x is affine, which morphing reproduces exactly, so
  the volume grows by (1 + α ΔT)³ − 1 ≈ 3 α ΔT and every edge by α ΔT, the hole too;
- a morph held to a tolerance it can't meet is refused with the deviation named.
"""

import numpy as np
import pytest
from conftest_geometry import BAR, bar_project, write_bar_step
from solvers import over

from puzzle.errors import PuzzleError
from puzzle.model.schema import (
    Feature,
    Geometry,
    NamedSelection,
    Physics,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.post.exports import export
from puzzle.project import save_project
from puzzle.results.store import ResultStore
from puzzle.solve import run_study

pytest.importorskip("OCP")

L, B, H = BAR
CONSTANT = {"temperature_dependent_materials": False}


def _solve(project, comp, root, tmp):
    study = project.add(Study(name="Load", component=comp.id, solver="calculix",
                              steps=[SolveStep(type="stationary")]))  # fmt: skip
    save_project(project, root)
    store = ResultStore(root / "results")
    run = run_study(project, study.id, root, tmp / "w", store=store)
    return store, run


@pytest.fixture(scope="module")
def cantilever(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("morph")
    root = tmp / "p"
    root.mkdir()
    force = Feature(type="force", selection="Bar/Tip", values={"y": Quantity(value=-100, unit="N")})
    physics = Physics(type="solid_mechanics", settings=CONSTANT,
                      features=[Feature(type="fixed", selection="Bar/Root"), force])  # fmt: skip
    project, comp = bar_project(write_bar_step(root / "bar.step"), [physics], mesh_size_mm=5.0)
    store, run = _solve(project, comp, root, tmp)
    tip = run.results.last("displacement").at(run.mesh.groups["Bar/Tip"].node_ids)[:, 1].min()
    return project, store, run.run_id, root, float(tip)


def _read(path):
    from OCP.BRepCheck import BRepCheck_Analyzer

    from puzzle.geometry.prep import read_cad
    from puzzle.post.exports.morphed import _volume_area

    shape, labels = read_cad(path)
    assert BRepCheck_Analyzer(shape).IsValid()
    return shape, labels, _volume_area(shape)[0]


def _bounds(shape) -> np.ndarray:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    from puzzle.geometry.history import _static

    box = Bnd_Box()
    _static(BRepBndLib, "AddOptimal")(shape, box, False, False)
    lo, hi = box.CornerMin(), box.CornerMax()
    return np.array([[lo.X(), lo.Y(), lo.Z()], [hi.X(), hi.Y(), hi.Z()]])


@over("calculix")
def test_the_cantilever_morphed(cantilever, solver):
    project, store, run_id, root, tip = cantilever
    step, note = export(project, store, run_id, "morphed_step", root / "out" / "bent", root=root)
    shape, labels, volume = _read(step)
    assert list(labels) == ["Bar"]
    text = note.read_text(encoding="utf-8")
    print(text)
    lowest = _bounds(shape)[0, 1]
    print(f"lowest point {lowest:.9g} m, stored tip deflection {tip:.9g} m")
    assert lowest == pytest.approx(tip, rel=2e-3)  # the tip, moved by the stored deflection
    # Bending barely changes the volume (7e-5 here, from the shear and the root's hold),
    # and the morphed volume changes as the deformed mesh's does.
    from puzzle.post.vtk import to_grid

    record = store.get_run(run_id)
    grid = to_grid(store.read_mesh(record.mesh_id), store.read_results(run_id).steps[-1])
    mesh_change = grid.warp_by_vector("displacement").volume - grid.volume
    assert abs(volume - L * B * H) < 1e-4 * L * B * H
    assert volume - L * B * H == pytest.approx(mesh_change, rel=1e-2)
    assert "check Bar: largest deviation" in text and "deformed: moved by +u" in text

    back, note = export(project, store, run_id, "morphed_step", root / "out" / "comp",
                        compensate=True, root=root)  # fmt: skip
    shape, _, _ = _read(back)
    assert _bounds(shape)[1, 1] == pytest.approx(B - tip, rel=2e-3)  # the top rises
    assert _bounds(shape)[0, 1] == pytest.approx(0.0, abs=1e-6)  # the root's bottom stays
    assert "compensated: moved by -u" in note.read_text(encoding="utf-8")

    # Exaggerated 50 times (the tip 76 mm down on a 200 mm bar): still within tolerance,
    # since the morph is linear in u.
    big, note = export(project, store, run_id, "morphed_step", root / "out" / "x50", scale=50,
                       root=root)  # fmt: skip
    shape, _, _ = _read(big)
    assert _bounds(shape)[0, 1] == pytest.approx(50 * tip, rel=2e-3)
    assert "(exaggerated)" in note.read_text(encoding="utf-8")


@over("calculix")
def test_a_morph_past_its_tolerance_is_refused(cantilever, solver):
    project, store, run_id, root, tip = cantilever
    with pytest.raises(PuzzleError, match="morphed Bar is off the deformed mesh") as caught:
        export(project, store, run_id, "morphed_step", root / "out" / "strict", root=root,
               tolerance=1e-9)  # fmt: skip
    assert caught.value.code == "export_failed"
    assert "deformed_step" in str(caught.value)
    assert not (root / "out" / "strict.step").exists()


def _plate_step(path, size=(0.04, 0.02, 0.01), r=0.004):
    import gmsh

    from puzzle.geometry import gmsh_session

    with gmsh_session("plate"):
        box = gmsh.model.occ.addBox(0, 0, 0, *size)
        hole = gmsh.model.occ.addCylinder(0.02, 0.01, 0, 0, 0, size[2], r)
        gmsh.model.occ.cut([(3, box)], [(3, hole)])
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    return path


@over("calculix")
def test_a_heated_plate_grows_by_alpha_dt(tmp_path, solver):
    from puzzle.geometry import import_step
    from puzzle.materials import library_material
    from puzzle.post.exports.morphed import _volume_area

    root = tmp_path / "p"
    root.mkdir()
    size, r, dt = np.array([0.04, 0.02, 0.01]), 0.004, 100.0
    alpha = library_material("structural_steel").properties["thermal_expansion"].to_si()
    held = [Feature(type="displacement", selection=f"Bar/{name}",
                    values={axis: Quantity(value=0, unit="mm")})
            for name, axis in (("Root", "x"), ("Side", "y"), ("Bottom", "z"))]  # fmt: skip
    heat = Feature(type="body_temperature", selection="Bar/Body",
                   values={"T": Quantity(value=20 + dt, unit="degC")})  # fmt: skip
    physics = Physics(type="solid_mechanics", settings=CONSTANT, features=[*held, heat])
    step_file = _plate_step(root / "bar.step", tuple(size), r)
    project, comp = bar_project(step_file, [physics], mesh_size_mm=3.0)
    geo = project.of_type(Geometry)[0]
    model = import_step(step_file)
    geo.selections = [
        NamedSelection(name="Body", entity="body", members=["body-1"]),
        NamedSelection(name="Root", entity="face", members=model.faces_on_plane("x", 0.0)),
        NamedSelection(name="Side", entity="face", members=model.faces_on_plane("y", 0.0)),
        NamedSelection(name="Bottom", entity="face", members=model.faces_on_plane("z", 0.0)),
    ]
    store, run = _solve(project, comp, root, tmp_path)
    step, note = export(project, store, run.run_id, "morphed_step", root / "out" / "hot",
                        root=root)  # fmt: skip
    print(note.read_text(encoding="utf-8"))
    shape, labels, volume = _read(step)
    e = alpha * dt
    v0 = float(np.prod(size)) - np.pi * r**2 * size[2]
    from puzzle.geometry.prep import read_cad

    v_cad = _volume_area(read_cad(step_file)[0])[0]
    assert v_cad == pytest.approx(v0, rel=1e-9)
    print(f"volume grows {(volume - v0) / v0:.6e}, 3 alpha dT = {3 * e:.6e}")
    assert (volume - v0) / v0 == pytest.approx((1 + e) ** 3 - 1, rel=1e-4)
    assert (volume - v0) / v0 == pytest.approx(3 * e, rel=2 * e)  # first order
    lo, hi = _bounds(shape)
    assert hi - lo == pytest.approx(size * (1 + e), abs=1e-6)  # each edge grows by alpha dT L
    # The hole grows too: its radius is (1 + e) r, its axis moved to (1 + e) c.
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopoDS import TopoDS

    from puzzle.geometry.history import _static, _subshapes

    radii = []
    for face in _subshapes(shape, TopAbs_FACE):
        s = BRepAdaptor_Surface(_static(TopoDS, "Face")(face))
        u1, u2 = s.FirstUParameter(), s.LastUParameter()
        v = (s.FirstVParameter() + s.LastVParameter()) / 2
        p = [s.Value(u, v) for u in np.linspace(u1, u2, 7)]
        xy = np.array([(q.X(), q.Y()) for q in p])
        d = np.linalg.norm(xy - np.array([0.02, 0.01]) * (1 + e), axis=1)
        if np.ptp(d) < 1e-6 and d.mean() < 2 * r:
            radii.append(d.mean())
    assert radii and np.allclose(radii, r * (1 + e), rtol=1e-6)


@over("calculix")
def test_an_assembly_morphs_part_by_part(tmp_path, solver):
    """The split bar (two halves, the right one placed 100 mm along, tied end to end)
    pulled by 2 kN: one body per part, named, the right half's end moved by F L / (E A)."""
    from conftest_geometry import HALF, split_bar_project

    from puzzle.materials import library_material
    from puzzle.model.schema import Contact

    root = tmp_path / "p"
    root.mkdir()
    force = 2000.0
    held = [Feature(type="displacement", selection=f"Left/{face}",
                    values={axis: Quantity(value=0, unit="m")})
            for face, axis in (("Side", "y"), ("Bottom", "z"), ("Start", "x"))]  # fmt: skip
    pull = Feature(type="force", selection="Right/End",
                   values={"x": Quantity(value=force, unit="N")})  # fmt: skip
    bond = Contact(name="Joint", type="bonded", source="Right/Start", target="Left/End",
                   meshing="nonconformal")  # fmt: skip
    project, study = split_bar_project(root, "calculix", [*held, pull], bond)
    save_project(project, root)
    store = ResultStore(root / "results")
    run = run_study(project, study.id, root, tmp_path / "w", store=store)
    step, note = export(project, store, run.run_id, "morphed_step", root / "out" / "pulled",
                        root=root)  # fmt: skip
    print(note.read_text(encoding="utf-8"))
    _, labels, _ = _read(step)
    assert set(labels) == {"Left", "Right"}
    e_mod = library_material("structural_steel").properties["youngs_modulus"].to_si()
    stretch = force * 2 * HALF / (e_mod * B * H)
    end = _bounds(labels["Right"][0])[1, 0]
    print(f"right end at {end:.9f} m, exact {2 * HALF + stretch:.9f} m")
    assert end == pytest.approx(2 * HALF + stretch, abs=0.02 * stretch)
    assert _bounds(labels["Left"][0])[0, 0] == pytest.approx(0.0, abs=1e-6)
