"""Symmetry planes on every FE solver (ADR 0081 step 1): an eighth of a bar in tension,
held only by symmetry on x = 0, y = 0 and z = 0, against the closed form: the end moves
σL/E and the sides draw in νσb/E. Uniform strain, so any mesh gets it to round-off."""

import pytest
from conftest_geometry import BAR, bar_project, write_bar_step
from solvers import over

from puzzle.geometry import import_step
from puzzle.model.schema import (
    Feature,
    Material,
    NamedSelection,
    Physics,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.solve import run_study

STRESS = 10e6  # Pa, pulling the end


@over("calculix", "code_aster", "elmer", "fenicsx")
def test_symmetry_planes_hold_a_bar_in_tension(tmp_path, solver):
    pytest.importorskip("OCP")
    step = write_bar_step(tmp_path / "bar.step")
    model = import_step(step)
    physics = Physics(type="solid_mechanics", settings={"temperature_dependent_materials": False},
                      features=[
                          Feature(type="symmetry", selection="Bar/Root"),
                          Feature(type="symmetry", selection="Bar/Side"),
                          Feature(type="symmetry", selection="Bar/Bottom"),
                          Feature(type="pressure", selection="Bar/Tip",
                                  values={"p": Quantity(value=-STRESS, unit="Pa")}),
                      ])  # fmt: skip
    project, comp = bar_project(step, [physics], 5.0)
    geo = project.objects[comp.parts[0].geometry]
    geo.selections += [
        NamedSelection(name="Side", entity="face", members=model.faces_on_plane("y", 0.0)),
        NamedSelection(name="Top", entity="face", members=model.faces_on_plane("y", BAR[1])),
        NamedSelection(name="Bottom", entity="face", members=model.faces_on_plane("z", 0.0)),
    ]
    study = project.add(Study(name="S", component=comp.id, solver=solver,
                              steps=[SolveStep(type="stationary")]))  # fmt: skip
    run = run_study(project, study.id, tmp_path, tmp_path / "w", timeout_s=600)
    steel = next(iter(project.of_type(Material)))
    E, nu = steel.properties["youngs_modulus"].to_si(), steel.properties["poissons_ratio"].to_si()
    u = run.results.last("displacement")
    tip = u.at(run.mesh.groups["Bar/Tip"].node_ids)[:, 0].mean()
    top = u.at(run.mesh.groups["Bar/Top"].node_ids)[:, 1].mean()
    print(f"{solver}: end {tip:.8g} m against {STRESS * BAR[0] / E:.8g}; "
          f"side {top:.8g} m against {-nu * STRESS * BAR[1] / E:.8g}")  # fmt: skip
    assert tip == pytest.approx(STRESS * BAR[0] / E, rel=1e-4)
    assert top == pytest.approx(-nu * STRESS * BAR[1] / E, rel=1e-4)
    # Nothing moves through the planes.
    root = u.at(run.mesh.groups["Bar/Root"].node_ids)[:, 0]
    assert abs(root).max() <= 1e-12 + 1e-9 * tip


TURN = 30.0  # degrees about z: the bar's planes are across no global axis but z


def _turned_bar(tmp_path):
    """The bar turned TURN degrees about z, with its faces named by where they are."""
    import math

    import gmsh

    from puzzle.geometry import gmsh_session

    path = tmp_path / "turned.step"
    with gmsh_session("turned"):
        box = gmsh.model.occ.addBox(0, 0, 0, *BAR)
        gmsh.model.occ.rotate([(3, box)], 0, 0, 0, 0, 0, 1, math.radians(TURN))
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    return path


@over("calculix", "code_aster", "elmer")
def test_symmetry_planes_across_no_axis(tmp_path, solver):
    """The same eighth of a bar, turned 30° about z: its root and side planes are across
    no global axis (CalculiX *TRANSFORM, Code_Aster LIAISON_OBLIQUE, Elmer
    Normal-Tangential), and the answer is the same closed form along the bar."""
    import math

    import numpy as np

    pytest.importorskip("OCP")
    step = _turned_bar(tmp_path)
    model = import_step(step)
    a = math.radians(TURN)
    along, across = (
        np.array([math.cos(a), math.sin(a), 0.0]),
        np.array([-math.sin(a), math.cos(a), 0.0]),
    )

    def where(test):
        return [e.name for e in model.of_dim(2) if test(np.asarray(e.center))]

    tol = 1e-6
    faces = {
        "Root": where(lambda c: abs(c @ along) < tol),
        "Tip": where(lambda c: abs(c @ along - BAR[0]) < tol),
        "Side": where(lambda c: abs(c @ across) < tol),
        "Top": where(lambda c: abs(c @ across - BAR[1]) < tol),
        "Bottom": where(lambda c: abs(c[2]) < tol),
    }
    assert all(len(v) == 1 for v in faces.values()), faces
    physics = Physics(type="solid_mechanics", settings={"temperature_dependent_materials": False},
                      features=[
                          Feature(type="symmetry", selection="Bar/Root"),
                          Feature(type="symmetry", selection="Bar/Side"),
                          Feature(type="symmetry", selection="Bar/Bottom"),
                          Feature(type="pressure", selection="Bar/Tip",
                                  values={"p": Quantity(value=-STRESS, unit="Pa")}),
                      ])  # fmt: skip
    project, comp = bar_project(step, [physics], 5.0)
    geo = project.objects[comp.parts[0].geometry]
    geo.selections = [s for s in geo.selections if s.entity == "body"] + [
        NamedSelection(name=k, entity="face", members=v) for k, v in faces.items()
    ]
    study = project.add(Study(name="S", component=comp.id, solver=solver,
                              steps=[SolveStep(type="stationary")]))  # fmt: skip
    if solver == "elmer":  # it averages normals where the two oblique planes meet
        with pytest.raises(Exception, match="meet at an edge"):
            run_study(project, study.id, tmp_path, tmp_path / "w", timeout_s=600)
        return
    run = run_study(project, study.id, tmp_path, tmp_path / "w", timeout_s=600)
    steel = next(iter(project.of_type(Material)))
    E, nu = steel.properties["youngs_modulus"].to_si(), steel.properties["poissons_ratio"].to_si()
    u = run.results.last("displacement")
    tip = (u.at(run.mesh.groups["Bar/Tip"].node_ids) @ along).mean()
    top = (u.at(run.mesh.groups["Bar/Top"].node_ids) @ across).mean()
    print(f"{solver}: end {tip:.8g} m against {STRESS * BAR[0] / E:.8g}; "
          f"side {top:.8g} m against {-nu * STRESS * BAR[1] / E:.8g}")  # fmt: skip
    assert tip == pytest.approx(STRESS * BAR[0] / E, rel=1e-4)
    assert top == pytest.approx(-nu * STRESS * BAR[1] / E, rel=1e-4)
    root = u.at(run.mesh.groups["Bar/Root"].node_ids) @ along
    assert abs(root).max() <= 1e-9 * tip + 1e-12


RING = (0.02, 0.04, 0.01)  # inner and outer radius, height (m)
SECTOR = 30.0  # degrees
PRESSURE = 50e6  # Pa, inside


def _ring_sector(tmp_path):
    import math

    import gmsh

    from puzzle.geometry import gmsh_session

    a, b, h = RING
    path = tmp_path / "sector.step"
    with gmsh_session("sector"):
        outer = gmsh.model.occ.addCylinder(0, 0, 0, 0, 0, h, b, angle=math.radians(SECTOR))
        inner = gmsh.model.occ.addCylinder(0, 0, 0, 0, 0, h, a, angle=math.radians(SECTOR))
        gmsh.model.occ.cut([(3, outer)], [(3, inner)])
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    return path


@over("calculix", "code_aster", "elmer")
def test_a_ring_sector_on_symmetry_planes(tmp_path, solver):
    """A 30° sector of a thick ring under internal pressure, on symmetry planes at 0°
    (across y), 30° (across no axis) and its base: Lamé's plane-stress solution, exact for
    a short ring with free ends (σz = 0, σr + σθ constant). The oblique plane meets no
    other oblique plane, so Elmer writes it too."""
    import math

    import numpy as np

    pytest.importorskip("OCP")
    a, b, h = RING
    step = _ring_sector(tmp_path)
    model = import_step(step)
    t = math.radians(SECTOR)
    cut = np.array([-math.sin(t), math.cos(t), 0.0])  # the 30° plane's normal

    def where(test):
        return [e.name for e in model.of_dim(2) if test(np.asarray(e.center))]

    tol = 1e-6
    radius = lambda c: math.hypot(c[0], c[1])  # noqa: E731
    faces = {
        "Start": where(lambda c: abs(c[1]) < tol and abs(c[2] - h / 2) < h / 4),
        "End": where(lambda c: abs(c @ cut) < tol and abs(c[2] - h / 2) < h / 4),
        "Base": where(lambda c: abs(c[2]) < tol),
        "Inside": where(lambda c: abs(c[2] - h / 2) < tol and radius(c) < a),
        "Outside": where(lambda c: abs(c[2] - h / 2) < tol and radius(c) > 0.9 * b),
    }
    assert all(len(v) == 1 for v in faces.values()), faces
    physics = Physics(type="solid_mechanics", settings={"temperature_dependent_materials": False},
                      features=[
                          Feature(type="symmetry", selection="Bar/Start"),
                          Feature(type="symmetry", selection="Bar/End"),
                          Feature(type="symmetry", selection="Bar/Base"),
                          Feature(type="pressure", selection="Bar/Inside",
                                  values={"p": Quantity(value=PRESSURE, unit="Pa")}),
                      ])  # fmt: skip
    project, comp = bar_project(step, [physics], 2.5)
    geo = project.objects[comp.parts[0].geometry]
    geo.selections = [s for s in geo.selections if s.entity == "body"] + [
        NamedSelection(name=k, entity="face", members=v) for k, v in faces.items()
    ]
    study = project.add(Study(name="S", component=comp.id, solver=solver,
                              steps=[SolveStep(type="stationary")]))  # fmt: skip
    run = run_study(project, study.id, tmp_path, tmp_path / "w", timeout_s=600)
    steel = next(iter(project.of_type(Material)))
    E, nu = steel.properties["youngs_modulus"].to_si(), steel.properties["poissons_ratio"].to_si()
    A = PRESSURE * a**2 / (b**2 - a**2)
    B = PRESSURE * a**2 * b**2 / (b**2 - a**2)
    exact = ((1 - nu) * A * b + (1 + nu) * B / b) / E
    nodes = run.mesh.groups["Bar/Outside"].node_ids
    xyz = run.mesh.node_coords(nodes)
    r_hat = xyz[:, :2] / np.linalg.norm(xyz[:, :2], axis=1)[:, None]
    u = run.results.last("displacement").at(nodes)
    radial = (u[:, :2] * r_hat).sum(axis=1)
    print(f"{solver}: outer radius grows {radial.mean():.8g} m against {exact:.8g} "
          f"(spread {radial.std() / exact:.2e})")  # fmt: skip
    assert radial.mean() == pytest.approx(exact, rel=2e-3)
    assert radial.std() / exact < 2e-3  # the same all round: nothing held off the planes
    through = run.results.last("displacement").at(run.mesh.groups["Bar/End"].node_ids) @ cut
    assert abs(through).max() <= 1e-5 * exact  # CalculiX prints six digits


SHAFT = (0.01, 0.1)  # radius, length (m)
TORQUE = 100.0  # N·m on the whole shaft


@over("calculix", "code_aster", "elmer", "fenicsx")
def test_antisymmetry_halves_a_shaft_in_torsion(tmp_path, solver):
    """Torsion is antisymmetric about any plane through the axis: half a round shaft on
    an antisymmetry plane (nothing moves along the cut), fixed at its base, carrying half
    the torque about the axis, twists TL/(GJ) as the whole one does. A symmetry plane
    there would hold the twist instead."""
    import math

    import gmsh

    from puzzle.geometry import gmsh_session

    pytest.importorskip("OCP")
    r, length = SHAFT
    step = tmp_path / "half.step"
    with gmsh_session("half"):
        gmsh.model.occ.addCylinder(0, 0, 0, 0, 0, length, r, angle=math.pi)
        gmsh.model.occ.synchronize()
        gmsh.write(str(step))
    model = import_step(step)
    faces = {
        "Base": model.faces_on_plane("z", 0.0),
        "Top": model.faces_on_plane("z", length),
        "Cut": model.faces_on_plane("y", 0.0),  # OCC's two radial faces, one plane
    }
    assert [len(v) for v in faces.values()] == [1, 1, 2], faces
    at = dict(zip("xyz", (0, 0, length), strict=True))
    moment = {"Mz": Quantity(value=TORQUE / 2, unit="N*m"),
              **{f"p{a}": Quantity(value=v, unit="m") for a, v in at.items()}}  # fmt: skip
    physics = Physics(type="solid_mechanics", settings={"temperature_dependent_materials": False},
                      features=[
                          Feature(type="fixed", selection="Bar/Base"),
                          Feature(type="antisymmetry", selection="Bar/Cut"),
                          Feature(type="remote_force", selection="Bar/Top", values=moment),
                      ])  # fmt: skip
    project, comp = bar_project(step, [physics], 2.0)
    geo = project.objects[comp.parts[0].geometry]
    geo.selections = [s for s in geo.selections if s.entity == "body"] + [
        NamedSelection(name=k, entity="face", members=v) for k, v in faces.items()
    ]
    study = project.add(Study(name="S", component=comp.id, solver=solver,
                              steps=[SolveStep(type="stationary")]))  # fmt: skip
    run = run_study(project, study.id, tmp_path, tmp_path / "w", timeout_s=900)
    steel = next(iter(project.of_type(Material)))
    E, nu = steel.properties["youngs_modulus"].to_si(), steel.properties["poissons_ratio"].to_si()
    exact = TORQUE * length / (E / (2 * (1 + nu)) * math.pi * (2 * r) ** 4 / 32)
    nodes = run.mesh.groups["Bar/Top"].node_ids
    x, y = run.mesh.node_coords(nodes)[:, :2].T
    u = run.results.last("displacement").at(nodes)
    twist = float((-y * u[:, 0] + x * u[:, 1]).sum() / (x**2 + y**2).sum())
    print(f"{solver}: twist {twist:.8g} rad against {exact:.8g}")
    assert twist == pytest.approx(exact, rel=5e-3)
    along = run.results.last("displacement").at(run.mesh.groups["Bar/Cut"].node_ids)
    assert abs(along[:, [0, 2]]).max() <= 1e-6 * exact * r  # nothing moves along the cut


@over("calculix", "code_aster")
def test_a_cyclic_sector_of_a_ring(tmp_path, solver):
    """The ring as one sector of twelve (ADR 0081 step 3): the 30° face follows the 0°
    face turned (u_t = R u_s), no symmetry planes on the cuts, one point held round so it
    can't spin. Lamé again, and each target node moves as its source node turned."""
    import math

    import numpy as np

    from puzzle.model.schema import CyclicSymmetry

    pytest.importorskip("OCP")
    a, b, h = RING
    step = _ring_sector(tmp_path)
    model = import_step(step)
    t = math.radians(SECTOR)
    cut = np.array([-math.sin(t), math.cos(t), 0.0])

    def where(test, dim=2):
        return [e.name for e in model.of_dim(dim) if test(np.asarray(e.center))]

    tol = 1e-6
    radius = lambda c: math.hypot(c[0], c[1])  # noqa: E731
    faces = {
        "Start": where(lambda c: abs(c[1]) < tol and abs(c[2] - h / 2) < h / 4),
        "End": where(lambda c: abs(c @ cut) < tol and abs(c[2] - h / 2) < h / 4),
        "Base": where(lambda c: abs(c[2]) < tol),
        "Inside": where(lambda c: abs(c[2] - h / 2) < tol and radius(c) < a),
        "Outside": where(lambda c: abs(c[2] - h / 2) < tol and radius(c) > 0.9 * b),
    }
    corner = where(lambda c: np.linalg.norm(c - [b, 0, 0]) < tol, dim=0)
    assert all(len(v) == 1 for v in faces.values()) and len(corner) == 1, (faces, corner)
    physics = Physics(type="solid_mechanics", settings={"temperature_dependent_materials": False},
                      features=[
                          Feature(type="symmetry", selection="Bar/Base"),
                          Feature(type="displacement", selection="Bar/Corner",
                                  values={"y": Quantity(value=0.0, unit="m")}),
                          Feature(type="pressure", selection="Bar/Inside",
                                  values={"p": Quantity(value=PRESSURE, unit="Pa")}),
                      ])  # fmt: skip
    project, comp = bar_project(step, [physics], 2.5)
    comp.cyclic = CyclicSymmetry(source="Bar/Start", target="Bar/End", sectors=12)
    geo = project.objects[comp.parts[0].geometry]
    geo.selections = (
        [s for s in geo.selections if s.entity == "body"]
        + [NamedSelection(name=k, entity="face", members=v) for k, v in faces.items()]
        + [NamedSelection(name="Corner", entity="vertex", members=corner)]
    )
    study = project.add(Study(name="S", component=comp.id, solver=solver,
                              steps=[SolveStep(type="stationary")]))  # fmt: skip
    run = run_study(project, study.id, tmp_path, tmp_path / "w", timeout_s=600)
    steel = next(iter(project.of_type(Material)))
    E, nu = steel.properties["youngs_modulus"].to_si(), steel.properties["poissons_ratio"].to_si()
    A = PRESSURE * a**2 / (b**2 - a**2)
    B = PRESSURE * a**2 * b**2 / (b**2 - a**2)
    exact = ((1 - nu) * A * b + (1 + nu) * B / b) / E
    u = run.results.last("displacement")
    nodes = run.mesh.groups["Bar/Outside"].node_ids
    xyz = run.mesh.node_coords(nodes)
    r_hat = xyz[:, :2] / np.linalg.norm(xyz[:, :2], axis=1)[:, None]
    radial = (u.at(nodes)[:, :2] * r_hat).sum(axis=1)
    print(f"{solver}: outer radius grows {radial.mean():.8g} m against {exact:.8g} "
          f"(spread {radial.std() / exact:.2e})")  # fmt: skip
    assert radial.mean() == pytest.approx(exact, rel=2e-3)
    assert radial.std() / exact < 2e-3
    from puzzle.cyclic import pairs

    p = pairs(comp, run.mesh)
    turned = u.at(p[:, 1]) @ comp.cyclic.rotation().T
    assert np.abs(u.at(p[:, 0]) - turned).max() <= 1e-5 * exact


def _kinked_sector(tmp_path):
    """A 30° sector of the ring whose cut faces each bend at r = 30 mm (radial inside,
    10° on outside): no plane cuts it, so only cyclic symmetry can join its sectors."""
    import math

    import gmsh

    from puzzle.geometry import gmsh_session

    a, b, h = RING

    def turn(p, deg):
        t = math.radians(deg)
        return (p[0] * math.cos(t) - p[1] * math.sin(t), p[0] * math.sin(t) + p[1] * math.cos(t))

    bend = (0.03, 0.0)
    far = turn((0.06, 0.0), 10.0)
    corners = [(0.0, 0.0), bend, far, turn(far, SECTOR), turn(bend, SECTOR)]
    path = tmp_path / "kinked.step"
    with gmsh_session("kinked"):
        occ = gmsh.model.occ
        points = [occ.addPoint(x, y, 0) for x, y in corners]
        lines = [occ.addLine(points[i], points[(i + 1) % len(points)]) for i in range(len(points))]
        face = occ.addPlaneSurface([occ.addCurveLoop(lines)])
        prism = occ.extrude([(2, face)], 0, 0, h)
        solid = [d for d in prism if d[0] == 3]
        ring = occ.cut(
            [(3, occ.addCylinder(0, 0, 0, 0, 0, h, b))], [(3, occ.addCylinder(0, 0, 0, 0, 0, h, a))]
        )[0]
        occ.intersect(ring, solid)
        occ.synchronize()
        gmsh.write(str(path))
    return path


@over("calculix", "code_aster")
def test_a_cyclic_sector_with_kinked_cuts(tmp_path, solver):
    """The ring under internal pressure as one of twelve sectors whose cut faces bend: no
    symmetry plane fits them, cyclic symmetry joins them (each node on the 30° cut
    follows its twin on the 0° cut, turned), and Lamé holds all round."""
    import math

    import numpy as np

    from puzzle.model.schema import CyclicSymmetry

    pytest.importorskip("OCP")
    a, b, h = RING
    step = _kinked_sector(tmp_path)
    model = import_step(step)
    tol = 1e-6
    radius = lambda c: math.hypot(c[0], c[1])  # noqa: E731
    angle = lambda c: math.degrees(math.atan2(c[1], c[0]))  # noqa: E731
    sides = [e for e in model.of_dim(2) if abs(e.center[2] - h / 2) < tol
             and e.kind == "Plane"]  # fmt: skip
    first = [e.name for e in sides if angle(e.center) < SECTOR / 2]
    second = [e.name for e in sides if angle(e.center) >= SECTOR / 2]
    faces = {
        "Start": first,
        "End": second,
        "Base": [e.name for e in model.of_dim(2) if abs(e.center[2]) < tol],
        "Inside": [e.name for e in model.of_dim(2)
                   if abs(e.center[2] - h / 2) < tol and radius(e.center) < a],
        "Outside": [e.name for e in model.of_dim(2)
                    if abs(e.center[2] - h / 2) < tol and radius(e.center) > 0.9 * b],
    }  # fmt: skip
    corner = [
        e.name for e in model.of_dim(0) if np.linalg.norm(np.asarray(e.center) - [a, 0, 0]) < tol
    ]
    assert [len(faces[k]) for k in ("Start", "End", "Base", "Inside", "Outside")] == [2, 2, 1, 1, 1]
    assert len(corner) == 1
    physics = Physics(type="solid_mechanics", settings={"temperature_dependent_materials": False},
                      features=[
                          Feature(type="symmetry", selection="Bar/Base"),
                          Feature(type="displacement", selection="Bar/Corner",
                                  values={"y": Quantity(value=0.0, unit="m")}),
                          Feature(type="pressure", selection="Bar/Inside",
                                  values={"p": Quantity(value=PRESSURE, unit="Pa")}),
                      ])  # fmt: skip
    project, comp = bar_project(step, [physics], 2.5)
    comp.cyclic = CyclicSymmetry(source="Bar/Start", target="Bar/End", sectors=12)
    geo = project.objects[comp.parts[0].geometry]
    geo.selections = (
        [s for s in geo.selections if s.entity == "body"]
        + [NamedSelection(name=k, entity="face", members=v) for k, v in faces.items()]
        + [NamedSelection(name="Corner", entity="vertex", members=corner)]
    )
    study = project.add(Study(name="S", component=comp.id, solver=solver,
                              steps=[SolveStep(type="stationary")]))  # fmt: skip
    run = run_study(project, study.id, tmp_path, tmp_path / "w", timeout_s=600)
    steel = next(iter(project.of_type(Material)))
    E, nu = steel.properties["youngs_modulus"].to_si(), steel.properties["poissons_ratio"].to_si()
    A = PRESSURE * a**2 / (b**2 - a**2)
    B = PRESSURE * a**2 * b**2 / (b**2 - a**2)
    exact = ((1 - nu) * A * b + (1 + nu) * B / b) / E
    nodes = run.mesh.groups["Bar/Outside"].node_ids
    xyz = run.mesh.node_coords(nodes)
    r_hat = xyz[:, :2] / np.linalg.norm(xyz[:, :2], axis=1)[:, None]
    radial = (run.results.last("displacement").at(nodes)[:, :2] * r_hat).sum(axis=1)
    print(f"{solver}: outer radius grows {radial.mean():.8g} m against {exact:.8g} "
          f"(spread {radial.std() / exact:.2e})")  # fmt: skip
    assert radial.mean() == pytest.approx(exact, rel=2e-3)
    assert radial.std() / exact < 2e-3


def test_cyclic_heat_on_kinked_cuts(tmp_path):
    """Heat across the kinked sector (CalculiX, ADR 0081 step 3): bore at 100 °C, rim at
    20 °C, the cuts joined cyclically (T_t = T_s): T(r) = T_a + (T_b - T_a) ln(r/a)/ln(b/a)
    on the cut faces, where nothing but the cyclic link holds the field."""
    import math

    import numpy as np

    from puzzle.model.schema import CyclicSymmetry

    pytest.importorskip("OCP")
    a, b, h = RING
    step = _kinked_sector(tmp_path)
    model = import_step(step)
    tol = 1e-6
    radius = lambda c: math.hypot(c[0], c[1])  # noqa: E731
    angle = lambda c: math.degrees(math.atan2(c[1], c[0]))  # noqa: E731
    sides = [e for e in model.of_dim(2) if abs(e.center[2] - h / 2) < tol and e.kind == "Plane"]
    faces = {
        "Start": [e.name for e in sides if angle(e.center) < SECTOR / 2],
        "End": [e.name for e in sides if angle(e.center) >= SECTOR / 2],
        "Inside": [e.name for e in model.of_dim(2)
                   if abs(e.center[2] - h / 2) < tol and radius(e.center) < a],
        "Outside": [e.name for e in model.of_dim(2)
                    if abs(e.center[2] - h / 2) < tol and radius(e.center) > 0.9 * b],
    }  # fmt: skip
    hot, cold = 100.0, 20.0
    physics = Physics(type="heat_transfer", settings={"temperature_dependent_materials": False},
                      features=[
                          Feature(type="temperature", selection="Bar/Inside",
                                  values={"T": Quantity(value=hot, unit="degC")}),
                          Feature(type="temperature", selection="Bar/Outside",
                                  values={"T": Quantity(value=cold, unit="degC")}),
                      ])  # fmt: skip
    project, comp = bar_project(step, [physics], 2.5)
    comp.cyclic = CyclicSymmetry(source="Bar/Start", target="Bar/End", sectors=12)
    geo = project.objects[comp.parts[0].geometry]
    geo.selections = [s for s in geo.selections if s.entity == "body"] + [
        NamedSelection(name=k, entity="face", members=v) for k, v in faces.items()
    ]
    study = project.add(Study(name="S", component=comp.id, solver="calculix",
                              steps=[SolveStep(type="stationary")]))  # fmt: skip
    run = run_study(project, study.id, tmp_path, tmp_path / "w", timeout_s=600)
    nodes = np.concatenate([run.mesh.groups[g].node_ids for g in ("Bar/Start", "Bar/End")])
    r = np.linalg.norm(run.mesh.node_coords(nodes)[:, :2], axis=1)
    exact = hot + (cold - hot) * np.log(r / a) / np.log(b / a) + 273.15
    got = run.results.last("temperature").at(nodes).ravel()
    print(f"worst {np.abs(got - exact).max():.3g} K")
    assert np.abs(got - exact).max() < 0.05


@over("calculix", "code_aster")
def test_a_periodic_slice_of_a_slab(tmp_path, solver):
    """ADR 0081 step 4: the bar as a slice of an endless slab, its ends linked (u_t = u_s,
    no symmetry planes there), pressed on top, on rollers below and at y = 0, one corner
    held along x. No strain along the slab: u_z(top) = −p h (1 − ν²)/E and the free side
    moves ν (1 + ν) p b / E."""
    import numpy as np

    from puzzle.model.schema import LinearPeriodicity

    pytest.importorskip("OCP")
    step = write_bar_step(tmp_path / "bar.step")
    model = import_step(step)
    length, b, h = BAR
    p = 10e6
    corner = [e.name for e in model.of_dim(0) if np.linalg.norm(np.asarray(e.center)) < 1e-9]
    physics = Physics(type="solid_mechanics", settings={"temperature_dependent_materials": False},
                      features=[
                          Feature(type="symmetry", selection="Bar/Bottom"),
                          Feature(type="symmetry", selection="Bar/Side"),
                          Feature(type="displacement", selection="Bar/Corner",
                                  values={"x": Quantity(value=0.0, unit="m")}),
                          Feature(type="pressure", selection="Bar/Top",
                                  values={"p": Quantity(value=p, unit="Pa")}),
                      ])  # fmt: skip
    project, comp = bar_project(step, [physics], 5.0)
    comp.periodic = LinearPeriodicity(source="Bar/Root", target="Bar/Tip",
                                      translation=(length, 0.0, 0.0))  # fmt: skip
    geo = project.objects[comp.parts[0].geometry]
    geo.selections += [
        NamedSelection(name="Side", entity="face", members=model.faces_on_plane("y", 0.0)),
        NamedSelection(name="Far", entity="face", members=model.faces_on_plane("y", b)),
        NamedSelection(name="Bottom", entity="face", members=model.faces_on_plane("z", 0.0)),
        NamedSelection(name="Top", entity="face", members=model.faces_on_plane("z", h)),
        NamedSelection(name="Corner", entity="vertex", members=corner),
    ]
    study = project.add(Study(name="S", component=comp.id, solver=solver,
                              steps=[SolveStep(type="stationary")]))  # fmt: skip
    run = run_study(project, study.id, tmp_path, tmp_path / "w", timeout_s=600)
    steel = next(iter(project.of_type(Material)))
    E, nu = steel.properties["youngs_modulus"].to_si(), steel.properties["poissons_ratio"].to_si()
    u = run.results.last("displacement")
    top = u.at(run.mesh.groups["Bar/Top"].node_ids)[:, 2].mean()
    far = u.at(run.mesh.groups["Bar/Far"].node_ids)[:, 1].mean()
    print(f"{solver}: top {top:.8g} m against {-p * h * (1 - nu**2) / E:.8g}; "
          f"side {far:.8g} m against {nu * (1 + nu) * p * b / E:.8g}")  # fmt: skip
    assert top == pytest.approx(-p * h * (1 - nu**2) / E, rel=1e-4)
    assert far == pytest.approx(nu * (1 + nu) * p * b / E, rel=1e-4)
    ends = u.at(run.mesh.groups["Bar/Tip"].node_ids)[:, 0]
    assert abs(ends).max() <= 1e-5 * abs(top)  # the slab doesn't stretch
