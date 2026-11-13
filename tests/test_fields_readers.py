"""Imported fields from other tools, and fields varying in time (ADR 0044).

Readers, each checked against values put in by hand or by Puzzle's own writers:
- a Fluent point profile (tests/data/fields/walls.prof: p = 1000 + 2000 x + 500 y Pa);
- Exodus II from the ADR 0043 writer (netCDF-3) and a netCDF-4 (HDF5) one made with h5py:
  nodal values at the nodes, element values at the elements' corner means, every step;
- EnSight Gold from the ADR 0043 writer: node, vector and symmetric tensor variables
  (float32: 7 digits), the volume part by default, a boundary part by name;
- an OpenFOAM case (tests/data/fields/channel, by hand): two cells, the second a
  trapezoidal prism whose centroid is (49/30, 1/2, 8/15) m; patch values written,
  matched by a quoted pattern, or left to the cells (zeroGradient).
Time: a field linear in time (two files, a time column, two OpenFOAM times) is exact at
any time between and held outside; each solve step gets its own value at its time.
"""

from pathlib import Path

import numpy as np
import pytest
from exports_mesh import cube, results

from puzzle.fields import (
    clouds,
    field_inputs,
    new_field_data,
    sniff,
    step_time,
    use_values,
)
from puzzle.model import (
    Component,
    Feature,
    FieldColumn,
    FieldUse,
    ImportedField,
    MeshRecipe,
    PartInstance,
    Physics,
    Project,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.model.schema import Geometry

DATA = Path(__file__).parent / "data" / "fields"


def _made(root, path, name="F", **extra) -> tuple[Project, ImportedField]:
    p = Project(name="readers")
    f = p.add(ImportedField(**new_field_data(root, path, name, **extra)))
    return p, f


def _values(p, f, root, column, targets, time=None):
    return use_values(p, root, FieldUse(field=f.id, column=column), targets, time=time)[0]


# --- Fluent profiles ------------------------------------------------------------------------


def test_a_fluent_profile_is_read_whole_or_by_profile(tmp_path):
    found = sniff(DATA / "walls.prof")
    assert found["format"] == "fluent_profile"
    assert [c["name"] for c in found["columns"]] == ["pressure", "x-wall-shear"]
    assert found["regions"] == ["wall-top", "wall-bottom"]
    p, f = _made(tmp_path, DATA / "walls.prof")
    [(_, c)] = clouds(p, tmp_path, f)
    x, y, _ = c.points.T
    assert len(c.points) == 7 and c.points[:4, 2] == pytest.approx(0.02)
    assert c.values["pressure"] == pytest.approx(1000 + 2000 * x + 500 * y, rel=1e-12)
    assert c.values["x-wall-shear"] == pytest.approx(1.5 + 10 * x, rel=1e-12)
    p, f = _made(tmp_path, DATA / "walls.prof", region="wall-bottom")
    assert len(clouds(p, tmp_path, f)[0][1].points) == 3


# --- Exodus II ------------------------------------------------------------------------------


def _exodus(tmp_path):
    from puzzle.post.exports.exodus import write_exodus

    mesh = cube(2)
    res = results(mesh)  # transient: times 0.5 and 1.25
    path = tmp_path / "out" / "cube.exo"
    path.parent.mkdir()
    write_exodus(mesh, res, path)
    return mesh, res, path


def test_exodus_nodal_and_element_variables_at_every_step(tmp_path):
    from puzzle.post.exports.fluid import volume

    mesh, res, path = _exodus(tmp_path)
    found = sniff(path)
    assert found["format"] == "exodus"
    assert [s["time"] for s in found["steps"]] == [0.5, 1.25]
    assert found["locations"]["displacement_x"] == "node"
    assert found["locations"]["stress_zx"] == "element"
    root = tmp_path / "project"
    wanted = (("temperature", "K"), ("displacement_y", "m"))
    p, f = _made(root, path, columns=[{"name": n, "role": "value", "unit": u} for n, u in wanted])
    c = clouds(p, root, f)[0][1]  # the last step by default
    x = mesh.coords[:, 0]
    assert c.points == pytest.approx(mesh.coords, abs=0)
    assert c.values["temperature"] == pytest.approx(293.15 + 20 * x, rel=1e-15)
    assert c.values["displacement_y"] == pytest.approx(res.steps[1].fields["displacement"]
                                                       .values[:, 1], rel=1e-15)  # fmt: skip
    # Element values at the elements' corner means, step 1 of 2 picked by index.
    stress = [{"name": f"stress_{k}", "role": "value", "unit": "Pa"} for k in ("xx", "zx")]
    p, f = _made(root, path, "S", columns=stress, index="1")
    c = clouds(p, root, f)[0][1]
    vol = volume(mesh)
    assert c.points == pytest.approx(vol.centroids, rel=1e-15)
    put = res.steps[0].fields["stress"].values
    assert c.values["stress_xx"] == pytest.approx(put[:, 0]) and c.values["stress_zx"] == (
        pytest.approx(put[:, 5]))  # fmt: skip
    mixed = [{"name": "temperature", "role": "value"}, {"name": "stress_xx", "role": "value"}]
    p, f = _made(root, path, "M", columns=mixed)
    with pytest.raises(Exception, match="node and element values"):
        clouds(p, root, f)


def test_a_netcdf4_exodus_file_reads_through_h5py(tmp_path):
    h5py = pytest.importorskip("h5py")
    path = tmp_path / "hdf.exo"
    xyz = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
    with h5py.File(path, "w") as h:
        for name, n in (("num_nodes", 4), ("time_step", 2), ("num_nod_var", 1),
                        ("len_name", 33), ("num_el_blk", 1), ("num_el_in_blk1", 1),
                        ("num_nod_per_el1", 4)):  # fmt: skip
            h.create_dataset(name, data=np.zeros(n)).make_scale(name)
        for k, axis in enumerate("xyz"):
            h[f"coord{axis}"] = xyz[:, k]
        h["time_whole"] = [0.0, 2.0]
        name = np.zeros((1, 33), "S1")
        name[0, 0] = b"T"
        h["name_nod_var"] = name
        h["vals_nod_var1"] = np.array([300 + 10 * xyz[:, 0], 320 + 10 * xyz[:, 0]])
        h["connect1"] = np.array([[1, 2, 3, 4]], np.int32)
        h["connect1"].attrs["elem_type"] = b"TETRA4"
    found = sniff(path)
    assert found["format"] == "exodus" and [c["name"] for c in found["columns"]] == ["T"]
    p, f = _made(tmp_path, path, every_step=True)
    assert [t.time for t in f.times] == [0.0, 2.0]
    at = np.array([[0.5, 0.2, 0.1]])
    assert _values(p, f, tmp_path, "T", at, time=1.0) == pytest.approx(315.0, rel=1e-12)


# --- EnSight Gold ---------------------------------------------------------------------------


def _ensight(tmp_path):
    from puzzle.post.exports.ensight import write_ensight

    mesh = cube(1)
    res = results(mesh)
    out = tmp_path / "out"
    out.mkdir()
    files, _ = write_ensight(mesh, res, out / "cube.case")
    return mesh, res, files[0]


def test_ensight_gold_node_vector_and_tensor_variables(tmp_path):
    from puzzle.post.exports.fluid import volume

    mesh, res, case = _ensight(tmp_path)
    found = sniff(case)
    assert found["format"] == "ensight" and found["regions"][0] == "volume"
    assert "Box/Top" in found["regions"] and [s["time"] for s in found["steps"]] == [0.5, 1.25]
    names = [c["name"] for c in found["columns"]]
    assert {"temperature", "displacement_x", "displacement_z", "stress_xx", "stress_zx"} <= set(
        names)  # fmt: skip
    root = tmp_path / "project"
    cols = [{"name": n, "role": "value", "unit": ""} for n in ("temperature", "displacement_z")]
    p, f = _made(root, case, columns=cols, index="1")
    assert (root / "fields" / "cube" / "cube.geo").exists()  # its files came with it
    c = clouds(p, root, f)[0][1]
    x, y, z = c.points.T
    put = 1e-3 * 1.0 * 3 * x * z  # step 1's u_z = k 3 x z 1e-3
    assert len(c.points) == 8  # the volume part only, not the boundary parts' copies
    assert c.values["temperature"] == pytest.approx(293.15 + 10 * x, rel=1e-7)
    assert c.values["displacement_z"] == pytest.approx(put, rel=1e-6, abs=1e-12)
    cols = [{"name": f"stress_{k}", "role": "value", "unit": "Pa"} for k in ("xy", "yz", "zx")]
    p, f = _made(root, case, "S", columns=cols)  # the last step: k = 2
    c = clouds(p, root, f)[0][1]
    vol = volume(mesh)
    assert c.points == pytest.approx(vol.centroids, rel=1e-7)
    put = res.steps[1].fields["stress"].values
    for k, name in ((3, "xy"), (4, "yz"), (5, "zx")):
        assert c.values[f"stress_{name}"] == pytest.approx(put[:, k], rel=1e-7)
    temperature = [{"name": "temperature", "role": "value"}]
    p, f = _made(root, case, "Top", columns=temperature, region="Box/Top")
    c = clouds(p, root, f)[0][1]
    assert c.points[:, 2] == pytest.approx(1.0) and len(c.points) == 4


def test_an_ascii_ensight_case_with_given_node_ids(tmp_path):
    case = DATA / "ascii" / "tet.case"  # by hand: one tetrahedron, node ids given
    found = sniff(case)
    assert found["locations"] == {"T": "node", "q": "element"} and found["steps"] == []
    p, f = _made(tmp_path, case, columns=[{"name": "T", "role": "value", "unit": "K"}])
    c = clouds(p, tmp_path, f)[0][1]
    assert c.points == pytest.approx(np.array([[0, 0, 0], [1, 0, 0], [0, 2, 0], [0, 0, 4.0]]))
    assert list(c.values["T"]) == [300, 310, -25, 320]
    p, f = _made(tmp_path, case, "Q", columns=[{"name": "q", "role": "value", "unit": "W/m^3"}])
    c = clouds(p, tmp_path, f)[0][1]
    assert c.points == pytest.approx(np.array([[0.25, 0.5, 1.0]])) and c.values["q"] == [1250]


# --- OpenFOAM -------------------------------------------------------------------------------


def test_an_openfoam_case_gives_cell_centres_and_patch_values(tmp_path):
    case = DATA / "channel"
    found = sniff(case / "system" / "controlDict")
    assert found["format"] == "openfoam"
    assert [c["name"] for c in found["columns"]] == ["T", "U_x", "U_y", "U_z"]
    assert found["regions"] == ["inlet", "outlet", "walls"]
    assert [(s["time"], s["index"]) for s in found["steps"]] == [(0.0, "0"), (1.0, "1")]
    root = tmp_path / "project"
    p, f = _made(root, case, columns=[{"name": "T", "role": "value", "unit": "K"},
                                      {"name": "U_y", "role": "value", "unit": "m/s"}])  # fmt: skip
    assert f.file == "fields/channel" and (root / f.file / "constant/polyMesh/faces").exists()
    c = clouds(p, root, f)[0][1]
    assert c.points == pytest.approx(np.array([[0.5, 0.5, 0.5], [49 / 30, 0.5, 8 / 15]]), rel=1e-14)
    assert c.values["T"] == pytest.approx([400, 420]) and c.values["U_y"] == pytest.approx(
        [0, 0.5])  # fmt: skip

    def patch(region, column="T", index=""):
        p, f = _made(root, case, region, columns=[{"name": column, "role": "value"}],
                     region=region, index=index)  # fmt: skip
        return clouds(p, root, f)[0][1]

    outlet = patch("outlet")
    assert outlet.points == pytest.approx(np.array([[2.25, 0.5, 0.5]])) and outlet.values["T"] == [
        420
    ]
    assert list(patch("walls").values["T"]) == list(range(401, 409))
    assert list(patch("inlet", index="0").values["T"]) == [290]
    assert list(patch("walls", "U_z").values["U_z"]) == [0, -0.25] * 4  # the cells' values
    # Two times: linear between them.
    p, f = _made(root, case, "Both", columns=[{"name": "T", "role": "value"}], every_step=True)
    at = np.array([[0.5, 0.5, 0.5]])
    assert _values(p, f, root, "T", at, time=0.25) == pytest.approx(325.0)


def test_a_binary_openfoam_field_is_refused_with_the_way_out(tmp_path):
    import shutil

    case = tmp_path / "case"
    shutil.copytree(DATA / "channel", case)
    text = (
        (case / "1" / "T")
        .read_text(encoding="utf-8")
        .replace("format      ascii", "format      binary")
    )
    (case / "1" / "T").write_text(text, encoding="utf-8")
    p = Project(name="b")
    f = p.add(ImportedField(name="T", file="case", format="openfoam",
                            columns=[FieldColumn(name="T")]))  # fmt: skip
    with pytest.raises(Exception, match="writeFormat ascii"):
        clouds(p, tmp_path, f)


# --- Ansys Mechanical's text layouts ---------------------------------------------------------


def test_ansys_mechanical_exports_name_their_node_numbers_and_locations(tmp_path):
    path = tmp_path / "mech.txt"
    path.write_text("Node Number\tX Location (mm)\tY Location (mm)\tZ Location (mm)\t"
                    "Temperature (°C)\n1\t0.\t0.\t0.\t22.5\n2\t10.\t0.\t0.\t23.5\n",
                    encoding="utf-8")  # fmt: skip
    found = sniff(path)
    assert found["delimiter"] == "\t" and found["length_unit"] == "mm"
    assert [(c["role"], c["unit"]) for c in found["columns"]] == [
        ("ignore", ""), ("x", ""), ("y", ""), ("z", ""), ("value", "°C")]  # fmt: skip
    fluent = tmp_path / "fluent.csv"
    fluent.write_text("nodenumber, x-coordinate, y-coordinate, z-coordinate, pressure\n"
                      "1, 0, 0, 0, 5\n", encoding="utf-8")  # fmt: skip
    assert [c["role"] for c in sniff(fluent)["columns"]] == ["ignore", "x", "y", "z", "value"]


# --- time ---------------------------------------------------------------------------------


def _linear_csv(path, t):
    g = np.linspace(0.0, 1.0, 5)
    x, y, z = (a.ravel() for a in np.meshgrid(g, g, g, indexing="ij"))
    rows = ["x,y,z,T [K]"] + [f"{a:.9g},{b:.9g},{c:.9g},{300 + 10 * t + 100 * a:.12g}"
                              for a, b, c in zip(x, y, z, strict=True)]  # fmt: skip
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_a_field_over_two_files_is_exact_between_and_held_outside(tmp_path):
    first = _linear_csv(tmp_path / "t0.csv", 0.0)
    second = _linear_csv(tmp_path / "t10.csv", 10.0)
    root = tmp_path / "project"
    p, f = _made(root, first, files=[second], times=[0.0, 10.0])
    assert f.varies() and [t.file for t in f.times] == ["fields/t0.csv", "fields/t10.csv"]
    targets = np.array([[0.033, 0.07, 0.01], [0.09, 0.02, 0.05]])
    x = targets[:, 0]
    for t, want in ((4.0, 340 + 100 * x), (-1.0, 300 + 100 * x), (25.0, 400 + 100 * x)):
        assert _values(p, f, root, "T", targets, t) == pytest.approx(want, rel=1e-12)
    with pytest.raises(Exception, match="varies in time"):
        _values(p, f, root, "T", targets)
    with pytest.raises(Exception, match="2 files need 2 times"):
        _made(root, first, files=[second])


def test_a_time_column_groups_the_rows(tmp_path):
    a = _linear_csv(tmp_path / "a.csv", 0.0).read_text(encoding="utf-8").splitlines()
    b = _linear_csv(tmp_path / "b.csv", 30.0).read_text(encoding="utf-8").splitlines()
    path = tmp_path / "series.csv"
    path.write_text("\n".join(["time [min]," + a[0]] + [f"0,{r}" for r in a[1:]]
                              + [f"0.5,{r}" for r in b[1:]]) + "\n", encoding="utf-8")  # fmt: skip
    found = sniff(path)
    assert found["columns"][0] == {"name": "time", "role": "time", "unit": "min"}
    root = tmp_path / "project"
    p, f = _made(root, path)
    assert f.varies() and [t for t, _ in clouds(p, root, f)] == [0.0, 30.0]  # seconds
    at = np.array([[0.05, 0.05, 0.05]])
    assert _values(p, f, root, "T", at, 15.0) == pytest.approx(300 + 150 + 5, rel=1e-12)


def test_new_field_settings_keep_older_hashes(tmp_path):
    from puzzle.project.graph import _own

    p, f = _made(tmp_path, _linear_csv(tmp_path / "t.csv", 0.0))
    assert not {"times", "region", "index"} & set(_own(f))
    f.region = "walls"
    assert _own(f)["region"] == "walls"


def _two_step_project(root, use_time_field=True):
    first = _linear_csv(root / "t0.csv", 0.0)
    second = _linear_csv(root / "t10.csv", 10.0)
    p = Project(name="steps")
    f = p.add(ImportedField(**new_field_data(root, first, "Oven", files=[second],
                                             times=[0.0, 10.0])))  # fmt: skip
    geo = p.add(Geometry(name="G", source="g.step"))
    recipe = p.add(MeshRecipe(name="M"))
    pressure = Feature(type="pressure", selection="Box/Top", values={"p": Quantity(value=0,
                       unit="Pa")}, fields={"p": FieldUse(field=f.id, column="T")})  # fmt: skip
    heat = Feature(type="body_temperature", selection="Box", values={"T": Quantity(value=20,
                   unit="degC")}, fields={"T": FieldUse(field=f.id, column="T")})  # fmt: skip
    comp = p.add(Component(name="C", mesh=recipe.id, parts=[PartInstance(name="Box",
                 geometry=geo.id)], physics=[Physics(type="solid_mechanics",
                 features=[pressure, heat])]))  # fmt: skip
    steps = [
        SolveStep(type="stationary"),
        SolveStep(type="stationary", settings={"end_time": Quantity(value=7.5, unit="s")}),
    ]
    study = p.add(Study(name="S", component=comp.id, solver="calculix", steps=steps))
    return p, study


def test_each_solve_step_takes_the_field_at_its_time(tmp_path):
    p, study = _two_step_project(tmp_path)
    mesh = cube(1)
    assert [step_time(s, k) for k, s in enumerate(study.solve_steps(), 1)] == [1.0, 7.5]
    made = field_inputs(p, tmp_path, study, mesh)
    by = {(i.role, i.step): i.field for i in made}
    assert set(by) == {("feature", 1), ("feature", 2), ("load", 1), ("load", 2)}
    top = mesh.groups["Box/Top"].node_ids
    x = mesh.node_coords(top)[:, 0]
    assert by[("feature", 1)].at(top)[:, 0] == pytest.approx(310 + 100 * x, rel=1e-12)
    assert by[("feature", 2)].at(top)[:, 0] == pytest.approx(375 + 100 * x, rel=1e-12)
    every = mesh.node_ids
    assert by[("load", 2)].at(every)[:, 0] == pytest.approx(375 + 100 * mesh.coords[:, 0])
    # One job per step (split studies) sees its own step's values as plain inputs.
    from puzzle.solve import _step_inputs

    steps = study.solve_steps()
    seen = _step_inputs(made, 2, [None, None], steps)
    assert {i.step for i in seen} == {None} and len(seen) == 2
    assert seen[0].field.at(top)[:, 0] == pytest.approx(375 + 100 * x, rel=1e-12)
    assert _step_inputs(made, 1, [None], steps) is made  # one deck: all, tagged


def test_a_transient_step_takes_the_history_where_the_solver_follows_it(tmp_path):
    from puzzle.adapters.calculix import CalculixAdapter
    from puzzle.adapters.code_aster import CodeAsterAdapter

    p, study = _two_step_project(tmp_path)
    comp = p.get(study.component, Component)
    comp.physics[0].features.pop()  # the body temperature can't follow a transient step
    study.steps = [SolveStep(type="transient", settings={"time_step": 1.0, "end_time": 10.0})]
    made = field_inputs(p, tmp_path, study, cube(1))
    [i] = made
    assert i.times == (0.0, 10.0) and i.field.values.shape[1] == 2
    assert not any("follow" in m for m in CalculixAdapter().check(p, study))
    assert any("through a transient step" in m for m in CodeAsterAdapter().check(p, study))


def test_the_cli_and_the_agent_take_times(tmp_path, capsys):
    pytest.importorskip("mcp")
    import asyncio
    import shutil

    from puzzle.agent.mcp import build_server
    from puzzle.cli import main
    from puzzle.project.store import load_project, save_project

    root = tmp_path / "proj"
    save_project(Project(name="cli"), root)
    a, b = _linear_csv(tmp_path / "a.csv", 0.0), _linear_csv(tmp_path / "b.csv", 60.0)
    assert main(["import-field", str(root), str(a), str(b), "--times", "0", "60"]) == 0
    assert "varies in time: 0 s, 60 s" in capsys.readouterr().out
    [f] = load_project(root).of_type(ImportedField)
    assert [t.time for t in f.times] == [0.0, 60.0]
    shutil.copytree(DATA / "channel", root / "cases" / "channel")
    server = build_server(root, "test")
    args = {"file": "cases/channel", "region": "walls", "every_step": True}
    result = asyncio.run(server.call_tool("field_import", args))
    out = result.structured_content.get("result", result.structured_content)
    data = out["create"]["data"]
    assert data["format"] == "openfoam" and data["region"] == "walls"
    assert [t["index"] for t in data["times"]] == ["0", "1"]
