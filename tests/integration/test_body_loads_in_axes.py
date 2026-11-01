"""Body loads in a coordinate system (ADR 0049 step 5) give what the same load in global
terms gives: gravity in axes turned 90° about z, and a rotation about a cylindrical
system's axis (the feature's own axis values set wrong, so only the system's can be
used). Each against its plain twin, on the same mesh."""

import numpy as np
import pytest
from conftest_geometry import bar_project, write_bar_step
from solvers import over
from test_body_loads import _disc_project

from puzzle.model.schema import CoordinateSystem, Feature, Physics, Quantity, SolveStep, Study
from puzzle.solve import run_study

pytest.importorskip("OCP")


def g(v):
    return Quantity(value=v, unit="m/s^2")


@over("calculix", "code_aster", "fenicsx", "elmer")
def test_gravity_in_turned_axes(tmp_path, solver):
    tips = []
    for in_axes in (False, True):
        where = tmp_path / ("axes" if in_axes else "plain")
        where.mkdir()
        project, comp = bar_project(write_bar_step(where / "bar.step"), [], mesh_size_mm=5)
        turned = project.add(CoordinateSystem(name="Turned", angles=(0.0, 0.0, 90.0)))
        # Global -y is the turned axes' -x (their x is global y).
        weight = (Feature(type="gravity", selection="Bar/Body", axes=turned.id,
                          values={"gx": g(-9810), "gy": g(0), "gz": g(0)})
                  if in_axes else
                  Feature(type="gravity", selection="Bar/Body",
                          values={"gx": g(0), "gy": g(-9810), "gz": g(0)}))  # fmt: skip
        comp.physics = [Physics(type="solid_mechanics", features=[
            Feature(type="fixed", selection="Bar/Root"), weight],
            settings={"temperature_dependent_materials": False})]  # fmt: skip
        study = project.add(Study(name="s", component=comp.id, solver=solver,
                                  steps=[SolveStep(type="stationary")]))  # fmt: skip
        run = run_study(project, study.id, where, where / "run", timeout_s=600)
        tip = run.mesh.groups["Bar/Tip"].node_ids
        tips.append(run.results.last("displacement").at(tip)[:, 1].mean())
    assert tips[0] < 0 and tips[1] == pytest.approx(tips[0], rel=1e-6)


@over("calculix", "code_aster", "fenicsx", "elmer")
def test_rotation_about_a_cylindrical_systems_axis(tmp_path, solver):
    rims = []
    for in_axes in (False, True):
        p, study = _disc_project(solver)
        comp = p.objects[study.component]
        spin = comp.physics[0].features[-1]
        if in_axes:
            axis = p.add(CoordinateSystem(name="Spindle", type="cylindrical", axis=(0.0, 0.0, 1.0)))
            spin.axes = axis.id
            spin.values |= {k: Quantity(value=v, unit="dimensionless")
                            for k, v in (("nx", 1.0), ("ny", 0.0), ("nz", 0.0))}  # fmt: skip
        where = tmp_path / ("axes" if in_axes else "plain")
        run = run_study(p, study.id, where, where / "run", timeout_s=600)
        u = run.results.last("displacement")
        xyz = run.mesh.node_coords(u.ids)
        rim = np.isclose(np.hypot(xyz[:, 0], xyz[:, 1]), 0.1, atol=1e-6)
        rims.append(float(np.mean(u.values[rim, 0] * xyz[rim, 0] + u.values[rim, 1] * xyz[rim, 1])))
    assert rims[0] > 0 and rims[1] == pytest.approx(rims[0], rel=1e-6)
