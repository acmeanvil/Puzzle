"""The fatigue tool (ADR 0026): life, damage and safety factor at every node, like the
Ansys Fatigue Tool, by the formulas of `materials.fatigue` (Tension-meter's).

Each block of the duty gives an amplitude and a mean stress tensor at each node:

- `range`: half a range combination's `stress_range`, and its `stress_mean`;
- `fully_reversed`: the case's stress, mean zero;
- `zero_based`: half the case's stress, as amplitude and as mean.

The stress measure turns tensors into numbers: signed von Mises (von Mises, signed by
the principal stress of largest magnitude, so compression doesn't count as tension), the
maximum principal, or a component. The mean-stress correction gives the equivalent
completely reversed stress; the life model its cycles to failure, per material (each
node takes the material assigned where it lies). Miner's rule sums the blocks:

- life = repetitions of the duty to failure (cycles, for one block of one cycle);
- damage = design life / life;
- safety factor = the factor on every stress that makes the life the design life
  (capped at 15, as Ansys does).

High-cycle and proportional loading only: where a range's and its mean's principal
directions differ at the critical node, the result says so.
"""

from __future__ import annotations

import numpy as np

from puzzle.errors import PuzzleError
from puzzle.materials import fatigue as fm
from puzzle.post.quantities import Item, compute
from puzzle.results.model import Field, Results, StepResult

TENSOR = ["xx", "yy", "zz", "xy", "yz", "zx"]
SAFETY_CAP = 15.0
LIFE_CAP = 1e12  # shown for infinite life
STRESS_LABELS = {
    "signed_von_mises": "Signed von Mises",
    "max_principal": "Maximum principal",
    **{c: f"Stress {c.upper()}" for c in TENSOR},
}
MODEL_LABELS = {
    "shigley": "Shigley's S-N line (from Sut)",
    "sn_curve": "The material's S-N curve",
    "strain_life": "The material's strain-life model",
}


def _item(what: str) -> Item:
    return Item(f"stress:{what}", what, "stress", what, "Stress", "pressure")


def _principal(v: np.ndarray) -> np.ndarray:
    return np.stack([compute(_item(p), v, TENSOR) for p in ("p1", "p2", "p3")], axis=1)


def measure(tensors: np.ndarray, how: str, signed: bool) -> np.ndarray:
    """The stress measure of (n, 6) tensors; `signed` keeps the sign (means), else the
    magnitude (amplitudes)."""
    if how in TENSOR:
        v = tensors[:, TENSOR.index(how)]
    elif how == "max_principal":
        p = _principal(tensors)
        v = p[:, 0] if signed else np.max(np.abs(p), axis=1)
    else:
        vm = compute(_item("von_mises"), tensors, TENSOR)
        p = _principal(tensors)
        largest = p[np.arange(len(p)), np.argmax(np.abs(p), axis=1)]
        v = np.where(largest < 0, -vm, vm)
    return v if signed else np.abs(v)


def _equivalent_strain(tensors: np.ndarray) -> np.ndarray:
    item = Item("strain:equivalent", "", "strain", "equivalent", "Strain", None)
    return compute(item, tensors, TENSOR)


class _Material:
    """What the life model needs from one material, in SI."""

    def __init__(self, material, settings) -> None:
        self.name = material.name
        p = material.properties

        def si(key):
            return p[key].to_si() if key in p else None

        self.sut, self.sy, self.e = (
            si("tensile_strength"),
            si("yield_strength"),
            si("youngs_modulus"),
        )
        need = []
        if (
            settings.mean_stress in ("goodman", "gerber", "smith_dolan")
            or settings.life_model == "shigley"
        ):
            need += [] if self.sut else ["tensile strength"]
        if settings.mean_stress in ("asme_elliptic", "soderberg"):
            need += [] if self.sy else ["yield strength"]
        if need:
            raise PuzzleError(
                f"{material.name}: the fatigue tool needs its {' and '.join(need)} "
                "(Material builder, Structural)",
                code="incomplete_material",
            )
        self.line = self.curve = self.strain = None
        if settings.life_model == "shigley":
            try:
                e = fm.endurance_limit(
                    self.sut,
                    measured_se_prime=settings.endurance_limit,
                    surface=settings.surface,
                    size=settings.size,
                    d=settings.diameter,
                    h=settings.height,
                    b=settings.width,
                    temperature_c=settings.temperature,
                    reliability=settings.reliability,
                    kf=settings.misc_factor,
                )
            except ValueError as exc:
                raise PuzzleError(f"{material.name}: {exc}", code="invalid") from exc
            self.endurance = e
            self.line = fm.SnLine(e.se, self.sut)
        elif settings.life_model == "sn_curve":
            curves = material.fatigue.sn_curves
            pick = [c for c in curves if settings.sn_curve in (None, c.name)]
            if not pick:
                what = (
                    f"an S-N curve named '{settings.sn_curve}'"
                    if settings.sn_curve
                    else "an S-N curve"
                )
                raise PuzzleError(
                    f"{material.name} has no {what.removeprefix('an ')}: add one in the "
                    "Material builder's Fatigue tab",
                    code="incomplete_material",
                )
            self.curve = pick[0]
        else:
            sl = material.fatigue.strain_life
            if sl is None:
                raise PuzzleError(
                    f"{material.name} has no strain-life model: add one in the Material "
                    "builder's Fatigue tab",
                    code="incomplete_material",
                )
            if sl.model == "coffin_manson_basquin" and not self.e:
                raise PuzzleError(
                    f"{material.name}: Coffin-Manson-Basquin needs Young's modulus",
                    code="incomplete_material",
                )
            self.strain = fm.StrainLife(
                sl.model,
                sl.fatigue_strength,
                sl.strength_exponent,
                sl.fatigue_ductility,
                sl.ductility_exponent,
                self.e,
                sl.langer_a,
                sl.langer_b,
                sl.langer_c,
            )

    def life(self, settings, sa, sm, ea):
        """Cycles to failure of each node's cycle (inf: infinite)."""
        if self.strain is not None:
            return self.strain.cycles(ea)
        rev = fm.reversed_stress(
            settings.mean_stress, sa, sm, self.sut or np.nan, self.sy or np.nan
        )
        if self.line is not None:
            return np.where(np.isinf(rev), 0.0, self.line.cycles(np.where(np.isinf(rev), 0, rev)))
        n = fm.cycles_on_curve(self.curve.points, np.where(np.isinf(rev), 1e300, rev))
        return np.where(np.isinf(rev), 0.0, n)


def _materials(project, mesh, study_id, settings) -> tuple[list[_Material], np.ndarray, list[str]]:
    """The materials and, per mesh node, which one is there (by the component's
    assignments: each node takes the material of a body selection it lies in)."""
    from puzzle.model.schema import Component, Material, Study

    study = project.get(study_id, Study)
    comp = project.get(study.component, Component)
    mats: list[_Material] = []
    which = np.full(len(mesh.node_ids), -1)
    order = np.argsort(mesh.node_ids)
    notes = []
    for a in comp.materials:
        material = project.get(a.material, Material)
        idx = next((i for i, m in enumerate(mats) if m.name == material.name), None)
        if idx is None:
            mats.append(_Material(material, settings))
            idx = len(mats) - 1
        group = mesh.groups.get(a.selection)
        if group is None:
            notes.append(f"no mesh group for '{a.selection}'; its nodes take the first material")
            continue
        pos = order[np.searchsorted(mesh.node_ids, group.node_ids, sorter=order)]
        which[pos] = idx
    if not mats:
        raise PuzzleError(f"component '{comp.name}' has no material assigned", code="inconsistent")
    which[which < 0] = 0
    return mats, which, notes


def _block_tensors(project, store, block, base_mesh, evaluate, run_for):
    """A block's amplitude and mean tensors (stress, and strain if stored) on the base
    mesh, scaled; the study it came from; and the mesh it was on."""
    from puzzle.model.schema import ResultDefinition
    from puzzle.transfer import map_field

    if block.loading == "range":
        combo = project.objects.get(block.combination)
        if not isinstance(combo, ResultDefinition) or combo.method != "range":
            raise PuzzleError("a range loading needs a range combination", code="invalid")
        d = evaluate(project, store, combo)
        mesh, fields = d.mesh, d.results.steps[0].fields
        study = combo.cases[0].study

        def pair(name):
            if f"{name}_range" not in fields:
                return None
            return fields[f"{name}_range"], fields[f"{name}_mean"], 0.5, 1.0

        stress, strain = pair("stress"), pair("strain")
        run_ids = d.run_ids
    else:
        record = run_for(project, store, block.case.study, block.case.point)
        results = store.read_results(record.run_id)
        mesh = store.read_mesh(record.mesh_id)
        step = results.steps[block.case.step]
        study = block.case.study
        run_ids = [record.run_id]
        half = 0.5 if block.loading == "zero_based" else 1.0
        mean = 0.5 if block.loading == "zero_based" else 0.0

        def pair(name):
            f = step.fields.get(name)
            return (f, f, half, mean) if f is not None else None

        stress, strain = pair("stress"), pair("strain")
    if stress is None:
        raise PuzzleError("the fatigue tool needs stresses: the run has none", code="not_found")

    def on_base(p):
        if p is None:
            return None
        amp_f, mean_f, ka, km = p
        if amp_f.location != "node":
            raise PuzzleError("the fatigue tool needs nodal stresses", code="unsupported")
        amp = map_field(amp_f, mesh, base_mesh).values * ka * block.scale
        mean = map_field(mean_f, mesh, base_mesh).values * km * block.scale
        return np.asarray(amp, float), np.asarray(mean, float)

    return on_base(stress), on_base(strain), study, mesh, run_ids


def evaluate_fatigue(project, store, definition, evaluate, run_for):
    """(mesh, Results, items, run ids, warnings) of a fatigue definition."""
    s = definition.fatigue
    warnings: list[str] = []
    first = s.blocks[0]
    base_mesh = _identity_mesh(project, store, first, evaluate, run_for)  # carries the result
    blocks, run_ids, study = [], [], None
    for b in s.blocks:
        stress, strain, origin, _, ids = _block_tensors(
            project, store, b, base_mesh, evaluate, run_for
        )
        study = study or origin
        if s.life_model == "strain_life" and strain is None:
            raise PuzzleError(
                "strain life needs strains: the run stores no strain field", code="not_found"
            )
        blocks.append((b, stress, strain))
        run_ids += ids
    mats, which, notes = _materials(project, base_mesh, study, s)
    warnings += notes

    def damage(scale: np.ndarray) -> tuple[np.ndarray, list[np.ndarray]]:
        total = np.zeros(len(which))
        lives = []
        for b, (amp, mean), strain in blocks:
            sa = measure(amp, s.stress, signed=False) * scale
            sm = measure(mean, s.stress, signed=True) * scale
            ea = _equivalent_strain(strain[0]) * scale if strain is not None else None
            life = np.empty(len(which))
            for i, m in enumerate(mats):
                at = which == i
                life[at] = m.life(s, sa[at], sm[at], ea[at] if ea is not None else None)
            with np.errstate(divide="ignore"):
                total += np.where(np.isinf(life), 0.0, b.cycles / np.maximum(life, 1e-300))
            lives.append(life)
        return total, lives

    per_duty, lives = damage(np.ones(len(which)))
    with np.errstate(divide="ignore"):
        life = np.where(per_duty > 0, 1.0 / per_duty, np.inf)
    shown_life = np.minimum(life, LIFE_CAP)
    design_damage = per_duty * s.design_life
    safety = _safety(damage, s.design_life, len(which))

    amp0, mean0 = blocks[0][1]
    sa0 = measure(amp0, s.stress, signed=False)
    sm0 = measure(mean0, s.stress, signed=True)
    ids = base_mesh.node_ids.copy()

    def f(name, v):
        return Field(name, "node", [""], ids, np.asarray(v, float)[:, None])

    fields = {
        "fatigue_life": f("fatigue_life", shown_life),
        "fatigue_damage": f("fatigue_damage", design_damage),
        "fatigue_safety_factor": f("fatigue_safety_factor", safety),
        "fatigue_alternating": f("fatigue_alternating", sa0),
        "fatigue_mean": f("fatigue_mean", sm0),
    }
    one = len(s.blocks) == 1 and first.cycles == 1
    unit = "cycles" if one else "repetitions of the duty"
    items = [
        Item("fatigue_life:value", f"Life ({unit})", "fatigue_life", "value", "Fatigue", None),
        Item(
            "fatigue_damage:value",
            f"Damage at the design life ({s.design_life:g})",
            "fatigue_damage",
            "value",
            "Fatigue",
            None,
        ),
        Item(
            "fatigue_safety_factor:value",
            "Safety factor",
            "fatigue_safety_factor",
            "value",
            "Fatigue",
            None,
        ),
        Item(
            "fatigue_alternating:value",
            "Alternating stress",
            "fatigue_alternating",
            "value",
            "Fatigue",
            "pressure",
        ),
        Item("fatigue_mean:value", "Mean stress", "fatigue_mean", "value", "Fatigue", "pressure"),
    ]
    worst = int(np.argmin(safety))
    warnings += _proportionality(blocks, worst)
    if np.isinf(life).all():
        warnings.append("infinite life everywhere: every stress is below the endurance limit")
    elif np.any(np.isinf(life)):
        warnings.append(f"life is shown as {LIFE_CAP:g} where it is infinite")
    if s.life_model == "sn_curve":
        for i, m in enumerate(mats):
            top = max(p[1] for p in m.curve.points)
            rev = fm.reversed_stress(s.mean_stress, sa0, sm0, m.sut or np.nan, m.sy or np.nan)
            if np.any(rev[which == i] > top):
                warnings.append(
                    f"{m.name}: stresses above its S-N curve's highest point; the first "
                    "segment is extended there"
                )
    if s.life_model == "strain_life":
        warnings.append("strain life here takes no mean-stress correction")
    results = Results("derived", [StepResult(1, "fatigue", 0.0, fields=fields)])
    return base_mesh, results, items, run_ids, warnings


def _identity_mesh(project, store, block, evaluate, run_for):
    """The mesh a block's results are on (so the first block maps onto itself)."""
    from puzzle.model.schema import ResultDefinition

    if block.loading == "range":
        combo = project.objects.get(block.combination)
        if not isinstance(combo, ResultDefinition):
            raise PuzzleError("a range loading needs a range combination", code="invalid")
        return evaluate(project, store, combo).mesh
    record = run_for(project, store, block.case.study, block.case.point)
    return store.read_mesh(record.mesh_id)


def _safety(damage, design_life: float, n: int) -> np.ndarray:
    """The factor on all stresses that makes each node's life the design life, by
    bisection on its logarithm between 1/15 and 15."""
    lo, hi = np.full(n, np.log(1 / SAFETY_CAP)), np.full(n, np.log(SAFETY_CAP))
    at_cap = damage(np.full(n, SAFETY_CAP))[0] * design_life < 1
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        fails = damage(np.exp(mid))[0] * design_life >= 1
        lo, hi = np.where(fails, lo, mid), np.where(fails, mid, hi)
    return np.where(at_cap, SAFETY_CAP, np.exp(0.5 * (lo + hi)))


def _proportionality(blocks, node: int) -> list[str]:
    """Warn where a range's principal directions differ from its mean's at `node`."""
    out = []
    for b, (amp, mean), _ in blocks:
        if b.loading != "range":
            continue
        a, m = _tensor(amp[node]), _tensor(mean[node])
        if np.abs(m).max() < 1e-6 * max(np.abs(a).max(), 1e-30):
            continue
        va = np.linalg.eigh(a)[1][:, np.argmax(np.abs(np.linalg.eigvalsh(a)))]
        vm = np.linalg.eigh(m)[1][:, np.argmax(np.abs(np.linalg.eigvalsh(m)))]
        angle = np.degrees(np.arccos(min(1.0, abs(float(va @ vm)))))
        if angle > 15:
            out.append(
                f"the loading looks non-proportional at the critical node (principal "
                f"directions {angle:.0f}° apart): this stress-life result is an estimate; "
                "critical-plane methods are out of scope"
            )
    return out


def _tensor(v) -> np.ndarray:
    xx, yy, zz, xy, yz, zx = v
    return np.array([[xx, xy, zx], [xy, yy, yz], [zx, yz, zz]])
