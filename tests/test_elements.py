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


def test_calculix_rows_marked_written_are_what_its_writer_names():
    from puzzle.adapters.calculix import inp, spring_beams

    source = inspect.getsource(inp) + inspect.getsource(spring_beams)
    written = {t for t, _ in inp.ELEMENTS.values()} | {
        t for kinds in inp.PLANAR_ELEMENTS.values() for t in kinds.values()}  # fmt: skip
    for e in elements.table("calculix"):
        names = [n.strip() for n in e.native.split("/")]
        if e.written:
            assert all(n in written or n in source for n in names), e.native
    assert "S3" not in {n for e in elements.table("calculix") if e.written
                        for n in e.native.split("/")}  # fmt: skip


def test_code_aster_rows_marked_written_are_what_its_command_file_names():
    from puzzle.adapters.code_aster import comm

    source = inspect.getsource(comm)
    for e in elements.table("code_aster"):
        first = e.native.split(" ")[0].split("/")[0]
        if e.written:
            assert f"'{first}'" in source or f'"{first}"' in source, e.native
        else:
            assert f"MODELISATION='{first}'" not in source, e.native


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
