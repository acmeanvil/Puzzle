"""Contact results (ADR 0056) against closed-form answers, on CalculiX and Code_Aster.

The frictionless stack of test_contact.py (a 10 mm aluminium cube pressed by 20 MPa on
a steel one, meshed apart): the interface carries p A = 2000 N, all of it in contact
and sliding (a closed frictionless contact slides). CalculiX's contact pressure is
20 MPa at every slave node; Code_Aster's, recovered from its nodal forces, is its own
Lagrange multiplier (LAGS_C) and averages 20 MPa.

The sliding block (cap pressed 1 um and dragged 10 um on the base, μ = 0.3): every
closed node slides, and the frictional force is μ times the normal one."""

import numpy as np
import pytest
from solvers import over
from test_contact import PRESSURE, SIDE, _stack

from puzzle.model import Contact, Feature, Quantity
from puzzle.post.contact import contact_results
from puzzle.solve import run_study


def _run(tmp_path, solver, contact, features=None, **kwargs):
    project, study = _stack(tmp_path, solver, contact, features, **kwargs)
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=900)
    comp = project.objects[study.component]
    return run, comp


@over("calculix", "code_aster")
def test_contact_pressure_through_a_stack(tmp_path, solver):
    contact = Contact(name="Interface", type="frictionless", source="Cap/Bottom",
                      target="Base/Top")  # fmt: skip
    run, comp = _run(tmp_path, solver, contact)
    step = run.results.steps[-1]
    p = step.fields["contact_pressure"]
    assert set(p.ids) == set(run.mesh.groups["Cap/Bottom"].node_ids)
    [r] = contact_results(step, comp, run.mesh)
    print(f"{solver}: force {r.normal_force:.6g} N, pressure {p.values.min():.4g}.."
          f"{p.values.max():.4g} Pa, area share {r.area_share:.4f}")  # fmt: skip
    assert r.normal_force == pytest.approx(PRESSURE * SIDE**2, rel=1e-3)
    assert r.area_share == pytest.approx(1.0) and r.tangential_force == pytest.approx(0.0)
    assert set(step.fields["contact_status"].values[:, 0]) == {1.0}  # sliding
    if solver == "calculix":
        assert np.abs(p.values[:, 0] / PRESSURE - 1).max() < 1e-3
    else:  # its multiplier: the pressure itself, compression negative
        from puzzle.adapters.code_aster.tables import read_table

        rows = [x for x in read_table(next((tmp_path / "run").glob("**/displacement.csv")))
                if x.get("LAGS_C", "-") not in ("-", "") and float(x["INST"]) == 1.0]  # fmt: skip
        lags = {int(x["NOEUD"]): -float(x["LAGS_C"]) for x in rows}
        ours = dict(zip(p.ids.tolist(), p.values[:, 0], strict=True))
        assert all(ours[n] == pytest.approx(v, rel=1e-6) for n, v in lags.items())
        assert np.mean(p.values) == pytest.approx(PRESSURE, rel=0.01)


@over("calculix", "code_aster")
def test_a_sliding_block_slides_everywhere(tmp_path, solver):
    mu = 0.3
    zero = Quantity(value=0, unit="m")
    features = [
        Feature(type="fixed", selection="Base/Bottom"),
        Feature(type="displacement", selection="Base/SymY", values={"y": zero}),
        Feature(type="displacement", selection="Cap/Top", values={
            "x": Quantity(value=10, unit="um"), "y": zero, "z": Quantity(value=-1, unit="um")}),
    ]  # fmt: skip
    contact = Contact(name="Interface", type="frictional", friction=mu, source="Cap/Bottom",
                      target="Base/Top")  # fmt: skip
    order, overhang = (1, 0.002) if solver == "code_aster" else (2, 0.0)
    run, comp = _run(tmp_path, solver, contact, features, overhang=overhang, order=order)
    step = run.results.steps[-1]
    [r] = contact_results(step, comp, run.mesh)
    status = step.fields["contact_status"].values[:, 0]
    print(f"{solver}: normal {r.normal_force:.4g} N, frictional {r.tangential_force:.4g} N, "
          f"sticking {r.sticking_share:.3f}, sliding up to {r.max_sliding:.3g} m")  # fmt: skip
    assert r.normal_force > 50
    assert r.tangential_force / r.normal_force == pytest.approx(mu, rel=0.02)
    assert r.sticking_share < 0.02 and (status[status > 0] == 1).mean() > 0.95
    assert r.max_sliding == pytest.approx(10e-6, rel=0.2)


@over("calculix")
def test_the_contact_tools_results_tab(tmp_path, solver):
    """The Results tab lists the stack's contact with its force, and shows the pressure
    on the source face (20 MPa)."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.window import MainWindow
    from puzzle.project import save_project
    from puzzle.project.layout import RESULTS_DIR
    from puzzle.results.store import ResultStore

    app = QApplication.instance() or QApplication([])  # noqa: F841
    contact = Contact(name="Interface", type="frictionless", source="Cap/Bottom",
                      target="Base/Top")  # fmt: skip
    project, study = _stack(tmp_path, solver, contact)
    save_project(project, tmp_path)
    run_study(project, study.id, tmp_path, tmp_path / "run",
              store=ResultStore(tmp_path / RESULTS_DIR), timeout_s=900)  # fmt: skip
    window = MainWindow(tmp_path)
    window.open_contacts("results")
    tab = window.contacts.results
    assert tab.run.count() == 1 and tab.table.rowCount() == 1
    assert tab.table.item(0, 0).text() == "Interface"
    assert tab.table.item(0, 1).text() == "2000 N" and tab.table.item(0, 3).text() == "100%"
    tab.show_field("contact_pressure")
    shown = window.viewport.shown
    assert shown["scalar"] == "Contact pressure"
    assert shown["min"] == pytest.approx(PRESSURE, rel=1e-3)
    assert shown["max"] == pytest.approx(PRESSURE, rel=1e-3)
    # The agent reads the same numbers.
    import asyncio

    from puzzle.agent.mcp import build_server

    out = asyncio.run(build_server(tmp_path, "test").call_tool("contact_results",
                                                               {"study": "Press"}))  # fmt: skip
    data = out.structured_content.get("result", out.structured_content)
    [c] = data["contacts"]
    assert c["normal_force_N"] == pytest.approx(PRESSURE * SIDE**2, rel=1e-3)
    assert c["area_share"] == pytest.approx(1.0)
