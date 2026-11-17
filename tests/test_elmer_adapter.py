import math

import numpy as np
import pytest
from conftest_geometry import bar_project, write_bar_step

from puzzle.adapters.common import InputError
from puzzle.adapters.elmer import ElmerAdapter
from puzzle.adapters.elmer.output import eigenvalues, read_results, read_series, read_vtu
from puzzle.errors import PuzzleError
from puzzle.mesh import generate_mesh
from puzzle.model import Feature, Physics, Quantity, SolveStep, Study


def _case(tmp_path, features, step, physics="solid_mechanics"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    stepfile = write_bar_step(tmp_path / "bar.step")
    project, comp = bar_project(
        stepfile, [Physics(type=physics, features=features)], mesh_size_mm=5
    )
    study = project.add(Study(name="s", component=comp.id, solver="elmer", steps=[step]))
    mesh = generate_mesh(project, comp.id, tmp_path, tmp_path / "m").mesh
    sif = ElmerAdapter().write_input(project, study, mesh, tmp_path / "case")
    return sif, mesh


def test_mesh_files_describe_the_mesh(tmp_path):
    fixed = Feature(type="fixed", selection="Bar/Root")
    sif, mesh = _case(tmp_path, [fixed], SolveStep(type="stationary"))
    d = sif.parent / "mesh"
    header = (d / "mesh.header").read_text(encoding="utf-8").split()
    n_nodes, n_elements, n_boundary = map(int, header[:3])
    assert n_nodes == len(mesh.node_ids)
    assert n_elements == sum(len(b.ids) for b in mesh.blocks if b.dim == 3)
    assert n_boundary == sum(len(b.ids) for b in mesh.groups["Bar/Root"].blocks(mesh) if b.dim == 2)
    first = (d / "mesh.elements").read_text(encoding="utf-8").splitlines()[0].split()
    assert first[1:3] == ["1", "510"] and len(first) == 3 + 10  # body 1, quadratic tetra
    boundary = (d / "mesh.boundary").read_text(encoding="utf-8").splitlines()[0].split()
    assert (
        boundary[1] == "1" and int(boundary[2]) > 0 and boundary[4] == "306"
    )  # with a parent element


def test_sif_translates_loads(tmp_path):
    fixed = Feature(type="fixed", selection="Bar/Root")
    force = Feature(type="force", selection="Bar/Tip", values={"y": Quantity(value=-100, unit="N")})
    sif, _ = _case(tmp_path, [fixed, force], SolveStep(type="stationary"))
    text = sif.read_text(encoding="utf-8")
    assert '"StressSolve" "StressSolver"' in text and "Calculate Stresses = True" in text
    assert "Displacement 1 = 0" in text
    traction = float(next(line for line in text.splitlines() if "Force 2 =" in line).split("=")[1])
    assert traction == pytest.approx(-100 / (0.01 * 0.01), rel=1e-9)  # force over the tip area
    assert "Youngs Modulus = " in text and "Poisson Ratio = 0.3" in text


def test_pressure_becomes_normal_force(tmp_path):
    p = Feature(type="pressure", selection="Bar/Tip", values={"p": Quantity(value=2, unit="MPa")})
    sif, _ = _case(
        tmp_path, [Feature(type="fixed", selection="Bar/Root"), p], SolveStep(type="stationary")
    )
    assert "Normal Force = -2000000" in sif.read_text(encoding="utf-8")


def test_modal_and_heat_solvers(tmp_path):
    sif, _ = _case(
        tmp_path / "a",
        [Feature(type="fixed", selection="Bar/Root")],
        SolveStep(type="modal", settings={"modes": 5}),
    )
    assert "Eigen System Values = 5" in sif.read_text(encoding="utf-8")
    hot = Feature(
        type="temperature", selection="Bar/Root", values={"T": Quantity(value=100, unit="degC")}
    )
    sif, _ = _case(tmp_path / "b", [hot], SolveStep(type="stationary"), physics="heat_transfer")
    text = sif.read_text(encoding="utf-8")
    assert '"HeatSolve" "HeatSolver"' in text and "Temperature = 373.15" in text
    # Structural steel's EN 1993-1-2 tables become functions of temperature.
    assert "Heat Conductivity = Variable Temperature" in text and "573.15 44.01" in text
    assert "Heat Capacity = Variable Temperature" in text
    assert "Nonlinear System Max Iterations = 50" in text


def test_unsupported_steps_are_refused(tmp_path):
    with pytest.raises(InputError, match="transient"):
        _case(tmp_path, [Feature(type="fixed", selection="Bar/Root")], SolveStep(type="transient"))


VTU = """<?xml version="1.0"?>
<VTKFile type="UnstructuredGrid" version="0.1">
  <UnstructuredGrid><Piece NumberOfPoints="3" NumberOfCells="1">
    <PointData>
      <DataArray type="Float64" Name="displacement" NumberOfComponents="3" format="ascii">
        1 2 3  4 5 6  7 8 9
      </DataArray>
      <DataArray type="Float64" Name="displacement EigenMode1"
                 NumberOfComponents="3" format="ascii">
        0 0 1  0 0 2  0 0 3
      </DataArray>
    </PointData>
    <CellData>
      <DataArray type="Int32" Name="GeometryIds" format="ascii">42</DataArray>
    </CellData>
  </Piece></UnstructuredGrid>
</VTKFile>
"""
LOG = """EigenSolve: Computed 1 Eigen Values
EigenSolve: 1:    1.642173E+06   0.000000E+00
"""


def test_reader_maps_points_to_puzzle_nodes_and_modes_to_hz(tmp_path):
    vtu = tmp_path / "case_t0001.vtu"
    vtu.write_text(VTU, encoding="utf-8")
    assert set(read_vtu(vtu)) == {"displacement", "displacement EigenMode1"}  # cell data skipped
    results = read_results(vtu, np.array([30, 10, 20]), LOG)
    (mode,) = results.modes()
    assert mode.value == pytest.approx(math.sqrt(1.642173e6) / (2 * math.pi))
    disp = mode.fields["displacement"]
    assert list(disp.ids) == [10, 20, 30] and list(disp.values[:, 2]) == [2, 3, 1]


def test_eigenvalue_parsing():
    assert eigenvalues(LOG + "EigenSolve: 2:    6.3E+07   0.0E+00\n") == [1.642173e6, 6.3e7]


def test_transient_heat_sif(tmp_path):
    hot = Feature(
        type="temperature", selection="Bar/Root", values={"T": Quantity(value=100, unit="degC")}
    )
    settings = {"time_step": Quantity(value=5, unit="s"), "end_time": Quantity(value=30, unit="s")}
    sif, _ = _case(
        tmp_path, [hot], SolveStep(type="transient", settings=settings), physics="heat_transfer"
    )
    text = sif.read_text(encoding="utf-8")
    assert "Simulation Type = Transient" in text and "Timestep Sizes(1) = 5" in text
    assert "Timestep Intervals(1) = 6" in text and "Exec Solver = After Timestep" in text
    times = (sif.parent / "mesh" / "puzzle-times.txt").read_text(encoding="utf-8").split()
    assert [float(t) for t in times] == [5, 10, 15, 20, 25, 30]


def test_output_every_writes_only_the_output_steps(tmp_path):
    """ADR 0090 ELMER-1: the result output runs every k-th step (from the k-th), so
    Elmer writes one file per listed time."""
    hot = Feature(
        type="temperature", selection="Bar/Root", values={"T": Quantity(value=100, unit="degC")}
    )
    settings = {"time_step": Quantity(value=5, unit="s"), "end_time": Quantity(value=35, unit="s"),
                "output_every": 3}  # fmt: skip
    sif, _ = _case(
        tmp_path, [hot], SolveStep(type="transient", settings=settings), physics="heat_transfer"
    )
    text = sif.read_text(encoding="utf-8")
    output = text[text.index('Equation = "Result Output"') :].split("End")[0]
    assert "Exec Intervals = 3" in output and "Exec Intervals Offset = 2" in output
    times = (sif.parent / "mesh" / "puzzle-times.txt").read_text(encoding="utf-8").split()
    assert [float(t) for t in times] == [15, 30]


HEAT_VTU = """<?xml version="1.0"?>
<VTKFile type="UnstructuredGrid" version="0.1">
  <UnstructuredGrid><Piece NumberOfPoints="2" NumberOfCells="1">
    <PointData>
      <DataArray type="Float64" Name="temperature" NumberOfComponents="1" format="ascii">
        {a} {b}
      </DataArray>
    </PointData>
  </Piece></UnstructuredGrid>
</VTKFile>
"""


def test_reader_refuses_files_that_are_not_one_per_time(tmp_path):
    vtus = []
    for k in range(1, 5):
        vtus.append(tmp_path / f"case_t{k:04d}.vtu")
        vtus[-1].write_text(HEAT_VTU.format(a=k, b=10 * k), encoding="utf-8")
    ids = np.array([1, 2])
    results = read_series(vtus, [5.0, 10.0, 15.0, 20.0], ids, "")
    assert [s.value for s in results.steps] == [5, 10, 15, 20]
    assert results.steps[2].fields["temperature"].values[:, 0].tolist() == [3, 30]
    with pytest.raises(PuzzleError, match="4 result files where 2") as info:
        read_series(vtus, [10.0, 20.0], ids, "")  # every step written, every 2nd expected
    assert info.value.code == "solver_failed"
    with pytest.raises(PuzzleError, match="1 result files where 2"):
        read_series(vtus[:1], [10.0, 20.0], ids, "")
    (one,) = read_series(vtus[:1], [10.0], ids, "").steps  # a one-output transient: its time
    assert one.value == 10.0


def test_electrostatics_sif(tmp_path):
    from puzzle.model import Material

    tmp_path.mkdir(parents=True, exist_ok=True)
    stepfile = write_bar_step(tmp_path / "bar.step")
    ground = Feature(type="ground", selection="Bar/Root")
    live = Feature(
        type="electric_potential", selection="Bar/Tip", values={"V": Quantity(value=5, unit="kV")}
    )
    project, comp = bar_project(
        stepfile, [Physics(type="electrostatics", features=[ground, live])], mesh_size_mm=5
    )
    project.of_type(Material)[0].properties["relative_permittivity"] = Quantity(value=4.5)
    study = project.add(
        Study(name="e", component=comp.id, solver="elmer", steps=[SolveStep(type="stationary")])
    )
    mesh = generate_mesh(project, comp.id, tmp_path, tmp_path / "m").mesh
    text = (
        ElmerAdapter()
        .write_input(project, study, mesh, tmp_path / "case")
        .read_text(encoding="utf-8")
    )
    assert '"StatElecSolve" "StatElecSolver"' in text and "Calculate Electric Energy = True" in text
    assert (
        "Relative Permittivity = 4.5" in text
        and "Permittivity Of Vacuum = 8.8541878128e-12" in text
    )
    assert "Potential = 0" in text and "Potential = 5000" in text


def test_reader_picks_up_energy():
    vtu_log = "StatElecSolve: Tot. Electric Energy  :   1.2345E-07\n"
    from puzzle.adapters.elmer.output import ENERGY_LINE

    assert float(ENERGY_LINE.findall(vtu_log)[-1]) == pytest.approx(1.2345e-7)


def test_magnetostatics_sif(tmp_path):
    from puzzle.model import Material

    tmp_path.mkdir(parents=True, exist_ok=True)
    stepfile = write_bar_step(tmp_path / "bar.step")
    features = [
        Feature(
            type="coil_current", selection="Bar/Body", values={"I": Quantity(value=2, unit="kA")}
        ),
        Feature(type="coil_start", selection="Bar/Root"),
        Feature(type="coil_end", selection="Bar/Tip"),
    ]
    project, comp = bar_project(
        stepfile, [Physics(type="magnetic_fields", features=features)], mesh_size_mm=5
    )
    steel = project.of_type(Material)[0]
    steel.properties["relative_permeability"] = Quantity(value=1)
    steel.properties["electrical_conductivity"] = Quantity(value=5.8e7, unit="S/m")
    study = project.add(
        Study(name="m", component=comp.id, solver="elmer", steps=[SolveStep(type="stationary")])
    )
    mesh = generate_mesh(project, comp.id, tmp_path, tmp_path / "m").mesh
    text = (
        ElmerAdapter()
        .write_input(project, study, mesh, tmp_path / "case")
        .read_text(encoding="utf-8")
    )
    assert "Desired Coil Current = Real 2000" in text
    assert '"MagnetoDynamics" "WhitneyAVSolver"' in text and "Solver 4" in text  # + output
    assert "Equation = 1" in text and "Body Force = 1" in text
    assert "Coil Start = Logical True" in text and "Coil End = Logical True" in text
    assert "Relative Permeability = 1" in text and "Electric Conductivity = 58000000" in text


def test_magnetostatics_needs_a_coil(tmp_path):
    insulated = Feature(type="magnetic_insulation", selection="Bar/Root")
    with pytest.raises(InputError, match="one coil"):
        _case(tmp_path, [insulated], SolveStep(type="stationary"), physics="magnetic_fields")


def test_reader_picks_up_magnetic_energy():
    from puzzle.adapters.elmer.output import MAGNETIC_ENERGY_LINE

    line = "MagnetoDynamicsCalcFields: ElectroMagnetic Field Energy:    1.840431E-05\n"
    assert float(MAGNETIC_ENERGY_LINE.findall(line)[-1]) == pytest.approx(1.840431e-5)


@pytest.mark.parametrize(
    ("scheme", "lines"),
    [
        ({}, ["Timestepping Method = BDF", "BDF Order = 1"]),
        ({"time_scheme": "bdf2"}, ["BDF Order = 2"]),
        ({"time_scheme": "crank_nicolson"}, ["Timestepping Method = Crank-Nicolson"]),
        (
            {"time_scheme": "theta", "theta": 0.6},
            ["Timestepping Method = Newmark", "Newmark Beta = 0.6"],
        ),
    ],
)
def test_time_schemes(tmp_path, scheme, lines):
    hot = Feature(
        type="temperature", selection="Bar/Root", values={"T": Quantity(value=100, unit="degC")}
    )
    settings = {"time_step": Quantity(value=5, unit="s"), "end_time": Quantity(value=30, unit="s")}
    sif, _ = _case(
        tmp_path,
        [hot],
        SolveStep(type="transient", settings=settings | scheme),
        physics="heat_transfer",
    )
    text = sif.read_text(encoding="utf-8")
    assert all(line in text for line in lines)


TABLE_SIF = """Solver 1
  Equation = "Stress"
  Frequency = Variable Time
    Real
      1 10.0
      2 20.0
    End
  Linear System Solver = Direct
End
"""


def test_overrides_skip_a_tables_end():
    """ADR 0090 ELMER-2: a table's indented `End` does not end its section, so a new
    keyword goes before the section's own End and one after the table is replaced."""
    from puzzle.adapters.elmer.sif_edit import apply, sections

    assert sections(TABLE_SIF) == [("Solver 1", 0, 8)]
    added = apply(TABLE_SIF, {"Solver */Nonlinear System Max Iterations": "5"}).split("\n")
    assert added[6:9] == ["    End", "  Linear System Solver = Direct",
                          "  Nonlinear System Max Iterations = 5"]  # fmt: skip
    assert added[9] == "End"
    replaced = apply(TABLE_SIF, {"Solver 1/Linear System Solver": "Iterative"})
    assert replaced.count("Linear System Solver") == 1
    assert "  Linear System Solver = Iterative\nEnd" in replaced
    assert "      2 20.0\n    End\n" in replaced  # the table untouched
    table = apply(TABLE_SIF, {"Solver 1/Frequency": "Real 5.0"})
    assert "Frequency = Real 5.0\n  Linear System Solver" in table and "Real\n" not in table


def test_expert_keyword_on_a_material_with_tables(tmp_path):
    """A material's temperature tables end in an indented End; an expert keyword for
    the material lands in the section, outside every table."""
    from puzzle.adapters.elmer.sif_edit import sections

    hot = Feature(
        type="temperature", selection="Bar/Root", values={"T": Quantity(value=100, unit="degC")}
    )
    tmp_path.mkdir(parents=True, exist_ok=True)
    stepfile = write_bar_step(tmp_path / "bar.step")
    project, comp = bar_project(
        stepfile, [Physics(type="heat_transfer", features=[hot])], mesh_size_mm=5
    )
    expert = {"elmer": {"Material 1/Emissivity": "Real 0.5"}}
    steps = [SolveStep(type="stationary")]
    study = project.add(
        Study(name="s", component=comp.id, solver="elmer", steps=steps, expert_settings=expert)
    )
    mesh = generate_mesh(project, comp.id, tmp_path, tmp_path / "m").mesh
    sif = ElmerAdapter().write_input(project, study, mesh, tmp_path / "case")
    text = sif.read_text(encoding="utf-8")
    lines = text.split("\n")
    ((_, first, end),) = [s for s in sections(text) if s[0] == "Material 1"]
    body = lines[first + 1 : end]
    assert "Variable Temperature" in "\n".join(body)  # the table is there
    assert body[-1] == "  Emissivity = Real 0.5" and body[-2] == "    End"


@pytest.mark.parametrize(("tables", "iterations"), [(True, 50), (False, 1)])
def test_linear_heat_is_solved_once(tmp_path, tables, iterations):
    """ADR 0090 ELMER-4: without temperature tables the heat equation is linear, and a
    second nonlinear iteration only confirms no change (a recorded transient log:
    ITER=2 change 0.0 at every step), so one is written."""
    hot = Feature(
        type="temperature", selection="Bar/Root", values={"T": Quantity(value=100, unit="degC")}
    )
    heat = Physics(type="heat_transfer", features=[hot],
                   settings={"temperature_dependent_materials": tables})  # fmt: skip
    stepfile = write_bar_step(tmp_path / "bar.step")
    project, comp = bar_project(stepfile, [heat], mesh_size_mm=5)
    study = project.add(Study(name="s", component=comp.id, solver="elmer",
                              steps=[SolveStep(type="stationary")]))  # fmt: skip
    mesh = generate_mesh(project, comp.id, tmp_path, tmp_path / "m").mesh
    sif = ElmerAdapter().write_input(project, study, mesh, tmp_path / "case")
    text = sif.read_text(encoding="utf-8")
    assert ("Variable Temperature" in text) is tables
    assert f"Nonlinear System Max Iterations = {iterations}\n" in text


def test_output_files_in_output_order():
    """ADR 0090 ELMER-5: files are taken by output number, so the 10,000th output (past
    Elmer's four padded digits) follows the 9,999th, not the 1,000th."""
    from pathlib import Path

    from puzzle.adapters.elmer.output import in_order

    names = ["case_t10000.vtu", "case_t1001.vtu", "case_t0002.vtu", "case_t9999.vtu"]
    assert [p.name for p in in_order(Path(n) for n in names)] == [
        "case_t0002.vtu", "case_t1001.vtu", "case_t9999.vtu", "case_t10000.vtu"]  # fmt: skip


def test_a_mode_without_its_eigenvalue_is_refused(tmp_path):
    """ADR 0090 ELMER-5: a mode shape whose eigenvalue the log lacks was stored with a
    NaN frequency; it is a failed read."""
    vtu = tmp_path / "case_t0001.vtu"
    vtu.write_text(VTU, encoding="utf-8")
    with pytest.raises(PuzzleError, match="1 mode shapes but its log gives 0") as info:
        read_results(vtu, np.array([30, 10, 20]), "")
    assert info.value.code == "solver_failed"


def test_an_iteration_limit_comes_with_a_tolerance(tmp_path):
    """ADR 0090 to-check 2: StressSolve has no nonlinear tolerance by default, so with
    `max_iterations` = 5 Elmer solved the (linear) elasticity five times (logged: ITER=1
    to 5, changes 2.0 then 0.0). A tolerance goes with the limit, and it stops at two."""
    fixed = Feature(type="fixed", selection="Bar/Root")
    plain, _ = _case(tmp_path / "a", [fixed], SolveStep(type="stationary"))
    assert "Nonlinear System" not in plain.read_text(encoding="utf-8")  # unset: unwritten
    step = SolveStep(type="stationary", settings={"max_iterations": 5})
    sif, _ = _case(tmp_path / "b", [fixed], step)
    text = sif.read_text(encoding="utf-8")
    elasticity = text[text.index('Equation = "Linear elasticity"') :].split("\nEnd")[0]
    assert "Nonlinear System Max Iterations = 5" in elasticity
    assert "Nonlinear System Convergence Tolerance = 1e-10" in elasticity
    step = SolveStep(type="stationary", settings={"max_iterations": 5, "tolerance": 1e-6})
    text = _case(tmp_path / "c", [fixed], step)[0].read_text(encoding="utf-8")
    assert "Nonlinear System Convergence Tolerance = 1e-06" in text and "1e-10" not in text
