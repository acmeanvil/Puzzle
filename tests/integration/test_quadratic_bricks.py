"""Second-order bricks (ADR 0082): Gmsh's 20-node (serendipity) and 27-node (Lagrange)
hexahedra on every FE solver that takes them, against Timoshenko's cantilever,
δ = PL³/3EI + PL/(κGA): the steel bar 200 x 10 x 10 mm, 100 N at its tip, structured
bricks 5 mm (two through the depth). Quadratic bricks don't lock, so each lands within
1 %; 20 and 27 nodes agree to 0.5 %.

- 20 nodes: CalculiX C3D20 (and C3D20R by an element choice), Code_Aster HEXA20 (and
  3D_SI), Elmer 820;
- 27 nodes: Code_Aster HEXA27, Elmer 827, FEniCSx Q2; CalculiX refuses them (it has
  none), and FEniCSx the 20-node ones (no serendipity cells).
"""

import pytest
from conftest_geometry import BAR, bar_project, write_bar_step
from solvers import over

from puzzle.errors import PuzzleError
from puzzle.materials import library_material
from puzzle.model.schema import ElementChoice, Feature, Physics, Quantity, SolveStep, Study
from puzzle.solve import run_study

L, B, H = BAR
FORCE = 100.0
STEEL = library_material("structural_steel").properties
E, NU = STEEL["youngs_modulus"].to_si(), STEEL["poissons_ratio"].to_si()
EXACT = FORCE * L**3 / (3 * E * B * H**3 / 12) + FORCE * L / ((5 / 6) * E / (2 * (1 + NU)) * B * H)
q = lambda v, u: Quantity(value=v, unit=u)  # noqa: E731


def _tip(tmp_path, solver, nodes, formulation=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    step = write_bar_step(tmp_path / "bar.step")
    physics = Physics(type="solid_mechanics", settings={"temperature_dependent_materials": False},
                      features=[Feature(type="fixed", selection="Bar/Root"),
                                Feature(type="force", selection="Bar/Tip",
                                        values={"y": q(-FORCE, "N")})])  # fmt: skip
    project, comp = bar_project(step, [physics], 5.0, element_shape="hexahedra",
                                element_order=2, hexahedron_nodes=nodes)  # fmt: skip
    if formulation:
        comp.elements = [ElementChoice(selection="Bar/Body", formulation=formulation)]
    study = project.add(Study(name="S", component=comp.id, solver=solver,
                              steps=[SolveStep(type="stationary")]))  # fmt: skip
    run = run_study(project, study.id, tmp_path, tmp_path / "w", timeout_s=900)
    kinds = {b.element for b in run.mesh.blocks if b.dim == 3}
    assert kinds == {f"hex{nodes}"}, kinds
    tip = run.mesh.groups["Bar/Tip"].node_ids
    return -run.results.last("displacement").at(tip)[:, 1].mean()


@over("calculix", "code_aster", "elmer")
def test_twenty_node_bricks(tmp_path, solver):
    got = _tip(tmp_path, solver, 20)
    print(f"{solver} hex20: {got / EXACT:.4f} of Timoshenko's")
    assert got == pytest.approx(EXACT, rel=0.01)


@over("code_aster", "elmer", "fenicsx")
def test_twenty_seven_node_bricks(tmp_path, solver):
    got = _tip(tmp_path, solver, 27)
    print(f"{solver} hex27: {got / EXACT:.4f} of Timoshenko's")
    assert got == pytest.approx(EXACT, rel=0.01)


@over("calculix", "code_aster")
def test_reduced_quadratic_bricks(tmp_path, solver):
    got = _tip(tmp_path, solver, 20, "reduced")
    files = list((tmp_path / "w").rglob("*.inp")) + list((tmp_path / "w").rglob("*.comm"))
    text = "".join(f.read_text(encoding="utf-8") for f in files)
    print(f"{solver} reduced hex20: {got / EXACT:.4f} of Timoshenko's")
    assert ("TYPE=C3D20R" in text) if solver == "calculix" else ("MODELISATION='3D_SI'" in text)
    assert got == pytest.approx(EXACT, rel=0.01)


@over("calculix")
def test_calculix_refuses_lagrange_bricks(tmp_path, solver):
    with pytest.raises(PuzzleError, match="hex27"):
        _tip(tmp_path, solver, 27)


@over("fenicsx")
def test_fenicsx_refuses_serendipity_bricks(tmp_path, solver):
    with pytest.raises(PuzzleError, match="hexahedron nodes to 27"):
        _tip(tmp_path, solver, 20)
