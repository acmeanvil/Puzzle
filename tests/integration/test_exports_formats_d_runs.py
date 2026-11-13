"""Nastran, Abaqus/CalculiX, glTF and Lorentz-force exports (ADR 0043 steps 6 and 7) on
real runs, checked by the software that receives them where we have it.

- Abaqus/CalculiX: CalculiX solves decks that *INCLUDE the exported mesh: with the run's
  own nodal tip loads on the exported material, section and sets, the tip deflects as
  the stored run does; with the exported tip displacements as *BOUNDARY data, the bar's
  middle deflects as stored; with a heated bar's exported temperatures as a load, the
  free bar grows by α ΔT L.
- Nastran: the bulk data parsed back has every node at its coordinates (10 digits) and
  every element; the punch's tip displacement is the stored one to seven digits.
- glTF: VTK reads the cantilever's surface with as many points and triangles.
- Lorentz: in a wire carrying a steady current (Elmer or FEniCSx magnetostatics),
  J × B points radially inward with magnitude μ0 J² r / 2.
"""

import numpy as np
import pytest
from conftest_geometry import BAR, bar_project, write_bar_step
from solvers import over

from puzzle.jobs import run_job, runtime_for
from puzzle.model.schema import Feature, Physics, Quantity, SolveStep, Study
from puzzle.post.exports import export
from puzzle.project import save_project
from puzzle.results.store import ResultStore
from puzzle.solve import run_study

P = 100.0
L = BAR[0]
ALPHA = 12.16e-6  # structural steel, 1/K
HOT = 393.15  # K
REFERENCE = 293.15


def _solve(tmp, physics, steps, name):
    root = tmp / name
    root.mkdir()
    project, comp = bar_project(write_bar_step(root / "bar.step"), [physics], mesh_size_mm=5.0)
    study = project.add(Study(name=name, component=comp.id, solver="calculix", steps=steps))
    save_project(project, root)
    store = ResultStore(root / "results")
    work = tmp / f"{name}-work"
    run = run_study(project, study.id, root, work, store=store)
    return project, store, run, work


@pytest.fixture(scope="module")
def bent(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("formats")
    force = Feature(type="force", selection="Bar/Tip", values={"y": Quantity(value=-P, unit="N")})
    physics = Physics(type="solid_mechanics", features=[Feature(type="fixed", selection="Bar/Root"),
                      force], settings={"temperature_dependent_materials": False})  # fmt: skip
    project, store, run, work = _solve(tmp, physics, [SolveStep(type="stationary")], "Bend")
    return project, store, run, work, tmp


@pytest.fixture(scope="module")
def heated(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("heated")
    hot = [Feature(type="temperature", selection=f"Bar/{s}", values={"T": Quantity(value=120,
               unit="degC")}) for s in ("Root", "Tip")]  # fmt: skip
    physics = Physics(type="heat_transfer", features=hot,
                      settings={"temperature_dependent_materials": False})  # fmt: skip
    project, store, run, work = _solve(tmp, physics, [SolveStep(type="stationary")], "Heat")
    return project, store, run, tmp


def _ccx(folder, deck: str) -> dict[int, np.ndarray]:
    """Run CalculiX on `deck`; the printed displacements by node."""
    (folder / "check.inp").write_text(deck, encoding="utf-8")
    result = run_job(["ccx", "-i", "check"], folder, runtime_for("calculix"), timeout_s=300)
    assert result.ok, result.log.read_text(encoding="utf-8")[-3000:]
    rows = {}
    for line in (folder / "check.dat").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[0].isdigit():
            rows[int(parts[0])] = np.array([float(p) for p in parts[1:]])
    return rows


def _loads(work) -> list[str]:
    """The *CLOAD block of the run's own deck (nodal forces on the tip)."""
    (deck,) = [p for p in work.rglob("*.inp")]
    lines = deck.read_text(encoding="utf-8").splitlines()
    at = lines.index(next(line for line in lines if line.startswith("*CLOAD")))
    end = next(k for k in range(at + 1, len(lines)) if lines[k].startswith("*"))
    return lines[at:end]


@over("calculix")
def test_calculix_solves_the_exported_deck(bent, solver):
    project, store, run, work, tmp = bent
    out = tmp / "abaqus"
    files = export(project, store, run.run_id, "abaqus_inp", out / "bar")
    assert [f.name for f in files] == ["bar.inp", "bar.txt"]  # no temperatures in a bend
    text = files[0].read_text(encoding="utf-8")
    assert "*ELEMENT, TYPE=C3D10, ELSET=E_BAR" in text and "*NSET, NSET=N_BAR_TIP" in text
    tip_nodes = run.mesh.groups["Bar/Tip"].node_ids
    stored = run.results.last("displacement")
    deck = "\n".join(["*INCLUDE, INPUT=bar.inp", "*STEP", "*STATIC", "*BOUNDARY",
                      "N_BAR_ROOT, 1, 3", *_loads(work), "*NODE PRINT, NSET=N_BAR_TIP", "U",
                      "*END STEP"]) + "\n"  # fmt: skip
    got = _ccx(out, deck)
    assert sorted(got) == sorted(tip_nodes.tolist())
    tip = min(u[1] for u in got.values())
    # The stored run is read from CalculiX's .frd, six significant digits.
    assert tip == pytest.approx(stored.at(tip_nodes)[:, 1].min(), rel=5e-6)
    print(f"tip deflection through the exported deck {tip:.6e} m")


@over("calculix")
def test_exported_tip_displacements_as_boundary(bent, solver):
    project, store, run, work, tmp = bent
    out = tmp / "boundary"
    files = export(project, store, run.run_id, "abaqus_displacement", out / "bar",
                   selection="Bar/Tip")  # fmt: skip
    assert files[1].name == "bar_displacement.inp"
    mesh, stored = run.mesh, run.results.last("displacement")
    x = mesh.coords[:, 0]
    middle = mesh.node_ids[np.abs(x - L / 2) < 1e-9]
    nset = ["*NSET, NSET=MIDDLE", *(f"{int(n)}," for n in middle)]
    deck = "\n".join(["*INCLUDE, INPUT=bar.inp", *nset, "*STEP", "*STATIC", "*BOUNDARY",
                      "N_BAR_ROOT, 1, 3", "*INCLUDE, INPUT=bar_displacement.inp",
                      "*NODE PRINT, NSET=MIDDLE", "U", "*END STEP"]) + "\n"  # fmt: skip
    got = _ccx(out, deck)
    want = stored.at(middle)
    scale = np.abs(stored.values).max()  # the given values carry the .frd's six digits
    for k, n in enumerate(middle):
        assert got[int(n)] == pytest.approx(want[k], rel=2e-5, abs=1e-5 * scale)


@over("calculix")
def test_exported_temperatures_expand_a_free_bar(heated, solver):
    project, store, run, tmp = heated
    out = tmp / "thermal"
    files = export(project, store, run.run_id, "abaqus_inp", out / "bar")
    assert [f.name for f in files] == ["bar.inp", "bar_temperature.inp", "bar.txt"]
    t = run.results.last("temperature")
    assert t.values.min() == pytest.approx(HOT, abs=1e-6)  # the bar is uniformly hot
    mesh = run.mesh

    def node_at(x, y, z):
        return int(mesh.node_ids[np.argmin(np.linalg.norm(mesh.coords - [x, y, z], axis=1))])

    origin, top = node_at(0, 0, 0), node_at(0, 0, BAR[2])
    tip = run.mesh.groups["Bar/Tip"].node_ids
    # Held just enough: the root face in x, one corner in y and z, one above it in y.
    deck = "\n".join(["*INCLUDE, INPUT=bar.inp",
                      "*INITIAL CONDITIONS, TYPE=TEMPERATURE", f"N_ALL, {REFERENCE}",
                      "*STEP", "*STATIC", "*BOUNDARY", "N_BAR_ROOT, 1, 1", f"{origin}, 2, 3",
                      f"{top}, 2, 2", "*INCLUDE, INPUT=bar_temperature.inp",
                      "*NODE PRINT, NSET=N_BAR_TIP", "U", "*END STEP"]) + "\n"  # fmt: skip
    got = _ccx(out, deck)
    grown = np.array([got[int(n)][0] for n in tip])
    exact = ALPHA * (HOT - REFERENCE) * L
    print(f"tip growth {grown.mean():.6e} m, α ΔT L = {exact:.6e} m")
    assert grown == pytest.approx(np.full(len(grown), exact), rel=1e-6)


def _large_field(text: str) -> list[tuple[str, list[str]]]:
    cards = []
    for line in text.splitlines():
        if line.startswith("$") or not line.strip():
            continue
        fields = [line[8 + 16 * k : 24 + 16 * k].strip() for k in range(4)]
        if line.startswith("*"):
            cards[-1][1].extend(fields)
        else:
            cards.append((line[:8].strip().rstrip("*"), fields))
    return cards


@over("calculix")
def test_nastran_round_trip(bent, solver):
    project, store, run, work, tmp = bent
    bdf, pch, note = export(project, store, run.run_id, "nastran", tmp / "nastran" / "bar")
    mesh, stored = run.mesh, run.results.last("displacement")
    cards = _large_field(bdf.read_text(encoding="utf-8"))
    grids = {int(f[0]): [float(v) if v else 0.0 for v in f[2:5]] for n, f in cards if n == "GRID"}
    assert len(grids) == len(mesh.node_ids)
    xyz = np.array([grids[int(n)] for n in mesh.node_ids])
    assert np.allclose(xyz, mesh.coords, rtol=1e-9, atol=1e-15)
    tetras = [f for n, f in cards if n == "CTETRA"]
    assert len(tetras) == sum(len(b.ids) for b in mesh.blocks if b.dim == 3)
    assert all(len([v for v in f if v]) == 12 for f in tetras)  # id, property, 10 grids
    lines = pch.read_text(encoding="utf-8").splitlines()
    at = lines.index("$DISPLACEMENTS".ljust(72) + f"{4:8d}")
    shown = {int(line[:10]): float(line[36:54]) for line in lines[at + 3 :]
             if line[10:18].strip() == "G"}  # fmt: skip
    tip_nodes = mesh.groups["Bar/Tip"].node_ids
    for n, uy in zip(tip_nodes, stored.at(tip_nodes)[:, 1], strict=True):
        assert shown[int(n)] == pytest.approx(uy, rel=5e-7)
    assert "metres" in note.read_text(encoding="utf-8")


@over("calculix")
def test_gltf_of_the_cantilever(bent, solver):
    from vtkmodules.vtkIOGeometry import vtkGLTFReader

    from puzzle.post.exports.gltf import node_values, surfaces

    project, store, run, work, tmp = bent
    glb, note = export(project, store, run.run_id, "gltf", tmp / "gltf" / "bar",
                       quantity="displacement:total", scale=50.0)  # fmt: skip
    step = run.results.steps[-1]
    _, values = node_values(run.results, step, "displacement:total", run.mesh)
    ((points, triangles, _),) = surfaces(run.mesh, step, values, 50.0).values()
    reader = vtkGLTFReader()
    reader.SetFileName(str(glb))
    reader.Update()
    it = reader.GetOutput().NewIterator()
    it.InitTraversal()
    read = it.GetCurrentDataObject()
    assert (read.GetNumberOfPoints(), read.GetNumberOfCells()) == (len(points), len(triangles))
    lowest = min(read.GetPoint(k)[1] for k in range(read.GetNumberOfPoints()))
    tip = run.results.last("displacement").values[:, 1].min()
    assert lowest == pytest.approx(50 * tip, rel=1e-5)  # float32 positions
    assert "x 50" in note.read_text(encoding="utf-8")


@over("elmer", "fenicsx")
def test_lorentz_force_in_a_wire(tmp_path, solver):
    """The wire of test_magnetostatics, meshed finer inside: its 0.4 mm elements (0.4 a)
    leave the nodal J (projected, overshooting by up to 30 % beside the wire's surface)
    and B (constant per element, averaged) too coarse to check a product inside."""
    from test_magnetostatics import CURRENT, MU0, A, _wire
    from test_magnetostatics import L as WIRE

    from puzzle.model import MeshRecipe

    project, study = _wire(tmp_path, solver)
    (recipe,) = project.of_type(MeshRecipe)
    # In the conductor; Elmer's direct A-V solve (UMFPACK) runs out of memory at 0.15 mm.
    fine = {"fenicsx": 0.15, "elmer": 0.25}[solver]
    recipe.controls[0].size = Quantity(value=fine, unit="mm")
    store = ResultStore(tmp_path / "results")
    run = run_study(project, study.id, tmp_path, tmp_path / "run", store=store, timeout_s=1800)
    csv, note = export(project, store, run.run_id, "lorentz_cloud", tmp_path / "out" / "f",
                       selection="Conductor/Body")  # fmt: skip
    rows = np.loadtxt(csv, delimiter=",", skiprows=1)
    xyz, f, size = rows[:, :3], rows[:, 3:6], rows[:, 6]
    r = np.hypot(xyz[:, 0], xyz[:, 1])
    inside = (r > 0.2 * A) & (r < 0.7 * A) & (np.abs(xyz[:, 2] - WIRE / 2) < WIRE / 4)
    j = CURRENT / (np.pi * A**2)
    ratio = size[inside] / (MU0 * j**2 * r[inside] / 2)
    inward = -np.einsum("ij,ij->i", f[inside, :2], xyz[inside, :2] / r[inside, None])
    inward /= size[inside]
    spread = np.percentile(np.abs(ratio - 1), 90)
    print(f"{solver}: |J × B| / (mu0 J^2 r / 2) over {inside.sum()} nodes: median "
          f"{np.median(ratio):.4f}, 90 % within {spread:.2%}; inward cosine mean "
          f"{inward.mean():.4f}, least {inward.min():.3f}")  # fmt: skip
    # Measured: FEniCSx (0.15 mm) median 1.001, 90 % within 5.5 %; Elmer (0.25 mm, its
    # finest here) median 1.040, 90 % within 20.5 %: the fields' error, not the product's.
    median, scatter = {"fenicsx": (0.03, 0.1), "elmer": (0.06, 0.25)}[solver]
    assert np.median(ratio) == pytest.approx(1.0, abs=median)
    assert spread < scatter
    assert inward.mean() > 0.99 and np.percentile(inward, 5) > 0.95


def test_formats_are_registered():
    from puzzle.post.exports import EXPORTS

    assert {"nastran", "abaqus_inp", "abaqus_displacement", "gltf", "lorentz_cloud"} <= set(EXPORTS)
