"""Every adapter writes what it is given or refuses it with a code, never drops it (ADR
0090 pattern 2, work package 5), and agrees with its own `Capabilities`.

The matrix and its differential method are in `written_or_refused.py`: each cell adds one
thing to a minimal case and writes both, through `run_study`, stopped before the solver
starts. No solver runs; the places are meshed once per session. One test id per cell;
a failing cell names the adapter, physics, step kind, what was added and where.
"""

from __future__ import annotations

from collections import defaultdict

import pytest
import written_or_refused as wor

CELLS = wor.all_cells()
_OUTCOMES: dict = {}


@pytest.fixture(scope="session")
def world(tmp_path_factory):
    return wor.World(tmp_path_factory.mktemp("written-or-refused"))


def outcome(world, c: wor.Cell) -> wor.Outcome:
    if c not in _OUTCOMES:
        _OUTCOMES[c] = wor.run_cell(world, c)
    return _OUTCOMES[c]


def _place_needs(c: wor.Cell) -> None:
    """Frames and 2D faces are history geometry: OCP's (the pip job has none)."""
    if c.case.where in ("frame", "planar", "planar_quad"):
        pytest.importorskip("OCP")


@pytest.mark.parametrize("c", CELLS, ids=[c.ident() for c in CELLS])
def test_written_or_refused(world, c):
    _place_needs(c)
    got = outcome(world, c)
    if got.kind == "silent" and c.exemption():
        return
    assert got.kind in ("written", "refused"), f"{c.ident()}: {got}"
    if c.what == "shape":  # EDDY-1: the eddy-current tables take tetrahedra/triangles
        assert got.kind == "refused", f"{c.ident()}: {got}"
    if c.what == "base":
        claimed = c.case.step in wor.capabilities(c.case.solver).physics.get(c.case.physics, ())
        if not claimed:
            assert got.kind == "refused", f"{c.ident()}: not claimed, yet {got}"
        elif c.case.place in ("", "tet10", "tet4"):  # a claim the minimal case can't use
            assert got.kind == "written", f"{c.ident()}: claimed, yet {got}"


def _by(what: str, physics: bool = True) -> dict:
    """Cells of one slice by (solver, item, physics): every step kind and place they
    were tried; across physics with `physics` off (a setting is the solver's)."""
    out = defaultdict(list)
    for c in CELLS:
        if c.what == what:
            out[(c.case.solver, c.item, c.case.physics if physics else "any")].append(c)
    return out


def _agrees(world, groups, claimed) -> list[str]:
    """A claim with no writer, or a writer with no claim: (solver, item) whose cells are
    all refused though claimed, or written though not claimed."""
    wrong = []
    for (solver, item, physics), cells in sorted(groups.items()):
        kinds = []
        for c in cells:
            if c.case.where in ("frame", "planar", "planar_quad"):
                continue  # their own claims (beams, dimensions) are checked elsewhere
            kinds.append(outcome(world, c).kind)
        if not kinds:
            continue
        says = claimed(solver, item, physics)
        if says and "written" not in kinds and "silent" not in kinds:
            wrong.append(f"{solver}: {item} ({physics}) is claimed and refused everywhere")
        if not says and "written" in kinds:
            wrong.append(f"{solver}: {item} ({physics}) is written but not claimed")
    return wrong


def _none(wrong: list[str]) -> None:
    assert not wrong, "\n".join(wrong)


def test_features_agree_with_capabilities(world):
    def claimed(solver, feature, physics):
        return feature in wor.capabilities(solver).features

    groups = {k: [c for c in v if wor.supported_base(c.case)] for k, v in _by("feature").items()}
    _none(_agrees(world, groups, claimed))


def test_material_models_agree_with_capabilities(world):
    def claimed(solver, model, physics):
        return model in wor.capabilities(solver).material_models

    _none(_agrees(world, _by("model"), claimed))


def test_contacts_agree_with_capabilities(world):
    def claimed(solver, contact, physics):
        caps = wor.capabilities(solver)
        return contact in caps.contacts and physics in caps.physics

    groups = {k: v for k, v in _by("contact").items() if k[2] == "solid_mechanics"}
    _none(_agrees(world, groups, claimed))


def test_fasteners_and_welds_agree_with_capabilities(world):
    def fastener(solver, item, physics):
        return f"beam_fastener:{item.split('+')[0]}" in wor.capabilities(solver).connectors

    def weld(solver, item, physics):
        return item in wor.capabilities(solver).welds

    _none(_agrees(world, _by("fastener"), fastener) + _agrees(world, _by("weld"), weld))


def test_shared_settings_agree_with_capabilities(world):
    def claimed(solver, key, physics):
        taken = wor.capabilities(solver).shared_settings
        return key in taken or any(t.startswith(f"{key}:") for t in taken)

    _none(_agrees(world, _by("setting", physics=False), claimed))


def test_couplings_agree_with_capabilities(world):
    def claimed(solver, coupling, physics):
        return coupling in wor.capabilities(solver).couplings

    _none(_agrees(world, _by("coupling"), claimed))


def test_weak_springs_agree_with_capabilities(world):
    def claimed(solver, item, physics):
        return wor.capabilities(solver).weak_springs

    _none(_agrees(world, _by("weak_springs"), claimed))


def test_the_matrix_prints_as_a_table(world):
    """The table people read (scripts/written_matrix.py): a row per adapter and thing,
    a column per step kind, written or refused."""
    cells = [c for c in CELLS if c.what == "feature" and c.case.solver == "calculix"]
    text = wor.table({c: outcome(world, c) for c in cells})
    assert "| calculix | solid_mechanics | force |" in text
    assert "written" in text and "refused" in text
