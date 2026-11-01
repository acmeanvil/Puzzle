"""Hertz line contact (ADR 0056): a steel cylinder (R = 10 mm) pressed onto a steel
block, plane strain (a 0.5 mm slice held at z = 0 and z = t), half the model by
symmetry. The cylinder's top is moved down; the force it then carries per unit
length, P, sets Hertz's contact half-width and peak pressure

    a = sqrt(4 P R / (π E*)),  p0 = 2 P / (π a),  E* = E / (2 (1 - ν²)),

which the contact results are checked against (the half-width to an element)."""

import math

import gmsh
import numpy as np
import pytest
from solvers import over

from puzzle.geometry import gmsh_session, import_step
from puzzle.materials import add_library_material, library_material
from puzzle.model import (
    Component,
    Contact,
    Feature,
    Geometry,
    MaterialAssignment,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Physics,
    Project,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.model.schema import BoxControl
from puzzle.post.contact import contact_results
from puzzle.solve import run_study

R, T, W = 0.010, 0.0005, 0.030  # radius, slice, block width and depth
DROP, FINE = 20e-6, 0.05e-3


def _selections(model, faces):
    out = [NamedSelection(name="Body", entity="body", members=["body-1"])]
    for name, (axis, value) in faces.items():
        out.append(NamedSelection(name=name, entity="face",
                                  members=model.faces_on_plane(axis, value)))  # fmt: skip
    return out


def _cylinder(path):
    with gmsh_session("cylinder"):
        occ = gmsh.model.occ
        # The bottom 1 mm as its own body, so the source face is only the part of the
        # curve that can touch: Code_Aster's pairing failed with the whole quarter.
        pieces = []
        for low, high in ((-R, 1e-3), (1e-3, R)):
            disc = occ.addCylinder(0, R, 0, 0, 0, T, R)
            box = occ.addBox(0, low, 0, R, high - low, T)
            pieces += occ.intersect([(3, disc)], [(3, box)])[0]
        occ.fragment(pieces[:1], pieces[1:])
        occ.synchronize()
        gmsh.write(str(path))
    model = import_step(path)
    out = _selections(model, {"Top": ("y", R), "Sym": ("x", 0.0), "Front": ("z", 0.0),
                              "Back": ("z", T)})  # fmt: skip
    out[0] = NamedSelection(name="Body", entity="body", members=[e.name for e in model.of_dim(3)])
    low = min(model.faces_of_kind("Cylinder"), key=lambda f: model[f].bbox[4])
    out.append(NamedSelection(name="Curved", entity="face", members=[low]))
    return out


def _block(path):
    with gmsh_session("block"):
        gmsh.model.occ.addBox(0, -W, 0, W, W, T)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    model = import_step(path)
    return _selections(model, {"Top": ("y", 0.0), "Bottom": ("y", -W), "Sym": ("x", 0.0),
                               "Front": ("z", 0.0), "Back": ("z", T)})  # fmt: skip


def _hertz(tmp_path, solver):
    p = Project(name="hertz")
    cyl = p.add(Geometry(name="Cylinder", source="cylinder.step",
                         selections=_cylinder(tmp_path / "cylinder.step")))  # fmt: skip
    block = p.add(Geometry(name="Block", source="block.step",
                           selections=_block(tmp_path / "block.step")))  # fmt: skip
    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="Mesh", global_size=Quantity(value=1.5, unit="mm"), controls=[
        BoxControl(name="Contact", low=(0.0, -0.8e-3, 0.0), high=(1.2e-3, 0.8e-3, T),
                   size=Quantity(value=FINE * 1e3, unit="mm"))]))  # fmt: skip
    zero = Quantity(value=0, unit="m")
    held = [Feature(type="displacement", selection=f"{part}/{face}", values={axis: zero})
            for part in ("Cylinder", "Block")
            for face, axis in (("Sym", "x"), ("Front", "z"), ("Back", "z"))]  # fmt: skip
    features = held + [
        Feature(type="displacement", selection="Block/Bottom", values={"y": zero}),
        Feature(type="displacement", selection="Cylinder/Top",
                values={"y": Quantity(value=-DROP, unit="m")}),
    ]  # fmt: skip
    comp = p.add(Component(
        name="Hertz",
        parts=[PartInstance(name="Cylinder", geometry=cyl.id),
               PartInstance(name="Block", geometry=block.id)],
        mesh=mesh.id,
        materials=[MaterialAssignment(material=steel.id, selection=f"{n}/Body")
                   for n in ("Cylinder", "Block")],
        contacts=[Contact(name="Line", type="frictionless", source="Cylinder/Curved",
                          target="Block/Top")],
        physics=[Physics(type="solid_mechanics", features=features)],
    ))  # fmt: skip
    study = p.add(Study(name="Press", component=comp.id, solver=solver,
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    return p, study, comp


@over("calculix")
def test_hertz_line_contact(tmp_path, solver):
    p, study, comp = _hertz(tmp_path, solver)
    run = run_study(p, study.id, tmp_path, tmp_path / "run", timeout_s=3600)
    props = library_material("structural_steel").properties
    e, nu = props["youngs_modulus"].to_si(), props["poissons_ratio"].to_si()
    e_star = e / (2 * (1 - nu**2))
    step = run.results.steps[-1]
    [r] = contact_results(step, comp, run.mesh)
    force = 2 * r.normal_force / T  # per unit length, both halves
    a = math.sqrt(4 * force * R / (math.pi * e_star))
    p0 = 2 * force / (math.pi * a)
    pressure = step.fields["contact_pressure"]
    status = step.fields["contact_status"]
    xyz = run.mesh.node_coords(pressure.ids)
    closed = status.values[:, 0] > 0
    width = float(xyz[closed, 0].max())
    peak = float(pressure.values[:, 0].max())
    print(f"{solver}: P {force:.5g} N/m; half-width {width * 1e3:.4f} mm (Hertz "
          f"{a * 1e3:.4f}); peak {peak:.5g} Pa (Hertz {p0:.5g})")  # fmt: skip
    assert 0.2e-3 < a < 1.0e-3  # the fine box covers the contact
    assert width == pytest.approx(a, abs=1.5 * FINE)
    assert peak == pytest.approx(p0, rel=0.05)
    # The pressure across the contact follows p0 sqrt(1 - (x/a)^2).
    inside = closed & (xyz[:, 0] < 0.8 * a)
    expected = p0 * np.sqrt(1 - (xyz[inside, 0] / a) ** 2)
    assert np.abs(pressure.values[inside, 0] - expected).max() < 0.1 * p0


def test_code_aster_refuses_a_line_contact_and_the_checks_warn(tmp_path):
    """Code_Aster 18's continuous contact fails to pair this model before its first
    increment (66,198 projection failures), with the whole quarter or its bottom 1 mm as
    the source, POINT_FIXE or Newton geometry, quadratic or linear tets; its line-contact
    validation is 2D. So the writer refuses a curved face on a face of another shape, and
    the checks warn before the run; CalculiX runs it (above). Open (ADR 0056)."""
    from puzzle.adapters.code_aster.comm import write_case
    from puzzle.adapters.common import InputError
    from puzzle.contact_check import checks
    from puzzle.mesh import generate_mesh

    p, study, comp = _hertz(tmp_path, "code_aster")
    mesh = generate_mesh(p, comp.id, tmp_path, tmp_path / "mesh").mesh
    warned = [w for w in checks(p, comp, mesh, "code_aster") if w.code == "contact_nonconforming"]
    assert len(warned) == 1 and "CalculiX" in warned[0].message
    assert not [w for w in checks(p, comp, mesh, "calculix") if w.code == "contact_nonconforming"]
    with pytest.raises(InputError, match="curved") as refused:
        write_case(p, study, mesh, tmp_path / "case")
    assert refused.value.code == "unsupported"
