"""OpenCourant's adapter without the solver (ADR 0085): the stable time step and cost,
the deck's cards, the Engine's progress lines and the checks. The solved benchmarks are
in tests/integration/test_explicit.py."""

import math

import numpy as np
import pytest

from puzzle import explicit
from puzzle.mesh.mesh import CellBlock, Group, Mesh
from puzzle.model import (
    Component,
    Feature,
    Geometry,
    MaterialAssignment,
    MeshRecipe,
    PartInstance,
    Physics,
    Project,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.model.schema import Material

q = lambda v, u: Quantity(value=v, unit=u)  # noqa: E731


def _cube_mesh(n=2, size=0.01):
    """n x n x n bricks of `size`, one body group "P"."""
    ids, coords = {}, []
    for i in range(n + 1):
        for j in range(n + 1):
            for k in range(n + 1):
                ids[i, j, k] = len(coords) + 1
                coords.append((i * size, j * size, k * size))
    conn = []
    for i in range(n):
        for j in range(n):
            for k in range(n):
                c = [ids[i, j, k], ids[i + 1, j, k], ids[i + 1, j + 1, k], ids[i, j + 1, k],
                     ids[i, j, k + 1], ids[i + 1, j, k + 1], ids[i + 1, j + 1, k + 1],
                     ids[i, j + 1, k + 1]]  # fmt: skip
                conn.append(c)
    block = CellBlock("hex8", 3, (3, 1), np.arange(1, len(conn) + 1), np.array(conn))
    nodes = np.arange(1, len(coords) + 1)
    mesh = Mesh(nodes, np.array(coords, float), [block])
    mesh.groups["P"] = Group("P", 3, [(3, 1)], nodes)
    return mesh


def _project(e=200e9, nu=0.3, rho=7850.0):
    p = Project(name="x")
    m = p.add(Material(name="Steel", properties={
        "youngs_modulus": q(e, "Pa"), "poissons_ratio": q(nu, "dimensionless"),
        "density": q(rho, "kg/m^3")}))  # fmt: skip
    geo = p.add(Geometry(name="G", source="cube.step"))
    recipe = p.add(MeshRecipe(name="M"))
    comp = p.add(Component(name="C", parts=[PartInstance(name="P", geometry=geo.id)],
                           mesh=recipe.id,
                           materials=[MaterialAssignment(material=m.id, selection="P")],
                           physics=[Physics(type="solid_mechanics")]))  # fmt: skip
    return p, comp


def test_the_stable_time_step_is_the_smallest_crossing_time():
    p, comp = _project()
    mesh = _cube_mesh(2, 0.01)
    c = explicit.wave_speed(200e9, 0.3, 7850.0)
    assert c == pytest.approx(math.sqrt(200e9 * 0.7 / (7850 * 1.3 * 0.4)))
    est = explicit.estimate(p, comp, mesh, end_time=1e-3, scale=0.9)
    assert est.time_step == pytest.approx(0.01 / c, rel=1e-9)  # a cube: V / A_max = its edge
    assert est.cycles == pytest.approx(1e-3 / (0.9 * 0.01 / c))
    assert est.mass == pytest.approx(7850 * 0.02**3)
    assert est.suggestion is None and explicit.warnings(est) == []


def test_a_long_run_is_warned_with_the_mass_scaling_that_would_shorten_it():
    p, comp = _project()
    est = explicit.estimate(p, comp, _cube_mesh(2, 0.01), end_time=100.0)
    assert est.cycles > explicit.CYCLES_WARN
    target = 100.0 / (0.9 * explicit.CYCLES_AIM)
    assert est.suggestion == pytest.approx(target)
    added = est.mass * ((target / est.time_step) ** 2 - 1)  # every element is under it
    assert est.added_mass == pytest.approx(added)
    (said,) = explicit.warnings(est)
    assert "mass scaling" in said and "cycles" in said


def test_the_engine_lines_become_progress():
    from puzzle.adapters.opencourant.progress import OpenCourantProgress

    parser = OpenCourantProgress(end_time=2e-5)
    events = []
    for line in [
        " ROOT: puzzle  RESTART: 0001",
        " NC=       0 T= 0.0000E+00 DT= 8.4813E-07 ERR=  0.0% DM/M= 0.0000E+00",
        " NC=     100 T= 1.0000E-05 DT= 8.4813E-07 ERR=  0.3% DM/M= 0.0000E+00",
    ]:
        events += parser.feed(line)
    increments = [e for e in events if e.kind == "increment"]
    assert [e.time for e in increments] == [0.0, 1e-5] and increments[-1].end_time == 2e-5
    assert parser.error == pytest.approx(0.3)
    assert parser.finish(True)[0].kind == "converged"
    events = []
    for line in ["ERROR ID :    678", "** ERROR IN BOUNDARY CONDITION DEFINITION",
                 "   UNDEFINED NODE GROUP ID=0", "", "     ERROR TERMINATION"]:  # fmt: skip
        events += parser.feed(line)
    kinds = [e.kind for e in events]
    assert "message" in kinds and kinds[-1] == "diverged"
    message = next(e for e in events if e.kind == "message")
    assert message.code == "678" and "UNDEFINED NODE GROUP" in message.text


def test_the_deck_has_the_model_and_the_run(tmp_path):
    from puzzle.adapters.opencourant import deck

    p, comp = _project()
    mesh = _cube_mesh(2, 0.01)
    face = np.array([n for n, x in zip(mesh.node_ids, mesh.coords, strict=True) if x[0] == 0])
    mesh.groups["P/Start"] = Group("P/Start", 2, [], face)
    comp.physics[0].features = [
        Feature(type="fixed", selection="P/Start"),
        Feature(type="initial_velocity", selection="P", values={"vx": q(-5, "m/s")}),
        Feature(type="rigid_wall", selection="P", values={"x0": q(-1, "mm"),
                                                          "nx": q(1, "dimensionless")}),
        Feature(type="gravity", selection="P", values={"gz": q(-9.81, "m/s^2")}),
    ]  # fmt: skip
    study = p.add(Study(name="Drop", component=comp.id, solver="opencourant",
                        steps=[SolveStep(type="explicit", settings={
                            "end_time": q(2, "ms"), "frames": 10})]))  # fmt: skip
    run = deck.write(p, study, mesh, tmp_path, study.steps[0])
    starter = (tmp_path / "puzzle_0000.rad").read_text(encoding="utf-8")
    engine = (tmp_path / "puzzle_0001.rad").read_text(encoding="utf-8")
    assert starter.startswith("#RADIOSS STARTER\n/BEGIN") and starter.endswith("/END\n")
    for card in ("/MAT/ELAST/1", "/PROP/SOLID/1", "/PART/1", "/BRICK/1", "/BCS/1",
                 "/INIVEL/TRA/1", "/RWALL/PLANE/1", "/GRAV/1", "/FUNCT/1"):  # fmt: skip
        assert f"\n{card}\n" in starter, card
    assert starter.count("\n/BRICK/1\n") == 1 and len(starter.split("/BRICK/1\n")[1].split(
        "\n/")[0].splitlines()) == 8  # fmt: skip
    end = float(engine.splitlines()[2])
    assert 2e-3 < end < 2e-3 * 1.01  # a little past: the last frame is written
    assert "/ANIM/DT" in engine and f"{2e-4:>20.12g}" in engine
    assert "starter -i puzzle_0000.rad" in run.read_text(encoding="utf-8")
    assert run.name == "run.sh"


def test_settings_are_checked():
    from puzzle.adapters.common import InputError
    from puzzle.adapters.opencourant import deck

    with pytest.raises(InputError, match="end_time"):
        deck.settings_of(SolveStep(type="explicit", settings={}))
    with pytest.raises(InputError, match="time_step_scale"):
        deck.settings_of(
            SolveStep(type="explicit", settings={"end_time": 1.0, "time_step_scale": 1.5})
        )
    s = deck.settings_of(SolveStep(type="explicit", settings={"end_time": q(5, "ms")}))
    assert (s.end_time, s.frame_dt, s.history_dt) == pytest.approx((5e-3, 2.5e-4, 5e-6))


def test_implicit_solvers_refuse_explicit_steps_and_features():
    from puzzle.adapters import available_adapters

    p, comp = _project()
    comp.physics[0].features = [Feature(type="fixed", selection="P"),
                                Feature(type="initial_velocity", selection="P",
                                        values={"vx": q(1, "m/s")})]  # fmt: skip
    study = p.add(
        Study(
            name="S",
            component=comp.id,
            solver="calculix",
            steps=[SolveStep(type="explicit", settings={"end_time": 1e-3})],
        )
    )
    problems = available_adapters()["calculix"]().check(p, study)
    assert any("explicit" in x for x in problems)
    assert any("initial_velocity" in x for x in problems)
    study.solver = "opencourant"
    assert available_adapters()["opencourant"]().check(p, study) == []


def _history(**series):
    from puzzle.results.model import Field, Results, StepResult

    n = len(next(iter(series.values())))
    steps = []
    for k in range(n):
        fields = {name: Field(name, "global", [""], np.array([0]), np.array([[values[k]]]))
                  for name, values in series.items()}  # fmt: skip
        steps.append(StepResult(1, "explicit", float(k), fields=fields))
    return Results("opencourant", steps)


def test_the_energy_balance_hourglass_and_added_mass_are_checked():
    # a bar thrown at 10 m/s into a spring: kinetic into internal, the sum held
    ok = _history(kinetic_energy=[10.0, 6.0, 2.0], internal_energy=[0.0, 4.0, 8.0],
                  external_work=[0.0, 0.0, 0.0], mass=[1.0, 1.0, 1.0])  # fmt: skip
    assert explicit.run_checks(ok) == []
    driven = _history(kinetic_energy=[0.0, 1.0, 1.0], internal_energy=[0.0, 1.0, 3.0],
                      external_work=[0.0, 2.0, 4.0])  # fmt: skip
    assert explicit.run_checks(driven) == []  # work done on it is in the balance
    made = _history(kinetic_energy=[10.0, 9.0, 9.0], internal_energy=[0.0, 4.0, 8.0])
    assert [c for c, _ in explicit.run_checks(made)] == ["energy_error"]
    hour = _history(
        kinetic_energy=[10.0, 2.0],
        internal_energy=[0.0, 7.0],
        hourglass_energy=[0.0, 1.0],
        added_mass=[0.0, 0.2],
        mass=[1.0, 1.0],
    )
    assert {c for c, _ in explicit.run_checks(hour)} == {"hourglass", "added_mass"}


def test_a_remote_host_runs_the_pinned_image_with_apptainer():
    """ADR 0085 step 3: on a cluster, Apptainer pulls the image by its digest."""
    from puzzle import hosts
    from puzzle.jobs.remote import RemoteRuntime

    box = RemoteRuntime(hosts.Host(name="hpc", address="hpc", runtime="apptainer"), "opencourant")
    line = box.solve_lines(["sh", "run.sh"])[-1]
    assert line.startswith("apptainer exec")
    assert "docker://docker.io/opencourant/opencourant@sha256:b3f042fe" in line
    assert line.endswith("sh run.sh")


def test_a_release_is_fetched_checked_and_unpacked(tmp_path, monkeypatch):
    """ADR 0085 step 3: the zip's SHA-256 must be the pinned one; nothing outside its
    folder is unpacked."""
    import hashlib
    import io
    import threading
    import zipfile
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from puzzle.adapters.opencourant import native

    def zipped(names):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for n in names:
                z.writestr(n, "x")
        return buf.getvalue()

    good = zipped(["OpenCourant/exec/starter_linux64_gf", "OpenCourant/hm_cfg_files/a.cfg"])
    evil = zipped(["OpenCourant/exec/starter_linux64_gf", "../outside.txt"])
    served = {"/good.zip": good, "/evil.zip": evil}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = served[self.path]
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(native, "home", lambda: tmp_path / "solvers")
    monkeypatch.setattr(native, "platform_key", lambda: "linux64")
    monkeypatch.delenv("PUZZLE_OPENCOURANT_PATH", raising=False)
    try:
        with pytest.raises(RuntimeError, match="SHA-256"):
            native.fetch(lambda s: None, f"{base}/good.zip", "0" * 64)
        assert native.root() is None
        with pytest.raises(RuntimeError, match="outside"):
            native.fetch(lambda s: None, f"{base}/evil.zip", hashlib.sha256(evil).hexdigest())
        assert not (tmp_path / "outside.txt").exists()
        folder = native.fetch(lambda s: None, f"{base}/good.zip", hashlib.sha256(good).hexdigest())
        assert native.root() == folder and (folder / "hm_cfg_files" / "a.cfg").exists()
        env = native.environment(folder, {})
        assert env["RAD_CFG_PATH"] == str(folder / "hm_cfg_files")
        assert str(folder / "extlib" / "hm_reader" / "linux64") in env["LD_LIBRARY_PATH"]
    finally:
        server.shutdown()


def test_the_new_study_form_says_which_solver_runs_explicit_dynamics():
    from puzzle.gui.menus import _solver_fits

    assert _solver_fits("opencourant", "explicit") is None
    said = _solver_fits("calculix", "explicit")
    assert "explicit dynamics" in said and "OpenCourant" in said
    assert "CalculiX" in _solver_fits("opencourant", "modal")
    assert _solver_fits("calculix", "stationary") is None


def test_the_advisor_asks_an_explicit_study_for_a_load_or_a_motion():
    from puzzle.advisor import advise

    p, comp = _project()
    study = p.add(
        Study(
            name="Drop",
            component=comp.id,
            solver="opencourant",
            steps=[SolveStep(type="explicit", settings={"end_time": 1e-3})],
        )
    )
    conditions = next(a for a in advise(p, component=comp, study=study) if a.step == "conditions")
    assert conditions.state == "needed" and "a load or a motion" in conditions.detail
    assert conditions.action.endswith(":initial_velocity")  # no support asked for
    comp.physics[0].features = [Feature(type="initial_velocity", selection="P",
                                        values={"vz": q(-5, "m/s")})]  # fmt: skip
    conditions = next(a for a in advise(p, component=comp, study=study) if a.step == "conditions")
    assert conditions.state == "done"


def test_the_energy_error_is_tracked_live():
    from puzzle.adapters.opencourant.progress import OpenCourantProgress

    events = OpenCourantProgress(1e-3).feed(
        " NC=      50 T= 5.0000E-04 DT= 1.0000E-05 ERR= -1.2% DM/M= 0.0000E+00"
    )
    tracked = [e for e in events if e.kind == "tracked"]
    assert [(e.name, e.value, e.unit, e.time) for e in tracked] == [
        ("energy error", -1.2, "%", 5e-4)
    ]
