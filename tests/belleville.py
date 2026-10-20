"""Belleville (conical disc) springs between platens, for the stack benchmarks.

A DIN 2093-style spring, 40 x 20.4 x 1.5 mm with a free cone height h0 = 1.15 mm, as a
quarter model (symmetry planes x = 0 and y = 0). Its rectangular section is revolved
about z: the outer rim sits at the bottom, the inner rim at the top. Arrangements:

- single:   base platen | spring | top platen
- series:   two springs face to face (inner rims touching): twice the deflection
- parallel: two springs nested (full faces touching): twice the force

The top platen's upper face is driven down; the base's lower face is fixed. Platens
are ten times stiffer than steel, so the spring takes the deflection. Contacts are
frictionless, springs are the slave (source) side.
"""

from __future__ import annotations

import math
from pathlib import Path

import gmsh
import numpy as np

from puzzle.geometry import gmsh_session, import_step
from puzzle.materials import add_library_material
from puzzle.model import (
    Component,
    Contact,
    Feature,
    Geometry,
    Material,
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

DE, DI, T, H0 = 0.040, 0.0204, 0.0015, 0.00115  # m
RO, RI = DE / 2, DI / 2
PLATE = 0.002  # platen thickness, m
# Rim line contact on a bending-dominated part: CalculiX converges with a softer penalty
# (see its adapter). Code_Aster starts the rim contacts closed.
CONTACT = {
    "calculix": {"penalty_factor": 5.0},
    "code_aster": {"contact_init": "OUI"},  # the spring touches along edges only
}
SIDE = RO + 0.002  # platen half-width (quarter square), m


def almen_laszlo(s: np.ndarray, e: float, nu: float) -> np.ndarray:
    """Spring force (N) at deflection s (m), DIN 2092 without contact flats."""
    delta = DE / DI
    k1 = (
        (1 / math.pi)
        * ((delta - 1) / delta) ** 2
        / ((delta + 1) / (delta - 1) - 2 / math.log(delta))
    )
    st, ht = s / T, H0 / T
    return 4 * e / (1 - nu**2) * T**4 / (k1 * DE**2) * st * ((ht - st) * (ht - st / 2) + 1)


def _spring_step(path: Path, inverted: bool) -> list[NamedSelection]:
    """A quarter spring; inverted puts the inner rim at the bottom."""
    lo, hi = (H0, 0.0) if not inverted else (0.0, H0)  # z of the inner, outer rim bottoms
    with gmsh_session("spring"):
        occ = gmsh.model.occ
        pts = [
            occ.addPoint(RI, 0, lo),
            occ.addPoint(RO, 0, hi),
            occ.addPoint(RO, 0, hi + T),
            occ.addPoint(RI, 0, lo + T),
        ]
        lines = [occ.addLine(pts[i], pts[(i + 1) % 4]) for i in range(4)]
        face = occ.addPlaneSurface([occ.addCurveLoop(lines)])
        occ.revolve([(2, face)], 0, 0, 0, 0, 0, 1, math.pi / 2)
        occ.remove([(2, face)], recursive=True)
        occ.synchronize()
        gmsh.write(str(path))
    model = import_step(path)
    cones = sorted(model.faces_of_kind("Cone"), key=lambda f: model[f].center[2])
    return [
        NamedSelection(name="Body", entity="body", members=["body-1"]),
        NamedSelection(name="Lower", entity="face", members=[cones[0]]),
        NamedSelection(name="Upper", entity="face", members=[cones[1]]),
        NamedSelection(name="SymX", entity="face", members=model.faces_on_plane("x", 0.0)),
        NamedSelection(name="SymY", entity="face", members=model.faces_on_plane("y", 0.0)),
    ]


def _plate_step(path: Path) -> list[NamedSelection]:
    with gmsh_session("plate"):
        gmsh.model.occ.addBox(0, 0, 0, SIDE, SIDE, PLATE)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    model = import_step(path)
    return [
        NamedSelection(name="Body", entity="body", members=["body-1"]),
        NamedSelection(name="Bottom", entity="face", members=model.faces_on_plane("z", 0.0)),
        NamedSelection(name="Top", entity="face", members=model.faces_on_plane("z", PLATE)),
    ]


def project(
    tmp_path: Path,
    solver: str,
    arrangement: str = "single",
    stroke: float = 0.75,
    increments: int = 10,
    order: int = 2,
) -> tuple[Project, Study]:
    """`stroke`: the top platen's travel as a fraction of the stack's free cone height."""
    p = Project(name=f"Belleville {arrangement}")
    spring = p.add(
        Geometry(
            name="Spring",
            source="spring.step",
            selections=_spring_step(tmp_path / "spring.step", False),
        )
    )
    flipped = p.add(
        Geometry(
            name="Flipped spring",
            source="flipped.step",
            selections=_spring_step(tmp_path / "flipped.step", True),
        )
    )
    plate = p.add(
        Geometry(
            name="Platen", source="plate.step", selections=_plate_step(tmp_path / "plate.step")
        )
    )
    steel = add_library_material(p, "structural_steel")
    rigid = p.add(
        Material(
            name="Platen steel",
            properties={
                "youngs_modulus": Quantity(value=2100, unit="GPa"),
                "poissons_ratio": Quantity(value=0.3),
                "density": Quantity(value=7850, unit="kg/m^3"),
            },
        )
    )

    # Springs bottom-up: (name, geometry, z offset).
    if arrangement == "single":
        springs = [("A", spring, 0.0)]
        height, travel = H0 + T, H0
    elif arrangement == "series":  # B flipped on top, inner rims touching
        springs = [("A", spring, 0.0), ("B", flipped, H0 + T)]
        height, travel = 2 * (H0 + T), 2 * H0
    elif arrangement == "parallel":  # B nested one thickness higher
        springs = [("A", spring, 0.0), ("B", spring, T)]
        height, travel = H0 + 2 * T, H0
    else:
        raise ValueError(arrangement)

    parts = [PartInstance(name="Base", geometry=plate.id, translation=(0, 0, -PLATE))]
    parts += [PartInstance(name=n, geometry=g.id, translation=(0, 0, z)) for n, g, z in springs]
    parts.append(PartInstance(name="Press", geometry=plate.id, translation=(0, 0, height)))
    contacts = [
        Contact(
            extensions=CONTACT,
            name="Base contact",
            type="frictionless",
            source="A/Lower",
            target="Base/Top",
        )
    ]
    if arrangement == "series":
        contacts.append(
            Contact(
                extensions=CONTACT,
                name="Inner rims",
                type="frictionless",
                source="B/Lower",
                target="A/Upper",
            )
        )
    elif arrangement == "parallel":
        contacts.append(
            Contact(
                extensions=CONTACT,
                name="Nested faces",
                type="frictionless",
                source="B/Lower",
                target="A/Upper",
            )
        )
    top = springs[-1][0]
    contacts.append(
        Contact(
            extensions=CONTACT,
            name="Press contact",
            type="frictionless",
            source=f"{top}/Upper",
            target="Press/Bottom",
        )
    )

    zero = Quantity(value=0, unit="m")
    features = [Feature(type="fixed", selection="Base/Bottom")]
    for name, _, _ in springs:
        features += [
            Feature(type="displacement", selection=f"{name}/SymX", values={"x": zero}),
            Feature(type="displacement", selection=f"{name}/SymY", values={"y": zero}),
        ]
    features.append(
        Feature(
            type="displacement",
            selection="Press/Top",
            values={"x": zero, "y": zero, "z": Quantity(value=-stroke * travel, unit="m")},
        )
    )

    mesh = p.add(
        MeshRecipe(
            name="Mesh",
            element_order=order,
            global_size=Quantity(value=2.0, unit="mm"),
            local_sizes={f"{n}/Body": Quantity(value=0.5, unit="mm") for n, _, _ in springs},
        )
    )
    comp = p.add(
        Component(
            name="Stack",
            parts=parts,
            mesh=mesh.id,
            contacts=contacts,
            materials=[
                MaterialAssignment(material=rigid.id, selection="Base/Body"),
                MaterialAssignment(material=rigid.id, selection="Press/Body"),
                *(
                    MaterialAssignment(material=steel.id, selection=f"{n}/Body")
                    for n, _, _ in springs
                ),
            ],
            physics=[Physics(type="solid_mechanics", features=features)],
        )
    )
    study = p.add(
        Study(
            name="Compress",
            component=comp.id,
            solver=solver,
            steps=[
                SolveStep(
                    type="stationary",
                    settings={
                        "geometric_nonlinearity": True,
                        "increments": increments,
                        "initial_increment": 0.02,
                    },
                )
            ],
        )
    )
    return p, study


def curve(run) -> tuple[np.ndarray, np.ndarray]:
    """(deflection, force) per increment for the full spring (4 x the quarter model)."""
    from puzzle.results.query import history

    force = history(run.results, run.mesh, "reaction_force", "Press/Top", "total")
    top = history(run.results, run.mesh, "displacement", "Press/Bottom", "mean")
    base = history(run.results, run.mesh, "displacement", "Base/Top", "mean")
    s = np.array([b["mean"][2] - t["mean"][2] for t, b in zip(top, base, strict=True)])
    f = np.array([-4 * h["total"][2] for h in force])
    return s, f
