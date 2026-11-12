"""A light weld's strength by AISC 360-16 Chapter J2 (ADR 0078 step 2), from its line
force at each station (`post/weld_fields`).

- Effective throat (J2.2a): a fillet's shortest distance from root to face, 0.707 w for
  equal legs, l1 l2 / √(l1² + l2²) for unequal; a PJP groove's depth of groove as given
  (Table J2.1's reductions for the process and groove angle are the user's to apply).
- Nominal strength per length (J2.4): R_n / l = F_nw t_e, F_nw = 0.60 F_EXX (Eq. J2-3,
  Table J2.5); with `directional`, F_nw = 0.60 F_EXX (1.0 + 0.50 sin^1.5 θ) (Eq. J2-5),
  θ the angle between the line force and the weld's axis, which J2.4(b) allows for
  linear welds and groups loaded in plane through their centre.
- LRFD φ = 0.75 and ASD Ω = 2.00: the utilisation is the line force over φ R_n / l, or
  over (R_n / l) / Ω, for loads factored for that method.
- F_EXX from the weld's filler (a material with a tensile strength), else the base
  metal's matching filler (`matching`), for carbon and low-alloy steel only: stainless
  welds are AISC 370's and aluminium ones the Aluminum Design Manual's, not built yet.
- Not checked here: a CJP groove (its base metal governs, J2.5), the base metal along
  the weld (J4), and Table J2.4's minimum sizes (they need the plates' thicknesses).
"""

from __future__ import annotations

import math

import numpy as np

PHI, OMEGA = 0.75, 2.00
# The matching filler for a base metal's group (AWS D1.1 Table 5.4's E70 for the common
# structural steels): its classification strength F_EXX.
MATCHING = {"Carbon Steel": ("E70XX", 482.6e6), "Low Alloy Steel": ("E70XX", 482.6e6)}
STAINLESS, ALUMINIUM = ("Stainless Steel",), ("Aluminium", "Aluminum")


def throat(weld) -> float:
    """The effective throat t_e (m)."""
    a = weld.leg.to_si()
    if weld.type == "groove":
        return a
    b = weld.leg2.to_si() if weld.leg2 is not None else a
    return a * b / math.hypot(a, b)


def directional_factor(along: np.ndarray, across: np.ndarray) -> np.ndarray:
    """1.0 + 0.50 sin^1.5 θ, θ between the line force and the weld's axis (Eq. J2-5)."""
    theta = np.arctan2(np.abs(across), np.abs(along))
    return 1.0 + 0.5 * np.sin(theta) ** 1.5


def filler(project, comp, weld) -> tuple[str, float, str]:
    """(name, F_EXX in Pa, code) of the weld's filler, or a ValueError saying why none."""
    from puzzle.model.schema import Material

    if weld.filler is not None:
        m = project.objects.get(weld.filler)
        if not isinstance(m, Material) or "tensile_strength" not in m.properties:
            raise ValueError(f"weld '{weld.name}': its filler has no tensile strength (F_EXX)")
        return m.name, m.properties["tensile_strength"].to_si(), "AISC 360-16 J2"
    groups = set()
    for face in weld.faces:
        part = face.split("/", 1)[0]
        for a in comp.materials:
            if a.selection.split("/", 1)[0] == part:
                m = project.objects.get(a.material)
                groups.add((getattr(m, "info", None) or {}).get("group", "") or "")
    if groups & set(STAINLESS):
        raise ValueError(f"weld '{weld.name}' joins stainless steel: AISC 370's weld "
                         "provisions are not built yet")  # fmt: skip
    if groups & set(ALUMINIUM):
        raise ValueError(f"weld '{weld.name}' joins aluminium: the Aluminum Design Manual's "
                         "weld provisions are not built yet")  # fmt: skip
    for g in groups:
        if g in MATCHING:
            name, fexx = MATCHING[g]
            return name, fexx, "AISC 360-16 J2"
    raise ValueError(f"weld '{weld.name}': no filler given and no matching one for its base "
                     f"metal ({', '.join(sorted(g for g in groups if g)) or 'none'})")  # fmt: skip


def check(weld, fexx: float, along, across, magnitude, directional: bool = False) -> dict:
    """The J2 check at each station: capacity per length (N/m) for LRFD and ASD, and the
    utilisations; the governing station's index."""
    if weld.type == "groove" and weld.groove == "cjp":
        return {"checked": False, "why": "a CJP groove weld's strength is its base metal's "
                "(AISC 360 J2.5, Table J2.5): check the base metal"}  # fmt: skip
    t_e = throat(weld)
    factor = directional_factor(np.asarray(along), np.asarray(across)) if directional else 1.0
    nominal = 0.60 * fexx * factor * t_e * np.ones(len(magnitude))
    q = np.abs(np.asarray(magnitude, float))
    lrfd, asd = q / (PHI * nominal), q / (nominal / OMEGA)
    at = int(np.argmax(lrfd))
    return {
        "checked": True,
        "throat_m": t_e,
        "nominal_N_per_m": nominal,
        "utilisation_lrfd": lrfd,
        "utilisation_asd": asd,
        "governing": at,
        "clause": "AISC 360-16 J2.4, Eq. J2-5" if directional else "AISC 360-16 J2.4, Eq. J2-3",
    }
