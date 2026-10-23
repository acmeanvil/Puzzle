import os
import sys
from pathlib import Path

# Tests use Puzzle's generic materials only, never a user's local library.
os.environ["PUZZLE_MATERIALS"] = ""
# Tests never touch the real keychain (or pop its prompts); key tests install their own.
os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
# Nor the user's own settings (settings.toml, agent.toml): a scratch user folder.
import tempfile  # noqa: E402

os.environ["PUZZLE_USER_DIR"] = tempfile.mkdtemp(prefix="puzzle-user-")

import pytest

from puzzle.model import (
    Component,
    Contact,
    Feature,
    Geometry,
    Material,
    MaterialAssignment,
    MeshRecipe,
    NamedSelection,
    ParameterSet,
    PartInstance,
    Physics,
    Project,
    Quantity,
    SolveStep,
    Study,
    Transfer,
)


@pytest.fixture
def bracket() -> Project:
    """The heated-bracket example from ADR 0009: thermal -> structural -> modal.

    The bracket geometry is global, so its selections are shared by every user.
    """
    p = Project(name="Bracket assessment")
    p.add(ParameterSet(name="Globals", values={"fillet_r": Quantity(value=2, unit="mm")}))
    geo = p.add(
        Geometry(
            name="Bracket",
            source="bracket.step",
            selections=[
                NamedSelection(name="All", entity="body", members=["body-1"]),
                NamedSelection(name="Bolt holes", entity="face", members=["hole-1", "hole-2"]),
                NamedSelection(name="Heated face", entity="face", members=["top"]),
            ],
        )
    )
    al = p.add(
        Material(
            name="Al 6061-T6",
            properties={
                "youngs_modulus": Quantity(value=68.9, unit="GPa"),
                "poissons_ratio": Quantity(value=0.33),
                "density": Quantity(value=2700, unit="kg/m^3"),
                "thermal_expansion": Quantity(value=23.6e-6, unit="1/K"),
                "thermal_conductivity": Quantity(value=167, unit="W/(m*K)"),
                "specific_heat": Quantity(value=896, unit="J/(kg*K)"),
                "relative_permeability": Quantity(value=1.0),
                "electrical_conductivity": Quantity(value=2.5e7, unit="S/m"),
            },
        )
    )
    mesh = p.add(MeshRecipe(name="Tets", global_size=Quantity(value=3, unit="mm")))
    comp = p.add(
        Component(
            name="Bracket",
            parts=[PartInstance(name="Bracket", geometry=geo.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=al.id, selection="Bracket/All")],
            physics=[
                Physics(
                    type="heat_transfer",
                    features=[
                        Feature(
                            type="temperature",
                            selection="Bracket/Heated face",
                            values={"T": Quantity(value=120, unit="degC")},
                        )
                    ],
                ),
                Physics(
                    type="solid_mechanics",
                    features=[Feature(type="fixed", selection="Bracket/Bolt holes")],
                ),
            ],
        )
    )
    thermal = p.add(
        Study(
            name="Thermal",
            component=comp.id,
            solver="elmer",
            steps=[SolveStep(type="stationary")],
        )
    )
    static = p.add(
        Study(
            name="Static",
            component=comp.id,
            solver="calculix",
            steps=[SolveStep(type="stationary")],
        )
    )
    modal = p.add(
        Study(name="Modal", component=comp.id, solver="calculix", steps=[SolveStep(type="modal")])
    )
    p.add(
        Transfer(
            name="Temperature",
            source=thermal.id,
            field="temperature",
            target=static.id,
            role="load",
        )
    )
    p.add(
        Transfer(
            name="Prestress", source=static.id, field="stress", target=modal.id, role="prestress"
        )
    )
    return p


@pytest.fixture
def assembly(bracket: Project) -> Project:
    """The bracket bolted to a local base plate, with frictional contact between them."""
    p = bracket
    mesh = p.of_type(MeshRecipe)[0]
    al = p.of_type(Material)[0]
    bracket_geo = p.of_type(Geometry)[0]
    comp = Component(
        name="Bracket on plate",
        parts=[PartInstance(name="Bracket", geometry=bracket_geo.id)],
        mesh=mesh.id,
        selections=[NamedSelection(name="Everything", entity="body", members=["*"])],
        materials=[MaterialAssignment(material=al.id, selection="Everything")],
    )
    plate = p.add(
        Geometry(
            name="Plate",
            source="plate.step",
            scope="local",
            owner=comp.id,
            selections=[
                NamedSelection(name="Top", entity="face", members=["top"]),
                NamedSelection(name="Underside", entity="face", members=["bottom"]),
            ],
        )
    )
    comp.parts.append(PartInstance(name="Plate", geometry=plate.id, translation=(0, 0, -10)))
    comp.contacts.append(
        Contact(
            name="Bracket on plate",
            type="frictional",
            source="Bracket/Bolt holes",
            target="Plate/Top",
            friction=0.3,
        )
    )
    comp.physics.append(
        Physics(
            type="solid_mechanics", features=[Feature(type="fixed", selection="Plate/Underside")]
        )
    )
    p.add(comp)
    p.add(
        Study(
            name="Assembly static",
            component=comp.id,
            solver="calculix",
            steps=[SolveStep(type="stationary")],
        )
    )
    return p


@pytest.fixture
def induction(bracket: Project) -> Project:
    """Axisymmetric induction heating on Elmer: harmonic magnetics, then stationary heat."""
    p = bracket
    comp = Component(
        name="Billet and coil",
        dimension="2d_axisymmetric",
        parts=[PartInstance(name="Bracket", geometry=p.of_type(Geometry)[0].id)],
        mesh=p.of_type(MeshRecipe)[0].id,
        physics=[
            Physics(
                type="magnetic_fields",
                features=[
                    Feature(
                        type="coil_current",
                        selection="Bracket/All",
                        values={"I": Quantity(value=500, unit="A")},
                    )
                ],
            ),
            Physics(type="heat_transfer"),
        ],
        couplings=["joule_heating"],
    )
    p.add(comp)
    p.add(
        Study(
            name="Induction heating",
            component=comp.id,
            solver="elmer",
            steps=[
                SolveStep(
                    type="frequency_domain",
                    physics=["magnetic_fields"],
                    settings={"frequency": Quantity(value=10, unit="kHz")},
                ),
                SolveStep(type="stationary", physics=["heat_transfer"]),
            ],
        )
    )
    return p


# Shared geometry helpers live in tests/conftest_geometry.py; make them importable.
sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(autouse=True)
def _fresh_settings():
    """Every test starts from the default preferences (ADR 0024) and no materials of the
    user's own (ADR 0020), and leaves none behind."""
    import puzzle.settings as settings
    from puzzle.materials import custom_library_path, reload_library

    def clean():
        settings.settings_path().unlink(missing_ok=True)
        custom = custom_library_path()
        custom.unlink(missing_ok=True)
        custom.with_name(custom.stem + "-history.jsonl").unlink(missing_ok=True)
        settings._current = None
        settings._project_system = None
        reload_library()

    clean()
    yield
    clean()


@pytest.fixture
def engineering_units():
    """Lengths in mm, stresses in MPa: what tests that type "30" for 30 mm assume."""
    import puzzle.settings as settings

    s = settings.Settings()
    s.set("units.system", "engineering")
    s.save()
    settings.reload()
    return s
