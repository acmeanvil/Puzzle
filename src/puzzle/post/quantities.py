"""Every result a run can show, derived from the fields it stored.

A run stores fields (displacement, stress, temperature, ...); people ask for results:
total deformation, deformation along X, equivalent (von Mises) stress, the principal
stresses, a heat flux component. `catalog(fields)` lists them, grouped by field, with
readable names and the kind of unit they are in; `compute(item, values)` evaluates one
from the field's values.

Tensors are stored as xx, yy, zz, xy, yz, zx (ADR 0008). Strains are tensor strains
(the shear components are half the engineering shear), so the equivalent strain is
sqrt(2/3 e_dev : e_dev).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Field -> (group name, quantity names: (total, component prefix), unit kind).
VECTORS = {
    "displacement": ("Deformation", ("Total deformation", "Deformation"), "length"),
    # A harmonic response (ADR 0041): each component's amplitude, and its phase in degrees.
    "displacement_amplitude": (
        "Deformation amplitude",
        ("Deformation amplitude (all components)", "Deformation amplitude"),
        "length",
    ),
    "displacement_phase": ("Deformation phase", ("Phase (vector of angles)", "Phase, °"), None),
    "velocity": ("Velocity", ("Velocity magnitude", "Velocity"), "velocity"),
    "heat_flux": ("Heat flux", ("Total heat flux", "Heat flux"), "heat_flux"),
    "reaction_force": ("Reaction force", ("Reaction force magnitude", "Reaction force"), "force"),
    "electric_field": ("Electric field", ("Electric field magnitude", "Electric field"), None),
    "magnetic_flux_density": (
        "Magnetic flux density",
        ("Magnetic flux density magnitude", "Magnetic flux density"),
        None,
    ),
    "current_density": ("Current density", ("Current density magnitude", "Current density"), None),
}
TENSORS = {
    "stress": ("Stress", "stress", "pressure"),
    "strain": ("Strain", "strain", None),
    "plastic_strain": ("Plastic strain", "plastic strain", None),  # directional plastic strain
}
SCALARS = {
    "plastic_strain_equivalent": ("Equivalent plastic strain", None),
    "temperature": ("Temperature", "temperature"),
    "pressure": ("Pressure", "pressure"),
    "electric_potential": ("Electric potential", None),
}
AXES = {"x": "X", "y": "Y", "z": "Z"}
SUFFIXES = {"range": "range", "mean": "mean"}  # fields a range combination makes
PAIRS = {"xx": "X", "yy": "Y", "zz": "Z", "xy": "XY", "yz": "YZ", "zx": "ZX"}


@dataclass(frozen=True)
class Item:
    """One result to show: `key` is "field:what" (what: total, a component, von_mises, ...)."""

    key: str
    label: str
    field: str
    what: str
    group: str
    unit: str | None  # the kind of unit (units.DIMENSIONS), None if shown as stored

    @property
    def components(self) -> bool:
        return self.what not in ("total", "value", "von_mises", "equivalent", "max_shear")


def catalog(fields: list[tuple[str, str, list[str]]]) -> list[Item]:
    """The results a run can show: `fields` are (name, location, components) as stored.
    Whole-model (global) values are not drawn on the mesh and are left out."""
    items: list[Item] = []
    for name, location, comps in fields:
        if location == "global":
            continue
        base, _, word = name.rpartition("_")
        if word in SUFFIXES and base:  # a combination's range or mean (ADR 0025)
            for i in catalog([(base, location, comps)]):
                text = f"{i.label} ({SUFFIXES[word]})"
                group = f"{i.group} {SUFFIXES[word]}"
                items.append(Item(f"{name}:{i.what}", text, name, i.what, group, i.unit))
            continue
        if name in VECTORS or (len(comps) == 3 and comps == ["x", "y", "z"]):
            group, (total, prefix), unit = VECTORS.get(
                name, (words(name), (f"{words(name)} magnitude", words(name)), None)
            )
            items.append(Item(f"{name}:total", total, name, "total", group, unit))
            items += [
                Item(f"{name}:{c}", f"{prefix} {AXES[c]}", name, c, group, unit) for c in comps
            ]
        elif len(comps) == 6:
            group, word, unit = TENSORS.get(name, (words(name), name.replace("_", " "), None))
            equivalent = (
                ("von_mises", "Equivalent (von Mises) stress")
                if name == "stress"
                else ("equivalent", f"Equivalent {word}")
            )
            items.append(
                Item(f"{name}:{equivalent[0]}", equivalent[1], name, equivalent[0], group, unit)
            )
            for what, text in (
                ("p1", "Maximum principal"),
                ("p2", "Middle principal"),
                ("p3", "Minimum principal"),
            ):
                items.append(Item(f"{name}:{what}", f"{text} {word}", name, what, group, unit))
            if name == "stress":
                items.append(
                    Item("stress:max_shear", "Maximum shear stress", name, "max_shear", group, unit)
                )
            for c in comps:
                kind = "Normal" if c in ("xx", "yy", "zz") else "Shear"
                items.append(
                    Item(f"{name}:{c}", f"{kind} {word} {PAIRS.get(c, c)}", name, c, group, unit)
                )
        else:
            group, unit = SCALARS.get(name, (words(name), None))
            items.append(Item(f"{name}:value", group, name, "value", group, unit))
    return items


def label_of(key: str) -> str:
    """A listed result's name from its key alone ("stress:von_mises" -> "Equivalent (von
    Mises) stress"), for places that have no run at hand (the tree's probes)."""
    field = key.split(":")[0]
    base = field.removesuffix("_range").removesuffix("_mean")
    if base in VECTORS:
        comps = ["x", "y", "z"]
    elif base in TENSORS:
        comps = ["xx", "yy", "zz", "xy", "yz", "zx"]
    else:
        comps = [""]
    found = next((i for i in catalog([(field, "node", comps)]) if i.key == key), None)
    return found.label if found is not None else words(field)


def words(name: str) -> str:
    """A field name in words: "electric_potential" -> "Electric potential"."""
    text = name.replace("_", " ")
    return text[:1].upper() + text[1:]


def default(items: list[Item]) -> Item | None:
    """What a run shows first: deformation, else temperature, else velocity, else the first."""
    for key in ("displacement:total", "temperature:value", "velocity:total", "stress:von_mises"):
        hit = next((i for i in items if i.key == key), None)
        if hit:
            return hit
    return items[0] if items else None


def compute(item: Item, values: np.ndarray, components: list[str]) -> np.ndarray:
    """The item's value at each node or element, from the field's (n, c) values (SI)."""
    v = np.asarray(values, float)
    if v.ndim == 1:
        v = v[:, None]
    if item.what == "value":
        return v[:, 0]
    if item.what in components:
        return v[:, components.index(item.what)]
    if item.what == "total":
        return np.linalg.norm(v, axis=1)
    xx, yy, zz, xy, yz, zx = (v[:, components.index(c)] for c in PAIRS)
    if item.what == "von_mises":
        return np.sqrt(
            0.5 * ((xx - yy) ** 2 + (yy - zz) ** 2 + (zz - xx) ** 2) + 3 * (xy**2 + yz**2 + zx**2)
        )
    tensors = np.stack(
        [np.stack([xx, xy, zx], -1), np.stack([xy, yy, yz], -1), np.stack([zx, yz, zz], -1)], -2
    )
    finite = np.isfinite(tensors).all(axis=(1, 2))
    principal = np.full((len(v), 3), np.nan)
    principal[finite] = np.linalg.eigvalsh(tensors[finite])[:, ::-1]  # largest first
    if item.what in ("p1", "p2", "p3"):
        return principal[:, int(item.what[1]) - 1]
    if item.what == "max_shear":
        return 0.5 * (principal[:, 0] - principal[:, 2])
    if item.what == "equivalent":
        mean = (xx + yy + zz) / 3
        dev = [xx - mean, yy - mean, zz - mean]
        inner = sum(d**2 for d in dev) + 2 * (xy**2 + yz**2 + zx**2)
        return np.sqrt(2.0 / 3.0 * inner)
    raise KeyError(f"no result '{item.what}' for {item.field}")
