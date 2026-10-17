from pathlib import Path

import numpy as np
import pytest
from conftest_geometry import bar_project, write_bar_step

from puzzle.adapters.calculix import CalculixAdapter, InputError
from puzzle.adapters.calculix.frd import read_frd
from puzzle.adapters.calculix.inp import set_name, write_inp
from puzzle.mesh import generate_mesh
from puzzle.model import Feature, Physics, Quantity, SolveStep, Study

DATA = Path(__file__).parent / "data"


def _cantilever(tmp_path, features, steps, **kwargs):
    step = write_bar_step(tmp_path / "bar.step")
    physics = [Physics(type="solid_mechanics", features=features)]
    project, comp = bar_project(step, physics, **kwargs)
    study = project.add(Study(name="Static", component=comp.id, solver="calculix", steps=steps))
    mesh = generate_mesh(project, comp.id, tmp_path, tmp_path / "mesh").mesh
    return project, study, mesh


def _load(cards: str, card: str) -> list[list[str]]:
    lines = cards.splitlines()
    i = lines.index(card) + 1
    rows = []
    while i < len(lines) and not lines[i].startswith("*"):
        rows.append([x.strip() for x in lines[i].split(",")])
        i += 1
    return rows


def test_set_names():
    assert set_name("N", "Bar/Root face") == "N_BAR_ROOT_FACE"


def test_deck_has_the_model(tmp_path):
    fixed = Feature(type="fixed", selection="Bar/Root")
    force = Feature(type="force", selection="Bar/Tip", values={"y": Quantity(value=-100, unit="N")})
    project, study, mesh = _cantilever(tmp_path, [fixed, force], [SolveStep(type="stationary")])
    deck = write_inp(project, study, mesh)

    assert "*ELEMENT, TYPE=C3D10, ELSET=E_BAR" in deck
    assert "*MATERIAL, NAME=M1" in deck and "*SOLID SECTION, ELSET=E_BAR_BODY, MATERIAL=M1" in deck
    assert "N_BAR_ROOT, 1, 3, 0." in deck
    assert "*STATIC" in deck and "*NODE FILE\nU" in deck

    forces = _load(deck, "*CLOAD")
    assert all(row[1] == "2" for row in forces)
    assert sum(float(row[2]) for row in forces) == pytest.approx(-100.0)
    tip_nodes = set(mesh.groups["Bar/Tip"].node_ids.tolist())
    assert {int(row[0]) for row in forces} <= tip_nodes


def test_tet10_nodes_are_reordered_for_calculix(tmp_path):
    project, study, mesh = _cantilever(
        tmp_path, [Feature(type="fixed", selection="Bar/Root")], [SolveStep(type="stationary")]
    )
    block = next(b for b in mesh.blocks if b.element == "tet10")
    deck = write_inp(project, study, mesh).splitlines()
    elements = deck[next(i for i, line in enumerate(deck) if line.startswith("*ELEMENT")) :]
    row = next(line for line in elements if line.startswith(f"{block.ids[0]}, "))
    written = [int(x) for x in row.split(",")[1:]]
    gmsh_order = block.connectivity[0]
    assert written[:8] == gmsh_order[:8].tolist()
    assert written[8:] == [gmsh_order[9], gmsh_order[8]]


def test_pressure_uses_element_faces(tmp_path):
    pressure = Feature(
        type="pressure", selection="Bar/Tip", values={"p": Quantity(value=1, unit="MPa")}
    )
    project, study, mesh = _cantilever(
        tmp_path,
        [Feature(type="fixed", selection="Bar/Root"), pressure],
        [SolveStep(type="stationary")],
    )
    rows = _load(write_inp(project, study, mesh), "*DLOAD")
    assert rows and all(r[1] in {"P1", "P2", "P3", "P4"} and float(r[2]) == 1e6 for r in rows)


def test_modal_step_writes_frequency_and_drops_loads(tmp_path):
    force = Feature(type="force", selection="Bar/Tip", values={"y": Quantity(value=-1, unit="N")})
    steps = [SolveStep(type="modal", settings={"modes": 4})]
    project, study, mesh = _cantilever(
        tmp_path, [Feature(type="fixed", selection="Bar/Root"), force], steps
    )
    deck = write_inp(project, study, mesh)
    assert "*FREQUENCY\n4" in deck
    assert "*CLOAD" not in deck


def test_heat_and_structure_in_one_step_need_the_coupling(tmp_path):
    step = write_bar_step(tmp_path / "bar.step")
    project, comp = bar_project(
        step, [Physics(type="solid_mechanics"), Physics(type="heat_transfer")]
    )
    study = project.add(
        Study(name="s", component=comp.id, solver="calculix", steps=[SolveStep(type="stationary")])
    )
    mesh = generate_mesh(project, comp.id, tmp_path, tmp_path / "mesh").mesh
    with pytest.raises(InputError, match="thermal_expansion"):
        write_inp(project, study, mesh)


def test_frd_reader_static_and_modal():
    results = read_frd(DATA / "cube_static_modal.frd")
    static, mode1, mode2 = results.steps
    assert (static.index, static.mode) == (1, None)
    assert set(static.fields) == {"displacement", "stress", "strain"}
    disp = static.fields["displacement"]
    assert disp.components == ["x", "y", "z"]
    assert disp.at(np.array([2]))[0, 0] == pytest.approx(4.55e-9)
    assert disp.at(np.array([3]))[0, 1] == pytest.approx(
        -9.75e-10
    )  # fields run together in the file
    assert static.fields["stress"].values.shape == (8, 6)
    assert [m.mode for m in results.modes()] == [1, 2]
    assert mode1.value == pytest.approx(636.8916146)


def test_adapter_reports_missing_results(tmp_path):
    with pytest.raises(FileNotFoundError, match="job.frd"):
        CalculixAdapter().read_results(tmp_path / "job.inp")
