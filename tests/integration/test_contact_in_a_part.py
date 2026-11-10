"""A frictionless contact between two bodies of one part, solved (ADR 0039).

The stack of test_contact_results.py (a 10 mm aluminium cube pressed by 20 MPa on a
steel one), but as one STEP file holding both cubes, as an imported assembly is: the
mesher joins the touching bodies, then parts the contact's faces after meshing. The
interface carries p A = 2000 N, all of it through the contact.
"""

import numpy as np
import pytest
from solvers import over

from puzzle.materials import add_library_material
from puzzle.model import Contact, Feature, Quantity
from puzzle.model.schema import (
    Component,
    Geometry,
    MaterialAssignment,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Physics,
    Project,
    SolveStep,
    Study,
)
from puzzle.post.contact import contact_results
from puzzle.solve import run_study

pytest.importorskip("OCP")

SIDE = 0.01
PRESSURE = 20e6


def _write(path):
    from OCP.BRep import BRep_Builder
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    from OCP.TopoDS import TopoDS_Compound

    from puzzle.geometry.prep import _set_unit

    builder, compound = BRep_Builder(), TopoDS_Compound()
    builder.MakeCompound(compound)
    for z in (0, 10):  # mm
        builder.Add(compound, BRepPrimAPI_MakeBox(gp_Pnt(0, 0, z), 10, 10, 10).Shape())
    writer = STEPControl_Writer()
    _set_unit("MM")
    writer.Transfer(compound, STEPControl_AsIs)
    writer.Write(str(path))


def _faces_by_centre(project, geometry, root):
    """face name -> its centre (m), from the imported file."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    from puzzle.geometry.history import _static
    from puzzle.geometry.naming import named_geometry

    out = {}
    for name, faces in named_geometry(project, geometry, root).of["face"].items():
        props = GProp_GProps()
        _static(BRepGProp, "SurfaceProperties")(faces[0], props)
        c = props.CentreOfMass()
        out[name] = np.array([c.X(), c.Y(), c.Z()])
    return out


@over("calculix")
def test_a_stack_in_one_part_carries_its_load_through_the_contact(tmp_path, solver):
    _write(tmp_path / "stack.step")
    p = Project(name="stack")
    geo = p.add(Geometry(name="Stack", source="stack.step", source_unit="mm"))
    centres = _faces_by_centre(p, geo, tmp_path)

    def at(**where):
        axes = {"x": 0, "y": 1, "z": 2}
        return [n for n, c in centres.items()
                if all(abs(c[axes[k]] - v) < 1e-9 for k, v in where.items())]  # fmt: skip

    geo.selections += [
        NamedSelection(name="Base", entity="body", members=["body-1"]),
        NamedSelection(name="Cap", entity="body", members=["body-2"]),
        NamedSelection(name="SymX", entity="face", members=at(x=0.0)),
        NamedSelection(name="SymY", entity="face", members=at(y=0.0)),
        NamedSelection(name="Bottom", entity="face", members=at(z=0.0)),
        NamedSelection(name="Top", entity="face", members=at(z=2 * SIDE)),
    ]
    # The two faces at z = 10 mm: the base's top and the cap's bottom, by their bodies.
    from puzzle.geometry.contacts import _face_owners
    from puzzle.geometry.naming import named_geometry

    owners = _face_owners(named_geometry(p, geo, tmp_path))
    middle = at(z=SIDE)
    for name, body in (("Cap bottom", "body-2"), ("Base top", "body-1")):
        (face,) = [f for f in middle if owners[f] == body]
        geo.selections.append(NamedSelection(name=name, entity="face", members=[face]))

    steel = add_library_material(p, "structural_steel")
    aluminium = add_library_material(p, "aluminium_6061_t6")
    mesh = p.add(MeshRecipe(name="Tets", element_order=2,
                            global_size=Quantity(value=2.5, unit="mm")))  # fmt: skip
    features = [
        Feature(type="displacement", selection=f"Stack/{face}",
                values={axis: Quantity(value=0, unit="m")})
        for face, axis in (("SymX", "x"), ("SymY", "y"), ("Bottom", "z"))
    ] + [Feature(type="pressure", selection="Stack/Top",
                 values={"p": Quantity(value=PRESSURE, unit="Pa")})]  # fmt: skip
    contact = Contact(name="Interface", type="frictionless", source="Stack/Cap bottom",
                      target="Stack/Base top", stiffness="accurate")  # fmt: skip
    comp = p.add(Component(
        name="Stack", mesh=mesh.id, parts=[PartInstance(name="Stack", geometry=geo.id)],
        materials=[MaterialAssignment(material=steel.id, selection="Stack/Base"),
                   MaterialAssignment(material=aluminium.id, selection="Stack/Cap")],
        contacts=[contact],
        physics=[Physics(type="solid_mechanics", features=features)],
    ))  # fmt: skip
    study = p.add(Study(name="Press", component=comp.id, solver=solver,
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    run = run_study(p, study.id, tmp_path, tmp_path / "run", timeout_s=900)
    step = run.results.steps[-1]
    [r] = contact_results(step, comp, run.mesh)
    print(f"{solver}: contact force {r.normal_force:.6g} N, area share {r.area_share:.4f}")
    assert r.normal_force == pytest.approx(PRESSURE * SIDE**2, rel=1e-3)
    assert r.area_share == pytest.approx(1.0, abs=1e-3)
