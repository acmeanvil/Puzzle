import numpy as np
import pytest
from conftest_geometry import write_bar_step

from puzzle.adapters.common import InputError
from puzzle.adapters.openfoam import OpenFoamAdapter
from puzzle.adapters.openfoam.foam import read_internal_field
from puzzle.geometry import import_step
from puzzle.materials import add_library_material, library_material
from puzzle.mesh import generate_mesh
from puzzle.model import (
    Component,
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

AIR = library_material("air").properties
RHO, MU, K = (AIR[p].to_si() for p in ("density", "dynamic_viscosity", "thermal_conductivity"))


def _duct(tmp_path, flow_settings=None, heat=None, inlet_values=None, order=1, extra_features=()):
    """A 40 x 10 x 10 mm duct of air with an inlet, outlet and walls, as an OpenFOAM case."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    step = write_bar_step(tmp_path / "duct.step", size=(0.04, 0.01, 0.01))
    m = import_step(step)
    p = Project(name="duct")
    geo = p.add(
        Geometry(
            name="Duct",
            source=step.name,
            selections=[
                NamedSelection(name="Fluid", entity="body", members=["body-1"]),
                NamedSelection(name="Inlet", entity="face", members=m.faces_on_plane("x", 0.0)),
                NamedSelection(name="Outlet", entity="face", members=m.faces_on_plane("x", 0.04)),
                NamedSelection(
                    name="Walls",
                    entity="face",
                    members=[*m.faces_on_plane("y", 0.0), *m.faces_on_plane("y", 0.01)],
                ),
                NamedSelection(
                    name="Sides",
                    entity="face",
                    members=[*m.faces_on_plane("z", 0.0), *m.faces_on_plane("z", 0.01)],
                ),
            ],
        )
    )
    air = add_library_material(p, "air")
    mesh = p.add(
        MeshRecipe(name="m", global_size=Quantity(value=2.5, unit="mm"), element_order=order)
    )
    physics = [
        Physics(
            type="fluid_flow",
            settings=flow_settings or {},
            features=[
                Feature(
                    type="velocity_inlet",
                    selection="Duct/Inlet",
                    values={"U": Quantity(value=1, unit="m/s"), **(inlet_values or {})},
                ),
                Feature(
                    type="pressure_outlet",
                    selection="Duct/Outlet",
                    values={"p": Quantity(value=10, unit="Pa")},
                ),
                Feature(type="wall", selection="Duct/Walls"),
                Feature(type="symmetry", selection="Duct/Sides"),
                *extra_features,
            ],
        )
    ]
    if heat is not None:
        physics.append(Physics(type="heat_transfer", features=heat))
    comp = p.add(
        Component(
            name="Duct",
            parts=[PartInstance(name="Duct", geometry=geo.id)],
            mesh=mesh.id,
            physics=physics,
            materials=[MaterialAssignment(material=air.id, selection="Duct/Fluid")],
        )
    )
    study = p.add(
        Study(name="s", component=comp.id, solver="openfoam", steps=[SolveStep(type="stationary")])
    )
    mesh_ = generate_mesh(p, comp.id, tmp_path, tmp_path / "m").mesh
    return OpenFoamAdapter().write_input(p, study, mesh_, tmp_path / "case"), mesh_


def test_case_files(tmp_path):
    allrun, _ = _duct(tmp_path)
    case = allrun.parent
    script = allrun.read_text()
    assert script.index("gmshToFoam") < script.index("simpleFoam") < script.index("postProcess")
    assert "entry0/Duct_Walls/type -set wall" in script
    assert "entry0/Duct_Sides/type -set symmetry" in script
    msh = (case / "mesh.msh").read_text()
    assert msh.startswith("$MeshFormat\n2.2") and '"Duct_Inlet"' in msh
    assert OpenFoamAdapter().command(allrun) == ["bash", "Allrun"]

    u, p = (case / "0" / "U").read_text(), (case / "0" / "p").read_text()
    assert "surfaceNormalFixedValue" in u and "refValue uniform -1;" in u  # into the domain
    assert f"uniform {10 / RHO:.10g};" in p  # kinematic pressure
    nu = (case / "constant" / "transportProperties").read_text()
    assert f"nu {MU / RHO:.10g};" in nu
    assert "simulationType laminar;" in (case / "constant" / "turbulenceProperties").read_text()
    control = (case / "system" / "controlDict").read_text()
    assert "writeFormat ascii;" in control and "writeCompression off;" in control


def test_heat_transfer_case(tmp_path):
    heat = [
        Feature(
            type="temperature",
            selection="Duct/Inlet",
            values={"T": Quantity(value=20, unit="degC")},
        ),
        Feature(
            type="heat_flux", selection="Duct/Walls", values={"q": Quantity(value=1, unit="kW/m^2")}
        ),
    ]
    allrun, _ = _duct(tmp_path, heat=heat)
    case = allrun.parent
    t = (case / "0" / "T").read_text()
    walls = t[t.index("Duct_Walls") :]
    assert "fixedGradient" in walls[:200] and f"uniform {1000 / K:.10g};" in walls[:200]
    inlet = t[t.index("Duct_Inlet") :]
    assert "uniform 293.15;" in inlet[:200]
    control = (case / "system" / "controlDict").read_text()
    prandtl = MU * AIR["specific_heat"].to_si() / K
    assert "scalarTransport" in control and f"alphaD {1 / prandtl:.10g};" in control
    assert "T 1e-08;" in (case / "system" / "fvSolution").read_text()  # T is in the stop test


def test_turbulence_case(tmp_path):
    allrun, _ = _duct(
        tmp_path,
        flow_settings={"turbulence_model": "k-omega-SST"},
        inlet_values={"intensity": Quantity(value=0.04)},
    )
    case = allrun.parent
    assert "RASModel kOmegaSST;" in (case / "constant" / "turbulenceProperties").read_text()
    k, omega = (case / "0" / "k").read_text(), (case / "0" / "omega").read_text()
    assert "intensity 0.04;" in k and "kqRWallFunction" in k
    assert "omegaWallFunction" in omega
    assert "mixingLength 0.0007;" in omega  # 0.07 x 4A/P of the 10 mm square


def test_unsupported_inputs_are_refused(tmp_path):
    with pytest.raises(InputError, match="first-order"):
        _duct(tmp_path / "a", order=2)
    flow = Feature(
        type="mass_flow_inlet",
        selection="Duct/Inlet",
        values={"m_dot": Quantity(value=1, unit="kg/s")},
    )
    with pytest.raises(InputError, match="mass_flow_inlet"):
        _duct(tmp_path / "b", extra_features=[flow])
    convection = Feature(
        type="convection",
        selection="Duct/Walls",
        values={"h": Quantity(value=10, unit="W/(m^2*K)"), "T_inf": Quantity(value=300, unit="K")},
    )
    with pytest.raises(InputError, match="convection"):
        _duct(tmp_path / "c", heat=[convection])
    with pytest.raises(InputError, match="turbulence_model"):
        _duct(tmp_path / "d", flow_settings={"turbulence_model": "LES"})


def test_field_reader(tmp_path):
    vector = (
        "FoamFile\n{\n    class volVectorField;\n}\ndimensions [0 1 -1 0 0 0 0];\n"
        "internalField nonuniform List<vector> \n3\n(\n(1 2 3)\n(4 5 6)\n(-7 8e-3 9)\n)\n;\n"
        "boundaryField\n{\n    inlet\n    {\n        type fixedValue;\n"
        "        value nonuniform List<vector> 1((0 0 0));\n    }\n}\n"
    )
    (tmp_path / "U").write_text(vector)
    assert read_internal_field(tmp_path / "U", 3).tolist() == [
        [1, 2, 3],
        [4, 5, 6],
        [-7, 0.008, 9],
    ]
    (tmp_path / "p").write_text("dimensions [0 2 -2 0 0 0 0];\ninternalField uniform 2.5;\n")
    assert read_internal_field(tmp_path / "p", 2).tolist() == [[2.5], [2.5]]
    with pytest.raises(ValueError, match="cells"):
        read_internal_field(tmp_path / "U", 4)


def _list(values: np.ndarray, kind: str) -> str:
    rows = "\n".join(
        "(" + " ".join(f"{v:.12g}" for v in row) + ")" if kind == "vector" else f"{row[0]:.12g}"
        for row in values
    )
    return f"internalField nonuniform List<{kind}> \n{len(values)}\n(\n{rows}\n)\n;\n"


def test_results_map_back_to_elements(tmp_path):
    """A solved case, faked: cells in reverse order, values that are functions of x."""
    allrun, mesh = _duct(tmp_path)
    case = allrun.parent
    centres = np.load(case / "element-centroids.npy")[::-1]
    final = case / "170"
    final.mkdir()
    (final / "C").write_text(_list(centres, "vector"))
    (final / "U").write_text(_list(np.column_stack([centres[:, 0], 0 * centres[:, :2]]), "vector"))
    (final / "p").write_text(_list(centres[:, :1] * 2.0, "scalar"))
    results = OpenFoamAdapter().read_results(allrun)
    velocity, pressure = results.last("velocity"), results.last("pressure")
    assert velocity.location == "element"
    blocks = [b for b in mesh.blocks if b.dim == 3]
    ids = np.concatenate([b.ids for b in blocks])
    x = np.vstack(
        [
            mesh.node_coords(b.connectivity.ravel()).reshape(*b.connectivity.shape, 3).mean(axis=1)
            for b in blocks
        ]
    )[:, 0]
    assert np.allclose(velocity.at(ids)[:, 0], x)
    assert np.allclose(pressure.at(ids)[:, 0], 2.0 * x * RHO)  # back to Pa
