"""Threads on smooth cylinders against a meshed thread (ADR 0076 step 2: the gate).

An M16×2 screw in a steel boss, 2D axisymmetric on CalculiX, 40 kN pulling the screw
out of the boss, the boss held on the face the screw leaves by (the first engaged
thread is there, as under a nut). The reference meshes the 60° teeth (root 6.773 mm,
crest 7.9 mm; boss crest 6.9175 mm, root 8.1 mm), bonded on the bearing flanks only (0.1
mm apart on the others, radial gaps at crests and roots). The smooth model has the
screw at its minor radius 6.773 mm against the boss's bore, with an `iso` thread
(Yamamoto's teeth, `threads.TEETH_FACTOR`) tying both flanks, as the glued reference.
Both have a 6.7 mm shank above the boss.

Measured: the axial force through the screw at each thread (σ_zz over its section),
each thread's share, the axial stiffness (load over the screw top's rise). The meshed
reference is converged (0.25 and 0.12 mm elements: first share 50.0 and 50.1 %, 1.167
and 1.166 kN/µm).
"""

import math

import numpy as np
import pytest
from solvers import available, over

from puzzle.materials import add_library_material
from puzzle.model import Contact, Feature, Quantity
from puzzle.model.schema import (
    Component,
    Geometry,
    HistoryStep,
    MaterialAssignment,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Physics,
    Project,
    SolveStep,
    Study,
    Thread,
)
from puzzle.solve import run_study

pytest.importorskip("OCP")

MM = 1e-3
P, D, R_BOSS, ENGAGED, TOP = 2.0, 16.0, 16.0, 16.0, 36.0  # mm
# The smooth faces' radius: the screw's minor (d3/2), where the screw's core carries
# the load as the meshed one's does (a fatter screw spreads the load).
R_PITCH = 6.773
R_SHANK = 6.7
LOAD = 40e3  # N
TAN = math.tan(math.radians(30))
# The meshed teeth (mm): the screw's root and crest, the boss's crest and root.
S_ROOT, S_CREST, B_CREST, B_ROOT = 6.773, 7.9, 6.9175, 8.1
RISE = (S_CREST - S_ROOT) * TAN
CREST, ROOT_FLAT = 0.3, P - 2 * RISE - 0.3
A = ROOT_FLAT / 2  # where a pitch's lower flank starts
GAP = 0.1  # mm between the unloaded flanks


def _screw_teeth():
    pts = [(0.0, 0.0), (S_ROOT, 0.0)]
    for k in range(int(ENGAGED / P)):
        z = k * P
        pts += [(S_ROOT, z + A), (S_CREST, z + A + RISE), (S_CREST, z + A + RISE + CREST),
                (S_ROOT, z + A + 2 * RISE + CREST)]  # fmt: skip
    pts += [(S_ROOT, ENGAGED), (R_SHANK, ENGAGED), (R_SHANK, TOP), (0.0, TOP)]
    return pts


def _upper(k, r):  # the screw's upper (bearing) flank of tooth k at radius r
    return k * P + A + RISE + CREST + (S_CREST - r) * TAN


def _lower(k, r):  # the screw's lower flank of tooth k at radius r
    return k * P + A + (r - S_ROOT) * TAN


def _boss_teeth():
    """The boss's bore, counter-clockwise: up the bore (r small) is the region's left."""
    n = int(ENGAGED / P)
    bore = [(B_ROOT, 0.0)]
    for k in range(n):
        bore += [(B_ROOT, _upper(k, B_ROOT)), (B_CREST, _upper(k, B_CREST))]
        if k + 1 < n:
            bore += [(B_CREST, _lower(k + 1, B_CREST) - GAP), (B_ROOT, _lower(k + 1, B_ROOT) - GAP)]
        else:
            bore += [(B_CREST, ENGAGED)]
    # counter-clockwise: bottom out to R, up, the top back in, then down the bore
    return [(B_ROOT, 0.0), (R_BOSS, 0.0), (R_BOSS, ENGAGED)] + bore[::-1][:-1]


def _face(name, pts):
    points = [[x * MM, y * MM] for x, y in pts]
    return HistoryStep(kind="face", name=name, args={"profile": {"points": points}})


def _edges(pts, test):
    """The side names of a profile whose edge (from point i to i+1) passes `test`."""
    out = []
    for i, (a, b) in enumerate(zip(pts, pts[1:] + pts[:1], strict=True), start=1):
        if test(a, b):
            out.append(f"side-{i}")
    return out


def _model(meshed: bool, solver: str, size=0.25, kind="bonded", shape=None):
    p = Project(name="thread")
    steel = add_library_material(p, "structural_steel")
    if meshed:
        screw_pts, boss_pts = _screw_teeth(), _boss_teeth()
    else:
        screw_pts = [(0.0, 0.0), (R_PITCH, 0.0), (R_PITCH, ENGAGED), (R_SHANK, ENGAGED),
                     (R_SHANK, TOP), (0.0, TOP)]  # fmt: skip
        boss_pts = [(R_PITCH, 0.0), (R_BOSS, 0.0), (R_BOSS, ENGAGED), (R_PITCH, ENGAGED)]
    screw = p.add(Geometry(name="Screw", history=[_face("S", screw_pts)]))
    boss = p.add(Geometry(name="Boss", history=[_face("B", boss_pts)]))
    top = _edges(screw_pts, lambda a, b: a[1] == b[1] == TOP)
    held = _edges(boss_pts, lambda a, b: a[1] == b[1] == ENGAGED and min(a[0], b[0]) < R_BOSS)
    screw.selections = [NamedSelection(name="Top", entity="edge",
                                       members=[f"S/{e}" for e in top])]  # fmt: skip
    boss.selections = [NamedSelection(name="Held", entity="edge",
                                      members=[f"B/{e}" for e in held])]  # fmt: skip
    contacts = []
    if meshed:  # the bearing flanks shared: bonded, meshed conformally
        screw.selections.append(NamedSelection(name="Region", entity="face", members=["S/face"]))
        boss.selections.append(NamedSelection(name="Region", entity="face", members=["B/face"]))
        contacts.append(Contact(name="Teeth", type="bonded", source="Screw/Region",
                                target="Boss/Region"))  # fmt: skip
    else:
        screw.selections.append(NamedSelection(name="Thread", entity="edge", members=[
            "S/" + _edges(screw_pts, lambda a, b: a[0] == b[0] == R_PITCH)[0]]))  # fmt: skip
        boss.selections.append(NamedSelection(name="Bore", entity="edge", members=[
            "B/" + _edges(boss_pts, lambda a, b: a[0] == b[0] == R_PITCH)[0]]))  # fmt: skip
        thread = Thread(form="iso", size="M16")
        contacts.append(Contact(name="Thread", type=kind, source="Screw/Thread",
                                target="Boss/Bore", thread=thread))  # fmt: skip
    # FEniCSx (dolfinx) reads no 8-node quadrilaterals: six-node triangles there.
    shape = shape or ("tetrahedra" if solver == "fenicsx" else "hexahedra")
    mesh = p.add(MeshRecipe(name="Quads", element_order=2, element_shape=shape,
                            global_size=Quantity(value=size, unit="mm")))  # fmt: skip
    comp = p.add(Component(
        name="Joint", dimension="2d_axisymmetric", mesh=mesh.id,
        parts=[PartInstance(name="Screw", geometry=screw.id),
               PartInstance(name="Boss", geometry=boss.id)],
        materials=[MaterialAssignment(material=steel.id, selection="Screw"),
                   MaterialAssignment(material=steel.id, selection="Boss")],
        contacts=contacts,
        physics=[Physics(type="solid_mechanics", features=[
            Feature(type="displacement", selection="Boss/Held",
                    values={"y": Quantity(value=0, unit="m")}),
            Feature(type="force", selection="Screw/Top",
                    values={"y": Quantity(value=LOAD, unit="N")})])],
    ))  # fmt: skip
    study = p.add(Study(name="Pull", component=comp.id, solver=solver,
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    return p, study


def _axial_force(run, z):
    """The axial force (N) through the screw at height z (m): σ_zz (yy here) over its
    section, linear in the screw's own nodes."""
    from scipy.interpolate import LinearNDInterpolator

    stress = run.results.last("stress")
    screw = set(np.asarray(run.mesh.groups["Screw"].node_ids).tolist())
    ids = np.array([i for i in stress.ids if int(i) in screw])
    at = {int(i): k for k, i in enumerate(stress.ids)}
    xyz = run.mesh.node_coords(ids)
    syy = np.array([stress.values[at[int(i)]][1] for i in ids])
    from scipy.interpolate import NearestNDInterpolator

    f, near = LinearNDInterpolator(xyz[:, :2], syy), NearestNDInterpolator(xyz[:, :2], syy)
    # The section's outer radius: where the screw's boundary crosses z (its nodes near z).
    close = xyz[np.abs(xyz[:, 1] - z) < 0.05 * P * MM]
    r_out = float(np.max(close[:, 0]))
    r = np.linspace(0, r_out, 800)
    pts = np.column_stack([r, np.full_like(r, z)])
    s = f(pts)
    s = np.where(np.isnan(s), near(pts), s)
    return float(np.trapezoid(s * 2 * np.pi * r, r))


def _shares(run):
    """Each thread's share of the load, the first (at the held face) first, from the
    force through the screw at each pitch's root flat."""
    # The last cut above the boss: the pair at its held face carries a share too.
    cuts = [(k * P + A / 2) * MM for k in range(int(ENGAGED / P))] + [(ENGAGED + 0.5) * MM]
    forces = [_axial_force(run, z) for z in cuts]
    per = np.diff(forces)  # the force each pitch passes into the boss
    return (per / LOAD)[::-1], forces


@over("calculix")
def test_a_smooth_thread_shares_the_load_as_the_meshed_one(tmp_path, solver):
    results = {}
    for meshed in (True, False):
        # Like for like: the reference's bearing flanks are glued, so the smooth thread
        # ties both flanks (no_separation: in 2D their normals hold every direction).
        p, study = _model(meshed, solver, kind="no_separation")
        run = run_study(p, study.id, tmp_path, tmp_path / ("ref" if meshed else "smooth"),
                        timeout_s=1800)  # fmt: skip
        shares, forces = _shares(run)
        u = run.results.last("displacement")
        top = [i for i, x in zip(u.ids, run.mesh.node_coords(u.ids), strict=True)
               if abs(x[1] - TOP * MM) < 1e-9]  # fmt: skip
        at = {int(i): k for k, i in enumerate(u.ids)}
        rise = float(np.mean([u.values[at[int(i)]][1] for i in top]))
        results[meshed] = (shares, LOAD / rise, forces)
        print(("meshed" if meshed else "smooth"), "shares", np.round(shares * 100, 1),
              f"stiffness {LOAD / rise / 1e9:.3f} kN/µm", "forces", np.round(forces))  # fmt: skip
    ref, smooth = results[True], results[False]
    assert ref[0].sum() == pytest.approx(1.0, abs=0.02)
    assert smooth[0].sum() == pytest.approx(1.0, abs=0.02)
    assert abs(smooth[0][0] - ref[0][0]) < 0.03  # the first thread within 3 points
    assert np.all(np.abs(smooth[0] - ref[0]) < 0.05)
    assert smooth[1] == pytest.approx(ref[1], rel=0.05)


def _smooth(tmp_path, solver, name, **thread):
    p, study = _model(False, solver)
    c = p.objects[study.component].contacts[0]
    kind = thread.pop("type", "bonded")
    form = thread.pop("form", "iso")
    if form in ("iso",):
        c.thread = Thread(form=form, size="M16", **thread)
    else:  # another form at the same diameter and pitch
        c.thread = Thread(form=form, major=Quantity(value=D, unit="mm"),
                          pitch=Quantity(value=P, unit="mm"), **thread)  # fmt: skip
    c.type = kind
    run = run_study(p, study.id, tmp_path, tmp_path / name, timeout_s=1800)
    step = run.results.steps[-1]
    return run, step.fields["thread_load"], step.fields["thread_wedge"]


@over("calculix", "fenicsx", "code_aster")
def test_forms_and_flanks_wedge_and_share_as_they_should(tmp_path, solver):
    """On the smooth model: `thread_load` sums to the load and matches the section cuts;
    unilateral (gap) flanks bear as the bonded tie under a pull; a square thread has
    no wedge (< 1 % of the 60° one), acme's and trapezoidal's lie between; buttress's
    7° load flank wedges little, and its 45° flank (bearing "other") much more."""
    run, load, wedge = _smooth(tmp_path, solver, "iso")
    shares, _ = _shares(run)
    per = load.values[0] / LOAD
    print("iso thread_load", np.round(per * 100, 1), "cuts", np.round(shares * 100, 1),
          f"wedge {wedge.values[0][0]:.0f} N")  # fmt: skip
    assert per.sum() == pytest.approx(1.0, abs=0.02)
    assert per[0] == pytest.approx(shares[0], abs=0.03)
    iso_wedge = wedge.values[0][0]
    # The 60° form wedges by about F tan 30° (the radial share of the flank force).
    assert iso_wedge == pytest.approx(LOAD * math.tan(math.radians(30)), rel=0.1)
    _, gap_load, _ = _smooth(tmp_path, solver, "gap", type="frictionless")
    assert np.allclose(gap_load.values[0] / LOAD, per, atol=0.02)
    wedges = {}
    for form, extra in (("square", {}), ("acme", {}), ("trapezoidal", {}),
                        ("buttress", {}), ("buttress", {"bearing": "other"})):  # fmt: skip
        key = form + ("/other" if extra else "")
        _, f_load, f_wedge = _smooth(tmp_path, solver, key.replace("/", "-"), form=form, **extra)
        wedges[key] = f_wedge.values[0][0]
        assert f_load.values[0].sum() / LOAD == pytest.approx(1.0, abs=0.02), key
    print({k: round(v) for k, v in wedges.items()})
    assert abs(wedges["square"]) < 0.01 * iso_wedge
    assert 0 < wedges["acme"] < iso_wedge and 0 < wedges["trapezoidal"] < iso_wedge
    assert wedges["buttress"] == pytest.approx(LOAD * math.tan(math.radians(7)), rel=0.15)
    assert wedges["buttress/other"] == pytest.approx(LOAD * math.tan(math.radians(45)), rel=0.15)


def _model_3d(solver, kind="bonded", size=3.0, fine=1.0, refine=("Screw/Thread", "Boss/Bore")):
    """The smooth model revolved: a screw (minor radius over the engagement, the 6.7 mm
    shank above) in a boss with a bore at the same radius, held on its top face (and
    softly on its outside, against rigid motion), pulled at the screw's top."""
    from puzzle.model.schema import NamedSelection as Sel

    p = Project(name="thread3d")
    steel = add_library_material(p, "structural_steel")
    m = MM
    screw = p.add(Geometry(name="Screw", history=[
        HistoryStep(kind="cylinder", name="S", args={"r": R_PITCH * m, "h": ENGAGED * m}),
        HistoryStep(kind="cylinder", name="K", args={"r": R_SHANK * m, "h": (TOP - ENGAGED) * m,
                                                      "z": ENGAGED * m, "mode": "add"}),
    ]))  # fmt: skip
    boss = p.add(Geometry(name="Boss", history=[
        HistoryStep(kind="cylinder", name="B", args={"r": R_BOSS * m, "h": ENGAGED * m}),
        HistoryStep(kind="cylinder", name="H", args={"r": R_PITCH * m, "h": ENGAGED * m,
                                                      "mode": "cut"}),
    ]))  # fmt: skip
    screw.selections = [Sel(name="Thread", entity="face", members=["S/wall"]),
                        Sel(name="Top", entity="face", members=["K/end"])]  # fmt: skip
    boss.selections = [Sel(name="Bore", entity="face", members=["H/wall"]),
                       Sel(name="Held", entity="face", members=["B/end"]),
                       Sel(name="Outer", entity="face", members=["B/wall"])]  # fmt: skip
    near = Quantity(value=fine, unit="mm")
    mesh = p.add(MeshRecipe(name="Tets", element_order=2,
                            global_size=Quantity(value=size, unit="mm"),
                            local_sizes={k: near for k in refine}))  # fmt: skip
    thread = Thread(form="iso", size="M16")
    comp = p.add(Component(
        name="Joint", mesh=mesh.id,
        parts=[PartInstance(name="Screw", geometry=screw.id),
               PartInstance(name="Boss", geometry=boss.id)],
        materials=[MaterialAssignment(material=steel.id, selection="Screw"),
                   MaterialAssignment(material=steel.id, selection="Boss")],
        contacts=[Contact(name="Thread", type=kind, source="Screw/Thread", target="Boss/Bore",
                          thread=thread)],
        physics=[Physics(type="solid_mechanics", features=[
            Feature(type="displacement", selection="Boss/Held",
                    values={"z": Quantity(value=0, unit="m")}),
            Feature(type="elastic_support", selection="Boss/Outer",
                    values={"kn": Quantity(value=1, unit="N/mm^3"),
                            "kt": Quantity(value=1, unit="N/mm^3")}),
            Feature(type="force", selection="Screw/Top",
                    values={"z": Quantity(value=LOAD, unit="N")})])],
    ))  # fmt: skip
    study = p.add(Study(name="Pull", component=comp.id, solver=solver,
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    return p, study


@over("calculix")
def test_the_revolved_model_shares_as_the_two_dimensional_one(tmp_path, solver):
    """3D on tets (ADR 0076 step 3): each thread's share within 3 points of the 2D
    axisymmetric model's, the wedge within 5 %, the flank pressure even around the first
    thread (its spread under 10 %)."""
    two_d, load_2d, wedge_2d = _smooth(tmp_path, solver, "2d")
    p, study = _model_3d(solver)
    run = run_study(p, study.id, tmp_path, tmp_path / "3d", timeout_s=3600)
    step = run.results.steps[-1]
    load, wedge = step.fields["thread_load"], step.fields["thread_wedge"]
    s2, s3 = load_2d.values[0][:8] / LOAD, load.values[0][:8] / LOAD
    print("2D", np.round(s2 * 100, 1), "3D", np.round(s3 * 100, 1),
          f"wedge 2D {wedge_2d.values[0][0]:.0f} N, 3D {wedge.values[0][0]:.0f} N")  # fmt: skip
    assert load.values[0].sum() / LOAD == pytest.approx(1.0, abs=0.02)
    assert np.all(np.abs(s3 - s2) < 0.03)
    assert wedge.values[0][0] == pytest.approx(wedge_2d.values[0][0], rel=0.05)
    # Around the first thread (within a pitch of the mouth) the flank pressure is even.
    force = step.fields["thread_force"]
    xyz = run.mesh.node_coords(force.ids)
    near = xyz[:, 2] > (ENGAGED - P) * MM
    normal = force.values[near, 0]
    angle = np.arctan2(xyz[near, 1], xyz[near, 0])
    bins = np.digitize(angle, np.linspace(-np.pi, np.pi, 9))
    per_sector = np.array([normal[bins == b].sum() for b in range(1, 9)])
    spread = (per_sector.max() - per_sector.min()) / per_sector.mean()
    print("first thread per 45° sector", np.round(per_sector), f"spread {spread:.3f}")
    assert spread < 0.10


def _measure(tmp_path, solver, kind, name):
    """The smooth model on six-node triangles (a mesh both solvers read): each thread's
    share by section cuts, the axial stiffness and the wedge."""
    p, study = _model(False, solver, kind=kind, shape="tetrahedra")
    run = run_study(p, study.id, tmp_path, tmp_path / name, timeout_s=1800)
    shares, _ = _shares(run)
    u = run.results.last("displacement")
    top = [i for i, x in zip(u.ids, run.mesh.node_coords(u.ids), strict=True)
           if abs(x[1] - TOP * MM) < 1e-9]  # fmt: skip
    at = {int(i): k for k, i in enumerate(u.ids)}
    rise = float(np.mean([u.values[at[int(i)]][1] for i in top]))
    step = run.results.steps[-1]
    load = step.fields["thread_load"].values[0] / LOAD
    return shares, LOAD / rise, step.fields["thread_wedge"].values[0][0], load


@pytest.mark.parametrize("kind", ["bonded", "no_separation", "frictionless"])
@over("fenicsx")
def test_fenicsx_threads_match_calculix(tmp_path, solver, kind):
    """ADR 0076 step 5: FEniCSx's flank springs (added to its assembled stiffness; an
    active set for frictionless flanks) against CalculiX's on one mesh: each thread's
    share within 2 points, the stiffness and the wedge within 2 %. Measured: bonded and
    no_separation agree to 0.03 points and four digits; frictionless shares within 0.4
    points and stiffness 0.2 %, but CalculiX's own thread_load sums to 103 % of the load
    there (its nonlinear springs, in an NLGEOM step) where FEniCSx's sums to 100 %, so
    its wedge is 3.4 % larger: compared to 5 %, and FEniCSx's load checked to sum to 1."""
    if not available("calculix"):
        pytest.skip("calculix not available")
    f_shares, f_stiff, f_wedge, f_load = _measure(tmp_path, solver, kind, "fenicsx")
    c_shares, c_stiff, c_wedge, _ = _measure(tmp_path, "calculix", kind, "calculix")
    print(kind, "fenicsx", np.round(f_shares * 100, 2), f"{f_stiff / 1e9:.4f} kN/µm",
          f"wedge {f_wedge:.0f} N; calculix", np.round(c_shares * 100, 2),
          f"{c_stiff / 1e9:.4f} kN/µm", f"wedge {c_wedge:.0f} N")  # fmt: skip
    assert np.all(np.abs(f_shares - c_shares) < 0.02)
    assert f_stiff == pytest.approx(c_stiff, rel=0.02)
    assert f_wedge == pytest.approx(c_wedge, rel=0.05 if kind == "frictionless" else 0.02)
    assert f_load.sum() == pytest.approx(1.0, abs=0.01)


CASES = [("bonded", 0.0), ("no_separation", 0.0), ("frictionless", 0.0), ("frictionless", 0.01)]


@pytest.mark.parametrize(("kind", "clearance"), CASES)
@over("code_aster")
def test_code_aster_threads_match_calculix(tmp_path, solver, kind, clearance):
    """ADR 0076 step 3: Code_Aster's flanks (a helper node per pair whose DX is the
    opening along n, a point spring on it, DIS_CHOC for frictionless ones; per radian in
    AXIS) against CalculiX's on one mesh: each thread's share within 2 points, the
    stiffness within 2 %, the wedge within 2 % where both are linear. Measured: bonded and
    no_separation agree to 0.02 points and four digits; frictionless shares within 0.4
    points and the stiffness 0.2 % (0.4 % with a 0.01 mm clearance: 0.772 kN/µm, by hand
    0.772). CalculiX's frictionless flanks solve in an NLGEOM step and its wedge comes out
    3 % (10 % with the clearance) above F tan 30°; Code_Aster's is within 2 % of it, as the
    bonded bearing flank's (23,094 N), so that is what a frictionless wedge is checked
    against."""
    if not available("calculix"):
        pytest.skip("calculix not available")
    extra = {"clearance": Quantity(value=clearance, unit="mm")} if clearance else {}

    def measure(solver, name):
        p, study = _model(False, solver, kind=kind)
        c = p.objects[study.component].contacts[0]
        c.thread = Thread(form="iso", size="M16", **extra)
        run = run_study(p, study.id, tmp_path, tmp_path / name, timeout_s=1800)
        shares, _ = _shares(run)
        u = run.results.last("displacement")
        top = [i for i, x in zip(u.ids, run.mesh.node_coords(u.ids), strict=True)
               if abs(x[1] - TOP * MM) < 1e-9]  # fmt: skip
        at = {int(i): k for k, i in enumerate(u.ids)}
        rise = float(np.mean([u.values[at[int(i)]][1] for i in top]))
        step = run.results.steps[-1]
        assert u.ids.max() <= run.mesh.node_ids.max()  # the helper nodes dropped
        return (shares, LOAD / rise, step.fields["thread_wedge"].values[0][0],
                step.fields["thread_load"].values[0] / LOAD)  # fmt: skip

    a_shares, a_stiff, a_wedge, a_load = measure(solver, "code_aster")
    c_shares, c_stiff, c_wedge, _ = measure("calculix", "calculix")
    print(kind, clearance, "code_aster", np.round(a_shares * 100, 2), f"{a_stiff / 1e9:.4f} kN/µm",
          f"wedge {a_wedge:.0f} N; calculix", np.round(c_shares * 100, 2),
          f"{c_stiff / 1e9:.4f} kN/µm", f"wedge {c_wedge:.0f} N")  # fmt: skip
    assert np.all(np.abs(a_shares - c_shares) < 0.02)
    assert a_stiff == pytest.approx(c_stiff, rel=0.02)
    if kind == "frictionless":  # the bearing flank alone: F tan 30°, as the bonded one's
        assert a_wedge == pytest.approx(LOAD * math.tan(math.radians(30)), rel=0.03)
    else:
        assert a_wedge == pytest.approx(c_wedge, rel=0.02)
    assert a_load.sum() == pytest.approx(1.0, abs=0.01)
    if clearance:  # the flanks close after the clearance along n: c / cos 30° more rise
        bonded = 0.9932e9  # the same model without clearance (kN/µm, measured)
        assert LOAD / a_stiff == pytest.approx(LOAD / bonded + clearance * MM / math.cos(
            math.radians(30)), rel=0.01)  # fmt: skip


def _boss_shares(run):
    """Each thread's share of the load from the boss's side: the axial force through its
    section (σ_zz over the annulus) at each pitch, the first thread (at the held top)
    first. The same measure for a solid screw and a beam bolt."""
    from scipy.interpolate import LinearNDInterpolator

    stress = run.results.last("stress")
    boss = set(np.asarray(run.mesh.groups["Boss"].node_ids).tolist())
    ids = np.array([i for i in stress.ids if int(i) in boss])
    xyz = run.mesh.node_coords(ids)
    szz = stress.at(ids)[:, 2]
    f = LinearNDInterpolator(xyz, szz)
    r = np.linspace(R_PITCH * MM * 1.01, R_BOSS * MM * 0.995, 40)
    t = np.linspace(0, 2 * np.pi, 72, endpoint=False)
    rr, tt = np.meshgrid(r, t)

    def force(z):
        pts = np.column_stack([(rr * np.cos(tt)).ravel(), (rr * np.sin(tt)).ravel(),
                               np.full(rr.size, z)])  # fmt: skip
        s = np.nan_to_num(f(pts)).reshape(rr.shape)
        return float(np.trapezoid(np.trapezoid(s * rr, r, axis=1), t))

    cuts = [k * P * MM for k in range(int(ENGAGED / P) + 1)]
    cuts[0], cuts[-1] = 0.05 * MM, (ENGAGED - 0.05) * MM
    forces = np.array([force(z) for z in cuts])
    return -np.diff(forces)[::-1] / LOAD  # the boss is in compression above its threads


def _beam_bolt_model(solver):
    """A beam M16 cap screw (ISO 8.8, `tapped`) through a plate held 2 mm above the
    boss, into its bore: 40 kN pulls the plate up, the bolt carries it to the thread."""
    from puzzle.model.schema import BeamFastener, BoltType
    from puzzle.model.schema import NamedSelection as Sel

    p, study = _model_3d(solver, refine=("Boss/Bore",))
    comp = p.objects[study.component]
    screw_part = next(x for x in comp.parts if x.name == "Screw")
    m = MM
    plate = p.add(Geometry(name="Plate", history=[
        HistoryStep(kind="box", name="P", args={"dx": 40 * m, "dy": 40 * m, "dz": 10 * m,
                                                "x0": -20 * m, "y0": -20 * m,
                                                "z0": (ENGAGED + 2) * m}),
        HistoryStep(kind="cylinder", name="C", args={"r": 8.5 * m, "h": 10 * m,
                                                     "z": (ENGAGED + 2) * m, "mode": "cut"}),
    ]))  # fmt: skip
    plate.selections = [Sel(name="Hole", entity="face", members=["C/wall"]),
                        Sel(name="Top", entity="face", members=["P/+z"])]  # fmt: skip
    grade = add_library_material(p, "iso_898_1_class_8_8")
    m16 = p.add(BoltType(name="M16 8.8", size="M16", material=grade.id, preload="none",
                         joint="tapped", formulation="beam", ends="distributing"))  # fmt: skip
    comp.parts = [x for x in comp.parts if x is not screw_part]
    comp.parts.append(PartInstance(name="Plate", geometry=plate.id))
    comp.materials = [MaterialAssignment(material=comp.materials[0].material, selection=s)
                      for s in ("Boss", "Plate")]  # fmt: skip
    comp.contacts = []
    comp.connectors = [BeamFastener(name="Cap screw", source="Plate/Hole", target="Boss/Bore",
                                    type=m16.id)]  # fmt: skip
    feats = comp.physics[0].features
    feats[2] = Feature(type="force", selection="Plate/Top",
                       values={"z": Quantity(value=LOAD, unit="N")})  # fmt: skip
    return p, study


@over("calculix")
def test_a_beam_bolt_shares_its_thread_as_a_solid_screw(tmp_path, solver):
    """ADR 0076 step 4: a beam bolt's tapped end as a thread (a core line with flank
    springs) shares its load along the thread within 5 points of the solid smooth screw,
    measured from the boss's side alike."""
    p, study = _model_3d(solver)
    solid = _boss_shares(run_study(p, study.id, tmp_path, tmp_path / "solid", timeout_s=3600))
    p, study = _beam_bolt_model(solver)
    run = run_study(p, study.id, tmp_path, tmp_path / "beam", timeout_s=3600)
    beam = _boss_shares(run)
    print("solid", np.round(solid * 100, 1), "beam", np.round(beam * 100, 1))
    # The boss's section, sampled on a grid by the bore, recovers ~90 % of the load in
    # both (the first run: 0.902 and 0.90); the shares compare as fractions of their sum.
    assert 0.85 < solid.sum() < 1.05 and 0.85 < beam.sum() < 1.05
    assert np.all(np.abs(beam / beam.sum() - solid / solid.sum()) < 0.05)
