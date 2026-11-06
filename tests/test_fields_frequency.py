"""Imported fields varying with frequency (ADR 0044), for harmonic steps (ADR 0041).

A field linear in x and in frequency (p = 1000 + 10 f + 100 x Pa) from a CSV with a
frequency column, or two files at 100 and 300 Hz, is exact at any frequency between and
held outside; a harmonic step gets its values at each of the field's frequencies for
the adapter to interpolate, which CalculiX, Code_Aster and FEniCSx do (the solves are
in tests/integration/test_imported_fields_frequency.py) and Elmer refuses. A field
varying with frequency outside a harmonic step, or one varying in time inside one, is
refused with the code field_axis.
"""

import numpy as np
import pytest
from exports_mesh import cube
from pydantic import ValidationError

from puzzle.errors import CODES, PuzzleError
from puzzle.fields import clouds, field_inputs, new_field_data, sniff, use_values
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


def _rows(f):
    g = np.linspace(0.0, 1.0, 5)
    x, y, z = (a.ravel() for a in np.meshgrid(g, g, g, indexing="ij"))
    return [f"{a:.9g},{b:.9g},{c:.9g},{1000 + 10 * f + 100 * a:.12g}"
            for a, b, c in zip(x, y, z, strict=True)]  # fmt: skip


def _file(path, f):
    path.write_text("\n".join(["x,y,z,p [Pa]", *_rows(f)]) + "\n", encoding="utf-8")
    return path


def _spectrum(path):
    """A frequency column in kHz: rows at 0.1 and 0.3 kHz."""
    rows = ["f [kHz],x,y,z,p [Pa]"]
    rows += [f"0.1,{r}" for r in _rows(100.0)] + [f"0.3,{r}" for r in _rows(300.0)]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_a_frequency_column_groups_the_rows_in_hertz(tmp_path):
    found = sniff(_spectrum(tmp_path / "s.csv"))
    assert found["columns"][0] == {"name": "f", "role": "frequency", "unit": "kHz"}
    root = tmp_path / "project"
    p = Project(name="f")
    f = p.add(ImportedField(**new_field_data(root, tmp_path / "s.csv", "S")))
    assert f.varies() and f.along() == "frequency"
    assert [t for t, _ in clouds(p, root, f)] == pytest.approx([100.0, 300.0])
    at = np.array([[0.5, 0.5, 0.5]])
    use = FieldUse(field=f.id, column="p")
    for hz, want in ((150.0, 2550.0), (50.0, 2050.0), (900.0, 4050.0)):  # held outside
        assert use_values(p, root, use, at, time=hz)[0] == pytest.approx([want], rel=1e-12)
    with pytest.raises(PuzzleError, match="varies with frequency"):
        use_values(p, root, use, at)


def test_files_at_frequencies_and_one_axis_only(tmp_path):
    a, b = _file(tmp_path / "a.csv", 100.0), _file(tmp_path / "b.csv", 300.0)
    root = tmp_path / "project"
    data = new_field_data(root, a, "S", files=[b], frequencies=[100.0, 300.0])
    f = ImportedField(**data)
    assert data["axis"] == "frequency" and f.along() == "frequency"
    assert [t.time for t in f.times] == [100.0, 300.0]
    with pytest.raises(PuzzleError, match="not both"):
        new_field_data(root, a, "S", files=[b], times=[0, 1], frequencies=[1, 2])
    still = ImportedField(**new_field_data(root, a, "Still"))
    assert still.along() is None
    columns = [FieldColumn(name=n, role=r) for n, r in (("x", "x"), ("y", "y"), ("z", "z"),
               ("t", "time"), ("f", "frequency"), ("p", "value"))]  # fmt: skip
    with pytest.raises(ValidationError, match="not both"):
        ImportedField(name="Both", file="x.csv", columns=columns)
    assert "field_axis" in CODES


def test_an_axis_left_at_time_keeps_older_hashes(tmp_path):
    from puzzle.project.graph import _own

    a = _file(tmp_path / "a.csv", 100.0)
    f = ImportedField(**new_field_data(tmp_path / "p", a, "S"))
    assert "axis" not in _own(f)
    f.axis = "frequency"
    assert _own(f)["axis"] == "frequency"


def _project(root, steps, frequency=True, solver="calculix"):
    root.mkdir(parents=True, exist_ok=True)
    a, b = _file(root / "a.csv", 100.0), _file(root / "b.csv", 300.0)
    p = Project(name="harmonic")
    axis = {"frequencies": [100.0, 300.0]} if frequency else {"times": [0.0, 10.0]}
    f = p.add(ImportedField(**new_field_data(root, a, "Spectrum", files=[b], **axis)))
    geo = p.add(Geometry(name="G", source="g.step"))
    recipe = p.add(MeshRecipe(name="M"))
    pressure = Feature(type="pressure", selection="Box/Top", values={"p": Quantity(value=0,
                       unit="Pa")}, fields={"p": FieldUse(field=f.id, column="p")})  # fmt: skip
    fixed = Feature(type="fixed", selection="Box/Bottom")
    comp = p.add(Component(name="C", mesh=recipe.id, parts=[PartInstance(name="Box",
                 geometry=geo.id)], physics=[Physics(type="solid_mechanics",
                 features=[fixed, pressure])]))  # fmt: skip
    study = p.add(Study(name="S", component=comp.id, solver=solver, steps=steps))
    return p, study, pressure


HARMONIC = SolveStep(type="frequency_domain", physics=["solid_mechanics"],
                     settings={"frequencies": "150, 250"})  # fmt: skip


def test_a_harmonic_step_gets_the_values_at_each_frequency(tmp_path):
    p, study, _ = _project(tmp_path, [HARMONIC])
    mesh = cube(1)
    [i] = field_inputs(p, tmp_path, study, mesh)
    assert i.frequencies == (100.0, 300.0) and i.times is None and i.step == 1
    assert i.field.components == ["100 Hz", "300 Hz"]
    top = mesh.groups["Box/Top"].node_ids
    x = mesh.node_coords(top)[:, 0]
    assert i.field.at(top)[:, 0] == pytest.approx(2000 + 100 * x, rel=1e-12)
    assert i.field.at(top)[:, 1] == pytest.approx(4000 + 100 * x, rel=1e-12)


def test_an_axis_that_does_not_fit_the_step_is_refused(tmp_path):
    from puzzle.adapters.calculix import CalculixAdapter

    p, study, _ = _project(tmp_path / "a", [SolveStep(type="stationary"), HARMONIC])
    with pytest.raises(PuzzleError, match="harmonic .* steps only") as raised:
        field_inputs(p, tmp_path / "a", study, cube(1))
    assert raised.value.code == "field_axis"
    assert any("harmonic steps only" in m for m in CalculixAdapter().check(p, study))
    p2, study2, pressure = _project(tmp_path / "c", [SolveStep(type="stationary"), HARMONIC])
    pressure.steps = [2]  # limited to the harmonic step: fine
    assert len(field_inputs(p2, tmp_path / "c", study2, cube(1))) == 1
    assert not any("frequency" in m for m in CalculixAdapter().check(p2, study2))
    p3, study3, _ = _project(tmp_path / "d", [HARMONIC], frequency=False)
    with pytest.raises(PuzzleError, match="frequency axis") as raised:
        field_inputs(p3, tmp_path / "d", study3, cube(1))
    assert raised.value.code == "field_axis"
    assert any("varying in time" in m for m in CalculixAdapter().check(p3, study3))


@pytest.mark.parametrize(
    ("solver", "takes"),
    [("calculix", True), ("code_aster", True), ("fenicsx", True), ("elmer", False)],
)
def test_the_solvers_that_cannot_apply_it_refuse_it(tmp_path, solver, takes):
    from puzzle.adapters import available_adapters

    p, study, _ = _project(tmp_path, [HARMONIC], solver=solver)
    problems = [m for m in available_adapters()[solver]().check(p, study) if "frequency" in m]
    assert not problems if takes else problems, problems


def test_code_aster_multiplies_a_charge_per_frequency_by_a_hat_function():
    from puzzle.adapters.code_aster.comm import _spectral_loads

    rows = [["_F(GROUP_NO='FP1N1', FX=0, FY=-1, FZ=0)"]] * 3
    out = _spectral_loads([((100.0, 200.0, 400.0), rows)])
    assert len(out["charges"]) == 3 and out["vectors"].count("OPTION='CHAR_MECA'") == 3
    assert out["functions"][0].startswith("ht1_1 = DEFI_FONCTION(NOM_PARA='FREQ', "
                                          "VALE=(100, 1, 200, 0)")  # fmt: skip
    assert "VALE=(100, 0, 200, 1, 400, 0)" in out["functions"][1]
    assert "VALE=(200, 0, 400, 1)" in out["functions"][2]
    assert out["excit"].count("FONC_MULT=ht1_") == 3
    one = _spectral_loads([((100.0,), rows[:1])])
    assert not one["functions"] and "COEF_MULT=1.0" in one["excit"]


def test_the_cli_and_the_agent_take_frequencies(tmp_path, capsys):
    pytest.importorskip("mcp")
    import asyncio
    import shutil

    from puzzle.agent.mcp import build_server
    from puzzle.cli import main
    from puzzle.project.store import load_project, save_project

    root = tmp_path / "proj"
    save_project(Project(name="cli"), root)
    a, b = _file(tmp_path / "a.csv", 100.0), _file(tmp_path / "b.csv", 300.0)
    assert main(["import-field", str(root), str(a), str(b), "--frequencies", "100", "300"]) == 0
    assert "varies with frequency: 100 Hz, 300 Hz" in capsys.readouterr().out
    [f] = load_project(root).of_type(ImportedField)
    assert f.along() == "frequency" and [t.time for t in f.times] == [100.0, 300.0]
    shutil.copy(a, root / "a.csv")
    shutil.copy(b, root / "b.csv")
    server = build_server(root, "test")
    args = {"file": "a.csv", "files": ["b.csv"], "frequencies": [100.0, 300.0]}
    result = asyncio.run(server.call_tool("field_import", args))
    out = result.structured_content.get("result", result.structured_content)
    data = out["create"]["data"]
    assert data["axis"] == "frequency" and [t["time"] for t in data["times"]] == [100.0, 300.0]
