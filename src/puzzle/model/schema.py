"""Project objects: resources, components, studies and transfers.

See ADR 0006 (solver-neutral IR) and ADR 0009 (project model). Objects refer to
each other by UUID only.

Geometry has a scope. Global geometry lives under Global Definitions and is
shared by reference: every component using it sees the same named selections.
Local geometry belongs to one component; copying that component copies the
geometry and its selections with no link back to the original.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal, TypeVar
from uuid import UUID, uuid4

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    from puzzle.errors import Problem

SCHEMA_VERSION = 1


class Quantity(BaseModel):
    """A value with a unit. Converted to SI only when an adapter exports it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: float
    unit: str = "dimensionless"

    @field_validator("unit")
    @classmethod
    def _known_unit(cls, unit: str) -> str:
        """Refused where it is typed, not later in a solver run."""
        from puzzle.model.units import known

        if not known(unit):
            raise ValueError(f"unknown unit '{unit}' (e.g. mm, MPa, W/(m^2*K), degC)")
        return unit

    def to_si(self) -> float:
        from puzzle.model.units import to_si

        return to_si(self.value, self.unit)


class ProjectObject(BaseModel):
    """Base for every object stored as its own file in a project."""

    model_config = ConfigDict(extra="forbid")

    kind: ClassVar[str]

    id: UUID = Field(default_factory=uuid4)
    name: str

    def dependencies(self) -> list[UUID]:
        """IDs of the objects this one reads from; used for hashing and staleness."""
        return []


class NamedSelection(BaseModel):
    """A set of topology entities, identified by persistent names (ADR 0004)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    entity: Literal["body", "face", "edge", "vertex"]
    members: list[str] = Field(default_factory=list)
    # Where each member was when selected: [kind, measure, x, y, z] in SI. Re-finds a
    # member whose name is gone, and checks file-order names after a re-import (G3).
    anchors: dict[str, list[str | float]] = Field(default_factory=dict)
    # Members described instead of listed, evaluated at every regeneration:
    # {"plane": {"axis": "x", "at": 0.0}}, {"radius": 0.004}, {"kind": "Cylinder"}.
    rule: dict[str, Any] | None = None


# --- Resources ---------------------------------------------------------------


class ParameterSet(ProjectObject):
    kind: ClassVar[str] = "parameters"

    values: dict[str, Quantity] = Field(default_factory=dict)


class HistoryStep(BaseModel):
    """One operation of a geometry history (ADR 0014).

    Lengths are metres; a string value names a parameter. Faces are named by the step
    that made them ("Plate/+z", "Hole1/wall", "Round/edge-2", "Body/side-3"); bodies by
    the `body` argument ("Body" by default) or an enclosure's name. The operations and
    their arguments are listed in puzzle.geometry.history.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "import",
        "heal",
        "remove",
        "fragment",
        "box",
        "cylinder",
        "cone",
        "sphere",
        "extrude",
        "revolve",
        "hole",
        "fillet",
        "chamfer",
        "move",
        "rotate",
        "pattern",
        "split",
        "enclosure",
        "polyline",
        "sheet",
        "curved_sheet",
        "sketch",  # a 2D sketch on a plane or a planar face, for extrude and revolve (ADR 0017)
    ]
    name: str
    args: dict[str, float | str | bool | list[Any] | dict[str, Any]] = Field(default_factory=dict)
    suppressed: bool = Field(default=False, description="skipped when the shape is rebuilt")


class Geometry(ProjectObject):
    """One part's geometry and the named selections defined on it.

    Either a `source` file (STEP, IGES, BREP, or faceted STL/OBJ/3MF; selections name its
    faces "face-N") or a
    `history` that Puzzle regenerates (selections use the history's persistent names).
    """

    kind: ClassVar[str] = "geometry"

    source: str | None = None
    history: list[HistoryStep] = Field(default_factory=list)
    source_unit: str | None = Field(
        default=None, description="STL/OBJ length unit (default mm); 3MF states its own"
    )
    facet_angle: float = Field(
        default=40.0, description="STL/OBJ/3MF: degrees of turn that start a new face"
    )
    scope: Literal["global", "local"] = "global"
    owner: UUID | None = Field(default=None, description="Owning component, for local geometry")
    selections: list[NamedSelection] = Field(default_factory=list)

    @model_validator(mode="after")
    def _owner_matches_scope(self) -> Geometry:
        if (self.scope == "local") != (self.owner is not None):
            raise ValueError("local geometry needs an owner; global geometry has none")
        if (self.source is None) == (not self.history):
            raise ValueError("a geometry has either a source file or a history")
        return self


class TablePoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    temperature: float = Field(description="Kelvin")
    value: float
    source: int | None = Field(default=None, description="Index into Material.sources")


class PropertyTable(BaseModel):
    """A temperature-dependent material property, in SI units.

    Several sources may give a value at the same temperature; `curve()` merges them
    into the single monotonic table solvers need. `reference_temperature` is set
    for secant (mean) thermal expansion, which is measured from that temperature.
    """

    model_config = ConfigDict(extra="forbid")

    unit: str
    points: list[TablePoint] = Field(min_length=1)
    reference_temperature: float | None = Field(default=None, description="Kelvin")

    def curve(self) -> list[tuple[float, float]]:
        """(temperature, value) pairs sorted by temperature, duplicates averaged."""
        by_t: dict[float, list[float]] = {}
        for p in self.points:
            by_t.setdefault(p.temperature, []).append(p.value)
        return [(t, sum(v) / len(v)) for t, v in sorted(by_t.items())]

    def at(self, temperature: float) -> float:
        """Linear interpolation, held constant beyond the table's ends."""
        curve = self.curve()
        if temperature <= curve[0][0]:
            return curve[0][1]
        for (t0, v0), (t1, v1) in itertools.pairwise(curve):
            if temperature <= t1:
                return v0 + (v1 - v0) * (temperature - t0) / (t1 - t0)
        return curve[-1][1]


class HardeningCurve(BaseModel):
    """True stress against equivalent plastic strain; the first point is the yield
    stress at zero plastic strain."""

    model_config = ConfigDict(extra="forbid")

    temperature: float | None = Field(default=None, description="Kelvin; None: any")
    points: list[tuple[float, float]] = Field(
        min_length=1, description="(equivalent plastic strain, true stress in Pa)"
    )

    @model_validator(mode="after")
    def _a_hardening_curve(self) -> HardeningCurve:
        strains = [e for e, _ in self.points]
        if strains[0] != 0:
            raise ValueError("a hardening curve starts at zero plastic strain (the yield stress)")
        if any(b <= a for a, b in zip(strains, strains[1:], strict=False)):
            raise ValueError("plastic strains in a hardening curve must increase")
        if any(s <= 0 for _, s in self.points):
            raise ValueError("stresses in a hardening curve must be positive")
        return self


class Plasticity(BaseModel):
    """Rate-independent plasticity with a von Mises yield surface."""

    model_config = ConfigDict(extra="forbid")

    hardening: Literal["isotropic"] = "isotropic"
    curves: list[HardeningCurve] = Field(min_length=1)


Axis = tuple[float, float, float]


class OrthotropicElasticity(BaseModel):
    """Nine elastic constants in the material axes (axis 1, axis 2, and 3 = 1 x 2)."""

    model_config = ConfigDict(extra="forbid")

    e1: float
    e2: float
    e3: float
    nu12: float
    nu13: float
    nu23: float
    g12: float
    g13: float
    g23: float
    axis1: Axis = (1.0, 0.0, 0.0)
    axis2: Axis = (0.0, 1.0, 0.0)


class Hyperelasticity(BaseModel):
    """Rubber-like materials: Neo-Hookean (C10) or Mooney-Rivlin (C10, C01); D1 sets
    compressibility (0: incompressible). Pa, and 1/Pa for D1."""

    model_config = ConfigDict(extra="forbid")

    model: Literal["neo_hookean", "mooney_rivlin"] = "neo_hookean"
    c10: float
    c01: float = 0.0
    d1: float = 0.0


class AnisotropicConductivity(BaseModel):
    """Thermal conductivity along the material axes, W/(m K)."""

    model_config = ConfigDict(extra="forbid")

    k1: float
    k2: float
    k3: float
    axis1: Axis = (1.0, 0.0, 0.0)
    axis2: Axis = (0.0, 1.0, 0.0)


class MaterialModels(BaseModel):
    """Behaviour beyond single values (ADR 0020). An adapter writes the models its
    capabilities list and refuses the others, never silently dropping one."""

    model_config = ConfigDict(extra="forbid")

    plasticity: Plasticity | None = None
    orthotropic_elasticity: OrthotropicElasticity | None = None
    hyperelasticity: Hyperelasticity | None = None
    anisotropic_conductivity: AnisotropicConductivity | None = None

    def used(self) -> list[str]:
        return [k for k in type(self).model_fields if getattr(self, k) is not None]


class SnCurve(BaseModel):
    """A measured stress-life curve (ADR 0026): stress amplitude against cycles."""

    model_config = ConfigDict(extra="forbid")

    name: str
    basis: str = Field(default="", description="R ratio or mean stress, test conditions")
    points: list[tuple[float, float]] = Field(
        min_length=2, description="(cycles to failure, stress amplitude in Pa)"
    )
    source: str = ""
    reference: str = ""

    @model_validator(mode="after")
    def _an_sn_curve(self) -> SnCurve:
        cycles = [n for n, _ in self.points]
        if any(b <= a for a, b in zip(cycles, cycles[1:], strict=False)):
            raise ValueError("cycles in an S-N curve must increase")
        if any(n <= 0 or s <= 0 for n, s in self.points):
            raise ValueError("cycles and stresses in an S-N curve must be positive")
        return self


class StrainLifeModel(BaseModel):
    """Coffin-Manson-Basquin (εa = σf'/E (2N)^b + εf' (2N)^c, E the material's) or
    Langer (NUREG/CR-6909: ln N = A − B ln(εa% − C))."""

    model_config = ConfigDict(extra="forbid")

    model: Literal["coffin_manson_basquin", "langer"]
    fatigue_strength: float | None = Field(default=None, description="σf' in Pa")
    strength_exponent: float | None = Field(default=None, description="b")
    fatigue_ductility: float | None = Field(default=None, description="εf'")
    ductility_exponent: float | None = Field(default=None, description="c")
    langer_a: float | None = None
    langer_b: float | None = None
    langer_c: float | None = Field(default=None, description="in percent strain")
    source: str = ""
    reference: str = ""

    @model_validator(mode="after")
    def _complete(self) -> StrainLifeModel:
        need = (
            ("langer_a", "langer_b", "langer_c")
            if self.model == "langer"
            else (
                "fatigue_strength",
                "strength_exponent",
                "fatigue_ductility",
                "ductility_exponent",
            )
        )
        missing = [n for n in need if getattr(self, n) is None]
        if missing:
            raise ValueError(f"the {self.model.replace('_', '-')} model needs {', '.join(missing)}")
        return self


class FatigueData(BaseModel):
    """A material's fatigue data (ADR 0026): measured S-N curves and a strain-life model."""

    model_config = ConfigDict(extra="forbid")

    sn_curves: list[SnCurve] = Field(default_factory=list)
    strain_life: StrainLifeModel | None = None

    def empty(self) -> bool:
        return not self.sn_curves and self.strain_life is None


class Material(ProjectObject):
    """A material owned by the project.

    Adding a material from the built-in library copies it in; from then on it is
    static and has no connection to the library (see `puzzle.materials`).
    `properties` hold room-temperature values; `tables` hold temperature-dependent
    data where it exists. `info` carries type, group, specification and notes.
    """

    kind: ClassVar[str] = "material"

    description: str = ""
    info: dict[str, str] = Field(default_factory=dict)
    properties: dict[str, Quantity] = Field(default_factory=dict)
    tables: dict[str, PropertyTable] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)
    models: MaterialModels = Field(default_factory=MaterialModels)
    # Expert pass-through (ADR 0020/0023): solver -> its own material keywords, e.g.
    # {"calculix": "*CREEP, LAW=NORTON ..."}. The adapter places them or refuses.
    solver_keywords: dict[str, str] = Field(default_factory=dict)
    fatigue: FatigueData = Field(default_factory=FatigueData)


class SizeControl(BaseModel):
    """An element size on a selection's faces, edges or bodies (and their boundary)."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["size"] = "size"
    selection: str = Field(description="Part/Selection")
    size: Quantity

    def describe(self) -> str:
        return f"size {self.size.value:g} {self.size.unit} on {self.selection}"


class RefineNearControl(BaseModel):
    """Finer elements near a selection, growing back to the global size: `size` within
    `near` of it, then linearly up to the global size at `far` (Gmsh Distance +
    Threshold fields)."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["refine_near"] = "refine_near"
    selection: str
    size: Quantity
    near: Quantity
    far: Quantity

    def describe(self) -> str:
        return (
            f"refine near {self.selection}: {self.size.value:g} {self.size.unit} within "
            f"{self.near.value:g} {self.near.unit}, back to the global size by "
            f"{self.far.value:g} {self.far.unit}"
        )


class TransfiniteControl(BaseModel):
    """A set number of elements along a selection's edges, evenly or graded."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["transfinite"] = "transfinite"
    selection: str
    count: int = Field(ge=1, description="elements along each edge")
    progression: float = Field(default=1.0, gt=0, description="each element over the one before")

    def describe(self) -> str:
        graded = f", graded {self.progression:g}" if self.progression != 1 else ""
        return f"{self.count} elements along {self.selection}{graded}"


class BoxControl(BaseModel):
    """An element size inside an axis-aligned box (corners in m)."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["box"] = "box"
    name: str = "Box"
    low: tuple[float, float, float]
    high: tuple[float, float, float]
    size: Quantity

    def describe(self) -> str:
        return f"size {self.size.value:g} {self.size.unit} in box {self.name}"


class BallControl(BaseModel):
    """An element size inside a ball (centre in m)."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["ball"] = "ball"
    name: str = "Ball"
    centre: tuple[float, float, float]
    radius: Quantity
    size: Quantity

    def describe(self) -> str:
        return f"size {self.size.value:g} {self.size.unit} in ball {self.name}"


class BoundaryLayerControl(BaseModel):
    """Thin layers of prisms grown off a selection's faces, for walls in flow and steep
    gradients (first layer height, growth ratio, layers)."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["boundary_layer"] = "boundary_layer"
    selection: str
    first_height: Quantity
    growth: float = Field(default=1.2, ge=1.0)
    layers: int = Field(default=5, ge=1)

    def describe(self) -> str:
        return (
            f"boundary layer on {self.selection}: {self.layers} layers from "
            f"{self.first_height.value:g} {self.first_height.unit}, growth {self.growth:g}"
        )


MeshControl = Annotated[
    SizeControl
    | RefineNearControl
    | TransfiniteControl
    | BoxControl
    | BallControl
    | BoundaryLayerControl,
    Field(discriminator="kind"),
]


class MeshRecipe(ProjectObject):
    """How a component is meshed (ADR 0019): the mesher, element order and shape, a
    global size, controls on named selections (in order, like a geometry history), and
    the mesher's own settings, typed where common and passed through otherwise."""

    kind: ClassVar[str] = "mesh"

    mesher: Literal["gmsh", "netgen"] = "gmsh"
    element_order: Literal[1, 2] = 2
    element_shape: Literal["tetrahedra", "hexahedra"] = Field(
        default="tetrahedra",
        description="hexahedra: structured (transfinite) hex mesh of block-like volumes",
    )
    global_size: Quantity | None = None
    controls: list[MeshControl] = Field(default_factory=list)
    curvature_points: int = Field(
        default=12, ge=0, description="elements per 2π of curvature (0: sizes ignore curvature)"
    )
    algorithm_2d: Literal[
        "automatic", "meshadapt", "delaunay", "frontal_delaunay", "frontal_quads", "packing"
    ] = Field(default="frontal_delaunay", description="Gmsh's default: Frontal-Delaunay")
    algorithm_3d: Literal["delaunay", "frontal", "hxt", "mmg"] = "delaunay"
    optimiser: Literal["gmsh", "netgen", "off"] = Field(
        default="gmsh", description="tetrahedra are improved by Gmsh's optimiser, or Netgen's"
    )
    high_order_optimise: Literal["off", "optimise", "elastic_and_optimise", "elastic"] = Field(
        default="elastic_and_optimise",
        description="untangles curved second-order elements",
    )
    smoothing: int = Field(default=1, ge=0, description="smoothing passes of the surface mesh")
    quality_floor: float | None = Field(
        default=None,
        description="smallest element shape quality allowed (1: regular, 0: flat); below it "
        "meshing fails, saying where",
    )
    gmsh_options: dict[str, float | str] = Field(
        default_factory=dict,
        description="any other Gmsh option by its own name (Mesh.AnisoMax); unknown names "
        "are refused",
    )
    netgen_options: dict[str, float | str] = Field(
        default_factory=dict,
        description="Netgen's own settings: grading, curvaturesafety, segmentsperedge, "
        "quad_dominated",
    )

    @model_validator(mode="before")
    @classmethod
    def _local_sizes(cls, data):
        """Older recipes kept local sizes as a map; they are size controls now."""
        if isinstance(data, dict) and "local_sizes" in data:
            data = dict(data)
            sizes = data.pop("local_sizes") or {}
            data["controls"] = [
                {"kind": "size", "selection": name, "size": q} for name, q in sizes.items()
            ] + list(data.get("controls", []))
        return data

    def sizes(self) -> list[SizeControl]:
        return [c for c in self.controls if isinstance(c, SizeControl)]


# --- Components: single parts and assemblies ---------------------------------


class PartInstance(BaseModel):
    """One geometry placed in a component. A single-part component has one."""

    model_config = ConfigDict(extra="forbid")

    name: str
    geometry: UUID
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float] = Field(
        default=(0.0, 0.0, 0.0), description="Euler angles XYZ, degrees"
    )


class Contact(BaseModel):
    """A contact constraint between two selections, usually on different parts.

    Bonded contacts are meshed conformally by default: the parts are fragmented so
    they share nodes on the interface and no contact constraint is needed. When a
    conformal mesh can't be made, `meshing="nonconformal"` meshes the parts
    separately and the solver ties them (CalculiX *TIE, Elmer mortar tie contact).
    Every other contact type is always nonconformal.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    type: Literal["bonded", "no_separation", "frictionless", "frictional", "rough"]
    source: str
    target: str
    friction: float | None = Field(default=None, ge=0)
    meshing: Literal["conformal", "nonconformal"] | None = None
    extensions: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> Contact:
        if (self.type == "frictional") != (self.friction is not None):
            raise ValueError("a friction coefficient is required for, and only for, frictional")
        if self.meshing == "conformal" and self.type != "bonded":
            raise ValueError("only bonded contacts can be meshed conformally")
        return self

    @property
    def effective_meshing(self) -> Literal["conformal", "nonconformal"]:
        if self.meshing:
            return self.meshing
        return "conformal" if self.type == "bonded" else "nonconformal"

    @property
    def capability(self) -> str:
        """The key adapters list in their capability matrix, e.g. "bonded_nonconformal"."""
        return f"bonded_{self.effective_meshing}" if self.type == "bonded" else self.type


class MaterialAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material: UUID
    selection: str


class Feature(BaseModel):
    """A boundary condition, load or initial value applied to a named selection."""

    model_config = ConfigDict(extra="forbid")

    type: str
    selection: str
    values: dict[str, Quantity] = Field(default_factory=dict)
    extensions: dict[str, dict[str, Any]] = Field(
        default_factory=dict, description="Per-solver settings with no neutral equivalent"
    )
    amplitude: UUID | None = Field(
        default=None, description="an Amplitude its values follow over time (ADR 0037)"
    )
    axes: UUID | None = Field(
        default=None,
        description="a CoordinateSystem its x, y, z values are in (cylindrical: r, θ, z)",
    )
    steps: list[int] = Field(
        default_factory=list,
        description="the solve steps (1, 2, ...) it applies in; empty: every step (ADR 0037)",
    )

    def active(self, index: int | None) -> bool:
        """Whether it applies in solve step `index` (1-based; None: any step)."""
        return index is None or not self.steps or index in self.steps


class CoordinateSystem(ProjectObject):
    """Local axes (ADR 0037) that displacements and forces can be given in.

    Cartesian: x along `x_axis`, y in the plane of `x_axis` and `xy_direction`, z their
    cross product. Cylindrical about the line through `origin` along `axis`: a feature's
    x, y, z values are radial, tangential (θ, anticlockwise about `axis`) and axial."""

    kind: ClassVar[str] = "axes"

    type: Literal["cartesian", "cylindrical"] = "cartesian"
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    x_axis: tuple[float, float, float] = (1.0, 0.0, 0.0)
    xy_direction: tuple[float, float, float] = (0.0, 1.0, 0.0)
    axis: tuple[float, float, float] = Field(
        default=(0.0, 0.0, 1.0), description="cylindrical: the axis direction"
    )

    def rotation(self) -> np.ndarray:
        """Cartesian: the 3x3 matrix whose columns are the local x, y, z in global."""
        x = np.asarray(self.x_axis, float)
        x /= np.linalg.norm(x)
        z = np.cross(x, np.asarray(self.xy_direction, float))
        if not np.linalg.norm(z):
            raise ValueError(f"axes '{self.name}': x and the xy direction are parallel")
        z /= np.linalg.norm(z)
        return np.column_stack([x, np.cross(z, x), z])

    def frames(self, xyz) -> np.ndarray:
        """Local axes at points (n, 3, 3): columns radial, tangential, axial for a
        cylindrical system; the same rotation everywhere for a Cartesian one."""
        xyz = np.atleast_2d(np.asarray(xyz, float))
        if self.type == "cartesian":
            return np.repeat(self.rotation()[None], len(xyz), axis=0)
        a = np.asarray(self.axis, float) / np.linalg.norm(self.axis)
        d = xyz - np.asarray(self.origin, float)
        r = d - np.outer(d @ a, a)
        r /= np.maximum(np.linalg.norm(r, axis=1, keepdims=True), 1e-300)
        t = np.cross(a, r)
        return np.stack([r, t, np.repeat(a[None], len(xyz), axis=0)], axis=2)

    def to_global(self, local, xyz) -> np.ndarray:
        """Vectors given in these axes, at points, in global components (n, 3)."""
        return np.einsum("nij,j->ni", self.frames(xyz), np.asarray(local, float))


class Amplitude(ProjectObject):
    """A load's factor against time (ADR 0037), shared by the features that name it:
    linear between its points, the end values held outside them. Transient steps read it
    at each time."""

    kind: ClassVar[str] = "amplitude"

    points: list[tuple[float, float]] = Field(
        min_length=2, description="(time in s, factor on the feature's values)"
    )

    @model_validator(mode="after")
    def _increasing(self) -> Amplitude:
        times = [t for t, _ in self.points]
        if any(b <= a for a, b in zip(times, times[1:], strict=False)):
            raise ValueError("an amplitude's times must increase")
        return self

    def at(self, t: float) -> float:
        times, factors = zip(*self.points, strict=True)
        return float(np.interp(t, times, factors))


class Unknown(BaseModel):
    """A field solved for in a custom PDE, with its finite element space."""

    model_config = ConfigDict(extra="forbid")

    name: str
    family: Literal["Lagrange", "N1curl", "RT", "DG"] = "Lagrange"
    degree: int = Field(default=1, ge=0)
    shape: Literal["scalar", "vector"] = "scalar"


class CustomEquation(BaseModel):
    """A user-written PDE in weak form, for code-generation solvers (FEniCSx).

    `weak_form` is a UFL expression for the residual F, solved as F == 0. It may
    use the unknowns, their test functions (`v_<name>`), and the coefficients.
    """

    model_config = ConfigDict(extra="forbid")

    unknowns: list[Unknown] = Field(min_length=1)
    weak_form: str
    coefficients: dict[str, Quantity] = Field(default_factory=dict)


class Physics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "solid_mechanics",
        "heat_transfer",
        "electrostatics",
        "electric_currents",
        "magnetic_fields",
        "electromagnetic_waves",
        "fluid_flow",
        "custom_pde",
    ]
    features: list[Feature] = Field(default_factory=list)
    settings: dict[str, Quantity | bool | int | float | str] = Field(
        default_factory=dict, description="Physics-wide options, e.g. turbulence_model"
    )
    equation: CustomEquation | None = None

    @model_validator(mode="after")
    def _equation_only_for_custom_pde(self) -> Physics:
        if (self.type == "custom_pde") != (self.equation is not None):
            raise ValueError("an equation is required for, and only for, custom_pde physics")
        return self


Coupling = Literal[
    "thermal_expansion", "joule_heating", "electromagnetic_force", "non_isothermal_flow"
]


class ShellSection(BaseModel):
    """A sheet body's thickness (ADR 0027 shells): on the part, or a selection of its
    faces. `offset` moves the shell's mid-surface off the sheet, in thicknesses: 0 is
    centred, +0.5 puts the sheet on the shell's bottom face (the side against the
    face's normal)."""

    model_config = ConfigDict(extra="forbid")

    selection: str = Field(description="the part, or Part/Selection of its faces")
    thickness: Quantity
    offset: float = Field(default=0.0, ge=-0.5, le=0.5, description="in thicknesses")

    def describe(self) -> str:
        """`10 mm thick`, as typed (the outline and the tree)."""
        t = self.thickness
        where = f", offset {self.offset:+g} t" if self.offset else ""
        return f"{t.value:g} {t.unit} thick{where}"


class BeamSection(BaseModel):
    """A line body's cross-section (ADR 0027 beams): on a selection of its edges (or the
    whole part), a shape and its sizes. The section's first axis is `orientation`
    (made square to the beam): a rectangle's width lies along it and its height across.
    """

    model_config = ConfigDict(extra="forbid")

    selection: str = Field(description="the part, or Part/Selection of its edges")
    shape: Literal["rectangle", "circle", "tube", "box"] = "rectangle"
    width: Quantity | None = Field(default=None, description="rectangle, box: along the first axis")
    height: Quantity | None = Field(default=None, description="rectangle, box: across it")
    diameter: Quantity | None = Field(default=None, description="circle, tube: outside")
    thickness: Quantity | None = Field(default=None, description="tube, box: the wall")
    orientation: tuple[float, float, float] = (0.0, 1.0, 0.0)

    @model_validator(mode="after")
    def _sizes(self) -> BeamSection:
        need = {
            "rectangle": ("width", "height"),
            "circle": ("diameter",),
            "tube": ("diameter", "thickness"),
            "box": ("width", "height", "thickness"),
        }[self.shape]
        missing = [k for k in need if getattr(self, k) is None]
        if missing:
            raise ValueError(f"a {self.shape} section needs {', '.join(missing)}")
        return self

    def describe(self) -> str:
        """`rectangle 20 × 40 mm`, as typed (the outline and the tree)."""
        sizes = {
            "rectangle": ("width", "height"),
            "circle": ("diameter",),
            "tube": ("diameter", "thickness"),
            "box": ("width", "height", "thickness"),
        }[self.shape]
        values = [getattr(self, k) for k in sizes]
        units = {v.unit for v in values}
        if len(units) == 1:
            text = " × ".join(f"{v.value:g}" for v in values) + f" {units.pop()}"
        else:
            text = " × ".join(f"{v.value:g} {v.unit}" for v in values)
        return f"{self.shape} {text}"

    def properties(self) -> dict[str, float]:
        """Area, second moments about the first and second axes, torsion constant (SI)."""
        import math

        if self.shape in ("circle", "tube"):
            d = self.diameter.to_si()
            t = self.thickness.to_si() if self.shape == "tube" else d / 2
            di = d - 2 * t
            area = math.pi * (d**2 - di**2) / 4
            second = math.pi * (d**4 - di**4) / 64
            return {"area": area, "i1": second, "i2": second, "j": 2 * second}
        b, h = self.width.to_si(), self.height.to_si()
        if self.shape == "rectangle":
            short, long_ = min(b, h), max(b, h)
            j = short**3 * long_ * (1 / 3 - 0.21 * short / long_ * (1 - short**4 / (12 * long_**4)))
            return {"area": b * h, "i1": b * h**3 / 12, "i2": h * b**3 / 12, "j": j}
        t = self.thickness.to_si()
        bi, hi = b - 2 * t, h - 2 * t
        j = 2 * t * (b - t) ** 2 * (h - t) ** 2 / (b + h - 2 * t)  # thin-walled box (Bredt)
        return {
            "area": b * h - bi * hi,
            "i1": (b * h**3 - bi * hi**3) / 12,
            "i2": (h * b**3 - hi * bi**3) / 12,
            "j": j,
        }


class BeamFastener(BaseModel):
    """A bolt, pin or rivet as a beam between two selections, with no geometry (ADR 0027).

    The beam runs between the centres of its two ends (a hole face's centre is on its
    axis), with a round section, of a project material. Each end is joined to its
    selection's nodes by a spider: rigid (RBE2, Ansys rigid: it stiffens the hole) or
    distributing (RBE3, Ansys deformable: it spreads the load without stiffening)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["beam_fastener"] = "beam_fastener"
    name: str
    source: str = Field(description="Part/Selection at one end (a hole face, usually)")
    target: str = Field(description="Part/Selection at the other end")
    diameter: Quantity
    material: UUID
    ends: Literal["rigid", "distributing"] = "rigid"
    elements: int = Field(default=1, ge=1, description="beam elements along it")
    pretension: Quantity | None = Field(
        default=None, description="a force applied in the first step, then locked"
    )


class Component(ProjectObject):
    """A single part or an assembly, with its mesh, materials, contacts and physics.

    Part selections are addressed as "<part instance>/<selection>". Selections that
    span parts are defined on the component and addressed by bare name. Couplings
    join physics solved together in one study (multiphysics).
    """

    kind: ClassVar[str] = "component"

    dimension: Literal["3d", "2d_planar", "2d_axisymmetric"] = "3d"
    parts: list[PartInstance] = Field(min_length=1)
    mesh: UUID
    selections: list[NamedSelection] = Field(default_factory=list)
    materials: list[MaterialAssignment] = Field(default_factory=list)
    contacts: list[Contact] = Field(default_factory=list)
    physics: list[Physics] = Field(default_factory=list)
    couplings: list[Coupling] = Field(default_factory=list)
    beam_sections: list[BeamSection] = Field(
        default_factory=list, description="line bodies' cross-sections (ADR 0027 beams)"
    )
    shell_sections: list[ShellSection] = Field(
        default_factory=list, description="sheet bodies' thicknesses (ADR 0027 shells)"
    )
    connectors: list[BeamFastener] = Field(
        default_factory=list, description="fasteners between parts, without geometry (ADR 0027)"
    )

    def dependencies(self) -> list[UUID]:
        amplitudes = [
            ref
            for p in self.physics
            for f in p.features
            for ref in (f.amplitude, f.axes)
            if ref is not None
        ]
        return [
            *(p.geometry for p in self.parts),
            self.mesh,
            *(m.material for m in self.materials),
            *amplitudes,
            *(c.material for c in self.connectors),
        ]

    def used_selections(self) -> list[str]:
        used = [a.selection for a in self.materials]
        used += [s for c in self.contacts for s in (c.source, c.target)]
        used += [f.selection for p in self.physics for f in p.features]
        used += [s for c in self.connectors for s in (c.source, c.target)]
        used += [b.selection for b in self.beam_sections]
        used += [s.selection for s in self.shell_sections]
        return used


# --- Studies, sweeps and transfers -------------------------------------------


class SolveStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["stationary", "transient", "frequency_domain", "modal", "buckling"]
    physics: list[str] = Field(
        default_factory=list, description="Physics solved in this step; empty means all"
    )
    settings: dict[str, Quantity | bool | int | float | str] = Field(
        default_factory=dict,
        description="e.g. time_step, end_time, time_scheme (backward_euler, crank_nicolson, "
        "bdf2, theta) and theta; modes; buckling (ADR 0028): modes, shift, and preload "
        "(true: the static step before it is held fixed, its loads not scaled)",
    )

    def solves(self, physics: str) -> bool:
        return not self.physics or physics in self.physics


class SweepParameter(BaseModel):
    """A parameter's values, or a material choice (ADR 0018): at `location` (a material
    assignment's selection in the study's component), each of `materials` in turn."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="A parameter defined in a parameter set, or a choice's label")
    values: list[Quantity] = Field(default_factory=list)
    location: str | None = Field(
        default=None, description="A material assignment's selection: this is a material choice"
    )
    materials: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def _values_or_materials(self) -> SweepParameter:
        if self.location is not None:
            if not self.materials or self.values:
                raise ValueError("a material choice lists materials (and no values)")
        elif not self.values or self.materials:
            raise ValueError("a swept parameter lists values (and no materials)")
        return self

    @property
    def key(self) -> str:
        """How a point names it: the parameter, or "material:<location>"."""
        return f"material:{self.location}" if self.location is not None else self.name

    def choices(self) -> list:
        return [str(m) for m in self.materials] if self.location is not None else self.values


class SweepStep(BaseModel):
    """Repeats every solve step after it once per parameter combination.

    `grid` runs every combination of the listed values; `paired` runs the i-th value
    of each parameter together and needs equal-length lists. Several sweep steps
    nest, outermost first.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["parametric_sweep"] = "parametric_sweep"
    parameters: list[SweepParameter] = Field(min_length=1)
    combination: Literal["grid", "paired"] = "grid"

    @model_validator(mode="after")
    def _paired_choice_lengths_match(self) -> SweepStep:
        if self.combination == "paired" and len({len(p.choices()) for p in self.parameters}) > 1:
            raise ValueError("paired sweep parameters need the same number of values")
        return self

    def points(self) -> list[dict[str, Quantity | str]]:
        """Each run's values: parameter name -> Quantity, "material:<location>" -> id."""
        names = [p.key for p in self.parameters]
        columns = [p.choices() for p in self.parameters]
        rows = (
            itertools.product(*columns)
            if self.combination == "grid"
            else zip(*columns, strict=True)
        )
        return [dict(zip(names, row, strict=True)) for row in rows]


Step = Annotated[SolveStep | SweepStep, Field(discriminator="type")]


class ComparisonMetric(BaseModel):
    """How a shoot-out compares one quantity between solvers."""

    model_config = ConfigDict(extra="forbid")

    quantity: str = Field(description="A result field, or 'eigenfrequencies' for modal steps")
    norm: Literal["max_abs_difference", "relative_l2", "relative_value"]
    tolerance: float = Field(gt=0)


class Shootout(BaseModel):
    """Runs the same study on further solvers and compares their results (ADR 0011).

    The primary solver's results stay the study's results; transfers always read
    them. Shoot-out runs only feed the comparison report.
    """

    model_config = ConfigDict(extra="forbid")

    solvers: list[str] = Field(min_length=1)
    metrics: list[ComparisonMetric] = Field(default_factory=list)


class Study(ProjectObject):
    kind: ClassVar[str] = "study"

    component: UUID
    solver: str
    steps: list[Step] = Field(default_factory=list)
    shootout: Shootout | None = None
    # ADR 0023: each solver's own settings ({solver: {key: value}}, from its adapter's
    # settings_schema) and expert pass-through ({solver: {path: value}}).
    solver_settings: dict[str, dict[str, float | int | str | bool]] = Field(default_factory=dict)
    expert_settings: dict[str, dict[str, str]] = Field(default_factory=dict)

    def all_solvers(self) -> list[str]:
        return [self.solver, *(self.shootout.solvers if self.shootout else [])]

    def dependencies(self) -> list[UUID]:
        return [self.component]

    def sweeps(self) -> list[SweepStep]:
        return [s for s in self.steps if isinstance(s, SweepStep)]

    def solve_steps(self) -> list[SolveStep]:
        return [s for s in self.steps if isinstance(s, SolveStep)]

    def swept_parameters(self) -> set[str]:
        """The project parameters the study's sweeps vary (material choices aside)."""
        return {p.name for sweep in self.sweeps() for p in sweep.parameters if p.location is None}

    def material_choices(self) -> list[SweepParameter]:
        return [p for sweep in self.sweeps() for p in sweep.parameters if p.location is not None]

    def sweep_points(self) -> list[dict[str, Quantity | str]]:
        """Every run this study makes: one empty point if it has no sweep."""
        points: list[dict[str, Quantity | str]] = [{}]
        for sweep in self.sweeps():
            points = [outer | inner for outer in points for inner in sweep.points()]
        return points


class Transfer(ProjectObject):
    """Passes a result field from one study into another: the only schematic edge.

    When the source study is swept over parameters the target doesn't sweep,
    `source_point` picks which run to take the field from.
    """

    kind: ClassVar[str] = "transfer"

    source: UUID
    field: str
    source_step: int = -1
    source_point: dict[str, Quantity] = Field(default_factory=dict)
    target: UUID
    target_step: int = 0
    role: Literal["load", "initial_condition", "prestress"]
    target_selection: str | None = None
    mapping: Literal["nearest", "interpolate", "conservative"] = "interpolate"

    def dependencies(self) -> list[UUID]:
        return [self.source]


class ResultCase(BaseModel):
    """One input of a derived result: a study's latest run (or one sweep point's), one of
    its result steps, scaled by a coefficient."""

    model_config = ConfigDict(extra="forbid")

    study: UUID
    point: str | None = Field(
        default=None, description='a swept study\'s point, as the results list it ("Copper")'
    )
    step: int = Field(default=-1, description="index into the run's result steps; -1 = last")
    coefficient: float = 1.0


class FatigueBlock(BaseModel):
    """One kind of cycle in a fatigue duty (ADR 0026), and how many of it per duty.

    `range`: between the two cases of a range combination (`combination`); amplitude =
    half the range, mean = the combination's mean. `fully_reversed`: `case` and its
    reverse (mean zero). `zero_based`: `case` and nothing (amplitude = mean = half)."""

    model_config = ConfigDict(extra="forbid")

    loading: Literal["range", "fully_reversed", "zero_based"] = "fully_reversed"
    combination: UUID | None = None
    case: ResultCase | None = None
    scale: float = 1.0
    cycles: float = Field(default=1.0, gt=0, description="cycles of this kind per duty")

    @model_validator(mode="after")
    def _source(self) -> FatigueBlock:
        if self.loading == "range" and self.combination is None:
            raise ValueError("a range loading names its range combination")
        if self.loading != "range" and self.case is None:
            raise ValueError(f"a {self.loading.replace('_', '-')} loading names its load case")
        return self


class FatigueSettings(BaseModel):
    """The fatigue tool's settings: Shigley's stress life (ch. 6) or strain life."""

    model_config = ConfigDict(extra="forbid")

    blocks: list[FatigueBlock] = Field(min_length=1)
    stress: Literal["signed_von_mises", "max_principal", "xx", "yy", "zz", "xy", "yz", "zx"] = (
        "signed_von_mises"
    )
    mean_stress: Literal[
        "goodman", "gerber", "asme_elliptic", "soderberg", "smith_dolan", "none"
    ] = "goodman"
    life_model: Literal["shigley", "sn_curve", "strain_life"] = "shigley"
    sn_curve: str | None = Field(default=None, description="the material's curve; None: its first")
    design_life: float = Field(default=1e6, gt=0, description="repetitions of the duty")
    # Shigley's Marin factors for the part (Eq. 6-18).
    surface: Literal["polished", "ground", "machined", "hot_rolled", "forged"] = "machined"
    size: Literal["rotating_round", "non_rotating_round", "rectangle", "axial", "none"] = "none"
    diameter: float | None = Field(default=None, description="m, for a round's size factor")
    height: float | None = Field(default=None, description="m, for a rectangle's")
    width: float | None = Field(default=None, description="m, for a rectangle's")
    temperature: float = Field(default=20.0, description="°C, for the temperature factor")
    reliability: float = Field(default=50.0, gt=0, lt=100, description="%")
    misc_factor: float = Field(default=1.0, gt=0, description="kf: plating, corrosion, ...")
    endurance_limit: float | None = Field(
        default=None, description="measured S'e in Pa; None: 0.5 Sut (steels)"
    )
    # The fatigue stress-concentration factor Kf (Shigley Sec. 6-10), for a stress raiser
    # the mesh doesn't resolve: 1 where it does (its stresses already carry Kt).
    notch_from: Literal["direct", "neuber"] = "direct"
    notch_factor: float = Field(default=1.0, ge=1.0, description="Kf, given directly")
    notch_kt: float | None = Field(default=None, ge=1.0, description="Kt, for Neuber")
    notch_radius: float | None = Field(default=None, gt=0, description="m, for Neuber")
    notch_shear: bool = Field(default=False, description="Neuber's shear (torsion) constant")
    notch_on_mean: bool = Field(
        default=False, description="Kf on the mean stress too (Shigley: alternating only)"
    )

    @model_validator(mode="after")
    def _notch(self) -> FatigueSettings:
        if self.notch_from == "neuber" and (self.notch_kt is None or self.notch_radius is None):
            raise ValueError("Neuber's notch sensitivity needs Kt and the notch radius")
        return self


class ResultDefinition(ProjectObject):
    """A derived result (ADR 0025), evaluated from stored runs when shown, never stored.

    Combinations (`linear`, `srss`, `envelope`, `range`) take `cases`. `envelope` keeps
    the largest (or smallest) of `quantity` (a listed result's key, "stress:von_mises")
    over them; `range` gives each field's range and mean. An `equation` evaluates
    `expression` over the results of `source` (a study, or another derived result),
    with `unit` the kind of unit its value has (model.units.DIMENSIONS), if any.
    """

    kind: ClassVar[str] = "result"

    method: Literal["linear", "srss", "envelope", "range", "equation", "fatigue"]
    cases: list[ResultCase] = Field(default_factory=list)
    quantity: str | None = None
    extreme: Literal["max", "min"] = "max"
    source: UUID | None = None
    source_point: str | None = None
    source_step: int = -1
    expression: str | None = None
    unit: str | None = None
    fatigue: FatigueSettings | None = None

    @model_validator(mode="after")
    def _complete(self) -> ResultDefinition:
        if self.method == "fatigue":
            if self.fatigue is None:
                raise ValueError("a fatigue result needs its settings")
        elif self.method == "equation":
            if self.source is None or not (self.expression or "").strip():
                raise ValueError("an equation needs a source and an expression")
        elif not self.cases:
            raise ValueError(f"a {self.method} combination needs at least one case")
        elif self.method == "envelope" and not self.quantity:
            raise ValueError("an envelope needs the quantity to compare")
        return self

    def dependencies(self) -> list[UUID]:
        own = [self.source] if self.source is not None else []
        if self.fatigue is not None:
            for b in self.fatigue.blocks:
                own += [b.combination] if b.combination else [b.case.study]
        return own + [c.study for c in self.cases]


class ResultProbe(ProjectObject):
    """A result tool (ADR 0032, 0042), evaluated for the current run of its source (a
    study, or a derived result) whenever it is shown; values are never stored.

    - `point`: the result at `point` (undeformed geometry, metres);
    - `selection`: the result over a selection, reduced by `reduction`;
    - `reaction`: the force (and moment about `about`, else the nodes' centroid) a
      selection's nodes take: the sum of the solver's nodal reaction forces;
    - `path`: the result at `samples` points from `point` to `end`.
    """

    kind: ClassVar[str] = "probe"

    source: UUID = Field(description="a study, or a derived result")
    type: Literal["point", "selection", "reaction", "path"] = "point"
    quantity: str = Field(
        default="reaction_force:total",
        description='a listed result\'s key, e.g. "stress:von_mises"',
    )
    point: tuple[float, float, float] | None = Field(
        default=None, description="point: where; path: the start"
    )
    selection: str | None = Field(default=None, description="Part/Selection, or a part")
    reduction: Literal["max", "min", "mean", "integral"] = "max"
    about: tuple[float, float, float] | None = Field(
        default=None, description="reaction: the moment's point; none: the nodes' centroid"
    )
    end: tuple[float, float, float] | None = Field(default=None, description="path: the end")
    samples: int = Field(default=50, ge=2, le=10000, description="path: points along it")
    step: int = Field(default=-1, description="index into the result steps; -1 = last")

    @model_validator(mode="after")
    def _needs(self) -> ResultProbe:
        need = {
            "point": ("point",),
            "selection": ("selection",),
            "reaction": ("selection",),
            "path": ("point", "end"),
        }[self.type]
        missing = [k for k in need if getattr(self, k) is None]
        if missing:
            raise ValueError(f"a {self.type} probe needs {', '.join(missing)}")
        if self.type == "reaction":
            self.quantity = "reaction_force:total"
        return self

    def dependencies(self) -> list[UUID]:
        return [self.source]


class ProjectUnits(ProjectObject):
    """The project's own display units (ADR 0030). With `use` on, everyone who opens the
    project sees them, over their own preference; off, each user's preference applies.
    Display only: values stay as typed, and solvers get SI (ADR 0024)."""

    kind: ClassVar[str] = "units"

    name: str = "Units"
    use: bool = Field(default=True, description="the project's units override the user's")
    system: str = Field(default="engineering", description="a model.units.SYSTEMS key")

    @field_validator("system")
    @classmethod
    def _known_system(cls, v: str) -> str:
        from puzzle.model.units import SYSTEMS

        if v not in SYSTEMS:
            raise ValueError(f"no unit system '{v}'; one of {', '.join(SYSTEMS)}")
        return v


class SettingsPreset(ProjectObject):
    """Solver settings saved to apply to any study (ADR 0023): "robust contact", "fast
    transient". The shared settings go on each solve step; each solver's own block and
    pass-through replace the study's for that solver."""

    kind: ClassVar[str] = "preset"

    shared: dict[str, float | int | str] = Field(default_factory=dict)
    solver_settings: dict[str, dict[str, float | int | str | bool]] = Field(default_factory=dict)
    expert_settings: dict[str, dict[str, str]] = Field(default_factory=dict)


OBJECT_TYPES: tuple[type[ProjectObject], ...] = (
    ParameterSet,
    Geometry,
    Material,
    MeshRecipe,
    Component,
    Study,
    Transfer,
    ResultDefinition,
    ProjectUnits,
    ResultProbe,
    Amplitude,
    CoordinateSystem,
    SettingsPreset,
)


T = TypeVar("T", bound=ProjectObject)


class Budget(BaseModel):
    """What an agent at "act_in_budget" may spend before it must ask (ADR 0015)."""

    model_config = ConfigDict(extra="forbid")

    runs: int = Field(default=5, ge=0, description="fresh solver runs")
    solver_seconds: float = Field(default=600.0, ge=0, description="total solver wall time")


class AgentSettings(BaseModel):
    """How much an AI agent may do in this project, and who may connect (ADR 0015).

    Changed only through the set_autonomy command; agents can never change it.
    """

    model_config = ConfigDict(extra="forbid")

    level: Literal["advise", "propose", "act_in_budget", "act"] = "propose"
    budget: Budget = Field(default_factory=Budget)
    mcp: Literal["off", "local", "network"] = Field(
        default="local", description="who may drive the project over MCP"
    )


@dataclass
class Project:
    """All objects in a project, keyed by ID. The model tree and schematic are views of this."""

    name: str
    schema_version: int = SCHEMA_VERSION
    objects: dict[UUID, ProjectObject] = field(default_factory=dict)
    agent: AgentSettings = field(default_factory=AgentSettings)

    def add(self, obj: T) -> T:
        if obj.id in self.objects:
            raise ValueError(f"duplicate object id {obj.id}")
        self.objects[obj.id] = obj
        return obj

    def of_type(self, cls: type[T]) -> list[T]:
        return [o for o in self.objects.values() if isinstance(o, cls)]

    def get(self, obj_id: UUID, cls: type[T]) -> T:
        obj = self.objects.get(obj_id)
        if not isinstance(obj, cls):
            raise KeyError(f"no {cls.kind} with id {obj_id}")
        return obj

    def units(self) -> ProjectUnits | None:
        """The project's own unit system, when it has one switched on (ADR 0030)."""
        found = [u for u in self.of_type(ProjectUnits) if u.use]
        return found[0] if found else None

    def transfers_into(self, study_id: UUID) -> list[Transfer]:
        return [t for t in self.of_type(Transfer) if t.target == study_id]

    def parameter_names(self) -> set[str]:
        return {name for ps in self.of_type(ParameterSet) for name in ps.values}

    def selection_names(self, comp: Component) -> set[str]:
        names = {s.name for s in comp.selections}
        names |= {part.name for part in comp.parts}  # a part's own group (a line body's)
        for part in comp.parts:
            geo = self.objects.get(part.geometry)
            if isinstance(geo, Geometry):
                names |= {f"{part.name}/{s.name}" for s in geo.selections}
        return names

    def copy_component(self, comp_id: UUID, name: str) -> Component:
        """Copy a component for use in another study.

        Local geometry is copied, selections included, with no link to the original.
        Global geometry, materials and the mesh recipe stay shared by reference.
        """
        original = self.get(comp_id, Component)
        copy = original.model_copy(deep=True, update={"id": uuid4(), "name": name})
        for part in copy.parts:
            geo = self.get(part.geometry, Geometry)
            if geo.scope == "local":
                local = geo.model_copy(deep=True, update={"id": uuid4(), "owner": copy.id})
                part.geometry = self.add(local).id
        return self.add(copy)

    def validate(self) -> list[str]:
        """Return human-readable problems; an empty list means the project is consistent."""
        return [p.message for p in self.problems()]

    def _unconstrained(self, study: Study) -> list:
        """A static or transient structural solve with nothing holding the body: the
        stiffness is singular, and CalculiX still returns a "result" (kilometres of
        rigid-body drift). Free modal analysis is fine, so modal steps are not flagged."""
        from puzzle.errors import Problem

        comp = self.objects.get(study.component)
        if not isinstance(comp, Component):
            return []
        solid = [p for p in comp.physics if p.type == "solid_mechanics"]
        holding = (
            "fixed",
            "displacement",
            "remote_displacement",
            "elastic_support",
            "compression_only_support",
        )
        held = any(f.type in holding for p in solid for f in p.features)
        steps = [
            s
            for s in study.solve_steps()
            if s.type in ("stationary", "transient")
            and (not s.physics or "solid_mechanics" in s.physics)
        ]
        if not solid or held or not steps:
            return []
        return [
            Problem(
                "unconstrained",
                study.name,
                f"study '{study.name}' solves solid mechanics on '{comp.name}', which has no "
                "fixed or displacement condition: the body is free to move as a rigid body",
                {"component": comp.name},
            )
        ]

    def _incomplete_materials(self, study, comp) -> list[Problem]:
        """Materials lacking a property the study needs (ADR 0020), said before a run."""
        from puzzle.errors import Problem
        from puzzle.materials.registry import label, missing, needs

        wanted = needs(comp, study, self.transfers_into(study.id))
        out = []
        for assignment in comp.materials:
            material = self.objects.get(assignment.material)
            if not isinstance(material, Material):
                continue
            for physics, keys in wanted.items():
                lacking = missing(material, keys)
                if lacking:
                    words = ", ".join(label(k).lower() for k in lacking)
                    out.append(
                        Problem(
                            "incomplete_material",
                            study.name,
                            f"study '{study.name}': material '{material.name}' has no {words}, "
                            f"needed for {physics.replace('_', ' ')}",
                            {"material": material.name, "missing": lacking, "physics": physics},
                        )
                    )
        return out

    def problems(self) -> list[Problem]:
        """Consistency problems, each with a code (see puzzle.errors.Problem)."""
        from puzzle.errors import Problem

        out: list[Problem] = []
        for obj in self.objects.values():
            out += [
                Problem(
                    "missing_reference",
                    obj.name,
                    f"{obj.kind} '{obj.name}' refers to missing object {dep}",
                    {"id": str(dep)},
                )
                for dep in obj.dependencies()
                if dep not in self.objects
            ]

        for comp in self.of_type(Component):
            names = self.selection_names(comp)
            out += [
                Problem(
                    "unknown_selection",
                    comp.name,
                    f"component '{comp.name}' uses unknown selection '{s}'",
                    {"selection": s},
                )
                for s in comp.used_selections()
                if s not in names
            ]
            for part in comp.parts:
                geo = self.objects.get(part.geometry)
                if isinstance(geo, Geometry) and geo.scope == "local" and geo.owner != comp.id:
                    out.append(
                        Problem(
                            "foreign_local_geometry",
                            comp.name,
                            f"component '{comp.name}' uses geometry '{geo.name}', "
                            "which is local to another component",
                        )
                    )

        known = self.parameter_names()
        for study in self.of_type(Study):
            out += self._unconstrained(study)
            seen_solve = False
            for step in study.steps:
                if isinstance(step, SolveStep):
                    seen_solve = True
                elif seen_solve:
                    out.append(
                        Problem(
                            "sweep_order",
                            study.name,
                            f"study '{study.name}': sweep steps must come first",
                        )
                    )
            out += [
                Problem(
                    "unknown_parameter",
                    study.name,
                    f"study '{study.name}' sweeps unknown parameter '{p}'",
                    {"parameter": p},
                )
                for p in sorted(study.swept_parameters() - known)
            ]
            comp = self.objects.get(study.component)
            if isinstance(comp, Component):
                present = {ph.type for ph in comp.physics}
                out += [
                    Problem(
                        "missing_physics",
                        study.name,
                        f"study '{study.name}' solves {ph}, which component '{comp.name}' lacks",
                        {"physics": ph},
                    )
                    for step in study.solve_steps()
                    for ph in step.physics
                    if ph not in present
                ]
                out += self._incomplete_materials(study, comp)
            solvers = study.all_solvers()
            if len(set(solvers)) != len(solvers):
                out.append(
                    Problem(
                        "duplicate_solver",
                        study.name,
                        f"study '{study.name}': shoot-out solvers must differ",
                    )
                )

        for t in self.of_type(Transfer):
            source = self.objects.get(t.source)
            target = self.objects.get(t.target)
            if not isinstance(target, Study):
                out.append(
                    Problem(
                        "missing_reference",
                        t.name,
                        f"transfer '{t.name}' targets missing study {t.target}",
                        {"id": str(t.target)},
                    )
                )
                continue
            if isinstance(source, Study):
                unpinned = (
                    source.swept_parameters() - target.swept_parameters() - set(t.source_point)
                )
                out += [
                    Problem(
                        "unpinned_transfer",
                        t.name,
                        f"transfer '{t.name}' needs source_point for swept parameter '{p}'",
                        {"parameter": p},
                    )
                    for p in sorted(unpinned)
                ]
        return out
