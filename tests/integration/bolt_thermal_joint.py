"""A bolted sleeve for the bolts-under-temperature benchmarks (ADR 0090 package 6,
`test_bolt_thermal.py`).

An aluminium 6061-T6 sleeve (r 6 to 12 mm, L = 40 mm along z) clamped by one ASTM A36
bolt along its axis: a beam bolt (`BeamFastener`, 10 mm, distributing or rigid ends on
the sleeve's end faces), or a modelled one (a solid part: a 5 mm radius shank with a
head and nut 10 mm thick as wide as the sleeve bearing on the end faces, tied or in contact, cut
at mid-length by a pretension section). Weak tangential springs on the sleeve's outside
(1e-6 of E/L per area) hold its rigid motion and nothing else, so the
sleeve grows freely.

The two-spring joint: with C = 1/k_b + 1/k_c the joint's compliance seen from the bolt,
a free length mismatch delta changes the bolt force by delta / C, and a uniform dT
gives delta = (alpha_c - alpha_b) dT L. C is measured on the model itself by a
`bolt_adjustment` (or a beam bolt's preload) run: F = d / C exactly in a linear model.
"""

from __future__ import annotations

from puzzle.materials import add_library_material
from puzzle.model.schema import (
    BeamFastener,
    Component,
    Contact,
    Feature,
    Geometry,
    HistoryStep,
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

MM = 1e-3
L, R_IN, R_OUT = 40 * MM, 6 * MM, 12 * MM  # the sleeve
R_BOLT, D_BEAM, R_HEAD, T_HEAD = 5 * MM, 10 * MM, R_OUT, 10 * MM
T_REF = 293.15  # K, the structural physics' reference (20 °C)


def q(v, u):
    return Quantity(value=v, unit=u)


def plane(name, z):
    return NamedSelection(name=name, entity="face", rule={"plane": {"axis": "z", "at": z}})


def joint(solver: str, bolt: str = "beam", *, sleeve="aluminium_6061_t6", steel="astm_a36",
          preload: float = 10e3, adjustment: float | None = None, contact: str = "bonded",
          ends: str = "distributing", size: float = 2.5, springs: float = 1e-6,
          ) -> tuple[Project, Study, Component]:  # fmt: skip
    """The joint, one static step (the caller sets the steps and the heat). `springs`:
    the weak springs' stiffness per area, times E_sleeve / L (Code_Aster's MUMPS found
    1e-6 too ill-conditioned for a linear static solve with no preload; 1e-4 solves)."""
    p = Project(name="bolted sleeve")
    sleeve_geo = p.add(Geometry(name="Sleeve", history=[
        HistoryStep(kind="cylinder", name="Outer", args={"r": R_OUT, "h": L}),
        HistoryStep(kind="cylinder", name="Bore", args={"r": R_IN, "h": 2 * L, "z": -L / 2,
                                                        "mode": "cut"}),
    ], selections=[
        NamedSelection(name="Body", entity="body", members=["Body"]),
        plane("Top", L), plane("Bottom", 0.0),
        NamedSelection(name="Outside", entity="face", members=["Outer/wall"]),
        NamedSelection(name="Bore", entity="face", members=["Bore/wall"]),
    ]))  # fmt: skip
    al = add_library_material(p, sleeve)
    st = add_library_material(p, steel)
    parts = [PartInstance(name="S", geometry=sleeve_geo.id)]
    materials = [MaterialAssignment(material=al.id, selection="S/Body")]
    e_sleeve = al.properties["youngs_modulus"].to_si()
    kt = springs * e_sleeve / L
    features = [Feature(type="elastic_support", selection="S/Outside",
                        values={"kn": q(0.0, "N/m^3"), "kt": q(kt, "N/m^3")})]  # fmt: skip
    connectors, contacts = [], []
    if bolt == "beam":
        connectors.append(BeamFastener(
            name="Bolt", source="S/Top", target="S/Bottom", diameter=q(D_BEAM, "m"),
            material=st.id, ends=ends, formulation="beam",
            pretension=q(preload, "N") if preload else None))  # fmt: skip
    else:
        bolt_geo = p.add(Geometry(name="Bolt", history=[
            HistoryStep(kind="cylinder", name="Shank", args={"r": R_BOLT, "h": L + 2 * T_HEAD,
                                                             "z": -T_HEAD}),
            HistoryStep(kind="cylinder", name="Head", args={"r": R_HEAD, "h": T_HEAD, "z": L,
                                                            "mode": "add"}),
            HistoryStep(kind="cylinder", name="Nut", args={"r": R_HEAD, "h": T_HEAD,
                                                           "z": -T_HEAD, "mode": "add"}),
            HistoryStep(kind="split", name="Cut", args={"axis": "z", "at": L / 2}),
        ], selections=[
            NamedSelection(name="Body", entity="body", members=["Body"]),
            plane("Under head", L), plane("Under nut", 0.0), plane("Section", L / 2),
            plane("Top of head", L + T_HEAD), plane("Bottom of nut", -T_HEAD),
        ]))  # fmt: skip
        parts.append(PartInstance(name="B", geometry=bolt_geo.id))
        materials.append(MaterialAssignment(material=st.id, selection="B/Body"))
        # the sleeve's ends are the slave faces, inside the head's and the nut's (Code_Aster
        # wants the slave within its master)
        contacts += [
            Contact(name="Head", type=contact, source="S/Top", target="B/Under head"),
            Contact(name="Nut", type=contact, source="S/Bottom", target="B/Under nut"),
        ]
        if adjustment is not None:
            features.append(Feature(type="bolt_adjustment", selection="B/Section",
                                    values={"d": q(adjustment, "m")}))  # fmt: skip
        elif preload:
            features.append(Feature(type="bolt_pretension", selection="B/Section",
                                    values={"F": q(preload, "N")}))  # fmt: skip
    mesh = p.add(MeshRecipe(name="Tets", global_size=q(size, "mm"), element_order=2))
    comp = p.add(Component(
        name="Joint", parts=parts, mesh=mesh.id, materials=materials, contacts=contacts,
        connectors=connectors,
        physics=[Physics(type="solid_mechanics", features=features,
                         settings={"temperature_dependent_materials": False})]))  # fmt: skip
    study = p.add(Study(name="Heat", component=comp.id, solver=solver,
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    return p, study, comp


def heat(selection: str, temperature_k: float, steps=()) -> Feature:
    """A uniform body temperature (K) on a body selection, in `steps` (all when empty)."""
    return Feature(type="body_temperature", selection=selection,
                   values={"T": q(temperature_k, "K")}, steps=list(steps))  # fmt: skip
