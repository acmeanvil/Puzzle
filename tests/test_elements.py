"""The element library (ADR 0082 step 1): rows in the fixed vocabularies, the ones
marked written truly written by their adapter, and docs/elements.md current."""

import inspect
from pathlib import Path

import pytest

from puzzle import elements


def test_rows_keep_to_the_vocabularies():
    for solver, rows in elements.everything().items():
        assert rows, solver
        names = [e.native for e in rows]
        assert len(names) == len(set(names)), f"{solver}: a name twice"
        for e in rows:
            assert e.family in elements.FAMILIES, (solver, e.native)
            assert e.formulation in elements.FORMULATIONS, (solver, e.native)
            assert set(e.studies) <= set(elements.STUDIES), (solver, e.native)
            assert set(e.shapes) <= set(elements.SHAPES), (solver, e.native)
            assert e.use and e.reference, (solver, e.native)


# Written through an element choice (ADR 0082 step 3): the library names them, the
# adapter writes what it is handed; each has its benchmark in test_element_choice.py.
CHOSEN = {
    "C3D8I",
    "3D_INCO_UPG",
    "POU_D_E",
    "3D_DIAG",
    "C3D20R",
    "3D_SI",
    "DST",
    "S8R",
    "B32R",
    "C3D8R",
    "Q4G",
    "Taylor-Hood",
    "p:2",
}


def test_chosen_elements_have_their_benchmark():
    here = Path(__file__).parent / "integration"
    bench = "".join((here / name).read_text(encoding="utf-8")
                    for name in ("test_element_choice.py", "test_quadratic_bricks.py",
                                 "test_thick_shells.py"))  # fmt: skip
    assert all(name in bench for name in CHOSEN)


def test_calculix_rows_marked_written_are_what_its_writer_names():
    from puzzle.adapters.calculix import inp, spring_beams

    source = inspect.getsource(inp) + inspect.getsource(spring_beams)
    written = {t for t, _ in inp.ELEMENTS.values()} | {
        t for kinds in inp.PLANAR_ELEMENTS.values() for t in kinds.values()}  # fmt: skip
    for e in elements.table("calculix"):
        names = [n.strip() for n in e.native.split("/")]
        if e.written:
            assert all(n in written or n in source or n in CHOSEN for n in names), e.native
    assert "S3" not in {n for e in elements.table("calculix") if e.written
                        for n in e.native.split("/")}  # fmt: skip


def test_code_aster_rows_marked_written_are_what_its_command_file_names():
    from puzzle.adapters.code_aster import comm

    source = inspect.getsource(comm)
    for e in elements.table("code_aster"):
        first = e.native.split(" ")[0].split("/")[0]
        if e.written:
            assert f"'{first}'" in source or f'"{first}"' in source or first in CHOSEN, e.native
        else:
            assert f"MODELISATION='{first}'" not in source, e.native


# ADR 0090 ADR-65: the other solvers' stars, each row by the names its writer uses.
WRITTEN_BY = {
    "elmer": (
        "puzzle.adapters.elmer.meshfiles",
        "puzzle.adapters.elmer.sif",
        "puzzle.adapters.elmer.magnetics",
    ),
    "fenicsx": ("puzzle.adapters.fenicsx.study",),
    "pynite": ("puzzle.adapters.pynite.frame",),
    "opencourant": ("puzzle.adapters.opencourant.deck",),
}
TOKENS = {
    "elmer": {
        "504/510": ["504", "510"],
        "808/820/827": ["808", "820", "827"],
        "706/715": ["706", "715"],
        "303/306/404/408": ["303", "306", "404", "408"],
        "WhitneyAV edge elements": ["WhitneyAV"],
        "ShellSolver": ['"ShellSolver'],
        "BeamSolver3D": ['"BeamSolver'],
    },
    "fenicsx": {
        "Lagrange P2": ['("Lagrange", degree'],
        "Lagrange P1": ['("Lagrange", degree'],
        "Lagrange P1/P2 (2D)": ['("Lagrange", degree'],
        "Lagrange Q2 (27-node bricks)": ['("Lagrange", degree'],
        "N1curl (Nédélec, first kind)": ['"N1curl"'],
        "Taylor-Hood P2-P1": ["mixed_element(["],
        "Lagrange P3+": ["degree = 3"],
        "Raviart-Thomas, DG": ['"RT"'],
    },
    "pynite": {
        "Member": ["add_member("],
        "Spring support": ["def_support_spring("],
        "Spring": ["add_spring("],
        "Quad (MITC4)": ["add_quad("],
        "Plate (rectangular)": ["add_plate("],
    },
    "opencourant": {
        "BRICK, Isolid 24 (HEPH)": ['"/BRICK"', "Isolid 24"],
        "TETRA4": ['"/TETRA4"'],
        "TETRA10": ['"/TETRA10"'],
        "SHELL, Ishell 24 (QEPH)": ['"/SHELL"', "Ishell 24"],
        "SH3N": ['"/SH3N"'],
        "BEAM (type 3)": ['"/BEAM"'],
    },
}


@pytest.mark.parametrize("solver", sorted(WRITTEN_BY))
def test_other_solvers_rows_marked_written_are_what_their_writers_name(solver):
    """A row marked written names what the writer writes; one not marked, nothing it
    writes (Elmer's wedges were starred, and its mesh writer refuses them)."""
    import importlib.util

    # read, not imported: FEniCSx's study.py and PyNite's frame.py run in their own envs
    source = "".join(Path(importlib.util.find_spec(m).origin).read_text(encoding="utf-8")
                     for m in WRITTEN_BY[solver])  # fmt: skip
    for e in elements.table(solver):
        if e.native in CHOSEN:
            continue
        tokens = TOKENS[solver].get(e.native)
        assert tokens is not None, f"{solver} {e.native}: name the writer's tokens in TOKENS"
        found = all(tok in source for tok in tokens)
        assert found == e.written, (solver, e.native, e.written)


def test_the_listing_groups_and_filters():
    shells = elements.listing(family="shell")
    assert "## Shells" in shells and "## 3D solids" not in shells
    assert "DKT ★" in shells and "S6 ★" in shells and "DST" in shells
    buckling = elements.query(study="buckling", written=True)
    assert ("code_aster", "POU_D_T") in {(n, e.native) for n, e in buckling}


def test_the_docs_listing_is_current():
    docs = Path(__file__).parents[1] / "docs" / "elements.md"
    assert docs.read_text(encoding="utf-8") == elements.listing(), (
        "regenerate docs/elements.md from puzzle.elements.listing()"
    )


def test_agents_list_elements(tmp_path):
    pytest.importorskip("OCP")
    import asyncio

    from puzzle.agent.mcp import build_server
    from puzzle.model.schema import Project
    from puzzle.project import save_project

    save_project(Project(name="p"), tmp_path / "p")
    got = asyncio.run(build_server(tmp_path / "p", "t").call_tool(
        "elements_list", {"solver": "code_aster", "family": "shell"}))  # fmt: skip
    rows = got.structured_content["elements"]
    assert {r["native"] for r in rows} >= {"DKT", "DST", "COQUE_3D"}
    assert next(r for r in rows if r["native"] == "DKT")["written"] is True


def test_choices_map_through_the_library_or_are_refused():
    from puzzle.adapters.common import InputError
    from puzzle.element_choice import check, lookup

    assert lookup("calculix", "solid_3d", "incompatible_modes", "hex8") == "C3D8I"
    assert lookup("code_aster", "solid_3d", "mixed_up", "tet10") == "3D_INCO_UPG"
    assert lookup("code_aster", "beam", "euler_bernoulli", "line2") == "POU_D_E"
    assert lookup("code_aster", "thermal", "lumped", "tet10") == "3D_DIAG"
    assert lookup("calculix", "solid_3d", "full", "tet10") == "C3D10"  # the default
    with pytest.raises(InputError, match="has no incompatible modes .* tet10 .* C3D10"):
        lookup("calculix", "solid_3d", "incompatible_modes", "tet10", "P/Body")

    class Comp:
        elements = [type("C", (), {"selection": "P/Body", "formulation": "mixed_up",
                                   "solver_types": {}})()]  # fmt: skip

    assert check("calculix", Comp()) == [
        "CalculiX writes no mixed displacement-pressure (nearly incompressible) element yet "
        "(on 'P/Body')"]  # fmt: skip
    assert check("code_aster", Comp()) == []


def test_no_choice_keeps_the_run_keys():
    from uuid import uuid4

    from puzzle.model.schema import Component, PartInstance
    from puzzle.project.graph import _own

    comp = Component(name="C", mesh=uuid4(), parts=[PartInstance(name="P", geometry=uuid4())])
    assert "elements" not in _own(comp)


def test_large_strains_under_large_deflection_are_flagged():
    from uuid import uuid4

    import numpy as np

    from puzzle.materials import add_library_material
    from puzzle.model.schema import (
        Component,
        MaterialAssignment,
        PartInstance,
        Project,
        SolveStep,
        Study,
    )
    from puzzle.results.model import Field, Results, StepResult
    from puzzle.solve import _large_strain

    p = Project(name="p")
    steel = add_library_material(p, "structural_steel")
    parts = [PartInstance(name="P", geometry=uuid4())]
    assigned = [MaterialAssignment(material=steel.id, selection="P/Body")]
    comp = p.add(Component(name="C", mesh=uuid4(), parts=parts, materials=assigned))
    study = Study(name="S", component=comp.id, solver="calculix", steps=[
        SolveStep(type="stationary", settings={"geometric_nonlinearity": True})])  # fmt: skip

    def said(strain):
        res = Results(solver="calculix", steps=[StepResult(1, "stationary", 1.0, fields={
            "strain": Field("strain", "node", list("abcdef"), np.array([1]),
                            np.array([strain]))})])  # fmt: skip
        got = []
        _large_strain(res, p, study, got.append)
        return got

    assert said([0.01, 0, 0, 0, 0, 0]) == []
    (warning,) = said([0.08, 0, 0, 0, 0, 0])
    assert warning.code == "large_strain" and "8.0%" in warning.text
