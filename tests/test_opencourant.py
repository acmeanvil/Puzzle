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


def _two_blocks(gap):
    """Two bricks along x, `gap` apart, and one face group "P/Skin" of their facing
    faces (A's +x, B's -x), for a self-contact's gap."""
    s = 0.01
    coords, conn = [], []
    for x0 in (0.0, s + gap):
        base = len(coords)
        coords += [(x0 + dx, dy, dz) for dz in (0, s) for dy in (0, s) for dx in (0, s)]
        c = [0, 1, 3, 2, 4, 5, 7, 6]
        conn.append([base + 1 + k for k in c])
    solid = CellBlock("hex8", 3, (3, 1), np.array([1, 2]), np.array(conn))
    faces = CellBlock("quad4", 2, (2, 1), np.array([3, 4]),
                      np.array([[2, 4, 8, 6], [9, 13, 15, 11]]))  # fmt: skip
    mesh = Mesh(np.arange(1, 17), np.array(coords, float), [solid, faces])
    mesh.groups["P"] = Group("P", 3, [(3, 1)], np.arange(1, 17))
    mesh.groups["P/Skin"] = Group("P/Skin", 2, [(2, 1)], np.array([2, 4, 6, 8, 9, 11, 13, 15]))
    return mesh


def test_a_self_contacts_gap_is_set_below_faces_that_start_close():
    """ADR 0085 step 5: faces of one selection that share no element and start within
    Radioss's own gap (a tenth of the smallest edge, 1 mm here) halve it; else 0 (its
    own)."""
    from puzzle.adapters.opencourant.deck import self_gap

    close = _two_blocks(1e-4)
    assert self_gap(close.groups["P/Skin"], close) == pytest.approx(5e-5)
    apart = _two_blocks(2e-3)
    assert self_gap(apart.groups["P/Skin"], apart) == 0.0


def test_self_contact_and_failure_are_refused_by_implicit_solvers():
    """A contact of a selection with itself is its own capability, and the failure model
    an explicit one: CalculiX refuses both rather than writing a surface against itself
    or dropping the failure."""
    from puzzle.adapters import available_adapters
    from puzzle.adapters.common import InputError, check_contacts
    from puzzle.model.schema import Contact, Failure, HardeningCurve, MaterialModels, Plasticity

    p, comp = _project()
    comp.contacts = [Contact(name="fold", type="frictionless", source="P/Skin", target="P/Skin")]
    assert comp.contacts[0].capability == "self_contact"
    with pytest.raises(InputError, match="self_contact"):
        check_contacts(comp, {"frictionless", "frictional"}, "calculix")
    check_contacts(comp, available_adapters()["opencourant"].capabilities.contacts, "opencourant")
    comp.contacts = []
    m = p.objects[comp.materials[0].material]
    m.models = MaterialModels(plasticity=Plasticity(curves=[HardeningCurve(points=[
        (0.0, 250e6), (0.1, 300e6)])]), failure=Failure(strain=0.2))  # fmt: skip
    comp.physics[0].features = [Feature(type="fixed", selection="P")]
    study = p.add(Study(name="S", component=comp.id, solver="calculix",
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    assert any("failure" in x.lower() for x in available_adapters()["calculix"]().check(p, study))


def test_beams_and_failure_are_written(tmp_path):
    """Two-node beams on /PROP/BEAM with the section's A, I1, I2, J and its first axis as
    each element's vector; the failure model as /FAIL/JOHNSON on its material; fixed
    beam nodes hold their rotations; the estimate counts the beams (L / √(E/ρ))."""
    from puzzle.adapters.opencourant import deck
    from puzzle.frames import constants
    from puzzle.model.schema import BeamSection, Failure, JohnsonCook, MaterialModels

    nodes = np.arange(1, 6)
    coords = np.array([(0.1 * k, 0.0, 0.0) for k in range(5)])
    line = CellBlock(
        "line2", 1, (1, 1), np.arange(1, 5), np.array([[k, k + 1] for k in range(1, 5)])
    )
    mesh = Mesh(nodes, coords, [line])
    mesh.groups["P"] = Group("P", 1, [(1, 1)], nodes)
    mesh.groups["P/Root"] = Group("P/Root", 0, [], np.array([1]))
    p, comp = _project()
    m = p.objects[comp.materials[0].material]
    m.models = MaterialModels(johnson_cook=JohnsonCook(a=250e6), failure=Failure(strain=0.3))
    section = BeamSection(selection="P", shape="rectangle", width=q(10, "mm"),
                          height=q(20, "mm"), orientation=(0.0, 0.0, 1.0))  # fmt: skip
    comp.beam_sections = [section]
    comp.physics[0].features = [Feature(type="fixed", selection="P/Root")]
    study = p.add(
        Study(
            name="B",
            component=comp.id,
            solver="opencourant",
            steps=[SolveStep(type="explicit", settings={"end_time": 1e-3})],
        )
    )
    deck.write(p, study, mesh, tmp_path, study.steps[0])
    starter = (tmp_path / "puzzle_0000.rad").read_text(encoding="utf-8")
    prop = starter.split("/PROP/BEAM/1\n")[1].splitlines()
    a, i1, i2, j = constants(section)
    assert [float(x) for x in prop[3].split()] == pytest.approx([a, i1, i2, j], rel=1e-9)
    rows = starter.split("\n/BEAM/1\n")[1].split("\n/")[0].splitlines()
    assert len(rows) == 4 and rows[0][:40] == deck._i(1, 1, 2, 0)
    assert [float(x) for x in rows[0][40:].split()] == [0.0, 0.0, 1.0]
    failure = starter.split("/FAIL/JOHNSON/1\n")[1].splitlines()
    assert float(failure[0][:20]) == 0.3 and failure[1][20:40] == deck._i(1, 1)
    bcs = starter.split("/BCS/1\n")[1].splitlines()[1]
    assert bcs[:10] == "   111 111"
    est = explicit.estimate(p, comp, mesh, 1e-3)
    assert est.time_step == pytest.approx(0.1 / math.sqrt(200e9 / 7850.0))
    assert est.mass == pytest.approx(7850.0 * a * 0.4)


def test_names_never_start_a_line_radioss_would_skip_or_read_as_a_keyword(tmp_path):
    """ADR 0090 OC-1: Radioss skips a line starting with `#` or `$` and reads one starting
    with `/` as a keyword, so a title from such a name shifted the card under it (an
    amplitude `#1 ramp` lost its first point; a material `#1 Steel` read E as its
    density). Titles from names now start with something else, the name kept after it."""
    from puzzle.adapters.opencourant import deck
    from puzzle.model.schema import Amplitude

    p, comp = _project()
    mat = next(o for o in p.objects.values() if isinstance(o, Material))
    mat.name = "#1 Steel"
    mesh = _cube_mesh(2, 0.01)
    face = np.array([n for n, x in zip(mesh.node_ids, mesh.coords, strict=True) if x[0] == 0])
    mesh.groups["/P/Start"] = Group("/P/Start", 2, [], face)
    amp = p.add(Amplitude(name="  #1 ramp", points=[(0.0, 0.0), (1e-3, 1.0)]))
    comp.physics[0].features = [
        Feature(type="fixed", selection="/P/Start"),
        Feature(type="velocity", selection="P", values={"x": q(1, "m/s")}, amplitude=amp.id),
    ]
    study = p.add(
        Study(
            name="$tudy",
            component=comp.id,
            solver="opencourant",
            steps=[SolveStep(type="explicit", settings={"end_time": q(1, "ms")})],
        )
    )
    deck.write(p, study, mesh, tmp_path, study.steps[0])
    lines = (tmp_path / "puzzle_0000.rad").read_text(encoding="utf-8").splitlines()
    for key, title in zip(lines, lines[1:], strict=False):  # a keyword, then its title
        if key.startswith("/") and title[:1] != " ":  # (not /NODE's or a block's data)
            assert title[:1] != "#" and title[:1] != "$", (key, title)
            assert not title.startswith("/") or title.split("/")[1].isupper(), (key, title)
    assert "_#1 Steel" in lines and "_#1 ramp" in lines and "_$tudy" in lines
    funct = lines.index("_#1 ramp") + 1  # the ramp keeps its first point
    assert [float(x) for x in lines[funct].split()] == [0.0, 0.0]


def test_a_displacement_that_jumps_at_the_start_is_refused(tmp_path):
    """ADR 0090 OC-2: in an explicit step a nonzero displacement with no amplitude, or one
    starting above zero, moves its nodes at once: an impact the user rarely meant (a
    block rang at 2.2 times its static stress). It is refused; a ramp from zero is
    written, and a held (zero) component needs none."""
    from puzzle.adapters.common import InputError
    from puzzle.adapters.opencourant import deck
    from puzzle.model.schema import Amplitude

    p, comp = _project()
    mesh = _cube_mesh(2, 0.01)
    study = p.add(
        Study(
            name="Pull",
            component=comp.id,
            solver="opencourant",
            steps=[SolveStep(type="explicit", settings={"end_time": q(1, "ms")})],
        )
    )
    pull = Feature(type="displacement", selection="P", values={"x": q(0.01, "mm")})
    comp.physics[0].features = [pull]
    with pytest.raises(InputError, match="no amplitude") as info:
        deck.write(p, study, mesh, tmp_path, study.steps[0])
    assert info.value.code == "invalid_input"
    pull.amplitude = p.add(Amplitude(name="step", points=[(0.0, 1.0), (1e-3, 1.0)])).id
    with pytest.raises(InputError, match="starting at 1"):
        deck.write(p, study, mesh, tmp_path, study.steps[0])
    pull.amplitude = p.add(Amplitude(name="ramp", points=[(0.0, 0.0), (1e-3, 1.0)])).id
    deck.write(p, study, mesh, tmp_path, study.steps[0])
    assert "/IMPDISP/1" in (tmp_path / "puzzle_0000.rad").read_text(encoding="utf-8")
    comp.physics[0].features = [Feature(type="displacement", selection="P",
                                        values={"x": q(0, "m"), "y": q(0, "m")})]  # fmt: skip
    deck.write(p, study, mesh, tmp_path, study.steps[0])  # held only: nothing moves


def test_a_force_along_an_edge_is_shared_by_tributary_length(tmp_path):
    """ADR 0090 OC-6: a force on an edge selection was shared equally by its nodes, the
    ends taking an interior node's share and uneven spacing ignored. A graded edge
    (spacings 1, 2, 3, 4 mm) now gives each node half of each element it ends: ends
    0.5 and 2 mm of 10, the interior nodes 1.5, 2.5 and 3.5."""
    from puzzle.adapters.opencourant import deck

    p, comp = _project()
    mesh = _cube_mesh(1, 0.01)
    xs = np.array([0.0, 1.0, 3.0, 6.0, 10.0]) * 1e-3
    first = len(mesh.node_ids) + 1
    edge_nodes = np.arange(first, first + len(xs))
    mesh.node_ids = np.concatenate([mesh.node_ids, edge_nodes])
    mesh.coords = np.vstack([mesh.coords, np.column_stack([xs, np.zeros((len(xs), 2))])])
    line = np.column_stack([edge_nodes[:-1], edge_nodes[1:]])
    mesh.blocks.append(CellBlock("line2", 1, (1, 7), np.arange(100, 100 + len(line)), line))
    mesh.groups["P/Edge"] = Group("P/Edge", 1, [(1, 7)], edge_nodes)
    shares = deck._edge_shares(mesh.groups["P/Edge"], mesh)
    assert [shares[int(n)] for n in edge_nodes] == pytest.approx([0.05, 0.15, 0.25, 0.35, 0.2])
    comp.physics[0].features = [Feature(type="fixed", selection="P"),
                                Feature(type="force", selection="P/Edge",
                                        values={"z": q(100, "N")})]  # fmt: skip
    study = p.add(
        Study(
            name="Edge",
            component=comp.id,
            solver="opencourant",
            steps=[SolveStep(type="explicit", settings={"end_time": q(1, "ms")})],
        )
    )
    deck.write(p, study, mesh, tmp_path, study.steps[0])
    lines = (tmp_path / "puzzle_0000.rad").read_text(encoding="utf-8").splitlines()
    loads = [float(lines[i + 2][-20:]) for i, x in enumerate(lines) if x.startswith("/CLOAD/")]
    assert sorted(loads) == pytest.approx([5.0, 15.0, 20.0, 25.0, 35.0])


def test_without_mpiexec_the_starter_splits_nothing(monkeypatch, tmp_path):
    """ADR 0090 OC-4: the native driver ran the Starter with -np N before looking for
    mpiexec, then the one-process Engine on a model split for N. It looks first now."""
    from puzzle.adapters.opencourant import native

    monkeypatch.setattr(native.shutil, "which", lambda *a, **k: None)
    starter, engine, note = native.commands(tmp_path, "linux64", "puzzle", 4, "", {})
    assert starter[-2:] == ["-np", "1"] and len(engine) == 3
    assert engine[0].endswith("engine_linux64_gf")
    assert "one process" in note
    monkeypatch.setattr(native.shutil, "which", lambda *a, **k: "/usr/bin/mpiexec")
    starter, engine, note = native.commands(tmp_path, "linux64", "puzzle", 4, "", {})
    assert starter[-2:] == ["-np", "4"] and engine[:3] == ["/usr/bin/mpiexec", "-np", "4"]
    assert engine[3].endswith("_ompi") and note == ""


def test_frames_past_999_are_read_in_order(tmp_path):
    """ADR 0090 OC-5: frames past 999 take a fourth digit, which `A[0-9][0-9][0-9]`
    dropped (and A1000 sorts before A999 as text)."""
    from puzzle.adapters.opencourant.output import frame_files

    for k in (1, 2, 999, 1000, 1001):
        (tmp_path / f"puzzleA{k:03d}").write_text("", encoding="utf-8")
        (tmp_path / f"puzzleA{k:03d}.vtk").write_text("", encoding="utf-8")
    (tmp_path / "puzzleT01").write_text("", encoding="utf-8")
    assert [f.name for f in frame_files(tmp_path, "puzzle")] == [
        "puzzleA001", "puzzleA002", "puzzleA999", "puzzleA1000", "puzzleA1001"]  # fmt: skip
    assert [f.name for f in frame_files(tmp_path, "puzzle", ".vtk")][-1] == "puzzleA1001.vtk"


def test_a_last_frame_short_of_the_end_is_said():
    """ADR 0090 OC-5: the run is carried two estimated steps past its end so the frame
    there is written; a solver step past that leaves it out, which is now said."""
    step = SolveStep(type="explicit", settings={"end_time": q(1, "ms"), "frames": 10})
    whole = _history(kinetic_energy=[1.0] * 11, internal_energy=[0.0] * 11,
                     external_work=[0.0] * 11)  # fmt: skip
    for k, s in enumerate(whole.steps):
        s.value = k * 1e-4
    assert explicit.run_checks(whole, step) == []
    whole.steps.pop()  # the frame at 1 ms missing
    ((code, text),) = explicit.run_checks(whole, step)
    assert code == "last_frame" and "short of the end time" in text
