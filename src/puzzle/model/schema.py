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


class MeshRecipe(ProjectObject):
    kind: ClassVar[str] = "mesh"

    mesher: Literal["gmsh", "netgen"] = "gmsh"
    element_order: Literal[1, 2] = 2
    element_shape: Literal["tetrahedra", "hexahedra"] = Field(
        default="tetrahedra",
        description="hexahedra: structured (transfinite) hex mesh of block-like volumes",
    )
    global_size: Quantity | None = None
    local_sizes: dict[str, Quantity] = Field(
        default_factory=dict, description="Named selection -> element size"
    )


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

    def dependencies(self) -> list[UUID]:
        return [*(p.geometry for p in self.parts), self.mesh, *(m.material for m in self.materials)]

    def used_selections(self) -> list[str]:
        used = [a.selection for a in self.materials]
        used += [s for c in self.contacts for s in (c.source, c.target)]
        used += [f.selection for p in self.physics for f in p.features]
        return used


# --- Studies, sweeps and transfers -------------------------------------------


class SolveStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["stationary", "transient", "frequency_domain", "modal"]
    physics: list[str] = Field(
        default_factory=list, description="Physics solved in this step; empty means all"
    )
    settings: dict[str, Quantity | bool | int | float | str] = Field(
        default_factory=dict,
        description="e.g. time_step, end_time, time_scheme (backward_euler, crank_nicolson, "
        "bdf2, theta) and theta; modes",
    )

    def solves(self, physics: str) -> bool:
        return not self.physics or physics in self.physics


class SweepParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="A parameter defined in a parameter set")
    values: list[Quantity] = Field(min_length=1)


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
    def _paired_lengths_match(self) -> SweepStep:
        if self.combination == "paired" and len({len(p.values) for p in self.parameters}) > 1:
            raise ValueError("paired sweep parameters need the same number of values")
        return self

    def points(self) -> list[dict[str, Quantity]]:
        names = [p.name for p in self.parameters]
        columns = [p.values for p in self.parameters]
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

    def all_solvers(self) -> list[str]:
        return [self.solver, *(self.shootout.solvers if self.shootout else [])]

    def dependencies(self) -> list[UUID]:
        return [self.component]

    def sweeps(self) -> list[SweepStep]:
        return [s for s in self.steps if isinstance(s, SweepStep)]

    def solve_steps(self) -> list[SolveStep]:
        return [s for s in self.steps if isinstance(s, SolveStep)]

    def swept_parameters(self) -> set[str]:
        return {p.name for sweep in self.sweeps() for p in sweep.parameters}

    def sweep_points(self) -> list[dict[str, Quantity]]:
        """Every run this study makes: one empty point if it has no sweep."""
        points: list[dict[str, Quantity]] = [{}]
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


OBJECT_TYPES: tuple[type[ProjectObject], ...] = (
    ParameterSet,
    Geometry,
    Material,
    MeshRecipe,
    Component,
    Study,
    Transfer,
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

    def transfers_into(self, study_id: UUID) -> list[Transfer]:
        return [t for t in self.of_type(Transfer) if t.target == study_id]

    def parameter_names(self) -> set[str]:
        return {name for ps in self.of_type(ParameterSet) for name in ps.values}

    def selection_names(self, comp: Component) -> set[str]:
        names = {s.name for s in comp.selections}
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
        held = any(f.type in ("fixed", "displacement") for p in solid for f in p.features)
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
