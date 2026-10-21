"""Geometry files beyond plain STEP: assembly part names, IGES and BREP.

The assembly is written with OCCT's document (XDE) writer, as CAD tools write one, so
the part names travel the same way they do from SolidWorks or Inventor. Test shapes are
built in millimetres, as such files usually are; the import must arrive in metres.
"""

import pytest

pytest.importorskip("OCP")

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402
from OCP.gp import gp_Pnt  # noqa: E402

from puzzle.geometry import import_step  # noqa: E402
from puzzle.geometry.history import _static, write_brep  # noqa: E402


def _assembly(path):
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    shapes = _static(XCAFDoc_DocumentTool, "ShapeTool")(doc.Main())
    for name, origin in (("Bracket", (0, 0, 0)), ("Base plate", (50, 0, 0))):  # mm
        solid = BRepPrimAPI_MakeBox(gp_Pnt(*origin), 20, 20, 10).Shape()
        label = shapes.AddShape(solid, False)
        _static(TDataStd_Name, "Set")(label, TCollection_ExtendedString(name))
    writer = STEPCAFControl_Writer()
    writer.SetNameMode(True)
    writer.Transfer(doc, STEPControl_AsIs)
    assert writer.Write(str(path)) == IFSelect_RetDone


def test_step_assembly_parts_keep_their_names(tmp_path):
    _assembly(tmp_path / "assembly.step")
    model = import_step(tmp_path / "assembly.step")
    parts = model.labelled_bodies()
    assert set(parts) == {"Bracket", "Base plate"}
    (base,) = parts["Base plate"]
    assert model[base].center[0] == pytest.approx(0.06)  # the one placed at x = 50 mm


def test_iges_and_brep_import(tmp_path):
    from OCP.IGESControl import IGESControl_Writer

    write_brep(BRepPrimAPI_MakeBox(0.03, 0.02, 0.01).Shape(), tmp_path / "box.brep")  # m
    box = BRepPrimAPI_MakeBox(30, 20, 10).Shape()  # mm, for the IGES file
    writer = IGESControl_Writer("MM", 1)
    writer.AddShape(box)
    writer.ComputeModel()
    assert writer.Write(str(tmp_path / "box.iges"))
    for name in ("box.brep", "box.iges"):
        model = import_step(tmp_path / name)
        faces = model.of_dim(2)
        assert len(faces) == 6, name
        assert sum(f.measure for f in faces) == pytest.approx(
            2 * (0.03 * 0.02 + 0.03 * 0.01 + 0.02 * 0.01), rel=1e-6
        ), name
