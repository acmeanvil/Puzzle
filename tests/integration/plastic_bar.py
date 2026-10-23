"""A bar pulled 1 % past yield in uniaxial stress, for the plasticity benchmark.

10 x 10 x 100 mm along x; symmetry supports (x on the -x end, y on -y, z on -z) leave it
free to contract, and the +x end is moved 1 mm. The stress state is uniform, so every
node has the closed-form answer of bilinear isotropic hardening:

    eps = s/E + ep,  s = sy + H ep   =>   ep = (eps - sy/E) / (1 + H/E)

with E = 200 GPa, sy = 250 MPa, H = 500 MPa (300 MPa at ep = 0.1): ep = 8.7282e-3,
s = 254.36 MPa. Plastic flow keeps volume: ep_yy = ep_zz = -ep/2.
"""

from __future__ import annotations

from puzzle.model.schema import (
    Component,
    Feature,
    Geometry,
    HistoryStep,
    Material,
    MaterialAssignment,
    MaterialModels,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Physics,
    Project,
    Quantity,
    SolveStep,
    Study,
)

E, NU, SY, H, STRAIN, L = 200e9, 0.3, 250e6, 500e6, 0.01, 0.1
EP = (STRAIN - SY / E) / (1 + H / E)
STRESS = SY + H * EP


def plastic_bar(solver: str = "calculix", increments: int = 10) -> Project:
    p = Project(name="Plastic bar")
    geo = p.add(
        Geometry(
            name="Bar",
            history=[HistoryStep(kind="box", name="Bar", args={"dx": L, "dy": 0.01, "dz": 0.01})],
            selections=[
                NamedSelection(name="Solid", entity="body", members=["Body"]),
                NamedSelection(name="Root", entity="face", members=["Bar/-x"]),
                NamedSelection(name="Tip", entity="face", members=["Bar/+x"]),
                NamedSelection(name="Side Y", entity="face", members=["Bar/-y"]),
                NamedSelection(name="Side Z", entity="face", members=["Bar/-z"]),
            ],
        )
    )
    steel = p.add(
        Material(
            name="Bilinear steel",
            properties={
                "youngs_modulus": Quantity(value=E, unit="Pa"),
                "poissons_ratio": Quantity(value=NU),
                "density": Quantity(value=7850, unit="kg/m^3"),
            },
            models=MaterialModels(
                plasticity={"curves": [{"points": [(0.0, SY), (0.1, SY + H * 0.1)]}]}
            ),
        )
    )
    mesh = p.add(
        MeshRecipe(name="Coarse", global_size=Quantity(value=5, unit="mm"), element_order=2)
    )
    m = Quantity(value=0, unit="m")
    comp = p.add(
        Component(
            name="Bar",
            parts=[PartInstance(name="Bar", geometry=geo.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=steel.id, selection="Bar/Solid")],
            physics=[
                Physics(
                    type="solid_mechanics",
                    features=[
                        Feature(type="displacement", selection="Bar/Root", values={"x": m}),
                        Feature(type="displacement", selection="Bar/Side Y", values={"y": m}),
                        Feature(type="displacement", selection="Bar/Side Z", values={"z": m}),
                        Feature(
                            type="displacement",
                            selection="Bar/Tip",
                            values={"x": Quantity(value=STRAIN * L, unit="m")},
                        ),
                    ],
                )
            ],
        )
    )
    p.add(
        Study(
            name="Pull",
            component=comp.id,
            solver=solver,
            steps=[SolveStep(type="stationary", settings={"increments": increments})],
        )
    )
    return p
