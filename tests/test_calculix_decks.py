"""CalculiX decks written as asked (ADR 0090 package 6), checked on the deck itself."""

import numpy as np
import pytest
from conftest_geometry import bar_project, write_bar_step

from puzzle.adapters.calculix.inp import write_inp
from puzzle.adapters.common import frequency_list
from puzzle.mesh import generate_mesh
from puzzle.model import Feature, Physics, Quantity, SolveStep, Study


def _bar(tmp_path, features, steps):
    step = write_bar_step(tmp_path / "bar.step")
    project, comp = bar_project(step, [Physics(type="solid_mechanics", features=features)])
    study = project.add(Study(name="S", component=comp.id, solver="calculix", steps=steps))
    mesh = generate_mesh(project, comp.id, tmp_path, tmp_path / "mesh").mesh
    return project, study, mesh


def _steady_states(deck: str) -> list[tuple[float, float]]:
    lines = deck.splitlines()
    rows = [lines[i + 1].split(",") for i, ln in enumerate(lines) if ln == "*STEADY STATE DYNAMICS"]
    return [(float(r[0]), float(r[1])) for r in rows]


@pytest.mark.parametrize("spacing", ["linear", "log"])
def test_a_harmonic_range_solves_its_own_frequencies(tmp_path, spacing):
    """CCX-4: 10 to 1000 Hz in 7 points is seven steady-state steps at exactly
    `frequency_list`'s frequencies (CalculiX's own were `points` per interval between
    eigenfrequencies, log spacing ignored)."""
    settings = {"start_frequency": 10.0, "end_frequency": 1000.0, "points": 7,
                "spacing": spacing, "modes": 6}  # fmt: skip
    steps = [SolveStep(type="frequency_domain", settings=settings)]
    force = Feature(type="force", selection="Bar/Tip", values={"y": Quantity(value=-1, unit="N")})
    project, study, mesh = _bar(
        tmp_path, [Feature(type="fixed", selection="Bar/Root"), force], steps
    )
    got = _steady_states(write_inp(project, study, mesh))
    want = frequency_list(steps[0])
    assert len(got) == 7
    assert [lo for lo, _ in got] == pytest.approx(want, rel=1e-9)  # written to ten digits
    assert [hi for _, hi in got] == pytest.approx(want, rel=1e-9)  # written to ten digits
    if spacing == "log":
        assert np.diff(np.log(want)) == pytest.approx(np.full(6, np.log(100) / 6))


@pytest.mark.parametrize(
    ("extra", "match"),
    [("Bar/tip", "both written as CalculiX set N_BAR_TIP"),
     ("ALL", "keeps for itself"), ("Bar/" + "x" * 80, "CalculiX takes 80")],
)  # fmt: skip
def test_selections_sharing_a_set_name_are_refused(tmp_path, extra, match):
    """CCX-7: "Bar/Tip" and "Bar/tip" were one CalculiX set (a repeated *NSET appends),
    so a hold on one held both; a part named "All" became N_ALL."""
    from dataclasses import replace

    from puzzle.adapters.calculix.inp import InputError

    fixed = Feature(type="fixed", selection="Bar/Root")
    project, study, mesh = _bar(tmp_path, [fixed], [SolveStep(type="stationary")])
    write_inp(project, study, mesh)  # as made: distinct
    mesh.groups[extra] = replace(mesh.groups["Bar/Tip"], name=extra)
    with pytest.raises(InputError, match=match) as err:
        write_inp(project, study, mesh)
    assert err.value.code == "invalid"


@pytest.mark.parametrize("solver", ["calculix", "code_aster"])
def test_a_displacement_with_no_component_is_refused(tmp_path, solver):
    """A prescribed displacement that names no component holds nothing: CalculiX wrote
    nothing for it and Code_Aster `_F(GROUP_MA='G1', )`, a syntax error in the solve."""
    from puzzle.adapters import available_adapters
    from puzzle.adapters.common import InputError

    held = Feature(type="displacement", selection="Bar/Root", values={})
    fixed = Feature(type="fixed", selection="Bar/Root")
    project, study, mesh = _bar(tmp_path, [fixed, held], [SolveStep(type="stationary")])
    study.solver = solver
    work = tmp_path / "w"
    work.mkdir()
    with pytest.raises(InputError, match="gives no component") as err:
        available_adapters()[solver]().write_input(project, study, mesh, work)
    assert err.value.code == "invalid"
