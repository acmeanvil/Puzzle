"""Exodus II, VTKHDF and UNV exports (ADR 0043 step 3) of CalculiX runs on the cantilever,
read back and compared with the stored run.

- Static bend: every node's coordinates and displacement (Exodus and VTKHDF in double
  precision, exactly; UNV's coordinates in 17 digits, its values in 6), the elements'
  connectivity, and every selection as an Exodus node set (and side set for faces, drawn
  by VTK from the element and side numbers) and a UNV group, by name.
- Modal: four modes as four time steps whose values are the frequencies, and UNV
  datasets of analysis type "normal mode" carrying the mode number and frequency.
"""

import numpy as np
import pytest
from conftest_geometry import bar_project, write_bar_step
from export_readers import read_2414, read_exodus, read_mesh, read_unv
from solvers import over

from puzzle.model.schema import Feature, Physics, Quantity, SolveStep, Study
from puzzle.post.exports import export
from puzzle.project import save_project
from puzzle.results.store import ResultStore
from puzzle.solve import run_study


def _solve(tmp, name, steps, features):
    root = tmp / name
    root.mkdir()
    physics = Physics(type="solid_mechanics", features=features,
                      settings={"temperature_dependent_materials": False})  # fmt: skip
    project, comp = bar_project(write_bar_step(root / "bar.step"), [physics], mesh_size_mm=5.0)
    study = project.add(Study(name=name, component=comp.id, solver="calculix", steps=steps))
    save_project(project, root)
    store = ResultStore(root / "results")
    run = run_study(project, study.id, root, tmp / f"w-{name}", store=store)
    return project, store, run.run_id


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("formats")
    fixed = Feature(type="fixed", selection="Bar/Root")
    force = Feature(
        type="force", selection="Bar/Tip", values={"y": Quantity(value=-100.0, unit="N")}
    )
    static = _solve(tmp, "Bend", [SolveStep(type="stationary")], [fixed, force])
    modal = _solve(tmp, "Modes", [SolveStep(type="modal", settings={"modes": 4})], [fixed])
    return tmp, static, modal


def _stored(store, run_id):
    return store.read_mesh(store.get_run(run_id).mesh_id), store.read_results(run_id)


def _at(mesh, ids):
    o = np.argsort(mesh.node_ids)
    return o[np.searchsorted(mesh.node_ids, ids, sorter=o)]


@over("calculix")
def test_exodus_static_and_modal(runs, solver):
    from scipy.io import netcdf_file

    tmp, (project, store, run_id), (mproject, mstore, mrun_id) = runs
    mesh, results = _stored(store, run_id)
    exo, note = export(project, store, run_id, "exodus", tmp / "out" / "bend")
    assert exo.suffix == ".exo" and "time values: time (s)" in note.read_text(encoding="utf-8")
    f = netcdf_file(str(exo), "r", mmap=False)
    v = f.variables
    assert np.array_equal(v["node_num_map"].data, mesh.node_ids)
    xyz = np.column_stack([v[f"coord{a}"].data for a in "xyz"])
    assert np.array_equal(xyz, mesh.coords)
    u = results.steps[0].fields["displacement"].at(mesh.node_ids)
    got = np.column_stack([v[f"vals_nod_var{k}"].data[0] for k in (1, 2, 3)])
    assert np.array_equal(got, u)
    sets = [b"".join(r).decode().rstrip("\x00") for r in v["ns_names"].data]
    selections = [n for n in mesh.groups if "/" in n]
    assert sets == selections == ["Bar/Root", "Bar/Tip", "Bar/Body"]
    f.close()
    found, _ = read_exodus(exo, 0)
    faces = 0
    for name in selections:
        group = mesh.groups[name]
        nodes = found[("Node Sets", name)].point_data["GlobalNodeId"]
        assert sorted(nodes) == sorted(group.node_ids)
        if group.dim == 2:  # a face: its side set's faces are the selection's, by corners
            side = found[("Side Sets", name)]
            cells = np.asarray(side.cells).reshape(side.n_cells, -1)[:, 1:4]
            corners = np.asarray(side.point_data["GlobalNodeId"])[cells]
            want = np.concatenate([b.connectivity[:, :3] for b in group.blocks(mesh)
                                   if b.dim == 2])  # fmt: skip
            assert sorted(map(sorted, corners.tolist())) == sorted(map(sorted, want.tolist()))
            faces += 1
    assert faces == 2  # the root and the tip

    # Modal: four modes, the time values their frequencies (Hz).
    mmesh, modes = _stored(mstore, mrun_id)
    exo, note = export(mproject, mstore, mrun_id, "exodus", tmp / "out" / "modes")
    f = netcdf_file(str(exo), "r", mmap=False)
    freqs = [s.value for s in modes.steps]
    assert len(freqs) == 4
    if np.all(np.diff(freqs) > 0):
        assert np.asarray(f.variables["time_whole"].data).tolist() == freqs
        assert "frequency (Hz)" in note.read_text(encoding="utf-8")
    else:  # equal frequencies (a square section's pair) are numbered instead
        assert np.asarray(f.variables["time_whole"].data).tolist() == [1.0, 2.0, 3.0, 4.0]
    for k, s in enumerate(modes.steps):
        shape = s.fields["displacement"].at(mmesh.node_ids)
        got = np.column_stack([f.variables[f"vals_nod_var{c}"].data[k] for c in (1, 2, 3)])
        assert np.array_equal(got, shape)
    f.close()


@over("calculix")
def test_vtkhdf_static_and_modal(runs, solver):
    import pyvista as pv
    from vtkmodules.vtkCommonExecutionModel import vtkStreamingDemandDrivenPipeline as SDDP
    from vtkmodules.vtkIOHDF import vtkHDFReader

    tmp, _, (project, store, run_id) = runs
    mesh, results = _stored(store, run_id)
    path, note = export(project, store, run_id, "vtkhdf", tmp / "out" / "modes")
    r = vtkHDFReader()
    r.SetFileName(str(path))
    r.UpdateInformation()
    times = r.GetOutputInformation(0).Get(SDDP.TIME_STEPS())
    assert len(times) == 4
    for t, step in zip(times, results.steps, strict=True):
        r.UpdateTimeStep(t)
        grid = pv.wrap(r.GetOutputDataObject(0))
        at = _at(mesh, grid.point_data["node_id"])
        assert np.array_equal(grid.points, mesh.coords[at])
        want = step.fields["displacement"].at(mesh.node_ids)[at]
        assert np.array_equal(grid.point_data["displacement"], want)


@over("calculix")
def test_unv_static_and_modal(runs, solver):
    tmp, (project, store, run_id), (mproject, mstore, mrun_id) = runs
    mesh, results = _stored(store, run_id)
    path, note = export(project, store, run_id, "unv", tmp / "out" / "bend")
    assert "SI (dataset 164" in note.read_text(encoding="utf-8")
    sets = read_unv(path)
    coords, elements = read_mesh(sets)
    assert np.array_equal(np.array([coords[n] for n in mesh.node_ids]), mesh.coords)
    volume = [b for b in mesh.blocks if b.dim == 3]
    assert len(elements) == sum(len(b.ids) for b in volume)
    unv = [0, 4, 1, 5, 2, 6, 7, 9, 8, 3]
    for b in volume:
        assert b.element == "tet10"
        for e, conn in zip(b.ids[:50], b.connectivity[:50], strict=True):
            assert elements[e] == (118, conn[unv].tolist())
    names = [lines[1].strip() for lines in _group_heads(sets[2467][0])]
    assert names == list(mesh.groups)
    (u,) = [d for d in read_2414(sets) if d["ids"][0].strip() == "displacement"]
    assert u["record9"][:4] == [1, 1, 2, 8]  # structural, static, 3 DOF vector, displacement
    stored = results.steps[0].fields["displacement"]
    scale = np.abs(stored.values).max()
    for n, row in u["values"].items():
        assert np.allclose(row, stored.at(np.array([n]))[0], rtol=5e-6, atol=1e-6 * scale)
    assert len(u["values"]) == len(mesh.node_ids)

    mmesh, modes = _stored(mstore, mrun_id)
    path, _ = export(mproject, mstore, mrun_id, "unv", tmp / "out" / "modes")
    shapes = [d for d in read_2414(read_unv(path)) if d["ids"][0].strip() == "displacement"]
    assert len(shapes) == 4
    for k, (d, s) in enumerate(zip(shapes, modes.steps, strict=True), 1):
        assert d["record9"][:2] == [1, 2] and d["record10"][5] == k  # normal mode k
        assert d["record12"][1] == pytest.approx(s.value, rel=5e-6)  # its frequency, Hz
        stored = s.fields["displacement"]
        scale = np.abs(stored.values).max()
        n = mmesh.groups["Bar/Tip"].node_ids[0]
        assert np.allclose(d["values"][n], stored.at(np.array([n]))[0], rtol=5e-6,
                           atol=1e-6 * scale)  # fmt: skip


def _group_heads(lines):
    """Each group's record 1 and name line, skipping its entity lines."""
    k = 0
    while k < len(lines):
        count = int(lines[k][70:80])
        yield lines[k : k + 2]
        k += 2 + (count + 1) // 2
